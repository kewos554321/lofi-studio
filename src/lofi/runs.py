#!/usr/bin/env python3
"""runs.py — 產線 run 帳本（append-only 事件流）。

問題背景：
  長流程（make_episode / batch_run / scratch 腳本）各寫各的 log，狀態散在
  `logs/.gpu.lock`（有 pid）、`publish/*.json`（上片狀態）、`catalog/*.jsonl`（產品事件）
  與 `logs/*.log`（人看的文字），**沒有任何一處能回答「現在跑到哪」**。

  這裡把「run × stage × 進度」記成單一事件流 `catalog/runs.jsonl`（append-only，
  與 tracks.jsonl／releases.jsonl 同一套機制），供 `lofi status`（監控）與
  `lofi notify`（通知）讀取。

設計：
  - 一筆狀態轉移一行（stage_start / progress / stage_end / run_end）；
    progress 只在**批次邊界**寫，避免事件流膨脹。
  - append-only：狀態變更用新事件，不改舊行（與 catalog 其他事件流同規則）。
  - 純函式與 I/O 分離：`fold()`／`current()`／`pid_alive()` 可單獨測試（比照 qc.judge）。
  - `logs/.run_state.json` 是「現在」快照（衍生物、可重建）；**真相在 runs.jsonl**。
  - pid 存活（os.kill(pid,0)）才是「還在跑」的判準：進度事件之間可能隔很久
    （音樂每首 4–5 分，progress 只在每 chunk 邊界），單看事件時間會誤判中斷。
"""
import json
import os
import socket
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from lofi import config, io
from lofi.catalog import now_iso
from lofi.paths import RUNS_JSONL, RUN_STATE

SCHEMA_VERSION = 1

# 階段狀態
OPEN = "running"
OK = "ok"
FAIL = "fail"
SKIP = "skip"

# 只影響顯示順序（episode 的固定五段）；不在此列的 stage 排在後面。
STAGE_ORDER = ("music", "image", "video", "upload", "cleanup", "release")


def append(event, **fields):
    """append 一筆事件到 runs.jsonl，並更新「現在」快照。回傳寫入的事件 dict。"""
    ev = {"ts": now_iso(), "schema_version": SCHEMA_VERSION, "event": event}
    ev.update({k: v for k, v in fields.items() if v not in (None, "", [], {})})
    io.append_jsonl(RUNS_JSONL, ev)
    _snapshot()
    return ev


def stage_start(run_id, stage, *, kind="", cmd="", log="", total=None,
                unit="", pid=None, host=""):
    """開始一個階段（run 的一步）。"""
    return append("stage_start", run_id=run_id, kind=kind, stage=stage, cmd=cmd,
                  log=log, total=total, unit=unit, pid=pid or os.getpid(),
                  host=host or socket.gethostname(), started_at=now_iso())


def progress(run_id, stage, done, total=None, *, unit=""):
    """回報進度（建議只在批次邊界呼叫）。"""
    return append("progress", run_id=run_id, stage=stage, done=done,
                  total=total, unit=unit)


def stage_end(run_id, stage, status=OK, *, elapsed_s=None, artifacts=None, note=""):
    """結束一個階段（status: ok | fail | skip）。"""
    return append("stage_end", run_id=run_id, stage=stage, status=status,
                  elapsed_s=round(elapsed_s, 1) if elapsed_s is not None else None,
                  artifacts=list(artifacts) if artifacts else None, note=note)


def run_end(run_id, status=OK, *, note=""):
    """結束整個 run（所有階段跑完或中止）。"""
    return append("run_end", run_id=run_id, status=status, note=note)


