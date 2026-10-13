#!/usr/bin/env bash
#
# check.sh — 提交前的本機檢查（CI 也跑同一套）。
#   1) 編譯所有模組（抓語法/縮排錯）
#   2) 單元測試（python3 -m unittest）
#   3) 有裝 shellcheck 就順手檢查 scripts/*.sh
#
# 用法:
#   ./scripts/check.sh
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# macOS 系統 python 會把 bytecode 快取在 ~/Library/Caches/com.apple.python（不是 __pycache__），
# 若殘留舊版可能讓測試跑到過期程式碼。改用獨立快取目錄，確保一定從原始碼編譯。
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-$(mktemp -d)}"

fail=0

echo "==> compile: src/lofi"
python3 -m compileall -q src/lofi || { echo "  ❌ 編譯失敗"; fail=1; }

echo "==> unittest"
python3 -m unittest discover -s tests || { echo "  ❌ 測試失敗"; fail=1; }

if command -v shellcheck >/dev/null 2>&1; then
  echo "==> shellcheck: scripts/*.sh"
  shellcheck scripts/*.sh || { echo "  ❌ shellcheck 有問題"; fail=1; }
else
  echo "==> shellcheck: 未安裝，略過（brew install shellcheck）"
fi

echo
if [ "$fail" = 0 ]; then
  echo "✅ 全部通過"
else
  echo "❌ 有檢查未通過"
  exit 1
fi
