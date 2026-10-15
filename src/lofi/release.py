#!/usr/bin/env python3
"""release.py — 把一個生成 run 包成「可上架的 release」（串流／商店上架包）。

定位：產線的責任是**生出上架包**，不是自動上架。
DSP（Spotify/Apple Music…）與 Bandcamp 都**沒有公開 API**，所以自動化的正確形狀是
「產出齊備的資料夾 ＋ 一頁清單」，人只負責拖檔與填表。理由與平台地圖見 docs/DISTRIBUTION.md。

一個生成 run（`assets/tracks/<style>/<run>/`）本身就是**一張專輯**，本模組負責：

  1. 依檔名順序讀 sidecar，取曲名（`mood`）／調性／BPM
  2. 把 mp3 過 loudnorm 成正規化母帶（預設 -14 LUFS / -1 dBTP）
  3. 產生 3000×3000 方圖封面（**純圖、不疊字**；本機無 Pillow/ImageMagick，見 DISTRIBUTION §6-1）
  4. 寫 `release.json` / `tracklist.csv` / `LISTING.md` / `credits.txt` / `CHECKLIST.md`
  5. 打包 `download/<album>.zip`（Gumroad／Bandcamp 的交付檔）
  6. append 一筆事件到 `catalog/releases.jsonl`（append-only、只記事件不含整包快照）

為什麼要多 `LISTING.md`：DSP 商（DistroKid…）**沒有 API**，而它們的表單欄位（genre／explicit／
songwriter 真名／AI Credits／封面／ISRC）散在好幾頁。這裡一次把**兩個平台要填的值**印成一頁，
上架時只負責複製貼上；`release.json` 則保留機器可讀的同一份資料（見 docs/DISTRIBUTION.md §5）。

用法:
  lofi release plan    --run coastal01                     # 只看會產出什麼，不寫檔
  lofi release package --run coastal01                     # artist 取 config/LOFI_RELEASE_ARTIST
  lofi release package --run coastal01 --artist "Slow Harbour"
  lofi release package --style coastal_guitar --run coastal01 --visual-run sc01
  lofi release package --run coastal01 --format flac        # 母帶用 FLAC（檔案較小）

輸出（預設在 output/releases/<run>/，外接碟）:
  audio/01_<slug>.wav   正規化母帶（ID3 已填）
  cover_3000x3000.jpg   封面（方形；Gumroad 縮圖用的同一套圖）
  artwork_3840x2160.jpg 交付 ZIP 用 4K 高畫質橫式圖（Gumroad 16:9 封面用的同一套圖）
  gumroad_*.jpg         商店頁直接上傳用（封面 1920×1080／縮圖 1080×1080）
  download/<album>.zip  交付檔（audio/＋方形與橫式兩張圖＋tracklist＋credits，給 Gumroad／Bandcamp）
  release.json          機器用（album/artist/track 順序/genre/explicit/songwriter/AI credits/授權 provenance）
  tracklist.csv         人用（No/Title/Duration/Key/BPM/Mood/ISRC）
  LISTING.md            上架文案（DistroKid／Gumroad 逐欄對照；直接複製貼上）
  credits.txt           隨交付檔附上的 AI 揭露與模型授權（商用 provenance）
  CHECKLIST.md          各平台上架步驟
"""
import argparse
import csv
import html as _html
import io as _io
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

from lofi import catalog as cat
from lofi import config
from lofi.meta import unique_titles      # noqa: F401（專輯內曲名去重；給曲目表與上架包共用）

# 曲目清單的手動編號前綴（給 listing_text 保留、listing_html 剝掉）
LEADING_NO = re.compile(r"^(\d{1,3})\s+")
from lofi import io
from lofi import media
from lofi import meta as mm
from lofi.character import load_character
from lofi.paths import (CATALOG_DIR, RELEASES_DIR, ROOT, TRACKS_DIR, VISUALS_DIR)

RELEASES_JSONL = CATALOG_DIR / "releases.jsonl"
SCHEMA_VERSION = 1
COVER_SIZE = 3000
# 交付 ZIP 內附的高畫質橫式圖（＝商店 16:9 封面的同一套圖，但解析度更高）。
# 兩張都給全：方形 3000²（商店縮圖同一套）＋ 橫式 3840×2160（商店封面同一套，4K 高畫質）。
# 來源優先用該集 AI 放大過的 `*_fix-4k`（pick_wide_cover 取解析度最高者），所以真的是 4K 細節。
ARTWORK_SIZE = (3840, 2160)
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
LOSSLESS_EXTS = (".flac", ".wav", ".aiff", ".aif")   # 無損母帶可接受的容器

# 執行緒上限與 niceness：不設時 ffmpeg 會吃滿所有核心，在背景生成時會明顯搶 CPU。
# 可用 `--threads/--nice` 或環境變數 LOFI_THREADS／LOFI_NICE 指定（比照 make_long_lofi.sh 的 THREADS/NICE）。
_THREADS = int(os.environ.get("LOFI_THREADS", "0") or 0)
_NICE = int(os.environ.get("LOFI_NICE", "0") or 0)
# 衍生檔的保留字首（見 docs/NAMING.md）：不適合當封面
DERIVED_PREFIXES = ("anim-", "cmp-", "mask-", "fix-")

# ── 上架文案／平台欄位 ─────────────────────────────────────────────────
# distroKid 的 AI Credits 選項（2026 起上傳流程強制填；見 docs/DISTRIBUTION.md §5）。
AI_CREDIT_SCOPES = ("none", "lyrics", "music", "part_audio", "all_audio")
AI_CREDIT_LABELS = {                      # 內部文件（LISTING.md/CHECKLIST.md）用繁中
    "none": "無 AI 生成（純人工）",
    "lyrics": "AI 寫詞",
    "music": "AI 作曲（旋律／編曲非人工）",
    "part_audio": "部分音訊為 AI 生成",
    "all_audio": "全部音訊為 AI 生成",
}
# credits.txt 給買家看的角色描述（**不要寫「Songwriter」**：旋律是 AI 生成的，
# 寫成作曲家等於誇大 authorship；DistroKid 的必填「Songwriter」欄位另見 LISTING.md）。
CREDIT_ROLE = "Produced, edited and mastered by"
RULE = "=" * 64                          # credits.txt 的大分隔線（用 ASCII，避免怪編輯器亂碼）
SUB = "-" * 64                           # 小分隔線
KV_WIDTH = 34                            # credits.txt 標籤欄寬（要大於最長標籤 + 1 個空白）
AI_CREDIT_LABELS_EN = {                   # 交付檔內附給**買家／平台**的說明一律英文
    "none": "no AI-generated content",
    "lyrics": "AI-written lyrics",
    "music": "AI-composed music",
    "part_audio": "part of the audio is AI-generated",
    "all_audio": "all of the audio is AI-generated",
}
# Gumroad Discover 的 tag 規則：每個 2–20 字、不含開頭 #、單一商品上限 10 個。
GUMROAD_MAX_TAGS = 10
TAG_MAXLEN = 20
# 專輯名裡不值得當 tag 的功能詞。
TAG_STOPWORDS = {"into", "the", "and", "with", "from", "this", "that", "for", "out",
                 "over", "under", "after", "before", "part", "vol"}
LISTING_BASE_TAGS = ("lofi", "instrumental", "study music", "sleep", "focus")


# ── 純函式（可單元測試，不碰檔案）───────────────────────────────────────

def slugify(text, maxlen=40):
    """曲名 → 檔名用 slug（小寫英數 + 連字號）。"""
    s = str(text or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:maxlen].strip("-")


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


