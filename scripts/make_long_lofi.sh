#!/usr/bin/env bash
#
# make_long_lofi.sh
# 產生 N 分鐘 lofi 影片。設計目標：**重運算只做短的一次，長片用複製（零重編碼）**，
# 避免 CPU/GPU 長時間滿載而過熱。
#
# 原理：
#   1. 視覺只算一個短 loop（預設 20s，且 20 整除 600），再用 concat -c copy 複製成整部片。
#   2. 升頻/淡入淡出用硬體編碼 h264_videotoolbox，不用 libx264 medium。
#   3. 重步驟之間 sleep 降溫；可用環境變數 THREADS / NICE 調整。
#
# 用法:
#   ./scripts/make_long_lofi.sh --generate                     # 生一張新圖再做成 10 分鐘
#   ./scripts/make_long_lofi.sh --image assets/visuals/x.png   # 沿用既有圖（不開 ComfyUI，最不熱）
#
# 常用選項:
#   --minutes 10         影片長度（分），預設 10
#   --loop 20            loop 秒數，必須整除總長，預設 20
#   --fps 30
#   --image PATH         沿用既有圖
#   --generate           用 ComfyUI 生一張新圖（單張，生完即停）
#   --ckpt NAME          checkpoint，預設 meinamix_meinaV11.safetensors
#   --prompt "..."       生圖 prompt（預設：靠窗書桌的動漫女生，無雨）
#   --negative "..."     負向 prompt（預設允許 1girl）
#   --size 768x512       生圖尺寸
#   --tracks A B C ...   串接的歌曲（預設 6 首）
#   --xfade 8            交叉淡入秒數
#   --cg-args "..."      額外傳給 make_cinemagraph.py 的參數（例: --steam 1.2 ...）
#   --no-video-fade      不做影像淡入淡出（可省一次重編碼，最省電）
#   --keep-temp          保留中間檔（預設成功後自動刪除，省空間）
#   --vbitrate N         最終影片位元率 Mbps（預設 9；lofi 用 4~5 就很夠，檔案小一半）
#   --out PATH           輸出檔，預設 output/videos/lofi_<minutes>min.mp4
#   --episode NAME       一集一包：全部輸出放 output/episodes/<NAME>/（video.mp4 + mix.wav + visual_loop.mp4）
#   --style NAME         渲染後自動產生上片資訊（publish/；style 見 prompts/styles/）
#   --no-meta            不要產生上片資訊
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MIN=10
LOOP=20
FPS=30
XF=8
KEEP=0
VBR=9
IMAGE=""
GENERATE=0
CKPT="meinamix_meinaV11.safetensors"
PREFIX="lofi10scene"
SIZE="768x512"
PROMPT="1girl, solo, anime girl sitting at a cozy desk by the window at night, warm desk lamp, coffee mug, books, potted plant, long hair, looking at viewer, lo-fi anime illustration, soft warm lighting, detailed background, muted colors, nostalgic, masterpiece, best quality"
NEG="lowres, blurry, bad anatomy, bad hands, extra digits, fewer digits, bad proportions, 2girls, multiple girls, extra limbs, watermark, signature, text, error, jpeg artifacts"
CG_ARGS=""
FADE=1
OUT=""
STYLE=""
EPISODE=""
META=1
THREADS="${THREADS:-0}"
NICE="${NICE:-10}"
TRACKS=(
  assets/tracks/rainy_lofi/legacy/rainy_lofi_01_00001.mp3
  assets/tracks/jazzhop_lounge/legacy/jazzhop_lounge_01_00001.mp3
  assets/tracks/cozy_morning/legacy/cozy_morning_01_00001.mp3
  assets/tracks/tokyo_night/legacy/tokyo_night_01_00001.mp3
  assets/tracks/focus_minimal/legacy/focus_minimal_01_00001.mp3
  assets/tracks/rnb_soul/legacy/rnb_soul_01_00001.mp3
)

