#!/usr/bin/env bash
#
# render_video.sh
# 把「視覺循環影片」與「長片音訊」合成為最終 YouTube / OBS 用的 mp4。
# 視覺會無限循環直到音訊結束。
#
# 用法:
#   ./render_video.sh VISUAL_LOOP AUDIO OUTPUT.mp4 [FPS=30] [CRF=20]
#
# 範例:
#   ./render_video.sh output/visual_loop.mp4 output/mixes/mix_1hr.wav output/videos/lofi_1hr.mp4
#
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "用法: $0 VISUAL_LOOP AUDIO OUTPUT.mp4 [FPS=30] [CRF=20]" >&2
  exit 1
fi

VIS="$1"
AUD="$2"
OUT="$3"
FPS="${4:-30}"
CRF="${5:-20}"
PRESET="${PRESET:-medium}"

command -v ffmpeg >/dev/null 2>&1 || { echo "錯誤: 找不到 ffmpeg" >&2; exit 1; }
[ -f "$VIS" ] || { echo "錯誤: 找不到視覺檔 $VIS" >&2; exit 1; }
[ -f "$AUD" ] || { echo "錯誤: 找不到音訊檔 $AUD" >&2; exit 1; }

mkdir -p "$(dirname "$OUT")"

# 用音訊實際長度當作影片長度，避免 -stream_loop 收尾不精準
ADUR=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$AUD")

echo "==> 合成影片: $VIS + $AUD -> $OUT (${ADUR}s)"
ffmpeg -hide_banner -loglevel error -y \
  -stream_loop -1 -i "$VIS" \
  -i "$AUD" \
  -map 0:v:0 -map 1:a:0 \
  -t "$ADUR" \
  -c:v libx264 -preset "$PRESET" -crf "$CRF" -pix_fmt yuv420p -r "$FPS" \
  -maxrate 8M -bufsize 16M \
  -c:a aac -b:a 192k -ar 44100 \
  -shortest -movflags +faststart "$OUT"

DUR=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$OUT")
SIZE=$(du -h "$OUT" | cut -f1)
echo "✅ 完成: $OUT (${DUR}s, ${SIZE})"
