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
  python3 scripts/batch_generate.py --quality stable # 取樣品質預設 fast|balanced|stable（時間換穩定度）
  python3 scripts/batch_generate.py --seed 12345    # 固定 seed（可重現）
  python3 scripts/batch_generate.py --run-id dusk01 # 批次識別碼（檔名唯一、可續傳）
  python3 scripts/batch_generate.py --csv X --offset 50 --limit 50 --run-id dusk01  # 續跑第 2 批
  python3 scripts/batch_generate.py --clean-raw     # 複製後刪除 ComfyUI 原始檔
  python3 scripts/batch_generate.py --dry-run       # 只印出將送出的設定

前置：ComfyUI 需以 scripts/launch_comfyui.sh 啟動，且 ACE-Step 1.5 模型已就位。
"""
import argparse, csv, os, random, re, shutil, sys, time, urllib.error
from contextlib import nullcontext
from pathlib import Path

from lofi import catalog as cat      # 音樂目錄（sidecar + JSONL）
from lofi import comfy               # ComfyUI API 小客戶端
from lofi import media               # ffmpeg 工具 + 磁碟空間檢查
from lofi import qc                  # 生成後自動品檢（可用 --no-qc 關閉）
from lofi.config import (COMFY_AUDIO_OUT, COMFY_OUTPUT, COMFY_URL, MIN_FREE_GB, MUSIC_CLIP1,
                         MUSIC_CLIP2, MUSIC_UNET, MUSIC_VAE, check_commercial,
                         model_license)
from lofi.lock import gpu_lock
from lofi.paths import MASTERS_DIR, PROMPT_LIB, TRACKS_DIR

CSV_PATH = PROMPT_LIB
COMFY_OUT = COMFY_AUDIO_OUT

# 取樣品質預設：一次調 steps / temperature / cfg_scale（見 docs/RECIPES.md §1）。
# 以「時間換穩定度」：步數越高越穩越慢；temperature 越低越穩定、越高越有變化。
# 個別旗標（--steps / --temperature / --cfg-scale）有給就蓋過此預設。
QUALITY_PRESETS = {
    "fast":     {"steps": 8,  "temperature": 0.85, "cfg_scale": 2.0},
    "balanced": {"steps": 10, "temperature": 0.82, "cfg_scale": 2.0},
    "stable":   {"steps": 12, "temperature": 0.80, "cfg_scale": 2.0},
}


def sampling_params(quality, steps=None, temperature=None, cfg_scale=None):
    """合併 --quality 預設與個別覆寫，回傳有效的取樣參數（純函式，方便測試）。

    個別參數為 None（未指定）時採 --quality 的值；有給就以其為準。
    """
    preset = QUALITY_PRESETS[quality]
    return {
        "steps": preset["steps"] if steps is None else steps,
        "temperature": preset["temperature"] if temperature is None else temperature,
        "cfg_scale": preset["cfg_scale"] if cfg_scale is None else cfg_scale,
    }


def _unique(p):
    """若路徑已存在，自動加 -2 / -3 序號（防跨批次覆蓋）。"""
    p = Path(p)
    if not p.exists():
        return p
    k = 2
    while p.with_name(f"{p.stem}-{k}{p.suffix}").exists():
        k += 1
    return p.with_name(f"{p.stem}-{k}{p.suffix}")


def ensure_external_dir(path, label="外接碟"):
    """建立外接碟上的目錄；symlink 目標不在（碟未掛載）時給人話的錯誤。"""
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
    except (FileNotFoundError, OSError) as e:
        sys.exit(f"{label} 無法寫入：{path}（{e}）\n"
                 f"  外接碟可能沒掛載或掉線 → 先跑 ./scripts/disk_report.sh 確認。")
    return Path(path)


def transcode_to_mp3(src, dst):
    """無損母帶 → 工作用 mp3（V0）。內接只留 mp3，母帶放外接（見 docs/DISTRIBUTION.md §6-3）。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    rc, _, err = media.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-c:a", "libmp3lame", "-q:a", "0", "-write_id3v2", "1", "-id3v2_version", "3", str(dst)])
    if rc != 0:
        sys.exit(f"無損母帶轉 mp3 失敗：{src.name}\n{err.strip()[:300]}")
    return dst


