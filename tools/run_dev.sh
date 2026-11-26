#!/usr/bin/env bash
# The development loop: run -> validate -> self-check -> determinism -> secret change.
#
#   tools/run_dev.sh [work-dir]
#
# TOOLKIT_DIR  path to the organisers' kit (default: ../Secret-Wards-Toolkit)
# PYTHON       interpreter to use (default: python3)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOOLKIT_DIR="${TOOLKIT_DIR:-$ROOT/../Secret-Wards-Toolkit}"
PYTHON="${PYTHON:-python3}"
WORK="${1:-$ROOT/my_output}"
INPUT="$TOOLKIT_DIR/dev/input"

if [ ! -d "$INPUT" ]; then
  echo "practice data not found at $INPUT (set TOOLKIT_DIR)" >&2
  exit 2
fi

run() { # run <secret> <output-name>
  rm -rf "${WORK:?}/$2"
  RAKSHA_SECRET="$1" "$PYTHON" "$ROOT/main.py" --input "$INPUT" --output "$WORK/$2"
}

digest() { # one hash over sanitized/ and records.jsonl
  "$PYTHON" - "$1" <<'PY'
import hashlib, pathlib, sys
out, h = pathlib.Path(sys.argv[1]), hashlib.sha256()
files = sorted((out / "sanitized").rglob("*")) + [out / "records.jsonl"]
for f in files:
    if f.is_file():
        h.update(f.relative_to(out).as_posix().encode() + b"\0" + f.read_bytes() + b"\0")
print(h.hexdigest())
PY
}

input_digest() { find "$INPUT" -type f -print0 | LC_ALL=C sort -z | xargs -0 cat | cksum; }

before="$(input_digest)"
echo "== run A";  run practice a
echo "== run B";  run practice b
echo "== run C (different secret)"; run another-practice-secret c

echo; echo "== contract"
"$PYTHON" "$TOOLKIT_DIR/validate_output.py" --input "$INPUT" --output "$WORK/a"

echo; "$PYTHON" "$TOOLKIT_DIR/check_dev.py" --output "$WORK/a" --show "${SHOW:-10}"
echo; "$PYTHON" "$ROOT/tools/eval_stage.py" --output "$WORK/a" --toolkit "$TOOLKIT_DIR" --show "${SHOW:-10}"

echo; echo "== guarantees"
status=0
if [ "$(digest "$WORK/a")" = "$(digest "$WORK/b")" ]; then
  echo "ok    same secret twice gives identical output"
else
  echo "FAIL  output differs between two runs with the same secret"; status=1
fi
if [ "$(digest "$WORK/a")" != "$(digest "$WORK/c")" ]; then
  echo "ok    a different secret gives different output"
else
  echo "FAIL  output does not depend on the secret"; status=1
fi
if [ "$before" = "$(input_digest)" ]; then
  echo "ok    input left unchanged"
else
  echo "FAIL  input was modified"; status=1
fi
exit $status
