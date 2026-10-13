#!/usr/bin/env python3
"""release.py — 把一個生成 run 包成「可上架的 release」（串流／商店上架包）。

定位：產線的責任是**生出上架包**，不是自動上架。
DSP（Spotify/Apple Music…）與 Bandcamp 都**沒有公開 API**，所以自動化的正確形狀是
「產出齊備的資料夾 ＋ 一頁清單」，人只負責拖檔與填表。理由與平台地圖見 docs/DISTRIBUTION.md。

一個生成 run（`assets/tracks/<style>/<run>/`）本身就是**一張專輯**，本模組負責：

  1. 依檔名順序讀 sidecar，取曲名（`mood`）／調性／BPM
  2. 把 mp3 過 loudnorm 成正規化母帶（預設 -14 LUFS / -1 dBTP）
  3. 產生 3000×3000 方圖封面（**純圖、不疊字**；本機無 Pillow/ImageMagick，見 DISTRIBUTION §6-1）
  4. 寫 `release.json` / `tracklist.csv` / `CHECKLIST.md`
  5. append 一筆事件到 `catalog/releases.jsonl`（append-only、只記事件不含整包快照）

用法:
  lofi release plan    --run coastal01                     # 只看會產出什麼，不寫檔
  lofi release package --run coastal01                     # artist 取 config/LOFI_RELEASE_ARTIST
  lofi release package --run coastal01 --artist "Slow Harbour"
  lofi release package --style coastal_guitar --run coastal01 --visual-run sc01
  lofi release package --run coastal01 --format flac        # 母帶用 FLAC（檔案較小）

輸出（預設在 output/releases/<run>/，外接碟）:
  audio/01_<slug>.wav   正規化母帶（ID3 已填）
  cover_3000x3000.jpg   封面
  release.json          機器用（album/artist/track 順序/授權 provenance）
  tracklist.csv         人用（No/Title/Duration/Key/BPM/Mood/ISRC）
  CHECKLIST.md          各平台上架步驟
"""
import argparse
import csv
import io as _io
import json
import re
import sys
from pathlib import Path

from lofi import catalog as cat
from lofi import config
from lofi import io
from lofi import media
from lofi import meta as mm
from lofi.character import load_character
from lofi.paths import (CATALOG_DIR, RELEASES_DIR, ROOT, TRACKS_DIR, VISUALS_DIR)

RELEASES_JSONL = CATALOG_DIR / "releases.jsonl"
SCHEMA_VERSION = 1
COVER_SIZE = 3000
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
LOSSLESS_EXTS = (".flac", ".wav", ".aiff", ".aif")   # 無損母帶可接受的容器
# 衍生檔的保留字首（見 docs/NAMING.md）：不適合當封面
DERIVED_PREFIXES = ("anim-", "cmp-", "mask-", "fix-")


# ── 純函式（可單元測試，不碰檔案）───────────────────────────────────────

def slugify(text, maxlen=40):
    """曲名 → 檔名用 slug（小寫英數 + 連字號）。"""
    s = str(text or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:maxlen].strip("-")


def unique_titles(titles):
    """專輯內曲名去重：同名者依出現順序加 ` (pt. N)`。

    為什麼要：sidecar 的 `mood` 會重複（同風格多首共用同一句），
    而同一張 release 內曲名重複會讓 DSP／版稅申報混淆。
    用「出現順序」而非隨機，確保重跑結果一致（可重現）。
    """
    seen = {}
    out = []
    for t in titles:
        key = str(t or "").strip().lower()
        if key in seen:
            seen[key] += 1
            out.append(f"{t} (pt. {seen[key]})")
        else:
            seen[key] = 1
            out.append(t)
    return out


def track_title(audio, sidecar):
    """曲名：優先用 sidecar 的 mood，否則退回檔名。"""
    sc = sidecar or {}
    if sc.get("mood"):
        return mm.prettify(str(sc["mood"]))
    return mm.prettify(Path(audio).stem)


