"""lofi-studio — 本機 AI lofi 音樂／影片產線。

模組：
  paths     路徑解析
  catalog   音樂目錄（sidecar JSON + JSONL）
  qc        自動品檢
  generate  批次生成（ComfyUI）
  backfill  回填既有音檔
  meta      YouTube 上片資訊
  publish   上片佇列
  library   SQLite 圖書館 + 自動標籤
  cli       單一入口 `lofi`
"""

__version__ = "0.1.0"
