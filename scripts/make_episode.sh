#!/usr/bin/env bash
#
# make_episode.sh — 一集的自動化產線，分成五段：音樂 → 圖片 → 影片 → 上傳 → 整理。
#
# 五段各自獨立、可單獨執行（方便測試）；--stage all（預設）則一條龍跑完。
#   1) music   展開清單 -> 生成音樂 -> 品檢 -> 圖書館
#   2) image   ComfyUI + SD1.5 生場景圖
#   3) video   挑 keeper -> 混音 -> cinemagraph -> 合成影片 -> 上片資訊
#   4) upload  上傳到 YouTube（上線前自動；需一次性憑證，見 README「自動上傳設定」）
#   5) cleanup 封存到外接 archive/ 並清中間檔
#
# 用法:
#   ./scripts/make_episode.sh --style rainy_lofi --minutes 60 --count 40      # 五段全跑
#   ./scripts/make_episode.sh --style rainy_lofi --stage music --count 20     # 只跑音樂
#   ./scripts/make_episode.sh --style rainy_lofi --stage image --images 3     # 只生圖
#   ./scripts/make_episode.sh --style rainy_lofi --stage video --episode rl01 # 只合成影片
#   ./scripts/make_episode.sh --style rainy_lofi --stage upload  --episode rl01
#   ./scripts/make_episode.sh --style rainy_lofi --stage cleanup --episode rl01
#   ./scripts/make_episode.sh --style rainy_lofi --episode rl01 --dry-run     # 預覽（不執行）
#
# 常用選項:
#   --stage all|music|image|video|upload|cleanup   要跑哪一段（預設 all）
#   --style NAME                   風格（見 prompts/styles/）
#   --episode NAME                 影片輸出包名（預設 <style>-<MMDD-HHMM>）
#   --minutes N                    影片長度（分，預設 30）
#   --count N                      音樂階段生成曲數（預設 0 = 不生成、只用既有）
#   --run-id ID                    音樂批次碼（預設 = episode）
#   --limit N                      影片用幾首（預設 16；從 library keeper 挑）
#   --tracks A B C ...             影片指定曲目（給了就跳過 library 挑選）
#   --images N                     圖片階段生幾張（預設 1）
#   --image PATH                   影片沿用既有圖（給了就跳過 image 階段）
#   --upscale NAME                 生圖放大模型（預設 RealESRGAN_x4plus_anime_6B.pth；未安裝則跳過）
#   --upscale-out WxH              放大後尺寸（預設生成尺寸×2）
#   --no-upscale                   關閉生圖放大
#   --size WxH                     生圖尺寸（預設 768x512）
#   --ckpt NAME                    生圖 checkpoint（預設 meinamix_meinaV11.safetensors）
#   --seed N                       固定亂數種子（音樂/圖片共用）
#   --chunk N --sleep N            音樂批次大小 / 散熱秒數（預設 20 / 30）
#   --xfade N --loop N --fps N --vbitrate N   傳給 make_long_lofi.sh
#   --cg-args "..."                傳給 make_cinemagraph.py
#   --privacy private|unlisted|public          上傳可見性（預設 private＝上線手動）
#   --archive DIR                  封存目標（預設 output 同層的 archive/）
#   --keep-visual                  封存時保留 visual_loop.mp4
#   --no-upload / --no-cleanup     all 時略過第 4/5 段
#   --smoke                        小測試預設：1 分鐘、生 2 首、1 張圖、不上傳、不整理（可再覆寫）
#   --dry-run                      只印指令，不執行
#
# 前置：ComfyUI 需執行中（music / image 階段）；upload 需 Google 憑證（見 README）。
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

