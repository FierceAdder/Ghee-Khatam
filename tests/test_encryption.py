"""Encryption at rest: the staging area, sealed input and encrypted quarantine.

All names and numbers here are invented.
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import ROOT, write_tree

from wards import write
from wards.audit import Auditor
from wards.errors import ConfigError
from wards.pipeline import Pipeline
from wards.release import Release
from wards.security import SafeLog, Secret

sys.path.insert(0, str(ROOT / "tools"))

SECRET = b"fake-encryption-test-secret-0042"
NAME, PHONE, UHID = "Ibemhal Soraisam", "9733021864", "ZPH-583921"
FILES = {
    "registry/reg.csv": "UHID,Patient Name,Mobile,Visit Date,Notes\n"
                        f"{UHID},{NAME},{PHONE},03/03/2026,stable\n"
                        "ZPH-583922,Kekhrie Angami,9862014375,04/03/2026,stable\n",
    "notes/ibemhal_soraisam.txt": f"UHID: {UHID}\nPatient Name: {NAME}\nBP 120/80\n",
    "notes/wide.txt": f"Patient Name: {NAME}\nHb 11.2 g/dL\n".encode("utf-16"),
    "protocol.txt": "Hospital protocol. BP target below 130/80.\n",
    "scans/ibemhal_scan.pdf": b"%PDF-1.4 " + NAME.encode(),
    "empty.txt": b"",
}
PRIVATE = (NAME, "Soraisam", "soraisam", PHONE, "583921", "ibemhal")
# Released text that is harmless once audited, but must not be on disk before that.
UNAUDITED = ("Patient Name", "BP 120/80", "Hospital protocol", "Visit Date")


def tree(root: Path) -> dict:
    """``{relative path: bytes}`` of every regular file under ``root``."""
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()}


def released(out: Path) -> dict:
    return {k: v for k, v in tree(out).items()
            if k.startswith("sanitized/") or k == "records.jsonl"}


def assert_holds_none(root: Path, needles) -> None:
    """No file under ``root`` shows any of ``needles`` in its bytes or its name."""
    for relative, data in tree(root).items():
        for needle in needles:
            raw = needle if isinstance(needle, bytes) else needle.encode()
            assert raw not in data and raw not in os.fsencode(relative), relative


def make_runner(policy, lexicon, secret: bytes = SECRET, **overrides) -> Pipeline:
    return Pipeline(replace(policy, **overrides), Secret(secret), SafeLog(io.StringIO()), lexicon)


def assert_failed_and_empty(out: Path) -> None:
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert sorted(tree(out)) == ["logs/run.jsonl", "report.json"]
    assert list((out / "sanitized").iterdir()) == []
    assert not (out / ".stage").exists() and not (out / "quarantine").exists()
    assert_holds_none(out, PRIVATE + UNAUDITED)


# -------------------------------------------------------------------- staging


def test_staged_files_are_ciphertext_while_they_are_audited(tmp_path, policy, lexicon,
                                                            monkeypatch):
    source, out = write_tree(tmp_path / "in", FILES), tmp_path / "out"
    real, seen = Auditor.leaks, []

    def spy(self, doc):
        on_disk = tree(out)
        seen.append(len(on_disk))
        assert on_disk and all(name.startswith(".stage/") for name in on_disk)
        assert_holds_none(out, PRIVATE + UNAUDITED)
        return real(self, doc)

    monkeypatch.setattr(Auditor, "leaks", spy)
    result = make_runner(policy, lexicon).run(source, out)
    assert len(seen) == result.counts["files_sanitized"] == 4
    assert not (out / ".stage").exists()
    assert (out / "sanitized" / "protocol.txt").read_bytes() == FILES["protocol.txt"].encode()


def test_a_leak_the_audit_catches_was_never_on_disk_in_the_clear(tmp_path, policy, lexicon,
                                                                 monkeypatch):
    source, out = write_tree(tmp_path / "in", FILES), tmp_path / "out"
    serialize, leaks = write.serialize, Auditor.leaks

    def faulty(doc):
        extra = f"call {PHONE}\n" if doc.rel == "protocol.txt" else ""
        return serialize(doc) + extra

    def spy(self, doc):
        assert_holds_none(out, (PHONE,))
        return leaks(self, doc)

    monkeypatch.setattr(write, "serialize", faulty)
    monkeypatch.setattr(Auditor, "leaks", spy)
    result = make_runner(policy, lexicon).run(source, out)
    assert next(f for f in result.files
                if f.get("reason") == "self_scan_failed")["status"] == "quarantined"
    assert_holds_none(out, (PHONE,))


def staged(tmp_path, **files) -> Release:
    release = Release(tmp_path / "out", Secret(SECRET))
    release.open()
    for relative, content in files.items():
        release.add(relative, content)
    return release


def flip(path: Path, at: int) -> None:
    data = bytearray(path.read_bytes())
    data[at] ^= 1
    path.write_bytes(bytes(data))


@pytest.mark.parametrize("damage", [
    lambda r: flip(r.path("a"), 0),                                  # nonce
    lambda r: flip(r.path("a"), 15),                                 # body
    lambda r: flip(r.path("a"), -1),                                 # tag
    lambda r: r.path("a").write_bytes(r.path("a").read_bytes()[:-3]),
    lambda r: r.path("a").write_bytes(b""),
    lambda r: r.path("a").write_bytes(r.path("b").read_bytes()),     # swapped
    lambda r: r.path("a").write_bytes(b"plain text put in its place, long enough"),
])
def test_a_tampered_or_swapped_staged_file_is_rejected(tmp_path, damage):
    from wards.crypto import IntegrityError

    release = staged(tmp_path, a="first\n", b="second\n")
    damage(release)
    with pytest.raises(IntegrityError):
        release.read("a")
    with pytest.raises(IntegrityError):
        release.publish([], {}, ["a", "b"])
    release.abort()
    assert tree(tmp_path / "out") == {}
    assert list((tmp_path / "out").iterdir()) == []


def test_another_process_cannot_open_a_stage_even_with_the_secret(tmp_path):
    from wards.crypto import IntegrityError

    first = staged(tmp_path, a="first\n")
    blob = first.path("a").read_bytes()
    second = Release(tmp_path / "out", Secret(SECRET))
    second.open()                                       # clears what the first one left
    assert not first.path("a").exists()
    second.path("a").write_bytes(blob)
    with pytest.raises(IntegrityError):
        second.read("a")


def test_staging_round_trips_text(tmp_path):
    release = staged(tmp_path, a="मरीज़ स्थिर\n")
    assert release.read("a") == "मरीज़ स्थिर\n"


def test_a_stage_changed_after_the_audit_fails_the_whole_run(tmp_path, policy, lexicon,
                                                             monkeypatch):
    from wards.crypto import IntegrityError

    source, out = write_tree(tmp_path / "in", FILES), tmp_path / "out"
    real = Release.publish

    def meddle(self, records, report, expected, log=None):
        flip(self.path(expected[-1]), 20)
        return real(self, records, report, expected, log)

    monkeypatch.setattr(Release, "publish", meddle)
    with pytest.raises(IntegrityError):
        make_runner(policy, lexicon).run(source, out)
    assert_failed_and_empty(out)
    assert '"reason":"integrity_check_failed"' in (out / "logs" / "run.jsonl").read_text()


# Runs main.py and sends a signal to itself at a chosen moment of the run.
DRIVER = """
import os, shutil, signal, sys
sys.dont_write_bytecode = True
root, moment, name = sys.argv[1:4]
sys.path.insert(0, root)
import main
from wards.audit import Auditor
number = getattr(signal, name)
if name != "SIGKILL":
    # a test run started in the background hands down "ignored" for SIGINT and SIGQUIT
    signal.signal(number, signal.default_int_handler if name == "SIGINT" else signal.SIG_DFL)