while [ $# -gt 0 ]; do
  case "$1" in
    --minutes) MIN="$2"; shift 2;;
    --loop)    LOOP="$2"; shift 2;;
    --fps)     FPS="$2"; shift 2;;
    --xfade)   XF="$2"; shift 2;;
    --image)   IMAGE="$2"; shift 2;;
    --generate) GENERATE=1; shift;;
    --ckpt)    CKPT="$2"; shift 2;;
    --prefix)  PREFIX="$2"; shift 2;;
    --prompt)  PROMPT="$2"; shift 2;;
    --negative) NEG="$2"; shift 2;;
    --size)    SIZE="$2"; shift 2;;
    --cg-args) CG_ARGS="$2"; shift 2;;
    --no-video-fade) FADE=0; shift;;
    --keep-temp) KEEP=1; shift;;
    --vbitrate) VBR="$2"; shift 2;;
    --out)     OUT="$2"; shift 2;;
    --episode) EPISODE="$2"; shift 2;;
    --style)   STYLE="$2"; shift 2;;
    --no-meta) META=0; shift;;
    --tracks)  shift; TRACKS=(); while [ $# -gt 0 ] && [ "${1:0:2}" != "--" ]; do TRACKS+=("$1"); shift; done;;
    -h|--help) sed -n '2,40p' "$0"; exit 0;;
    *) echo "未知參數: $1（用 --help）" >&2; exit 1;;
  esac
done

TOTAL=$((MIN * 60))

# ---------- 輸出佈局 ----------
# --episode NAME：一集一包，全部放 output/episodes/<NAME>/（好備份、好上片、好清理）
# 未指定：維持舊行為（output/videos + output/mixes）
if [ -n "$EPISODE" ]; then
  WORK="output/episodes/$EPISODE"
  mkdir -p "$WORK"
  [ -z "$OUT" ] && OUT="$WORK/video.mp4"
  CINE_LOOP="$WORK/visual_loop.mp4"
  LOOP_1080="$WORK/_loop_1080.mp4"
  MIX_RAW="$WORK/_mix_raw.wav"
  MIX_FINAL="$WORK/mix.wav"
  VID_COPY="$WORK/_video_copy.mp4"
  LIST="$WORK/_loop_list.txt"
else
  [ -z "$OUT" ] && OUT="output/videos/lofi_${MIN}min.mp4"
  CINE_LOOP="output/lofi_loop.mp4"
  LOOP_1080="output/lofi_loop_1080.mp4"
  MIX_RAW="output/mixes/lofi_long_raw.wav"
  MIX_FINAL="output/mixes/lofi_long_${TOTAL}.wav"
  VID_COPY="output/lofi_video_${TOTAL}.mp4"
  LIST="output/_loop_list.txt"
fi

# 用 nice 包裝（nice -n 0 也合法，故陣列永遠非空，避免 bash3.2 + set -u 的空陣列問題）
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
NICEP=(nice -n "$NICE")

command -v ffmpeg >/dev/null || { echo "錯誤: 找不到 ffmpeg" >&2; exit 1; }

if [ $((TOTAL % LOOP)) -ne 0 ]; then
  echo "錯誤: --loop ${LOOP}s 必須整除總長 ${TOTAL}s。建議 loop=20（600/20=30）" >&2
  exit 1
fi

# ---------- 空間 preflight（避免做到一半爆碟）----------
OUTDIR="$(dirname "$OUT")"
mkdir -p "$OUTDIR"
# 預估：最終片 + concat 中間片（約同步存在）+ 混音 wav；乘 2.2 當餘裕
NEED_GB=$(awk -v v="$VBR" -v t="$TOTAL" 'BEGIN{printf "%.2f", v*t/8/1024*2.2 + 0.3}')
FREE_GB=$(df -Pk "$OUTDIR" 2>/dev/null | awk 'NR==2{printf "%.1f", $4/1024/1024}')
MIN_FREE_GB="${MIN_FREE_GB:-20}"
if [ "${SKIP_DISK_CHECK:-0}" != "1" ] && [ -n "${FREE_GB:-}" ]; then
  LOW=$(awk -v f="$FREE_GB" -v n="$NEED_GB" -v m="$MIN_FREE_GB" 'BEGIN{print (f < n+m)?"1":"0"}')
  HARD=$(awk -v f="$FREE_GB" -v n="$NEED_GB" 'BEGIN{print (f < n+5)?"1":"0"}')
  echo "==> 空間檢查: $OUTDIR 可用 ${FREE_GB}GB / 預估需要約 ${NEED_GB}GB"
  if [ "$HARD" = 1 ]; then
    echo "錯誤: 可用空間不足以完成輸出。" >&2
    echo "      清理: ./scripts/cleanup_outputs.sh --apply" >&2
    echo "      或把 output 指向外接碟；仍要繼續: SKIP_DISK_CHECK=1 $0 ..." >&2
    exit 1
  elif [ "$LOW" = 1 ]; then
    echo "⚠️  警告: 剩餘空間偏低（低於 需要+${MIN_FREE_GB}GB），過程可能不足。"
  fi
