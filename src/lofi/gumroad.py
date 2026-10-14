#!/usr/bin/env python3
"""gumroad.py — 把上架包送到 Gumroad（API v2）。

定位：Gumroad 是唯一**有 API、可全自動上架**的商店（DSP 商都沒有公開 API），
所以這條路走得通。它消費的是 `lofi release package` 的產物：

    output/releases/<run>/release.json   ← 標題/價格/tags/描述（＝ LISTING.md 的機器版）
    output/releases/<run>/download/*.zip ← 交付檔
    output/releases/<run>/cover_*.jpg    ← 封面（圖片一律建立後再補）

流程：POST /v2/products 建 draft → 上傳檔案 → 補封面 → （可選）enable 發佈
      → 商品 id/URL 記回 catalog/releases.jsonl（append-only）。

**踩過的坑（社群實測 + 官方原始碼）**：
  · Gumroad **幾乎什麼都回 HTTP 200**，成敗在 body 的 `success` 欄位 → 一律讀 success，
    看狀態碼會把失敗當成功。
  · `POST /v2/products` 建立的一定是 **draft**，發佈要另外 PUT `.../enable`。
  · 封面／縮圖**只能建立後再補**（`/covers`、`/thumbnail`）。
  · `GET /v2/products` 一頁只回 10 筆，其餘在 `next_page_url` → 判斷「是否已上架」必須翻頁，
    否則會重複建立商品。
  · 沒有 payout settings 或 email 未確認時，發佈會被擋並回一個 `warning`（照實印出，不假裝成功）。

憑證：`LOFI_GUMROAD_TOKEN` 或 `.secrets/gumroad_token.txt`（`.secrets/` 不進版控）。
"""
import argparse
import json
import mimetypes
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from lofi import catalog as cat
from lofi import io
from lofi.paths import CATALOG_DIR, RELEASES_DIR, ROOT

BASE = "https://api.gumroad.com/v2"
RELEASES_JSONL = CATALOG_DIR / "releases.jsonl"
SCHEMA_VERSION = 1
TOKEN_ENV = "LOFI_GUMROAD_TOKEN"
TOKEN_FILE = ROOT / ".secrets" / "gumroad_token.txt"
MAX_PAGES = 25                      # 防止壞掉的 cursor 無限迴圈
TIMEOUT = 60


class GumroadError(RuntimeError):
    """Gumroad 以 HTTP 200 + success=false 表示失敗；這裡把它變成例外。"""


# ── 憑證 ──────────────────────────────────────────────────────────────

def load_token():
    """讀 access token：環境變數優先，其次 .secrets/gumroad_token.txt。"""
    import os
    tok = (os.environ.get(TOKEN_ENV) or "").strip()
    if tok:
        return tok
    if TOKEN_FILE.exists():
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    sys.exit(f"找不到 Gumroad access token。\n"
             f"  · 設 {TOKEN_ENV}=...，或\n"
             f"  · 存到 {TOKEN_FILE.relative_to(ROOT)}（chmod 600；.secrets/ 不進版控）")


# ── HTTP ─────────────────────────────────────────────────────────────

def _encode(params):
    """form-encoded。

    接受 dict（key 以 `[]` 結尾的可重複 → doseq）或 **list of pairs**。

    ⚠️ 為什麼要支援 list of pairs：**順序會影響 Rails 的解析**。
    `parts[][part_number]` 與 `parts[][etag]` 必須**成對交錯**（p1,e1,p2,e2…），
    Rack 才會把兩者併進同一個 hash；若把 4 個 part_number 全部寫完才寫 4 個 etag，
    會被拆成 8 個殘缺項目 → `PartNumber must be >= 1`（踩過）。
    帶索引的寫法（parts[0][part_number]）也不行：Rack 會變成以 "0","1" 為 key 的 **hash**，
    伺服器拿 key 當 part number → 0 → 同一個錯。
    """
    items = list(params) if isinstance(params, (list, tuple)) else list((params or {}).items())
    return urllib.parse.urlencode(items, doseq=True).encode("utf-8")


