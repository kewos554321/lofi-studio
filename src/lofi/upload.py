#!/usr/bin/env python3
"""
upload.py — 把成片上傳到 YouTube（Data API v3）。

定位：**自動上傳到「上線前」為止**。預設上傳成 private（可改 unlisted），
之後的公開 / 排程 / 播放清單 / AI 揭露勾選，由你手動在 YouTube Studio 完成。

metadata（標題/描述/tags/category）取自 publish/<名稱>.json（由 make_meta.py 產生），
上傳成功後把 youtube_id / youtube_url / privacy / uploaded_at 寫回該 json。

前置（一次性，見 README「自動上傳設定」）:
  1. Google Cloud 專案 → 啟用 YouTube Data API v3
  2. 建立 OAuth 用戶端 ID（類型：桌面應用程式）→ 下載 JSON
  3. 放到 .secrets/client_secret.json（或用環境變數 YT_CLIENT_SECRET 指定）
  首次執行會開瀏覽器要求授權，token 快取在 .secrets/yt_token.json（不進版控）。

用法:
  python3 scripts/yt_upload.py --episode rl01                 # 讀 publish/rl01.json
  python3 scripts/yt_upload.py --episode rl01 --privacy unlisted
  python3 scripts/yt_upload.py --episode rl01 --video PATH    # 覆寫影片路徑
  python3 scripts/yt_upload.py --episode rl01 --dry-run       # 只驗證、不上傳
"""
import argparse
import json
import os
import sys
from pathlib import Path

from lofi import catalog as cat
from lofi import io
from lofi.paths import PUBLISH_DIR, ROOT, SECRETS_DIR

# 預設只要上傳權限（自動上傳用；非互動，不能跳出瀏覽器要授權）。
# 想用 API 讀取／更新／刪除既有影片（修說明、換縮圖）再設 LOFI_YT_SCOPE_EXTRA=1
# 重新授權一次（會多要 youtube.force-ssl）。
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
if os.environ.get("LOFI_YT_SCOPE_EXTRA") == "1":
    SCOPES.append("https://www.googleapis.com/auth/youtube.force-ssl")
DEFAULT_CLIENT_SECRET = SECRETS_DIR / "client_secret.json"
DEFAULT_TOKEN = SECRETS_DIR / "yt_token.json"
MAX_TITLE = 100
MAX_DESC = 5000
MAX_TAGS_CHARS = 500


def load_record(episode):
    p = PUBLISH_DIR / f"{episode}.json"
    if not p.exists():
        sys.exit(f"找不到 publish/{episode}.json（先用 make_meta.py 或 make_long_lofi.sh --style 產生）")
    return p, json.loads(p.read_text(encoding="utf-8"))


def resolve_video(episode, rec, override):
    if override:
        v = Path(override)
    elif rec.get("video"):
        v = ROOT / rec["video"]
    else:
        v = None
    if v and v.exists():
        return v
    for cand in (ROOT / "output" / "episodes" / episode / "video.mp4",
                 ROOT / "output" / "videos" / f"{episode}.mp4"):
        if cand.exists():
            return cand
    sys.exit(f"找不到影片：{override or rec.get('video') or episode}")


def clamp_tags(tags):
    """YouTube 限制 tags 總長 <= 500 字元（含逗號）。"""
    out, total = [], 0
    for t in tags or []:
        need = len(t) + (1 if out else 0)
        if total + need > MAX_TAGS_CHARS:
            break
        out.append(t)
        total += need
    return out