STAGE="all"
STYLE=""
EPISODE=""
MIN=30
COUNT=0
RUN_ID=""
LIMIT=16
IMAGES=1
IMAGE=""
UPSCALE="RealESRGAN_x4plus_anime_6B.pth"
UPSCALE_OUT=""
NO_UPSCALE=0
SIZE="768x512"
CKPT="meinamix_meinaV11.safetensors"
SEED=""
CHUNK=20
SLEEP=30
XFADE=8
LOOP=20
FPS=30
VBR=9
CG_ARGS=""
PRIVACY="private"
ARCHIVE=""
KEEP_VISUAL=0
NO_UPLOAD=0
NO_CLEANUP=0
DRY=0
TRACKS=()
TRACKS_SET=0

# --smoke：小測試預設（1 分鐘、生 2 首、1 張圖、低碼率、不上傳、不整理）；其餘旗標仍可覆寫
SMOKE=0
for _a in "$@"; do [ "$_a" = "--smoke" ] && SMOKE=1; done
if [ "$SMOKE" = 1 ]; then
  MIN=1; COUNT=2; IMAGES=1; VBR=2; XFADE=4; NO_UPLOAD=1; NO_CLEANUP=1
fi

while [ $# -gt 0 ]; do
  case "$1" in
    --stage)   STAGE="$2"; shift 2;;
    --style)   STYLE="$2"; shift 2;;
    --episode) EPISODE="$2"; shift 2;;
    --minutes) MIN="$2"; shift 2;;
    --count)   COUNT="$2"; shift 2;;
    --run-id)  RUN_ID="$2"; shift 2;;
    --limit)   LIMIT="$2"; shift 2;;
    --tracks)  shift; TRACKS=(); TRACKS_SET=1; while [ $# -gt 0 ] && [ "${1:0:2}" != "--" ]; do TRACKS+=("$1"); shift; done;;
    --images)  IMAGES="$2"; shift 2;;
    --image)   IMAGE="$2"; shift 2;;
    --upscale) UPSCALE="$2"; shift 2;;
    --upscale-out) UPSCALE_OUT="$2"; shift 2;;
    --no-upscale) NO_UPSCALE=1; shift;;
    --size)    SIZE="$2"; shift 2;;
    --ckpt)    CKPT="$2"; shift 2;;
    --seed)    SEED="$2"; shift 2;;
    --chunk)   CHUNK="$2"; shift 2;;
    --sleep)   SLEEP="$2"; shift 2;;
    --xfade)   XFADE="$2"; shift 2;;
    --loop)    LOOP="$2"; shift 2;;
    --fps)     FPS="$2"; shift 2;;
    --vbitrate) VBR="$2"; shift 2;;
    --cg-args) CG_ARGS="$2"; shift 2;;
    --privacy) PRIVACY="$2"; shift 2;;
    --archive) ARCHIVE="$2"; shift 2;;
    --keep-visual) KEEP_VISUAL=1; shift;;
    --no-upload) NO_UPLOAD=1; shift;;
    --no-cleanup) NO_CLEANUP=1; shift;;
    --smoke)   shift;;
    --dry-run) DRY=1; shift;;
    -h|--help) awk 'NR==1{next} /^[^#]/{exit} {print}' "$0"; exit 0;;
    *) echo "未知參數: $1（用 --help）" >&2; exit 1;;
  esac
done

[ -n "$STYLE" ] || { echo "錯誤: 需要 --style（見 prompts/styles/）" >&2; exit 1; }
if [ -z "$EPISODE" ]; then
  if [ "$SMOKE" = 1 ]; then EPISODE="${STYLE}-smoke"; else EPISODE="${STYLE}-$(date +%m%d-%H%M)"; fi
fi
[ -z "$RUN_ID" ] && RUN_ID="$EPISODE"

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

if [ $((MIN * 60 % LOOP)) -ne 0 ]; then
  echo "錯誤: --loop ${LOOP}s 必須整除影片長度 $((MIN * 60))s（例：--loop 20 對 30/60 分都整除）" >&2
  exit 1
fi

# 執行（--dry-run 只印）
run() {
  if [ "$DRY" = 1 ]; then
    printf '    [dry-run] %s\n' "$*"
    return 0
  fi
  "$@"
}

