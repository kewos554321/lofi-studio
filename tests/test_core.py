"""核心純函式測試。執行： python3 -m unittest discover -s tests -v"""
import json
import struct
import sys
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lofi import library, meta, qc, visual, visual_qc  # noqa: E402


def _png_with_prompt(path, workflow):
    """造一張只有 tEXt(prompt=…) 的最小 PNG（1x1），用來測 read_png_workflow / backfill。"""
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = b"\x00\xff\xff\xff"                       # filter 0 + 1px RGB
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
           + chunk(b"tEXt", b"prompt\x00" + json.dumps(workflow).encode())
           + chunk(b"IDAT", zlib.compress(raw))
           + chunk(b"IEND", b""))
    Path(path).write_bytes(png)
    return Path(path)


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


class TestVisualQC(unittest.TestCase):
    """visual_qc.judge 是純函式，用合成量測值驗判定（不需 ffmpeg）。"""

    @staticmethod
    def _m(w=1216, h=832, size=1319267, **stats):
        base = {"ymin": 34.0, "ylow": 56.0, "yavg": 110.0,
                "yhigh": 182.0, "ymax": 228.0, "satavg": 21.8, "satmax": 62.0}
        base.update(stats)
        return {"width": w, "height": h, "pix_fmt": "rgb24", "bytes": size,
                "decode_error": False, "stats": base, "entropy": 0.888}

    def test_normal_passes(self):
        r = visual_qc.judge(self._m(), expect_size=(1216, 832))
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(r["flags"], [])

    def test_black_frame_fails(self):
        r = visual_qc.judge(self._m(ymin=16, ylow=16, yavg=16, yhigh=16, ymax=16,
                                    satavg=0, satmax=0))
        self.assertEqual(r["verdict"], "fail")
        self.assertIn("black_frame", r["flags"])

    def test_white_frame_fails(self):
        r = visual_qc.judge(self._m(ymin=235, ylow=235, yavg=235, yhigh=235, ymax=235,
                                    satavg=0, satmax=0))
        self.assertIn("white_frame", r["flags"])

    def test_grayscale_fails(self):
        r = visual_qc.judge(self._m(satavg=0, satmax=0))
        self.assertEqual(r["verdict"], "fail")
        self.assertIn("grayscale", r["flags"])

    def test_flat_image_fails(self):
        m = self._m(yhigh=118, ylow=118)
        m["entropy"] = 0.0
        r = visual_qc.judge(m)
        self.assertIn("flat_image", r["flags"])

    def test_undecodable_fails(self):
        r = visual_qc.judge({"width": 0, "height": 0, "bytes": 0, "stats": {}})
        self.assertEqual(r["verdict"], "fail")
        self.assertIn("undecodable", r["flags"])

    def test_decode_error_fails(self):
        m = self._m()
        m["decode_error"] = True
        r = visual_qc.judge(m)
        self.assertIn("decode_error", r["flags"])

    def test_size_mismatch_warns(self):
        r = visual_qc.judge(self._m(), expect_size=(1536, 1024))
        self.assertEqual(r["verdict"], "warn")
        self.assertIn("size_mismatch", r["flags"])

    def test_suspiciously_small_warns_only_when_clean(self):
        r = visual_qc.judge(self._m(size=1000))
        self.assertIn("suspiciously_small", r["flags"])
        # 已有硬傷時不再補這條，避免雜訊
        r2 = visual_qc.judge(self._m(size=1000, ymax=16, satmax=0))
        self.assertNotIn("suspiciously_small", r2["flags"])

    def test_parse_size(self):
        self.assertEqual(visual_qc._parse_size("1536x1024"), (1536, 1024))