TRACKLIST_COLUMNS = ["No", "Title", "Artist", "Album", "Year", "Duration", "Vibe",
                     "BPM", "Key", "Instrumental", "Explicit", "Format", "File", "Size (MB)"]


def build_tracklist_csv(rows, album="", artist="", year="", audio_format=""):
    """rows → **買家視角**的曲目表 CSV（含表頭）。

    這份 CSV 會被打包進交付 ZIP，讀者是買家（不是我們、也不是發行商）。所以欄位以
    「他想知道什麼」為準：拿到什麼檔／多大（`Format`/`File`/`Size`）、什麼味道
    （`Vibe`＝sidecar 的 mood，本來就是情境描述，買家拿來挑歌很好用）、能不能混
    （`BPM`/`Key`）、有沒有歌詞或限制級（`Instrumental`/`Explicit`）、哪張專輯哪一年。

    **不放 ISRC**：那是發行商欄位（`release.json` 與 `LISTING.md` 有）。
    `File` 放**交付 ZIP 內**的相對路徑，不是產線內部的來源路徑。
    """
    buf = _io.StringIO()
    w = csv.writer(buf)
    w.writerow(TRACKLIST_COLUMNS)
    for r in rows:
        rel = f"audio/{Path(str(r['audio'])).name}" if r.get("audio") else str(r.get("file") or "")
        w.writerow([
            r["no"], r["title"], r.get("artist") or artist, album, year,
            mm.fmt_ts(r.get("duration") or 0), r.get("mood", ""),
            r.get("bpm", ""), r.get("key", ""),
            "Yes" if r.get("instrumental", True) else "No",
            "Yes" if r.get("explicit") else "No",
            audio_format, rel, r.get("size_mb", ""),
        ])
    return buf.getvalue()


def clean_tags(tags, limit=GUMROAD_MAX_TAGS):
    """清成 Gumroad Discover 可接受的 tag。回傳 (保留, 被丟掉的)。

    規則（Gumroad Discover，2026-10 查核）：每個 2–20 字、**不含開頭 `#`**、不分大小寫去重、
    單一商品上限 10 個。超出上限的一律丟掉而不是截斷字，避免產生一半的 tag。
    """
    kept, rejected, seen = [], [], set()
    for t in tags or []:
        s = str(t or "").strip().lstrip("#").strip()
        key = s.lower()
        if len(s) < 2 or len(s) > TAG_MAXLEN or key in seen or len(kept) >= limit:
            if s:
                rejected.append(s)
            continue
        seen.add(key)
        kept.append(s)
    return kept, rejected


def listing_tags(rec):
    """由 genre／style／專輯名推 Gumroad 的 tag（順序＝優先序：越特定的越前面）。

    為什麼要自動推：tag 是 Discover 的唯一曝光入口（每個 tag＝一個頁面），
    而這些資訊 sidecar／release.json 已經有了，不必每次手打。
    """
    words = []
    if rec.get("genre"):
        words.append(str(rec["genre"]).lower())
    words += [w.lower() for w in re.split(r"[^0-9A-Za-z]+", str(rec.get("style") or "")) if w]
    for w in re.split(r"[^0-9A-Za-z]+", str(rec.get("album") or "")):
        if len(w) >= 4 and w.lower() not in TAG_STOPWORDS:
            words.append(w.lower())
    words += list(LISTING_BASE_TAGS)
    return clean_tags(words)[0]


def build_listing(rec, tags=None, price_usd=None):
    """上架文案（Gumroad 的 summary/description、Bandcamp 的描述共用）＋ tags／定價。

    `tags` 有給就沿用（仍會過 `clean_tags`）；沒給就由 genre/style/專輯名推。

    ⚠️ **一律英文**：這是給商店／買家看的（Gumroad、DistroKid、Bandcamp 都是英文店面），
    本專案的內部中文說明在 `CHECKLIST.md`。故事段落刻意**不提 AI**，揭露只在 NOTES。
    """
    album = rec.get("album") or rec.get("run") or ""
    artist = rec.get("artist") or ""
    tracks = rec.get("tracks") or []
    n = len(tracks)
    dur = rec.get("total_duration") or 0
    genre = rec.get("genre") or "Lo-fi"
    fmt = (rec.get("format") or "wav").upper()
    master = rec.get("master") or {}
    minutes = max(1, round(dur / 60))
    short = (f'{n} tracks of instrumental {genre}, about {minutes} minutes — '
             f'"{album}" by {artist}.')
    scope = (rec.get("ai_credits") or {}).get("scope") or "none"
    lead = [f'"{album}" is a {genre} instrumental album by {artist} — '
            f'{n} tracks, {mm.fmt_ts(dur)}.',
            "All instrumental, no vocals: felt piano, warm pads, vinyl crackle and soft "
            "percussion, made for studying, working, reading or falling asleep."]
    sections = [{
        "heading": "WHAT YOU GET",
        "items": [f"{n} tracks in {fmt}, {mm.fmt_ts(dur)}",
                  f"Album cover, {COVER_SIZE} × {COVER_SIZE} px",
                  f"Cover art (16:9) in {ARTWORK_SIZE[0]} × {ARTWORK_SIZE[1]} px",
                  "Tracklist",
                  "Liner notes listing every model and licence used"],
        "closing": "One .zip download. The files are yours to keep.",
    }]
    if tracks:
        sections.append({"heading": "TRACKLIST", "ordered": True,
                         "items": [f"{int(t.get('no') or i):02d}  {t.get('title') or ''}"
                                   for i, t in enumerate(tracks, 1)]})
    notes = [f"Mastered to {_f(master.get('lufs'), -14):g} LUFS / "
             f"{_f(master.get('tp'), -1):g} dBTP — the level streaming services target."]
    if scope != "none":
        notes.append("Music and artwork are created with AI models licensed for commercial "
                     "use; the complete model and licence list ships with the download.")
    sections.append({"heading": "NOTES", "items": notes})
    if config.CHANNEL_URL:                       # 導流：商店的買家多半也是頻道觀眾
        sections.append({"heading": "LISTEN FREE",
                         "items": [f"Full-length mixes on YouTube: {config.CHANNEL_URL}"]})
    return {
        "short": short,
        "long": listing_text(lead, sections),      # 純文字：貼進表單／Bandcamp
        "html": listing_html(lead, sections),      # HTML：Gumroad 的商品頁（有標題與清單）
        "tags": clean_tags(tags)[0] if tags else listing_tags(rec),
        "price_usd": config.RELEASE_PRICE_USD if price_usd is None else price_usd,
    }


def listing_text(lead, sections):
    """商品描述的**純文字**版（段落之間空行、清單用 `- `）。"""
    parts = ["\n".join(lead)]
    for s in sections:
        items = "\n".join(i if s.get("ordered") else f"- {i}" for i in s["items"])
        parts.append(f"{s['heading']}\n{items}")
        if s.get("closing"):
            parts.append(s["closing"])
    return "\n\n".join(parts)


def _strip_no(text):
    """去掉「01  」這種手動曲目編號（給 <ol> 用；純文字版仍保留編號）。"""
    return LEADING_NO.sub("", text, count=1)


