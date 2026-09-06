#!/usr/bin/env bash
# 让宿主机上的 Python 测试使用隔离容器的 PHP CLI。
set -euo pipefail
exec docker exec -i "$LIBRESPEED_TEST_CONTAINER" php "$@"