def parse_loudnorm_json(stderr):
    """從 ffmpeg loudnorm 的 stderr 抓出量測 JSON；解不開回 None。

    loudnorm 會先印一堆 log，JSON 夾在 `{ ... }` 裡（且不含巢狀大括號），
    所以取最後一個最外層大括號區塊來解析。
    """
    if not stderr or '"input_i"' not in stderr:
        return None
    m = re.findall(r"\{[^{}]*\}", stderr)
    for block in reversed(m):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if "input_i" in data:
            return data
    return None


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def build_tracklist_csv(rows):
    """rows: [{no,title,duration,key,bpm,mood,file,isrc}] → CSV 字串（含表頭）。"""
    buf = _io.StringIO()
    w = csv.writer(buf)
    w.writerow(["No", "Title", "Artist", "Duration", "Key", "BPM", "Mood", "ISRC(new)", "File"])
    for r in rows:
        w.writerow([r["no"], r["title"], r.get("artist", ""), mm.fmt_ts(r.get("duration") or 0),
                    r.get("key", ""), r.get("bpm", ""), r.get("mood", ""), "", r.get("file", "")])
    return buf.getvalue()


def build_checklist(rec):
    """產出一頁上架清單（繁中）。"""
    artist = rec.get("artist", "")
    album = rec.get("album", "")
    n = len(rec.get("tracks") or [])
    lines = [
        f"# 上架清單：{album} — {artist}",
        "",
        f"- 曲數：{n}　｜　母帶：`audio/`（{rec.get('master', {}).get('lufs'):g} LUFS / "
        f"{rec.get('master', {}).get('tp'):g} dBTP）",
        f"- 封面：`cover_3000x3000.jpg`（{COVER_SIZE}×{COVER_SIZE}，無文字）",
        f"- 母帶來源：{rec.get('master_source', '')}",
        f"- 用到的模型（商用 provenance）："
        + "、".join(f"`{c['model']}`（{c['license']}）" for c in rec.get("credits") or []) or "（無紀錄）",
        "",
        "## 0. 發布前檢查（必做）",
        "",
        "- [ ] `./scripts/lofi license check` 全部可商用",
        "- [ ] 每首歌**聽過**（自動品檢只抓無聲/削波，抓不到「難聽」）",
        "- [ ] 曲名讀一遍，重複者已加 `pt. N` 或改成更適合的名字（改 `release.json` 後重跑）",
        "- [ ] 封面確認是你要的那張（`--cover`／`--visual-run` 可換）",
        "",
        "## A. DSP 串流（Spotify / Apple Music / Amazon / Tidal …一次到 150+ 平台）",
        "",
        "> 沒有 API。DistroKid / Amuse / TuneCore / RouteNote / CD Baby 都是手動上傳。",
        "> 每首歌若沒有 ISRC，發行商會自動配發（`tracklist.csv` 留空即代表要用新的）。",
        "",
        "1. 登入發行商後台 → 新增 Release → 類型選 **Album**（或 EP）",
        "2. 上傳 `audio/` 全部曲目，**依檔名順序**排 track number",
        f"3. Album title：`{album}`　Artist：`{artist}`"
        + (f"　Label：`{rec.get('label')}`" if rec.get("label") else ""),
        "4. 上傳封面 `cover_3000x3000.jpg`",
        "5. 逐首填曲名（見 `tracklist.csv`）、genre、mood、explicit = No",
        "6. 選發布日（建議留 2 週給 Spotify 編輯部 pitch 時間）",
        "",
        "## B. Bandcamp（無 API，但單價最高、可賣無損）",
        "",
        "1. 新增 album → 上傳 `audio/`（可同時上 WAV／FLAC）",
        "2. 封面同上；描述貼 `release.json` 的 album_note（或自己寫）",
        "3. 定價：建議「名稱你的價錢（name your price）」先累積聽眾",
        "",
        "## C. stock／sync 授權（單曲為單位、人工審核）",
        "",
        "1. Pond5 / AudioJungle / Songtradr 各建帳號，一軌一檔上傳",
        "2. 描述務必帶「AI-generated」與本包附的模型授權（`release.json` 的 `credits`）",
        "3. 標籤用 mood／instruments，不要用「lofi」這種太雜的詞當唯一標籤",
        "",
        "## D. 記回狀態",
        "",
        "- [ ] 上架完成後，把各平台 URL 記到 `catalog/releases.jsonl` 或 `release.json`",
        "- [ ] `catalog/releases.jsonl` 是 append-only；狀態變更用新事件，不要改舊行",
        "",
    ]
    return "\n".join(lines)


