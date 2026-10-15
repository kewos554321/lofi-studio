#!/usr/bin/env python3
"""lofi zimage — 用 GGUF 量化版跑 Z-Image Turbo 生場景圖（主角 LoRA 的產線路徑）。

為什麼另開一條、不用 `lofi visual`：
  - **LoRA 架構不同**：主角 LoRA 是在 Z-Image 上訓練的；`lofi visual` 是 SDXL + IPAdapter，
    掛不上 z-image 的 LoRA（見 docs/LORA.md §6）。
  - **Mac 上要用 GGUF**：同一張圖 int8 要 920 秒、GGUF(Q5_K_M) 只要 221 秒（差 4 倍，見 AGENTS.md）。
  - **LoRA 只 patch DiT**：z-image 的 LoRA 訓練時是「Text Encoder: 0 modules」，所以用
    `LoraLoaderModelOnly`（沒有 strength_clip），掛在 `ModelSamplingAuraFlow` 之前——
    txt2img 與 inpaint 兩個分支都會吃到。

產出沿用同一套規矩：`assets/visuals/<run-id>/` ＋ 一個 run 一份 `manifest.json`
（`config` 記模型與 `lora`/`lora_weight`，`images` 記逐檔 prompt/seed）。命名見 docs/NAMING.md。

用法:
  lofi zimage --prompt "mnxy, sitting on a bench, seaside sunset" --kind sc --slug seaside   # 1472x832 (16:9)
  lofi zimage --count 3 --slug desk                       # 同 prompt、三顆隨機 seed（模型只載入一次）
  lofi zimage --lora "" --prompt "…"                      # 不掛主角 LoRA（純場景圖）
  lofi zimage --init-image base.png --mask area.png --denoise 0.85 --prompt "…"    # 局部重繪
  lofi zimage --upscale RealESRGAN_x4plus_anime_6B.pth --prompt "…"
  lofi zimage --from-image assets/visuals/cozy-1hr-01/cozy-1hr-01_01.png --slug fix-4k \
              --upscale-out 3840x2160                      # 只做 AI 放大（交付 ZIP 的高畫質圖來源）
  lofi zimage --dry-run                                   # 只印設定與授權檢查

prompt 的規矩：**一定要含觸發詞**，而且**建議帶畫風句**（`--style`，預設取自
`config.ZIMAGE_STYLE`）——少了畫風句，觸發詞只綁得住物種、花色會飄（docs/LORA.md §6 第 2 點）。
"""
import argparse
import os
import random
import shutil
import sys
import time
from pathlib import Path

from lofi import comfy
from lofi import media
from lofi.config import (COMFY_OUTPUT, MIN_FREE_GB, UPSCALE_MODEL, ZIMAGE_CLIP, ZIMAGE_LORA,
                         ZIMAGE_LORA_WEIGHT, ZIMAGE_STYLE, ZIMAGE_UNET, ZIMAGE_VAE,
                         check_commercial, model_license)
from lofi.lock import gpu_lock
from lofi.paths import ROOT, VISUALS_DIR
from lofi.visual import (VIS_KINDS, model_tag, next_run_id, next_seq,
                         output_filename, print_run_header, sanitize_run_id,
                         update_manifest)

VIS_DIR = VISUALS_DIR
COMFY_OUT = COMFY_OUTPUT
TOOL = "z-image-turbo-gguf"