class Stage:
    """`with runs.stage_run(...) as st:` 拿到的把手：回報進度／產物／備註。"""

    def __init__(self, run_id, stage, total=None, unit=""):
        self.run_id = run_id
        self.stage = stage
        self.total = total or 0
        self.unit = unit
        self.done = 0
        self.artifacts = []
        self.status = OK
        self.note = ""

    def set_total(self, total, unit=None):
        self.total = total
        if unit:
            self.unit = unit
        return self

    def tick(self, done=None):
        """回報進度（done 不給就 +1），並寫一行 progress。"""
        self.done = self.done + 1 if done is None else done
        progress(self.run_id, self.stage, self.done, self.total or None, unit=self.unit)
        return self.done

    def artifact(self, path):
        """記錄一個產物路徑。"""
        p = str(path)
        if p not in self.artifacts:
            self.artifacts.append(p)
        return self

    def skip(self, why=""):
        self.status = SKIP
        self.note = why or self.note
        return self

    def fail(self, why=""):
        self.status = FAIL
        self.note = why or self.note
        return self


@contextmanager
def stage_run(run_id, stage, *, kind="", cmd="", log="", total=None, unit=""):
    """包住一個階段：自動寫 stage_start／stage_end（例外時記 fail，並重新拋出）。

        with runs.stage_run("cozy-1hr-03", "music", kind="episode", total=40) as st:
            for i, t in enumerate(tracks):
                ...生成...
                st.tick(i + 1)
            st.artifact("assets/tracks/cozy_morning/cozy-1hr-03")
    """
    t0 = time.time()
    st = Stage(run_id, stage, total, unit)
    stage_start(run_id, stage, kind=kind, cmd=cmd, log=log, total=total or None, unit=unit)
    try:
        yield st
    except BaseException as e:                       # 連 Ctrl-C 都要留下 fail 紀錄
        stage_end(run_id, stage, FAIL, elapsed_s=time.time() - t0,
                  artifacts=st.artifacts, note=st.note or f"{type(e).__name__}: {e}")
        raise
    else:
        stage_end(run_id, stage, st.status, elapsed_s=time.time() - t0,
                  artifacts=st.artifacts, note=st.note)


# ----------------------------- 讀取與摺疊 -----------------------------

def load_events(path=None):
    """讀 runs.jsonl；壞行略過（帳本是紀錄，不因單行壞損就全掛）。"""
    p = Path(path or RUNS_JSONL)
    if not p.exists():
        return []
    out = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def fold(events):
    """把事件流摺疊成 {run_id: run}（純函式）。

    每個 run：kind / stages{stage: {status, started, ended, elapsed_s,
    done, total, unit, pid, artifacts, note, last_ts}} / run_status / last_ts。
    同一 (run, stage) 重跑時以**最後一次**為準。
    """
    runs = {}
    for ev in events:
        rid = ev.get("run_id")
        if not rid:
            continue
        r = runs.setdefault(rid, {"run_id": rid, "kind": "", "stages": {},
                                  "started": "", "last_ts": "", "log": "", "cmd": ""})
        if ev.get("ts"):
            r["last_ts"] = ev["ts"]
        if ev.get("kind"):
            r["kind"] = ev["kind"]
        if ev.get("log"):
            r["log"] = ev["log"]
        e = ev.get("event")
        stage = ev.get("stage")

        if e == "stage_start":
            st = {"stage": stage, "status": OPEN,
                  "started": ev.get("started_at") or ev.get("ts"),
                  "ended": "", "elapsed_s": None,
                  "done": 0, "total": ev.get("total") or 0, "unit": ev.get("unit", ""),
                  "pid": ev.get("pid"), "host": ev.get("host", ""),
                  "artifacts": [], "note": "", "last_ts": ev.get("ts")}
            r["stages"][stage] = st
            if not r["started"]:
                r["started"] = st["started"]
            if ev.get("cmd"):
                r["cmd"] = ev["cmd"]
        elif e == "progress" and stage in r["stages"]:
            st = r["stages"][stage]
            st["done"] = ev.get("done", st["done"])
            st["total"] = ev.get("total") or st["total"]
            if ev.get("unit"):
                st["unit"] = ev["unit"]
            st["last_ts"] = ev.get("ts", st["last_ts"])
        elif e == "stage_end":
            st = r["stages"].setdefault(stage, {
                "stage": stage, "started": "", "done": 0, "total": 0,
                "unit": "", "pid": None, "artifacts": []})
            st["status"] = ev.get("status", OK)
            st["ended"] = ev.get("ts")
            st["elapsed_s"] = ev.get("elapsed_s")
            st["artifacts"] = ev.get("artifacts") or st.get("artifacts") or []
            st["note"] = ev.get("note", "")
            st["last_ts"] = ev.get("ts", st.get("last_ts"))
        elif e == "run_end":
            r["run_status"] = ev.get("status", OK)
            r["ended"] = ev.get("ts")
            r["note"] = ev.get("note", "")
    return runs


