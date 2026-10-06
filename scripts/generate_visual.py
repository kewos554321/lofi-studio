#!/usr/bin/env python3
"""
generate_visual.py — 用 ComfyUI + SD1.5 生成 lofi 場景圖。

產出的圖片放到 assets/visuals/，再交給 make_visual_loop.sh 做無縫循環。

用法:
  python3 scripts/generate_visual.py                          # 預設場景，1 張
  python3 scripts/generate_visual.py --count 3                # 生 3 張（不同 seed）
  python3 scripts/generate_visual.py --prompt "your scene"    # 自訂 prompt
  python3 scripts/generate_visual.py --size 768x512
  python3 scripts/generate_visual.py --dry-run

前置：ComfyUI 需執行中，且 v1-5-pruned-emaonly-fp16.safetensors 已在 models/checkpoints。
"""
import argparse, json, random, shutil, sys, time, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIS_DIR = ROOT / "assets" / "visuals"
COMFY_OUT = Path.home() / "ComfyUI" / "output"
CKPT = "v1-5-pruned-emaonly-fp16.safetensors"

DEFAULT_POS = ("cozy lofi study room at night, rain on the window, warm desk lamp, "
               "coffee mug, books, potted plant, lo-fi anime illustration, soft warm lighting, "
               "detailed background, muted colors, nostalgic, masterpiece, best quality")
DEFAULT_NEG = ("lowres, blurry, bad anatomy, watermark, signature, text, error, "
               "people, face, hands, extra limbs, jpeg artifacts")


def build_prompt(pos, neg, w, h, seed, steps, cfg, prefix):
    return {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": pos, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["4", 1]}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "3": {"class_type": "KSampler", "inputs": {
                  "model": ["4", 0], "seed": seed, "steps": steps, "cfg": cfg,
                  "sampler_name": "dpmpp_2m", "scheduler": "karras",
                  "positive": ["6", 0], "negative": ["7", 0],
                  "latent_image": ["5", 0], "denoise": 1.0}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": prefix}},
    }


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
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        w, h = (int(x) for x in args.size.lower().split("x"))
    except ValueError:
        sys.exit("--size 格式應為 寬x高，例如 768x512")

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
            args.prompt, args.negative, w, h, seed, args.steps, args.cfg, args.prefix),
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