def summarize_release_event(rec):
    """事件流只記「發生什麼」，完整狀態在 release.json（避免 JSONL 膨脹）。"""
    t = rec.get("tracks")
    ev = {
        "run": rec.get("run"), "style": rec.get("style"),
        "album": rec.get("album"), "artist": rec.get("artist"),
        "tracks": len(t) if isinstance(t, (list, tuple)) else t,
        "format": rec.get("format"), "master_source": rec.get("master_source"),
        "dir": rec.get("dir"),
    }
    return {k: v for k, v in ev.items() if v not in (None, "", [], {})}


# ── 檔案層 ────────────────────────────────────────────────────────────

def find_run_dir(style, run):
    """找出 assets/tracks/<style>/<run>/；沒給 style 時只找唯一的 run。"""
    if not run:
        sys.exit("需要 --run（生成批次的 run-id，例如 coastal01）")
    if style:
        d = cat.track_subdir(style, run)
        if d.is_dir():
            return d
        sys.exit(f"找不到 {cat.relpath(d)}")
    hits = [p for p in sorted(TRACKS_DIR.glob(f"*/{run}")) if p.is_dir()]
    if not hits:
        sys.exit(f"在 assets/tracks/*/ 找不到 run「{run}」")
    if len(hits) > 1:
        sys.exit("多個風格都有同名 run：" + "、".join(cat.relpath(h) for h in hits) + "（請加 --style）")
    return hits[0]


def load_run(style, run, limit=0, offset=0, title_overrides=None):
    """回傳 [{audio, sidecar, ...}]，依檔名順序（即生成順序）。

    - `limit`/`offset`：只取一段（一個 run 可能有 30+ 首，整張專輯太長）。
    - `title_overrides`：{來源檔 relpath: 曲名}。用來「保住人工改過的曲名」：
      重跑時優先用既有 release.json 的曲名，不會被 sidecar 的 mood 蓋回去。
    """
    d = find_run_dir(style, run)
    audios = sorted(p for p in d.iterdir() if p.is_file() and cat.is_audio(p))
    if offset:
        audios = audios[offset:]
    if limit:
        audios = audios[:limit]
    if not audios:
        sys.exit(f"{cat.relpath(d)} 裡沒有可用的音檔（檢查 --limit/--offset）")
    rows = []
    for a in audios:
        sc = cat.load_sidecar(a) or {}
        dur = (sc.get("gen") or {}).get("duration") or media.ffprobe_duration(a)
        prev = (title_overrides or {}).get(cat.relpath(a))
        rows.append({
            "audio": a, "sidecar": sc,
            "title": prev or track_title(a, sc),
            "mood": sc.get("mood", ""), "key": sc.get("key", ""), "bpm": sc.get("bpm", ""),
            "duration": dur, "style": sc.get("style", style),
            "master_src": find_lossless_master(a, sc, style, run),
        })
    titles = unique_titles([r["title"] for r in rows])
    for i, (r, t) in enumerate(zip(rows, titles), 1):
        r["title"] = t
        r["no"] = i
    return d, rows