def listing_html(lead, sections):
    """商品描述的 **HTML** 版（Gumroad 的商品頁吃 HTML：標題＋清單才會真的排版）。

    只用最保守的標籤（p/h3/ul/ol/li）並全部 escape——Gumroad 的 editor 會把它存成
    rich content；標籤愈少愈不容易被它的清洗規則改掉。

    注意：**<ol> 本身就會自動編號**，所以有序清單要剝掉資料層的 "01  " 前綴，
    否則頁面會出現「1. 01 Title」這種雙重編號。純文字版（listing_text）沒有自動編號，
    所以那裡保留編號。
    """
    esc = _html.escape
    parts = [f"<p>{esc(p)}</p>" for p in lead]
    for s in sections:
        ordered = bool(s.get("ordered"))
        tag = "ol" if ordered else "ul"
        items = "".join(
            f"<li>{esc(_strip_no(i) if ordered else i)}</li>" for i in s["items"])
        parts.append(f"<h3>{esc(s['heading'].title())}</h3><{tag}>{items}</{tag}>")
        if s.get("closing"):
            parts.append(f"<p>{esc(s['closing'])}</p>")
    return "".join(parts)


def probe_audio_format(path):
    """回傳交付母帶的 (取樣率, 位元深度)；量不到回 (None, None)。

    為什麼要量：`credits.txt` 要寫「24-bit / 48 kHz」這種事實，不能用猜的
    （ffmpeg 寫 WAV 時 `bits_per_sample` 常是空的，要退看 `bits_per_raw_sample`）。
    """
    rc, out, _ = media.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=sample_rate,bits_per_sample,bits_per_raw_sample",
         "-of", "default=noprint_wrappers=1", str(path)])
    if rc != 0:
        return None, None
    vals = dict(ln.split("=", 1) for ln in (out or "").splitlines() if "=" in ln)
    def _int(k):
        try:
            return int(str(vals.get(k, "")).strip() or 0) or None
        except ValueError:
            return None
    return _int("sample_rate"), (_int("bits_per_raw_sample") or _int("bits_per_sample"))


def cover_credit(rec):
    """封面圖用到的模型（讀該 visuals run 的 `manifest.json`），回傳 list of dict。

    封面不是音樂模型，所以不會出現在 `rec["credits"]`；但商用 provenance 要一致，
    這裡把 manifest 的 config（SDXL 的 `ckpt`／z-image 的 `unet`+`clip`+`vae`／放大模型）
    翻成授權資訊一起印出來。
    """
    src = str(rec.get("cover_source") or "")
    if not src:
        return []
    mf = (ROOT / src).parent / "manifest.json"
    if not mf.exists():
        return []
    try:
        cfg = json.loads(mf.read_text(encoding="utf-8")).get("config") or {}
    except json.JSONDecodeError:
        return []
    out, seen = [], set()
    for role in ("ckpt", "unet", "clip", "vae", "upscale"):
        name = cfg.get(role)
        if not name or name in seen:
            continue
        seen.add(name)
        info = config.model_license(name)
        out.append({"role": role, "model": name, "license": info["license"],
                    "source": info["source"], "commercial": info["commercial"]})
    return out


def build_credits_txt(rec):
    """交付檔內附的 credits.txt：**英文、分區塊、欄位對齊**（買家與平台都會讀）。

    ⚠️ **不寫「Songwriter」**：旋律是 AI 生成的，寫成詞曲作者會誇大 authorship，
    也跟同一份檔案的 AI 揭露矛盾（DistroKid 的表單欄位另見 `LISTING.md`）。
    """
    ai = rec.get("ai_credits") or {}
    scope = ai.get("scope") or "none"
    master = rec.get("master") or {}
    tracks = rec.get("tracks") or []
    fmt = (rec.get("format") or "wav").upper()
    sr, bits = master.get("sample_rate"), master.get("bits")
    audio_fmt = f"{bits}-bit / {sr / 1000:g} kHz {fmt}" if (bits and sr) else fmt
    art = cover_credit(rec)

    def kv(label, value):
        return f"  {label:<{KV_WIDTH}} {value}"

    L = []
    L += [RULE, f"  {(rec.get('album') or '').upper()}", f"  {rec.get('artist') or ''}", RULE]

    L += ["", "CREDITS", SUB,
          kv(CREDIT_ROLE, rec.get("songwriter") or "(not set)"),
          kv("Music", "AI-generated — see models below"),
          kv("Artwork", "AI-generated — see models below" if art else "(not recorded)")]

    L += ["", 'AI DISCLOSURE   (matches DistroKid "AI Credits")', SUB,
          kv("AI-generated content", AI_CREDIT_LABELS_EN.get(scope, scope)),
          kv("Artist name", "an AI persona" if ai.get("persona") else "a human"),
          kv("Lyrics", "none (instrumental)")]

    L += ["", "MODELS & LICENCES   (commercial provenance)", SUB]
    if rec.get("credits"):
        L.append("  MUSIC")
        for c in rec["credits"]:
            L += _credit_lines(c)
    else:
        L += ["  MUSIC", "    (none recorded)"]
    if art:
        L.append("  ARTWORK")
        for c in art:
            L += _credit_lines(c)

    L += ["", "MASTER & ASSETS", SUB,
          kv("Loudness", f"{_f(master.get('lufs'), -14):g} LUFS / "
                         f"{_f(master.get('tp'), -1):g} dBTP (loudnorm)"),
          kv("Audio", audio_fmt),
          kv("Cover", f"{COVER_SIZE} x {COVER_SIZE} px, JPG, no text"),
          kv("Tracks", str(len(tracks))),
          kv("Duration", mm.fmt_ts(rec.get("total_duration") or 0))]

    if tracks:
        L += ["", "TRACKLIST", SUB]
        for i, t in enumerate(tracks, 1):
            L.append(f"  {int(t.get('no') or i):02d}  {t.get('title') or ''}")
    return "\n".join(L).rstrip() + "\n"


def _credit_lines(c):
    """單一模型的授權區塊（給 build_credits_txt 用）。"""
    ok = "commercial use OK" if c.get("commercial") else "needs review"
    out = [f"    {c.get('model')}",
           f"      licence: {c.get('license')}   [{ok}]"]
    if c.get("source"):
        out.append(f"      source:  {c['source']}")
    return out


