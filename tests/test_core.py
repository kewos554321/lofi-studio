"""核心純函式測試。執行： python3 -m unittest discover -s tests -v"""
import argparse
import json
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lofi import (backfill, catalog, character, config, expand, generate, io,  # noqa: E402
                  library, media, meta, qc, release, upload, visual, visual_qc)


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
    規則說明見 docs/NAMING.md。"""

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


class TestVisualIPAdapter(unittest.TestCase):
    """--ref（IPAdapter 鎖角色）的節點接線與紀錄往返：純函式，不需 ComfyUI。"""

    @staticmethod
    def _wf(**over):
        kw = dict(pos="a black cat", neg="blurry", w=832, h=1216, seed=7, steps=28,
                  cfg=6.5, prefix="p", ckpt="DreamShaperXL-v1.0-Alpha2.safetensors",
                  ref_image="mascot_blackcat_ref.png", ipa_weight=0.6)
        kw.update(over)
        return visual.build_prompt(**kw)

    def test_without_ref_keeps_plain_sampler(self):
        g = self._wf(ref_image="")
        self.assertNotIn("18", g)                       # 沒 --ref 就不掛 IPAdapter 節點
        self.assertEqual(g["3"]["inputs"]["model"], ["4", 0])

    def test_ref_wires_ipadapter_chain(self):
        g = self._wf()
        self.assertEqual(g["18"]["class_type"], "IPAdapterAdvanced")
        self.assertEqual(g["18"]["inputs"]["model"], ["16", 0])     # 接在 UnifiedLoader 之後
        self.assertEqual(g["18"]["inputs"]["image"], ["17", 0])
        self.assertEqual(g["17"]["inputs"]["image"], "mascot_blackcat_ref.png")
        self.assertEqual(g["18"]["inputs"]["weight"], 0.6)
        self.assertEqual(g["3"]["inputs"]["model"], ["18", 0])      # KSampler 吃 IPAdapter 的模型

    def test_ref_roundtrips_through_manifest(self):
        """產圖後從 workflow 反推要能還原 ref/weight（backfill 與 tool 判定才對得上）。"""
        g = self._wf(ipa_weight=0.65)
        cfg, entry = visual.manifest_from_workflow(g)
        self.assertEqual(cfg["ref"], "mascot_blackcat_ref.png")
        self.assertEqual(cfg["weight"], 0.65)
        self.assertEqual(cfg["preset"], "PLUS (high strength)")
        self.assertEqual((entry["seed"], entry["prompt"]), (7, "a black cat"))
        self.assertEqual(visual.guess_tool(g), "sdxl-ipadapter")

    def test_ipadapter_models_are_registered(self):
        """--ref 會一併查 IPAdapter 模型授權，這兩支沒登錄就會被 fail-closed 擋掉產圖。"""
        for name in config.IPADAPTER_MODELS:
            self.assertTrue(config.model_license(name)["commercial"], name)


class TestVisualInpaint(unittest.TestCase):
    """局部重繪（--init-image + --mask）的節點接線與紀錄往返：純函式，不需 ComfyUI。"""

    @staticmethod
    def _wf(**over):
        kw = dict(pos="a black cat", neg="blurry", w=832, h=1216, seed=7, steps=28,
                  cfg=6.5, prefix="p", ckpt="DreamShaperXL-v1.0-Alpha2.safetensors",
                  ref_image="")                       # 預設不掛 IPAdapter，單獨看 inpaint
        kw.update(over)
        return visual.build_prompt(**kw)

    def test_init_image_without_mask_is_plain_img2img(self):
        g = self._wf(init_image="base.png")
        self.assertEqual(g["15"]["class_type"], "VAEEncode")
        self.assertNotIn("19", g)                        # 沒遮罩就不掛遮罩鏈
        self.assertEqual(g["3"]["inputs"]["latent_image"], ["15", 0])

    def test_mask_wires_inpaint_chain(self):
        g = self._wf(init_image="base.png", mask_image="m.png", grow_mask=12, grow_mask_by=4)
        self.assertEqual(g["15"]["class_type"], "VAEEncodeForInpaint")
        self.assertEqual(g["15"]["inputs"]["pixels"], ["14", 0])       # 起點圖
        self.assertEqual(g["15"]["inputs"]["mask"], ["21", 0])
        self.assertEqual(g["15"]["inputs"]["grow_mask_by"], 4)
        self.assertEqual(g["20"]["class_type"], "ImageToMask")
        self.assertEqual(g["20"]["inputs"]["image"], ["19", 0])
        self.assertEqual(g["21"]["inputs"]["expand"], 12)
        self.assertEqual(g["3"]["inputs"]["latent_image"], ["15", 0])

    def test_inpaint_roundtrips_through_manifest(self):
        """反推要能還原起點圖／遮罩／擴張值（改遮罩設定才看得出哪批重繪過什麼）。"""
        g = self._wf(init_image="base.png", mask_image="m.png", grow_mask=12,
                     grow_mask_by=4, mask_channel="green", denoise=0.9)
        cfg, _ = visual.manifest_from_workflow(g)
        self.assertEqual(cfg["init_image"], "base.png")
        self.assertEqual(cfg["mask"], "m.png")
        self.assertEqual(cfg["grow_mask"], 12)
        self.assertEqual(cfg["grow_mask_by"], 4)
        self.assertEqual(cfg["mask_channel"], "green")
        self.assertIn("遮罩擴張", dict(visual.format_run_fields(cfg)))

    def test_inpaint_and_character_lock_together(self):
        """「重畫成定裝角色」＝ inpaint ＋ IPAdapter 同時用（白襪鎖不住的正解）。"""
        g = self._wf(init_image="base.png", mask_image="m.png",
                     ref_image="mascot_blackcat_ref.png")
        self.assertEqual(g["15"]["class_type"], "VAEEncodeForInpaint")
        self.assertEqual(g["3"]["inputs"]["model"], ["18", 0])         # 仍走 IPAdapter
        self.assertEqual(g["3"]["inputs"]["latent_image"], ["15", 0])


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



class TestIO(unittest.TestCase):
    """原子寫入 / append_jsonl / 壞檔隔離。"""

    def test_atomic_write_json_no_tmp_left(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "a.json"
            io.atomic_write_json(p, {"k": "值"})
            self.assertTrue(p.exists())
            self.assertFalse((p.with_name(p.name + ".tmp")).exists())
            self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["k"], "值")

    def test_append_jsonl_multiple_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "e.jsonl"
            io.append_jsonl(p, {"a": 1})
            io.append_jsonl(p, {"a": 2})
            lines = p.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual([json.loads(x)["a"] for x in lines], [1, 2])

    def test_quarantine_keeps_bad_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.mp3.json"
            p.write_text("{ not json", encoding="utf-8")
            dest = io.quarantine(p, why="test", log=False)
            self.assertFalse(p.exists())
            self.assertTrue(dest.exists())
            self.assertIn(".corrupt-", dest.name)

    def test_load_sidecar_quarantines_corrupt(self):
        """壞掉的 sidecar 不可被當成新檔覆蓋——要留下 .corrupt-。"""
        with tempfile.TemporaryDirectory() as d:
            audio = Path(d) / "a.mp3"
            audio.write_bytes(b"")
            catalog.sidecar_path(audio).write_text("{ bad", encoding="utf-8")
            self.assertIsNone(catalog.load_sidecar(audio))
            self.assertEqual(len(list(Path(d).glob("*.corrupt-*"))), 1)


class TestDiskFree(unittest.TestCase):
    """磁碟空間 preflight（Python 端，原本只有 shell 有）。"""

    def test_free_gb_positive(self):
        self.assertGreater(media.free_gb("."), 0)

    def test_free_gb_nonexistent_falls_back_to_ancestor(self):
        self.assertGreater(media.free_gb("/no/such/path/here/deep"), 0)

    def test_require_free_passes_when_room(self):
        media.require_free(".", 0.0001, "test")   # 不應中止

    def test_require_free_exits_when_full(self):
        with self.assertRaises(SystemExit):
            media.require_free(".", 10 ** 9, "test")   # 要求 10 億 GB


class TestMediaProbe(unittest.TestCase):
    """ffprobe 尺寸：img2img/inpaint 的 manifest 要記真實尺寸，不能記沒用到的 --size。"""

    def test_ffprobe_size_reads_image(self):
        with tempfile.TemporaryDirectory() as d:
            p = _png_with_prompt(Path(d) / "one.png", {})     # 1x1 的最小 PNG
            self.assertEqual(media.ffprobe_size(p), (1, 1))

    def test_ffprobe_size_on_garbage_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "not.png"
            bad.write_bytes(b"not an image")
            self.assertIsNone(media.ffprobe_size(bad))

    def test_ffprobe_size_missing_file_is_none(self):
        self.assertIsNone(media.ffprobe_size("/no/such/image.png"))


class TestCatalogLayer(unittest.TestCase):
    """catalog 資料層：sidecar 生命週期 / 事件流 / 路徑。

    P2-11 改過寫入路徑（原子寫 + 隔離 + 鎖），這層需要回歸測試。
    以 temp 目錄取代 ROOT，不碰真實 assets/。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "assets" / "tracks").mkdir(parents=True)
        (self.root / "catalog").mkdir()
        self._saved = {k: getattr(catalog, k) for k in
                       ("ROOT", "TRACKS_DIR", "CATALOG_DIR", "JSONL_PATH", "INDEX_CSV")}
        catalog.ROOT = self.root
        catalog.TRACKS_DIR = self.root / "assets" / "tracks"
        catalog.CATALOG_DIR = self.root / "catalog"
        catalog.JSONL_PATH = catalog.CATALOG_DIR / "tracks.jsonl"
        catalog.INDEX_CSV = catalog.CATALOG_DIR / "index.csv"

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(catalog, k, v)
        self.tmp.cleanup()

    def _audio(self, rel="rainy_lofi/run01/song_01.mp3"):
        p = catalog.TRACKS_DIR / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"ID3")
        return p

    def test_upsert_load_roundtrip(self):
        audio = self._audio()
        catalog.upsert_track(audio, {"style": "rainy_lofi", "track_id": "t-1", "tags": ["a"]})
        sc = catalog.load_sidecar(audio)
        self.assertEqual(sc["track_id"], "t-1")
        self.assertEqual(sc["style"], "rainy_lofi")
        self.assertEqual(sc["file"], "assets/tracks/rainy_lofi/run01/song_01.mp3")
        self.assertIn("updated_at", sc)
        self.assertEqual(sc["schema_version"], catalog.SCHEMA_VERSION)

    def test_upsert_defaults_track_id_and_file(self):
        audio = self._audio("misc/run/song.mp3")
        catalog.upsert_track(audio, {})
        sc = catalog.load_sidecar(audio)
        self.assertEqual(sc["track_id"], "song")
        self.assertEqual(sc["file"], "assets/tracks/misc/run/song.mp3")

    def test_sidecar_written_atomically(self):
        audio = self._audio()
        catalog.upsert_track(audio, {"style": "x"})
        p = catalog.sidecar_path(audio)
        self.assertTrue(p.exists())
        json.loads(p.read_text(encoding="utf-8"))                      # 合法 JSON
        self.assertFalse(p.with_name(p.name + ".tmp").exists())        # 無 tmp 殘留

    def test_append_event_fields(self):
        ev = catalog.append_event("generated", {"track_id": "t1", "gen": {"seed": 7}})
        self.assertEqual((ev["event"], ev["track_id"], ev["seed"]), ("generated", "t1", 7))
        self.assertIn("ts", ev)
        self.assertEqual(ev["schema_version"], catalog.SCHEMA_VERSION)
        ev2 = catalog.append_event("tag", "t2")      # 非 dict → 當 track_id
        self.assertEqual(ev2["track_id"], "t2")

    def test_event_is_compact_not_snapshot(self):
        """事件只記發生什麼，不是整包快照（避免 tracks.jsonl 單調膨脹）。"""
        rec = {"track_id": "t1", "file": "a.mp3", "style": "x", "prompt": "p",
               "auto_qc": {"verdict": "pass", "score": 90, "flags": [],
                           "lufs": -14.0, "fingerprint": [0.1] * 40}}
        ev = catalog.append_event("auto_qc", rec)
        self.assertEqual(ev["verdict"], "pass")
        self.assertEqual(ev["score"], 90)
        self.assertNotIn("fingerprint", ev)      # 大欄位不進事件
        self.assertNotIn("auto_qc", ev)          # 不再是整包快照
        self.assertNotIn("prompt", ev)           # auto_qc 事件不需要 prompt

    def test_event_drops_empty_values(self):
        ev = catalog.append_event("generated", {"track_id": "t1", "style": "", "gen": {"seed": 7}})
        self.assertNotIn("style", ev)
        self.assertEqual(ev["seed"], 7)

    def test_event_stream_lines_parse(self):
        catalog.append_event("generated", {"track_id": "t1"})
        catalog.append_event("auto_qc", {"track_id": "t1", "verdict": "pass"})
        lines = catalog.JSONL_PATH.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual([json.loads(x)["event"] for x in lines], ["generated", "auto_qc"])

    def test_track_subdir_cleans_names(self):
        self.assertEqual(catalog.track_subdir("rainy lofi", "run/1"),
                         catalog.TRACKS_DIR / "rainy_lofi" / "run_1")
        self.assertEqual(catalog.track_subdir("", ""), catalog.TRACKS_DIR / "misc" / "legacy")

    def test_iter_audio_files_recursive_sorted(self):
        self._audio("b/x.wav")
        self._audio("a/y.mp3")
        (catalog.TRACKS_DIR / "a" / "note.txt").write_text("x", encoding="utf-8")
        self.assertEqual([p.name for p in catalog.iter_audio_files()], ["y.mp3", "x.wav"])

    def test_is_audio(self):
        self.assertTrue(catalog.is_audio("a.MP3"))
        self.assertFalse(catalog.is_audio("a.txt"))

    def test_relpath_inside_and_outside(self):
        self.assertEqual(catalog.relpath(self.root / "catalog" / "x.json"), "catalog/x.json")
        outside = self.root.parent / "nope.mp3"
        self.assertEqual(catalog.relpath(outside), str(outside))

    def test_sha1_stable(self):
        p = self.root / "f.bin"
        p.write_bytes(b"hello")
        self.assertEqual(catalog.sha1_file(p), catalog.sha1_file(p))

    def test_style_version_reads_json(self):
        (self.root / "prompts" / "styles").mkdir(parents=True)
        (self.root / "prompts" / "styles" / "rainy.json").write_text('{"version": 7}', encoding="utf-8")
        self.assertEqual(catalog.style_version("rainy"), 7)
        self.assertIsNone(catalog.style_version("nope"))
        self.assertIsNone(catalog.style_version(""))

    def test_load_catalog_keyed_by_track_id(self):
        catalog.upsert_track(self._audio("a/one.mp3"), {"track_id": "keep-me"})
        self.assertIn("keep-me", catalog.load_catalog())

    def test_reconstruct_index_writes_rows(self):
        for i in range(3):
            catalog.upsert_track(self._audio(f"a/s{i}.mp3"),
                                 {"track_id": f"t{i}", "auto_qc": {"verdict": "pass", "score": 90}})
        idx, n = catalog.reconstruct_index()
        self.assertEqual(n, 3)
        lines = idx.read_text(encoding="utf-8").strip().splitlines()
        self.assertIn("track_id", lines[0])
        self.assertEqual(len(lines), 4)          # 表頭 + 3 列

    def test_model_credit_from_sidecar(self):
        """商用 provenance：meta 由 sidecar 取模型授權（含舊記錄容錯）。"""
        audio = self._audio("a/credit.mp3")
        catalog.upsert_track(audio, {"gen": {"model": "acestep_v1.5_turbo"}})
        c = meta.track_model_credit(audio)
        self.assertEqual(c["model"], "acestep_v1.5_turbo")
        self.assertEqual(c["license"], "MIT")
        self.assertTrue(c["commercial"])


