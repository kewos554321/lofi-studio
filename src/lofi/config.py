"""集中「會隨環境／模型而變」的設定（single source of truth）。

換模型、改 ComfyUI 位址只改這裡，或用環境變數覆寫（不必動程式）：

  LOFI_COMFY_URL       ComfyUI API 位址（預設 http://127.0.0.1:8188）
  LOFI_COMFY_ROOT      ComfyUI 安裝目錄（預設 ~/ComfyUI）
  LOFI_MUSIC_UNET      ACE-Step diffusion model 檔名
  LOFI_MUSIC_CLIP1/2   兩個 text encoder 檔名
  LOFI_MUSIC_VAE       audio VAE 檔名
  LOFI_IMAGE_CKPT      場景圖 checkpoint 檔名
  LOFI_UPSCALE_MODEL   放大模型檔名

查目前值：`lofi config`（或 --json / --sh / --licenses）。
"""
import json
import os
from pathlib import Path


def _env(name, default):
    v = os.environ.get(name)
    return v if v else default


def _mark(commercial):
    return {True: "✅", False: "⛔"}.get(commercial, "⚠️")


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

# IPAdapter 鎖角色用的輔助模型（`visual --ref`；PLUS 系列 preset 對應這兩支）。
# 由 ComfyUI 的 IPAdapterUnifiedLoader 依 preset 自己挑檔，這裡只用來做商用授權稽核與記錄。
IPADAPTER_MODELS = ("ip-adapter-plus_sdxl_vit-h.safetensors",
                    "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors")

# ── 資源保護 ─────────────────────────────────────────────────────────
# 生成前要求內接碟至少這麼多可用 GB（見 AGENTS.md 資源限制）
MIN_FREE_GB = float(_env("LOFI_MIN_FREE_GB", "20"))


# ── 模型授權登錄（商用合規）────────────────────────────────────────────
# 換模型前先查這裡；commercial: True=可商用 / False=禁止商用 / None=未知。
# source 為官方頁面（授權以該頁最新標示為準）。詳見 docs/LICENSING.md。
MODEL_LICENSES = {
    # 音樂（ACE-Step 1.5）
    "acestep_v1.5_turbo.safetensors": {
        "license": "MIT", "commercial": True,
        "source": "https://github.com/ace-step/ACE-Step / https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files"},
    "qwen_0.6b_ace15.safetensors": {
        "license": "Apache-2.0", "commercial": True,
        "source": "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files"},
    "qwen_1.7b_ace15.safetensors": {
        "license": "Apache-2.0", "commercial": True,
        "source": "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files"},
    "ace_1.5_vae.safetensors": {
        "license": "MIT", "commercial": True,
        "source": "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files"},
    # 圖片（SDXL 系）
    "DreamShaperXL-v1.0-Alpha2.safetensors": {
        "license": "CreativeML Open RAIL++-M", "commercial": True,
        "source": "https://civitai.com/models/112902"},
    "sd_xl_base_1.0.safetensors": {
        "license": "CreativeML Open RAIL++-M", "commercial": True,
        "source": "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0"},
    "v1-5-pruned-emaonly-fp16.safetensors": {
        "license": "CreativeML Open RAIL-M", "commercial": True,
        "source": "https://huggingface.co/runwayml/stable-diffusion-v1-5"},
    # Z-Image（實驗，但曾用於 ref01-05/sc06）
    "z-image-turbo-Q5_K_M.gguf": {
        "license": "Apache-2.0", "commercial": True,
        "source": "https://github.com/Tongyi-MAI/Z-Image"},
    "Qwen3-4B-Q8_0.gguf": {
        "license": "Apache-2.0", "commercial": True,
        "source": "https://huggingface.co/Qwen/Qwen3-4B"},
    "ae.safetensors": {
        "license": "Apache-2.0", "commercial": True,
        "source": "https://github.com/Tongyi-MAI/Z-Image（VAE）"},
    # 放大
    "RealESRGAN_x4plus_anime_6B.pth": {
        "license": "BSD-3-Clause", "commercial": True,
        "source": "https://github.com/xinntao/Real-ESRGAN"},
    # 實驗（IPAdapter / ControlNet；曾用於 ds01-03、sc01-05）
    "ip-adapter-plus_sdxl_vit-h.safetensors": {
        "license": "Apache-2.0", "commercial": True,
        "source": "https://huggingface.co/h94/IP-Adapter"},
    "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors": {
        "license": "MIT", "commercial": True,
        "source": "https://huggingface.co/laion/CLIP-ViT-H-14-laion2B-s32B-b79K"},
    "controlnet-union-sdxl-1.0.safetensors": {
        "license": "Apache-2.0", "commercial": True,
        "source": "https://huggingface.co/xinsir/controlnet-union-sdxl-1.0"},
}