def build_listing_md(rec):
    """把 DistroKid／Gumroad 要填的欄位整理成一頁（複製貼上用）。"""
    listing = rec.get("listing") or {}
    ai = rec.get("ai_credits") or {}
    scope = ai.get("scope") or "none"
    album = rec.get("album") or ""
    artist = rec.get("artist") or ""
    n = len(rec.get("tracks") or [])
    price = listing.get("price_usd")
    price_txt = "名稱你的價錢 / pay-what-you-want" if not price else f"US${price:g}"
    tags = listing.get("tags") or []
    zip_name = f"download/{slugify(album) or 'release'}.zip"
    sw = rec.get("songwriter") or ""
    sw_cell = f"`{sw}`" if sw else "⚠️ 未填（DistroKid 原創曲**必填真名**）"
    dsp = rec.get("dsp") or {}
    dsp_genre = dsp.get("genre") or rec.get("genre") or ""
    dsp_sub = dsp.get("subgenre") or ""
    lines = [
        f"# 上架資料：{album} — {artist}",
        "",
        f"- 曲數 {n}｜全長 {mm.fmt_ts(rec.get('total_duration') or 0)}｜"
        f"母帶 {rec.get('master', {}).get('lufs'):g} LUFS / {rec.get('master', {}).get('tp'):g} dBTP",
        f"- 交付檔：`{zip_name}`（audio/ ＋ 封面 ＋ tracklist.csv ＋ credits.txt）",
        f"- 封面：`cover_3000x3000.jpg`（3000×3000、JPG、無文字）",
        "",
        "> 本頁是**給人複製貼上的值**；同一份資料的機器可讀版在 `release.json`。",
        "",
        "## 0. 兩個平台共用的值",
        "",
        "| 欄位 | 值 |",
        "|---|---|",
        f"| Album title | `{album}` |",
        f"| Artist | `{artist}` |",
        f"| Genre（文案／ID3 用） | `{rec.get('genre') or ''}` |",
        f"| Genre（**表單**用） | `{dsp_genre}`"
        + (f"　Secondary `{dsp_sub}`" if dsp_sub else "（沒有次類型）") + " |",
        f"| Explicit | {'Yes' if rec.get('explicit') else 'No'} |",
        f"| Songwriter（真名） | {sw_cell}"
        "　← **平台必填欄位**；給買家的 `credits.txt` 會寫成「"
        f"{CREDIT_ROLE}」＋「Music generated by AI」，不宣稱詞曲作者 |",
        f"| AI Credits | `{scope}`（{AI_CREDIT_LABELS.get(scope, scope)}）"
        + (f"，artist 名為{' AI 角色' if ai.get('persona') else '真人'}" if scope == "all_audio" else "")
        + " |",
        f"| Tags | {', '.join(f'`{t}`' for t in tags) or '（無）'} |",
        f"| Price（Gumroad） | {price_txt} |",
        "",
        "## A. DistroKid（無 API，手動上傳）",
        "",
        "1. 選商店（Services）→ 建議全選；Musician 方案送審即上線，Plus/Ultimate 才能排未來日",
        f"2. Artist/band name：`{artist}`（新名字會佔一個 artist slot）",
        f"3. Album title：`{album}`｜曲數 {n}（＝Album）",
        f"4. Primary genre：`{dsp_genre}`"
        + (f"　Secondary：`{dsp_sub}`" if dsp_sub else "（次類型可留空）")
        + f"　⚠️ **不要選 `{rec.get('genre') or 'Lo-fi'}`**——那不是發行商的選項"
          "（官方 help：選最接近的；鋼琴／ambient 選 Electronic，boom-bap beat 選 Hip-Hop/Rap）",
        f"5. 封面：上傳 `cover_3000x3000.jpg`",
        "6. Artist mapping：全新藝人 → 選「尚無 artist profile」",
        f"7. Songwriter：**原創曲** → 填真名 `{sw or '⚠️ 未填'}`",
        f"8. Explicit：{'Yes' if rec.get('explicit') else 'No'}（逐首）",
        f"9. **AI Credits**：選 `{scope}`"
        + ("，並聲明 artist 名是 AI 角色" if scope == "all_audio" else "") + "（2026 起強制）",
        "10. Language：`English`（**必須與曲名語言一致**，我們的曲名都是英文）",
        "11. 「這首單曲之前發行過嗎？」→ **No**（這是首發）",
        "12. ISRC：留空 → DistroKid 免費自動配發（**買家的 `tracklist.csv` 不含 ISRC**，"
        "那是發行商欄位）",
        "",
        "逐首 ISRC（配發後貼回 `release.json` 的 `tracks[].isrc`，下次重跑才會沿用——"
        "ISRC 跟著錄音一輩子，重發同一批要填舊的，否則平台會當成兩首歌）：",
        "",
        "| # | Title | ISRC |",
        "|---|---|---|",
    ] + [f"| {int(t.get('no') or i):02d} | {t.get('title') or ''} | {t.get('isrc') or ''} |"
         for i, t in enumerate(rec.get("tracks") or [], 1)] + [
        "",
        "> `tracklist.csv` 是**給買家**的曲目表（曲名／長度／味道／BPM／調性／格式／檔名／大小）。",
        "",
        "## B. Gumroad（有 API v2，第一版仍先手動）",
        "",
        f"1. Product type：**Digital product**（名稱＝`{album}`）",
        f"2. Price：{price_txt}" + ("（Gumroad 定價填 0 即可）" if not price else ""),
        f"3. Cover image：上傳 `cover_3000x3000.jpg`（另做縮圖需要 **方形 ≥600×600**）",
        f"4. Summary（一句話）：`{listing.get('short') or ''}`",
        "5. Description：貼下面的長版文案",
        f"6. Tags（上限 10）：{', '.join(f'`{t}`' for t in tags) or '（無）'}",
        "7. Category（Discover 需要；不要選 Other）＋ 打開 ratings",
        f"8. 內容（Content）：上傳 `{zip_name}`",
        "",
        "長版描述（純文字；`lofi release gumroad` 會直接送 **HTML 版**，"
        "商品頁的標題與清單才會真的排版）：",
        "",
        "```",
        (listing.get("long") or "").strip(),
        "```",
        "",
        "> Gumroad 先行條件：帳號要**完成 payout settings** 才能發佈（否則只存成 draft）。",
        "> Discover 要 USD$100 真實銷售＋風控審查＋至少 1 筆非退費銷售，所以第一筆要靠自己的通路帶。",
        "> 商品建立後再把 URL 記回 `catalog/releases.jsonl`（append-only，狀態變更用新事件）。",
        "",
    ]
    return "\n".join(lines)


def release_zip_path(dest, album):
    """交付 ZIP 的路徑：output/releases/<run>/download/<album>.zip。"""
    return Path(dest) / "download" / f"{slugify(album) or 'release'}.zip"


