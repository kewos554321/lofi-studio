#!/usr/bin/env python3
"""臨時：用 GGUF 量化版跑 Z-Image Turbo（測速度）。

跟 _tmp_zimage_test.py 差別：UNETLoader -> UnetLoaderGGUF、CLIPLoader -> CLIPLoaderGGUF。
"""
import argparse
import random
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lofi.visual import (VIS_DIR, COMFY_OUT, api,        # noqa: E402
                         update_manifest, print_run_header, model_tag,
                         output_filename, next_seq, next_run_id, VIS_KINDS)

GGUF_UNET = "z-image-turbo-Q5_K_M.gguf"
GGUF_CLIP = "Qwen3-4B-Q8_0.gguf"
VAE = "ae.safetensors"


def build(pos, w, h, seed, steps, cfg, prefix, shift=3.0,
          sampler="res_multistep", scheduler="simple",
          unet=GGUF_UNET, clip=GGUF_CLIP, vae=VAE,
          init_image="", mask_image="", grow=8, grow_mask_by=6, denoise=1.0):
    g = {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": unet}},
        "2": {"class_type": "CLIPLoaderGGUF",
              "inputs": {"clip_name": clip, "type": "lumina2"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "4": {"class_type": "ModelSamplingAuraFlow",
              "inputs": {"model": ["1", 0], "shift": shift}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": pos}},
        "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": prefix}},
    }
    # 起點：給了基準圖+遮罩就做局部重繪（inpaint），否則從空 latent 生圖
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
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8188")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--size", default="1024x1024")
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--cfg", type=float, default=1.0)
    ap.add_argument("--shift", type=float, default=3.0)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--prefix", default="zimage_gguf",
                    help="ComfyUI 自己的暫存檔名（會被 rename 複製走；成品檔名看 --slug）")
    ap.add_argument("--slug", default="",
                    help="檔名標籤（小寫英數），例 eyes-closed -> ref05_02_eyes-closed.png")
    ap.add_argument("--kind", default="sc", choices=VIS_KINDS,
                    help="用途代號（未給 --run-id 時用來編號）：ref=定裝參考 ds=訓練集 sc=場景測試")
    ap.add_argument("--run-id", default="",
                    help="批次代號＝資料夾名，建議 <kind><NN>（例 ref05）；未給則自動 <kind><NN>")
    ap.add_argument("--init-image", default="", help="局部重繪的基準圖（需在 ComfyUI/input/）")
    ap.add_argument("--mask", default="", help="遮罩圖（黑底白區=要重繪處，需在 ComfyUI/input/）")
    ap.add_argument("--grow", type=int, default=8, help="遮罩擴張像素")
    ap.add_argument("--grow-mask-by", type=int, default=6)
    ap.add_argument("--denoise", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    w, h = (int(x) for x in args.size.lower().split("x"))
    if not args.dry_run:
        try:
            api(args.url, "/system_stats", timeout=5)
        except Exception as e:
            sys.exit(f"無法連線 ComfyUI：{e}")

    # 未給 run-id 就自動編號（ref05 之後給 ref06）
    run_id = args.run_id or next_run_id(args.kind)
    out_dir = VIS_DIR / run_id
    seed = args.seed if args.seed is not None else random.randint(1, 2**31 - 1)
    prefix = args.prefix
    # config＝該批固定、所有圖共用的設定；prompt/seed 逐檔寫進 manifest 的 images
    config = {
        "unet": GGUF_UNET,
        "clip": GGUF_CLIP,
        "clip_type": "lumina2",
        "vae": VAE,
        "size": f"{w}x{h}",
        "steps": args.steps,
        "cfg": args.cfg,
        "shift": args.shift,
        "sampler": "res_multistep",
        "scheduler": "simple",
        "denoise": args.denoise,
        "init_image": args.init_image,
    }
    if args.mask:                          # 只有局部重繪才需要遮罩相關欄位
        config["mask"] = args.mask
        config["grow"] = args.grow
    print_run_header("Z-Image Turbo（GGUF）", run_id, out_dir, config)
    if args.dry_run:
        print(f"（dry-run，未實際生成）prompt：{args.prompt[:80]}…")
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    seq = next_seq(out_dir, run_id)
    print(f"[1/1] {model_tag(config)} {w}x{h} seed={seed}")
    r = api(args.url, "/prompt", {"prompt": build(
        args.prompt, w, h, seed, args.steps, args.cfg, args.prefix, shift=args.shift,
        init_image=args.init_image, mask_image=args.mask,
        grow=args.grow, grow_mask_by=args.grow_mask_by, denoise=args.denoise),
        "client_id": "lofi-zimage-gguf"})
    pid = r["prompt_id"]
    t0 = time.time()
    while True:
        try:
            hist = api(args.url, f"/history/{pid}", timeout=15)
        except Exception:
            hist = {}
        if hist and pid in hist:
            break
        time.sleep(2)
    res = hist[pid]
    if res.get("status", {}).get("status_str") == "error":
        print("   ❌ ComfyUI 回報錯誤：")
        for m in res.get("status", {}).get("messages", []):
            if m[0] == "execution_error":
                print("     ", m[1].get("node_type"), ":", m[1].get("exception_message"))
        return
    for im in res.get("outputs", {}).get("10", {}).get("images", []):
        src = COMFY_OUT / im.get("subfolder", "") / im["filename"]
        if not src.exists():
            continue
        # 複製時改成標準檔名：<run-id>_<NN>[_<slug>].<ext>（ComfyUI 的 _00001_ 不帶走）
        dst_name = output_filename(run_id, seq, args.slug, Path(im["filename"]).suffix)
        seq += 1
        shutil.copy2(src, out_dir / dst_name)
        update_manifest(out_dir, run_id=run_id, tool="z-image-turbo-gguf",
                        caller=Path(sys.argv[0]).name, config=config,
                        images=[{"file": dst_name, "seed": seed, "prompt": args.prompt}])
        print(f"    ✅ {time.time()-t0:.0f}s -> {dst_name}")


if __name__ == "__main__":
    main()