fi

[ "$FADE" = 1 ] || FADEMSG="（無影像淡入淡出）"
echo "=================================================="
echo " 目標: ${MIN} 分鐘 ($TOTAL s) | loop ${LOOP}s x $((TOTAL/LOOP)) | ${FPS}fps"
echo " 散熱: nice=$NICE threads=${THREADS:-auto} | 硬體編碼 videotoolbox ${FADEMSG:-+淡化}"
echo "=================================================="
"${NICEP[@]}" true 2>/dev/null || true
pmset -g therm 2>/dev/null | sed 's/^/  /' || true

mkdir -p "$(dirname "$OUT")" "$(dirname "$MIX_FINAL")"

# ---------- 階段 A：取得場景圖 ----------
echo
echo "==> [A] 場景圖"
if [ -n "$IMAGE" ]; then
  [ -f "$IMAGE" ] || { echo "錯誤: 找不到 --image $IMAGE" >&2; exit 1; }
  echo "    沿用既有圖（未開 ComfyUI）：$IMAGE"
elif [ "$GENERATE" = 1 ]; then
  curl -s --max-time 5 http://127.0.0.1:8188/system_stats >/dev/null \
    || { echo "錯誤: ComfyUI 未啟動（127.0.0.1:8188）" >&2; exit 1; }
  echo "    生成新圖（唯一 GPU 步驟，約 40s）…"
  GENOUT="$("$PY" scripts/generate_visual.py --count 1 --size "$SIZE" \
            --ckpt "$CKPT" --prefix "$PREFIX" --prompt "$PROMPT" --negative "$NEG")"
  echo "$GENOUT" | sed 's/^/    /'
  IMAGE="$(printf '%s\n' "$GENOUT" | grep -o 'assets/visuals/[^ ]*\.png' | tail -1 || true)"
  [ -n "$IMAGE" ] && [ -f "$IMAGE" ] || { echo "錯誤: 無法取得生成圖路徑" >&2; exit 1; }
else
  echo "錯誤: 請指定 --image 或 --generate" >&2; exit 1
fi
echo "    -> $IMAGE"
echo "    冷卻 10s…"; sleep 10

# ---------- 階段 B：短 loop（CPU，唯一較重的算圖）----------
echo
echo "==> [B] 產生 ${LOOP}s 無縫 loop @ ${FPS}fps"
# shellcheck disable=SC2086
"${NICEP[@]}" "$PY" scripts/make_cinemagraph.py "$IMAGE" "$CINE_LOOP" \
  --duration "$LOOP" --fps "$FPS" $CG_ARGS

echo "    升到 1080p / 16:9（硬體編碼）…"
"${NICEP[@]}" ffmpeg -hide_banner -loglevel error -y -i "$CINE_LOOP" \
  -vf "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080" \
  -c:v h264_videotoolbox -b:v 9M -pix_fmt yuv420p "$LOOP_1080"
echo "    冷卻 8s…"; sleep 8