def make_release_zip(dst, members):
    """把 (來源檔, ZIP 內路徑) 清單打包成交付 ZIP。

    為什麼自己寫 tmp → os.replace 而不用 `io.atomic_write_*`：母帶動輒數百 MB，
    整包讀進記憶體會在 16GB、ComfyUI 同時跑的時候出事，所以**串流寫檔**、只保留原子替換。
    成員日期固定成 1980-01-01，讓同一份內容重跑得到位元組相同的 ZIP（可重現；也避免
    「檔案變了」的錯覺——每次上架都重建 ZIP，時間戳不同會讓 hash 每次都變）。
    """
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for src, arcname in members:
                src = Path(src)
                if not src.is_file():
                    continue
                info = zipfile.ZipInfo(str(arcname), date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                z.writestr(info, src.read_bytes())
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            tmp.unlink()
    return dst


def build_checklist(rec):
    """產出一頁上架清單（繁中）。"""
    artist = rec.get("artist", "")
    album = rec.get("album", "")
    n = len(rec.get("tracks") or [])
    ai = rec.get("ai_credits") or {}
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
        "### 各平台上架要填的共用值（詳見同資料夾的 `LISTING.md`）",
        "",
        f"- Genre：`{rec.get('genre') or ''}`　｜　Explicit：{'Yes' if rec.get('explicit') else 'No'}",
        f"- Songwriter（**真名**）：`{rec.get('songwriter') or '⚠️ 未填（DistroKid 原創曲必填真名）'}`",
        f"- AI Credits：`{ai.get('scope') or 'none'}`"
        + (f"（artist 為{'AI 角色' if ai.get('persona') else '真人'}）" if ai.get("scope") == "all_audio" else ""),
        f"- 交付檔：`download/{slugify(album) or 'release'}.zip`（audio/＋封面＋tracklist＋credits）",
        "",
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
        f"5. 逐首填曲名（見 `tracklist.csv`）、genre = `{rec.get('genre') or ''}`、"
        f"explicit = {'Yes' if rec.get('explicit') else 'No'}",
        f"6. Songwriter（**真名**）：`{rec.get('songwriter') or '⚠️ 未填'}`"
        "（原創曲必填；填藝名會被退）",
        f"7. **AI Credits**（2026 起強制）：`{ai.get('scope') or 'none'}`"
        + ("，並聲明 artist 名是 AI 角色" if ai.get("scope") == "all_audio" else "")
        + "（DistroKid 上傳流程會問；漏填會被平台事後標記）",
        f"8. Language = `English`；「之前發行過嗎」= No",
        "9. ISRC 留空 → 由發行商自動配發；配發後把號碼填回 `release.json` 的 `tracks[].isrc`"
        "（見 `LISTING.md` 的逐首 ISRC 表）",
        "9. 選發布日（建議留 2 週給 Spotify 編輯部 pitch 時間）",
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
        "## D. Gumroad（**有 API v2**，第一版先手動一次；見 DISTRIBUTION §5／§7 P3）",
        "",
        "> Gumroad 是唯一「可全自動」的商店（API v2 已能建立/更新商品、上傳檔案、設封面），",
        "> 但發佈前要先完成 payout settings，且 Discover 有門檻，所以先手動上架一次驗證欄位。",
        "",
        f"1. 新增 **Digital product**，名稱 `{album}`",
        "2. 定價："
        + ("「名稱你的價錢（pay-what-you-want）」" if not (rec.get("listing") or {}).get("price_usd")
           else f"US${(rec.get('listing') or {}).get('price_usd'):g}")
        + "（先累積聽眾與第一筆真實銷售）",
        "3. 封面 `cover_3000x3000.jpg`；縮圖另需**方形 ≥600×600**",
        "4. Summary／Description 貼 `LISTING.md` 的文案；Tags："
        + (", ".join((rec.get("listing") or {}).get("tags") or []) or "（無）"),
        "5. 選 category（不要選 Other）並打開 ratings（Discover 的必要條件）",
        f"6. 內容上傳 `download/{slugify(album) or 'release'}.zip`",
        "",
        "## E. 記回狀態",
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


def _select_tracks(rows, tokens):
    """依選擇器挑曲目：1-based 序號、`track_id`、或檔名片段（可混用）。"""
    picked = []
    for tok in tokens:
        t = (tok or "").strip()
        if not t:
            continue
        hit = None
        if t.isdigit() and 1 <= int(t) <= len(rows):
            hit = rows[int(t) - 1]
        if hit is None:
            for r in rows:
                if t == (r["sidecar"].get("track_id") or "") or t in r["audio"].stem:
                    hit = r
                    break
        if hit is None:
            sys.exit(f"--tracks 找不到「{t}」（可用 plan 印出的序號、track_id 或檔名片段）")
        if hit not in picked:
            picked.append(hit)
    if not picked:
        sys.exit("--tracks 沒有選到任何曲目")
    return picked


def load_run(style, run, limit=0, offset=0, title_overrides=None, tracks=None, min_score=None,
             blocking=None, isrc_overrides=None):
    """回傳 (來源目錄, [{...}], 被品檢排除的 [(row, 原因)])，曲目依檔名順序（即生成順序）。

    選曲優先序：
      1. `tracks`（明確指定序號／track_id／檔名片段）→ 忽略下面兩者
      2. `offset` + `limit`（取連續一段；一個 run 可能有 30+ 首，整張專輯太長）
      3. `min_score`（只留自動品檢分數 ≥ N 的；沒有品檢紀錄的一律排除）
    最後一律套 `blocking`（品檢 flag 黑名單）。
    曲名去重與編號在最後做，所以重跑結果穩定。
    """
    d = find_run_dir(style, run)
    audios = sorted(p for p in d.iterdir() if p.is_file() and cat.is_audio(p))
    if not audios:
        sys.exit(f"{cat.relpath(d)} 裡沒有可用的音檔")

    title_overrides = title_overrides or {}
    rows = []
    for a in audios:
        sc = cat.load_sidecar(a) or {}
        dur = (sc.get("gen") or {}).get("duration") or media.ffprobe_duration(a)
        prev = title_overrides.get(cat.relpath(a))
        rows.append({
            "audio": a, "sidecar": sc,
            "title": prev or track_title(a, sc),
            "mood": sc.get("mood", ""), "key": sc.get("key", ""), "bpm": sc.get("bpm", ""),
            "duration": dur, "style": sc.get("style", style),
            "score": (sc.get("auto_qc") or {}).get("score"),
            "flags": (sc.get("auto_qc") or {}).get("flags") or [],
            "verdict": (sc.get("auto_qc") or {}).get("verdict"),
            "master_src": find_lossless_master(a, sc, style, run),
            # ISRC 只有發行商在乎（不進買家看的 tracklist.csv），但**人工填過的要留住**：
            # 它跟著錄音一輩子，重跑被清掉就等於下次可能拿到重複的號碼。
            "isrc": (isrc_overrides or {}).get(cat.relpath(a)),
        })

    if tracks:
        rows = _select_tracks(rows, tracks)
    else:
        if offset:
            rows = rows[offset:]
        if limit:
            rows = rows[:limit]
        if min_score is not None:
            before = len(rows)
            rows = [r for r in rows if r["score"] is not None and r["score"] >= min_score]
            if not rows:
                sys.exit(f"--min-score {min_score} 之下沒有任何曲目（原本 {before} 首）")

    rows, dropped = flag_filter(rows, blocking)

    if not rows:
        sys.exit(f"{cat.relpath(d)} 裡沒有可用的音檔"
                 f"（檢查 --tracks/--limit/--offset/--min-score/--exclude-flags）"
                 + (f"\n  被品檢排除 {len(dropped)} 首；要硬用請加 --exclude-flags ''（空字串＝不過濾）"
                    if dropped else ""))
    titles = unique_titles([r["title"] for r in rows])
    for i, (r, t) in enumerate(zip(rows, titles), 1):
        r["title"] = t
        r["no"] = i
    return d, rows, dropped


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
                "isrc": t.get("isrc") or "",
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
    """回傳 (width, height)；量不到回 (None, None)。

    ⚠️ 只檢查 returncode 不夠：ffprobe 對壞掉的圖會回 **returncode 0** 並在 stdout 印
    `0x0`（錯誤只出現在 stderr），所以要**再檢查數值 > 0**，否則壞圖會被當成合法尺寸
    ——正是 AGENTS.md 說的「假通過」。
    """
    rc, out, _ = media.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)])
    if rc != 0 or "x" not in (out or ""):
        return None, None
    try:
        w, h = out.strip().splitlines()[0].split("x")[:2]
        w, h = int(w), int(h)
    except (ValueError, IndexError):
        return None, None
    return (w, h) if w > 0 and h > 0 else (None, None)


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

    為什麼：品牌 Moni on the Journey 的主體就是這隻貓，封面不能出現別的貓（`sc01/sc02/sc06`
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


def _run_ff(args):
    """執行 ffmpeg，套用執行緒上限與 niceness（與背景工作共存用）。

    `--threads` 是關鍵：不設時 ffmpeg 會吃滿所有核心，在生成中一併跑會明顯搶 CPU。
    """
    if _NICE:
        try:
            os.nice(_NICE)
        except (AttributeError, OSError):
            pass
    if _THREADS:
        args = args[:1] + ["-threads", str(_THREADS)] + args[1:]
    return media.run(args)