def stop():
    os.kill(os.getpid(), number)
    for _ in range(1000):
        pass

if moment == "publish":
    real_replace, calls = os.replace, []
    def replace(a, b):
        calls.append(b)
        if len(calls) == 3:
            stop()
        real_replace(a, b)
    os.replace = replace
else:
    real_leaks, seen = Auditor.leaks, []
    def leaks(self, doc):
        seen.append(1)
        if len(seen) == 3:
            if moment == "cleanup":
                real_rmtree = shutil.rmtree
                def rmtree(*args, **kwargs):
                    shutil.rmtree = real_rmtree
                    stop()                     # a second signal, during the clean-up
                    real_rmtree(*args, **kwargs)
                shutil.rmtree = rmtree
            stop()
        return real_leaks(self, doc)
    Auditor.leaks = leaks
sys.exit(main.main(sys.argv[4:]))
"""


def environment(**extra) -> dict:
    return dict(os.environ, RAKSHA_SECRET=SECRET.decode(), **extra)


def drive(tmp_path, moment: str, name: str, source: Path, out: Path):
    script = tmp_path / "driver.py"
    script.write_text(DRIVER, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-B", str(script), str(ROOT), moment, name,
         "--input", str(source), "--output", str(out)],
        env=environment(), capture_output=True, text=True, timeout=120)


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="POSIX signals")
@pytest.mark.parametrize("moment, name, code", [
    ("audit", "SIGTERM", 143), ("audit", "SIGHUP", 143), ("audit", "SIGQUIT", 143),
    ("audit", "SIGINT", 130), ("cleanup", "SIGTERM", 143), ("cleanup", "SIGINT", 130),
    ("publish", "SIGTERM", 143), ("publish", "SIGHUP", 143),
])
def test_a_stop_signal_leaves_no_staged_or_partial_file(tmp_path, moment, name, code):
    source, out = write_tree(tmp_path / "in", FILES), tmp_path / "out"
    run = drive(tmp_path, moment, name, source, out)
    assert run.returncode == code, run.stderr
    assert "Traceback" not in run.stderr
    assert_failed_and_empty(out)


@pytest.mark.skipif(not hasattr(signal, "SIGKILL"), reason="POSIX signals")
@pytest.mark.parametrize("moment", ["audit", "publish"])
def test_a_killed_run_leaves_only_ciphertext_and_audited_files(tmp_path, moment):
    source, out = write_tree(tmp_path / "in", FILES), tmp_path / "out"
    run = drive(tmp_path, moment, "SIGKILL", source, out)
    assert run.returncode == -signal.SIGKILL
    left = tree(out)
    assert any(name.startswith(".stage/") for name in left)
    assert "report.json" not in left                       # so it is not a release
    assert_holds_none(out, PRIVATE)
    for relative, data in left.items():
        if relative.startswith(".stage/"):
            assert all(text.encode() not in data for text in UNAUDITED)
        else:
            assert moment == "publish"                     # audited files only


def test_the_next_run_clears_what_a_killed_run_left(tmp_path, policy, lexicon):
    source = write_tree(tmp_path / "in", FILES)
    out = write_tree(tmp_path / "out", {".stage/0123": b"old", ".wards-part-0a": b"old",
                                        "logs/.wards-part-0b": b"old", "keep.txt": b"mine"})
    make_runner(policy, lexicon).run(source, out)
    assert sorted(p.name for p in out.iterdir()) == \
        ["keep.txt", "logs", "records.jsonl", "report.json", "sanitized"]
    assert [p.name for p in (out / "logs").iterdir()] == ["run.jsonl"]


@pytest.mark.parametrize("failing_call", [1, 3, 5, 7])
def test_a_failed_publish_removes_temporary_files(tmp_path, policy, lexicon, monkeypatch,
                                                  failing_call):
    source, out = write_tree(tmp_path / "in", FILES), tmp_path / "out"
    real, calls = os.replace, []

    def flaky(a, b):
        calls.append(b)
        if len(calls) == failing_call:
            raise OSError("disk")
        real(a, b)

    monkeypatch.setattr("wards.release.os.replace", flaky)
    with pytest.raises(OSError):
        make_runner(policy, lexicon).run(source, out)
    assert_failed_and_empty(out)


def test_report_is_renamed_into_place_last(tmp_path, policy, lexicon, monkeypatch):
    source, out = write_tree(tmp_path / "in", FILES), tmp_path / "out"
    real, names = os.replace, []

    def spy(a, b):
        names.append(Path(b).name)
        real(a, b)

    monkeypatch.setattr("wards.release.os.replace", spy)
    make_runner(policy, lexicon).run(source, out)
    assert names[-1] == "report.json" and names.count("report.json") == 1


# ----------------------------------------------------------- fail closed


@pytest.fixture
def no_cryptography(tmp_path) -> str:
    """A PYTHONPATH on which importing ``cryptography`` fails."""
    write_tree(tmp_path / "shadow",
               {"cryptography/__init__.py": "raise ImportError('not installed')\n"})
    return str(tmp_path / "shadow")


@pytest.mark.parametrize("command", [
    lambda s, o: [str(ROOT / "main.py"), "--input", s, "--output", o],
    lambda s, o: [str(ROOT / "main.py"), "--sealed", s, "--output", o],
    lambda s, o: [str(ROOT / "tools" / "seal.py"), "--in", s, "--out", o],
])
def test_without_cryptography_nothing_runs(tmp_path, no_cryptography, command):
    source, out = write_tree(tmp_path / "in", FILES), tmp_path / "out"
    run = subprocess.run([sys.executable, "-B", *command(str(source), str(out))],
                         env=environment(PYTHONPATH=no_cryptography),
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 2
    assert run.stderr.strip() == "cannot_start reason=cryptography_not_installed"
    assert not out.exists()


def test_the_pipeline_refuses_without_cryptography(tmp_path, policy, lexicon, monkeypatch):
    monkeypatch.setattr("wards.crypto.AESGCM", None)
    source, out = write_tree(tmp_path / "in", FILES), tmp_path / "out"
    with pytest.raises(ConfigError, match="cryptography_not_installed"):
        make_runner(policy, lexicon).run(source, out)
    assert not out.exists()


# --------------------------------------------------------------- sealed input


def mixed_input(tmp_path) -> Path:
    big = {"big.csv": "a,b\n" + "1,2\n" * 300_000}        # above max_file_mb=1
    source = write_tree(tmp_path / "in", {**FILES, **big})
    outside = write_tree(tmp_path / "outside", {"o.txt": f"{NAME} {PHONE}\n"})
    (source / "link.txt").symlink_to(outside / "o.txt")
    return source


def seal(source: Path, target: Path, secret: bytes = SECRET) -> Path:
    import seal as tool

    target.mkdir()
    tool.seal(source, target, Secret(secret))
    return target


def test_a_sealed_folder_shows_neither_content_nor_names(tmp_path):
    source = mixed_input(tmp_path)
    before = tree(source)
    sealed = seal(source, tmp_path / "sealed")
    assert_holds_none(sealed, PRIVATE + UNAUDITED + ("registry", "protocol", "%PDF", "reg.csv"))
    names = sorted(p.relative_to(sealed).as_posix() for p in sealed.rglob("*"))
    assert all(n in ("files", "manifest.gks") or len(n) == len("files/") + 32 for n in names)
    assert len(names) == 2 + len(before)                  # the link has no content
    assert tree(source) == before                         # only read


def test_two_seals_of_one_folder_share_nothing(tmp_path):
    source = write_tree(tmp_path / "in", FILES)
    first, second = seal(source, tmp_path / "one"), seal(source, tmp_path / "two")
    assert not set(tree(first).values()) & set(tree(second).values())
    assert not {p.name for p in (first / "files").iterdir()} \
        & {p.name for p in (second / "files").iterdir()}


def test_a_sealed_run_releases_exactly_what_a_plain_run_does(tmp_path, policy, lexicon):
    source = mixed_input(tmp_path)
    sealed = seal(source, tmp_path / "sealed")
    before = tree(sealed)
    plain = make_runner(policy, lexicon, max_file_mb=1).run(source, tmp_path / "plain")
    again = make_runner(policy, lexicon, max_file_mb=1).run(sealed, tmp_path / "again",
                                                            sealed=True)
    assert released(tmp_path / "plain") == released(tmp_path / "again")
    assert len(released(tmp_path / "plain")) == 5
    assert plain.files == again.files and plain.counts == again.counts
    assert {f.get("reason") for f in again.files} >= {
        "unsupported_format", "empty_file", "file_too_large", "not_a_regular_file"}
    assert tree(sealed) == before


def test_a_file_browser_leftover_does_not_spoil_a_sealed_folder(tmp_path, policy, lexicon):
    sealed = seal(write_tree(tmp_path / "in", FILES), tmp_path / "sealed")
    (sealed / "files" / ".DS_Store").write_bytes(b"finder")
    result = make_runner(policy, lexicon).run(sealed, tmp_path / "out", sealed=True)
    assert result.counts["files_total"] == len(FILES)


# Runs main.py and fails if anything outside the output folder is opened for writing.
AUDIT_DRIVER = """
import os, sys
sys.dont_write_bytecode = True
root, out = sys.argv[1], os.path.realpath(sys.argv[2])
sys.path.insert(0, root)
import main
written = []

