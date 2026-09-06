#!/usr/bin/env bash
set -euo pipefail

# Linux/macOS 包装入口：把参数原样交给 Python 部署脚本。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

exec "$PYTHON_BIN" "$SCRIPT_DIR/deploy_vps_pair.py" "$@"
