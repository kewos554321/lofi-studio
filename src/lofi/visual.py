#!/usr/bin/env python3
"""
generate_visual.py — 用 ComfyUI 生 lofi 場景圖（SD1.5 或 SDXL 動漫模型皆可）。

產出的圖片放到 assets/visuals/，再交給 make_visual_loop.sh 做無縫循環。

用法:
  python3 scripts/generate_visual.py                          # 預設場景，1 張
  python3 scripts/generate_visual.py --count 3                # 生 3 張（不同 seed）
  python3 scripts/generate_visual.py --prompt "your scene"    # 自訂 prompt
  python3 scripts/generate_visual.py --size 768x512
  python3 scripts/generate_visual.py --upscale RealESRGAN_x4plus_anime_6B.pth  # AI 放大（768->1536）
  # SDXL 動漫模型（Illustrious / NoobAI）：原生高解析度 + clip skip 2
  python3 scripts/generate_visual.py --ckpt Illustrious-XL-v2.0.safetensors \
      --size 1216x832 --clip-skip 2 --steps 28 --cfg 5
  python3 scripts/generate_visual.py --dry-run

前置：ComfyUI 需執行中，且對應 checkpoint 已在 models/checkpoints。
SD1.5 用 v1-5-pruned-emaonly-fp16.safetensors；SDXL 動漫用 Illustrious-XL-v2.0.safetensors。
放大模型放在 ~/ComfyUI/models/upscale_models/（見 download_p2_models.sh）。
"""
import argparse, json, random, shutil, sys, time, urllib.request, urllib.error
from pathlib import Path

from lofi.paths import ROOT

VIS_DIR = ROOT / "assets" / "visuals"
COMFY_OUT = Path.home() / "ComfyUI" / "output"
CKPT = "v1-5-pruned-emaonly-fp16.safetensors"

DEFAULT_POS = ("1girl, solo, anime girl sitting at a cozy desk by the window at night, "
               "warm desk lamp, coffee mug, books, potted plant, long hair, looking at viewer, "
               "lo-fi anime illustration, soft warm lighting, detailed background, "
               "muted colors, nostalgic, masterpiece, best quality")
DEFAULT_NEG = ("lowres, blurry, bad anatomy, bad hands, extra digits, fewer digits, bad proportions, "
               "2girls, multiple girls, extra limbs, watermark, signature, text, error, jpeg artifacts")


def build_prompt(pos, neg, w, h, seed, steps, cfg, prefix, ckpt=CKPT,
                 upscale_model="", up_out=None, clip_skip=0,
                 sampler="dpmpp_2m", scheduler="karras"):
    g = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
    }
    # clip skip：SDXL 動漫模型（Illustrious/NoobAI）建議 2，晚於 CLIPTextEncode 之前切層
    clip_src = ["4", 1]
    if clip_skip and clip_skip > 1:
        g["13"] = {"class_type": "CLIPSetLastLayer",
                   "inputs": {"clip": ["4", 1], "stop_at_clip_layer": -clip_skip}}
        clip_src = ["13", 0]
    g["6"] = {"class_type": "CLIPTextEncode", "inputs": {"text": pos, "clip": clip_src}}
    g["7"] = {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": clip_src}}
    g["3"] = {"class_type": "KSampler", "inputs": {
                  "model": ["4", 0], "seed": seed, "steps": steps, "cfg": cfg,
                  "sampler_name": sampler, "scheduler": scheduler,
                  "positive": ["6", 0], "negative": ["7", 0],
                  "latent_image": ["5", 0], "denoise": 1.0}}
    src = ["8", 0]
    if upscale_model:
        ow, oh = up_out or (w * 2, h * 2)
        g["10"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": upscale_model}}
        g["11"] = {"class_type": "ImageUpscaleWithModel",
                   "inputs": {"upscale_model": ["10", 0], "image": ["8", 0]}}
        g["12"] = {"class_type": "ImageScale",
                   "inputs": {"image": ["11", 0], "width": ow, "height": oh,
                              "upscale_method": "lanczos", "crop": "disabled"}}
        src = ["12", 0]
    g["9"] = {"class_type": "SaveImage", "inputs": {"images": src, "filename_prefix": prefix}}
    return g


def api(url, path, payload=None, timeout=30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8188")
    ap.add_argument("--prompt", default=DEFAULT_POS)
    ap.add_argument("--negative", default=DEFAULT_NEG)
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--size", default="768x512", help="寬x高（SD1.5 建議 512~768）")
    ap.add_argument("--steps", type=int, default=25)
    ap.add_argument("--cfg", type=float, default=6.5)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--prefix", default="lofi_scene")
    ap.add_argument("--ckpt", default=CKPT, help="checkpoint 檔名（放在 ComfyUI/models/checkpoints）")
    ap.add_argument("--upscale", default="",
                    help="放大模型檔名（放 ComfyUI/models/upscale_models/），空=不放大，例：RealESRGAN_x4plus_anime_6B.pth")
    ap.add_argument("--upscale-out", default="", help="放大後輸出尺寸 寬x高（預設生成尺寸×2）")
    ap.add_argument("--clip-skip", type=int, default=0,
                    help="CLIP 切層（SDXL 動漫模型建議 2；0=用 checkpoint 預設）")
    ap.add_argument("--sampler", default="dpmpp_2m", help="取樣器（SDXL 動漫常用 euler_ancestral / dpmpp_2m）")
    ap.add_argument("--scheduler", default="karras", help="排程器（karras / normal / simple）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        w, h = (int(x) for x in args.size.lower().split("x"))
    except ValueError:
        sys.exit("--size 格式應為 寬x高，例如 768x512")

    up_out = None
    if args.upscale_out:
        try:
            up_out = tuple(int(x) for x in args.upscale_out.lower().split("x"))
        except ValueError:
            sys.exit("--upscale-out 格式應為 寬x高，例如 1536x1024")

    if not args.dry_run:
        try:
            api(args.url, "/system_stats", timeout=5)
        except Exception as e:
            sys.exit(f"無法連線 ComfyUI ({args.url})：{e}")

    VIS_DIR.mkdir(parents=True, exist_ok=True)
    for i in range(1, args.count + 1):
        seed = args.seed if args.seed is not None else random.randint(1, 2**31 - 1)
        print(f"[{i}/{args.count}] {w}x{h} seed={seed}")
        if args.dry_run:
            print("   ", args.prompt[:100], "…")
            continue
        r = api(args.url, "/prompt", {"prompt": build_prompt(
            args.prompt, args.negative, w, h, seed, args.steps, args.cfg, args.prefix, args.ckpt,
            upscale_model=args.upscale, up_out=up_out,
            clip_skip=args.clip_skip, sampler=args.sampler, scheduler=args.scheduler),
            "client_id": "lofi-visual"})
        pid = r["prompt_id"]
        t0 = time.time()
        while True:
            try:
                hist = api(args.url, f"/history/{pid}", timeout=15)
            except Exception:
                hist = {}
            if hist and pid in hist:
                break
            time.sleep(3)
        res = hist[pid]
        imgs = res.get("outputs", {}).get("9", {}).get("images", [])
        for im in imgs:
            src = COMFY_OUT / im.get("subfolder", "") / im["filename"]
            dst = VIS_DIR / im["filename"]
            if src.exists():
                shutil.copy2(src, dst)
                print(f"    ✅ {time.time()-t0:.0f}s -> {dst}")
    if args.dry_run:
        print("（dry-run，未實際生成）")
    else:
        print(f"\n完成，圖片在 {VIS_DIR}")


if __name__ == "__main__":
    main()
