#!/usr/bin/env python3
"""
batch_generate.py — 用 ComfyUI API 批次生成 ACE-Step 1.5 lofi 曲子。

從 prompts/prompt_library.csv 讀取氛圍設定（caption/bpm/key），
逐首送進 ComfyUI（預設 http://127.0.0.1:8188），生成後複製到 assets/tracks/。

用法:
  python3 scripts/batch_generate.py                 # 生成 CSV 全部（10 首）
  python3 scripts/batch_generate.py --limit 3       # 只生 3 首
  python3 scripts/batch_generate.py --csv prompts/generated/rainy_lofi.csv   # 用風格清單
  python3 scripts/batch_generate.py --duration 120  # 每首秒數（預設 120）
  python3 scripts/batch_generate.py --seed 12345    # 固定 seed（可重現）
  python3 scripts/batch_generate.py --run-id dusk01 # 批次識別碼（檔名唯一、可續傳）
  python3 scripts/batch_generate.py --csv X --offset 50 --limit 50 --run-id dusk01  # 續跑第 2 批
  python3 scripts/batch_generate.py --clean-raw     # 複製後刪除 ComfyUI 原始檔
  python3 scripts/batch_generate.py --dry-run       # 只印出將送出的設定

前置：ComfyUI 需以 scripts/launch_comfyui.sh 啟動，且 ACE-Step 1.5 模型已就位。
"""
import argparse, csv, json, random, re, shutil, sys, time, urllib.request, urllib.error
from pathlib import Path

from lofi import catalog as cat      # 音樂目錄（sidecar + JSONL）
from lofi import qc                  # 生成後自動品檢（可用 --no-qc 關閉）

ROOT = cat.ROOT
CSV_PATH = ROOT / "prompts" / "prompt_library.csv"
TRACKS_DIR = ROOT / "assets" / "tracks"
COMFY_OUT = Path.home() / "ComfyUI" / "output" / "audio"

UNET = "acestep_v1.5_turbo.safetensors"
CLIP1 = "qwen_0.6b_ace15.safetensors"
CLIP2 = "qwen_1.7b_ace15.safetensors"
VAE = "ace_1.5_vae.safetensors"


def build_prompt(tags, bpm, key, duration, seed, prefix,
                 steps=8, cfg_scale=2.0, temperature=0.85):
    return {
        "104": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "78":  {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["104", 0], "shift": 3.0}},
        "105": {"class_type": "DualCLIPLoader", "inputs": {
                    "clip_name1": CLIP1, "clip_name2": CLIP2, "type": "ace", "device": "default"}},
        "106": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "94":  {"class_type": "TextEncodeAceStepAudio1.5", "inputs": {
                    "clip": ["105", 0], "tags": tags, "lyrics": "", "seed": seed,
                    "bpm": int(bpm), "duration": float(duration), "timesignature": "4",
                    "language": "en", "keyscale": key, "generate_audio_codes": True,
                    "cfg_scale": cfg_scale, "temperature": temperature, "top_p": 0.9, "top_k": 0, "min_p": 0.0}},
        "47":  {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["94", 0]}},
        "98":  {"class_type": "EmptyAceStep1.5LatentAudio", "inputs": {"seconds": float(duration), "batch_size": 1}},
        "3":   {"class_type": "KSampler", "inputs": {
                    "model": ["78", 0], "seed": seed, "steps": steps, "cfg": 1,
                    "sampler_name": "euler", "scheduler": "simple",
                    "positive": ["94", 0], "negative": ["47", 0],
                    "latent_image": ["98", 0], "denoise": 1.0}},
        "18":  {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["3", 0], "vae": ["106", 0]}},
        "107": {"class_type": "SaveAudioMP3", "inputs": {
                    "audio": ["18", 0], "filename_prefix": prefix, "quality": "V0"}},
    }


