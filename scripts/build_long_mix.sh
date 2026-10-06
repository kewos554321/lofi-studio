#!/usr/bin/env bash
#
# build_long_mix.sh
# 將多首短曲交叉淡入（crossfade）串成一首長片，並正規化到 -14 LUFS。
#
# 用法:
#   ./build_long_mix.sh OUTPUT CROSSFADE_SECONDS TRACK1 TRACK2 [TRACK3 ...]
#
# 範例:
#   ./build_long_mix.sh output/mixes/mix_1hr.wav 8 assets/tracks/*.wav
#   ./build_long_mix.sh output/mixes/mix.wav 6 assets/tracks/a.wav assets/tracks/b.wav
#
# OUTPUT 副檔名決定格式: .wav / .flac / .mp3 / .m4a
# CROSSFADE_SECONDS 建議 lofi 用 6-10 秒。
#
set -euo pipefail

# ---------- 參數檢查 ----------
if [ "$#" -lt 3 ]; then
  echo "用法: $0 OUTPUT.{wav|flac|mp3|m4a} CROSSFADE_SECONDS TRACK1 TRACK2 [TRACK3 ...]" >&2
  exit 1
fi

OUT="$1"
XF="$2"
shift 2
TRACKS=("$@")
N=${#TRACKS[@]}

command -v ffmpeg  >/dev/null 2>&1 || { echo "錯誤: 找不到 ffmpeg" >&2; exit 1; }
command -v ffprobe >/dev/null 2>&1 || { echo "錯誤: 找不到 ffprobe" >&2; exit 1; }

for t in "${TRACKS[@]}"; do
  [ -f "$t" ] || { echo "錯誤: 找不到音檔 $t" >&2; exit 1; }
done

mkdir -p "$(dirname "$OUT")"

SR=44100                # 統一取樣率
TARGET_I=-14            # 目標整體響度 (LUFS)
TARGET_TP=-1.0          # 目標真峰值 (dBTP)
TARGET_LRA=11

# 依副檔名決定輸出編碼
case "${OUT##*.}" in
  wav)  ACODEC="pcm_s24le" ;;
  flac) ACODEC="flac" ;;
  mp3)  ACODEC="libmp3lame" ;;
  m4a)  ACODEC="aac" ;;
  *)    echo "警告: 未知副檔名，改用 wav(pcm_s24le)"; ACODEC="pcm_s24le"; OUT="${OUT%.*}.wav" ;;
esac

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
RAW="$WORK/_crossfaded.wav"

# ---------- 1) 建立 filtergraph: 每軌先統一格式，再串接 acrossfade ----------
echo "==> 交叉淡入 $N 首（每段 ${XF}s）..."
INPUTS=()
for t in "${TRACKS[@]}"; do INPUTS+=(-i "$t"); done

FILTER=""
PREV=""
for ((i=0; i<N; i++)); do
  FILTER+="[${i}:a]aformat=sample_fmts=fltp:sample_rates=${SR}:channel_layouts=stereo[ax${i}];"
  if [ -z "$PREV" ]; then
    PREV="ax${i}"
  else
    FILTER+="[${PREV}][ax${i}]acrossfade=d=${XF}:c1=tri:c2=tri[xo${i}];"
    PREV="xo${i}"
  fi
done
FILTER="${FILTER%;}"

ffmpeg -hide_banner -loglevel error -y "${INPUTS[@]}" \
  -filter_complex "$FILTER" -map "[${PREV}]" -c:a pcm_s24le "$RAW"

# ---------- 2) 量測響度（two-pass loudnorm）----------
echo "==> 量測響度..."
MEASURE_RAW="$WORK/_loudnorm.txt"
ffmpeg -hide_banner -nostats -i "$RAW" \
  -af "loudnorm=I=${TARGET_I}:TP=${TARGET_TP}:LRA=${TARGET_LRA}:print_format=json" \
  -f null - 2> "$MEASURE_RAW" || true

# 用 python 解析量測 JSON；找不到 python 就退回 one-pass
PYBIN=""
for c in python3 python3.11 python; do
  if command -v "$c" >/dev/null 2>&1; then PYBIN="$c"; break; fi
done

echo "==> 正規化到 ${TARGET_I} LUFS 並輸出..."
if [ -n "$PYBIN" ]; then
  eval "$("$PYBIN" - "$MEASURE_RAW" <<'PY'
import json, sys, re
txt = open(sys.argv[1], encoding="utf-8", errors="ignore").read()
blocks = re.findall(r'\{[^{}]*\}', txt)
if not blocks:
    sys.exit(0)
d = json.loads(blocks[-1])
print(f"MI={d['input_i']}")
print(f"MT={d['input_tp']}")
print(f"ML={d['input_lra']}")
print(f"MTH={d['input_thresh']}")
print(f"MO={d['target_offset']}")
PY
)"
fi

if [ -n "${MI:-}" ]; then
  ffmpeg -hide_banner -loglevel error -y -i "$RAW" \
    -af "loudnorm=I=${TARGET_I}:TP=${TARGET_TP}:LRA=${TARGET_LRA}:measured_I=${MI}:measured_TP=${MT}:measured_LRA=${ML}:measured_thresh=${MTH}:offset=${MO}:linear=true" \
    -ar "$SR" -c:a "$ACODEC" "$OUT"
else
  echo "   (量測解析失敗，改用 one-pass loudnorm)"
  ffmpeg -hide_banner -loglevel error -y -i "$RAW" \
    -af "loudnorm=I=${TARGET_I}:TP=${TARGET_TP}:LRA=${TARGET_LRA}" \
    -ar "$SR" -c:a "$ACODEC" "$OUT"
fi

# ---------- 3) 回報 ----------
DUR=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$OUT")
echo
echo "✅ 完成: $OUT"
awk -v d="$DUR" 'BEGIN{printf "   時長: %d 分 %02d 秒 (%.1f s)\n", d/60, d%60, d}'
ffmpeg -hide_banner -nostats -i "$OUT" -af "ebur128=peak=true" -f null - 2>&1 \
  | awk '/Integrated loudness/,/LRA:/' | grep -E "I:|LRA:" | sed 's/^/   /' || true