def hook(event, args):
    if event == "open" and isinstance(args[0], (str, bytes)):
        flags = args[2] or 0
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
            written.append(os.path.realpath(os.fsdecode(args[0])))

sys.addaudithook(hook)
code = main.main(sys.argv[3:])
outside = [p for p in written if p != os.devnull and not p.startswith(out + os.sep)]
print("outside=%d inside=%d" % (len(outside), len(written) - len(outside)))
sys.exit(code)
"""


def test_a_sealed_run_writes_nothing_outside_the_output(tmp_path):
    source = write_tree(tmp_path / "in", FILES)
    sealed = seal(source, tmp_path / "sealed")
    shutil.rmtree(source)                                  # no plaintext copy anywhere
    private = [tmp_path / "tmpdir", tmp_path / "home"]
    for folder in private:
        folder.mkdir()
    script = tmp_path / "audit.py"
    script.write_text(AUDIT_DRIVER, encoding="utf-8")
    out = tmp_path / "out"
    run = subprocess.run(
        [sys.executable, "-B", str(script), str(ROOT), str(out), "--sealed", str(sealed),
         "--output", str(out)],
        env=environment(TMPDIR=str(private[0]), HOME=str(private[1])),
        capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr
    assert "outside=0 inside=" in run.stdout and "inside=0" not in run.stdout
    assert all(list(folder.iterdir()) == [] for folder in private)
    assert_holds_none(out, PRIVATE)


def test_a_wrong_secret_is_refused_before_anything_is_written(tmp_path):
    sealed = seal(write_tree(tmp_path / "in", FILES), tmp_path / "sealed")
    out = tmp_path / "out"
    run = subprocess.run(
        [sys.executable, "-B", str(ROOT / "main.py"), "--sealed", str(sealed),
         "--output", str(out)],
        env=dict(environment(), RAKSHA_SECRET="some-other-fake-secret"),
        capture_output=True, text=True, timeout=120)
    assert run.returncode == 2
    assert run.stderr.strip() == "cannot_start reason=sealed_wrong_secret_or_damaged"
    assert run.stdout == "" and not out.exists()


def blobs(sealed: Path) -> list[Path]:
    return sorted((sealed / "files").iterdir(), key=lambda p: p.name)


def swap(a: Path, b: Path) -> None:
    first, second = a.read_bytes(), b.read_bytes()
    a.write_bytes(second)
    b.write_bytes(first)


@pytest.mark.parametrize("damage", [
    lambda s: flip(s / "manifest.gks", 3),                    # magic
    lambda s: flip(s / "manifest.gks", 10),                   # salt
    lambda s: flip(s / "manifest.gks", -5),
    lambda s: (s / "manifest.gks").write_bytes(b""),
    lambda s: (s / "manifest.gks").unlink(),
    lambda s: blobs(s)[0].unlink(),
    lambda s: (s / "files" / ("0" * 32)).write_bytes(b"extra"),
    lambda s: blobs(s)[0].rename(s / "files" / ("f" * 32)),
])
def test_a_damaged_sealed_folder_is_refused_at_the_start(tmp_path, policy, lexicon, damage):
    sealed = seal(write_tree(tmp_path / "in", FILES), tmp_path / "sealed")
    damage(sealed)
    with pytest.raises(ConfigError, match="sealed_"):
        make_runner(policy, lexicon).run(sealed, tmp_path / "out", sealed=True)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("damage", [
    lambda s: flip(blobs(s)[0], 2),                           # magic
    lambda s: flip(blobs(s)[0], 12),                          # wrapping nonce
    lambda s: flip(blobs(s)[0], 40),                          # wrapped data key
    lambda s: flip(blobs(s)[0], 70),                          # data nonce
    lambda s: flip(blobs(s)[0], 85),                          # body
    lambda s: flip(blobs(s)[0], -1),                          # tag
    lambda s: blobs(s)[0].write_bytes(blobs(s)[0].read_bytes()[:-1]),
    lambda s: blobs(s)[0].write_bytes(blobs(s)[0].read_bytes() + b"x"),
    lambda s: blobs(s)[0].write_bytes(b""),
    lambda s: swap(blobs(s)[0], blobs(s)[1]),
])
def test_a_tampered_or_swapped_sealed_file_fails_the_run(tmp_path, policy, lexicon, damage):
    from wards.crypto import IntegrityError

    # same size everywhere, so that a swap is caught by what the file is bound to
    files = {f"n{i}.txt": f"Patient Name: {NAME}\nBed {i}\n" for i in range(3)}
    sealed = seal(write_tree(tmp_path / "in", files), tmp_path / "sealed")
    damage(sealed)
    with pytest.raises(IntegrityError):
        make_runner(policy, lexicon).run(sealed, tmp_path / "out", sealed=True)
    assert_failed_and_empty(tmp_path / "out")


@pytest.mark.parametrize("path", ["../escape.txt", "/abs.txt", "a//b.txt", "a/./b.txt", ""])
def test_a_manifest_cannot_name_a_path_outside_the_release(tmp_path, path):
    from wards import crypto

    writer = crypto.ArchiveWriter(tmp_path / "sealed", Secret(SECRET), crypto.SEALED_INPUT)
    writer.add(path, [b"x"])
    writer.close()
    with pytest.raises(ConfigError, match="sealed_manifest_invalid"):
        crypto.Archive.open(tmp_path / "sealed", Secret(SECRET))


def test_odd_file_names_survive_sealing(tmp_path):
    from wards import crypto

    names = ["a/मरीज़.txt", "b/bad\udcff.txt", "c d/e.txt"]
    writer = crypto.ArchiveWriter(tmp_path / "sealed", Secret(SECRET), crypto.SEALED_INPUT)
    for name in names:
        writer.add(name, [b"one", b"", b"two"])
    writer.close()
    archive = crypto.Archive.open(tmp_path / "sealed", Secret(SECRET))
    assert [e.path for e in archive.entries] == names
    assert all(archive.read(e) == b"onetwo" for e in archive.entries)
    with pytest.raises(ConfigError):                  # a key of its own per purpose
        crypto.Archive.open(tmp_path / "sealed", Secret(SECRET), crypto.QUARANTINE)


def run_seal(*arguments, secret: str = SECRET.decode()):
    return subprocess.run(
        [sys.executable, "-B", str(ROOT / "tools" / "seal.py"), *map(str, arguments)],
        env=dict(os.environ, RAKSHA_SECRET=secret), capture_output=True, text=True, timeout=120)


def test_the_seal_tool_reports_counts_only(tmp_path):
    source, sealed = write_tree(tmp_path / "in", FILES), tmp_path / "sealed"
    run = run_seal("--in", source, "--out", sealed)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "sealed files=6 without_content=0" and run.stderr == ""


@pytest.mark.parametrize("target, reason", [
    ("in/sealed", "input_and_output_overlap"), ("in", "input_and_output_overlap"),
    (".", "input_and_output_overlap"), ("used", "output_is_not_an_empty_directory"),
])
def test_the_seal_tool_never_writes_into_the_input(tmp_path, target, reason):
    source = write_tree(tmp_path / "in", FILES)
    write_tree(tmp_path / "used", {"x": "x"})
    before = tree(tmp_path)
    run = run_seal("--in", source, "--out", tmp_path / target)
    assert run.returncode == 2 and run.stderr.strip() == f"cannot_start reason={reason}"
    assert tree(tmp_path) == before


def test_the_seal_tool_needs_the_secret(tmp_path):
    source = write_tree(tmp_path / "in", FILES)
    run = run_seal("--in", source, "--out", tmp_path / "sealed", secret="")
    assert run.returncode == 2 and "raksha_secret_not_set" in run.stderr
    assert not (tmp_path / "sealed").exists()


@pytest.mark.parametrize("existing", [False, True])
def test_an_interrupted_seal_leaves_nothing(tmp_path, monkeypatch, capsys, existing):
    import seal as tool

    from wards import crypto

    source, target = write_tree(tmp_path / "in", FILES), tmp_path / "sealed"
    if existing:
        target.mkdir()
    monkeypatch.setattr(crypto.ArchiveWriter, "close",
                        lambda self: (_ for _ in ()).throw(KeyboardInterrupt()))
    monkeypatch.setenv("RAKSHA_SECRET", SECRET.decode())
    monkeypatch.setattr(tool, "harden_process", lambda: None)
    assert tool.main(["--in", str(source), "--out", str(target)]) == 130
    assert target.exists() == existing and tree(tmp_path / "in") == tree(source)
    assert not existing or list(target.iterdir()) == []


def test_main_takes_one_kind_of_input(tmp_path, capsys, monkeypatch):
    import main

    monkeypatch.setattr(main, "harden_process", lambda: None)
    code = main.main(["--input", str(tmp_path), "--sealed", str(tmp_path),
                      "--output", str(tmp_path / "out")])
    assert code == 2 and "input_and_sealed_both_given" in capsys.readouterr().err


# ----------------------------------------------------------------- quarantine


def test_quarantined_files_are_not_kept_by_default(tmp_path, policy, lexicon):
    source = write_tree(tmp_path / "in", FILES)
    make_runner(policy, lexicon).run(source, tmp_path / "out")
    assert not (tmp_path / "out" / "quarantine").exists()


@pytest.mark.parametrize("sealed", [False, True])
def test_quarantined_files_are_kept_only_as_ciphertext(tmp_path, policy, lexicon, sealed):
    source = mixed_input(tmp_path)
    given = seal(source, tmp_path / "sealed") if sealed else source
    make_runner(policy, lexicon, max_file_mb=1).run(source, tmp_path / "plain")
    out = tmp_path / "out"
    runner = make_runner(policy, lexicon, max_file_mb=1)
    result = runner.run(given, out, sealed=sealed, keep_quarantine=True)
    assert released(out) == released(tmp_path / "plain")          # the release is the same
    assert_holds_none(out, PRIVATE + ("%PDF", "scans"))
    assert result.counts["files_quarantined"] == 4
    log = (out / "logs" / "run.jsonl").read_text(encoding="utf-8")
    assert '"event":"quarantine_encrypted","files":4' in log

    run = run_seal("--open-quarantine", "--in", out / "quarantine", "--out", tmp_path / "seen")
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "opened files=3 without_content=1"     # not the link
    assert tree(tmp_path / "seen") == {
        name: (source / name).read_bytes()
        for name in ("big.csv", "empty.txt", "scans/ibemhal_scan.pdf")}
    assert (tmp_path / "seen" / "empty.txt").stat().st_mode & 0o077 == 0


def test_a_quarantine_opens_only_with_the_secret_and_outside_the_output(tmp_path, policy,
                                                                        lexicon):
    out = tmp_path / "out"
    make_runner(policy, lexicon).run(write_tree(tmp_path / "in", FILES), out,
                                     keep_quarantine=True)
    run = run_seal("--open-quarantine", "--in", out / "quarantine", "--out", tmp_path / "seen",
                   secret="some-other-fake-secret")
    assert run.returncode == 2 and "sealed_wrong_secret_or_damaged" in run.stderr
    for target in (out / "seen", out / "quarantine" / "seen", out):
        run = run_seal("--open-quarantine", "--in", out / "quarantine", "--out", target)
        assert run.returncode == 2 and "overlap" in run.stderr
    assert_holds_none(out, PRIVATE)


def test_a_run_with_nothing_quarantined_makes_no_folder(tmp_path, policy, lexicon):
    source = write_tree(tmp_path / "in", {"a.txt": "BP 120/80\n"})
    make_runner(policy, lexicon).run(source, tmp_path / "out", keep_quarantine=True)
    assert not (tmp_path / "out" / "quarantine").exists()


def test_a_failed_run_keeps_no_quarantine(tmp_path, policy, lexicon, monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("disk")

    monkeypatch.setattr(Release, "publish", explode)
    source = write_tree(tmp_path / "in", FILES)
    with pytest.raises(RuntimeError):
        make_runner(policy, lexicon).run(source, tmp_path / "out", keep_quarantine=True)
    assert_failed_and_empty(tmp_path / "out")


def test_an_earlier_quarantine_is_never_overwritten(tmp_path, policy, lexicon):
    source = write_tree(tmp_path / "in", FILES)
    out = write_tree(tmp_path / "out", {"quarantine/old": "kept"})
    with pytest.raises(ConfigError, match="output_already_has_results"):
        make_runner(policy, lexicon).run(source, out, keep_quarantine=True)
    assert tree(out) == {"quarantine/old": b"kept"}


# ---------------------------------------------------------------------- keys


def test_neither_the_secret_nor_any_derived_key_is_ever_written(tmp_path, policy, lexicon,
                                                                monkeypatch):
    from wards import crypto

    keys: list[bytes] = [SECRET]
    labels: set[str] = set()

    def record(function):
        def wrapper(*args):
            value = function(*args)
            keys.append(value)
            return value
        return wrapper

    for name in ("_expand", "_stretch", "_new_key"):
        monkeypatch.setattr(crypto, name, record(getattr(crypto, name)))
    mac = Secret.mac

    def spy_mac(self, domain, value):
        digest = mac(self, domain, value)
        if domain.startswith("crypto."):
            labels.add(domain)
            keys.append(digest)
        return digest

    monkeypatch.setattr(Secret, "mac", spy_mac)

    def forms() -> list[bytes]:
        return [form for key in keys for form in (
            key, key.hex().encode(), key.hex().upper().encode(),
            base64.b64encode(key).rstrip(b"="))]

    private = tmp_path / "tmpdir"
    private.mkdir()
    monkeypatch.setenv("TMPDIR", str(private))
    real = Auditor.leaks

    def during(self, doc):                         # while files are staged
        assert_holds_none(tmp_path, forms())
        return real(self, doc)

    monkeypatch.setattr(Auditor, "leaks", during)
    sealed = seal(write_tree(tmp_path / "in", FILES), tmp_path / "sealed")
    stream = io.StringIO()
    log = SafeLog(stream, level="detail")
    Pipeline(policy, Secret(SECRET), log, lexicon).run(sealed, tmp_path / "out", sealed=True,
                                                       keep_quarantine=True)
    assert labels == {"crypto.stage", "crypto.seal", "crypto.quarantine"}
    assert len(set(keys)) > 15                     # secret, derived keys and data keys
    assert_holds_none(tmp_path, forms())
    assert all(form not in stream.getvalue().encode() for form in forms())
    assert list(private.iterdir()) == []
