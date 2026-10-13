"""集中「會隨環境／模型而變」的設定（single source of truth）。

換模型、改 ComfyUI 位址只改這裡，或用環境變數覆寫（不必動程式）：

  LOFI_COMFY_URL       ComfyUI API 位址（預設 http://127.0.0.1:8188）
  LOFI_COMFY_ROOT      ComfyUI 安裝目錄（預設 ~/ComfyUI）
  LOFI_MUSIC_UNET      ACE-Step diffusion model 檔名
  LOFI_MUSIC_CLIP1/2   兩個 text encoder 檔名
  LOFI_MUSIC_VAE       audio VAE 檔名
  LOFI_IMAGE_CKPT      場景圖 checkpoint 檔名
  LOFI_UPSCALE_MODEL   放大模型檔名

查目前值：`lofi config`（或 --json / --sh）。
"""
import json
import os
from pathlib import Path


def _env(name, default):
    v = os.environ.get(name)
    return v if v else default


# ── ComfyUI ────────────────────────────────────────────────────────────
COMFY_URL = _env("LOFI_COMFY_URL", "http://127.0.0.1:8188")
COMFY_ROOT = Path(_env("LOFI_COMFY_ROOT", str(Path.home() / "ComfyUI"))).expanduser()
COMFY_OUTPUT = COMFY_ROOT / "output"
COMFY_AUDIO_OUT = COMFY_OUTPUT / "audio"

# ── 音樂：ACE-Step 1.5（檔名對應 scripts/download_p2_models.sh）────────
MUSIC_UNET = _env("LOFI_MUSIC_UNET", "acestep_v1.5_turbo.safetensors")
MUSIC_CLIP1 = _env("LOFI_MUSIC_CLIP1", "qwen_0.6b_ace15.safetensors")
MUSIC_CLIP2 = _env("LOFI_MUSIC_CLIP2", "qwen_1.7b_ace15.safetensors")
MUSIC_VAE = _env("LOFI_MUSIC_VAE", "ace_1.5_vae.safetensors")

# ── 圖片：場景圖 checkpoint／放大模型 ─────────────────────────────────
IMAGE_CKPT = _env("LOFI_IMAGE_CKPT", "DreamShaperXL-v1.0-Alpha2.safetensors")
UPSCALE_MODEL = _env("LOFI_UPSCALE_MODEL", "RealESRGAN_x4plus_anime_6B.pth")


def as_dict():
    """以「可讀名稱 -> 值」列出目前設定（給 `lofi config` 用）。"""
    return {
        "comfy_url": COMFY_URL,
        "comfy_root": str(COMFY_ROOT),
        "music_unet": MUSIC_UNET,
        "music_clip1": MUSIC_CLIP1,
        "music_clip2": MUSIC_CLIP2,
        "music_vae": MUSIC_VAE,
        "image_ckpt": IMAGE_CKPT,
        "upscale_model": UPSCALE_MODEL,
    }


# shell 用的環境變數對照（給 `lofi config --sh`，讓 shell 產線共用同一組預設）
SH_VARS = {
    "LOFI_COMFY_URL": COMFY_URL,
    "LOFI_COMFY_ROOT": str(COMFY_ROOT),
    "LOFI_IMAGE_CKPT": IMAGE_CKPT,
    "LOFI_UPSCALE_MODEL": UPSCALE_MODEL,
}


def main():
    import argparse

    ap = argparse.ArgumentParser(description="顯示集中設定（模型檔名 / ComfyUI 位址）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--json", action="store_true", help="以 JSON 輸出")
    g.add_argument("--sh", action="store_true", help="輸出 shell 可 eval 的 KEY=value（給腳本用）")
    args = ap.parse_args()

    if args.sh:
        for k, v in SH_VARS.items():
            print(f'{k}="{v}"')
        return
    if args.json:
        print(json.dumps(as_dict(), ensure_ascii=False, indent=2))
        return
    for k, v in as_dict().items():
        print(f"  {k:<14} {v}")


if __name__ == "__main__":
    main()
