#!/usr/bin/env bash
#
# make_visual_loop.sh
# 從一張靜態圖產生「可無縫循環」的細微動態影片（緩慢平移 + 底片顆粒 + 暗角）。
# 產出約 10-20 秒的 loop，之後可無限循環成 1 小時影片。
#
# 用法:
#   ./make_visual_loop.sh INPUT_IMAGE OUTPUT.mp4 [DURATION_SEC] [FPS]
#
# 範例:
#   ./make_visual_loop.sh assets/visuals/desk.png output/visual_loop.mp4 15 30
#
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "用法: $0 INPUT_IMAGE OUTPUT.mp4 [DURATION_SEC=15] [FPS=30]" >&2
  exit 1
fi

IMG="$1"
OUT="$2"
DUR="${3:-15}"
FPS="${4:-30}"

W=1920
H=1080
SCALE_W=2112   # 110% 放大，留出平移空間

command -v ffmpeg >/dev/null 2>&1 || { echo "錯誤: 找不到 ffmpeg" >&2; exit 1; }
[ -f "$IMG" ] || { echo "錯誤: 找不到圖片 $IMG" >&2; exit 1; }

mkdir -p "$(dirname "$OUT")"

echo "==> 產生無縫循環: $IMG -> $OUT (${DUR}s @ ${FPS}fps)"
# -loop 1: 靜圖重複; crop 的 x 用 sin(2*PI*t/DUR) 保證頭尾接得起來。
ffmpeg -hide_banner -loglevel error -y \
  -loop 1 -i "$IMG" -t "$DUR" -r "$FPS" \
  -vf "scale=${SCALE_W}:-2:flags=lanczos,\
crop=${W}:${H}:x='(in_w-out_w)/2*(1+sin(2*PI*t/${DUR}))':y='(in_h-out_h)/2',\
noise=alls=5:allf=p,\
vignette=PI/5,\
eq=saturation=1.05:contrast=1.02:brightness=-0.01,\
format=yuv420p" \
  -c:v libx264 -preset medium -crf 18 -movflags +faststart "$OUT"

DUR_OUT=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$OUT")
echo "✅ 完成: $OUT (${DUR_OUT}s)"
