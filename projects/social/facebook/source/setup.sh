#!/bin/bash
set -eu
cd -- "$(dirname -- "$0")"
if [ "$(uname -s)" != Darwin ]; then
  printf 'Use START.cmd on Windows. This installer targets macOS.\n' >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  printf 'Install Python 3.12 or 3.13 from https://www.python.org/downloads/macos/\n' >&2
  exit 1
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else "Python 3.12+ required")'
if [ ! -x .venv/bin/python ]; then
  if [ "${1:-}" = --check ]; then
    exec python3 facebook_local.py doctor
  fi
  python3 -m venv .venv
fi
PYTHON="$PWD/.venv/bin/python"
if [ "${1:-}" = --check ]; then
  exec "$PYTHON" facebook_local.py doctor
fi
STAMP=.venv/facebook-requirements.sha256
HASH=$("$PYTHON" -c 'import hashlib,pathlib; print(hashlib.sha256(pathlib.Path("requirements.txt").read_bytes()).hexdigest())')
if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$HASH" ]; then
  "$PYTHON" -m pip install --disable-pip-version-check -r requirements.txt
  "$PYTHON" -m pip check
  printf '%s' "$HASH" > "$STAMP"
fi
"$PYTHON" facebook_local.py init
if [ "${1:-}" = --install-only ]; then exit 0; fi
exec "$PYTHON" facebook_local.py start