def master_track(src, dst, *, lufs, tp, fmt="wav", metadata=None):
    """兩段式 loudnorm 正規化（量測 → 套用）；量測失敗則退回單段動態模式。

    回傳實際套用的參數 dict（含 fallback 旗標），供 release.json 記錄來源。
    """
    common = f"loudnorm=I={lufs:g}:TP={tp:g}:LRA=11"
    rc, _, err = _run_ff(
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
    rc, _, err = _run_ff(cmd)
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
    rc, _, err = _run_ff(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-vf", f"crop={side}:{side}:{x}:{y},scale={size}:{size}:flags=lanczos",
         "-q:v", "2", str(dst)])
    if rc != 0:
        sys.exit(f"封面產生失敗：{err.strip()[:400]}")
    return dst


def pick_wide_cover(run):
    """該集的橫式封面（影片封面圖）：assets/visuals/<run>/*.png 取**解析度最高**的橫式那張。

    為什麼不是取最新：交付 ZIP 的高畫質橫式圖要從最好的來源縮下來，AI 放大過的
    `*_fix-4k` 應該勝出（同尺寸才比時間新舊）。
    """
    d = VISUALS_DIR / str(run or "")
    if not d.is_dir():
        return None
    best = None
    for p in d.glob("*.png"):
        w, h = probe_wh(p)
        if not (w and h) or w / h <= 1.2:
            continue
        key = (w * h, p.stat().st_mtime)
        if best is None or key > best[0]:
            best = (key, p)
    return best[1] if best else None


def set_dpi(path, dpi=72):
    """把 JPEG 的 DPI 標成 72（Gumroad 規格）。用 macOS 內建 sips；沒有就靜默跳過。"""
    try:
        return subprocess.run(["sips", "-s", "dpiWidth", str(dpi), "-s", "dpiHeight", str(dpi),
                               str(path)], capture_output=True).returncode == 0
    except OSError:
        return False


def make_gumroad_thumb(src, dst, size=1080):
    """Gumroad 縮圖：方形 ≥600×600（方圖直接縮小，不裁切）。"""
    return make_cover(src, dst, size)


def make_gumroad_cover(src, dst, size=(1920, 1080), wide_src=None):
    """Gumroad 封面：橫式 ≥1280×720。

    優先用橫式原圖（該集影片封面）；沒有的話用方圖＋同張圖放大模糊當背景補滿，
    避免把方圖硬裁成 16:9 而切掉主角。
    """
    w, h = size
    wide_ok = bool(wide_src) and bool(probe_wh(wide_src)[0])
    if wide_ok:
        extra = ["-i", str(wide_src), "-vf",
                 f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h}"]
    else:
        extra = ["-i", str(src), "-filter_complex",
                 f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
                 f"crop={w}:{h},gblur=sigma=24[bg];"
                 f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
                 f"[bg][fg]overlay=(W-w)/2:(H-h)/2"]
    rc, _, err = _run_ff(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *extra,
                          "-q:v", "2", str(dst)])
    if rc != 0:
        sys.exit(f"Gumroad 封面產生失敗：{err.strip()[:400]}")
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
    if _THREADS or _NICE:
        print(f"  資源：threads={_THREADS or '不限'}　nice={_NICE or 0}"
              f"（跑其他工作時可調低）")
    print(line)


def flag_filter(rows, blocking):
    """依「母帶修不掉」的 flag 黑名單過濾曲目。回傳 (保留, 被排除的 [(row, 原因)])。

    為什麼不用 score 當門檻：全庫實測 126 首只有 3 種 flag，其中 2 種（`hot_true_peak` 110 次、
    `loudness_out_of_range` 5 次）是 release 的 loudnorm 一定修得掉的；用「score 100」會把
    112 首沒問題的歌判成不可用。

    `blocking` 為空 = 不過濾。**沒有品檢紀錄的一律排除**（AGENTS：量測不到不可假通過），
    但要靠 `blocking` 非空才會做——否則沒跑過 qc 的 run 會直接全滅。
    """
    if not blocking:
        return list(rows), []
    block = set(blocking)
    kept, dropped = [], []
    for r in rows:
        if r.get("verdict") is None:
            dropped.append((r, "沒有品檢紀錄"))
            continue
        hit = sorted(set(r.get("flags") or []) & block)
        dropped.append((r, "、".join(hit))) if hit else kept.append(r)
    return kept, dropped


def _parse_tracks(s):
    """`--tracks` 字串 → 選擇器清單（支援逗號與空白分隔）。"""
    return [t.strip() for t in re.split(r"[,\s]+", s or "") if t.strip()]


def _parse_flags(s):
    """`--exclude-flags` 字串 → flag 集合（空字串＝不過濾）。"""
    return tuple(f.strip() for f in re.split(r"[,\s]+", s or "") if f.strip())


def _parse_tags(s):
    """`--tags` 字串 → tag 清單。**只切逗號**，因為 tag 可以含空白（例：study music）。"""
    return [t.strip() for t in (s or "").split(",") if t.strip()]


def report_excluded(dropped):
    """把被品檢排除的曲目理由印出來（分類統計 + 一例）。"""
    if not dropped:
        return
    reasons = {}
    for _r, why in dropped:
        reasons[why] = reasons.get(why, 0) + 1
    summary = "、".join(f"{w} ×{n}" for w, n in sorted(reasons.items(), key=lambda x: -x[1]))
    print(f"  ⚠️  品檢排除 {len(dropped)} 首（{summary}）"
          f"；硬要收請加 --exclude-flags ''")
    print(f"     例：{dropped[0][0]['audio'].stem}（{dropped[0][1]}）")


def find_orphans(audio_dir, expected, exts=("*.wav", "*.flac")):
    """回傳 `audio/` 裡**不屬於這張專輯**的檔（改過曲名、或前次中斷留下的殘留）。

    為什麼需要：`CHECKLIST.md` 叫使用者「上傳 audio/ 全部」，多一個檔就是多傳一首重複的歌。
    為什麼不信「上一次 release.json 記錄的路徑」：改過曲名、或前一次中斷，都會留下對不到的檔
    （踩過：15 個檔裡 3 個是孤兒）。
    """
    want = {str(n) for n in expected}
    return [f for pat in exts for f in sorted(Path(audio_dir).glob(pat))
            if f.name not in want]


