#!/usr/bin/env bash
set -euo pipefail
exec "${PYTHON_BIN:-python3}" -B "$(dirname "$0")/start.py" "$@"