class TestWorkflowAssembly(unittest.TestCase):
    """ComfyUI workflow 與上片內容組裝（純函式回傳 dict/字串，最容易被改壞）。"""

    def test_visual_prompt_default_graph(self):
        g = visual.build_prompt("a cat", "blurry", 768, 512, 42, 25, 6.5, "p1", ckpt="M.safetensors")
        self.assertEqual(g["4"]["inputs"]["ckpt_name"], "M.safetensors")
        ks = g["3"]["inputs"]
        self.assertEqual(ks["seed"], 42)
        self.assertEqual(ks["steps"], 25)
        self.assertEqual(ks["positive"], ["6", 0])
        self.assertEqual(ks["negative"], ["7", 0])
        self.assertEqual(ks["latent_image"], ["5", 0])       # 無 init → 空 latent
        self.assertEqual(g["9"]["inputs"]["images"], ["8", 0])
        self.assertEqual(g["9"]["inputs"]["filename_prefix"], "p1")

    def test_visual_prompt_img2img_uses_encode(self):
        g = visual.build_prompt("a", "b", 768, 512, 1, 25, 6.5, "p", init_image="ref.png", denoise=0.5)
        self.assertIn("15", g)                                # VAEEncode
        self.assertEqual(g["3"]["inputs"]["latent_image"], ["15", 0])
        self.assertEqual(g["3"]["inputs"]["denoise"], 0.5)

    def test_visual_prompt_clip_skip_inserts_layer(self):
        g = visual.build_prompt("a", "b", 768, 512, 1, 25, 6.5, "p", clip_skip=2)
        self.assertEqual(g["13"]["inputs"]["stop_at_clip_layer"], -2)
        self.assertEqual(g["6"]["inputs"]["clip"], ["13", 0])

    def test_visual_prompt_upscale_branch(self):
        g = visual.build_prompt("a", "b", 768, 512, 1, 25, 6.5, "p", upscale_model="up.pth")
        self.assertEqual(g["10"]["inputs"]["model_name"], "up.pth")
        self.assertEqual(g["12"]["inputs"]["width"], 1536)    # 預設 2x
        self.assertEqual(g["9"]["inputs"]["images"], ["12", 0])

    def test_generate_prompt_nodes_and_params(self):
        wf = generate.build_prompt("lofi, rain", 70, "A minor", 120, 123, "audio/x", steps=8)
        self.assertEqual(wf["94"]["inputs"]["tags"], "lofi, rain")
        self.assertEqual(wf["94"]["inputs"]["bpm"], 70)
        self.assertEqual(wf["94"]["inputs"]["duration"], 120.0)
        self.assertEqual(wf["94"]["inputs"]["keyscale"], "A minor")
        self.assertEqual(wf["3"]["inputs"]["seed"], 123)
        self.assertEqual(wf["3"]["inputs"]["positive"], ["94", 0])
        self.assertEqual(wf["107"]["inputs"]["filename_prefix"], "audio/x")

    def test_generate_save_node_switches_on_format(self):
        """--format flac 要換成 SaveAudio（無損），預設仍是 SaveAudioMP3。"""
        mp3 = generate.build_prompt("t", 70, "A minor", 120, 1, "audio/x")
        self.assertEqual(mp3["107"]["class_type"], "SaveAudioMP3")
        self.assertEqual(mp3["107"]["inputs"]["quality"], "V0")
        flac = generate.build_prompt("t", 70, "A minor", 120, 1, "audio/x", audio_format="flac")
        self.assertEqual(flac["107"]["class_type"], "SaveAudio")
        self.assertNotIn("quality", flac["107"]["inputs"])

    def test_unique_path_adds_suffix(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.mp3"
            self.assertEqual(generate._unique(p), p)          # 不存在 → 原樣
            p.write_text("x")
            self.assertEqual(generate._unique(p).name, "a-2.mp3")
            (Path(td) / "a-2.mp3").write_text("x")
            self.assertEqual(generate._unique(p).name, "a-3.mp3")

    def test_upload_clamp_tags_500_limit(self):
        long = "x" * 400
        tags = upload.clamp_tags([long, long, "small"])       # 兩個 400 字元裝不下
        self.assertEqual(tags, [long])

    def test_upload_build_body_truncates(self):
        body = upload.build_body({"title": "T" * 200, "description": "D" * 6000, "tags": ["a"]}, "private")
        self.assertEqual(len(body["snippet"]["title"]), 100)
        self.assertEqual(len(body["snippet"]["description"]), 5000)
        self.assertEqual(body["status"]["privacyStatus"], "private")
        self.assertEqual(body["snippet"]["categoryId"], "10")

    def test_library_build_where(self):
        ns = argparse.Namespace(tag=["style:rainy_lofi"], style="rainy_lofi", status="keep",
                                verdict=None, min_score=90, min_rating=None,
                                bpm_min=None, bpm_max=None)
        where, params = library.build_where(ns)
        self.assertIn("EXISTS", where)
        self.assertEqual(params, ["style:rainy_lofi", "rainy_lofi", "keep", 90])

    def test_backfill_parse_name(self):
        self.assertEqual(backfill.parse_name("cozy_morning_16_00001"), ("cozy_morning", "16"))
        self.assertEqual(backfill.parse_name("lofi_rainy_night_00001"), ("lofi_rainy_night", None))
        self.assertEqual(backfill.parse_name("garbage"), (None, None))

    def test_meta_stable_index_and_dominant(self):
        self.assertEqual(meta.stable_index("x", 0), 0)
        self.assertIn(meta.stable_index("abc", 5), range(5))
        self.assertEqual(meta.stable_index("abc", 5), meta.stable_index("abc", 5))
        self.assertEqual(meta.dominant([None, "a", "a", "b"]), "a")
        self.assertIsNone(meta.dominant([None, ""]))

    def test_expand_build_prompt_joins_and_skips_empty(self):
        self.assertEqual(
            expand.build_prompt({"backbone": "lofi hip hop", "closing": ""}, "cozy", "warm keys", 70, "A minor"),
            "lofi hip hop, cozy, warm keys, 70 BPM, A minor")
        self.assertEqual(
            expand.build_prompt({"backbone": "x", "closing": "instrumental"}, "", "", 80, "C major"),
            "x, 80 BPM, C major, instrumental")


class TestParserFunctions(unittest.TestCase):
    """把 ffmpeg/ffprobe 的 stderr 解析抽成純函式後，可用合成字串測試（不需真的 ffmpeg）。"""

    def test_parse_ebur128(self):
        err = ("  Integrated loudness:\n    I:         -14.0 LUFS\n"
               "  Loudness range:\n    LRA:         6.0 LU\n"
               "  True peak:\n    Peak:       -1.0 dBFS\n    Peak count:  3\n")
        r = qc.parse_ebur128(err)
        self.assertEqual(r["lufs"], -14.0)
        self.assertEqual(r["lra"], 6.0)
        self.assertEqual(r["true_peak"], -1.0)

    def test_parse_ebur128_missing(self):
        r = qc.parse_ebur128("no stats here")
        self.assertIsNone(r["lufs"])
        self.assertIsNone(r["true_peak"])

    def test_parse_ebur128_takes_max_peak(self):
        err = "    Peak:       -3.0 dBFS\n    Peak:       -0.5 dBFS\n"
        self.assertEqual(qc.parse_ebur128(err)["true_peak"], -0.5)

    def test_measure_ebur128_uses_parser(self):
        """monkeypatch _run → 不需 ffmpeg 也能驗證解析被接上。"""
        orig = qc._run
        qc._run = lambda cmd: "    I:  -14.0 LUFS\n    Peak: -1.0 dBFS\n"
        try:
            self.assertEqual(qc.measure_ebur128("x.mp3")["lufs"], -14.0)
        finally:
            qc._run = orig

    def test_parse_astats(self):
        err = ("[Parsed_astats_0 @ 0x0] Overall\n"
               "    DC offset: 0.000123\n    Peak level dB: -1.50\n"
               "    RMS level dB: -18.20\n    Flat factor: 0.000\n"
               "    Zero crossings rate: 0.123\n    Peak count: 5\n")
        r = qc.parse_astats(err)
        self.assertEqual(r["rms_db"], -18.20)
        self.assertEqual(r["peak_db"], -1.50)
        self.assertAlmostEqual(r["dc_offset"], 0.000123)

    def test_parse_silence_ratio(self):
        err = "silence_duration: 5\nsilence_duration: 5\n"
        self.assertAlmostEqual(qc.parse_silence_ratio(err, 100.0), 0.1)
        self.assertIsNone(qc.parse_silence_ratio(err, None))          # 無時長 → None
        self.assertEqual(qc.parse_silence_ratio("silence_duration: 500", 100.0), 1.0)

    def test_parse_ffprobe_info(self):
        r = visual_qc.parse_ffprobe_info("1536,1024,rgb24\n")
        self.assertEqual((r["width"], r["height"], r["pix_fmt"]), (1536, 1024, "rgb24"))
        self.assertIsNone(visual_qc.parse_ffprobe_info(""))
        self.assertIsNone(visual_qc.parse_ffprobe_info("abc"))

    def test_parse_signalstats(self):
        err = ("[Parsed_metadata_1 @ 0x0] lavfi.signalstats.YMIN=16.0\n"
               "lavfi.signalstats.YAVG=110.5\nlavfi.signalstats.SATMAX=62.0\n")
        r = visual_qc.parse_signalstats(err)
        self.assertEqual(r["ymin"], 16.0)
        self.assertEqual(r["satmax"], 62.0)

    def test_parse_entropy(self):
        self.assertEqual(visual_qc.parse_entropy(
            "lavfi.entropy.normalized_entropy.normal.R=0.888\n"), 0.888)
        self.assertIsNone(visual_qc.parse_entropy("garbage"))

    def test_parse_duration(self):
        self.assertEqual(media.parse_duration("120.50\n"), 120.5)
        self.assertIsNone(media.parse_duration(""))
        self.assertIsNone(media.parse_duration("N/A"))


class TestCLISmoke(unittest.TestCase):
    """CLI 進入點接線：dispatch 與各子命令 argparse 都能建起來（不需 ComfyUI/ffmpeg）。"""

    def _cli(self, argv):
        import lofi.cli as cli
        orig = sys.argv
        sys.argv = ["lofi"] + argv
        try:
            cli.main()
        finally:
            sys.argv = orig

    def test_no_args_prints_usage(self):
        import io as pyio
        import contextlib
        buf = pyio.StringIO()
        with contextlib.redirect_stdout(buf):
            self._cli([])
        self.assertIn("commands:", buf.getvalue())

    def test_unknown_command_exits(self):
        with self.assertRaises(SystemExit):
            self._cli(["definitely-not-a-command"])

    def test_every_subcommand_help(self):
        import io as pyio
        import contextlib
        from lofi import cli
        buf = pyio.StringIO()
        with contextlib.redirect_stdout(buf):
            for name in cli.MODULES:
                if name == "cinemagraph":        # 需 numpy，系統 python 可能沒有
                    continue
                with self.assertRaises(SystemExit):
                    self._cli([name, "--help"])


class TestLicensing(unittest.TestCase):
    """商用授權登錄與守門（config.MODEL_LICENSES / check_commercial）。"""

    def test_known_models_commercial(self):
        self.assertTrue(config.model_license("acestep_v1.5_turbo.safetensors")["commercial"])
        # 容錯：舊記錄只存無副檔名，且 v1.5 的點不可被誤切
        self.assertTrue(config.model_license("acestep_v1.5_turbo")["commercial"])
        self.assertEqual(config.model_license("DreamShaperXL-v1.0-Alpha2.safetensors")["license"],
                         "CreativeML Open RAIL++-M")
        self.assertTrue(config.model_license("z-image-turbo-Q5_K_M.gguf")["commercial"])

    def test_unknown_model_is_unknown(self):
        info = config.model_license("mystery_model.safetensors")
        self.assertIsNone(info["commercial"])
        self.assertIsNone(config.model_license("")["commercial"])

    def test_check_commercial_fail_closed_on_unknown(self):
        blocked, findings = config.check_commercial(["mystery.safetensors"])
        self.assertTrue(blocked)                                  # 未知 → 預設擋
        blocked2, _ = config.check_commercial(["mystery.safetensors"], allow_unknown=True)
        self.assertFalse(blocked2)                                # 明確允許則放行
        blocked3, _ = config.check_commercial(["acestep_v1.5_turbo.safetensors"])
        self.assertFalse(blocked3)                                # 已登錄可商用

    def test_license_scan_returns_dicts(self):
        from lofi import license as lic
        self.assertIsInstance(lic.scan_music(), dict)
        self.assertIsInstance(lic.scan_visuals(), dict)


class TestRelease(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(release.slugify("Warm Breeze at the Shore"), "warm-breeze-at-the-shore")
        self.assertEqual(release.slugify("Slow Tide (pt. 2)"), "slow-tide-pt-2")
        self.assertEqual(release.slugify("  ..  "), "")
        self.assertEqual(release.slugify(None), "")

    def test_unique_titles_adds_pt(self):
        self.assertEqual(release.unique_titles(["Sunset", "Sunset", "Sunset"]),
                         ["Sunset", "Sunset (pt. 2)", "Sunset (pt. 3)"])
        self.assertEqual(release.unique_titles(["A", "B"]), ["A", "B"])

    def test_unique_titles_is_case_insensitive(self):
        self.assertEqual(release.unique_titles(["Rain", "rain"]), ["Rain", "rain (pt. 2)"])

    def test_track_title_prefers_mood(self):
        self.assertEqual(release.track_title("x.mp3", {"mood": "rain on the window"}),
                         "Rain on the Window")
        self.assertEqual(release.track_title("Slow Tide.mp3", {}), "Slow Tide")

    def test_parse_loudnorm_json(self):
        err = ('[Parsed_loudnorm_0 @ 0x7f] \n{\n\t"input_i" : "-12.90",\n'
               '\t"input_tp" : "-0.10",\n\t"target_offset" : "0.20"\n}\n')
        d = release.parse_loudnorm_json(err)
        self.assertEqual(d["input_i"], "-12.90")
        self.assertIsNone(release.parse_loudnorm_json(""))
        self.assertIsNone(release.parse_loudnorm_json("no json here"))

    def test_build_tracklist_csv(self):
        csv_text = release.build_tracklist_csv([{
            "no": 1, "title": "A", "artist": "X", "duration": 125,
            "key": "G major", "bpm": 70, "mood": "m", "file": "f.mp3"}])
        self.assertTrue(csv_text.startswith("No,Title,Artist,Duration"))
        self.assertIn("02:05", csv_text)                 # 125s 格式化
        self.assertIn("G major", csv_text)

    def test_summarize_release_event_drops_empty(self):
        ev = release.summarize_release_event(
            {"run": "r1", "album": "A", "tracks": 3, "label": "", "notes": []})
        self.assertEqual(ev, {"run": "r1", "album": "A", "tracks": 3})

    def test_build_checklist_has_all_platforms(self):
        md = release.build_checklist({
            "album": "Coastal", "artist": "X", "master": {"lufs": -14, "tp": -1},
            "master_source": "src", "credits": [{"model": "m", "license": "MIT"}],
            "tracks": [{"title": "A"}]})
        for section in ("DSP 串流", "Bandcamp", "stock／sync", "發布前檢查"):
            self.assertIn(section, md)

    def _rec(self, **over):
        rec = {"album": "Into the Forest", "artist": "Moni on the Journey", "run": "forest-1hr-01",
               "style": "forest_piano", "genre": "Lo-fi", "label": "", "explicit": False,
               "songwriter": "Real Name", "ai_credits": {"scope": "all_audio", "persona": True},
               "master": {"lufs": -14, "tp": -1}, "master_source": "src",
               "credits": [{"model": "acestep_v1.5_turbo.safetensors", "license": "MIT",
                            "source": "https://x", "commercial": True}],
               "tracks": [{"no": 1, "title": "A"}, {"no": 2, "title": "B"}],
               "total_duration": 240.0, "listing": {}}
        rec.update(over)
        return rec

    def test_clean_tags_rules(self):
        """Gumroad tag 規則：2–20 字、去開頭 #、不分大小寫去重、上限 10。"""
        kept, rejected = release.clean_tags(
            ["lofi", "lofi", "#sleep", "x", "study music", "a" * 21])
        self.assertEqual(kept, ["lofi", "sleep", "study music"])   # 重複／太短／太長都丟
        self.assertIn("x", rejected)
        _, over = release.clean_tags([f"tag{i}" for i in range(15)])
        self.assertEqual(len(over), 5)                             # 超過 10 個的都進 rejected
        self.assertLessEqual(len(release.clean_tags([f"t{i}" for i in range(15)])[0]), 10)

    def test_listing_tags_legal_and_include_genre(self):
        tags = release.listing_tags(self._rec())
        self.assertIn("lofi", tags)                                # genre 小寫進 tag
        self.assertIn("forest", tags)                              # 專輯名 → tag
        self.assertIn("piano", tags)                               # style → tag
        self.assertEqual(release.clean_tags(tags)[1], [])           # 產出的一定合法
        self.assertLessEqual(len(tags), release.GUMROAD_MAX_TAGS)

    def test_build_listing_defaults_and_price(self):
        listing = release.build_listing(self._rec())
        self.assertIn("Into the Forest", listing["short"])
        self.assertIn("Moni on the Journey", listing["short"])
        self.assertIn("AI", listing["long"])                       # scope != none → 要揭露
        self.assertEqual(listing["price_usd"], config.RELEASE_PRICE_USD)
        # 明給 tags/price 時要沿用（tags 仍會過濾），且 scope=none 不再宣稱 AI
        got = release.build_listing(self._rec(ai_credits={"scope": "none"}),
                                    tags=["#Shore", "study music"], price_usd=7.5)
        self.assertEqual(got["tags"], ["Shore", "study music"])
        self.assertEqual(got["price_usd"], 7.5)
        self.assertNotIn("AI 生成", got["long"])

    def test_build_listing_md_has_both_platforms(self):
        rec = self._rec()
        rec["listing"] = release.build_listing(rec)
        md = release.build_listing_md(rec)
        for needle in ("DistroKid", "Gumroad", "Into the Forest", "Moni on the Journey",
                       "Songwriter", "AI Credits", "cover_3000x3000.jpg", "download/"):
            self.assertIn(needle, md)
        self.assertIn("Real Name", md)
        # 沒填 songwriter 時要喊出來（DistroKid 原創曲必填真名）
        rec["songwriter"] = ""
        self.assertIn("必填真名", release.build_listing_md(rec))

    def test_build_credits_txt_lists_models(self):
        txt = release.build_credits_txt(self._rec())
        self.assertIn("acestep_v1.5_turbo.safetensors", txt)
        self.assertIn("MIT", txt)
        self.assertIn("all_audio", txt)
        self.assertIn("AI 角色", txt)                              # persona=True
        self.assertIn("（無紀錄）", release.build_credits_txt(self._rec(credits=[])))

    def test_build_checklist_includes_shared_fields(self):
        """LISTING 之後，CHECKLIST 也要直接看到共用欄位與 Gumroad（不必回頭查程式）。"""
        rec = self._rec()
        rec["listing"] = release.build_listing(rec)
        md = release.build_checklist(rec)
        for needle in ("Songwriter", "AI Credits", "Gumroad", "LISTING.md", "download/"):
            self.assertIn(needle, md)
        self.assertIn("explicit = No", md)

    def test_make_release_zip_is_deterministic_and_skips_missing(self):
        """交付 ZIP：內容決定檔案（時間戳固定），且缺少的成員直接略過不炸。"""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "a.wav").write_bytes(b"audio-bytes")
            (d / "tracklist.csv").write_text("No,Title\n1,A\n", encoding="utf-8")
            members = [(d / "a.wav", "audio/a.wav"),
                       (d / "tracklist.csv", "tracklist.csv"),
                       (d / "missing.txt", "missing.txt")]
            z1 = release.make_release_zip(d / "one.zip", members)
            z2 = release.make_release_zip(d / "two.zip", members)
            self.assertEqual(z1.read_bytes(), z2.read_bytes())     # 可重現
            with zipfile.ZipFile(z1) as z:
                self.assertEqual(sorted(z.namelist()), ["audio/a.wav", "tracklist.csv"])
                self.assertEqual(z.read("audio/a.wav"), b"audio-bytes")
            self.assertFalse((d / "one.zip.tmp").exists())        # 原子替換不留殘檔

    def test_ai_credit_scopes_and_defaults(self):
        self.assertIn(config.RELEASE_AI_CREDITS, release.AI_CREDIT_SCOPES)
        self.assertEqual(config.RELEASE_AI_CREDITS, "all_audio")   # ACE-Step 全生成
        self.assertEqual(release.AI_CREDIT_LABELS["all_audio"], "全部音訊為 AI 生成")
        self.assertFalse(config.RELEASE_EXPLICIT)                  # 純器樂、無歌詞

    def test_masters_subdir_mirrors_tracks(self):
        self.assertEqual(str(catalog.masters_subdir("coastal guitar", "coastal01")),
                         str(catalog.subdir(catalog.MASTERS_DIR, "coastal guitar", "coastal01")))
        self.assertTrue(str(catalog.masters_subdir("a", "b")).endswith("a/b"))

    def test_describe_master_source(self):
        desc = release.describe_master_source
        self.assertIn("無損母帶", desc([{"master_src": "/m.flac", "sidecar": {}},
                                        {"master_src": "/m2.flac", "sidecar": {}}]))
        self.assertIn("混合", desc([{"master_src": "/m.flac", "sidecar": {}},
                                    {"master_src": None, "sidecar": {}}]))
        self.assertIn("mp3", desc([{"master_src": None, "sidecar": {}}]))
        self.assertIn("母帶檔不在", desc([{"master_src": None,
                                          "sidecar": {"gen": {"format": "flac"}}}]))

    def test_character_score(self):
        ch = {"cover_match": {"must": ["black cat"],
                              "never": ["orange tabby", "tabby"],
                              "features": ["white paw", "white sock", "amber eye"]}}
        # 主角：命中特徵越多分數越高
        self.assertEqual(release.character_score("a sleek black cat with golden amber eyes", ch), 2)
        self.assertEqual(release.character_score(
            "black cat, clearly white paws, white socks on all four feet, golden amber eyes", ch), 4)
        # 不是主角：別的貓 / 無主角 / 空
        self.assertEqual(release.character_score("a chubby orange tabby cat", ch), -1)
        self.assertEqual(release.character_score("soft watercolor background", ch), -1)
        self.assertEqual(release.character_score("", ch), -1)
        self.assertEqual(release.character_score("black cat", None), -1)   # 沒有角色檔 → 不認

    def test_visual_lora_node_wiring(self):
        """--lora 要插入 LoraLoader，且 model 與 clip 都要接到它後面。"""
        g = visual.build_prompt("p", "n", 832, 1216, 1, 28, 6.5, "x",
                                lora="moni.safetensors", lora_weight=0.8)
        self.assertEqual(g["22"]["class_type"], "LoraLoader")
        self.assertEqual(g["22"]["inputs"]["lora_name"], "moni.safetensors")
        self.assertEqual(g["22"]["inputs"]["strength_model"], 0.8)
        self.assertEqual(g["22"]["inputs"]["strength_clip"], 0.8)
        self.assertEqual(g["22"]["inputs"]["model"], ["4", 0])   # 來源是 checkpoint
        self.assertEqual(g["6"]["inputs"]["clip"], ["22", 1])    # 文字編碼吃 LoRA 後的 clip
        self.assertEqual(g["3"]["inputs"]["model"], ["22", 0])   # 取樣器吃 LoRA 後的 model

    def test_visual_without_lora_is_unchanged(self):
        g = visual.build_prompt("p", "n", 832, 1216, 1, 28, 6.5, "x")
        self.assertNotIn("22", g)
        self.assertEqual(g["6"]["inputs"]["clip"], ["4", 1])
        self.assertEqual(g["3"]["inputs"]["model"], ["4", 0])

    def test_visual_lora_before_ipadapter(self):
        """順序：checkpoint → LoRA → IPAdapter（IPAdapter 要吃到 LoRA 的 model）。"""
        g = visual.build_prompt("p", "n", 832, 1216, 1, 28, 6.5, "x",
                                lora="m.safetensors", ref_image="ref.png")
        self.assertEqual(g["16"]["inputs"]["model"], ["22", 0])
        self.assertEqual(g["3"]["inputs"]["model"], ["18", 0])

    def test_kohya_toml_matches_dataset(self):
        t = character.build_kohya_toml("moni", "moni", repeats=10, epochs=10,
                                       resolution="832,1216",
                                       base_model="DreamShaperXL-v1.0-Alpha2.safetensors")
        self.assertIn('resolution = "832,1216"', t)      # 對齊資料集原尺寸，不要 1024 方圖
        self.assertIn("num_repeats = 10", t)
        self.assertIn("max_train_epochs = 10", t)
        self.assertIn("10_moni", t)
        self.assertIn("DreamShaperXL-v1.0-Alpha2.safetensors", t)
        self.assertIn("sdxl = true", t)

    def test_release_threads_and_nice_plumbing(self):
        """--threads 要插進 ffmpeg 指令（避免吃滿核心）；nice 不應炸掉。"""
        captured = {}
        orig_run, orig_t, orig_n = media.run, release._THREADS, release._NICE
        media.run = lambda cmd: (captured.__setitem__("cmd", cmd), (0, "", ""))[1]
        release._THREADS, release._NICE = 4, 0
        try:
            release._run_ff(["ffmpeg", "-i", "x", "y"])
        finally:
            media.run, release._THREADS, release._NICE = orig_run, orig_t, orig_n
        self.assertEqual(captured["cmd"], ["ffmpeg", "-threads", "4", "-i", "x", "y"])

    def test_find_orphans(self):
        """audio/ 裡不屬於本專輯的檔（改過曲名／前次中斷的殘留）要被找出來清掉。

        踩過的實例：15 個檔裡有 3 個孤兒，而 CHECKLIST 叫使用者「上傳 audio/ 全部」
        → 會多傳 3 首重複的歌。
        """
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for n in ("01_keep.wav", "02_keep.wav", "01_old-name.wav", "03_old-pt2.wav"):
                (d / n).write_bytes(b"x")
            (d / "cover.jpg").write_bytes(b"x")          # 非音檔，不該被當孤兒
            got = [f.name for f in release.find_orphans(d, ["01_keep.wav", "02_keep.wav"])]
            self.assertEqual(got, ["01_old-name.wav", "03_old-pt2.wav"])
            self.assertEqual(release.find_orphans(d, ["01_keep.wav", "02_keep.wav", "01_old-name.wav", "03_old-pt2.wav"]), [])

    def test_flag_filter(self):
        """品檢政策：只擋母帶修不掉的 flag；沒品檢紀錄的不可假通過。"""
        rows = [{"verdict": "warn", "flags": ["wide_dynamics"], "audio": Path("a.mp3")},
                {"verdict": "warn", "flags": ["hot_true_peak"], "audio": Path("b.mp3")},
                {"verdict": "pass", "flags": [], "audio": Path("c.mp3")},
                {"verdict": None, "flags": [], "audio": Path("d.mp3")}]
        kept, dropped = release.flag_filter(rows, ("wide_dynamics", "truncated"))
        self.assertEqual([r["audio"].name for r in kept], ["b.mp3", "c.mp3"])
        self.assertEqual([why for _r, why in dropped], ["wide_dynamics", "沒有品檢紀錄"])
        # 空 = 不過濾（連沒品檢紀錄的都留，方便硬跑）
        kept2, dropped2 = release.flag_filter(rows, ())
        self.assertEqual(len(kept2), 4)
        self.assertEqual(dropped2, [])

    def test_qc_policy_only_blocks_unfixable_flags(self):
        """hot_true_peak／loudness_out_of_range 是母帶修得掉的，不可列為擋掉。"""
        self.assertIn("wide_dynamics", config.QC_BLOCKING_FLAGS)
        self.assertNotIn("hot_true_peak", config.QC_BLOCKING_FLAGS)
        self.assertNotIn("loudness_out_of_range", config.QC_BLOCKING_FLAGS)

    def test_character_record_exists(self):
        ch = release.load_character("moni")
        self.assertIsNotNone(ch, "prompts/character/moni.json 應該存在")
        self.assertEqual(ch["name"], "Moni")
        self.assertEqual(ch["artist"], "Moni on the Journey")
        self.assertIn("black cat", ch["cover_match"]["must"])
        self.assertIsNone(release.load_character("no-such-character"))

    def test_caption_problems(self):
        spec = {"require_tags": ["black cat", "white socks"],
                "forbid_tags": ["black paws", "tabby"]}
        self.assertEqual(character.caption_problems("a black cat with white socks", spec), [])
        missing = character.caption_problems("a black cat", spec)
        self.assertTrue(any("white socks" in m for m in missing))
        conflict = character.caption_problems("a black cat with white socks and black paws", spec)
        self.assertTrue(any("black paws" in m for m in conflict))
        self.assertEqual(character.caption_problems("", spec), ["字幕是空的"])
        self.assertEqual(character.caption_problems(None, spec), ["字幕是空的"])

    def test_negative_problems(self):
        spec = {"negative_should_contain": ["black paws", "black feet"]}
        self.assertEqual(character.negative_problems("all black paws, black feet, blurry", spec), [])
        self.assertEqual(len(character.negative_problems("blurry only", spec)), 2)

    def test_has_trigger_is_token_not_substring(self):
        self.assertTrue(character.has_trigger("moni, a black cat", "moni"))
        self.assertTrue(character.has_trigger("a black cat, moni", "moni"))
        self.assertFalse(character.has_trigger("monitor, a black cat", "moni"))  # 不能誤中
        self.assertTrue(character.has_trigger("anything", ""))                   # 沒設觸發詞 → 算過

    def test_caption_plan(self):
        # 沒字幕 → 從 prompt 重建並補觸發詞
        self.assertEqual(character.caption_plan("", "a black cat", "moni"),
                         ("moni, a black cat", "reconstruct+trigger"))
        # 有字幕、缺觸發詞 → 補
        self.assertEqual(character.caption_plan("a black cat", "", "moni"),
                         ("moni, a black cat", "add-trigger"))
        # 已正確 → 不動
        self.assertEqual(character.caption_plan("moni, a black cat", "", "moni"),
                         ("moni, a black cat", "ok"))
        # 沒有來源 → None
        self.assertEqual(character.caption_plan("", "", "moni"), (None, None))
        # 沒設定觸發詞 → 不亂加
        self.assertEqual(character.caption_plan("a black cat", "", ""), ("a black cat", "ok"))

    def test_moni_caption_check_matches_ds03_style(self):
        """定裝的 caption_check 要能認過 ds03 那種字幕（避免規則訂得太嚴炸全部）。"""
        ch = character.load_character("moni")
        cap = ("moni, a sleek black cat with (large round golden amber eyes:1.2), (clearly white paws:1.3), "
               "(white socks on all four feet:1.2), pure black fur on the rest of the body")
        self.assertEqual(character.caption_problems(cap, ch["caption_check"]), [])
        # 沒有觸發詞就該被擋（否則模型會把「黑貓」這個通用概念學進去）
        no_trigger = cap.replace("moni, ", "")
        self.assertTrue(any("moni" in p
                            for p in character.caption_problems(no_trigger, ch["caption_check"])))


if __name__ == "__main__":
    unittest.main()
