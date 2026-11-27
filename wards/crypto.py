"""Encryption at rest (AES-256-GCM).

Three uses, each with its own key, all derived in memory from ``RAKSHA_SECRET`` under a
separate label and never written:

* **staging** (``output/.stage``): files waiting for the audit are on disk only as
  ciphertext. The key also mixes in a random salt that exists only in this process, so
  nobody can open what a killed run leaves behind, not even with the secret.
* **sealed input** (``tools/seal.py``, ``main.py --sealed``): envelope encryption. Every
  file has its own random data key, data keys are wrapped by a key-encryption key, and
  file names are replaced by keyed names; the real ones are in an encrypted manifest.
* **encrypted quarantine** (``--encrypt-quarantine``): the same envelope format, under
  its own label.

Every ciphertext is bound to the path it belongs to, so files cannot be swapped. Nonces and
salts are random: no ciphertext is part of the release, which stays byte-deterministic.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from wards import ingest
from wards.errors import ConfigError, QuarantineError, WardsError
from wards.policy import Policy
from wards.security import Secret

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.hashes import SHA256
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
except ImportError:  # the run is refused by require(); plaintext is never staged instead
    AESGCM = None

STAGE, SEALED_INPUT, QUARANTINE = "crypto.stage", "crypto.seal", "crypto.quarantine"

MANIFEST_NAME, FILES_NAME = "manifest.gks", "files"
_MANIFEST_MAGIC, _FILE_MAGIC = b"GKMANIF1", b"GKSEALD1"
_KEY, _SALT, _NONCE, _TAG = 32, 16, 12, 16
_WRAPPED = _KEY + _TAG
_HEADER = len(_FILE_MAGIC) + _NONCE + _WRAPPED + _NONCE
_CHUNK = 1 << 20
# Sealed folders outlive the run, so guessing a weak secret from one is made expensive.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 1 << 15, 8, 1


class IntegrityError(WardsError):
    """A ciphertext was changed, truncated or moved. The message is a fixed code."""


def require() -> None:
    if AESGCM is None:
        raise ConfigError("cryptography_not_installed")


# ----------------------------------------------------------------------- keys


def _expand(material: bytes, salt: bytes, info: str) -> bytes:
    return HKDF(algorithm=SHA256(), length=_KEY, salt=salt,
                info=f"ghee-khatam/v1/{info}".encode()).derive(material)


def _stretch(material: bytes, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=_KEY, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P).derive(material)


def _new_key() -> bytes:
    return os.urandom(_KEY)


def _bytes(text: str) -> bytes:
    return text.encode("utf-8", errors="surrogatepass")


def _keyed_name(key: bytes, relative: str) -> str:
    return hmac.new(key, _bytes(relative), hashlib.sha256).hexdigest()[:32]


# -------------------------------------------------------------------- staging


class StageCipher:
    """Seals staged files under a key that lives only as long as this object."""

    def __init__(self, secret: Secret | None = None) -> None:
        require()
        material = _new_key() if secret is None else secret.mac(STAGE, "v1")
        self._data = AESGCM(_expand(material, os.urandom(_KEY), "stage"))

    def seal(self, relative: str, data: bytes) -> bytes:
        nonce = os.urandom(_NONCE)
        return nonce + self._data.encrypt(nonce, data, b"stage\x00" + _bytes(relative))

    def open(self, relative: str, blob: bytes) -> bytes:
        if len(blob) < _NONCE + _TAG:
            raise IntegrityError("staged_file_rejected")
        try:
            return self._data.decrypt(blob[:_NONCE], blob[_NONCE:],
                                      b"stage\x00" + _bytes(relative))
        except InvalidTag:
            raise IntegrityError("staged_file_rejected") from None


# ------------------------------------------------------------------ envelopes


class _Keys:
    """The keys of one sealed folder."""

    def __init__(self, secret: Secret, label: str, salt: bytes) -> None:
        master = _stretch(secret.mac(label, "v1"), salt)
        self.wrap = AESGCM(_expand(master, salt, f"{label}/wrap"))
        self.manifest = AESGCM(_expand(master, salt, f"{label}/manifest"))
        self.names = _expand(master, salt, f"{label}/names")


@dataclass(frozen=True)
class Entry:
    """One input file of a sealed folder, as its manifest describes it."""

    path: str                    # relative, with "/" separators; secret
    sha256: str
    size: int = 0
    name: str = ""               # blob under files/; empty if no content was sealed
    reason: str = ""             # why it was, or must be, quarantined


def _wrap_context(name: str) -> bytes:
    return _FILE_MAGIC + b"key\x00" + name.encode("ascii")


def _data_context(name: str, relative: str) -> bytes:
    return _FILE_MAGIC + b"data\x00" + name.encode("ascii") + b"\x00" + _bytes(relative)


class ArchiveWriter:
    """Writes a sealed folder. It is complete only once :meth:`close` wrote the manifest."""

    def __init__(self, folder: Path, secret: Secret, label: str) -> None:
        require()
        self.folder = folder
        self._salt = os.urandom(_SALT)
        self._keys = _Keys(secret, label, self._salt)
        self._entries: list[Entry] = []
        (folder / FILES_NAME).mkdir(parents=True)

    def add(self, relative: str, chunks: Iterable[bytes], reason: str = "") -> None:
        """Encrypt one file, given in pieces, under its own data key."""
        name = _keyed_name(self._keys.names, relative)
        data_key = _new_key()
        wrap_nonce, data_nonce = os.urandom(_NONCE), os.urandom(_NONCE)
        wrapped = self._keys.wrap.encrypt(wrap_nonce, data_key, _wrap_context(name))
        encryptor = Cipher(algorithms.AES(data_key), modes.GCM(data_nonce)).encryptor()
        encryptor.authenticate_additional_data(_data_context(name, relative))
        digest, size = hashlib.sha256(), 0
        target = self.folder / FILES_NAME / name
        try:
            with open(target, "xb") as handle:
                handle.write(_FILE_MAGIC + wrap_nonce + wrapped + data_nonce)
                for chunk in chunks:
                    digest.update(chunk)
                    size += len(chunk)
                    handle.write(encryptor.update(chunk))
                handle.write(encryptor.finalize() + encryptor.tag)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        self._entries.append(Entry(relative, digest.hexdigest(), size, name, reason))

    def skip(self, relative: str, sha256: str, reason: str) -> None:
        """Record a file whose content cannot be sealed (a link, or unreadable)."""
        self._entries.append(Entry(relative, sha256, reason=reason))

    def close(self) -> None:
        listing = json.dumps([vars(e) for e in self._entries], sort_keys=True).encode("ascii")
        nonce = os.urandom(_NONCE)
        body = self._keys.manifest.encrypt(nonce, listing, _MANIFEST_MAGIC + self._salt)
        part = self.folder / (MANIFEST_NAME + ".part")
        part.write_bytes(_MANIFEST_MAGIC + self._salt + nonce + body)
        os.replace(part, self.folder / MANIFEST_NAME)


class Archive:
    """A sealed folder opened for reading. Nothing is ever written to it."""

    def __init__(self, folder: Path, keys: _Keys, entries: list[Entry]) -> None:
        self.folder = folder
        self.entries = entries
        self._keys = keys
        self._by_path = {e.path: e for e in entries}

    @classmethod
    def open(cls, folder: Path, secret: Secret, label: str = SEALED_INPUT) -> "Archive":
        """Check the secret, the manifest and the list of files before anything is read."""
        require()
        try:
            raw = (folder / MANIFEST_NAME).read_bytes()
        except OSError:
            raise ConfigError("sealed_manifest_missing") from None
        start = len(_MANIFEST_MAGIC) + _SALT
        if not raw.startswith(_MANIFEST_MAGIC) or len(raw) < start + _NONCE + _TAG:
            raise ConfigError("sealed_wrong_secret_or_damaged")
        salt = raw[len(_MANIFEST_MAGIC):start]
        keys = _Keys(secret, label, salt)
        try:
            listing = keys.manifest.decrypt(raw[start:start + _NONCE], raw[start + _NONCE:],
                                            _MANIFEST_MAGIC + salt)
        except InvalidTag:
            raise ConfigError("sealed_wrong_secret_or_damaged") from None
        entries = [Entry(**item) for item in json.loads(listing)]
        if not all(_safe_relative(e.path) for e in entries) \
                or len({e.path for e in entries}) != len(entries):
            raise ConfigError("sealed_manifest_invalid")
        try:                       # the macOS file browser leaves .DS_Store in any folder
            present = sorted(set(os.listdir(folder / FILES_NAME)) - {".DS_Store"})
        except OSError:
            present = []
        if present != sorted(e.name for e in entries if e.name):
            raise ConfigError("sealed_files_missing_or_extra")
        return cls(folder, keys, entries)

    def entry(self, relative: str) -> Entry:
        return self._by_path[relative]

    def read(self, entry: Entry) -> bytes:
        """The file's bytes, in memory only, and only if every one of them is authentic."""
        try:
            with open(self.folder / FILES_NAME / entry.name, "rb") as handle:
                length = os.fstat(handle.fileno()).st_size - _HEADER - _TAG
                header = handle.read(_HEADER)
                if length != entry.size or not header.startswith(_FILE_MAGIC):
                    raise IntegrityError("sealed_file_rejected")
                at = len(_FILE_MAGIC)
                wrap_nonce, at = header[at:at + _NONCE], at + _NONCE
                wrapped, data_nonce = header[at:at + _WRAPPED], header[at + _WRAPPED:]
                data_key = self._keys.wrap.decrypt(wrap_nonce, wrapped,
                                                   _wrap_context(entry.name))
                decryptor = Cipher(algorithms.AES(data_key), modes.GCM(data_nonce)).decryptor()
                decryptor.authenticate_additional_data(_data_context(entry.name, entry.path))
                pieces = []
                while length:
                    chunk = handle.read(min(length, _CHUNK))
                    if not chunk:
                        raise IntegrityError("sealed_file_rejected")
                    length -= len(chunk)
                    pieces.append(decryptor.update(chunk))
                pieces.append(decryptor.finalize_with_tag(handle.read(_TAG)))
        except (InvalidTag, OSError, ValueError):
            raise IntegrityError("sealed_file_rejected") from None
        return b"".join(pieces)