def stage_order(names):
    """依 episode 五段順序排 stage 名稱（不在表上的排後面）。"""
    return sorted(names, key=lambda s: (STAGE_ORDER.index(s) if s in STAGE_ORDER else 99, s))


def run_status(run):
    """run 的整體狀態：running（有階段開著）/ fail / ok。"""
    if any(st.get("status") == OPEN for st in run["stages"].values()):
        return OPEN
    if run.get("run_status"):
        return run["run_status"]
    if any(st.get("status") == FAIL for st in run["stages"].values()):
        return FAIL
    return OK


def last_stage(run):
    """摺疊後最後有動靜的階段名稱（顯示用）。"""
    stages = [st for st in run["stages"].values() if st.get("last_ts")]
    if not stages:
        return ""
    return max(stages, key=lambda s: s["last_ts"])["stage"]


def pid_alive(pid):
    """行程是否還活著：True/False；無法判定回 None。"""
    if not pid:
        return None
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (ValueError, TypeError, OSError):
        return None
    return True


def age_min(ts, now=None):
    """時間字串距現在幾分鐘（float）；解不開回 None。"""
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    now = now or datetime.now().astimezone()
    if t.tzinfo is None:
        t = t.replace(tzinfo=now.tzinfo)
    return round((now - t).total_seconds() / 60, 1)


def current(events=None, now=None):
    """「現在」：所有開著（尚未 stage_end）的階段，附是否卡死。

    stale 判定：行程已不在（pid 查無）→ 一定卡死；pid 未知且閒置超過
    config.STATUS_STALE_MIN 分鐘 → 視為可疑。
    """
    evs = load_events() if events is None else events
    folded = fold(evs)
    now = now or datetime.now().astimezone()
    out = []
    for rid, r in folded.items():
        for st in r["stages"].values():
            if st.get("status") != OPEN:
                continue
            alive = pid_alive(st.get("pid"))
            idle = age_min(st.get("last_ts"), now)
            stale = (alive is False
                     or (alive is None and idle is not None and idle >= config.STATUS_STALE_MIN))
            out.append({
                "run_id": rid, "stage": st.get("stage"), "kind": r.get("kind", ""),
                "done": st.get("done", 0), "total": st.get("total", 0),
                "unit": st.get("unit", ""), "started": st.get("started", ""),
                "pid": st.get("pid"), "alive": alive, "idle_min": idle, "stale": stale,
                "log": r.get("log", ""),
            })
    out.sort(key=lambda x: x.get("started") or "")
    return out


def recent_runs(events=None, limit=8):
    """最近的 run 摘要（依最後動靜排序；給 status 用）。"""
    evs = load_events() if events is None else events
    folded = fold(evs)
    rows = []
    for rid, r in folded.items():
        rows.append({
            "run_id": rid, "kind": r.get("kind", ""), "status": run_status(r),
            "last_stage": last_stage(r), "started": r.get("started", ""),
            "ended": r.get("ended", ""), "last_ts": r.get("last_ts", ""),
            "log": r.get("log", ""),
            "stages": {n: st.get("status") for n, st in r["stages"].items()},
        })
    rows.sort(key=lambda x: x["last_ts"] or "", reverse=True)
    return rows[:limit] if limit else rows