# ---------- 階段 C：10 分鐘音訊（CPU 極輕）----------
echo
echo "==> [C] 音樂：交叉淡入 ${#TRACKS[@]} 首（xfade ${XF}s）"
./scripts/build_long_mix.sh "$MIX_RAW" "$XF" "${TRACKS[@]}"
echo "    裁成 ${TOTAL}s 並加頭尾 2s 淡入淡出…"
AF="afade=t=in:d=2,afade=t=out:st=$((TOTAL-2)):d=2"
"${NICEP[@]}" ffmpeg -hide_banner -loglevel error -y -i "$MIX_RAW" \
  -threads "$THREADS" -af "$AF" -t "$TOTAL" -c:a pcm_s24le "$MIX_FINAL"
echo "    冷卻 5s…"; sleep 5

# ---------- 階段 D：合成 10 分鐘母帶（複製，零重編碼）----------
echo
echo "==> [D] 複製 loop 成 ${MIN} 分鐘（concat + copy，秒級、不重編碼）"
: > "$LIST"
for _ in $(seq 1 $((TOTAL / LOOP))); do
  printf "file '%s/%s'\n" "$ROOT" "$LOOP_1080" >> "$LIST"
done
"${NICEP[@]}" ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i "$LIST" \
  -threads "$THREADS" -c copy -t "$TOTAL" "$VID_COPY"
rm -f "$LIST"

echo "    最後封裝（只編音訊；影像 copy 或硬體淡化）…"
if [ "$FADE" = 1 ]; then
  "${NICEP[@]}" ffmpeg -hide_banner -loglevel error -y \
    -i "$VID_COPY" -i "$MIX_FINAL" \
    -map 0:v:0 -map 1:a:0 \
    -threads "$THREADS" -vf "fade=t=in:st=0:d=2,fade=t=out:st=$((TOTAL-2)):d=2" \
    -c:v h264_videotoolbox -b:v "${VBR}M" -pix_fmt yuv420p \
    -c:a aac -b:a 192k -ar 44100 -t "$TOTAL" -movflags +faststart "$OUT"
else
  "${NICEP[@]}" ffmpeg -hide_banner -loglevel error -y \
    -i "$VID_COPY" -i "$MIX_FINAL" \
    -map 0:v:0 -map 1:a:0 \
    -c:v copy -c:a aac -b:a 192k -ar 44100 -t "$TOTAL" -movflags +faststart "$OUT"
fi

# ---------- 清中間檔（預設）----------
if [ "$KEEP" = 0 ]; then
  if [ -n "$EPISODE" ]; then
    rm -f "$LOOP_1080" "$VID_COPY" "$MIX_RAW" "$LIST"
    echo "   （保留 video.mp4 / mix.wav / visual_loop.mp4；其餘中間檔已清）"
  else
    rm -f "$VID_COPY" "$MIX_RAW" "$MIX_FINAL" "$CINE_LOOP" "$LOOP_1080" "$LIST"
    echo "   （已清除中間檔；加 --keep-temp 可保留）"
  fi
fi

# ---------- 回報 ----------
echo
DUR=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$OUT")
RES=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=s=x:p=0 "$OUT")
SIZE=$(du -h "$OUT" | cut -f1)
echo "✅ 完成: $OUT"
awk -v d="$DUR" -v r="$RES" -v s="$SIZE" 'BEGIN{printf "   %s  %d分%02d秒  %s\n", r, d/60, d%60, s}'
echo
# ---------- 上片資訊（YouTube title/description/chapters/tags）----------
if [ "$META" = 1 ] && [ -n "$STYLE" ]; then
  echo "==> 產生上片資訊（style=${STYLE}）…"
  NAME_ARG=()
  [ -n "$EPISODE" ] && NAME_ARG=(--name "$EPISODE")
  "${NICEP[@]}" "$PY" scripts/make_meta.py --video "$OUT" --style "$STYLE" "${NAME_ARG[@]}" \
      --xfade "$XF" --tracks "${TRACKS[@]}" || echo "   （上片資訊產生失敗，可稍後手動跑 make_meta.py）"
  echo
fi
echo "散熱狀態："; pmset -g therm 2>/dev/null | sed 's/^/  /' || true
echo "（若想更保守：THREADS=4 NICE=15 ./scripts/make_long_lofi.sh ...）"