def build_prompt(tags, bpm, key, duration, seed, prefix,
                 steps=8, cfg_scale=2.0, temperature=0.85, audio_format="mp3",
                 init_audio="", init_denoise=1.0, generate_audio_codes=True, negative=""):
    # 有負面 caption 時才換成自訂節點（ComfyUI custom_nodes/lofi_ace_negative）；
    # 沒有就維持內建節點，行為與以前完全相同。
    use_neg = bool(negative and negative.strip())
    p = {
        "104": {"class_type": "UNETLoader", "inputs": {"unet_name": MUSIC_UNET, "weight_dtype": "default"}},
        "78":  {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["104", 0], "shift": 3.0}},
        "105": {"class_type": "DualCLIPLoader", "inputs": {
                    "clip_name1": MUSIC_CLIP1, "clip_name2": MUSIC_CLIP2, "type": "ace", "device": "default"}},
        "106": {"class_type": "VAELoader", "inputs": {"vae_name": MUSIC_VAE}},
        "94":  {"class_type": ("LofiAceStepNegativeEncode" if use_neg
                               else "TextEncodeAceStepAudio1.5"), "inputs": {
                    "clip": ["105", 0], "tags": tags, "lyrics": "", "seed": seed,
                    "bpm": int(bpm), "duration": float(duration), "timesignature": "4",
                    "language": "en", "keyscale": key, "generate_audio_codes": bool(generate_audio_codes),
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
    if use_neg:
        p["94"]["inputs"]["negative"] = negative
    if init_audio:
        # audio2audio：拿參考音檔（需在 ComfyUI/input/）的 latent 當起點。
        # denoise 越低＝越保留參考（旋律輪廓清楚）；越高＝越自由（只留情緒與節奏骨架）。
        # 版權注意：參考受保護的旋律時「像不像」是程度問題，建議搭配移調與高 denoise；
        # 完整參數記在 sidecar 的 gen.init_audio / gen.init_denoise（衍生來源可追溯）。
        p["110"] = {"class_type": "LoadAudio", "inputs": {"audio": init_audio}}
        p["111"] = {"class_type": "VAEEncodeAudio",
                    "inputs": {"audio": ["110", 0], "vae": ["106", 0]}}
        p["3"]["inputs"]["latent_image"] = ["111", 0]
        p["3"]["inputs"]["denoise"] = float(init_denoise)
    if audio_format == "flac":
        # 無損母帶：改用 SaveAudio 存 FLAC（同一條管線，只換存檔節點）。
        # 用途：串流發行的母帶來源；見 docs/DISTRIBUTION.md §6-3。
        p["107"] = {"class_type": "SaveAudio", "inputs": {
                        "audio": ["18", 0], "filename_prefix": prefix}}
    return p


def record_track(dst, row, style, rid, seed, args, csv_path, master_file=None, negative=None):
    """生成成功後，把可追溯的來源與參數寫進 sidecar + catalog/tracks.jsonl。

    `dst` 是**工作檔**（assets/tracks 內的 mp3）；`master_file` 是無損母帶
    （output/masters 內的 FLAC，外接碟）；沒有就留空。
    """
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
        "negative": (row.get("negative", "") if negative is None else negative),
        "master_file": cat.relpath(master_file) if master_file else None,
        "gen": {
            "seed": seed,
            "steps": args.steps,
            "cfg_scale": args.cfg_scale,
            "temperature": args.temperature,
            "quality": getattr(args, "quality", "fast"),
            "generate_audio_codes": getattr(args, "generate_audio_codes", True),
            "duration": args.duration,
            "model": MUSIC_UNET,
            "format": getattr(args, "format", "mp3"),
            "model_license": model_license(MUSIC_UNET)["license"],
            "model_source": model_license(MUSIC_UNET)["source"],
            # 衍生來源（audio2audio）：留空代表純文字生成
            "init_audio": getattr(args, "init_audio", "") or None,
            "init_denoise": getattr(args, "init_denoise", None) if getattr(args, "init_audio", "") else None,
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
    ap.add_argument("--url", default=COMFY_URL)
    ap.add_argument("--csv", default=str(CSV_PATH), help="曲目清單 CSV（預設 prompts/prompt_library.csv）")
    ap.add_argument("--limit", type=int, default=0, help="只生成前 N 首（0=全部）")
    ap.add_argument("--duration", type=float, default=120.0)
    ap.add_argument("--quality", default="fast", choices=sorted(QUALITY_PRESETS),
                    help="取樣品質預設（fast=原始預設；balanced/stable=時間換穩定度）。"
                         "個別 --steps/--temperature/--cfg-scale 有給就蓋過此預設（見 docs/RECIPES.md §1）")
    ap.add_argument("--steps", type=int, default=None,
                    help="取樣步數（未給＝依 --quality；8=快、10~12=較穩/較慢）")
    ap.add_argument("--cfg-scale", type=float, default=None,
                    help="CFG 強度（未給＝依 --quality）")
    ap.add_argument("--temperature", type=float, default=None,
                    help="越低越穩定、越高越有變化（未給＝依 --quality）")
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
    ap.add_argument("--timeout", type=float, default=1800.0,
                    help="單首等待上限秒數（預設 1800；ComfyUI 卡死就跳過該首）")
    ap.add_argument("--clean-raw", action="store_true",
                    help="複製到 assets/tracks 後刪除 ComfyUI 原始輸出（省硬碟）")
    ap.add_argument("--format", default="mp3", choices=["mp3", "flac"],
                    help="輸出格式：mp3（預設，省空間）或 flac（無損母帶，供串流發行；見 docs/DISTRIBUTION.md）")
    ap.add_argument("--init-audio", default="", dest="init_audio",
                    help="audio2audio 參考音檔（檔名需在 ComfyUI/input/）：旋律會受它影響。"
                         "版權提醒：參考受保護的旋律時，成品仍可能有衍生風險，建議搭移調與高 denoise")
    ap.add_argument("--init-denoise", type=float, default=0.8, dest="init_denoise",
                    help="參考音影響度（反指標）：0.3~0.5 旋律明顯跟著走；0.75~0.9 只留情緒與節奏骨架")
    ap.add_argument("--generate-audio-codes", action=argparse.BooleanOptionalAction,
                    default=None, dest="generate_audio_codes",
                    help="生成 LLM 語意音訊碼（提升音質但較慢）。未給＝純文字生成時開、"
                         "audio2audio（--init-audio）時關（ComfyUI 節點建議給音訊參考時關閉）")
    ap.add_argument("--negative", default="",
                    help="負面 caption（送進 ACE-Step 的 LLM 語音碼階段做 CFG 對比）。"
                         "未給＝用 CSV/風格檔每列的 negative；需安裝 custom node（見 scripts/comfy_nodes/）")
    ap.add_argument("--no-negative", action="store_true",
                    help="完全關掉負面 caption（即使 CSV 有 negative 也不用）")
    ap.add_argument("--no-catalog", action="store_true",
                    help="不要寫入音樂目錄（sidecar / tracks.jsonl）")
    ap.add_argument("--no-qc", action="store_true",
                    help="生成後不要跑自動品檢（預設會跑 auto_qc）")
    ap.add_argument("--allow-unknown-license", action="store_true",
                    help="允許使用授權未登錄的模型（商用前請先確認，見 docs/LICENSING.md）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # 取樣參數：--quality 提供預設，個別旗標可覆寫（見 QUALITY_PRESETS）
    sampling = sampling_params(args.quality, steps=args.steps,
                               temperature=args.temperature, cfg_scale=args.cfg_scale)
    args.steps = sampling["steps"]
    args.temperature = sampling["temperature"]
    args.cfg_scale = sampling["cfg_scale"]

    # LLM 語意音訊碼：ACE-Step 會先跑一段 LLM 產生語意碼再擴散（提升音質、較慢）。
    # ComfyUI 節點的 tooltip 明講「給模型音訊參考時要關掉」→ audio2audio 預設自動關，
    # 需要時用 --generate-audio-codes/--no-generate-audio-codes 強制。
    if args.generate_audio_codes is None:
        args.generate_audio_codes = not bool(args.init_audio)

    # 商用合規守門：檢查模型授權（未知授權預設 fail-closed）
    if os.environ.get("LOFI_IGNORE_LICENSE") != "1":
        blocked, findings = check_commercial(
            [MUSIC_UNET, MUSIC_CLIP1, MUSIC_CLIP2, MUSIC_VAE],
            allow_unknown=args.allow_unknown_license)
        for n, info in findings:
            mark = {True: "✅", False: "⛔"}.get(info["commercial"], "⚠️")
            print(f"  {mark} 授權 {n}：{info['license']}")
        if blocked and not args.dry_run:
            sys.exit("模型授權有疑慮（見上）→ 商用前請確認，或加 --allow-unknown-license 略過。"
                     "\n（已知／登錄於 src/lofi/config.py；詳見 docs/LICENSING.md）")

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
        ver = comfy.require_online(args.url)
        print(f"ComfyUI {ver} @ {args.url}")
        media.require_free(TRACKS_DIR, MIN_FREE_GB, "內接碟 assets/tracks")
        if args.format == "flac":
            # 母帶寫外接碟：先確認有掛載（symlink 目標不在會給人話錯誤），再查空間
            ensure_external_dir(MASTERS_DIR, "無損母帶目錄 output/masters")
            media.require_free(MASTERS_DIR, 5.0, "外接碟 output/masters")

    TRACKS_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    # 互斥鎖：音樂與圖片生成同時跑會爆 16GB RAM（見 AGENTS.md）
    guard = gpu_lock("music") if not args.dry_run else nullcontext()
    with guard:
        for i, row in enumerate(rows, 1):
            rid = row.get("id", f"{i:02d}")
            tags = row["prompt"].strip()
            bpm = row.get("bpm", "70")
            key = row.get("key", "D minor")
            # 負面 caption：--no-negative 關掉；--negative 覆寫全部；否則用每列的 negative。
            if args.no_negative:
                neg = ""
            elif args.negative:
                neg = args.negative.strip()
            else:
                neg = (row.get("negative") or "").strip()
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
            print(f"    取樣: steps={args.steps} temperature={args.temperature}"
                  f" cfg_scale={args.cfg_scale} | 品質預設={args.quality}"
                  f" | LLM 語音碼={'開' if args.generate_audio_codes else '關'}")
            if neg:
                print(f"    負面: {neg[:90]}")
            if args.init_audio:
                print(f"    audio2audio 參考：{args.init_audio}（denoise {args.init_denoise}）")

            if args.dry_run:
                continue

            try:
                pid = comfy.submit(args.url, build_prompt(
                    tags, bpm, key, args.duration, seed, prefix,
                    steps=args.steps, cfg_scale=args.cfg_scale, temperature=args.temperature,
                    audio_format=args.format,
                    init_audio=args.init_audio, init_denoise=args.init_denoise,
                    generate_audio_codes=args.generate_audio_codes, negative=neg),
                    client_id="lofi-batch")
            except urllib.error.HTTPError as e:
                print("    ❌ 提交失敗:", e.read().decode()[:500]); continue
            except urllib.error.URLError as e:
                print(f"    ❌ 無法連線 ComfyUI（{args.url}）：{e}")
                print("       請確認 ComfyUI 還在執行（scripts/launch_comfyui.sh）；中止本批。")
                break

            try:
                res, dt = comfy.wait_done(args.url, pid, timeout=args.timeout)
            except comfy.ComfyError as e:
                print(f"    ❌ 等待失敗，跳過此首：{e}")
                continue
            status = res.get("status", {}).get("status_str")
            if status != "success":
                print(f"    ❌ 生成失敗: {status} {res.get('status',{}).get('messages',[])[-2:]}")
                continue

            audio = res.get("outputs", {}).get("107", {}).get("audio", [])
            for a in audio:
                # ComfyUI 回報的 subfolder 是相對 output/ 根目錄（本管線 prefix 是 audio/<name>）
                # → 必須用 COMFY_OUTPUT 當底；用 COMFY_AUDIO_OUT 會變成 output/audio/audio/...
                src = COMFY_OUTPUT / a.get("subfolder", "") / a["filename"]
                if not src.exists():                      # 後備：prefix 沒帶子夾時檔案在 output/audio/
                    alt = COMFY_AUDIO_OUT / a["filename"]
                    if alt.exists():
                        src = alt
                if not src.exists():
                    print(f"    ⚠️  找不到輸出檔 {src}")
                    continue

                track_dir = cat.track_subdir(style, args.run_id)
                if args.format == "flac":
                    # 無損：母帶放外接（output/masters），內接只留轉出來的 mp3 工作檔
                    dst = _unique(track_dir / (Path(a["filename"]).stem + ".mp3"))
                    master = _unique(cat.masters_subdir(style, args.run_id) / (dst.stem + ".flac"))
                    ensure_external_dir(master.parent, "無損母帶目錄")
                    shutil.copy2(src, master)
                    transcode_to_mp3(master, dst)
                    if args.clean_raw:
                        src.unlink()
                    note = f"{dst.name}（母帶 {master.name} → 外接）"
                else:
                    dst = _unique(track_dir / a["filename"])
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    master = None
                    if args.clean_raw:
                        src.unlink()
                    note = dst.name
                print(f"    ✅ {dt:.0f}s -> {note}" + ("（已刪除 ComfyUI 原始檔）" if args.clean_raw else ""))
                ok += 1

                if not args.no_catalog:
                    record_track(dst, row, style, rid, seed, args, csv_path,
                                 master_file=master, negative=neg)
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