def build(pos, w, h, seed, steps, cfg, prefix, shift=3.0,
          sampler="res_multistep", scheduler="simple",
          unet=ZIMAGE_UNET, clip=ZIMAGE_CLIP, vae=ZIMAGE_VAE,
          init_image="", mask_image="", grow=8, grow_mask_by=6, denoise=1.0,
          upscale_model="", up_out=None, lora="", lora_weight=1.0):
    """組 ComfyUI 節點圖。cfg=1.0 時 negative 不作用（用 ConditioningZeroOut 佔位）。"""
    g = {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": unet}},
        "2": {"class_type": "CLIPLoaderGGUF",
              "inputs": {"clip_name": clip, "type": "lumina2"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": pos}},
        "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
    }
    # LoRA 只 patch DiT：z-image 的 LoRA 訓練時是「Text Encoder: 0 modules」（docs/LORA.md §6），
    # 用 LoraLoaderModelOnly（無 strength_clip），不是 SDXL 那顆 LoraLoader。
    model_src = ["1", 0]
    if lora:
        g["19"] = {"class_type": "LoraLoaderModelOnly",
                   "inputs": {"model": ["1", 0], "lora_name": lora,
                              "strength_model": lora_weight}}
        model_src = ["19", 0]
    g["4"] = {"class_type": "ModelSamplingAuraFlow",
              "inputs": {"model": model_src, "shift": shift}}
    # 起點：給了基準圖＋遮罩就局部重繪（inpaint），否則從空 latent 生圖
    if init_image and mask_image:
        g["11"] = {"class_type": "LoadImage", "inputs": {"image": init_image}}
        g["12"] = {"class_type": "LoadImage", "inputs": {"image": mask_image}}
        g["13"] = {"class_type": "ImageToMask", "inputs": {"image": ["12", 0], "channel": "red"}}
        g["14"] = {"class_type": "GrowMask",
                   "inputs": {"mask": ["13", 0], "expand": grow, "tapered_corners": True}}
        g["15"] = {"class_type": "VAEEncodeForInpaint",
                   "inputs": {"pixels": ["11", 0], "vae": ["3", 0],
                              "mask": ["14", 0], "grow_mask_by": grow_mask_by}}
        latent = ["15", 0]
    else:
        g["7"] = {"class_type": "EmptySD3LatentImage",
                  "inputs": {"width": w, "height": h, "batch_size": 1}}
        latent = ["7", 0]
    g["8"] = {"class_type": "KSampler",
              "inputs": {"model": ["4", 0], "seed": seed, "steps": steps, "cfg": cfg,
                         "sampler_name": sampler, "scheduler": scheduler,
                         "positive": ["5", 0], "negative": ["6", 0],
                         "latent_image": latent, "denoise": denoise}}
    src = ["9", 0]
    if upscale_model:      # 選配：AI 放大（x4）後再 lanczos 縮到目標尺寸（與 lofi.visual 同一套節點）
        ow, oh = up_out or (w * 2, h * 2)
        g["16"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": upscale_model}}
        g["17"] = {"class_type": "ImageUpscaleWithModel",
                   "inputs": {"upscale_model": ["16", 0], "image": ["9", 0]}}
        g["18"] = {"class_type": "ImageScale",
                   "inputs": {"image": ["17", 0], "width": ow, "height": oh,
                              "upscale_method": "lanczos", "crop": "disabled"}}
        src = ["18", 0]
    g["10"] = {"class_type": "SaveImage", "inputs": {"images": src, "filename_prefix": prefix}}
    return g


def build_upscale_only(src_name, upscale_model, out_size, prefix):
    """只做 AI 放大（衍生圖）：LoadImage → ×4 → lanczos 縮到 out_size → SaveImage。

    用途：把已經生好的場景圖（例 1920×1080 的影片封面）放大成高畫質版，當交付 ZIP 的
    4K 橫式圖來源。與生圖分開，是因為影片／cinemagraph 走 1080p 最省資源（見 AGENTS.md）。
    """
    g = {
        "1": {"class_type": "LoadImage", "inputs": {"image": src_name}},
        "16": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": upscale_model}},
        "17": {"class_type": "ImageUpscaleWithModel",
               "inputs": {"upscale_model": ["16", 0], "image": ["1", 0]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"images": ["17", 0], "filename_prefix": prefix}},
    }
    if out_size:
        g["18"] = {"class_type": "ImageScale",
                   "inputs": {"image": ["17", 0], "width": out_size[0], "height": out_size[1],
                              "upscale_method": "lanczos", "crop": "disabled"}}
        g["10"]["inputs"]["images"] = ["18", 0]
    return g


def compose_prompt(prompt, style=""):
    """把畫風句接在 prompt 後面（缺標點就補句點）。

    z-image 的觸發詞要有畫風句才完整生效（docs/LORA.md §6）：少了它只綁得住物種、花色會飄。
    """
    prompt = (prompt or "").strip()
    style = (style or "").strip()
    if not style:
        return prompt
    if not prompt:
        return style
    if prompt.endswith((".", "!", "?", "。")):
        return f"{prompt} {style}"
    return f"{prompt}. {style}"


def main():
    ap = argparse.ArgumentParser(prog="lofi zimage",
                                 description="Z-Image Turbo（GGUF）生場景圖——主角 LoRA 的產線路徑")
    ap.add_argument("--url", default=comfy.COMFY_URL)
    ap.add_argument("--prompt", default="",
                    help="prompt（**要含觸發詞**，例 mnxy, …；畫風句由 --style 自動接上）")
    ap.add_argument("--from-image", default="", dest="from_image",
                    help="只做 AI 放大：把既有圖放大成高畫質衍生圖（不生成）。"
                         "例 --from-image assets/visuals/cozy-1hr-01/cozy-1hr-01_01.png "
                         "--slug fix-4k --upscale-out 3840x2160")
    ap.add_argument("--style", default=ZIMAGE_STYLE,
                    help="畫風句（接在 prompt 後面；空字串＝不加）。預設取自 config.ZIMAGE_STYLE，"
                         "少了它觸發詞只綁得住物種、花色會飄（docs/LORA.md §6）")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--size", default="1472x832",
                    help="寬x高（預設 1472x832＝16:9，與 1920x1080 影片同比例）")
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--cfg", type=float, default=1.0)
    ap.add_argument("--shift", type=float, default=3.0)
    ap.add_argument("--sampler", default="res_multistep")
    ap.add_argument("--scheduler", default="simple")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--prefix", default="zimage_gguf",
                    help="ComfyUI 自己的暫存檔名（會被 rename 複製走；成品檔名看 --slug）")
    ap.add_argument("--slug", default="",
                    help="檔名標籤（小寫英數），例 seaside -> cv01_01_seaside.png")
    ap.add_argument("--kind", default="sc", choices=VIS_KINDS,
                    help="用途代號（未給 --run-id 時用來編號）：ref=定裝參考 ds=訓練集 sc=場景測試")
    ap.add_argument("--run-id", default="",
                    help="批次代號＝資料夾名，建議 <kind><NN>（例 cv01）；未給則自動 <kind><NN>")
    ap.add_argument("--lora", default=ZIMAGE_LORA,
                    help="主角 LoRA（放 ComfyUI/models/loras/）。預設取自 config.ZIMAGE_LORA；"
                         "給空字串＝不掛（純場景圖）")
    ap.add_argument("--lora-weight", type=float, default=ZIMAGE_LORA_WEIGHT, dest="lora_weight",
                    help="LoRA 權重（預設 1.0；實測 1.0 就是在模型上）")
    ap.add_argument("--init-image", default="", help="局部重繪的基準圖（需在 ComfyUI/input/）")
    ap.add_argument("--mask", default="", help="遮罩圖（黑底白區=要重繪處，需在 ComfyUI/input/）")
    ap.add_argument("--grow", type=int, default=8, help="遮罩擴張像素")
    ap.add_argument("--grow-mask-by", type=int, default=6)
    ap.add_argument("--denoise", type=float, default=1.0)
    ap.add_argument("--upscale", default="", help="放大模型（ComfyUI/models/upscale_models/）")
    ap.add_argument("--upscale-out", default="", help="放大後尺寸 寬x高（預設 ×2）")
    ap.add_argument("--timeout", type=float, default=900.0, help="單張等待上限（秒）")
    ap.add_argument("--clean-raw", action="store_true",
                    help="複製＋記錄成功後刪掉 ComfyUI 原始輸出（省內接碟）")
    ap.add_argument("--allow-unknown-license", action="store_true",
                    help="授權未知時仍繼續（商用前務必自行確認）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.prompt and not args.from_image:
        sys.exit("要給 --prompt（生圖）或 --from-image（只做 AI 放大）")
    if args.from_image and not args.upscale:
        args.upscale = UPSCALE_MODEL        # 放大模式沒指定就用產線預設那顆

    try:
        w, h = (int(x) for x in args.size.lower().split("x"))
    except ValueError:
        sys.exit("--size 格式應為 寬x高，例如 832x1216")

    up_out = None
    if args.upscale_out:
        try:
            up_out = tuple(int(x) for x in args.upscale_out.lower().split("x"))
            assert len(up_out) == 2
        except (ValueError, AssertionError):
            sys.exit("--upscale-out 格式應為 寬x高，例如 1664x2432")
    if args.mask and not args.init_image:
        sys.exit("--mask 要搭配 --init-image（遮罩是「這張圖的哪一塊要重畫」）")

    prompt = compose_prompt(args.prompt, args.style)
    if not args.from_image and args.lora and args.style.strip().lower()[:20] not in prompt.lower():
        print("  ⚠️  prompt 沒帶畫風句：觸發詞只會綁住物種，花色可能飄（docs/LORA.md §6）")

    # 商用合規守門（未知授權預設 fail-closed）
    if os.environ.get("LOFI_IGNORE_LICENSE") != "1":
        if args.from_image:                 # 放大模式只用放大模型
            names = [args.upscale]
        else:
            names = [ZIMAGE_UNET, ZIMAGE_CLIP, ZIMAGE_VAE] + ([args.upscale] if args.upscale else [])
            if args.lora:
                names.append(args.lora)
        blocked, findings = check_commercial(names, allow_unknown=args.allow_unknown_license)
        for n, info in findings:
            mark = {True: "✅", False: "⛔"}.get(info["commercial"], "⚠️")
            print(f"  {mark} 授權 {n}：{info['license']}")
        if blocked and not args.dry_run:
            sys.exit("模型授權有疑慮（見上）→ 商用前請確認，或加 --allow-unknown-license 略過。"
                     "\n（登錄於 src/lofi/config.py；詳見 docs/LICENSING.md）")

    if not args.dry_run:
        comfy.require_online(args.url)
        media.require_free(VIS_DIR, MIN_FREE_GB, "內接碟 assets/visuals")

    # ── 只做 AI 放大：把既有圖放大成高畫質衍生圖，然後結束（不生成）──────────
    # 用途：交付 ZIP 的高畫質橫式圖來源（商店顯示 1080p、ZIP 給 4K）。
    # 為什麼不把影片封面直接生大：cinemagraph／影片走 1080p 最省 RAM（AGENTS.md 資源限制）。
    if args.from_image:
        src_img = Path(args.from_image)
        if not src_img.is_file():
            sys.exit(f"--from-image 找不到檔案：{src_img}")
        base = media.ffprobe_size(src_img)
        if up_out is None:
            if not base:
                sys.exit("讀不到來源圖尺寸，請用 --upscale-out 指定（例 3840x2160）")
            up_out = (base[0] * 2, base[1] * 2)

        run_id = sanitize_run_id(args.run_id) or next_run_id(args.kind)
        out_dir = VIS_DIR / run_id
        try:
            _src_rel = str(src_img.resolve().relative_to(ROOT))
        except ValueError:
            _src_rel = str(src_img)
        cfg = {
            "mode": "upscale-only（AI 放大既有圖）",
            "from_image": _src_rel,
            "source_size": f"{base[0]}x{base[1]}" if base else "",
            "upscale": args.upscale,
            "upscale_out": f"{up_out[0]}x{up_out[1]}",
            "license": model_license(args.upscale)["license"],
            "source": model_license(args.upscale)["source"],
        }
        print_run_header(f"AI 放大（{args.upscale}）", run_id, out_dir,
                         {**cfg, "unet": args.upscale}, count=1)
        if args.dry_run:
            return
        out_dir.mkdir(parents=True, exist_ok=True)
        # ComfyUI 的 LoadImage 只讀它 input/ 目錄下的檔
        in_dir = Path.home() / "ComfyUI" / "input"
        in_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_img, in_dir / src_img.name)
        seq = next_seq(out_dir, run_id)
        with gpu_lock("z-image-upscale"):
            print(f"[1/1] {model_tag(cfg)} {up_out[0]}x{up_out[1]} ← {src_img.name}")
            try:
                pid = comfy.submit(args.url, build_upscale_only(
                    src_img.name, args.upscale, up_out, args.prefix),
                    client_id="lofi-zimage")
            except Exception as e:
                sys.exit(f"提交失敗：{e}")
            t0 = time.time()
            try:
                res, _ = comfy.wait_done(args.url, pid, poll=3, timeout=args.timeout)
            except comfy.ComfyError as e:
                sys.exit(f"等待失敗：{e}")
            for im in res.get("outputs", {}).get("10", {}).get("images", []):
                raw = COMFY_OUT / im.get("subfolder", "") / im["filename"]
                if not raw.exists():
                    continue
                dst_name = output_filename(run_id, seq, args.slug, Path(im["filename"]).suffix)
                shutil.copy2(raw, out_dir / dst_name)
                update_manifest(out_dir, run_id=run_id, tool=TOOL, caller="lofi.zimage",
                                config=cfg,
                                images=[{"file": dst_name, "seed": "",
                                         "prompt": f"derived from {src_img.name}：{args.upscale} "
                                                   f"×4 後縮到 {up_out[0]}x{up_out[1]}"}])
                note = ""
                if args.clean_raw:
                    raw.unlink(missing_ok=True)
                    note = "（已刪除 ComfyUI 原始檔）"
                print(f"    ✅ {time.time() - t0:.0f}s -> {dst_name}{note}")
        return

    run_id = sanitize_run_id(args.run_id) or next_run_id(args.kind)
    out_dir = VIS_DIR / run_id
    config = {
        "unet": ZIMAGE_UNET,
        "clip": ZIMAGE_CLIP,
        "clip_type": "lumina2",
        "vae": ZIMAGE_VAE,
        "size": f"{w}x{h}",
        "steps": args.steps,
        "cfg": args.cfg,
        "shift": args.shift,
        "sampler": args.sampler,
        "scheduler": args.scheduler,
        "denoise": args.denoise,
        "init_image": args.init_image,
        "license": model_license(ZIMAGE_UNET)["license"],
        "source": model_license(ZIMAGE_UNET)["source"],
    }
    if args.mask:
        config.update({"mask": args.mask, "grow": args.grow, "grow_mask_by": args.grow_mask_by})
    if args.upscale:
        config.update({"upscale": args.upscale,
                       "upscale_out": f"{up_out[0]}x{up_out[1]}" if up_out else f"{w * 2}x{h * 2}"})
    if args.lora:      # 回頭看得出這批套了哪顆 LoRA、多重
        config.update({"lora": args.lora, "lora_weight": args.lora_weight,
                       "lora_license": model_license(args.lora)["license"]})
    print_run_header(f"Z-Image Turbo（GGUF）{'+ LoRA' if args.lora else ''}",
                     run_id, out_dir, config, count=args.count)
    if args.dry_run:
        print(f"（dry-run，未實際生成）prompt：{prompt[:100]}…")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    tag = model_tag(config)
    seq = next_seq(out_dir, run_id)
    # 互斥鎖：Z-Image（約 9GB）與 SDXL／ACE-Step 同時在 RAM 會爆（見 AGENTS.md 資源限制）
    with gpu_lock("z-image"):
        for i in range(1, args.count + 1):
            seed = args.seed if args.seed is not None else random.randint(1, 2**31 - 1)
            print(f"[{i}/{args.count}] {tag} {w}x{h} seed={seed}")
            try:
                pid = comfy.submit(args.url, build(
                    prompt, w, h, seed, args.steps, args.cfg, args.prefix, shift=args.shift,
                    sampler=args.sampler, scheduler=args.scheduler,
                    unet=ZIMAGE_UNET, clip=ZIMAGE_CLIP, vae=ZIMAGE_VAE,
                    init_image=args.init_image, mask_image=args.mask,
                    grow=args.grow, grow_mask_by=args.grow_mask_by, denoise=args.denoise,
                    upscale_model=args.upscale, up_out=up_out,
                    lora=args.lora, lora_weight=args.lora_weight),
                    client_id="lofi-zimage")
            except Exception as e:
                print(f"    ❌ 提交失敗：{e}")
                break
            t0 = time.time()
            try:
                res, _ = comfy.wait_done(args.url, pid, poll=3, timeout=args.timeout)
            except comfy.ComfyError as e:
                print(f"    ❌ 等待失敗，中止：{e}")
                break
            for im in res.get("outputs", {}).get("10", {}).get("images", []):
                src = COMFY_OUT / im.get("subfolder", "") / im["filename"]
                if not src.exists():
                    continue
                dst_name = output_filename(run_id, seq, args.slug, Path(im["filename"]).suffix)
                seq += 1
                shutil.copy2(src, out_dir / dst_name)
                update_manifest(out_dir, run_id=run_id, tool=TOOL,
                                caller="lofi.zimage", config=config,
                                images=[{"file": dst_name, "seed": seed, "prompt": prompt}])
                note = ""
                if args.clean_raw:      # 複製＋記錄都成功後才刪原檔（省 ComfyUI/output）
                    try:
                        src.unlink()
                        note = "（已刪除 ComfyUI 原始檔）"
                    except OSError as e:
                        note = f"（⚠️  刪除原始檔失敗：{e}）"
                print(f"    ✅ {time.time()-t0:.0f}s -> {dst_name}{note}")


if __name__ == "__main__":
    main()
