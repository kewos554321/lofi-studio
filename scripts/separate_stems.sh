#!/usr/bin/env bash
#
# separate_stems.sh
# 用 Demucs 把音檔拆成 stems。lofi 最常用的是「去人聲」（--two-stems=vocals）。
#
# 用法:
#   ./separate_stems.sh INPUT.wav                 # 拆四軌 drums/bass/vocals/other
#   ./separate_stems.sh INPUT.wav vocals          # 只拆 vocals / no_vocals
#   ./separate_stems.sh INPUT.wav vocals OUTDIR
#
# 輸出預設在 output/stems/ 下。
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"

if [ "$#" -lt 1 ]; then
  echo "用法: $0 INPUT.wav [vocals|four] [OUTDIR]" >&2
  exit 1
fi

INPUT="$1"
MODE="${2:-four}"
OUTDIR="${3:-$ROOT/output/stems}"

[ -f "$INPUT" ] || { echo "錯誤: 找不到音檔 $INPUT" >&2; exit 1; }
mkdir -p "$OUTDIR"

# 優先用專案 venv 內的 demucs，其次系統 PATH
DEMUCS=""
if [ -x "$ROOT/.venv/bin/demucs" ]; then
  DEMUCS="$ROOT/.venv/bin/demucs"
elif command -v demucs >/dev/null 2>&1; then
  DEMUCS="$(command -v demucs)"
else
  echo "錯誤: 找不到 demucs。" >&2
  echo "請先執行: $ROOT/scripts/setup_python_env.sh" >&2
  exit 1
fi

echo "==> Demucs 分軌: $INPUT (mode=$MODE)"
if [ "$MODE" = "vocals" ]; then
  "$DEMUCS" -n htdemucs --two-stems=vocals -o "$OUTDIR" "$INPUT"
else
  "$DEMUCS" -n htdemucs -o "$OUTDIR" "$INPUT"
fi

BASE="$(basename "${INPUT%.*}")"
echo
echo "✅ 完成，輸出在: $OUTDIR/htdemucs/$BASE/"
ls -1 "$OUTDIR/htdemucs/$BASE/" 2>/dev/null | sed 's/^/   /' || true
