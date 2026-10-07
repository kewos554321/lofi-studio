#!/usr/bin/env bash
#
# disk_report.sh
# 一眼看清容量：內接 / 外接可用空間、專案各目錄大小、最大的影片、清理建議。
#
# 用法:
#   ./scripts/disk_report.sh
#
# 可用環境變數:
#   EXT_VOL=/Volumes/WJ_SATA   外接碟路徑（預設 WJ_SATA）
#   WARN_FREE_GB=20            內接可用低於此值就警示
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

EXT="${EXT_VOL:-/Volumes/WJ_SATA}"
WARN="${WARN_FREE_GB:-20}"

hr() { printf '─%.0s' {1..60}; echo; }

# ---- 磁碟空間 ----
echo "◆ 磁碟空間"
df -h / "$EXT" 2>/dev/null | awk '
  NR==1 {print "  "$0; next}
  {printf "  %-22s size=%-7s used=%-7s avail=%-7s (%s)\n", $9, $2, $3, $4, $5}'

# 內接可用（GB，整數）
FREE_INT=$(df -Pk /System/Volumes/Data 2>/dev/null | awk 'NR==2{printf "%d", $4/1024/1024}')
if [ -n "${FREE_INT:-}" ] && [ "$FREE_INT" -lt "$WARN" ]; then
  echo "  ⚠️  內接可用僅 ${FREE_INT}GB（低於門檻 ${WARN}GB）——建議把 output 移往外接或清理"
fi
if [ -d "$EXT" ]; then
  FREE_EXT=$(df -Pk "$EXT" 2>/dev/null | awk 'NR==2{printf "%d", $4/1024/1024}')
  echo "  ℹ️  外接 $EXT 可用 ${FREE_EXT:-?}GB"
else
  echo "  ⚠️  找不到外接碟 ${EXT}（未掛載？）"
fi

# ---- 專案目錄 ----
echo
echo "◆ 專案各目錄大小"
for d in assets/tracks assets/visuals prompts catalog output/videos output/episodes output/mixes output/stems output/logs .venv; do
  [ -e "$d" ] && printf "  %-18s %s\n" "$d" "$(du -sh "$d" 2>/dev/null | cut -f1)"
done
printf "  %-18s %s\n" "(專案總計)" "$(du -sh "$ROOT" 2>/dev/null | cut -f1)"

# ---- ComfyUI ----
if [ -d "$HOME/ComfyUI" ]; then
  echo
  echo "◆ ComfyUI"
  printf "  %-18s %s\n" "models" "$(du -sh "$HOME/ComfyUI/models" 2>/dev/null | cut -f1)"
  printf "  %-18s %s\n" "venv" "$(du -sh "$HOME/ComfyUI/venv" 2>/dev/null | cut -f1)"
  printf "  %-18s %s\n" "output/audio" "$(du -sh "$HOME/ComfyUI/output/audio" 2>/dev/null | cut -f1)"
fi

# ---- 最大影片 ----
FOUND_VID=0
if [ -d output/videos ] && [ -n "$(ls -A output/videos 2>/dev/null)" ]; then
  echo
  echo "◆ output/videos（由大到小）"
  ls -lhS output/videos 2>/dev/null | awk 'NR>1{printf "  %-8s %-10s %s\n", $5, $6" "$7, $9}'
  FOUND_VID=1
fi
if [ -d output/episodes ] && [ -n "$(find -H output/episodes -maxdepth 2 -name video.mp4 -type f 2>/dev/null)" ]; then
  echo
  echo "◆ output/episodes（一集一包，由大到小）"
  find -H output/episodes -maxdepth 2 -name video.mp4 -type f -exec stat -f '%z %N' {} \; 2>/dev/null \
    | sort -rn | awk '{printf "  %-8.1fMB %s\n", $1/1048576, $2}'
  FOUND_VID=1
fi
[ "$FOUND_VID" = 1 ] && echo "  → 每分鐘約 37MB；每天 1 支 1 小時片 ≈ 2.2GB ≈ 每月 66GB"

# ---- 中間殘留檔 ----
echo
echo "◆ 可清理的中間殘留檔"
LEFTOVER_FILES=$(find -H output -maxdepth 3 -type f \
  \( -name 'lofi_loop*.mp4' -o -name 'lofi_video_*.mp4' \
     -o -name '*_raw.wav' -o -name 'lofi_long_*.wav' -o -name '_loop_list.txt' \
     -o -name '_loop_1080.mp4' -o -name '_video_copy.mp4' -o -name '_mix_raw.wav' \) \
  2>/dev/null)
if [ -n "$LEFTOVER_FILES" ]; then
  LEFTOVER=$(printf '%s\n' "$LEFTOVER_FILES" | grep -c . || true)
  echo "  找到 $LEFTOVER 個（建議 ./scripts/cleanup_outputs.sh）:"
  printf '%s\n' "$LEFTOVER_FILES" | while IFS= read -r f; do
    [ -n "$f" ] && printf "    %-6s %s\n" "$(du -h "$f" 2>/dev/null | cut -f1)" "$f"
  done
else
  echo "  無（很好）"
fi