def load_prior_tracks(release_json):
    """讀既有 release.json，回傳 {來源檔 relpath: {"title":..., "audio":...}}。

    用途有二：**保住人工改過的曲名**，以及**清掉改名前留下的孤兒母帶**
    （否則改名重跑會在 audio/ 留下多餘檔，上架時容易誤傳）。
    """
    if not Path(release_json).exists():
        return {}
    try:
        rec = json.loads(Path(release_json).read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    out = {}
    for t in rec.get("tracks") or []:
        if t.get("file") and t.get("title"):
            out[t["file"]] = {
                "title": re.sub(r" \(pt\. \d+\)$", "", str(t["title"])),
                "audio": t.get("audio") or "",
            }
    return out


def collect_credits(rows):
    """彙整用到的音樂模型（商用 provenance）；沿用 meta 的邏輯。"""
    credits = {}
    for r in rows:
        gen = r["sidecar"].get("gen") or {}
        model = gen.get("model")
        if not model:
            continue
        info = config.model_license(model)
        credits[model] = {
            "model": model,
            "license": gen.get("model_license") or info["license"],
            "source": gen.get("model_source") or info["source"],
            "commercial": info["commercial"],
        }
    return [credits[k] for k in sorted(credits)]


def license_gate(rows, allow_unknown):
    """商用守門：model 授權未知／禁止即 fail-closed（比照 generate/visual）。"""
    names = [(r["sidecar"].get("gen") or {}).get("model") for r in rows]
    names = sorted({n for n in names if n})     # 去重：同一批通常只有一顆模型
    if not names:
        print("  ⚠️  這批 sidecar 沒有記錄模型（舊曲）→ 無法自動稽核授權；上架前請自行確認。")
        return
    blocked, findings = config.check_commercial(names, allow_unknown=allow_unknown)
    for n, info in findings:
        mark = {True: "✅", False: "⛔"}.get(info["commercial"], "⚠️")
        print(f"  {mark} 授權 {n}：{info['license']}")
    if blocked:
        sys.exit("模型授權有疑慮（見上）→ 商用前請確認，或加 --allow-unknown-license。"
                 "\n（模型登錄於 src/lofi/config.py；詳見 docs/LICENSING.md）")


def probe_wh(path):
    """回傳 (width, height)；量不到回 (None, None)。"""
    rc, out, _ = media.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)])
    if rc != 0 or "x" not in (out or ""):
        return None, None
    try:
        w, h = out.strip().splitlines()[0].split("x")[:2]
        return int(w), int(h)
    except (ValueError, IndexError):
        return None, None


