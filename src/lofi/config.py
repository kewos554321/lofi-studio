"""集中「會隨環境／模型而變」的設定（single source of truth）。

換模型、改 ComfyUI 位址只改這裡，或用環境變數覆寫（不必動程式）：

  LOFI_COMFY_URL       ComfyUI API 位址（預設 http://127.0.0.1:8188）
  LOFI_COMFY_ROOT      ComfyUI 安裝目錄（預設 ~/ComfyUI）
  LOFI_MUSIC_UNET      ACE-Step diffusion model 檔名
  LOFI_MUSIC_CLIP1/2   兩個 text encoder 檔名
  LOFI_MUSIC_VAE       audio VAE 檔名
  LOFI_IMAGE_CKPT      場景圖 checkpoint 檔名
  LOFI_UPSCALE_MODEL   放大模型檔名
  LOFI_RELEASE_ARTIST  音樂 artist 名義（預設 Moni on the Journey）
  LOFI_RELEASE_SONGWRITER   作詞作曲真名（DistroKid 原創曲必填）
  LOFI_RELEASE_AI_CREDITS   AI 揭露範圍（預設 all_audio；DistroKid AI Credits）
  LOFI_RELEASE_PRICE_USD    Gumroad 定價 USD（0＝名稱你的價錢）
  LOFI_QC_BLOCKING_FLAGS    品檢要擋掉的 flag（母帶修不掉的才擋）

查目前值：`lofi config`（或 --json / --sh / --licenses）。
"""
import json
import os
from pathlib import Path


def _env(name, default):
    v = os.environ.get(name)
    return v if v else default


def _bool_env(name, default):
    """布林環境變數：1/true/yes/on（不分大小寫）＝True；未設＝default。"""
    v = os.environ.get(name)
    if v is None or not v.strip():
        return default
    return v.strip().lower() in ("1", "true", "yes", "on", "y", "t")


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


# ── 發行（release / 上架包）─────────────────────────────────────────────
# 音樂 artist 名義：**與 YouTube 頻道同名**（2026-10 頻道由 `Drifted LoFi` 改名，
# 頻道／串流／商店都用這一個字串，避免同一個作品被當成兩個品牌），見 docs/DISTRIBUTION.md §6-2。
# 定案依據（2026-10 實查）：單字名（Sumi/Hoshi/Ember/Nori/Kage/Marlow/Bramble…）在
# Apple Music 與 MusicBrainz **全部撞名**；`Moni on the Journey` 兩邊零撞名，
# 且把主角（黑貓的旅程）寫進品牌。可用 handle：`monionthejourney.bandcamp.com`、`@monionthejourney`、`monionthejourney.com`。
# （2026-10 由舊名 `Moni's Journey` 改名：新名沒有撇號，原本「直／彎引號會被當成不同藝人」的坑自然消失。）
RELEASE_ARTIST = _env("LOFI_RELEASE_ARTIST", "Moni on the Journey")
RELEASE_LABEL = _env("LOFI_RELEASE_LABEL", "")
RELEASE_GENRE = _env("LOFI_RELEASE_GENRE", "Lo-fi")
# 母帶響度目標：Spotify 建議 -14 LUFS；true peak 留 1 dB 餘裕。
MASTER_LUFS = float(_env("LOFI_MASTER_LUFS", "-14"))
MASTER_TP = float(_env("LOFI_MASTER_TP", "-1"))

# 上架表單的共用欄位（release 產出 LISTING.md／CHECKLIST.md 用；DistroKid 與 Gumroad 都要）。
# DistroKid：**原創曲必填 songwriter 真名**（填藝名會被退）。這裡放**護照上的原文**
# （姓在前、全大寫、含逗號），以利平台／Stripe 的 KYC 比對；要改成名在前的顯示寫法
# （`Wei-Chieh Wang`）就改這一個值，或用 LOFI_RELEASE_SONGWRITER／`--songwriter` 覆寫。
# 留空時 `LISTING.md`／`CHECKLIST.md` 會標成「⚠️ 未填」提醒你上架前補。
RELEASE_SONGWRITER = _env("LOFI_RELEASE_SONGWRITER", "WANG, WEI-CHIEH")
# AI 揭露範圍（DistroKid 的 AI Credits；選項見 release.AI_CREDIT_SCOPES）。
# 本產線的音樂是 ACE-Step **全生成** → 預設 all_audio；封面（SDXL）另記於 credits.txt。
RELEASE_AI_CREDITS = _env("LOFI_RELEASE_AI_CREDITS", "all_audio")
# artist 名是否為「AI 角色」（DistroKid 選 all_audio 後會追問，2026 起強制）。
RELEASE_AI_PERSONA = _bool_env("LOFI_RELEASE_AI_PERSONA", True)
# explicit 歌詞標記；本產線為純器樂、無人聲 → 預設 False。
RELEASE_EXPLICIT = _bool_env("LOFI_RELEASE_EXPLICIT", False)
# Gumroad 定價（USD）；0＝「名稱你的價錢（pay-what-you-want）」，先累積聽眾再看要不要收費。
RELEASE_PRICE_USD = float(_env("LOFI_RELEASE_PRICE_USD", "0"))
# 頻道的公開網址：商品描述會放一行導流（免費長版混音），也是廣告與文件引用的唯一來源。
CHANNEL_URL = _env("LOFI_CHANNEL_URL", "https://www.youtube.com/@monionthejourney")


# ── 自動品檢（qc）政策 ─────────────────────────────────────────────────
# 哪些 flag 是「**母帶步驟修不掉**」的，因此應該擋掉。
# 依據（2026-10 全庫 126 首實測）：只出現 3 種 flag——
#   hot_true_peak 110 次 → release 的 loudnorm(-1 dBTP) 修得掉（實測產出後 12/12 pass）
#   loudness_out_of_range  5 次 → loudnorm(-14 LUFS) 修得掉
#   wide_dynamics          3 次 → 修不掉（響度會被拉平，但曲子本身忽大忽小聽得出來）
# 所以**不要用「score 100」當門檻**（那只剩 11/126 = 9%），改用這份黑名單（可用 123/126）。
# 空字串代表「不過濾」；也用 LOFI_QC_BLOCKING_FLAGS 覆寫。
QC_BLOCKING_FLAGS = tuple(
    f.strip() for f in
    _env("LOFI_QC_BLOCKING_FLAGS", "wide_dynamics,truncated,silent,clipped").split(",")
    if f.strip())


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
        "release_artist": RELEASE_ARTIST or "（未設定；見 LOFI_RELEASE_ARTIST）",
        "release_label": RELEASE_LABEL or "（無）",
        "release_genre": RELEASE_GENRE,
        "release_songwriter": RELEASE_SONGWRITER or "（未填；DistroKid 原創曲必填真名）",
        "release_ai_credits": RELEASE_AI_CREDITS,
        "release_price_usd": RELEASE_PRICE_USD,
        "channel_url": CHANNEL_URL or "（未設定；見 LOFI_CHANNEL_URL）",
        "master_target": f"{MASTER_LUFS:g} LUFS / {MASTER_TP:g} dBTP",
        "qc_blocking_flags": ", ".join(QC_BLOCKING_FLAGS) or "（不過濾）",
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