def _safe_relative(path: str) -> bool:
    parts = path.split("/")
    return "\x00" not in path and all(part not in ("", ".", "..") for part in parts)


# ---------------------------------------------------------------------- input


def file_chunks(handle) -> Iterator[bytes]:
    return iter(lambda: handle.read(_CHUNK), b"")


def seal_tree(source: Path, writer: ArchiveWriter, policy: Policy) -> None:
    """Seal every file under ``source``, which is only read."""
    for path in ingest.list_files(source):
        relative = path.relative_to(source).as_posix()
        if path.is_symlink() or not path.is_file():
            item = ingest.read_file(path, source, policy)     # a digest, never the content
            writer.skip(relative, item.sha256, item.reason)
            continue
        try:
            handle = open(path, "rb")
        except OSError:
            writer.skip(relative, hashlib.sha256(os.fsencode(relative)).hexdigest(),
                        "unreadable_file")
            continue
        with handle:
            writer.add(relative, file_chunks(handle))


def read_input(source: Path | Archive, policy: Policy) -> Iterator[ingest.InputFile]:
    """Stage 0 over a plain input folder or a sealed one, with the same result."""
    if not isinstance(source, Archive):
        for path in ingest.list_files(source):
            yield ingest.read_file(path, source, policy)
        return
    for entry in source.entries:
        item = ingest.InputFile(rel=entry.path, sha256=entry.sha256, reason=entry.reason)
        if not item.reason and entry.size > policy.max_file_mb * 1024 * 1024:
            item.reason = "file_too_large"
        if not item.reason:
            raw = source.read(entry)
            item.sha256 = hashlib.sha256(raw).hexdigest()
            try:
                ingest.classify(item, raw, Path(entry.path).suffix.lower(), policy)
            except QuarantineError as error:
                item.reason, item.text, item.kind = error.reason, "", None
        yield item


def keep_file(writer: ArchiveWriter, source: Path | Archive, item: ingest.InputFile,
              reason: str) -> None:
    """Put the original bytes of a quarantined input file into ``writer``."""
    if isinstance(source, Archive):
        entry = source.entry(item.rel)
        if entry.name:
            writer.add(item.rel, [source.read(entry)], reason)
            return
    else:
        path = source / item.rel
        if path.is_file() and not path.is_symlink():          # a link is never followed
            try:
                handle = open(path, "rb")
            except OSError:
                pass
            else:
                with handle:
                    writer.add(item.rel, file_chunks(handle), reason)
                return
    writer.skip(item.rel, item.sha256, reason)
