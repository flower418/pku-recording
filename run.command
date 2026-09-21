#!/usr/bin/env bash
# 双击运行（macOS）。也可以在终端里执行：./run.command
cd "$(dirname "$0")" || exit 1

PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python

exec "$PY" -m pku_recording "$@"