def probe_audio_sr(path):
    """回傳音檔取樣率（int）；量不到回 None。

    **一定要明確帶 -ar**：loudnorm 內部會把訊號升到 192kHz 處理，
    不指定輸出取樣率時偶發性地輸出 192kHz 檔（同批不同檔還會不一致）——
    串流母帶必須是 44.1/48kHz，所以這裡量測來源再原樣輸出。
    """
    rc, out, _ = media.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate", "-of", "csv=p=0", str(path)])
    if rc != 0:
        return None
    try:
        return int(out.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


def _rel(p):
    """記錄用路徑：優先相對於 ROOT（**不解 symlink**）。

    `output/` 是外接碟 symlink，`catalog.relpath` 會 resolve 出去而退回絕對路徑；
    紀錄裡想看到的是穩定的 `output/releases/<run>`，所以這裡先試 relative_to。
    """
    try:
        return str(Path(p).relative_to(ROOT))
    except ValueError:
        return cat.relpath(p)


def _kind_priority(name):
    """封面挑選的資料夾優先序：sc（場景）> ref（定裝）> ds（訓練集）> fix/其他。"""
    for i, k in enumerate(("sc", "ref", "ds")):
        if name.startswith(k):
            return i
    return len(("sc", "ref", "ds"))


def character_score(prompt, character):
    """用 manifest 的 prompt 判別這張圖有沒有主角。

    回傳 >0 = 是主角（數字為命中特徵數，越大越像）；-1 = 不是主角。
    manifest 逐檔存了 prompt，所以不必另外維護「哪些 run 有貓」的清單。
    """
    m = (character or {}).get("cover_match") or {}
    t = (prompt or "").lower()
    if any(n.lower() in t for n in m.get("never") or []):
        return -1
    if not any(k.lower() in t for k in m.get("must") or []):
        return -1
    return 1 + sum(1 for f in m.get("features") or [] if f.lower() in t)


def _run_images(d):
    """回傳某 run 的 [(圖檔, prompt)]；優先用 manifest 的逐檔 prompt，沒有就掃檔名。"""
    out = []
    mf = d / "manifest.json"
    if mf.exists():
        try:
            imgs = json.loads(mf.read_text(encoding="utf-8")).get("images") or []
        except json.JSONDecodeError:
            imgs = []
        for it in imgs:
            f = d / str(it.get("file", ""))
            if f.is_file() and f.suffix.lower() in IMAGE_EXTS:
                out.append((f, str(it.get("prompt") or "")))
    if not out:
        out = [(f, "") for f in sorted(d.iterdir())
               if f.is_file() and f.suffix.lower() in IMAGE_EXTS
               and not f.name.startswith(DERIVED_PREFIXES)]
    return out


def pick_cover(visual_run=None):
    """挑封面：**必須是主角（黑貓 Moni）**，其次才比 run 種類與新舊。

    為什麼：品牌 Moni's Journey 的主體就是這隻貓，封面不能出現別的貓（`sc01/sc02/sc06`
    是橘虎斑）或無主角的圖。判別條件來自 `prompts/character/<name>.json`。

    沒指定 `--visual-run` 時會掃**所有** run（不只是最新的 sc）——否則會挑到別隻貓。
    找不到主角圖時退回舊行為並**大聲警告**（要你改用 `--cover` 指定）。
    """
    if not VISUALS_DIR.exists():
        return None
    if visual_run:
        d = VISUALS_DIR / visual_run
        if not d.is_dir():
            sys.exit(f"找不到 {cat.relpath(d)}")
        runs = [d]
    else:
        runs = [p for p in VISUALS_DIR.iterdir() if p.is_dir()]
    if not runs:
        return None

    character = load_character()
    scored = []
    for d in runs:
        for f, prompt in _run_images(d):
            scored.append((character_score(prompt, character) if character else 0,
                           -_kind_priority(d.name), f.stat().st_mtime, f))
    if not scored:
        return None

    hero = [x for x in scored if x[0] > 0]
    if hero:
        hero.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        return hero[0][3]

    rest = sorted(scored, key=lambda x: (x[1], x[2]), reverse=True)
    name = (character or {}).get("name", "主角")
    print(f"⚠️  找不到{name}（主角）的圖，暫用 {cat.relpath(rest[0][3])}；"
          f"建議用 --cover 指定，或先補一張牠的圖。")
    return rest[0][3]


def master_track(src, dst, *, lufs, tp, fmt="wav", metadata=None):
    """兩段式 loudnorm 正規化（量測 → 套用）；量測失敗則退回單段動態模式。

    回傳實際套用的參數 dict（含 fallback 旗標），供 release.json 記錄來源。
    """
    common = f"loudnorm=I={lufs:g}:TP={tp:g}:LRA=11"
    rc, _, err = media.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(src),
         "-af", f"{common}:print_format=json", "-f", "null", "-"])
    meas = parse_loudnorm_json(err) if rc == 0 else None

    if meas:
        af = (f"{common}:measured_I={_f(meas.get('input_i')):g}"
              f":measured_TP={_f(meas.get('input_tp')):g}"
              f":measured_LRA={_f(meas.get('input_lra')):g}"
              f":measured_thresh={_f(meas.get('input_thresh')):g}"
              f":offset={_f(meas.get('target_offset')):g}:linear=true")
        mode = "two-pass"
    else:
        af = common
        mode = "single-pass（量測失敗）"

    codec = "flac" if fmt == "flac" else "pcm_s24le"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-af", af,
           "-c:a", codec]
    sr = probe_audio_sr(src)
    if sr:
        cmd += ["-ar", str(sr)]           # 明確指定，避免 loudnorm 留下 192kHz
    if fmt != "flac":
        cmd += ["-write_id3v2", "1"]      # WAV 內嵌 ID3，讓 DSP/播放器讀得到
    for k, v in (metadata or {}).items():
        if v not in (None, ""):
            cmd += ["-metadata", f"{k}={v}"]
    cmd.append(str(dst))
    rc, _, err = media.run(cmd)
    if rc != 0:
        sys.exit(f"母帶處理失敗：{src.name}\n{err.strip()[:400]}")

    applied = {"lufs": lufs, "tp": tp, "mode": mode,
               "measured_in_lufs": _f(meas.get("input_i")) if meas else None,
               "format": fmt}
    return applied


def make_cover(src, dst, size=COVER_SIZE):
    """方形封面：置中裁切再放大（lanczos）。純圖、不疊字。"""
    w, h = probe_wh(src)
    if not w or not h:
        sys.exit(f"讀不到圖片尺寸：{src}")
    side = min(w, h)
    x, y = (w - side) // 2, (h - side) // 2
    rc, _, err = media.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-vf", f"crop={side}:{side}:{x}:{y},scale={size}:{size}:flags=lanczos",
         "-q:v", "2", str(dst)])
    if rc != 0:
        sys.exit(f"封面產生失敗：{err.strip()[:400]}")
    return dst