def cmd_plan(args):
    # 與 package 看同一份既有 release.json，否則「預覽」會跟「實際產出」不一樣
    prior = load_prior_tracks(RELEASES_DIR / args.run / "release.json")
    d, rows, dropped = load_run(args.style, args.run, limit=args.limit, offset=args.offset,
                                title_overrides={k: v["title"] for k, v in prior.items()},
                                isrc_overrides={k: v["isrc"] for k, v in prior.items() if v.get("isrc")},
                                tracks=_parse_tracks(args.tracks), min_score=args.min_score,
                                blocking=_parse_flags(args.exclude_flags))
    cover = Path(args.cover) if args.cover else pick_cover(args.visual_run)
    print(f"來源：{cat.relpath(d)}（取 {len(rows)} 首"
          + (f"，offset {args.offset}" if args.offset else "")
          + (f"，limit {args.limit}" if args.limit else "") + "）")
    report_excluded(dropped)
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
    d, rows, dropped = load_run(args.style, args.run, limit=args.limit, offset=args.offset,
                                title_overrides={k: v["title"] for k, v in prior.items()},
                                isrc_overrides={k: v["isrc"] for k, v in prior.items() if v.get("isrc")},
                                tracks=_parse_tracks(args.tracks), min_score=args.min_score,
                                blocking=_parse_flags(args.exclude_flags))
    artist = args.artist or config.RELEASE_ARTIST
    album = args.album or mm.prettify(rows[0]["style"] or args.run)

    audio_dir = dest / "audio"
    cover_src = Path(args.cover) if args.cover else pick_cover(args.visual_run)
    cover_dst = dest / "cover_3000x3000.jpg"

    meta_master = {"lufs": config.MASTER_LUFS if args.lufs is None else args.lufs,
                   "tp": config.MASTER_TP if args.tp is None else args.tp}

    total_dur = sum(r["duration"] or 0 for r in rows)
    est_gb = max(total_dur * 0.29 / 1000 * 3, 2.0)   # 母帶 + 交付 ZIP + 餘裕
    media.require_free(RELEASES_DIR, est_gb, "外接碟 output/releases")

    rec = {
        "release_version": SCHEMA_VERSION, "run": args.run, "style": rows[0]["style"],
        "album": album, "artist": artist, "label": args.label or config.RELEASE_LABEL,
        "genre": args.genre, "format": args.format,
        # 發行商表單用的 genre（Lo-fi 不是 DistroKid 的選項，見 config 的說明）
        "dsp": {"genre": args.dsp_genre, "subgenre": args.dsp_subgenre},
        "explicit": bool(args.explicit or config.RELEASE_EXPLICIT),
        "songwriter": args.songwriter or config.RELEASE_SONGWRITER,
        "ai_credits": {"scope": args.ai_credits, "persona": config.RELEASE_AI_PERSONA},
        "master": meta_master,
        "master_source": describe_master_source(rows),
        "cover_source": cat.relpath(cover_src) if cover_src else None,
        "dir": _rel(dest),
        "created_at": cat.now_iso(),
        "credits": [],
        "tracks": [],
        "listing": {},
        "excluded": [],
        "notes": [],
    }

    print_header(rec, len(rows), dest, cover_src, cover_dst)
    license_gate(rows, args.allow_unknown_license)
    report_excluded(dropped)
    rec["excluded"] = [{"track": r["audio"].stem, "reason": why} for r, why in dropped]

    if not rows[0]["sidecar"]:
        rec["notes"].append("這批 sidecar 為空 → 曲名/調性可能不準，上架前請人工確認")

    # 交付母帶的內嵌標籤：credits 不能只活在一個 .txt 裡——**標籤跟著檔案走**，
    # 使用者只帶走音檔（丟掉 README）時還是看得到 AI 揭露與版權。
    # 注意：改標籤要 `--force`——既有檔會被「已存在，略過」跳過，標籤不會更新。
    model_credits = collect_credits(rows)
    year = str(rec.get("created_at") or "")[:4]
    ai_note = "AI-generated audio"
    if model_credits:
        ai_note += f" ({model_credits[0]['model']}, {model_credits[0]['license']})"
    if rec.get("songwriter"):
        ai_note += f". Produced, edited and mastered by {rec['songwriter']}."
    # 註：不要放 `album_artist`——實測 ffmpeg 的 WAV muxer 會把它丟掉（TPE2 不寫），
    # 留著只會讓人以為有寫進去。`artist` 已足夠（播放器就是讀它）。
    common_tags = {
        "artist": artist, "album": album,
        "genre": rec["genre"] or "", "date": year,
        "copyright": f"(C) {year} {artist}".strip() if year else "",
        "comment": f"{ai_note} All audio is AI-generated; see credits.txt.",
    }

    # 1) 母帶
    audio_dir.mkdir(parents=True, exist_ok=True)
    for r in rows:
        stem = f"{r['no']:02d}_{slugify(r['title']) or ('track' + str(r['no']))}"
        out = audio_dir / f"{stem}.{rec['format']}"
        source = r["master_src"] or r["audio"]
        r["audio_out"] = out
        if out.exists() and not args.force:
            print(f"[{r['no']:>2}] 已存在，略過：{out.name}")
            # 與新算的母帶保持同樣的欄位形狀（否則 release.json 每筆長得不一样）
            r["master"] = {"mode": "reuse", "lufs": rec["master"]["lufs"],
                           "tp": rec["master"]["tp"], "format": rec["format"],
                           "lossless": bool(r["master_src"]), "source": _rel(source),
                           "measured_in_lufs": None}
            continue
        print(f"[{r['no']:>2}] {r['title']} → {out.name}", flush=True)
        r["master"] = master_track(
            source, out, lufs=rec["master"]["lufs"], tp=rec["master"]["tp"], fmt=rec["format"],
            metadata={**common_tags, "title": r["title"],
                      "track": f"{r['no']}/{len(rows)}"})
        r["master"]["lossless"] = bool(r["master_src"])
        r["master"]["source"] = _rel(source)

    # 1b) 對帳：audio/ 裡不屬於這張專輯的檔一律刪掉（改過名、或前次中斷的殘留）。
    #      理由見 find_orphans()。
    expected = {r["audio_out"].name for r in rows}
    orphans = find_orphans(audio_dir, expected)
    for f in orphans:
        f.unlink()
        print(f"   ↳ 清掉孤兒檔 {f.name}")
    if orphans:
        print(f"   （共清掉 {len(orphans)} 個不屬於本專輯的檔）")
        r["audio_out"] = out

    # 2) 封面
    if cover_src:
        dest.mkdir(parents=True, exist_ok=True)
        make_cover(cover_src, cover_dst)
        print(f"封面 → {cover_dst.name}")
        # Gumroad 商品頁要兩個尺寸：橫式封面（≥1280×720）與方形縮圖（≥600×600），並標 72 DPI
        _wide = pick_wide_cover(rec.get("run") or "")
        _gc, _gt = dest / "gumroad_cover_1920x1080.jpg", dest / "gumroad_thumb_1080x1080.jpg"
        make_gumroad_cover(cover_src, _gc, wide_src=_wide)
        make_gumroad_thumb(cover_src, _gt)
        # 交付 ZIP 的高畫質橫式圖：同一套設計、更高解析度（買家拿到的和商店頁看到的一致）
        _art = dest / f"artwork_{ARTWORK_SIZE[0]}x{ARTWORK_SIZE[1]}.jpg"
        make_gumroad_cover(cover_src, _art, size=ARTWORK_SIZE, wide_src=_wide)
        for _p in (cover_dst, _gc, _gt, _art):
            set_dpi(_p)
        print(f"       → {_gc.name}（Gumroad 封面，橫式" +
              ("，用該集影片封面）" if _wide else "，方圖＋模糊背景）"))
        print(f"       → {_gt.name}（Gumroad 縮圖，方形）")
        print(f"       → {_art.name}（交付 ZIP 用高畫質橫式，同一套設計）")

    # 3) 記錄與清單
    # 交付母帶的實際格式（量測，不猜）：credits.txt 要寫「24-bit / 48 kHz」。
    first_out = rows[0].get("audio_out")
    if first_out and Path(first_out).exists():
        sr, bits = probe_audio_format(first_out)
        if sr:
            rec["master"]["sample_rate"] = sr
        if bits:
            rec["master"]["bits"] = bits
    rec["credits"] = model_credits
    rec["tracks"] = [{
        "no": r["no"], "title": r["title"], "mood": r["mood"], "key": r["key"], "bpm": r["bpm"],
        "duration": round(r["duration"], 1) if r["duration"] else None,
        "file": cat.relpath(r["audio"]), "audio": _rel(r["audio_out"]),
        "lossless": bool(r["master_src"]),
        # 人工填過的 ISRC 要沿用（load_prior_tracks 讀回來）；沒填＝留空，由發行商配發
        "isrc": r.get("isrc") or None, "master": r.get("master"),
    } for r in rows]
    rec["total_duration"] = round(sum(r["duration"] or 0 for r in rows), 1)
    rec["dir"] = _rel(dest)
    rec["listing"] = build_listing(rec, tags=_parse_tags(args.tags),
                                   price_usd=args.price)

    dest.mkdir(parents=True, exist_ok=True)
    # 買家視角的曲目表：帶上檔案大小（買家想知道要下載多少）與實測格式
    audio_fmt = (f"WAV {rec['master'].get('bits')}-bit / "
                 f"{rec['master'].get('sample_rate', 0) / 1000:g} kHz"
                 if rec["master"].get("bits") and rec["master"].get("sample_rate")
                 else (rec.get("format") or "wav").upper())
    csv_rows = []
    for t in rec["tracks"]:
        p = ROOT / t["audio"] if t.get("audio") else None
        csv_rows.append({**t, "artist": t.get("artist") or artist,
                         "explicit": rec.get("explicit"),
                         "size_mb": round(p.stat().st_size / 1e6, 1) if p and p.exists() else ""})
    io.atomic_write_json(dest / "release.json", rec)
    io.atomic_write_text(dest / "tracklist.csv", build_tracklist_csv(
        csv_rows, album=album, artist=artist,
        year=str(rec.get("created_at") or "")[:4], audio_format=audio_fmt))
    io.atomic_write_text(dest / "CHECKLIST.md", build_checklist(rec))
    io.atomic_write_text(dest / "LISTING.md", build_listing_md(rec))
    io.atomic_write_text(dest / "credits.txt", build_credits_txt(rec))

    # 4) 交付 ZIP（Gumroad／Bandcamp 的「內容」；重跑一律重建，內容決定檔案）
    zip_path = None
    if not args.no_zip:
        members = [(r["audio_out"], f"audio/{r['audio_out'].name}") for r in rows]
        # 買家拿到的要和商店頁看到的一致：商店的 16:9 封面／方形縮圖是「同一套圖」的
        # 小尺寸版，ZIP 就給同一套圖的高畫質版（方形 3000² ＋ 橫式 2560×1440）。
        members += [(dest / "cover_3000x3000.jpg", "cover_3000x3000.jpg"),
                    (dest / f"artwork_{ARTWORK_SIZE[0]}x{ARTWORK_SIZE[1]}.jpg",
                     f"artwork_{ARTWORK_SIZE[0]}x{ARTWORK_SIZE[1]}.jpg"),
                    (dest / "tracklist.csv", "tracklist.csv"),
                    (dest / "credits.txt", "credits.txt")]
        zip_path = make_release_zip(release_zip_path(dest, album), members)
        print(f"交付包 → {zip_path.name}（{zip_path.stat().st_size / 1e6:.0f} MB）")

    append_release_event(rec)

    print()
    print(f"✅ {_rel(dest)}")
    print(f"   曲數 {len(rows)}｜總長 {mm.fmt_ts(rec['total_duration'])}｜母帶 {rec['master']['lufs']:g} LUFS")
    print(f"   → release.json / tracklist.csv / LISTING.md / credits.txt / CHECKLIST.md"
          + (" / download/*.zip" if zip_path else ""))
    print("   接下來：照 LISTING.md（DistroKid／Gumroad 逐欄值）與 CHECKLIST.md 走"
          "——DSP 沒有 API，要手動上傳。）")
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
        p.add_argument("--tracks", default="",
                       help="只取指定曲目（序號／track_id／檔名片段，逗號分隔）。優先用 plan 看序號")
        p.add_argument("--min-score", type=int, default=None, dest="min_score",
                       help="只留自動品檢分數 ≥ N 的（沒有品檢紀錄的一律排除）")
        p.add_argument("--exclude-flags", default=",".join(config.QC_BLOCKING_FLAGS),
                       dest="exclude_flags",
                       help="品檢擋掉的 flag（母帶修不掉的才擋；空字串＝不過濾）"
                            "。預設見 config.QC_BLOCKING_FLAGS 與 docs/RUNBOOK.md §8.2")
        p.add_argument("--threads", type=int, default=_THREADS,
                       help="ffmpeg 執行緒上限（0=不限制）。跑其他工作時建議 2–4；也可設 LOFI_THREADS")
        p.add_argument("--nice", type=int, default=_NICE, dest="nice",
                       help="niceness（越高越讓 CPU 給別人）。也可設 LOFI_NICE")

    p1 = sub.add_parser("plan", help="只看會產出什麼（不寫檔）")
    add_common(p1)
    p1.set_defaults(func=cmd_plan)

    p2 = sub.add_parser("package", help="真的產出上架包")
    add_common(p2)
    p2.add_argument("--artist", default="", help="artist 名義（預設 config.RELEASE_ARTIST / LOFI_RELEASE_ARTIST）")
    p2.add_argument("--album", default="", help="專輯名（預設用風格名）")
    p2.add_argument("--label", default="", help="label（預設 config.RELEASE_LABEL）")
    p2.add_argument("--genre", default=config.RELEASE_GENRE,
                    help="描述文案用的 genre（預設 Lo-fi；ID3 與商品文案用）")
    p2.add_argument("--dsp-genre", default=config.RELEASE_DSP_GENRE, dest="dsp_genre",
                    help="發行商表單的 primary genre（預設 Electronic；Lo-fi 不在 DistroKid 清單）")
    p2.add_argument("--dsp-subgenre", default=config.RELEASE_DSP_SUBGENRE, dest="dsp_subgenre",
                    help="發行商表單的 secondary genre（可留空）")
    p2.add_argument("--songwriter", default="",
                    help="作詞作曲者**真名**（DistroKid 原創曲必填；預設 config.RELEASE_SONGWRITER）")
    p2.add_argument("--ai-credits", default=config.RELEASE_AI_CREDITS, choices=list(AI_CREDIT_SCOPES),
                    dest="ai_credits",
                    help="AI 揭露範圍（DistroKid AI Credits；本產線為全生成 → 預設 all_audio）")
    p2.add_argument("--explicit", action="store_true",
                    help="標記 explicit 歌詞（本產線為純器樂，預設否）")
    p2.add_argument("--price", type=float, default=None,
                    help="Gumroad 定價 USD（0＝名稱你的價錢；預設 config.RELEASE_PRICE_USD）")
    p2.add_argument("--tags", default="",
                    help="Gumroad tags（逗號分隔，可含空白）；預設由 genre/style/專輯名推得")
    p2.add_argument("--no-zip", action="store_true", dest="no_zip",
                    help="不產 download/<album>.zip 交付包")
    p2.add_argument("--format", default="wav", choices=["wav", "flac"], help="母帶格式（預設 wav）")
    p2.add_argument("--lufs", type=float, default=None, help="目標響度（預設 config.MASTER_LUFS）")
    p2.add_argument("--tp", type=float, default=None, help="true peak 上限 dBTP（預設 config.MASTER_TP）")
    p2.add_argument("--force", action="store_true", help="已存在的母帶也重新處理")
    p2.add_argument("--allow-unknown-license", action="store_true",
                    help="允許授權未登錄的模型（商用前請先確認，見 docs/LICENSING.md）")
    p2.set_defaults(func=cmd_package)

    # Gumroad（唯一有 API 的商店）由獨立模組處理，掛在同一個 `lofi release` 子命令底下
    from lofi import gumroad
    gumroad.add_parser(sub)

    args = ap.parse_args()
    global _THREADS, _NICE
    _THREADS = getattr(args, "threads", _THREADS)
    _NICE = getattr(args, "nice", _NICE)
    args.func(args)


if __name__ == "__main__":
    main()
