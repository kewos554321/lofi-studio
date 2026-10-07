#!/usr/bin/env bash
#
# cleanup_outputs.sh
# 清理輸出，釋放容量。預設只「預覽」不動作，加 --apply 才真的執行。
#
# 會處理：
#   1) 中間殘留檔（安全，可重生）：output/lofi_loop*.mp4、output/lofi_video_*.mp4、
#      output/mixes/*_raw.wav、output/mixes/lofi_long_*.wav、output/_loop_list.txt
#   2) 舊成片：output/videos/*.mp4（用 --keep / --older-than 決定留哪些）
#   3) 可選：--stems 清 output/stems
#
# 用法:
#   ./scripts/cleanup_outputs.sh                          # 預覽（預設保留最新 3 支影片）
#   ./scripts/cleanup_outputs.sh --apply                  # 真的刪
#   ./scripts/cleanup_outputs.sh --keep 5 --apply         # 只留最新 5 支影片
#   ./scripts/cleanup_outputs.sh --older-than 14 --apply  # 刪 14 天前的影片
#   ./scripts/cleanup_outputs.sh --archive /Volumes/WJ_SATA/lofi-studio/output/videos --apply
#   ./scripts/cleanup_outputs.sh --stems --apply          # 連 stems 一起清
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

KEEP=3; KEEP_SET=0
OLDER=""
ARCHIVE=""
APPLY=0
DO_STEMS=0

while [ $# -gt 0 ]; do
  case "$1" in
    --keep)       KEEP="$2"; KEEP_SET=1; shift 2;;
    --older-than) OLDER="$2"; shift 2;;
    --archive)    ARCHIVE="$2"; shift 2;;
    --apply)      APPLY=1; shift;;
    --stems)      DO_STEMS=1; shift;;
    -h|--help)    sed -n '2,26p' "$0"; exit 0;;
    *) echo "未知參數: $1（用 --help）" >&2; exit 1;;
  esac
done

# --older-than 沒同時指定 --keep 時，不套用預設保留數
[ -n "$OLDER" ] && [ "$KEEP_SET" = 0 ] && KEEP=0

# ---------- 收集 1) 中間殘留檔 ----------
LEFTOVERS=()
while IFS= read -r -d '' f; do LEFTOVERS+=("$f"); done < <(
  find -H output -maxdepth 2 -type f \
    \( -name 'lofi_loop*.mp4' -o -name 'lofi_video_*.mp4' \
       -o -name '*_raw.wav' -o -name 'lofi_long_*.wav' -o -name '_loop_list.txt' \) \
    -print0 2>/dev/null
)

# ---------- 收集 2) 舊成片 ----------
VIDEOS_SEL=()
if [ -d output/videos ]; then
  ALL=()
  # 依 mtime 由新到舊排序（檔名無空白，安全）
  while IFS= read -r f; do
    [ -n "$f" ] && ALL+=("$f")
  done < <(find -H output/videos -maxdepth 1 -type f -name '*.mp4' -exec stat -f '%m %N' {} \; 2>/dev/null \
            | sort -rn | cut -d' ' -f2-)
  NOW=$(date +%s)
  for ((i=0; i<${#ALL[@]}; i++)); do
    f="${ALL[$i]}"
    # 保留最新 KEEP 支
    if [ "$i" -lt "$KEEP" ]; then continue; fi
    # 若指定天數，只刪夠舊的
    if [ -n "$OLDER" ]; then
      MT=$(stat -f %m "$f" 2>/dev/null || echo "$NOW")
      AGE=$(( (NOW - MT) / 86400 ))
      [ "$AGE" -lt "$OLDER" ] && continue
    fi
    VIDEOS_SEL+=("$f")
  done
fi

# ---------- 收集 3) stems ----------
STEMS_SEL=()
if [ "$DO_STEMS" = 1 ] && [ -d output/stems ]; then
  while IFS= read -r -d '' f; do STEMS_SEL+=("$f"); done < <(
    find output/stems -type f -print0 2>/dev/null
  )
fi

TARGETS=()
[ "${#LEFTOVERS[@]}" -gt 0 ] && TARGETS+=("${LEFTOVERS[@]}")
[ "${#VIDEOS_SEL[@]}" -gt 0 ] && TARGETS+=("${VIDEOS_SEL[@]}")
[ "${#STEMS_SEL[@]}" -gt 0 ] && TARGETS+=("${STEMS_SEL[@]}")

# ---------- 報告 ----------
if [ "${#TARGETS[@]}" -eq 0 ]; then
  echo "✅ 沒有需要清理的檔案。"
  exit 0
fi

SIZE=$(du -ch "${TARGETS[@]}" 2>/dev/null | tail -1 | awk '{print $1}')
echo "=================================================="
if [ "$APPLY" = 1 ]; then
  echo " 模式: $([ -n "$ARCHIVE" ] && echo "搬移到 $ARCHIVE" || echo "刪除")"
else
  echo " 模式: 預覽（dry-run；加 --apply 才會執行）"
fi
echo " 將處理 ${#TARGETS[@]} 個檔案，釋放約 ${SIZE}"
echo "=================================================="

echo "【中間殘留檔】${#LEFTOVERS[@]} 個"
for f in "${LEFTOVERS[@]+"${LEFTOVERS[@]}"}"; do printf "  %-8s %s\n" "$(du -h "$f"|cut -f1)" "$f"; done
echo "【舊成片】${#VIDEOS_SEL[@]} 個（保留最新 ${KEEP} 支${OLDER:+，且僅刪 ${OLDER} 天前}）"
for f in "${VIDEOS_SEL[@]+"${VIDEOS_SEL[@]}"}"; do printf "  %-8s %s\n" "$(du -h "$f"|cut -f1)" "$f"; done
if [ "$DO_STEMS" = 1 ]; then
  echo "【stems】${#STEMS_SEL[@]} 個"
  for f in "${STEMS_SEL[@]+"${STEMS_SEL[@]}"}"; do printf "  %-8s %s\n" "$(du -h "$f"|cut -f1)" "$f"; done
fi

if [ "$APPLY" = 1 ]; then
  echo
  if [ -n "$ARCHIVE" ]; then
    mkdir -p "$ARCHIVE"
    for f in "${TARGETS[@]}"; do mv -f "$f" "$ARCHIVE/"; done
    echo "✅ 已搬移 ${#TARGETS[@]} 個檔案到 $ARCHIVE"
  else
    for f in "${TARGETS[@]}"; do rm -f "$f"; done
    echo "✅ 已刪除 ${#TARGETS[@]} 個檔案"
  fi
  echo "   內接可用空間: $(df -h /System/Volumes/Data | awk 'NR==2{print $4}')"
else
  echo
  echo "（這是預覽。確認後加 --apply 執行）"
fi