need_comfy() {
  [ "$DRY" = 1 ] && return 0
  curl -s --max-time 5 http://127.0.0.1:8188/system_stats >/dev/null \
    || { echo "錯誤: ComfyUI 未啟動（127.0.0.1:8188），請先 ./scripts/launch_comfyui.sh" >&2; exit 1; }
}

# ---------- 第 1 段：音樂 ----------
stage_music() {
  echo
  echo "===== [1/5] 音樂 (music) ====="
  if [ "$COUNT" -le 0 ]; then
    echo "  --count 0：跳過生成，沿用既有曲目 assets/tracks/${STYLE}/"
    return 0
  fi
  need_comfy
  local csv="prompts/generated/${STYLE}.csv"
  local sargs=()
  if [ -n "$SEED" ]; then sargs=(--seed "$SEED"); fi
  run "$PY" scripts/expand_style.py --style "$STYLE" --count "$COUNT" "${sargs[@]+"${sargs[@]}"}" --out "$csv"
  run ./scripts/batch_run.sh --csv "$csv" --chunk "$CHUNK" --sleep "$SLEEP" --run-id "$RUN_ID" --clean-raw
  run "$PY" scripts/auto_qc.py --all --index
  run "$PY" scripts/library.py build
  echo "  → 音樂階段完成（run-id=${RUN_ID}）"
}

# ---------- 第 2 段：圖片 ----------
stage_image() {
  echo
  echo "===== [2/5] 圖片 (image) ====="
  if [ -n "$IMAGE" ]; then
    echo "  指定 --image ${IMAGE}：跳過生圖"
    return 0
  fi
  need_comfy
  local sargs=()
  if [ -n "$SEED" ]; then sargs=(--seed "$SEED"); fi
  local uargs=()
  if [ "$NO_UPSCALE" = 0 ]; then
    if [ -f "$HOME/ComfyUI/models/upscale_models/${UPSCALE}" ]; then
      uargs=(--upscale "$UPSCALE")
      if [ -n "$UPSCALE_OUT" ]; then uargs+=(--upscale-out "$UPSCALE_OUT"); fi
      echo "  AI 放大: ${UPSCALE}${UPSCALE_OUT:+ -> $UPSCALE_OUT}"
    else
      echo "  （未安裝放大模型 ${UPSCALE}；跳過。可跑 ./scripts/download_p2_models.sh --upscale）"
    fi
  fi
  run "$PY" scripts/generate_visual.py --count "$IMAGES" --size "$SIZE" --ckpt "$CKPT" \
      --prefix "$EPISODE" "${sargs[@]+"${sargs[@]}"}" "${uargs[@]+"${uargs[@]}"}"
  echo "  → 圖片階段完成（assets/visuals/${EPISODE}*.png）"
}