class TestVisualNaming(unittest.TestCase):
    """命名規則：資料夾 <kind><NN>、檔名 <run-id>_<NN>[_<slug>].<ext>。
    規則說明見 docs/VISUAL_NAMING.md。"""

    def test_output_filename(self):
        self.assertEqual(visual.output_filename("ds02", 7, "walk-side-fireplace"),
                         "ds02_07_walk-side-fireplace.png")
        self.assertEqual(visual.output_filename("ref05", 1), "ref05_01.png")      # slug 可省
        self.assertEqual(visual.output_filename("ref01", 11, "anim-blink", "mp4"),
                         "ref01_11_anim-blink.mp4")
        self.assertEqual(visual.output_filename("ds02", 3, "Walk Side"),
                         "ds02_03_walk-side.png")                             # 正規化

    def test_sanitize_slug(self):
        # 中文/括號/底線都要清掉，只留小寫英數與 '-'
        self.assertEqual(visual.sanitize_slug("_cmp_base_原圖(睜眼)"), "cmp-base")
        self.assertEqual(visual.sanitize_slug("a/b:c"), "a-b-c")
        self.assertEqual(visual.sanitize_slug("..."), "")
        self.assertEqual(visual.output_filename("t01", 1, "a/b:c"), "t01_01_a-b-c.png")

    def test_parse_output_name(self):
        name = visual.output_filename("ds02", 7, "walk-side")
        self.assertEqual(visual.parse_output_name(name, "ds02"), ("ds02", 7, "walk-side", "png"))
        self.assertEqual(visual.parse_output_name("ref05_01.png", "ref05"),
                         ("ref05", 1, "", "png"))
        # 舊的（不合規則的）檔名不該被誤認
        self.assertIsNone(visual.parse_output_name("cat08_lora_ds01_00001_.png", "ds02"))

    def test_parse_run_id(self):
        self.assertEqual(visual.parse_run_id("ref05"), ("ref", 5))
        self.assertEqual(visual.parse_run_id("ds02"), ("ds", 2))
        self.assertEqual(visual.parse_run_id("20261013-1530"), ("", 0))   # 時間戳不是 kind
        self.assertEqual(visual.parse_run_id(""), ("", 0))

    def test_next_run_id_auto_numbers(self):
        """未給 --run-id 時自動編號：ref05 之後給 ref06（不用自己記數）。"""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            for n in ("ref01", "ref05", "ds02", "sc03"):
                (p / n).mkdir()
            self.assertEqual(visual.next_run_id("ref", p), "ref06")
            self.assertEqual(visual.next_run_id("ds", p), "ds03")
            self.assertEqual(visual.next_run_id("sc", p), "sc04")
            self.assertEqual(visual.next_run_id("ref", p / "nonexistent"), "ref01")

    def test_next_seq_continues_from_existing(self):
        """同一 run 再跑時序號要往後接，不能蓋掉既有檔。"""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            for n in ("ds02_01_a.png", "ds02_03_b.png", "other_09_c.png", "manifest.json"):
                (p / n).write_bytes(b"")
            self.assertEqual(visual.next_seq(p, "ds02"), 4)
            self.assertEqual(visual.next_seq(p), 10)
            self.assertEqual(visual.next_seq(p / "nonexistent", "ds02"), 1)

    def test_kinds_are_documented(self):
        self.assertEqual(visual.VIS_KINDS, ("ref", "ds", "sc"))
        self.assertEqual(visual.VIS_KIND_LABELS["ref"], "定裝參考")

    def test_age_text(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(visual.age_text((now - timedelta(minutes=5)).isoformat(), now), "5 分前")
        self.assertEqual(visual.age_text((now - timedelta(hours=3)).isoformat(), now), "3 小時前")
        self.assertEqual(visual.age_text((now - timedelta(days=2)).isoformat(), now), "2 天前")
        self.assertEqual(visual.age_text(""), "")
        self.assertEqual(visual.age_text("不是時間"), "")

    def test_pad_counts_cjk_as_two_cells(self):
        """表格要對齊：中文算兩格。"""
        self.assertEqual(visual._disp_width("定裝參考"), 8)
        self.assertEqual(visual._disp_width("ref01"), 5)
        self.assertEqual(visual._disp_width(visual._pad("定裝參考", 12)), 12)
        self.assertEqual(visual._disp_width(visual._pad("訓練集", 12)), 12)
        self.assertEqual(visual._pad("7", 5, right=True), "    7")

    def test_run_rows_reads_manifest(self):
        """總表要讀得到用途／時間／模型／張數。"""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            vd = Path(d)
            r = vd / "ref01"
            r.mkdir()
            (r / "ref01_01_x.png").write_bytes(b"")
            visual.update_manifest(r, run_id="ref01", tool="z-image-turbo-gguf",
                                   config={"unet": "z.gguf"},
                                   images=[{"file": "ref01_01_x.png", "seed": 1}])
            rows = visual.run_rows(vd)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["run_id"], "ref01")
            self.assertEqual(rows[0]["kind"], "ref")
            self.assertEqual(rows[0]["model"], "z.gguf")
            self.assertEqual(rows[0]["images"], 1)
            self.assertTrue(rows[0]["created_at"])

    def test_backfill_uses_file_time_not_now(self):
        """回填的 created_at 要用圖檔時間，不能寫成『現在』（不然看不出是哪時候跑的）。"""
        import os
        import tempfile
        import time
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ref09"                 # run-id 取自資料夾名
            p.mkdir()
            wf = {
                "4": {"class_type": "CheckpointLoaderSimple",
                      "inputs": {"ckpt_name": "A.safetensors"}},
                "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat"}},
                "3": {"class_type": "KSampler",
                      "inputs": {"seed": 7, "steps": 8, "cfg": 2.0, "sampler_name": "euler",
                                 "scheduler": "simple", "positive": ["6", 0]}},
            }
            img = _png_with_prompt(p / "ref09_01_cat.png", wf)
            old = time.time() - 7 * 86400
            os.utime(img, (old, old))
            self.assertEqual(visual.backfill_manifest(p), 1)
            m = visual.load_manifest(p)
            self.assertEqual((m["run_id"], m["tool"]), ("ref09", "lofi.visual"))
            self.assertEqual(m["config"]["ckpt"], "A.safetensors")
            self.assertEqual(m["images"][0]["seed"], 7)
            self.assertEqual(m["images"][0]["prompt"], "a cat")
            self.assertEqual(m["created_at"][:10],
                             datetime.fromtimestamp(old).astimezone().strftime("%Y-%m-%d"))

    def test_read_png_workflow_bad_input(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "not.png"
            f.write_bytes(b"hello")
            self.assertIsNone(visual.read_png_workflow(f))
            self.assertIsNone(visual.read_png_workflow(Path(d) / "missing.png"))


class TestVisualRunDir(unittest.TestCase):
    """一 run 一資料夾：run-id 清洗 + manifest 寫入是純函式，不需 ComfyUI。"""

    def test_sanitize_run_id(self):
        self.assertEqual(visual.sanitize_run_id("cat01"), "cat01")
        self.assertEqual(visual.sanitize_run_id("cat 01"), "cat-01")
        self.assertEqual(visual.sanitize_run_id("cat/01"), "cat01")
        # 路徑跳脫必須被清掉：不能留下 / 或 \，也不能是 . / ..
        for bad in ["../etc", "a/../../b", "..", ".", "/tmp/x"]:
            got = visual.sanitize_run_id(bad)
            self.assertNotIn("/", got)
            self.assertNotIn("\\", got)
            self.assertNotIn(got, (".", ".."))
        self.assertEqual(visual.sanitize_run_id(""), "")

    def test_write_manifest(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            visual.write_manifest(p, {"run_id": "t01", "images": []})
            got = json.loads((p / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(got["run_id"], "t01")
            self.assertEqual(got["images"], [])

    def test_manifest_shared_config_and_appended_images(self):
        """一個 run 一份：固定配置只存一次，同 run 再跑只是把新檔併進 images。"""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            visual.update_manifest(p, run_id="r01", tool="lofi.visual",
                                   config={"ckpt": "A.safetensors", "steps": 28, "negative": "blurry"},
                                   images=[{"file": "a.png", "seed": 1, "prompt": "cat A"}])
            m = visual.update_manifest(p, run_id="r01", tool="lofi.visual",
                                       config={"steps": 30, "negative": ""},   # 空值不覆蓋
                                       images=[{"file": "b.png", "seed": 2, "prompt": "cat B"}])
            self.assertEqual(m["manifest_version"], 2)
            self.assertEqual(m["config"]["steps"], 30)           # 新值覆蓋
            self.assertEqual(m["config"]["negative"], "blurry")  # 空字串不覆蓋
            self.assertEqual([e["file"] for e in m["images"]], ["a.png", "b.png"])

    def test_manifest_prompt_editable_per_file(self):
        """改某一張的 prompt 只動那一筆。"""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            visual.update_manifest(p, run_id="r01", images=[
                {"file": "a.png", "seed": 1, "prompt": "old"},
                {"file": "b.png", "seed": 2, "prompt": "keep"}])
            m = visual.update_manifest(p, run_id="r01", images=[{"file": "a.png", "prompt": "new"}])
            self.assertEqual({e["file"]: e["prompt"] for e in m["images"]},
                             {"a.png": "new", "b.png": "keep"})
            self.assertEqual(m["images"][0]["seed"], 1)          # 沒給的欄位不動

    def test_upgrade_legacy_flat_manifest(self):
        """舊扁平格式升級：配置收進 config、prompt 下放到 images。"""
        up = visual.upgrade_manifest({
            "run_id": "old", "tool": "z-image-turbo-gguf", "unet": "z.gguf",
            "steps": 8, "negative": "bad", "prompt": "a watercolor cat",
            "images": [{"file": "x.png", "seed": 9}]})
        self.assertEqual(up["manifest_version"], 2)
        self.assertEqual(up["config"], {"unet": "z.gguf", "steps": 8, "negative": "bad"})
        self.assertNotIn("prompt", up)
        self.assertEqual(up["images"][0]["prompt"], "a watercolor cat")
        self.assertEqual(up["images"][0]["seed"], 9)

    def test_manifest_from_workflow(self):
        """從 ComfyUI workflow 反推：模型/參數進 config，seed/prompt 逐檔。"""
        wf = {
            "4": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": "DreamShaperXL.safetensors"}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat"}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry"}},
            "3": {"class_type": "KSampler",
                  "inputs": {"seed": 42, "steps": 28, "cfg": 6.5, "sampler_name": "dpmpp_2m",
                             "scheduler": "karras", "denoise": 1.0,
                             "positive": ["6", 0], "negative": ["7", 0]}},
        }
        cfg, entry = visual.manifest_from_workflow(wf)
        self.assertEqual(cfg["ckpt"], "DreamShaperXL.safetensors")
        self.assertEqual((cfg["steps"], cfg["cfg"], cfg["sampler"]), (28, 6.5, "dpmpp_2m"))
        self.assertEqual(cfg["negative"], "blurry")
        self.assertEqual(entry, {"seed": 42, "prompt": "a cat"})
        self.assertEqual(visual.guess_tool(wf), "lofi.visual")

    def test_run_header_shows_model(self):
        """執行資訊要看得出來「哪個模型在跑」。"""
        cfg = {"ckpt": "DreamShaperXL-v1.0-Alpha2.safetensors", "steps": 28}
        self.assertEqual(visual.model_tag(cfg), "DreamShaperXL-v1.0-Alpha2")
        self.assertIn("checkpoint", dict(visual.format_run_fields(cfg)))
        self.assertEqual(visual.model_tag({"unet": "z-image-turbo-Q5_K_M.gguf"}), "z-image-turbo-Q5_K_M")
        self.assertNotIn("權重起點", dict(visual.format_run_fields({"start": None})))
        self.assertIn("權重起點", dict(visual.format_run_fields({"start": 0.0})))  # 0.0 是有效值


class TestAudioQC(unittest.TestCase):
    """qc.judge 是純函式（量測與判定分開），用合成量測值驗判定（不需 ffmpeg）。"""

    @staticmethod
    def _m(**over):
        m = {
            "duration": 120.0,
            "loud": {"lufs": -14.0, "lra": 6.0, "true_peak": -1.0},
            "stats": {"dc_offset": 0.0, "peak_db": -1.5, "rms_db": -18.0,
                      "flat_factor": 0.0, "zcr": 0.1, "peak_count": 3},
            "silence": 0.02,
            "high": {"rms_db": -40.0},
            "bpm_est": 72.0,
            "fingerprint": None,
        }
        for k, v in over.items():
            if isinstance(v, dict) and isinstance(m.get(k), dict):
                m[k] = {**m[k], **v}
            else:
                m[k] = v
        return m

    def test_normal_passes(self):
        r = qc.judge(self._m())
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(r["flags"], [])
        self.assertEqual(r["score"], 100)

    def test_clipping_fails(self):
        r = qc.judge(self._m(loud={"true_peak": 3.0}))
        self.assertEqual(r["verdict"], "fail")
        self.assertIn("clipping", r["flags"])

    def test_hot_true_peak_warns(self):
        r = qc.judge(self._m(loud={"true_peak": 0.5}))
        self.assertEqual(r["verdict"], "warn")
        self.assertIn("hot_true_peak", r["flags"])

    def test_loudness_out_of_range_warns(self):
        self.assertIn("loudness_out_of_range", qc.judge(self._m(loud={"lufs": -25.0}))["flags"])

    def test_truncated_fails(self):
        r = qc.judge(self._m(duration=100.0), requested_duration=200.0)
        self.assertEqual(r["verdict"], "fail")
        self.assertIn("truncated", r["flags"])

    def test_near_silent_fails(self):
        r = qc.judge(self._m(stats={"rms_db": -60.0}))
        self.assertIn("near_silent", r["flags"])

    def test_mostly_silent_fails(self):
        self.assertIn("mostly_silent", qc.judge(self._m(silence=0.6))["flags"])

    def test_trailing_silence_warns(self):
        self.assertIn("trailing_silence", qc.judge(self._m(silence=0.3))["flags"])

    def test_dc_offset_warns(self):
        self.assertIn("dc_offset", qc.judge(self._m(stats={"dc_offset": 0.05}))["flags"])

    def test_harsh_highs_warns(self):
        self.assertIn("harsh_highs", qc.judge(self._m(high={"rms_db": -8.0}))["flags"])

    def test_wide_dynamics_warns(self):
        self.assertIn("wide_dynamics", qc.judge(self._m(stats={"peak_db": 5.0}))["flags"])

    def test_measure_and_judge_are_separate(self):
        """judge 只吃量測 dict；analyze 才是 I/O 進入點。"""
        self.assertTrue(callable(qc.measure))
        self.assertTrue(callable(qc.judge))
        self.assertTrue(callable(qc.analyze))

    def test_measure_failure_is_error_not_pass(self):
        """工具壞/檔壞以致量不到時，不可判 pass（曾發生 ffmpeg 失敗→全綠）。"""
        r = qc.judge({"error": "no_measurement"})
        self.assertEqual(r["verdict"], "error")
        self.assertIn("measure_failed", r["flags"])
        self.assertIsNone(r["score"])
        # 沒有 error 欄位、但關鍵量測全 None，也要判 error
        r2 = qc.judge({"duration": None, "loud": {}, "stats": {}})
        self.assertEqual(r2["verdict"], "error")

    def test_missing_tools_is_error(self):
        r = qc.judge({"error": "missing_tools"})
        self.assertEqual(r["verdict"], "error")
        self.assertIn("missing_tools", r["flags"])


class TestComfyWait(unittest.TestCase):
    """wait_done 不可無限等待（ComfyUI 卡死/斷線要能丟錯）。"""

    def test_gives_up_after_max_errors(self):
        from lofi import comfy
        orig = comfy.api
        comfy.api = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            with self.assertRaises(comfy.ComfyError):
                comfy.wait_done("http://x", "pid", poll=0, max_errors=3)
        finally:
            comfy.api = orig

    def test_returns_result_when_done(self):
        from lofi import comfy
        orig = comfy.api
        comfy.api = lambda *a, **k: {"pid": {"status": {"status_str": "success"}}}
        try:
            res, dt = comfy.wait_done("http://x", "pid", poll=0)
            self.assertEqual(res["status"]["status_str"], "success")
        finally:
            comfy.api = orig



if __name__ == "__main__":
    unittest.main()
