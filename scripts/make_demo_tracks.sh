#!/usr/bin/env bash
#
# make_demo_tracks.sh
# 產生 P1 測試用的示範音檔與示範圖片（用 ffmpeg 合成，非 AI 生成）。
# 用途：在還沒接上 ACE-Step 前，先把「交叉淡入 -> 長片 -> 影片」整條管線跑通。
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
TRACKS="$ROOT/assets/tracks/misc/demo"
VIS="$ROOT/assets/visuals"
mkdir -p "$TRACKS" "$VIS"

DUR=8   # 每首示範曲長度（秒）

# 合成一首簡單、帶 lofi 味的示範曲：正弦和弦 + 震音 + 低通 + 回聲 + 底噪
make_track() {
  local out="$1" f1="$2" f2="$3" f3="$4" trem="$5"
  ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "sine=frequency=${f1}:duration=${DUR}" \
    -f lavfi -i "sine=frequency=${f2}:duration=${DUR}" \
    -f lavfi -i "sine=frequency=${f3}:duration=${DUR}" \
    -f lavfi -i "anoisesrc=color=brown:duration=${DUR}:amplitude=0.04" \
    -filter_complex "\
[0:a][1:a][2:a]amix=inputs=3:weights=1 0.5 0.35,volume=0.6,\
tremolo=f=${trem}:d=0.45,lowpass=f=2800,aecho=0.8:0.7:60|140:0.3|0.18[harm];\
[3:a]lowpass=f=7000,volume=0.12[crackle];\
[harm][crackle]amix=inputs=2:duration=first,\
afade=t=in:st=0:d=0.8,afade=t=out:st=$(awk "BEGIN{print ${DUR}-1}"):d=1[out]" \
    -map "[out]" -ar 44100 -ac 2 "$out"
  echo "  產生 $out ($(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$out")s)"
}

echo "==> 產生示範音軌（${DUR}s each）"
make_track "$TRACKS/demo_a.wav" 220 277 330 4
make_track "$TRACKS/demo_b.wav" 196 247 294 5
make_track "$TRACKS/demo_c.wav" 174 220 261 3.5

echo "==> 產生示範圖片"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "gradients=s=1920x1080:c0=0x1a1a2e:c1=0x16213e:c2=0x0f3460:d=1:n=3" \
  -frames:v 1 "$VIS/demo_visual.png"
echo "  產生 $VIS/demo_visual.png"

echo
echo "✅ 示範素材完成於 $TRACKS 與 $VIS"
