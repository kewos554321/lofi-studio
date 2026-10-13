"""ComfyUI HTTP API 小客戶端（generate.py / visual.py 共用）。

以前兩個模組各寫一份 api/wait_done、各自硬編位址；現在集中在這裡，
位址由 lofi.config 提供（可用 LOFI_COMFY_URL 覆寫）。
"""
import json
import time
import urllib.request

from lofi.config import COMFY_URL


def api(url, path, payload=None, timeout=30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def system_stats(url=COMFY_URL, timeout=5):
    return api(url, "/system_stats", timeout=timeout)


def require_online(url=COMFY_URL):
    """確認 ComfyUI 在線；不在就中止並提示。回傳版本字串（可能為空）。"""
    import sys
    try:
        st = system_stats(url)
    except Exception as e:
        sys.exit(f"無法連線 ComfyUI ({url})：{e}\n請先執行 scripts/launch_comfyui.sh")
    return st.get("system", {}).get("comfyui_version", "")


def submit(url, prompt, client_id="lofi"):
    """送出工作，回傳 prompt_id。"""
    return api(url, "/prompt", {"prompt": prompt, "client_id": client_id})["prompt_id"]


def wait_done(url, pid, poll=5, on_tick=None):
    """輪詢直到工作完成，回傳 (result, 經過秒數)。

    暫時性的連線錯誤會重試；on_tick(elapsed) 可選（給呼叫端印進度）。
    """
    t0 = time.time()
    while True:
        try:
            h = api(url, f"/history/{pid}", timeout=15)
        except Exception:
            h = {}
        if h and pid in h:
            return h[pid], time.time() - t0
        if on_tick:
            on_tick(time.time() - t0)
        time.sleep(poll)
