#!/usr/bin/env python3
"""notify.py — 產線狀態通知（Email；純標準庫 smtplib）。

單人維運最需要的是「跑完了／掛了要知道」。這支提供一個**fail-soft** 的通知層：

  - fail-soft：通知失敗**絕不中斷產線**（只印 stderr + 記 logs/notifications.jsonl）。
  - 可插拔：send() 依 channel 分派；日後要 Telegram／Bark／macOS 通知只要多一個 channel。
  - 不吵人：level 門檻（debug<info<warn<error）+ dedupe_key 冷卻，重複事件不會連發。
  - 可關：`LOFI_NOTIFY=0` 全域關閉；設定不完整時自動視為關閉（產線行為完全不變）。

憑證：`.secrets/notify.json`（不進版控）或 LOFI_SMTP_* 環境變數。範例：

    {
      "enabled": true,
      "smtp_host": "smtp.gmail.com", "smtp_port": 465, "use_ssl": true,
      "username": "you@gmail.com", "app_password": "xxxx xxxx xxxx xxxx",
      "to": ["you@gmail.com"],
      "from": "you@gmail.com",
      "min_level": "info", "dedupe_min": 30
    }

用法：
  lofi notify --status            # 看目前設定（不寄信）
  lofi notify --test              # 寄一封測試信（驗證 Gmail 應用程式密碼）
  lofi notify --subject "標題" --body "內容" [--level warn] [--dry-run]
"""
import json
import os
import smtplib
import ssl
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from lofi import config, io
from lofi.catalog import now_iso
from lofi.paths import NOTIFY_LOG

LEVELS = {"debug": 0, "info": 1, "warn": 2, "error": 3}
ICON = {"ok": "✅", "fail": "❌", "skip": "⏭️", "running": "▶"}

# channel -> 傳送函式（可插拔；目前只有 email）
CHANNELS = {}


def icon(status):
    return ICON.get(status, "•")


def fmt_dur(seconds):
    """秒數 → 人看的字串（純函式）。"""
    if seconds is None:
        return "-"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


# ----------------------------- 設定 -----------------------------

