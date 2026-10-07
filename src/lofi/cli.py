#!/usr/bin/env python3
"""lofi — 單一 CLI 入口。

用法:
  lofi generate  [batch_generate 參數]
  lofi qc        [auto_qc 參數]
  lofi library   build | query | summary | tag | set-status
  lofi meta      [make_meta 參數]
  lofi publish   [upload_status 參數]
  lofi backfill  [backfill_catalog 參數]
  lofi migrate   [遷移音檔佈局]
  lofi expand    [expand_style 參數]
  lofi visual    [generate_visual 參數]
  lofi cinemagraph [make_cinemagraph 參數]（需 numpy，用 .venv）

未安裝套件時也可用: PYTHONPATH=src python3 -m lofi.cli <command> ...
"""
import importlib
import sys

# 延遲 import：避免 `lofi qc` 之類不需 numpy 的指令被 cinemagraph 拖累
MODULES = {
    "generate":    ("lofi.generate",    "用 ComfyUI 批次生成音樂"),
    "qc":          ("lofi.qc",          "自動品檢（響度/削波/靜音/時長）"),
    "library":     ("lofi.library",     "SQLite 圖書館 + 自動標籤"),
    "meta":        ("lofi.meta",        "產生 YouTube 上片資訊"),
    "publish":     ("lofi.publish",     "上片佇列"),
    "backfill":    ("lofi.backfill",    "回填既有音檔到目錄"),
    "migrate":     ("lofi.migrate",     "音檔佈局遷移（扁平 <-> 分層）"),
    "expand":      ("lofi.expand",      "展開風格曲目清單（CSV）"),
    "visual":      ("lofi.visual",      "用 ComfyUI 生場景圖"),
    "cinemagraph": ("lofi.cinemagraph", "靜圖 -> 無縫循環局部微動（需 numpy）"),
}


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print("用法: lofi <command> [options]\n\ncommands:")
        for name, (_, desc) in MODULES.items():
            print(f"  {name:<12} {desc}")
        return
    cmd = argv[0]
    if cmd not in MODULES:
        sys.exit(f"未知指令: {cmd}\n可用: {', '.join(MODULES)}")
    mod = importlib.import_module(MODULES[cmd][0])
    sys.argv = [f"lofi {cmd}"] + argv[1:]
    mod.main()


if __name__ == "__main__":
    main()