def raw(method, path, params=None, token=None, timeout=TIMEOUT):
    """打一次 API，回傳解析後的 body（**不做 success 檢查**，供探索/除錯用）。"""
    token = token or load_token()
    url = BASE + path if path.startswith("/") else path
    data = None
    headers = {"Accept": "application/json", "User-Agent": "lofi-studio/1.0"}
    if method == "GET":
        q = _encode(params or {}).decode()
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode({"access_token": token})
        if q:
            url += "&" + q
    else:
        pairs = ([("access_token", token)] + list(params)
                 if isinstance(params, (list, tuple))
                 else [("access_token", token)] + list((params or {}).items()))
        data = _encode(pairs)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:               # 仍可能是 JSON（Gumroad 幾乎都回 200）
        text = e.read().decode("utf-8", "replace")
        if not text.strip():
            raise GumroadError(f"{method} {path} → HTTP {e.code}") from None
    try:
        return json.loads(text)
    except ValueError:
        raise GumroadError(f"{method} {path} → 非 JSON 回應：{text[:300]}") from None


def api(method, path, params=None, token=None):
    """同 raw()，但**檢查 success**（Gumroad 的成敗在這個欄位，不是狀態碼）。"""
    body = raw(method, path, params, token)
    if not body.get("success"):
        raise GumroadError(f"{method} {path} → {body.get('message') or body}")
    return body


def put_part(url, blob, timeout=1800):
    """把一個 part PUT 到 presigned S3 URL，回傳 ETag（完成 multipart 時要回報）。

    這不是 Gumroad API（是 S3），所以**不帶 token**。
    """
    req = urllib.request.Request(url, data=blob, method="PUT")
    req.add_header("Content-Length", str(len(blob)))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (r.headers.get("ETag") or "").strip('"')
    except urllib.error.HTTPError as e:
        raise GumroadError(f"S3 part 上傳失敗 HTTP {e.code}：{e.read()[:200]!r}") from None


# ── 檔案上傳（presign → S3 parts → complete）────────────────────────────

def presign(filename, file_size, content_type=None, token=None):
    """POST /files/presign → {upload_id, key, file_url, parts[{part_number, presigned_url}]}。"""
    ct = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return api("POST", "/files/presign",
               {"filename": filename, "content_type": ct, "file_size": int(file_size)}, token)


def complete_upload(upload_id, key, parts, token=None):
    """POST /files/complete。parts=[{part_number, etag}]。

    `parts` 用**成對交錯**的 list of pairs（見 `_encode` 的說明）——這是唯一能讓
    Rack 併成正確陣列、又不會被拆成殘缺項目的寫法（單一 part 時各種寫法都會過，
    所以這個坑只在多 part 才踩到）。
    """
    pairs = [("upload_id", upload_id), ("key", key)]
    for p in parts:
        pairs.append(("parts[][part_number]", p["part_number"]))
        pairs.append(("parts[][etag]", p["etag"]))
    try:
        return api("POST", "/files/complete", pairs, token)
    except GumroadError as first:
        try:                                            # 後備：整個 parts 當 JSON 字串
            return api("POST", "/files/complete",
                       {"upload_id": upload_id, "key": key,
                        "parts": json.dumps(parts)}, token)
        except GumroadError:
            raise first


UPLOAD_CACHE = ROOT / "logs" / ".gumroad-uploads.json"    # logs/ 不進版控
PENDING_CACHE = ROOT / "logs" / ".gumroad-uploads-pending.json"


def upload_cache_key(path):
    """檔案指紋＝**內容的 sha1**（不只看 mtime）。

    `release package` 每次都會重建交付 ZIP；成員與日期固定，所以內容通常一模一樣、
    只有 mtime 變。若用 mtime 當指紋，**只改文案／描述也會重傳 390 MB**（實際踩過），
    所以改成看內容：內容沒變就不重傳。
    """
    p = Path(path)
    return f"{p.name}:{cat.sha1_file(p)}"