def _snapshot():
    """把「現在」寫成 logs/.run_state.json（衍生物；寫不進去也不影響產線）。"""
    try:
        view = current()
        io.atomic_write_json(RUN_STATE, {"updated_at": now_iso(), "running": view})
    except Exception:
        pass


# ----------------------------- CLI -----------------------------

def _cli_ls(args):
    rows = recent_runs(limit=args.limit)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        print("（帳本尚無紀錄；跑一次產線或 `lofi runs begin …` 就會有）")
        return
    icon = {OPEN: "▶", OK: "✅", FAIL: "❌", SKIP: "⏭️"}
    for r in rows:
        stages = " ".join(f"{n}:{icon.get(r['stages'][n], '•')}"
                          for n in stage_order(r["stages"]))
        print(f"  {icon.get(r['status'], '•')} {r['run_id']:<20} {r['status']:<8} {stages}")


def main():
    import argparse

    ap = argparse.ArgumentParser(
        description="產線 run 帳本（catalog/runs.jsonl）：記錄 run × stage × 進度，給 status/notify 讀")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("begin", help="開始一個階段")
    b.add_argument("--run", required=True, help="run-id（＝episode／批次碼）")
    b.add_argument("--stage", required=True, help="階段名（music/image/video/upload/cleanup…）")
    b.add_argument("--kind", default="", help="run 類型（如 episode）")
    b.add_argument("--total", type=int, default=None, help="總量（如曲數），可省略")
    b.add_argument("--unit", default="", help="單位（如 tracks）")
    b.add_argument("--log", default="", help="log 檔路徑")
    b.add_argument("--cmd", dest="cmdline", default="", help="實際指令（追溯用）")
    b.add_argument("--pid", type=int, default=None,
                   help="階段的實際行程 pid（預設＝呼叫此指令的行程；供監控判存活）")

    p = sub.add_parser("progress", help="回報進度（批次邊界）")
    p.add_argument("--run", required=True)
    p.add_argument("--stage", required=True)
    p.add_argument("--done", type=int, required=True)
    p.add_argument("--total", type=int, default=None)
    p.add_argument("--unit", default="")

    e = sub.add_parser("end", help="結束一個階段")
    e.add_argument("--run", required=True)
    e.add_argument("--stage", required=True)
    e.add_argument("--status", default="ok", choices=["ok", "fail", "skip"])
    e.add_argument("--elapsed", type=float, default=None, help="耗時秒數")
    e.add_argument("--note", default="")
    e.add_argument("--artifact", action="append", default=[], help="產物路徑（可重複）")

    r = sub.add_parser("run-end", help="結束整個 run")
    r.add_argument("--run", required=True)
    r.add_argument("--status", default="ok", choices=["ok", "fail"])
    r.add_argument("--note", default="")

    ls = sub.add_parser("ls", help="列出最近的 run")
    ls.add_argument("--limit", type=int, default=10)
    ls.add_argument("--json", action="store_true")

    args = ap.parse_args()
    if args.cmd == "begin":
        # pid 預設＝呼叫者（shell 產線的腳本行程）：這個 pid 在整段期間都活著，
        # 監控才判得出「還在跑」還是「中斷」。一次性 CLI 自己的 pid 一寫就結束，不能用。
        ev = stage_start(args.run, args.stage, kind=args.kind, total=args.total,
                         unit=args.unit, log=args.log, cmd=args.cmdline,
                         pid=args.pid or os.getppid())
    elif args.cmd == "progress":
        ev = progress(args.run, args.stage, args.done, args.total, unit=args.unit)
    elif args.cmd == "end":
        ev = stage_end(args.run, args.stage, args.status, elapsed_s=args.elapsed,
                       artifacts=args.artifact, note=args.note)
    elif args.cmd == "run-end":
        ev = run_end(args.run, args.status, note=args.note)
    else:
        _cli_ls(args)
        return
    print(json.dumps(ev, ensure_ascii=False))


if __name__ == "__main__":
    main()