# ---------- 第 3 段：影片 ----------
stage_video() {
  echo
  echo "===== [3/5] 影片 (video) ====="

  # --- 選圖 ---
  local img="$IMAGE"
  if [ -z "$img" ] && [ "$DRY" = 0 ]; then
    img="$(ls -t "assets/visuals/${EPISODE}"*.png 2>/dev/null | head -1 || true)"
    if [ -z "$img" ]; then img="$(ls -t assets/visuals/*.png 2>/dev/null | head -1 || true)"; fi
  fi
  if [ "$DRY" = 1 ]; then
    echo "  圖片: ${img:-（自動挑 assets/visuals/ 最新，或 ${EPISODE}*.png）}"
  elif [ -z "$img" ]; then
    echo "  錯誤: 找不到場景圖；請先跑 --stage image 或指定 --image" >&2
    exit 1
  else
    echo "  場景圖: $img"
  fi

  # --- 選曲 ---
  local tracks=()
  if [ "$TRACKS_SET" = 1 ]; then
    tracks=("${TRACKS[@]}")
  elif [ "$DRY" = 0 ]; then
    local t
    while IFS= read -r t; do [ -n "$t" ] && tracks+=("$t"); done < <(
      "$PY" scripts/library.py query --style "$STYLE" --status keep --limit "$LIMIT" --paths 2>/dev/null || true)
    if [ "${#tracks[@]}" -eq 0 ]; then
      while IFS= read -r t; do [ -n "$t" ] && tracks+=("$t"); done < <(
        "$PY" scripts/library.py query --style "$STYLE" --limit "$LIMIT" --paths 2>/dev/null || true)
    fi
  fi
  if [ "$TRACKS_SET" = 1 ] || [ "$DRY" = 0 ]; then
    if [ "${#tracks[@]}" -eq 0 ]; then
      echo "  錯誤: 找不到曲目；請先跑 --stage music（或指定 --tracks）" >&2
      exit 1
    fi
    echo "  曲目: ${#tracks[@]} 首"
  else
    echo "  曲目: （自動從 library 挑 keeper，--limit ${LIMIT}）"
  fi

  # --- 合成 ---
  if [ "$DRY" = 1 ]; then
    echo "  指令: ./scripts/make_long_lofi.sh --image <img> --minutes ${MIN} --episode ${EPISODE} \\"
    echo "          --style ${STYLE} --tracks <曲目> --xfade ${XFADE} --loop ${LOOP} --fps ${FPS} --vbitrate ${VBR} ${CG_ARGS}"
    return 0
  fi
  run ./scripts/make_long_lofi.sh --image "$img" --minutes "$MIN" --episode "$EPISODE" \
      --style "$STYLE" --tracks "${tracks[@]}" --xfade "$XFADE" --loop "$LOOP" \
      --fps "$FPS" --vbitrate "$VBR" $CG_ARGS
}

# ---------- 第 4 段：上傳 ----------
stage_upload() {
  echo
  echo "===== [4/5] 上傳 (upload) ====="
  [ -x "$PY" ] || PY="$(command -v python3)"
  run "$PY" scripts/yt_upload.py --episode "$EPISODE" --privacy "$PRIVACY"
}

# ---------- 第 5 段：整理 ----------
stage_cleanup() {
  echo
  echo "===== [5/5] 整理 (cleanup) ====="
  local dargs=()
  if [ -n "$ARCHIVE" ]; then dargs=(--dest "$ARCHIVE"); fi
  if [ "$KEEP_VISUAL" = 1 ]; then dargs+=(--keep-visual); fi
  run ./scripts/archive_episode.sh --episode "$EPISODE" "${dargs[@]+"${dargs[@]}"}"
}

echo "==> make_episode: style=${STYLE} episode=${EPISODE} stage=${STAGE}$([ "$DRY" = 1 ] && echo " (dry-run)")"

case "$STAGE" in
  all)
    stage_music; stage_image; stage_video
    if [ "$NO_UPLOAD" = 0 ]; then stage_upload; else echo; echo "(略過上傳 --no-upload)"; fi
    if [ "$NO_CLEANUP" = 0 ]; then stage_cleanup; else echo "(略過整理 --no-cleanup)"; fi
    ;;
  music)   stage_music;;
  image)   stage_image;;
  video)   stage_video;;
  upload)  stage_upload;;
  cleanup) stage_cleanup;;
  *) echo "未知 --stage: ${STAGE}（all|music|image|video|upload|cleanup）" >&2; exit 1;;
esac

echo
echo "✅ 完成（stage=${STAGE}, episode=${EPISODE}）"
case "$STAGE" in
  video|all|upload)
    echo "   影片:   output/episodes/${EPISODE}/video.mp4";;
esac
case "$STAGE" in
  upload|all)
    echo "   上片資訊: publish/${EPISODE}.json";;
esac
case "$STAGE" in
  cleanup|all)
    echo "   封存:   <archive>/${EPISODE}/";;
esac