def append_release_event(rec):
    ev = {"ts": cat.now_iso(), "schema_version": SCHEMA_VERSION, "event": "release"}
    ev.update(summarize_release_event(rec))
    io.append_jsonl(RELEASES_JSONL, ev)
    return ev


# ── CLI ──────────────────────────────────────────────────────────────

def print_header(rec, n, dest, cover_src, cover_dst):
    line = "─" * 64
    print(line)
    print(f"▶ lofi.release（上架包）    run={rec['run']}    共 {n} 首")
    print(f"  artist：{rec['artist']}" + (f"     label：{rec['label']}" if rec.get("label") else ""))
    print(f"  album：{rec['album']}")
    print(f"  母帶：{rec['master']['lufs']:g} LUFS / {rec['master']['tp']:g} dBTP（{rec['format']}）")
    print(f"  母帶來源：{rec.get('master_source', '')}")
    if cover_src:
        print(f"  封面：{cat.relpath(cover_src)} → {cover_dst.name}（{COVER_SIZE}×{COVER_SIZE}，無文字）")
    else:
        print("  封面：⚠️  找不到場景圖（用 --cover 指定）")
    print(f"  輸出：{_rel(dest)}")
    print(line)


def cmd_plan(args):
    d, rows = load_run(args.style, args.run, limit=args.limit, offset=args.offset)
    cover = Path(args.cover) if args.cover else pick_cover(args.visual_run)
    print(f"來源：{cat.relpath(d)}（取 {len(rows)} 首"
          + (f"，offset {args.offset}" if args.offset else "")
          + (f"，limit {args.limit}" if args.limit else "") + "）")
    print(f"封面：{cat.relpath(cover) if cover else '⚠️ 找不到（--cover 或 --visual-run）'}")
    print(f"母帶來源：{describe_master_source(rows)}")
    print()
    print(f"{'#':>2}  {'Title':<38} {'Len':>6} {'BPM':>4}  Key")
    for r in rows:
        print(f"{r['no']:>2}  {r['title']:<38} {mm.fmt_ts(r['duration'] or 0):>6} "
              f"{str(r['bpm'] or ''):>4}  {r['key'] or ''}")
    total = sum(r["duration"] or 0 for r in rows)
    est_mb = total * 0.29                # 24-bit 48k 立體 ≈ 0.29 MB/s
    print()
    print(f"合計 {mm.fmt_ts(total)}，母帶約 {est_mb:.0f} MB（{est_mb / 1000:.1f} GB）")
    dup = [r["title"] for r in rows if "(pt. " in r["title"]]
    if dup:
        print(f"⚠️  {len(dup)} 首曲名在專輯內重複，會自動加「(pt. N)」：{', '.join(dup[:4])}"
              + ("…" if len(dup) > 4 else ""))
        print("   建議：用 --limit/--offset 挑一組，或先改 sidecar 的 mood，或產出後編 release.json 再重跑。")
    print("（plan 不寫任何檔案；要真的產出請用 `lofi release package`）")


def describe_master_source(rows):
    """描述這張 release 的母帶來源（給 release.json / CHECKLIST 用）。"""
    total = len(rows)
    n_ll = sum(1 for r in rows if r.get("master_src"))
    n_flac = sum(1 for r in rows if (r["sidecar"].get("gen") or {}).get("format") == "flac")
    if total and n_ll == total:
        return "無損母帶（output/masters 的 FLAC/WAV）直接正規化"
    if n_ll:
        return f"混合：{n_ll}/{total} 首用無損母帶，其餘用工作 mp3（有損）"
    if total and n_flac == total:
        return "工作 mp3（由無損母帶轉出，但母帶檔不在 output/masters）"
    return "來源為 mp3（ACE-Step 原始輸出），經 loudnorm 正規化"