UNKNOWN_LICENSE = {"license": "未知", "commercial": None, "source": ""}


MODEL_EXTS = (".safetensors", ".gguf", ".ckpt", ".pth", ".pt", ".sft", ".bin")


def _norm_model(name):
    """正規化模型檔名：去目錄、去已知副檔名、小寫（避免 v1.5 這種內含點被誤切）。"""
    b = str(name).rsplit("/", 1)[-1].lower()
    for e in MODEL_EXTS:
        if b.endswith(e):
            return b[: -len(e)]
    return b


def model_license(name):
    """回傳某模型檔名的授權資訊；未登錄回「未知」（commercial=None）。

    查表容錯：先比完整檔名，再比正規化檔名（舊記錄可能只存 `acestep_v1.5_turbo`）。
    """
    if not name:
        return dict(UNKNOWN_LICENSE)
    base = str(name).rsplit("/", 1)[-1]
    if base in MODEL_LICENSES:
        return dict(MODEL_LICENSES[base])
    norm = _norm_model(name)
    for key, info in MODEL_LICENSES.items():
        if _norm_model(key) == norm:
            return dict(info)
    return dict(UNKNOWN_LICENSE)


def check_commercial(names, *, allow_unknown=False):
    """檢查一組模型檔名可否商用。

    回傳 (blocked, findings)：findings=[(name, info)]，
    blocked=有明確禁止商用的模型，或（allow_unknown=False 時）有未知授權的模型。
    """
    blocked = False
    findings = []
    for n in names:
        if not n:
            continue
        info = model_license(n)
        findings.append((n, info))
        if info.get("commercial") is True:
            continue
        if info.get("commercial") is False:
            blocked = True
        elif not allow_unknown:
            blocked = True
    return blocked, findings



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
        "min_free_gb": MIN_FREE_GB,
    }


# shell 用的環境變數對照（給 `lofi config --sh`，讓 shell 產線共用同一組預設）
SH_VARS = {
    "LOFI_COMFY_URL": COMFY_URL,
    "LOFI_COMFY_ROOT": str(COMFY_ROOT),
    "LOFI_IMAGE_CKPT": IMAGE_CKPT,
    "LOFI_UPSCALE_MODEL": UPSCALE_MODEL,
}


def print_licenses():
    """列出授權登錄 + 目前產線用到的模型狀態（商用合規）。"""
    cur = {"music_unet": MUSIC_UNET, "music_clip1": MUSIC_CLIP1, "music_clip2": MUSIC_CLIP2,
           "music_vae": MUSIC_VAE, "image_ckpt": IMAGE_CKPT, "upscale_model": UPSCALE_MODEL}
    print("◆ 目前產線模型")
    for role, name in cur.items():
        info = model_license(name)
        print(f"  {_mark(info['commercial'])} {role:<12} {name}  [{info['license']}]")
    print("\n◆ 授權登錄（MODEL_LICENSES）")
    for name, info in sorted(MODEL_LICENSES.items()):
        print(f"  {_mark(info['commercial'])} {name}  [{info['license']}]  {info['source']}")
    print("\n※ 授權以官方頁面最新標示為準；換模型前務必查證。詳見 docs/LICENSING.md")


def main():
    import argparse

    ap = argparse.ArgumentParser(description="顯示集中設定（模型檔名 / ComfyUI 位址 / 授權）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--json", action="store_true", help="以 JSON 輸出")
    g.add_argument("--sh", action="store_true", help="輸出 shell 可 eval 的 KEY=value（給腳本用）")
    g.add_argument("--licenses", action="store_true", help="列出模型授權登錄（商用合規）")
    args = ap.parse_args()

    if args.sh:
        for k, v in SH_VARS.items():
            print(f'{k}="{v}"')
        return
    if args.json:
        print(json.dumps(as_dict(), ensure_ascii=False, indent=2))
        return
    if args.licenses:
        print_licenses()
        return
    for k, v in as_dict().items():
        print(f"  {k:<14} {v}")


if __name__ == "__main__":
    main()
