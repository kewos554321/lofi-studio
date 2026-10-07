#!/usr/bin/env python3
"""相容 shim：實作在 src/lofi/upload.py。等價於 `lofi upload`。

需要 Google 套件（.venv）：建議用 .venv/bin/python 執行。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from lofi.upload import main  # noqa: E402

if __name__ == "__main__":
    main()
