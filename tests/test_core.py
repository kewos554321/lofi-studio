"""核心純函式測試。執行： python3 -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lofi import library, meta  # noqa: E402


class TestMetaFormat(unittest.TestCase):
    def test_fmt_ts(self):
        self.assertEqual(meta.fmt_ts(0), "00:00")
        self.assertEqual(meta.fmt_ts(112), "01:52")
        self.assertEqual(meta.fmt_ts(3661), "01:01:01")

    def test_prettify(self):
        self.assertEqual(meta.prettify("rain on the window"), "Rain on the Window")
        self.assertEqual(meta.prettify("gentle start of the day"), "Gentle Start of the Day")

    def test_timeline_crossfade(self):
        orig = meta.ffprobe_duration
        meta.ffprobe_duration = lambda p: 120.0
        try:
            tl = meta.compute_timeline(["a", "b", "c"], 8.0)
        finally:
            meta.ffprobe_duration = orig
        self.assertEqual([t["start"] for t in tl], [0.0, 112.0, 224.0])
        self.assertEqual(meta.fmt_ts(tl[-1]["start"] + tl[-1]["duration"]), "05:44")


class TestLibraryTags(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(library.slug("A minor"), "a_minor")
        self.assertEqual(library.slug("F# minor"), "f__minor")

    def test_auto_tags(self):
        rec = {
            "style": "rainy_lofi", "key": "A minor", "bpm": 70,
            "prompt": "lofi hip hop, no vocals, mellow",
            "auto_qc": {"verdict": "pass", "rms_db": -14.5, "high_band_db": -22.0},
            "review": {"rating": 5},
        }
        tags = library.auto_tags(rec)
        for t in ("style:rainy_lofi", "bpm:mid", "energy:loud",
                  "brightness:neutral", "verdict:pass", "rating:5", "instrumental"):
            self.assertIn(t, tags)


if __name__ == "__main__":
    unittest.main()
