#!/usr/bin/env bash
# Bootstrap a venv, install the unified package, and launch the CLI.
set -euo pipefail
cd "$(dirname "$0")"
export NSE_UNIVERSE_DATA_DIR="$PWD/data"

if command -v uv >/dev/null 2>&1; then
  [ -d .venv ] || uv venv
  uv pip install -q -e .
else
  [ -d .venv ] || python3 -m venv .venv
  ./.venv/bin/pip install -q -e .
fi

exec ./.venv/bin/python -m fortress
