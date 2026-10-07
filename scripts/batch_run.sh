#!/usr/bin/env bash
#
# batch_run.sh
# 量產 runner：把一張 CSV 切成多批餵給 batch_generate.py，自動記錄進度與 log。
# 依賴 batch_generate.py 的 --run-id 續傳能力：中斷後用同一個 --run-id 重跑即可接續。
#
# 用法:
#   ./scripts/batch_run.sh --csv prompts/generated/dusk_jazz.csv
#   ./scripts/batch_run.sh --csv ... --chunk 50 --sleep 60 --run-id dusk01 --clean-raw
#   ./scripts/batch_run.sh --csv ... --total 1000 --rest 300      # 每批之間多休息 5 分鐘
#   ./scripts/batch_run.sh --csv ... --gen-args "--duration 150 --steps 10"
#
# 前置：ComfyUI 需執行中（scripts/launch_comfyui.sh）。
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CSV=""
CHUNK=50
SLEEP=60
REST=0
TOTAL=0
RUN_ID=""
CLEAN=0
GEN_ARGS=""
DRY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --csv)      CSV="$2"; shift 2;;
    --chunk)    CHUNK="$2"; shift 2;;
    --sleep)    SLEEP="$2"; shift 2;;
    --rest)     REST="$2"; shift 2;;
    --total)    TOTAL="$2"; shift 2;;
    --run-id)   RUN_ID="$2"; shift 2;;
    --clean-raw) CLEAN=1; shift;;
    --gen-args) GEN_ARGS="$2"; shift 2;;
    --dry-run)  DRY=1; shift;;
    -h|--help)  sed -n '2,17p' "$0"; exit 0;;
    *) echo "未知參數: $1（用 --help）" >&2; exit 1;;
  esac
done

[ -n "$CSV" ] || { echo "錯誤: 需要 --csv PATH" >&2; exit 1; }
[ -f "$CSV" ] || { echo "錯誤: 找不到 $CSV" >&2; exit 1; }

if [ -z "$RUN_ID" ]; then
  RUN_ID="r$(date +%Y%m%d-%H%M%S)"
fi
if [ "$TOTAL" -eq 0 ]; then
  TOTAL=$(( $(wc -l < "$CSV") - 1 ))
fi

# log 一律寫內接（外接碟掉線時才不會丟；輸出檔才是外接）
mkdir -p logs
LOG="logs/batch_${RUN_ID}.log"
CLEAN_FLAG=""
[ "$CLEAN" = 1 ] && CLEAN_FLAG="--clean-raw"
DRY_FLAG=""
[ "$DRY" = 1 ] && DRY_FLAG="--dry-run"

echo "=================================================="
echo " 量產 runner"
echo "   CSV      : ${CSV}（${TOTAL} 首）"
echo "   run-id   : ${RUN_ID}   （續傳請沿用同一個）"
echo "   chunk    : ${CHUNK} 首/批 | 每首散熱 ${SLEEP}s | 每批休息 ${REST}s"
echo "   log      : $LOG"
echo "=================================================="

# 外接碟（output）連通性檢查：掉了只警告，生成（存內接）仍可繼續
if ! ( : > output/.mount_check ) 2>/dev/null; then
  echo "⚠️  警告: output 無法寫入（外接碟 /Volumes/WJ_SATA 可能掉線）。"
  echo "         生成照常（音檔在內接 assets/tracks），但影片輸出/清理會失敗。"
else
  rm -f output/.mount_check
fi

if [ "$DRY" = 0 ]; then
  curl -s --max-time 5 http://127.0.0.1:8188/system_stats >/dev/null \
    || { echo "錯誤: ComfyUI 未啟動（127.0.0.1:8188）。先跑 scripts/launch_comfyui.sh" >&2; exit 1; }
fi

{
  echo "### batch_run start $(date '+%F %T') | csv=$CSV total=$TOTAL run=$RUN_ID"
} >> "$LOG"

off=0
batch=0
while [ "$off" -lt "$TOTAL" ]; do
  batch=$((batch+1))
  lim=$CHUNK
  [ $((off + lim)) -gt "$TOTAL" ] && lim=$((TOTAL - off))
  echo
  echo "==> 第 ${batch} 批：offset=${off} limit=${lim}（進度 ${off}/${TOTAL}）"
  # shellcheck disable=SC2086
  python3 scripts/batch_generate.py --csv "$CSV" --offset "$off" --limit "$lim" \
      --run-id "$RUN_ID" --sleep "$SLEEP" $CLEAN_FLAG $DRY_FLAG $GEN_ARGS 2>&1 | tee -a "$LOG"
  off=$((off + lim))
  echo "   進度：${off}/${TOTAL}" | tee -a "$LOG"
  if [ "$REST" -gt 0 ] && [ "$off" -lt "$TOTAL" ]; then
    echo "   批次間休息 ${REST}s…"
    sleep "$REST"
  fi
done

{
  echo "### batch_run done $(date '+%F %T') | 共 ${TOTAL} 首"
} >> "$LOG"

echo
echo "✅ 全部跑完（${TOTAL} 首）。log: $LOG"
echo "   品檢/標籤： python3 scripts/auto_qc.py --all --index && python3 scripts/library.py build"
