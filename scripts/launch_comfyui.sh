#!/usr/bin/env bash
#
# launch_comfyui.sh
# 啟動 ComfyUI（Mac / Apple Silicon MPS）。
# 預設加上 PYTORCH_ENABLE_MPS_FALLBACK=1，遇到 MPS 未支援的運算子會退回 CPU。
#
# 用法:
#   ./launch_comfyui.sh                 # 用 16GB 建議設定啟動
#   ./launch_comfyui.sh --cpu           # 強制 CPU
#   COMFYUI_DIR=/path ./launch_comfyui.sh
#
set -euo pipefail

COMFY="${COMFYUI_DIR:-$HOME/ComfyUI}"
[ -x "$COMFY/venv/bin/python" ] || { echo "錯誤: 找不到 $COMFY/venv，請先建立環境" >&2; exit 1; }

# 對不支援的運算子回退 CPU（Mac 上跑 ACE-Step / SD 很重要）
export PYTORCH_ENABLE_MPS_FALLBACK=1
# 降低記憶體碎片
export PYTORCH_MPS_HIGH_WATERMARK_RATIO="${PYTORCH_MPS_HIGH_WATERMARK_RATIO:-0.0}"

cd "$COMFY"
echo "==> 啟動 ComfyUI（MPS，fallback=on）"
echo "    開啟瀏覽器: http://127.0.0.1:8188"
exec venv/bin/python main.py --force-fp16 "$@"
