#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# The optional .pydeps directory is only used by sandboxes without a writable
# virtualenv. Normal environments can install requirements-dev.txt instead.
if [[ -d "$ROOT_DIR/.pydeps" ]]; then
  export PYTHONPATH="$ROOT_DIR:$ROOT_DIR/.pydeps${PYTHONPATH:+:$PYTHONPATH}"
else
  export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"
fi

exec python3 -m pytest -v "$@"
