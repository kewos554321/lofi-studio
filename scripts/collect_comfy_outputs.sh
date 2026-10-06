#!/usr/bin/env bash
#
# collect_comfy_outputs.sh
# 把 ComfyUI 生成的音訊收集到本專案的 assets/tracks/，方便後續拼接。
# 會依檔名排序，複製（不刪除原檔），可選擇重新命名為 trackNN。
#
# 用法:
#   ./collect_comfy_outputs.sh              # 複製新檔到 assets/tracks/
#   ./collect_comfy_outputs.sh --move       # 改成搬移
#   ./collect_comfy_outputs.sh --rename     # 重新命名為 track01, track02...
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
SRC="${COMFYUI_DIR:-$HOME/ComfyUI}/output/audio"
DST="$ROOT/assets/tracks"

MODE="copy"
RENAME=0
for a in "$@"; do
  case "$a" in
    --move) MODE="move" ;;
    --rename) RENAME=1 ;;
    *) echo "未知參數: $a" >&2; exit 1 ;;
  esac
done

[ -d "$SRC" ] || { echo "找不到 ComfyUI 輸出目錄: $SRC" >&2; exit 1; }
mkdir -p "$DST"

shopt -s nullglob
FILES=()
while IFS= read -r -d '' f; do
  FILES[${#FILES[@]}]="$f"
done < <(find "$SRC" -type f \( -iname '*.flac' -o -iname '*.wav' -o -iname '*.mp3' \) -print0 | sort -z)

if [ "${#FILES[@]}" -eq 0 ]; then
  echo "在 $SRC 找不到音訊檔。"
  exit 0
fi

echo "==> 從 $SRC 收集 ${#FILES[@]} 個音訊"
n=0
for f in "${FILES[@]}"; do
  n=$((n+1))
  base="$(basename "$f")"
  if [ "$RENAME" -eq 1 ]; then
    ext="${f##*.}"
    dest="$DST/$(printf 'track%02d.%s' "$n" "$ext")"
  else
    dest="$DST/$base"
  fi
  if [ "$MODE" = "move" ]; then
    mv -n "$f" "$dest"
    echo "  移動 $base -> $(basename "$dest")"
  else
    cp -n "$f" "$dest"
    echo "  複製 $base -> $(basename "$dest")"
  fi
done

echo
echo "✅ 完成。${DST} 目前內容："
ls -1 "$DST" | sed 's/^/  /'