def api(url, path, payload=None, timeout=30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def wait_done(url, pid, poll=5):
    t0 = time.time()
    while True:
        try:
            h = api(url, f"/history/{pid}", timeout=15)
        except Exception:
            h = {}
        if h and pid in h:
            return h[pid], time.time() - t0
        time.sleep(poll)


def record_track(dst, row, style, rid, seed, args, csv_path):
    """生成成功後，把可追溯的來源與參數寫進 sidecar + catalog/tracks.jsonl。"""
    record = {
        "source": "batch_generate",
        "source_csv": cat.relpath(csv_path),
        "row_id": str(rid),
        "run_id": getattr(args, "run_id", ""),
        "sha1": cat.sha1_file(dst),
        "created_at": cat.now_iso(),
        "style": style or row.get("style", ""),
        "style_version": cat.style_version(style),
        "mood": row.get("mood", ""),
        "instruments": row.get("instruments", ""),
        "key": row.get("key", ""),
        "bpm": int(row["bpm"]) if str(row.get("bpm", "")).strip().isdigit() else row.get("bpm"),
        "prompt": row.get("prompt", "").strip(),
        "negative": row.get("negative", ""),
        "gen": {
            "seed": seed,
            "steps": args.steps,
            "cfg_scale": args.cfg_scale,
            "temperature": args.temperature,
            "duration": args.duration,
            "model": UNET,
        },
        "status": "unreviewed",
        "tags": [],
        "review": {},
        "usage": [],
    }
    cat.upsert_track(dst, record, event="generated")
    return record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8188")
    ap.add_argument("--csv", default=str(CSV_PATH), help="曲目清單 CSV（預設 prompts/prompt_library.csv）")
    ap.add_argument("--limit", type=int, default=0, help="只生成前 N 首（0=全部）")
    ap.add_argument("--duration", type=float, default=120.0)
    ap.add_argument("--steps", type=int, default=8, help="取樣步數（8=快；10~12=較穩/較慢）")
    ap.add_argument("--cfg-scale", type=float, default=2.0)
    ap.add_argument("--temperature", type=float, default=0.85, help="越低越穩定（建議 0.8）")
    ap.add_argument("--seed", type=int, default=None, help="固定 seed（可重現）")
    ap.add_argument("--run-id", default="", dest="run_id",
                    help="批次識別碼（會放進檔名，確保唯一、可續傳）。未給則自動產生時間戳。"
                         "續傳時請帶同一個值。")
    ap.add_argument("--no-resume", action="store_true",
                    help="不要跳過已完成（同 run-id + source_csv + row_id）的列")
    ap.add_argument("--offset", type=int, default=0,
                    help="從清單第幾首開始（0-based；分批續傳用）")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="每首生成後等待秒數（散熱用，預設 0；建議 45~90）")
    ap.add_argument("--clean-raw", action="store_true",
                    help="複製到 assets/tracks 後刪除 ComfyUI 原始輸出（省硬碟）")
    ap.add_argument("--no-catalog", action="store_true",
                    help="不要寫入音樂目錄（sidecar / tracks.jsonl）")
    ap.add_argument("--no-qc", action="store_true",
                    help="生成後不要跑自動品檢（預設會跑 auto_qc）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        sys.exit(f"找不到 {csv_path}")

    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    if args.offset:
        rows = rows[args.offset:]
    if args.limit:
        rows = rows[:args.limit]
    total = len(rows)

    # ---- 批次識別碼：確保檔名唯一、可續傳 ----
    if not args.run_id:
        args.run_id = "r" + time.strftime("%Y%m%d-%H%M%S")
    args.run_id = re.sub(r"[^A-Za-z0-9_.-]", "-", args.run_id)

    # ---- 續傳：跳過同一 run 已完成的列 ----
    done = set()
    if not args.no_resume:
        src_rel = cat.relpath(csv_path)
        for rec in cat.load_catalog().values():
            if (rec.get("source_csv") == src_rel
                    and rec.get("run_id") == args.run_id
                    and rec.get("row_id") is not None):
                done.add(str(rec["row_id"]))
        if done:
            print(f"續傳：此 run（{args.run_id}）已完成 {len(done)} 列，將跳過")

    if not args.dry_run:
        try:
            stats = api(args.url, "/system_stats", timeout=5)
            print(f"ComfyUI {stats['system']['comfyui_version']} @ {args.url}")
        except Exception as e:
            sys.exit(f"無法連線 ComfyUI ({args.url})：{e}\n請先執行 scripts/launch_comfyui.sh")

    TRACKS_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    for i, row in enumerate(rows, 1):
        rid = row.get("id", f"{i:02d}")
        tags = row["prompt"].strip()
        bpm = row.get("bpm", "70")
        key = row.get("key", "D minor")
        seed = args.seed if args.seed is not None else random.randint(1, 2**31 - 1)
        # 風格 + 批次 id 當檔名前綴（rainy_lofi_01_r2026...），確保唯一、不覆蓋舊曲
        style = (row.get("style") or "").strip().replace(" ", "_")
        base = f"{style}_{rid}" if style else f"lofi_{rid}"
        prefix = f"audio/{base}_{args.run_id}"

        if str(rid) in done:
            print(f"[{i}/{total}] id={rid} 已完成，跳過")
            continue

        print(f"\n[{i}/{total}] id={rid} | {row.get('mood','')} | {bpm} BPM | {key} | seed={seed}")
        print(f"    {tags[:90]}…")

        if args.dry_run:
            continue

        try:
            r = api(args.url, "/prompt", {"prompt": build_prompt(
                tags, bpm, key, args.duration, seed, prefix,
                steps=args.steps, cfg_scale=args.cfg_scale, temperature=args.temperature),
                "client_id": "lofi-batch"})
        except urllib.error.HTTPError as e:
            print("    ❌ 提交失敗:", e.read().decode()[:500]); continue
        pid = r["prompt_id"]

        res, dt = wait_done(args.url, pid)
        status = res.get("status", {}).get("status_str")
        if status != "success":
            print(f"    ❌ 生成失敗: {status} {res.get('status',{}).get('messages',[])[-2:]}")
            continue

        audio = res.get("outputs", {}).get("107", {}).get("audio", [])
        for a in audio:
            src = Path.home() / "ComfyUI" / "output" / a.get("subfolder", "") / a["filename"]
            if not src.exists():
                print(f"    ⚠️  找不到輸出檔 {src}")
                continue
            dst = TRACKS_DIR / a["filename"]
            # 防覆蓋：若同名檔已存在（跨批次殘留），自動加序號
            if dst.exists():
                stem, ext = Path(a["filename"]).stem, Path(a["filename"]).suffix
                k = 2
                while dst.exists():
                    dst = TRACKS_DIR / f"{stem}-{k}{ext}"
                    k += 1
            shutil.copy2(src, dst)
            if args.clean_raw:
                src.unlink()
                print(f"    ✅ {dt:.0f}s -> {dst.name}（已刪除 ComfyUI 原始檔）")
            else:
                print(f"    ✅ {dt:.0f}s -> {dst.name}")
            ok += 1

            if not args.no_catalog:
                record_track(dst, row, style, rid, seed, args, csv_path)
                if not args.no_qc:
                    _, qcres, _ = qc.qc_file(dst)
                    print(f"       品檢: {qcres['verdict']} (score={qcres['score']})"
                          f"{' | ' + ', '.join(qcres['flags']) if qcres.get('flags') else ''}")
        if args.sleep and i < len(rows):
            print(f"    ⏸  降溫 {args.sleep:g}s…")
            time.sleep(args.sleep)

    if args.dry_run:
        print("\n（dry-run，未實際生成）")
    else:
        print(f"\n完成 {ok}/{len(rows)} 首，存放於 {TRACKS_DIR}")


if __name__ == "__main__":
    main()
