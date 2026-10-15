#!/usr/bin/env python3
"""status.py — 產線狀態（監控）：一眼看完「現在跑到哪」。

資料來源（狀態一律**從紀錄推導**，不依賴外接碟）：
  - `catalog/runs.jsonl`（run 帳本，真相）＋ `logs/.run_state.json`（快照）
  - `logs/.gpu.lock`（生成互斥鎖；內含 pid/host/label）
  - `publish/*.json`（上片佇列：pending / scheduled / uploaded）
  - 內接碟可用空間與 output（外接碟 symlink）連通性

用法：
  lofi status                     # 一眼看全貌
  lofi status --json              # 給程式用（未來排程／看板）
  lofi status --watch 5           # 每 5 秒刷新
  lofi status --run cozy-1hr-03   # 單一 run 的逐段細節
  lofi status --failures          # 只看問題（疑似中斷／失敗）
  lofi status --quiet             # 無輸出；有問題才 exit 1（給 watchdog/排程）
  lofi status --serve             # 本機監控 UI（預設 127.0.0.1:8787）
"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from lofi import config, media, publish, runs
from lofi.catalog import now_iso
from lofi.paths import ROOT, OUTPUT_DIR, LOGS_DIR


# ----------------------------- 小工具（純函式）-----------------------------

def _is_wide(ch):
    o = ord(ch)
    return (0x1100 <= o <= 0x115F or 0x2E80 <= o <= 0xA4CF or 0xAC00 <= o <= 0xD7A3
            or 0xF900 <= o <= 0xFAFF or 0xFE30 <= o <= 0xFE4F or 0xFF00 <= o <= 0xFF60
            or 0xFFE0 <= o <= 0xFFE6 or 0x20000 <= o <= 0x3FFFD)


def _w(s):
    return sum(2 if _is_wide(c) else 1 for c in str(s))


def _pad(s, width, right=False):
    s = str(s)
    gap = max(0, width - _w(s))
    return (" " * gap + s) if right else (s + " " * gap)


def _dur(seconds):
    if seconds is None:
        return "-"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def _clock(iso):
    """ISO 時間 → 本地 HH:MM；解不開回 ''。"""
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(iso)[:16]


STATUS_ICON = {"running": "▶", "ok": "✅", "fail": "❌", "skip": "⏭️"}


def _pct(done, total):
    if not total:
        return ""
    try:
        return f"{int(100 * done / total)}%"
    except (TypeError, ZeroDivisionError):
        return ""


# ----------------------------- 蒐集 -----------------------------

def _read_lock():
    try:
        return (Path(LOGS_DIR) / ".gpu.lock").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _queue():
    """上片佇列摘要（讀 publish/*.json 的 status，不做 ffprobe 以免拖慢）。"""
    try:
        recs = publish.load_records()
    except Exception:
        recs = {}
    counts = {"pending": 0, "scheduled": 0, "uploaded": 0, "unprepared": 0}
    pending = []
    for stem, rec in sorted(recs.items()):
        st = rec.get("status", "pending")
        counts[st] = counts.get(st, 0) + 1
        if st == "pending":
            pending.append({"stem": stem, "title": rec.get("title", "")})
    return {"counts": counts, "pending": pending, "pending_total": len(pending)}


def _resources():
    try:
        free = round(media.free_gb(ROOT), 1)
    except Exception:
        free = None
    od = Path(OUTPUT_DIR)
    mounted = od.exists() and od.is_dir()
    try:
        target = os.path.realpath(od)
    except Exception:
        target = str(od)
    return {"free_gb": free, "min_free_gb": config.MIN_FREE_GB,
            "output_mounted": mounted, "output_path": target, "lock": _read_lock()}


def collect(limit=None):
    """蒐集一份完整狀態（純讀取；測試可 patch runs/publish 的路徑）。

    limit=None → 用 config.STATUS_RECENT；limit=0 → 全部（--run 查舊 run 時要用）。
    """
    limit = config.STATUS_RECENT if limit is None else limit
    events = runs.load_events()
    running = runs.current(events)
    return {
        "generated_at": now_iso(),
        "running": running,
        "stale": [r for r in running if r.get("stale")],
        "runs": runs.recent_runs(events, limit=limit),
        "queue": _queue(),
        "resources": _resources(),
        "ledger": {"path": str(runs.RUNS_JSONL), "events": len(events)},
    }


# ----------------------------- 文字輸出 -----------------------------

def _line_running(r):
    prog = f"{r['done']}/{r['total']}" if r["total"] else (str(r["done"]) if r["done"] else "-")
    pct = _pct(r["done"], r["total"])
    prog = f"{prog} ({pct})" if pct else prog
    idle = f"閒置 {_dur((r['idle_min'] or 0) * 60)}" if r["idle_min"] is not None else ""
    flag = "⚠️ 疑似中斷" if r["stale"] else ("✅" if r["alive"] else "")
    return (f"  ▶ {_pad(r['run_id'] + ' · ' + str(r['stage']), 30)}"
            f"{_pad(prog, 14)}起 {_clock(r['started'])[-5:]}  "
            f"{_pad(idle, 12)}pid {str(r['pid'] or '-'):<7}{flag}").rstrip()


def render_text(view, *, failures_only=False, run=None):
    out = [f"◆ 產線狀態  ({_clock(view['generated_at'])})"]

    # 單一 run 細節
    if run:
        match = [r for r in view["runs"] if r["run_id"] == run]
        if not match:
            return f"找不到 run「{run}」（用 lofi runs ls 看有哪些）"
        r = match[0]
        out.append("")
        out.append(f"run {r['run_id']}（{r.get('kind') or '—'}）→ {r['status']}")
        for name, st in zip(runs.stage_order(r["stages"]), [r["stages"][n] for n in runs.stage_order(r["stages"])]):
            out.append(f"  {STATUS_ICON.get(st, '•')} {_pad(name, 10)} {st}")
        if r.get("last_ts"):
            out.append(f"  最後動靜：{_clock(r['last_ts'])}")
        if r.get("log"):
            out.append(f"  log：{r['log']}")
        return "\n".join(out)

    # 現在
    running = [r for r in view["running"] if r.get("stale")] if failures_only else view["running"]
    recents = view["runs"]
    if failures_only:
        recents = [r for r in recents if r["status"] == "fail"]
        if not running and not recents:
            return "✅ 沒有問題（沒有疑似中斷的階段、最近沒有失敗的 run）"

    out.append("")
    out.append("現在")
    if running:
        out.extend(_line_running(r) for r in running)
    else:
        out.append("  （沒有進行中的階段）")

    # 最近 run
    recent = recents
    if recent:
        out.append("")
        out.append("最近 run")
        for r in recent:
            stages = " ".join(f"{n}:{STATUS_ICON.get(r['stages'][n], '•')}"
                              for n in runs.stage_order(r["stages"]))
            out.append(f"  {STATUS_ICON.get(r['status'], '•')} {_pad(r['run_id'], 22)}"
                       f"{_pad(r['status'], 9)}{_pad(stages, 34)}{_clock(r['last_ts'])}")

    # 上片佇列
    q = view["queue"]["counts"]
    out.append("")
    out.append(f"上片佇列   🟢 待上傳 {q.get('pending', 0)} · 🟡 已排程 {q.get('scheduled', 0)}"
               f" · ✅ 已上傳 {q.get('uploaded', 0)}"
               + (f" · ⚪ 未備 {q.get('unprepared', 0)}" if q.get("unprepared") else ""))
    for p in view["queue"]["pending"][:3]:
        out.append(f"           • {p['stem']}" + (f" — {p['title']}" if p["title"] else ""))

    # 資源
    res = view["resources"]
    free = f"{res['free_gb']}GB" if res["free_gb"] is not None else "?"
    out.append("")
    out.append(f"資源       內接剩 {free}（門檻 {res['min_free_gb']:g}）"
               f" · 外接 output: {'✅ 掛載' if res['output_mounted'] else '❌ 未掛載'}")
    out.append(f"           GPU 鎖: {res['lock'] or '無'}")
    out.append(f"帳本       {view['ledger']['path']}（{view['ledger']['events']} 筆事件）")
    return "\n".join(out)


def render_json(view):
    return json.dumps(view, ensure_ascii=False, indent=2)


# ----------------------------- 監控 UI（stdlib http.server）-----------------------------

PAGE = """<!doctype html>
<html lang="zh-Hant"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>lofi-studio 監控</title>
<style>
 :root{--bg:#12141a;--card:#1b1e27;--fg:#e6e8ef;--muted:#8b91a4;--ok:#3ddc84;
       --warn:#ffcc4d;--bad:#ff6b6b;--accent:#7aa2ff}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
 header{padding:16px 20px;border-bottom:1px solid #262a36;display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
 h1{font-size:17px;margin:0}
 .muted{color:var(--muted)} .small{font-size:12px}
 main{padding:16px 20px;max-width:900px;margin:0 auto}
 section{margin-bottom:22px}
 h2{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:0 0 10px}
 .card{background:var(--card);border:1px solid #262a36;border-radius:10px;padding:12px 14px;margin-bottom:10px}
 .card.bad{border-color:var(--bad)}
 .rname{font-weight:600}
 .meta{color:var(--muted);font-size:13px;margin-top:4px}
 .bar{height:8px;background:#262a36;border-radius:6px;overflow:hidden;margin:8px 0}
 .bar>span{display:block;height:100%;background:var(--accent)}
 .bad .bar>span{background:var(--bad)}
 .pill{display:inline-block;padding:1px 8px;border-radius:999px;background:#262a36;font-size:12px;margin-right:6px}
 .ok{color:var(--ok)} .warn{color:var(--warn)} .badc{color:var(--bad)}
 table{width:100%;border-collapse:collapse;font-size:14px}
 td,th{text-align:left;padding:6px 8px;border-bottom:1px solid #23273a}
 th{color:var(--muted);font-weight:500;font-size:12px}
 code{color:var(--muted)}
</style></head><body>
<header>
  <h1>🌀 lofi-studio</h1>
  <span class="muted small">更新 <span id="updated">–</span> · 每 __INTERVAL__ 秒自動刷新</span>
</header>
<main>
  <section><h2>現在</h2><div id="running"></div></section>
  <section><h2>最近 run</h2><div id="runs"></div></section>
  <section><h2>上片佇列</h2><div class="card" id="queue"></div></section>
  <section><h2>資源</h2><div class="card" id="res"></div></section>
</main>
<script>
const e = s => (s==null?'':String(s)).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const si = {running:'▶', ok:'✅', fail:'❌', skip:'⏭️'};
async function refresh(){
  let v;
  try { v = await (await fetch('/api/status', {cache:'no-store'})).json(); }
  catch(err){ document.getElementById('updated').textContent = '連線失敗'; return; }
  if (v.error){ document.getElementById('running').innerHTML = '<div class="card bad">'+e(v.error)+'</div>'; return; }
  document.getElementById('updated').textContent = v.generated_at || '';

  const run = document.getElementById('running'); run.innerHTML='';
  if (!v.running || !v.running.length) run.innerHTML = '<div class="card muted">（沒有進行中的階段）</div>';
  else v.running.forEach(x=>{
    const pct = x.total ? Math.round(100*x.done/x.total) : 0;
    const prog = x.total ? x.done+'/'+x.total+' ('+pct+'%)' : (x.done||'-');
    const div = document.createElement('div');
    div.className = 'card' + (x.stale ? ' bad' : '');
    div.innerHTML = '<div class="rname">▶ '+e(x.run_id)+' · '+e(x.stage)+'</div>'
      + '<div class="bar"><span style="width:'+pct+'%"></span></div>'
      + '<div class="meta">'+e(prog)+'　'+(x.stale?'<span class="badc">⚠️ 疑似中斷</span>':'執行中')
      + '　pid '+e(x.pid)+(x.idle_min!=null?'　閒置 '+e(x.idle_min)+' 分':'')+'</div>';
    run.appendChild(div);
  });

  const rt = document.getElementById('runs');
  rt.innerHTML = '<table><tr><th>run</th><th>狀態</th><th>階段</th><th></th></tr>'
    + (v.runs||[]).map(r=>{
        const stages = Object.keys(r.stages).map(k=>e(k)+':'+(si[r.stages[k]]||'•')).join(' ');
        return '<tr><td>'+e(r.run_id)+'</td><td class="'+e(r.status)+'">'+(si[r.status]||'•')+' '+e(r.status)
          +'</td><td>'+stages+'</td><td class="muted small">'+e(r.last_ts?r.last_ts.slice(5,16).replace('T',' '):'')+'</td></tr>';
      }).join('') + '</table>';

  const q = v.queue.counts;
  document.getElementById('queue').innerHTML =
    '<span class="pill">🟢 待上傳 '+q.pending+'</span><span class="pill">🟡 已排程 '+(q.scheduled||0)
    +'</span><span class="pill">✅ 已上傳 '+(q.uploaded||0)+'</span>'
    + ((v.queue.pending||[]).slice(0,3).map(p=>'<div class="meta">• '+e(p.stem)+(p.title?' — '+e(p.title):'')+'</div>').join(''));

  const r = v.resources;
  document.getElementById('res').innerHTML =
    '內接剩 '+(r.free_gb!=null?e(r.free_gb)+'GB':'?')+'（門檻 '+e(r.min_free_gb)+'）　'
    + '外接 output: '+(r.output_mounted?'<span class="ok">✅ 掛載</span>':'<span class="badc">❌ 未掛載</span>')
    + '<div class="meta">GPU 鎖: '+e(r.lock||'無')+'</div>'
    + '<div class="meta">帳本: <code>'+e(v.ledger.path)+'</code>（'+e(v.ledger.events)+' 筆）</div>';
}
refresh();
setInterval(refresh, __INTERVAL__*1000);
</script></body></html>
"""


def serve(host="127.0.0.1", port=None, interval=10):
    """本機監控 UI（唯讀）。--host 0.0.0.0 可從手機連（同一個 Wi-Fi）。"""
    import http.server
    import socketserver

    port = int(port or config.STATUS_PORT)
    page = PAGE.replace("__INTERVAL__", str(interval))

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype):
            b = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/api/status", "/api/status/"):
                try:
                    view = collect()
                except Exception as exc:
                    view = {"error": f"{type(exc).__name__}: {exc}"}
                self._send(200, render_json(view), "application/json; charset=utf-8")
            elif path in ("/", "/index.html"):
                self._send(200, page, "text/html; charset=utf-8")
            else:
                self._send(404, "not found", "text/plain; charset=utf-8")

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((host, port), Handler) as httpd:
        print(f"🌀 監控 UI：http://{host}:{port}"
              + ("" if host in ("127.0.0.1", "localhost") else "（手機連同一個 Wi-Fi 可用）"))
        print("   Ctrl-C 結束")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n👋 已停止")


# ----------------------------- CLI -----------------------------

def main():
    import argparse

    ap = argparse.ArgumentParser(description="產線狀態（現在跑到哪／佇列／資源／監控 UI）")
    ap.add_argument("--json", action="store_true", help="以 JSON 輸出（給程式／排程）")
    ap.add_argument("--run", default="", help="只看某個 run 的逐段細節")
    ap.add_argument("--watch", nargs="?", type=int, const=5, default=None,
                    metavar="SEC", help="持續刷新（預設每 5 秒）")
    ap.add_argument("--failures", action="store_true", help="只看問題（疑似中斷／失敗）")
    ap.add_argument("--quiet", action="store_true",
                    help="無輸出；有問題（疑似中斷／失敗）才 exit 1")
    ap.add_argument("--limit", type=int, default=None, help="最近 run 顯示筆數")
    ap.add_argument("--serve", action="store_true", help="啟動本機監控 UI")
    ap.add_argument("--host", default="127.0.0.1", help="UI 綁定位址（0.0.0.0＝同網段可連）")
    ap.add_argument("--port", type=int, default=None, help=f"UI 埠（預設 {config.STATUS_PORT}）")
    ap.add_argument("--interval", type=int, default=10, help="UI 自動刷新秒數（預設 10）")
    args = ap.parse_args()

    if args.serve:
        serve(host=args.host, port=args.port, interval=args.interval)
        return

    # --run 指定單一 run 時要看得到舊的：不受「最近 N 筆」限制（limit=0 → 全部）
    lim = args.limit if args.limit is not None else (0 if args.run else None)

    if args.watch is not None:
        try:
            while True:
                view = collect(limit=lim)
                sys.stdout.write("\033[2J\033[H")
                print(render_text(view, failures_only=args.failures, run=args.run))
                print("\n(Ctrl-C 結束；--serve 有網頁版)")
                sys.stdout.flush()
                time.sleep(max(1, args.watch))
        except KeyboardInterrupt:
            return

    view = collect(limit=lim)
    if args.quiet:
        bad = view["stale"] or [r for r in view["runs"] if r["status"] == "fail"]
        sys.exit(1 if bad else 0)
    if args.json:
        print(render_json(view))
        return
    print(render_text(view, failures_only=args.failures, run=args.run))


if __name__ == "__main__":
    main()