def _as_bool(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y", "t")


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [t.strip() for t in v.split(",") if t.strip()]
    return [str(t).strip() for t in v if str(t).strip()]


def _read_file(path=None):
    """讀 .secrets/notify.json；不存在回 {}，壞 JSON 只警告（不讓通知設定壞掉拖垮產線）。"""
    p = Path(path or config.NOTIFY_FILE)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️  通知設定無法解析（{p}）：{e}", file=sys.stderr)
        return {}


def load_settings(file_path=None):
    """合併設定：環境變數 > .secrets/notify.json > config（含 LOFI_* 預設）。

    回傳 dict（enabled / channel / smtp_host / smtp_port / use_ssl /
    username / app_password / from / to / min_level / dedupe_min / timeout）。
    """
    fv = _read_file(file_path)

    def pick(key, env, default):
        raw = os.environ.get(env)
        if raw is None or not str(raw).strip():
            raw = fv.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return default
        return raw

    to_yaml = pick("to", "LOFI_NOTIFY_TO", None)
    to = _as_list(to_yaml) or list(config.NOTIFY_TO)

    return {
        "enabled": _as_bool(pick("enabled", "LOFI_NOTIFY", config.NOTIFY_ENABLED)),
        "channel": str(pick("channel", "LOFI_NOTIFY_CHANNEL", "email")),
        "smtp_host": str(pick("smtp_host", "LOFI_SMTP_HOST", config.NOTIFY_SMTP_HOST)),
        "smtp_port": int(pick("smtp_port", "LOFI_SMTP_PORT", config.NOTIFY_SMTP_PORT)),
        "use_ssl": _as_bool(pick("use_ssl", "LOFI_SMTP_SSL", config.NOTIFY_SMTP_SSL)),
        "username": str(pick("username", "LOFI_SMTP_USER", config.NOTIFY_SMTP_USER)),
        "app_password": str(pick("app_password", "LOFI_SMTP_PASS", config.NOTIFY_SMTP_PASS)),
        "from": str(pick("from", "LOFI_NOTIFY_FROM", config.NOTIFY_FROM)),
        "to": to,
        "min_level": str(pick("min_level", "LOFI_NOTIFY_MIN_LEVEL", config.NOTIFY_MIN_LEVEL)),
        "dedupe_min": int(pick("dedupe_min", "LOFI_NOTIFY_DEDUPE_MIN", config.NOTIFY_DEDUPE_MIN)),
        "timeout": config.NOTIFY_TIMEOUT,
    }


def status_of_settings(st=None):
    """回傳 (ready: bool, reason: str)：能不能真的寄信。"""
    st = st or load_settings()
    if not st["enabled"]:
        return False, "已停用（LOFI_NOTIFY=0 或 enabled=false）"
    if not st["username"] or not st["app_password"]:
        return False, f"缺 SMTP 帳密（編輯 {config.NOTIFY_FILE}：username / app_password）"
    if not st["to"]:
        return False, "缺收件者（notify.json 的 to，或 LOFI_NOTIFY_TO）"
    return True, "OK"


# ----------------------------- 訊息內容（純函式）-----------------------------

def render_stage(run_id, stage, status, *, elapsed_s=None, note="",
                 log="", artifacts=None):
    """組出階段事件的通知主旨／內文（純函式，方便測試與後續管道共用）。"""
    subject = f"{icon(status)} [{run_id}] {stage} {status}"
    lines = [f"run    : {run_id}",
             f"階段   : {stage} → {icon(status)} {status}"]
    if elapsed_s is not None:
        lines.append(f"耗時   : {fmt_dur(elapsed_s)}")
    if note:
        lines.append(f"備註   : {note}")
    if artifacts:
        lines.append("產物   : " + "、".join(artifacts))
    if log:
        lines.append(f"log    : {log}")
    lines.append(f"時間   : {now_iso()}")
    return subject, "\n".join(lines)


def render_run(run_id, status, *, stages=None, note="", extra=None):
    """組出整段 run 完成的通知（stages 例：{'music':'ok','video':'ok'}）。"""
    subject = f"{icon(status)} [{run_id}] run {status}"
    lines = [f"run    : {run_id} → {icon(status)} {status}"]
    if stages:
        for name, st in stages.items():
            lines.append(f"  {icon(st)} {name:<8} {st}")
    if note:
        lines.append(f"備註   : {note}")
    if extra:
        lines.append("")
        lines.extend(extra)
    lines.append(f"時間   : {now_iso()}")
    return subject, "\n".join(lines)


# ----------------------------- 傳送 -----------------------------

def _log(rec):
    try:
        io.append_jsonl(NOTIFY_LOG, rec)
    except Exception:
        pass


def _recent_dupe(dedupe_key, minutes, now=None):
    """同一個 dedupe_key 最近是否已成功寄出（冷卻中）。"""
    if not dedupe_key or not Path(NOTIFY_LOG).exists():
        return False
    now = now or datetime.now().astimezone()
    try:
        lines = Path(NOTIFY_LOG).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("dedupe_key") != dedupe_key or not rec.get("ok"):
            continue
        try:
            t = datetime.fromisoformat(rec.get("ts", ""))
        except (TypeError, ValueError):
            return False
        if t.tzinfo is None:
            t = t.replace(tzinfo=now.tzinfo)
        return (now - t).total_seconds() < minutes * 60
    return False


def send_email(st, subject, body):
    """真的用 SMTP 寄一封信（Gmail：use_ssl=true、埠 465、應用程式密碼）。

    Gmail 應用程式密碼畫面顯示成 `xxxx xxxx xxxx xxxx`，貼上時常帶空格——
    這裡直接去掉空白，含空格或不含都能用。
    """
    user = (st["username"] or "").strip()
    password = (st["app_password"] or "").replace(" ", "").strip()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = (st["from"] or "").strip() or user
    msg["To"] = ", ".join(st["to"])
    msg.set_content(body)
    if st["use_ssl"]:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(st["smtp_host"], int(st["smtp_port"]),
                              timeout=st["timeout"], context=ctx) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(st["smtp_host"], int(st["smtp_port"]), timeout=st["timeout"]) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(user, password)
            s.send_message(msg)
    return True, f"寄出給 {', '.join(st['to'])}"


CHANNELS["email"] = send_email


def send(subject, body, *, level="info", dedupe_key=None, dry_run=False,
         settings=None, record=True):
    """寄一則通知。**永不拋例外**（fail-soft）。

    回傳 (ok: bool, detail: str)。ok=False 代表沒寄（停用／未設定／門檻／冷卻／失敗）。
    """
    try:
        st = settings or load_settings()
    except Exception as e:                       # 設定壞掉也不能炸產線
        print(f"⚠️  通知設定載入失敗：{e}", file=sys.stderr)
        return False, f"設定載入失敗: {e}"

    lvl = LEVELS.get(level, 1)
    min_lvl = LEVELS.get(st.get("min_level", "info"), 1)
    if not st.get("enabled"):
        return False, "停用（LOFI_NOTIFY=0）"
    ready, reason = status_of_settings(st)
    if not ready:
        return False, reason
    if lvl < min_lvl:
        return False, f"低於門檻 {st['min_level']}（此為 {level}）"
    if dedupe_key and _recent_dupe(dedupe_key, st.get("dedupe_min", 30)):
        return False, f"冷卻中（{dedupe_key}）"

    if dry_run:
        print(f"[dry-run] 會寄給 {', '.join(st['to'])}：{subject}")
        print("-" * 60)
        print(body)
        return True, "dry-run（未實際寄出）"

    channel = CHANNELS.get(st.get("channel", "email"))
    if channel is None:
        return False, f"未知管道 {st.get('channel')}"
    try:
        ok, detail = channel(st, subject, body)
    except Exception as e:                       # ★ 通知失敗不影響產線
        ok, detail = False, f"{type(e).__name__}: {e}"
        print(f"⚠️  通知寄送失敗（不影響產線）：{detail}", file=sys.stderr)

    if record:
        _log({"ts": now_iso(), "event": "notify", "channel": st.get("channel", "email"),
              "level": level, "subject": subject, "ok": ok, "detail": detail,
              "dedupe_key": dedupe_key or ""})
    if ok:
        print(f"📧 通知已送出：{subject}")
    return ok, detail


def main():
    import argparse

    ap = argparse.ArgumentParser(description="狀態通知（Email/Gmail SMTP；fail-soft，不影響產線）")
    ap.add_argument("--status", action="store_true", help="顯示目前通知設定（不寄信）")
    ap.add_argument("--test", action="store_true", help="寄一封測試信（驗證設定）")
    ap.add_argument("--subject", default="", help="手動寄一封：主旨")
    ap.add_argument("--body", default="", help="手動寄一封：內文")
    ap.add_argument("--level", default="info", choices=list(LEVELS), help="訊息等級（預設 info）")
    ap.add_argument("--dedupe-key", default="", help="冷卻鍵（短時間內同鍵不重複寄）")
    ap.add_argument("--dry-run", action="store_true", help="只印出會寄什麼，不真的寄")
    args = ap.parse_args()

    if args.status:
        st = load_settings()
        ready, reason = status_of_settings(st)
        print(f"  enabled     : {st['enabled']}")
        print(f"  可以寄信    : {'✅' if ready else '❌'} {reason}")
        print(f"  channel     : {st['channel']}")
        print(f"  smtp        : {st['smtp_host']}:{st['smtp_port']}"
              f"{' (SSL)' if st['use_ssl'] else ' (STARTTLS)'}")
        print(f"  username    : {st['username'] or '（未設定）'}")
        print(f"  app_password: {'（已設定）' if st['app_password'] else '（未設定）'}")
        print(f"  from / to   : {st['from'] or st['username'] or '（未設定）'} → "
              f"{', '.join(st['to']) or '（未設定）'}")
        print(f"  min_level   : {st['min_level']}   dedupe_min: {st['dedupe_min']}")
        print(f"  設定檔      : {config.NOTIFY_FILE}")
        return

    if args.test:
        st = load_settings()
        ready, reason = status_of_settings(st)
        if not ready:
            print(f"❌ 尚未就緒：{reason}", file=sys.stderr)
            print(f"   把 SMTP 設定寫進 {config.NOTIFY_FILE}（Gmail 需『應用程式密碼』）", file=sys.stderr)
            sys.exit(1)
        subject = "✅ lofi-studio 通知測試"
        body = ("這是 lofi-studio 的測試通知。\n"
                f"寄件者：{st['from'] or st['username']}\n收件者：{', '.join(st['to'])}\n"
                f"SMTP ：{st['smtp_host']}:{st['smtp_port']}\n時間 ：{now_iso()}\n")
        ok, detail = send(subject, body, level="info", dry_run=args.dry_run)
        if not ok:
            print(f"❌ 未寄出：{detail}", file=sys.stderr)
            sys.exit(1)
        return

    if args.subject or args.body:
        ok, detail = send(args.subject or "(無主旨)", args.body, level=args.level,
                          dedupe_key=args.dedupe_key or None, dry_run=args.dry_run)
        if not ok:
            print(f"⚠️  未寄出：{detail}", file=sys.stderr)
            sys.exit(1)
        return

    ap.print_help()


if __name__ == "__main__":
    main()