def _cache_get(path):
    if not UPLOAD_CACHE.exists():
        return ""
    try:
        return (json.loads(UPLOAD_CACHE.read_text(encoding="utf-8"))
                .get(upload_cache_key(path)) or "")
    except (json.JSONDecodeError, OSError):
        return ""


def _load(path, default):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else default
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return default


def _cache_put(path, url):
    data = _load(UPLOAD_CACHE, {})
    data[upload_cache_key(path)] = url
    io.atomic_write_json(UPLOAD_CACHE, data)


def _pending_get(path):
    """已上傳 part 但還沒 complete 的紀錄（續傳用；避免重試時白傳數百 MB）。"""
    return _load(PENDING_CACHE, {}).get(upload_cache_key(path)) or {}


def _pending_put(path, pre, parts):
    data = _load(PENDING_CACHE, {})
    data[upload_cache_key(path)] = {
        "upload_id": pre["upload_id"], "key": pre["key"],
        "file_url": pre.get("file_url"), "parts": parts}
    io.atomic_write_json(PENDING_CACHE, data)


def _pending_del(path):
    data = _load(PENDING_CACHE, {})
    if data.pop(upload_cache_key(path), None) is not None:
        io.atomic_write_json(PENDING_CACHE, data)


def upload_file(path, token=None, label="", use_cache=True):
    """把一個檔案上傳給 Gumroad，回傳可附到商品的 https URL。

    流程：presign（決定 part 數）→ 逐 part PUT（收 ETag）→ complete。
    大檔用**串流分塊**讀，不要把整個檔案餵進記憶體（母帶 zip 可達數百 MB）。
    同一份檔案（name+size+mtime）上傳過就沿用快取，重跑不會白傳一次。
    """
    path = Path(path)
    size = path.stat().st_size
    pending = _pending_get(path) if use_cache else {}
    if use_cache and not pending:
        cached = _cache_get(path)
        if cached:
            print(f"     （已有上傳快取，跳過傳輸）{path.name}")
            return cached

    if pending:                                          # parts 已傳過 → 只重試 complete
        pre, uploaded = pending, pending["parts"]
        print(f"     （續傳：parts 已上傳，略過傳輸）{path.name}")
    else:
        pre = presign(path.name, size, token=token)
        parts = pre.get("parts") or []
        if not parts:
            raise GumroadError(f"presign 沒有回 parts：{pre}")
        n = len(parts)
        part_size = max(-(-size // n), 5 * 1024 * 1024)  # ceil；不小於 S3 的 5MB 下限
        uploaded = []
        with open(path, "rb") as f:
            for i, p in enumerate(parts, 1):
                blob = f.read(part_size)
                etag = put_part(p["presigned_url"], blob)
                uploaded.append({"part_number": p.get("part_number") or i, "etag": etag})
                if label:
                    print(f"     [{'▉' * i}{'·' * (n - i)}] {label} {i}/{n}"
                          f"（{len(blob) / 1e6:.0f} MB）", flush=True)
        if use_cache:
            _pending_put(path, pre, uploaded)             # 先落地，complete 失敗也不用重傳

    body = complete_upload(pre["upload_id"], pre["key"], uploaded, token)
    url = body.get("file_url") or body.get("url") or pre.get("file_url")
    if not url:
        raise GumroadError(f"complete 沒有回檔案 URL：{body}")
    _pending_del(path)
    _cache_put(path, url)
    return url


def attach_file(product_id, file_url, filename="", token=None):
    """把已上傳的檔案附到商品（files 是**整份替換**，不是 append）。"""
    params = {"files[][url]": [file_url]}
    if filename:
        params["files[][name]"] = [filename]
    return api("PUT", f"/products/{product_id}", params, token).get("product") or {}


def attach_cover(product_id, image_url, token=None):
    """封面只能建立後再補：POST /products/:id/covers。"""
    return api("POST", f"/products/{product_id}/covers", {"url": image_url}, token)


def attach_thumbnail(product_id, signed_blob_id, token=None):
    """縮圖：POST /products/:id/thumbnail。

    ⚠️ 只吃 `signed_blob_id`（Rails ActiveStorage 的簽章 id），而公開 API 的
    presign 流程**不會**產生它（實測：加 `purpose`/`type` 等參數都拿不到）。
    所以縮圖目前**只能**在 dashboard 設定，別依賴這支。留著是為了日後 API 補上時可用。
    """
    return api("POST", f"/products/{product_id}/thumbnail",
               {"signed_blob_id": signed_blob_id}, token)


# ── 商品查詢（一定翻頁）───────────────────────────────────────────────

def list_products(token=None, max_pages=MAX_PAGES):
    """列出全部商品（自動翻 next_page_url）。只抓一頁會讓「已存在」的判斷失準。"""
    token = token or load_token()
    out, url, page = [], BASE + "/products", 0
    while url and page < max_pages:
        body = api("GET", url, token=token)
        out += body.get("products") or []
        nxt = body.get("next_page_url")
        if nxt and not nxt.startswith("http"):
            nxt = BASE.rsplit("/v2", 1)[0] + nxt
        url, page = nxt, page + 1
    return out


def find_product(permalink="", name="", token=None, products=None):
    """依 permalink（優先）或 name 找既有商品；找不到回 None。"""
    perma = (permalink or "").strip().lower()
    title = (name or "").strip().lower()
    for p in (products if products is not None else list_products(token=token)):
        if perma and str(p.get("custom_permalink") or "").strip().lower() == perma:
            return p
        if title and str(p.get("name") or "").strip().lower() == title:
            return p
    return None


# ── payload（純函式，可單元測試）──────────────────────────────────────

def permalink_for(album):
    """專輯名 → 商品網址 slug。"""
    s = re.sub(r"[^a-z0-9]+", "-", str(album or "").lower()).strip("-")
    return s[:60].strip("-")


def product_name(rec):
    """商品標題＝專輯名（含藝人，讓 Gumroad 搜尋用整串比對抓得到）。"""
    album, artist = rec.get("album") or rec.get("run") or "", rec.get("artist") or ""
    return f"{album} — {artist}" if artist else album


def price_cents(rec, override=None):
    """回傳 (cents, currency)。listing.price_usd 為 0 → 0（＝Gumroad 的 pay-what-you-want）。"""
    usd = rec.get("listing", {}).get("price_usd") if override is None else override
    try:
        return int(round(float(usd or 0) * 100)), "usd"
    except (TypeError, ValueError):
        return 0, "usd"


def description_for(rec, fmt="html"):
    """商品描述。

    **預設送 HTML**（`listing.html`）：Gumroad 的商品頁吃 HTML，標題與清單才會真的排版；
    純文字只會變成一堆換行。`fmt="text"` 可強制純文字（萬一它的清洗規則擋掉標籤）。
    """
    listing = rec.get("listing") or {}
    if fmt == "html" and listing.get("html"):
        return listing["html"]
    if listing.get("long"):
        return listing["long"]
    return f"{rec.get('album')} — {rec.get('artist')}"


def tags_for(rec, override=None):
    """Gumroad 的 tags（上限 10、每個 2–20 字，規則見 release.clean_tags）。"""
    t = override if override is not None else (rec.get("listing") or {}).get("tags") or []
    return list(t)[:10]


def build_params(rec, *, price=None, permalink="", tags=None, publish=False, desc_format="html"):
    """把 release.json 轉成 POST /v2/products 的參數（純函式）。

    只帶「建立時能帶、且我們有值」的欄位；封面與檔案一律建立後再補（API 限制）。
    """
    cents, currency = price_cents(rec, price)
    params = {
        "native_type": "digital",
        "name": product_name(rec),
        "price": cents,
        "price_currency_type": currency,
        "description": description_for(rec, desc_format),
        "custom_permalink": permalink or permalink_for(rec.get("album")),
        "custom_summary": (rec.get("listing") or {}).get("short") or "",
        "published": "false" if not publish else "true",   # 一律先 draft，發佈用 enable
    }
    t = tags_for(rec, tags)
    if t:
        params["tags[]"] = t
    return {k: v for k, v in params.items() if v not in ("", None)}


# ── 商品寫入 ─────────────────────────────────────────────────────────

def create_product(params, token=None):
    return api("POST", "/products", params, token).get("product") or {}


def update_product(product_id, params, token=None):
    return api("PUT", f"/products/{product_id}", params, token).get("product") or {}


def enable_product(product_id, token=None):
    return api("PUT", f"/products/{product_id}/enable", {}, token)


def disable_product(product_id, token=None):
    return api("PUT", f"/products/{product_id}/disable", {}, token)


def delete_product(product_id, token=None):
    return api("DELETE", f"/products/{product_id}", {}, token)


def product_url(rec, token=None):
    """拿一個既存商品的公開網址（給記錄用）。"""
    p = find_product(permalink=permalink_for(rec.get("album")), token=token)
    return (p or {}).get("short_url") or (p or {}).get("url") or ""


def append_gumroad_event(rec, product):
    """把「已建立/更新」記回 catalog/releases.jsonl（append-only、只記事件）。"""
    ev = {"ts": cat.now_iso(), "schema_version": SCHEMA_VERSION, "event": "gumroad",
          "run": rec.get("run"), "album": rec.get("album"), "artist": rec.get("artist"),
          "product_id": (product or {}).get("id"),
          "url": (product or {}).get("short_url") or (product or {}).get("url"),
          "published": bool((product or {}).get("published"))}
    io.append_jsonl(RELEASES_JSONL, {k: v for k, v in ev.items() if v not in (None, "", [], {})})
    return ev


# ── CLI ──────────────────────────────────────────────────────────────

def load_release(run):
    """讀 output/releases/<run>/release.json。"""
    p = RELEASES_DIR / run / "release.json"
    if not p.exists():
        sys.exit(f"找不到 {p}（先跑 `lofi release package --run {run}`）")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        sys.exit(f"{p} 不是合法 JSON")


def deliverable_paths(run):
    """交付檔（zip）與封面。"""
    d = RELEASES_DIR / run
    zips = sorted((d / "download").glob("*.zip")) if (d / "download").is_dir() else []
    covers = [p for p in sorted(d.glob("cover_*.*")) if p.is_file()]
    return (zips[0] if zips else None), (covers[0] if covers else None)


def cmd_gumroad(args):
    rec = load_release(args.run)
    token = load_token()
    zip_path, cover = deliverable_paths(args.run)
    params = build_params(rec, price=args.price, permalink=args.permalink,
                          tags=_parse_tags(args.tags) or None, publish=False,
                          desc_format="text" if args.text_description else "html")

    price_txt = f"US${params['price'] / 100:g}" if params.get("price") else "pay-what-you-want"
    zip_txt = (f"{zip_path.name}（{zip_path.stat().st_size / 1e6:.0f} MB）"
               if zip_path else "⚠️ 沒有 zip（先跑 release package）")
    print("─" * 64)
    print(f"▶ lofi.release gumroad    run={args.run}")
    print(f"  商品名：{params['name']}")
    print(f"  網址：gumroad.com/l/{params['custom_permalink']}")
    print(f"  價格：{price_txt}")
    print(f"  tags：{', '.join(params.get('tags[]') or []) or '（無）'}")
    print(f"  交付檔：{zip_txt}")
    print(f"  封面：{cover.name if cover else '⚠️ 沒有封面'}")
    print("─" * 64)

    if args.dry_run:
        print("（dry-run：不呼叫 API）會送出的參數：")
        for k, v in params.items():
            print(f"  {k} = {str(v)[:80]}{'…' if len(str(v)) > 80 else ''}")
        print("\n接下來（真跑時）：POST /v2/products → 上傳 zip → 補封面 → enable")
        return

    existing = find_product(permalink=params["custom_permalink"], token=token)
    if existing:
        pid = existing["id"]
        print(f"  已存在同 permalink 的商品（{pid}）→ 改為更新，不重複建立")
        product = update_product(pid, {k: v for k, v in params.items()
                                       if k not in ("native_type", "published")}, token)
    else:
        product = create_product(params, token)
        pid = product.get("id")
        print(f"  ✅ 已建立 draft：{product.get('short_url') or pid}")
    if product.get("warning"):
        print(f"  ⚠️  Gumroad 提醒：{product['warning']}")

    # 交付檔（買家要拿到東西，否則 Gumroad 不會放行發佈）
    if args.no_upload:
        print("  （--no-upload：跳過上傳）")
    else:
        if zip_path:
            print(f"  上傳交付檔（{zip_path.stat().st_size / 1e6:.0f} MB）…")
            url = upload_file(zip_path, token=token, label=zip_path.name)
            attach_file(pid, url, zip_path.name, token)
            print(f"  ✅ 已附上 {zip_path.name}")
        # 封面：公開 API **吃不到我們私有的 S3 網址**（那會 403，Gumroad 回
        # "Cover must be an image"），也不給 presign 產生 signed_blob_id。
        # 所以只能 (a) 在 dashboard 手動拖一張，或 (b) 給一個公開可讀的圖片 URL。
        if args.cover_url:
            try:
                attach_cover(pid, args.cover_url, token)
                print("  ✅ 已附上封面（外部 URL）")
            except GumroadError as e:
                print(f"  ⚠️  封面附加失敗（{e}）→ 請在 dashboard 手動上傳")
        elif cover:
            print(f"  ⚠️  封面要手動：公開 API 只收**公開可讀**的圖片 URL，"
                  f"我們的 S3 是私有。\n"
                  f"      → 在 Gumroad 商品頁把 {cover.name} 拖進去（或下次給 --cover-url）")

    if args.publish:
        body = enable_product(pid, token)
        print(f"  🚀 已發佈：{body.get('product', {}).get('short_url') or ''}"
              + (f"（warning：{body.get('warning')}）" if body.get("warning") else ""))
    elif product.get("published"):
        # 已發佈的商品：更新內容就即時生效，不需要再 --publish（訊息不要誤導）
        print("  （商品**已在發佈中**：這次的內容更新已生效）")
    else:
        print("  （維持 draft；檢查後加 --publish 發佈）")

    append_gumroad_event(rec, product)
    print(f"  商品頁：{product.get('short_url') or ''}")


def _parse_tags(s):
    return [t.strip() for t in (s or "").split(",") if t.strip()]


def add_parser(sub):
    """掛進 `lofi release` 的子命令。"""
    p = sub.add_parser("gumroad", help="把上架包送進 Gumroad（API v2；有 API，可自動化）")
    p.add_argument("--run", required=True, help="已產出的 release run（output/releases/<run>/）")
    p.add_argument("--price", type=float, default=None,
                   help="售價 USD（0＝pay-what-you-want；預設讀 release.json 的 listing.price_usd）")
    p.add_argument("--permalink", default="", help="商品網址 slug（預設由專輯名推得）")
    p.add_argument("--tags", default="", help="覆寫 tags（逗號分隔；預設讀 release.json）")
    p.add_argument("--dry-run", action="store_true", help="只印會送出的參數，不呼叫 API")
    p.add_argument("--no-upload", action="store_true", dest="no_upload",
                   help="只建立/更新商品，不上傳交付檔與封面")
    p.add_argument("--cover-url", default="", dest="cover_url",
                   help="封面圖的**公開可讀** URL（API 不吃私有 S3；沒有就請在 dashboard 手動上傳）")
    p.add_argument("--publish", action="store_true",
                   help="上傳完成後直接發佈（預設只建 draft，讓你檢查）")
    p.add_argument("--text-description", action="store_true", dest="text_description",
                   help="商品描述送純文字（預設送 HTML，Gumroad 的標題／清單才會排版）")
    p.set_defaults(func=cmd_gumroad)
    return p


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Gumroad API v2（上架）")
    sp = ap.add_subparsers(dest="cmd", required=True)
    add_parser(sp)
    a = ap.parse_args()
    a.func(a)
