#!/usr/bin/env bash
#
# check_env.sh — 檢查這條 lofi 工作流所需的環境是否就緒。
#
set -uo pipefail

ok()   { printf "  ✅ %-22s %s\n" "$1" "$2"; }
miss() { printf "  ❌ %-22s %s\n" "$1" "$2"; }
warn() { printf "  ⚠️  %-22s %s\n" "$1" "$2"; }

echo "=== 系統 ==="
printf "  %-24s %s\n" "Chip" "$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo '?')"
printf "  %-24s %.0f GB\n" "RAM" "$(echo "$(sysctl -n hw.memsize) / 1073741824" | bc 2>/dev/null || echo '?')"
printf "  %-24s %s\n" "macOS" "$(sw_vers -productVersion 2>/dev/null || echo '?')"
echo

echo "=== 必要指令 ==="
for c in ffmpeg ffprobe python3.11 git; do
  if command -v "$c" >/dev/null 2>&1; then
    ok "$c" "$(command -v "$c")  ($("$c" --version 2>&1 | head -1))"
  else
    miss "$c" "未安裝"
  fi
done
echo

echo "=== 選用工具 ==="
for c in sox git-lfs; do
  if command -v "$c" >/dev/null 2>&1; then ok "$c" "$(command -v "$c")"; else warn "$c" "未安裝（選用）"; fi
done
echo

echo "=== Python 環境 ==="
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -x "$ROOT/.venv/bin/python" ]; then
  ok "venv" "$ROOT/.venv"
  if [ -x "$ROOT/.venv/bin/demucs" ]; then
    ok "demucs" "$("$ROOT/.venv/bin/demucs" --version 2>/dev/null || echo 'installed')"
  else
    warn "demucs" "尚未安裝（執行 scripts/setup_python_env.sh）"
  fi
else
  warn "venv" "尚未建立（執行 scripts/setup_python_env.sh）"
fi
echo

echo "=== AI 生成（P2）==="
if [ -d "$HOME/ComfyUI" ]; then ok "ComfyUI" "$HOME/ComfyUI"; else warn "ComfyUI" "未安裝（P2 再處理）"; fi
if [ -d "$HOME/ComfyUI/models/checkpoints" ] && find "$HOME/ComfyUI/models" -iname "*ace*" 2>/dev/null | grep -q .; then
  ok "ACE-Step" "疑似已放置"
else
  warn "ACE-Step" "未安裝模型（P2 再處理）"
fi
echo
echo "完成。"