def find_lossless_master(audio, sidecar, style, run):
    """找出這首的無損母帶（output/masters/...，外接碟）；沒有回 None。

    優先序：sidecar 的 `master_file`（生成時寫入）→ 母帶夾內同名 stem 的無損檔。
    找不到就退回工作用的 mp3（release 仍會產出，只是 `master_source` 會註明有損）。
    """
    sc = sidecar or {}
    mf = sc.get("master_file")
    if mf:
        p = ROOT / mf
        if p.exists():
            return p
    d = cat.masters_subdir(sc.get("style") or style, sc.get("run_id") or run)
    for ext in LOSSLESS_EXTS:
        p = d / f"{Path(audio).stem}{ext}"
        if p.exists():
            return p
    return None


def cmd_package(args):
    if not (config.RELEASE_ARTIST or args.artist):
        sys.exit("尚未設定 artist 名義。\n"
                 "  · 臨時：lofi release package --artist \"你的音樂名\"\n"
                 "  · 長期：設 LOFI_RELEASE_ARTIST，或改 src/lofi/config.py 的 RELEASE_ARTIST\n"
                 "  （刻意與 YouTube 頻道名分開，見 docs/DISTRIBUTION.md §6-2）")

    media.require("ffmpeg", "ffprobe")

    dest = RELEASES_DIR / args.run
    prior = load_prior_tracks(dest / "release.json")
    d, rows = load_run(args.style, args.run, limit=args.limit, offset=args.offset,
                       title_overrides={k: v["title"] for k, v in prior.items()})
    artist = args.artist or config.RELEASE_ARTIST
    album = args.album or mm.prettify(rows[0]["style"] or args.run)

    audio_dir = dest / "audio"
    cover_src = Path(args.cover) if args.cover else pick_cover(args.visual_run)
    cover_dst = dest / "cover_3000x3000.jpg"

    meta_master = {"lufs": config.MASTER_LUFS if args.lufs is None else args.lufs,
                   "tp": config.MASTER_TP if args.tp is None else args.tp}

    total_dur = sum(r["duration"] or 0 for r in rows)
    est_gb = max(total_dur * 0.29 / 1000 * 2, 2.0)   # 母帶 + 餘裕
    media.require_free(RELEASES_DIR, est_gb, "外接碟 output/releases")

    rec = {
        "release_version": SCHEMA_VERSION, "run": args.run, "style": rows[0]["style"],
        "album": album, "artist": artist, "label": args.label or config.RELEASE_LABEL,
        "genre": args.genre, "format": args.format,
        "master": meta_master,
        "master_source": describe_master_source(rows),
        "cover_source": cat.relpath(cover_src) if cover_src else None,
        "dir": _rel(dest),
        "created_at": cat.now_iso(),
        "credits": [],
        "tracks": [],
        "notes": [],
    }

    print_header(rec, len(rows), dest, cover_src, cover_dst)
    license_gate(rows, args.allow_unknown_license)

    if not rows[0]["sidecar"]:
        rec["notes"].append("這批 sidecar 為空 → 曲名/調性可能不準，上架前請人工確認")

    # 1) 母帶
    audio_dir.mkdir(parents=True, exist_ok=True)
    for r in rows:
        stem = f"{r['no']:02d}_{slugify(r['title']) or ('track' + str(r['no']))}"
        out = audio_dir / f"{stem}.{rec['format']}"
        if out.exists() and not args.force:
            print(f"[{r['no']:>2}] 已存在，略過：{out.name}")
            r["audio_out"] = out
            r["master"] = {"mode": "reuse", "lufs": rec["master"]["lufs"], "tp": rec["master"]["tp"]}
            continue
        print(f"[{r['no']:>2}] {r['title']} → {out.name}", flush=True)
        source = r["master_src"] or r["audio"]
        r["master"] = master_track(
            source, out, lufs=rec["master"]["lufs"], tp=rec["master"]["tp"], fmt=rec["format"],
            metadata={"title": r["title"], "artist": artist, "album": album,
                      "track": f"{r['no']}/{len(rows)}", "genre": rec["genre"] or ""})
        r["master"]["lossless"] = bool(r["master_src"])
        r["master"]["source"] = _rel(source)
        # 改過曲名 → 舊母帶是孤兒檔，刪掉（否則上架時會多看到一首不存在的歌）
        old = (prior.get(cat.relpath(r["audio"])) or {}).get("audio")
        if old:
            oldp = ROOT / old
            if oldp != out and oldp.exists() and oldp.parent == audio_dir:
                oldp.unlink()
                print(f"       ↳ 刪除改名前舊檔 {oldp.name}")
        r["audio_out"] = out

    # 2) 封面
    if cover_src:
        dest.mkdir(parents=True, exist_ok=True)
        make_cover(cover_src, cover_dst)
        print(f"封面 → {cover_dst.name}")

    # 3) 記錄與清單
    rec["credits"] = collect_credits(rows)
    rec["tracks"] = [{
        "no": r["no"], "title": r["title"], "mood": r["mood"], "key": r["key"], "bpm": r["bpm"],
        "duration": round(r["duration"], 1) if r["duration"] else None,
        "file": cat.relpath(r["audio"]), "audio": _rel(r["audio_out"]),
        "lossless": bool(r["master_src"]),
        "isrc": None, "master": r.get("master"),
    } for r in rows]
    rec["total_duration"] = round(sum(r["duration"] or 0 for r in rows), 1)
    rec["dir"] = _rel(dest)

    dest.mkdir(parents=True, exist_ok=True)
    io.atomic_write_json(dest / "release.json", rec)
    csv_rows = [{**t, "artist": artist} for t in rec["tracks"]]
    io.atomic_write_text(dest / "tracklist.csv", build_tracklist_csv(csv_rows))
    io.atomic_write_text(dest / "CHECKLIST.md", build_checklist(rec))
    append_release_event(rec)

    print()
    print(f"✅ {_rel(dest)}")
    print(f"   曲數 {len(rows)}｜總長 {mm.fmt_ts(rec['total_duration'])}｜母帶 {rec['master']['lufs']:g} LUFS")
    print(f"   → release.json / tracklist.csv / CHECKLIST.md")
    print("   接下來：照 CHECKLIST.md 走（DSP 沒有 API，要手動上傳）。")
    if any("(pt. " in t["title"] for t in rec["tracks"]):
        print("   ⚠️  有曲名自動加了「(pt. N)」，建議改成更好聽的名字後重跑。")