def build_body(rec, privacy):
    title = (rec.get("title") or "Lofi Mix")[:MAX_TITLE]
    desc = (rec.get("description") or "")[:MAX_DESC]
    return {
        "snippet": {
            "title": title,
            "description": desc,
            "tags": clamp_tags(rec.get("tags")),
            "categoryId": str(rec.get("category_id") or 10),
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }


def get_credentials(client_secret, token_path):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        sys.exit(
            "缺少 Google 套件。請用專案 .venv 執行並安裝：\n"
            "  .venv/bin/python -m pip install google-api-python-client google-auth-oauthlib google-auth-httplib2\n"
            "或改用半自動：手動上傳後 python3 scripts/upload_status.py --mark-uploaded <名稱> --url <網址>"
        )
    if not Path(client_secret).exists():
        sys.exit(
            f"找不到 OAuth 憑證 {client_secret}\n"
            "請見 README「自動上傳設定」：建立桌面應用程式 OAuth 用戶端並下載 JSON 放到該路徑，\n"
            "或設環境變數 YT_CLIENT_SECRET 指向你的 client_secret.json。"
        )
    creds = None
    if Path(token_path).exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    # 快取的 token 可能只有舊權限（例如先前只有 youtube.upload）：權限不足時要重新授權，
    # 否則 creds.valid 仍為 True，會一直用到權限不足為止（API 回 403 insufficient scope）。
    granted = set(getattr(creds, "scopes", None) or []) if creds else set()
    if creds and creds.valid and not set(SCOPES).issubset(granted):
        print(f"  既有 token 權限不足（{sorted(granted) or '未知'}）→ 重新授權以取得：",
              ", ".join(sorted(set(SCOPES) - granted)), flush=True)
        creds = None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            print("首次授權：將開啟瀏覽器，請用『Moni on the Journey』頻道那個 Google 帳號登入並同意。",
                  flush=True)
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
            creds = flow.run_local_server(port=0)
        Path(token_path).parent.mkdir(parents=True, exist_ok=True)
        io.atomic_write_text(Path(token_path), creds.to_json())
        print(f"已快取授權 token：{token_path}（不進版控）")
    return creds


def main():
    ap = argparse.ArgumentParser(description="上傳成片到 YouTube（上線前自動，之後手動）")
    ap.add_argument("--episode", default="", help="上片識別名（對應 publish/<名稱>.json）")
    ap.add_argument("--video", default="", help="覆寫影片路徑")
    ap.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"],
                    help="上傳後的可見性（預設 private，之後手動公開）")
    ap.add_argument("--client-secret", default="", help="OAuth client secret JSON 路徑")
    ap.add_argument("--token", default="", help="token 快取路徑")
    ap.add_argument("--auth-only", action="store_true", help="只做 OAuth 授權並快取 token，不上傳")
    ap.add_argument("--check", action="store_true", help="驗證憑證/上傳權限（開一個可續傳工作階段後取消，不留影片）")
    ap.add_argument("--dry-run", action="store_true", help="只驗證與印出，不上傳")
    args = ap.parse_args()

    if args.auth_only:
        client_secret = args.client_secret or str(DEFAULT_CLIENT_SECRET)
        token_path = args.token or str(DEFAULT_TOKEN)
        get_credentials(client_secret, token_path)
        print(f"✅ 授權完成，token 已快取：{token_path}")
        print("   之後 --stage upload 不必再開瀏覽器。")
        return

    if args.check:
        client_secret = args.client_secret or str(DEFAULT_CLIENT_SECRET)
        token_path = args.token or str(DEFAULT_TOKEN)
        creds = get_credentials(client_secret, token_path)
        print("==> 驗證上傳權限（不會建立影片）")
        from google.auth.transport.requests import AuthorizedSession
        session = AuthorizedSession(creds)
        url = ("https://www.googleapis.com/upload/youtube/v3/videos"
               "?uploadType=resumable&part=snippet,status")
        probe = {"snippet": {"title": "lofi-studio auth check", "categoryId": "10"},
                 "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}}
        r = session.post(url, json=probe, headers={
            "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": "0"})
        if r.status_code not in (200, 201):
            sys.exit(f"❌ 驗證失敗 HTTP {r.status_code}：{r.text[:400]}")
        loc = r.headers.get("Location")
        print(f"✅ 上傳端點可用（HTTP {r.status_code}）")
        if loc:
            session.delete(loc)   # 取消工作階段，不留任何影片
            print("   已取消工作階段，頻道上不會有東西。")
        return

    if not args.episode:
        sys.exit("需要 --episode（或 --auth-only 只授權）")

    rec_path, rec = load_record(args.episode)
    video = resolve_video(args.episode, rec, args.video)
    body = build_body(rec, args.privacy)

    size_mb = video.stat().st_size / 1048576
    print(f"==> 準備上傳: {video}  ({size_mb:.1f} MB)")
    print(f"    標題: {body['snippet']['title']}")
    print(f"    tags: {len(body['snippet']['tags'])} 個 | category: {body['snippet']['categoryId']} "
          f"| privacy: {args.privacy}")

    if args.dry_run:
        print("（dry-run，未實際上傳）")
        return

    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        sys.exit(
            "缺少 Google 套件。請用 .venv 執行並安裝：\n"
            "  .venv/bin/python -m pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
        )

    client_secret = args.client_secret or str(DEFAULT_CLIENT_SECRET)
    token_path = args.token or str(DEFAULT_TOKEN)
    creds = get_credentials(client_secret, token_path)
    youtube = build("youtube", "v3", credentials=creds)

    media = MediaFileUpload(str(video), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"    上傳中… {int(status.progress() * 100)}%", flush=True)

    vid = response["id"]
    url = f"https://youtu.be/{vid}"
    print(f"✅ 已上傳（{args.privacy}）: {url}")

    rec["youtube_id"] = vid
    rec["youtube_url"] = url
    rec["privacy"] = args.privacy
    rec["uploaded_at"] = cat.now_iso()
    rec["status"] = "uploaded"
    io.atomic_write_json(rec_path, rec)

    print()
    print("接下來請手動完成（上線）：")
    print(f"  1. 到 YouTube Studio 把影片設為公開/排程：{url}")
    print("  2. 勾選『變造或合成內容』AI 揭露（API 無法設定）")
    print("  3. 上傳縮圖、加入播放清單")
    print(f"  4. 完成後：python3 scripts/upload_status.py --mark-scheduled {args.episode}")


if __name__ == "__main__":
    main()
