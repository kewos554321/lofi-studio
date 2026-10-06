#!/usr/bin/env bash
#
# download_p2_models.sh
# 下載 P2 需要的模型：ACE-Step 1.5（分檔精簡版：2B turbo + 0.6B LM + VAE）
# 與 Stable Diffusion 1.5（生視覺圖用），以及 ComfyUI 工作流範本。
#
# 用法:
#   ./download_p2_models.sh              # 全部下載
#   ./download_p2_models.sh --ace        # 只下 ACE-Step
#   ./download_p2_models.sh --sd         # 只下 SD1.5
#
# 支援中斷續傳（curl -C -）。
#
set -uo pipefail

COMFY="${COMFYUI_DIR:-$HOME/ComfyUI}"

if [ ! -d "$COMFY" ]; then
  echo "錯誤: 找不到 ComfyUI 於 $COMFY（可用 COMFYUI_DIR 指定）" >&2
  exit 1
fi

DL=()
case "${1:-all}" in
  --ace) DL=(ace) ;;
  --sd)  DL=(sd) ;;
  all)   DL=(ace sd workflow) ;;
  *) echo "未知參數: $1" >&2; exit 1 ;;
esac

# 檔案規格: "相對路徑|URL|預期大小(bytes)"
ACE_FILES=(
"models/diffusion_models/acestep_v1.5_turbo.safetensors|https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files/diffusion_models/acestep_v1.5_turbo.safetensors|4787825604"
"models/text_encoders/qwen_0.6b_ace15.safetensors|https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files/text_encoders/qwen_0.6b_ace15.safetensors|1191588248"
"models/text_encoders/qwen_1.7b_ace15.safetensors|https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files/text_encoders/qwen_1.7b_ace15.safetensors|3708523360"
"models/vae/ace_1.5_vae.safetensors|https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files/vae/ace_1.5_vae.safetensors|337431732"
)

SD_FILES=(
"models/checkpoints/v1-5-pruned-emaonly-fp16.safetensors|https://huggingface.co/Comfy-Org/stable-diffusion-v1-5-archive/resolve/main/v1-5-pruned-emaonly-fp16.safetensors|2132696762"
)

WORKFLOW_FILES=(
"user/default/workflows/audio_ace_step_1_5_split.json|https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/audio_ace_step_1_5_split.json|0"
"user/default/workflows/audio_ace_step_1_5_checkpoint.json|https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/audio_ace_step_1_5_checkpoint.json|0"
)

human() { awk -v b="${1:-0}" 'BEGIN{if(b>=1073741824)printf "%.2f GB",b/1073741824; else if(b>=1048576)printf "%.1f MB",b/1048576; else printf "%d B",b}'; }

download_one() {
  local entry="$1"
  local rel="${entry%%|*}"; local rest="${entry#*|}"
  local url="${rest%%|*}"; local want="${rest##*|}"
  local dest="$COMFY/$rel"

  mkdir -p "$(dirname "$dest")"
  local have=0
  [ -f "$dest" ] && have=$(stat -f%z "$dest" 2>/dev/null || echo 0)

  if [ "$want" -gt 0 ] && [ "$have" -eq "$want" ]; then
    echo "  ✅ 已存在 $(basename "$dest") ($(human "$have"))"
    return 0
  fi

  echo "  ⬇️  $(basename "$dest")$( [ "$want" -gt 0 ] && echo " → $(human "$want")" )"
  curl -L --fail --retry 3 --retry-delay 3 -C - -o "$dest" "$url" \
    -w "     完成 %{size_download} B @ %{speed_download} B/s\n" || {
      echo "  ❌ 下載失敗: $url" >&2; return 1; }

  if [ "$want" -gt 0 ]; then
    have=$(stat -f%z "$dest" 2>/dev/null || echo 0)
    if [ "$have" -ne "$want" ]; then
      echo "  ⚠️  大小不符: 預期 $want，實際 $have（可重跑續傳）" >&2
      return 1
    fi
  fi
}

fail=0
if [[ " ${DL[*]} " == *" ace "* ]]; then
  echo "==> ACE-Step 1.5（分檔版）"
  for f in "${ACE_FILES[@]}"; do download_one "$f" || fail=1; done
fi
if [[ " ${DL[*]} " == *" sd "* ]]; then
  echo "==> Stable Diffusion 1.5 (fp16)"
  for f in "${SD_FILES[@]}"; do download_one "$f" || fail=1; done
fi
if [[ " ${DL[*]} " == *" workflow "* ]]; then
  echo "==> ComfyUI 工作流範本"
  for f in "${WORKFLOW_FILES[@]}"; do download_one "$f" || fail=1; done
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "✅ 下載完成。模型位置: $COMFY/models"
  du -sh "$COMFY/models" 2>/dev/null
else
  echo "⚠️  部分下載未完成，重跑此腳本即可續傳。"
  exit 1
fi