def main():
    ap = argparse.ArgumentParser(description="把一個生成 run 包成可上架的 release（上架包）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(p):
        p.add_argument("--run", required=True, help="生成批次的 run-id（例如 coastal01）")
        p.add_argument("--style", default="", help="風格名（同名 run 有多個時必填）")
        p.add_argument("--cover", default="", help="指定封面來源圖（預設挑 sc 場景 run）")
        p.add_argument("--visual-run", default="", dest="visual_run",
                       help="從指定 visuals run（例如 sc01）挑封面")
        p.add_argument("--limit", type=int, default=0, help="只取前 N 首（0=全部）")
        p.add_argument("--offset", type=int, default=0, help="從第幾首開始（0-based）")

    p1 = sub.add_parser("plan", help="只看會產出什麼（不寫檔）")
    add_common(p1)
    p1.set_defaults(func=cmd_plan)

    p2 = sub.add_parser("package", help="真的產出上架包")
    add_common(p2)
    p2.add_argument("--artist", default="", help="artist 名義（預設 config.RELEASE_ARTIST / LOFI_RELEASE_ARTIST）")
    p2.add_argument("--album", default="", help="專輯名（預設用風格名）")
    p2.add_argument("--label", default="", help="label（預設 config.RELEASE_LABEL）")
    p2.add_argument("--genre", default=config.RELEASE_GENRE, help="genre（預設 Lo-fi）")
    p2.add_argument("--format", default="wav", choices=["wav", "flac"], help="母帶格式（預設 wav）")
    p2.add_argument("--lufs", type=float, default=None, help="目標響度（預設 config.MASTER_LUFS）")
    p2.add_argument("--tp", type=float, default=None, help="true peak 上限 dBTP（預設 config.MASTER_TP）")
    p2.add_argument("--force", action="store_true", help="已存在的母帶也重新處理")
    p2.add_argument("--allow-unknown-license", action="store_true",
                    help="允許授權未登錄的模型（商用前請先確認，見 docs/LICENSING.md）")
    p2.set_defaults(func=cmd_package)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
