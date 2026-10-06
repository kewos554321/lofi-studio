#!/usr/bin/env bash
#
# setup_python_env.sh
# 建立/更新 .venv 並安裝 requirements.txt（Demucs 等）。
# Demucs 會拉入 PyTorch，首次安裝需下載數百 MB。
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
PY="${PYTHON:-python3.11}"

command -v "$PY" >/dev/null 2>&1 || { echo "錯誤: 找不到 $PY，請先 brew install python@3.11" >&2; exit 1; }

if [ ! -d "$ROOT/.venv" ]; then
  echo "==> 建立 venv"
  "$PY" -m venv "$ROOT/.venv"
fi

echo "==> 安裝 requirements"
"$ROOT/.venv/bin/pip" install --upgrade pip >/dev/null
"$ROOT/.venv/bin/pip" install -r "$ROOT/requirements.txt"

echo
echo "✅ Python 環境就緒: $ROOT/.venv"
"$ROOT/.venv/bin/demucs" --version 2>/dev/null || true
