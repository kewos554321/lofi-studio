#!/usr/bin/env python3
"""相容 shim：實作已移至 src/lofi/generate.py。等價於 `lofi generate`。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from lofi.generate import main  # noqa: E402

if __name__ == "__main__":
    main()
