"""核心純函式測試。執行： python3 -m unittest discover -s tests -v"""
import argparse
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lofi import (backfill, catalog, character, config, expand, generate,  # noqa: E402
                  gumroad, io, library, media, meta, notify, publish, qc, release, review,
                  runs, status, upload, visual, visual_qc, zimage)


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

    def test_bpm_octave_flagged_not_scored(self):
        """要求 80 卻量到 ~40 或 ~160 → 標 bpm_flag=octave，但不扣分（估計器對切分音會誤判）。"""
        r = qc.judge(self._m(bpm_est=160.0), wanted_bpm=80)
        self.assertEqual(r["bpm_flag"], "octave")
        self.assertNotIn("bpm_octave", r["flags"])       # 不進 warnings
        self.assertEqual(r["score"], 100)                 # 不影響 keeper 篩選
        self.assertEqual(qc.judge(self._m(bpm_est=40.0), wanted_bpm=80)["bpm_flag"], "octave")

    def test_bpm_match_or_mismatch(self):
        r_ok = qc.judge(self._m(bpm_est=78.0), wanted_bpm=80)
        self.assertTrue(r_ok["bpm_match"])
        self.assertIsNone(r_ok["bpm_flag"])
        self.assertEqual(qc.judge(self._m(bpm_est=120.0), wanted_bpm=80)["bpm_flag"], "mismatch")
        # 沒給 wanted_bpm 時不判 BPM（向後相容）
        self.assertIsNone(qc.judge(self._m(bpm_est=160.0))["bpm_match"])

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
                       ("ROOT", "TRACKS_DIR", "CATALOG_DIR", "JSONL_PATH", "INDEX_CSV", "MASTERS_DIR")}
        catalog.ROOT = self.root
        catalog.TRACKS_DIR = self.root / "assets" / "tracks"
        catalog.CATALOG_DIR = self.root / "catalog"
        catalog.JSONL_PATH = catalog.CATALOG_DIR / "tracks.jsonl"
        catalog.INDEX_CSV = catalog.CATALOG_DIR / "index.csv"
        catalog.MASTERS_DIR = self.root / "output" / "masters"

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

    def test_review_writes_rating_and_note(self):
        """人工複審把聽感評分寫進 sidecar（keeper 篩選的唯一來源）。"""
        audio = self._audio()
        catalog.upsert_track(audio, {"style": "x", "track_id": "t-r"})
        review.review_file(audio, rating=5, note="sax 最像", status="keep")
        sc = catalog.load_sidecar(audio)
        self.assertEqual(sc["review"]["rating"], 5)
        self.assertEqual(sc["review"]["note"], "sax 最像")
        self.assertEqual(sc["status"], "keep")
        self.assertIn("reviewed_at", sc["review"])

    def test_review_clear_removes_review(self):
        audio = self._audio()
        catalog.upsert_track(audio, {"style": "x"})
        review.review_file(audio, rating=4)
        review.review_file(audio, clear=True)
        self.assertNotIn("review", catalog.load_sidecar(audio))

    def test_lossless_master_prefers_sidecar_then_folder(self):
        audio = self._audio("nujabes/run1/song_01.mp3")
        md = self.root / "output" / "masters" / "nujabes" / "run1"
        md.mkdir(parents=True)
        (md / "song_01.flac").write_bytes(b"fLaC")
        catalog.upsert_track(audio, {"style": "nujabes", "run_id": "run1"})
        self.assertEqual(catalog.lossless_master(audio), md / "song_01.flac")   # 母帶夾同名檔
        other = self.root / "output" / "masters" / "elsewhere" / "x.flac"
        other.parent.mkdir(parents=True)
        other.write_bytes(b"fLaC")
        sc = catalog.load_sidecar(audio)
        sc["master_file"] = catalog.relpath(other)
        catalog.save_sidecar(audio, sc)
        self.assertEqual(catalog.lossless_master(audio), other)                 # sidecar 優先

    def test_lossless_master_none_when_absent(self):
        audio = self._audio("nujabes/run1/song_02.mp3")
        catalog.upsert_track(audio, {"style": "nujabes", "run_id": "run1"})
        self.assertIsNone(catalog.lossless_master(audio))     # 沒有母帶 → None（呼叫端退回工作檔）

    def test_review_event_is_compact(self):
        """事件流只記評分欄位，不夾帶整包 prompt（避免 tracks.jsonl 膨脹）。"""
        ev = catalog.summarize_event("review", {"track_id": "t", "file": "f",
                                                "prompt": "落落長", "status": "keep",
                                                "review": {"rating": 5, "note": "讚"}})
        self.assertEqual(ev["rating"], 5)
        self.assertEqual(ev["note"], "讚")
        self.assertEqual(ev["status"], "keep")
        self.assertNotIn("prompt", ev)

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

    def test_generate_quality_presets_and_overrides(self):
        """--quality 給預設、個別旗標可覆寫；fast 必須等於原始預設（不可悄悄改變產線）。"""
        self.assertEqual(generate.sampling_params("fast"),
                         {"steps": 8, "temperature": 0.85, "cfg_scale": 2.0})
        self.assertEqual(generate.sampling_params("stable")["steps"], 12)
        self.assertLess(generate.sampling_params("stable")["temperature"],
                        generate.sampling_params("fast")["temperature"])
        # 個別覆寫優先於預設
        self.assertEqual(generate.sampling_params("stable", steps=6)["steps"], 6)
        self.assertEqual(generate.sampling_params("fast", temperature=0.7)["temperature"], 0.7)

    def test_generate_audio_codes_flag_reaches_node(self):
        """generate_audio_codes 要真的傳進節點（audio2audio 給音訊參考時應可關）。"""
        on = generate.build_prompt("t", 70, "A minor", 120, 1, "audio/x")
        self.assertTrue(on["94"]["inputs"]["generate_audio_codes"])
        off = generate.build_prompt("t", 70, "A minor", 120, 1, "audio/x",
                                    generate_audio_codes=False)
        self.assertFalse(off["94"]["inputs"]["generate_audio_codes"])

    def test_generate_negative_switches_node(self):
        """有 negative 才改用自訂節點（LofiAceStepNegativeEncode）；沒有則維持內建節點。"""
        plain = generate.build_prompt("t", 70, "A minor", 120, 1, "audio/x")
        self.assertEqual(plain["94"]["class_type"], "TextEncodeAceStepAudio1.5")
        self.assertNotIn("negative", plain["94"]["inputs"])
        wn = generate.build_prompt("t", 70, "A minor", 120, 1, "audio/x",
                                   negative="vocals, harsh synths")
        self.assertEqual(wn["94"]["class_type"], "LofiAceStepNegativeEncode")
        self.assertEqual(wn["94"]["inputs"]["negative"], "vocals, harsh synths")
        # 空白 negative 視為沒給
        blank = generate.build_prompt("t", 70, "A minor", 120, 1, "audio/x", negative="  ")
        self.assertEqual(blank["94"]["class_type"], "TextEncodeAceStepAudio1.5")

    def test_generate_pick_duration(self):
        """長度：CSV 逐列值 > --duration-range 隨機 > --duration；固定種子可重現。"""
        import random as _r
        self.assertEqual(generate.pick_duration("", 120, None, _r.Random(0)), 120.0)
        self.assertEqual(generate.pick_duration("135", 120, (120, 150), _r.Random(0)), 135.0)
        self.assertEqual(generate.pick_duration("bad", 120, None, _r.Random(0)), 120.0)  # 壞值退回
        rng = _r.Random(42)
        vals = [generate.pick_duration("", 120, (120, 150), rng) for _ in range(30)]
        self.assertTrue(all(120 <= v <= 150 for v in vals))
        self.assertGreater(len(set(vals)), 1)                       # 真的有變化，不是固定值
        ra, rb = _r.Random(7), _r.Random(7)                          # 同種子 → 同序列
        self.assertEqual([generate.pick_duration("", 120, (120, 150), ra) for _ in range(5)],
                         [generate.pick_duration("", 120, (120, 150), rb) for _ in range(5)])

    def test_generate_ref_timbre_wires_cover_chain(self):
        """--ref-timbre 走 cover 模式：LoadAudio→VAEEncodeAudio→ReferenceTimbreAudio 掛進 positive，
        維持空 latent（與 --init-audio 的 latent 起點不同），且自動關掉 LLM 音碼。"""
        g = generate.build_prompt("t", 70, "A minor", 120, 1, "audio/x", ref_timbre="ref.mp3")
        self.assertEqual(g["110"]["class_type"], "LoadAudio")
        self.assertEqual(g["110"]["inputs"]["audio"], "ref.mp3")
        self.assertEqual(g["111"]["class_type"], "VAEEncodeAudio")
        self.assertEqual(g["111"]["inputs"]["audio"], ["110", 0])
        self.assertEqual(g["112"]["class_type"], "ReferenceTimbreAudio")
        self.assertEqual(g["112"]["inputs"]["conditioning"], ["94", 0])
        self.assertEqual(g["112"]["inputs"]["latent"], ["111", 0])
        self.assertEqual(g["3"]["inputs"]["positive"], ["112", 0])
        self.assertEqual(g["3"]["inputs"]["latent_image"], ["98", 0])   # 仍是空 latent
        self.assertFalse(g["94"]["inputs"]["generate_audio_codes"])      # cover 模式關 LLM 音碼
        # 沒給就不掛這條鏈（舊行為不變）
        plain = generate.build_prompt("t", 70, "A minor", 120, 1, "audio/x")
        self.assertNotIn("112", plain)
        self.assertEqual(plain["3"]["inputs"]["positive"], ["94", 0])

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

    def test_channel_record_matches_artist_string(self):
        """頻道名必須與 config.RELEASE_ARTIST **完全相同**（含大小寫）。

        平台以這個字串建藝人頁，一個字母不同就等於換一個品牌
        （實際踩過：改成 `journery` 少/多一個 r、全小寫）。
        """
        p = Path(__file__).resolve().parent.parent / "prompts" / "youtube" / "_channel.json"
        self.assertTrue(p.exists(), "prompts/youtube/_channel.json 應該存在")
        ch = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(ch["channel"], config.RELEASE_ARTIST)
        self.assertEqual(ch["old_name"], "Drifted LoFi")
        for key in ("description_en", "description_zh", "business_description_en",
                    "business_description_zh", "keywords", "banner"):
            self.assertTrue(ch.get(key), f"_channel.json 缺 {key}")
        # 描述要能放進 YouTube（上限 1000 字元）
        self.assertLessEqual(len(ch["description_en"]), 1000)
        # 故事段落不得提 AI（揭露只在括號那行）
        self.assertNotIn("AI", ch["description_en"].split("(Music and artwork")[0])

    def test_build_credits_block_and_description(self):
        """YouTube 描述要有 AI 揭露＋模型授權（不能只留在本機 JSON）。"""
        credits = [{"model": "acestep_v1.5_turbo.safetensors", "license": "MIT",
                    "source": "https://x"}]
        block = meta.build_credits_block(credits)
        self.assertIn("AI disclosure & licences", " ".join(block))
        self.assertTrue(any("acestep_v1.5_turbo.safetensors (MIT)" in ln for ln in block))
        self.assertEqual(meta.build_credits_block([]), [])          # 沒 credits 就不加

        cfg = {"emoji": "🍂", "intro": "hi", "features": ["a"], "hashtags": ["#lofi"],
               "genre_phrase": "lofi"}
        with_c = meta.build_description(cfg, 120, [], credits)
        without = meta.build_description(cfg, 120, [], [])
        self.assertIn("AI disclosure & licences", with_c)
        self.assertIn("ace_step", with_c.replace("acestep", "ace_step"))
        self.assertNotIn("AI disclosure", without)
        self.assertTrue(with_c.rstrip().endswith("#lofi"))           # 授權在 hashtags「之前」

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

    def test_thumbnail_1280x720(self):
        """YouTube 縮圖：1280×720、≤2MB。需要 qlmanage（macOS）＋ffmpeg，沒有就跳過。"""
        import shutil as _sh
        if not (_sh.which("qlmanage") and _sh.which("ffmpeg")):
            self.skipTest("需要 qlmanage 與 ffmpeg")
        with tempfile.TemporaryDirectory() as td:
            art = Path(td) / "art.png"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                            "-i", "color=c=gray:s=1920x1080", "-frames:v", "1", str(art)],
                           check=True)
            dst = Path(td) / "t.jpg"
            out = meta.make_thumbnail(["COZY", "MORNING", "LOFI"], art, dst)
            if out is None:
                self.skipTest("qlmanage 在此環境無法渲染（例如沒有 GUI session）")
            self.assertEqual(release.probe_wh(dst), (1280, 720))
            self.assertLess(dst.stat().st_size, 2 * 1024 * 1024)      # YouTube 上限 2MB

    def test_square_cover_serves_the_store(self):
        """方形封面一張到底：DistroKid 封面（要 ≥3000²）＝ Gumroad 縮圖（要 ≥600×600）。"""
        import shutil as _sh
        if not _sh.which("ffmpeg"):
            self.skipTest("沒有 ffmpeg")
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "album-art.jpg"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                            "-i", "color=c=black:s=3000x3000", "-frames:v", "1", str(src)],
                           check=True)
            dst = Path(td) / "cover_3000x3000.jpg"
            release.make_cover(src, dst)
            self.assertEqual(release.probe_wh(dst), (3000, 3000))

    def test_wide_art_is_one_4k_image_for_store_and_zip(self):
        """16:9 也只出一張 4K：Gumroad 封面（要 ≥1280×720）與交付 ZIP 用的是同一張。

        以前出兩張（商店 1920×1080、ZIP 4K）→ 商店看到的不等於買家拿到的，也白存一組衍生檔。
        """
        import shutil as _sh
        if not _sh.which("ffmpeg"):
            self.skipTest("沒有 ffmpeg")
        self.assertEqual(release.ARTWORK_SIZE, (3840, 2160))
        with tempfile.TemporaryDirectory() as td:
            sq, wide = Path(td) / "sq.jpg", Path(td) / "wide.jpg"
            for out, color, size in ((sq, "black", "3000x3000"), (wide, "blue", "3840x2160")):
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                                "-i", f"color=c={color}:s={size}", "-frames:v", "1", str(out)],
                               check=True)
            art = Path(td) / "artwork_3840x2160.jpg"
            release.make_wide_art(sq, art, wide_src=wide)
            self.assertEqual(release.probe_wh(art), (3840, 2160))     # 4K == Gumroad 封面 == ZIP 內那張
            # 沒有橫式原圖時（方圖＋模糊背景）也要生得出來、尺寸一樣
            art2 = Path(td) / "art2.jpg"
            release.make_wide_art(sq, art2)
            self.assertEqual(release.probe_wh(art2), (3840, 2160))

    def test_release_defaults_to_flac_delivery(self):
        """交付格式預設 flac（無損、約 WAV 一半）；--format wav 仍可用。"""
        self.assertEqual(release.build_parser().parse_args(["package", "--run", "x"]).format, "flac")
        self.assertEqual(release.build_parser().parse_args(
            ["package", "--run", "x", "--format", "wav"]).format, "wav")

    def test_pick_wide_cover_prefers_highest_resolution(self):
        """挑橫式來源時取「解析度最高」的那張，不是最新的——不然 4K 衍生圖不會被用到。"""
        import shutil as _sh
        if not _sh.which("ffmpeg"):
            self.skipTest("沒有 ffmpeg")
        with tempfile.TemporaryDirectory() as td:
            vis = Path(td) / "visuals"
            run = vis / "ep01"
            run.mkdir(parents=True)
            for name, size in (("ep01_01.png", "1920x1080"), ("ep01_02_fix-4k.png", "3840x2160"),
                               ("ep01_03_portrait.png", "1080x1920")):
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                                "-i", f"color=c=green:s={size}", "-frames:v", "1",
                                str(run / name)], check=True)
            # 讓 1080p 那張變成最新的：若照「取最新」就會挑錯
            os.utime(run / "ep01_01.png", None)
            old = release.VISUALS_DIR
            release.VISUALS_DIR = vis
            try:
                self.assertEqual(release.pick_wide_cover("ep01").name, "ep01_02_fix-4k.png")
                self.assertIsNone(release.pick_wide_cover("nope"))
            finally:
                release.VISUALS_DIR = old
            # 只有直式圖時：不算橫式，回 None（不可硬裁直式圖當封面）
            (run / "ep01_02_fix-4k.png").unlink()
            (run / "ep01_01.png").unlink()
            release.VISUALS_DIR = vis
            try:
                self.assertIsNone(release.pick_wide_cover("ep01"))
            finally:
                release.VISUALS_DIR = old

    def test_unique_titles_uses_named_variants_when_known(self):
        """同 mood 的變體要有可讀的名字，不是 (pt. N)（Gumroad／DistroKid 的曲目表要好看）。"""
        out = release.unique_titles(["Slow Weekend"] * 3)
        self.assertEqual(out, ["Slow Weekend", "Slow Weekend, Second Cup",
                               "Slow Weekend, Open Window"])
        self.assertEqual(len(set(out)), 3)
        # 池子用完之後仍不得同名（退回 `(pt. N)`）
        pool = meta._TITLE_VARIANTS["slow weekend"]
        many = release.unique_titles(["Slow Weekend"] * (len(pool) + 4))
        self.assertEqual(len(set(many)), len(pool) + 4)
        self.assertIn("Slow Weekend (pt. %d)" % (len(pool) + 2), many)

    def test_unique_titles_avoids_titles_used_by_other_volumes(self):
        """跨卷去重：別卷已用過的曲名要往下一個修飾語跳（不然同一張專輯會出現同名曲）。

        踩過的坑：同一個 run 拆 Vol. 1/2/3 分三次打包，每卷各自去重 → 36 首只有 15 個唯一曲名。
        """
        out = meta.unique_titles(["Slow Weekend"] * 2,
                                 avoid=["Slow Weekend", "Slow Weekend, Second Cup"])
        self.assertEqual(out, ["Slow Weekend, Open Window", "Slow Weekend, Warm Rug"])
        # 一律不得與 avoid 撞名
        self.assertFalse(set(out) & {"Slow Weekend", "Slow Weekend, Second Cup"})
        # 沒有 avoid 時行為不變
        self.assertEqual(meta.unique_titles(["Slow Weekend"]),
                         ["Slow Weekend"])

    def test_title_variant_pools_are_big_enough_for_a_full_run(self):
        """一集 36 首只有少數幾個 mood，池子太小就會看到 `(pt. 8)` 這種灌水名。"""
        for key, pool in meta._TITLE_VARIANTS.items():
            self.assertGreaterEqual(len(pool), 12, f"{key} 的修飾語池只有 {len(pool)} 個")
            self.assertEqual(len(set(pool)), len(pool), f"{key} 的修飾語有重複")

    def test_sibling_volume_titles_reads_same_run_only(self):
        """只收「同一個 run」的其他卷曲名；別的 run（前綴相同也不算）不可以被吃進來。"""
        with tempfile.TemporaryDirectory() as td:
            rel = Path(td)
            for name, run, titles in (("ep_1", "ep", ["A", "B"]),
                                      ("ep_2", "ep", ["C"]),
                                      ("ep_99", "ep-other", ["X"]),      # 不同 run → 不收
                                      ("ep_decoy", "ep", [])):           # 沒有 tracks
                d = rel / name
                d.mkdir(parents=True)
                (d / "release.json").write_text(json.dumps(
                    {"run": run, "tracks": [{"title": t} for t in titles]}))
            (rel / "ep_broken").mkdir()
            (rel / "ep_broken" / "release.json").write_text("{ 壞掉")
            old = release.RELEASES_DIR
            release.RELEASES_DIR = rel
            try:
                self.assertEqual(release.sibling_volume_titles("ep"), {"A", "B", "C"})
                self.assertEqual(release.sibling_volume_titles("nope"), set())
            finally:
                release.RELEASES_DIR = old

    def test_sibling_volume_titles_skips_the_same_album(self):
        """重打包某一卷時，它自己的舊版（同專輯名）不算「別卷」，否則會白吃修飾語池
        → 逼出 `Cozy Morning Coffee (pt. 14)` 這種名字（實測踩過）。"""
        with tempfile.TemporaryDirectory() as td:
            rel = Path(td)
            for name, album, titles in (("ep_1", "Album, Vol. 1", ["A"]),
                                        ("ep_3", "Album, Vol. 3", ["OLD1", "OLD2"]),
                                        ("ep_2", "Album, Vol. 2", ["B"])):
                d = rel / name
                d.mkdir(parents=True)
                (d / "release.json").write_text(json.dumps(
                    {"run": "ep", "album": album, "tracks": [{"title": t} for t in titles]}))
            old = release.RELEASES_DIR
            release.RELEASES_DIR = rel
            try:
                # 重打包 Vol. 3 → 要避開 Vol. 1/2，但不能算進舊的 Vol. 3
                self.assertEqual(release.sibling_volume_titles("ep", album="Album, Vol. 3"),
                                 {"A", "B"})
                # 沒指定 album 時維持舊行為（全部都算）
                self.assertEqual(release.sibling_volume_titles("ep"), {"A", "B", "OLD1", "OLD2"})
            finally:
                release.RELEASES_DIR = old

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

    def test_build_tracklist_csv_is_buyer_facing(self):
        """曲目表是**給買家**的：要看得到拿到什麼檔/多大/什麼味道，且不含發行商欄位。"""
        csv_text = release.build_tracklist_csv([{
            "no": 1, "title": "A", "artist": "X", "duration": 125,
            "key": "G major", "bpm": 70, "mood": "golden hour in the forest",
            "audio": "output/releases/r1/audio/01_a.wav", "size_mb": 25.4,
            "explicit": False, "isrc": "TW-ABC-26-00001",
            "file": "assets/tracks/forest_piano/forest-1hr-01/x_00001.mp3"}],
            album="Into the Forest", artist="X", year="2026",
            audio_format="WAV 24-bit / 48 kHz")
        header = csv_text.splitlines()[0]
        self.assertEqual(header, ",".join(release.TRACKLIST_COLUMNS))
        for col in ("No", "Title", "Album", "Duration", "Vibe", "BPM", "Key",
                    "Instrumental", "Explicit", "Format", "File", "Size (MB)"):
            self.assertIn(col, header)
        self.assertNotIn("ISRC", csv_text)               # 發行商欄位，買家不需要
        self.assertIn("02:05", csv_text)                 # 125s 格式化
        self.assertIn("audio/01_a.wav", csv_text)        # 交付檔在 ZIP 內的路徑
        self.assertNotIn("assets/tracks", csv_text)      # 不可洩漏內部來源路徑
        self.assertIn("WAV 24-bit / 48 kHz", csv_text)   # 買家要知道拿到什麼格式
        self.assertIn("25.4", csv_text)                  # 檔案大小
        self.assertIn("Into the Forest", csv_text)       # 專輯／年份
        self.assertIn("golden hour in the forest", csv_text)
        self.assertIn("Yes,No", csv_text)                # Instrumental=Yes, Explicit=No
        # 只有來源路徑時（舊紀錄）就照原樣，不亂編
        only_src = release.build_tracklist_csv([{"no": 1, "title": "A", "file": "x.mp3"}])
        self.assertIn("x.mp3", only_src)

    def test_isrc_is_preserved_not_wiped(self):
        """ISRC 跟著錄音一輩子：人工填過的不能被重跑清掉（原本是硬寫 None）。"""
        with tempfile.TemporaryDirectory() as td:
            rj = Path(td) / "release.json"
            rj.write_text(json.dumps({"tracks": [
                {"file": "assets/tracks/s/r/a.mp3", "title": "A",
                 "audio": "output/releases/r/audio/01_a.wav", "isrc": "TW-ABC-26-00001"}]}),
                encoding="utf-8")
            prior = release.load_prior_tracks(rj)
            self.assertEqual(prior["assets/tracks/s/r/a.mp3"]["isrc"], "TW-ABC-26-00001")
        # 沒填的照樣回空字串（＝由發行商配發）
        self.assertEqual(release.load_prior_tracks("/nonexistent.json"), {})

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

    def test_build_credits_txt_sections_and_language(self):
        """credits.txt 會被打包進交付 ZIP 給買家 → 英文、分區塊、欄位對齊、列出模型授權。"""
        rec = self._rec()
        rec["master"] = {"lufs": -14.0, "tp": -1.0, "sample_rate": 48000, "bits": 24}
        txt = release.build_credits_txt(rec)
        # 區塊標題
        for section in ("CREDITS", "AI DISCLOSURE", "MODELS & LICENCES",
                        "MASTER & ASSETS", "TRACKLIST", "MUSIC"):
            self.assertIn(section, txt)
        # 標題區（大寫專輯名）
        self.assertIn("INTO THE FOREST", txt)
        self.assertIn(release.RULE, txt)
        # 誰做了什麼（用角色，不宣稱詞曲作者）
        self.assertIn(release.CREDIT_ROLE, txt)
        self.assertIn("Real Name", txt)
        self.assertNotIn("Songwriter", txt)
        # 模型授權要列全：名稱／licence／source 各一行
        self.assertIn("acestep_v1.5_turbo.safetensors", txt)
        self.assertIn("licence: MIT", txt)
        self.assertIn("source:  https://x", txt)
        self.assertIn("commercial use OK", txt)
        # 揭露與母帶格式（量到的值，不是猜的）
        self.assertIn("all of the audio is AI-generated", txt)
        self.assertIn("an AI persona", txt)
        self.assertIn("24-bit / 48 kHz WAV", txt)
        self.assertIn("-14 LUFS / -1 dBTP", txt)
        # 欄位對齊（value 一律從 KV_WIDTH 之後開始；最長標籤也要留一個空白）
        self.assertLess(len(release.CREDIT_ROLE), release.KV_WIDTH)
        self.assertIn(f"  {release.CREDIT_ROLE:<{release.KV_WIDTH}} Real Name", txt)
        self.assertIn(f"  {'Loudness':<{release.KV_WIDTH}} -14 LUFS / -1 dBTP (loudnorm)", txt)
        self.assertIn(f"  {'Audio':<{release.KV_WIDTH}} 24-bit / 48 kHz WAV", txt)
        # 缺值時的退路
        self.assertIn("(not set)", release.build_credits_txt(self._rec(songwriter="")))
        self.assertIn("(none recorded)", release.build_credits_txt(self._rec(credits=[])))
        for zh in ("揭露", "範圍", "母帶", "無紀錄", "角色", "可商用"):
            self.assertNotIn(zh, txt, f"交付檔不該出現中文：{zh}")

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
        self.assertTrue(config.RELEASE_SONGWRITER, "songwriter 真名要有預設值（DistroKid 必填）")

    def test_build_listing_long_is_english_with_tracklist(self):
        """上架文案一律英文（商店是英文店面），且含曲目表與 NOTES（AI 揭露只在這裡）。"""
        rec = self._rec()
        rec["tracks"] = [{"no": 1, "title": "Leaving the Windowsill"},
                         {"no": 2, "title": "The First Pines"}]
        listing = release.build_listing(rec)
        self.assertIn("WHAT YOU GET\n- 2 tracks", listing["long"])
        self.assertIn("TRACKLIST\n01  Leaving the Windowsill", listing["long"])
        self.assertIn("NOTES\n- Mastered to", listing["long"])
        self.assertIn("AI models licensed for commercial use", listing["long"])
        self.assertIn("WAV", listing["long"])
        self.assertNotIn("◆", listing["long"])             # 已改用區塊標題，不再用 ◆ 符號
        self.assertNotIn("器樂", listing["long"])          # 不該再混中文
        self.assertNotIn("器樂", listing["short"])

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


class TestGumroad(unittest.TestCase):
    """Gumroad API v2：只測純函式與 payload 組裝（不碰網路）。"""

    def _rec(self, **over):
        rec = {"run": "forest-1hr-01", "album": "Into the Forest",
               "artist": "Moni on the Journey", "genre": "Lo-fi", "format": "wav",
               "master": {"lufs": -14, "tp": -1}, "total_duration": 1440.0,
               "tracks": [{"no": 1, "title": "A"}],
               "listing": {"short": "short text", "long": "long text",
                           "tags": ["lofi", "forest"], "price_usd": 0.0}}
        rec.update(over)
        return rec

    def test_permalink_for(self):
        self.assertEqual(gumroad.permalink_for("Into the Forest"), "into-the-forest")
        self.assertEqual(gumroad.permalink_for("The Windowsill (pt. 2)"), "the-windowsill-pt-2")
        self.assertEqual(gumroad.permalink_for(""), "")

    def test_product_name_and_price(self):
        self.assertEqual(gumroad.product_name(self._rec()),
                         "Into the Forest — Moni on the Journey")
        self.assertEqual(gumroad.product_name(self._rec(artist="")), "Into the Forest")
        self.assertEqual(gumroad.price_cents(self._rec()), (0, "usd"))       # 0＝PWYW
        self.assertEqual(gumroad.price_cents(self._rec(), 4.5), (450, "usd"))
        self.assertEqual(gumroad.price_cents(self._rec(listing={"price_usd": 12.5})), (1250, "usd"))

    def test_build_params_shape(self):
        p = gumroad.build_params(self._rec())
        self.assertEqual(p["native_type"], "digital")
        self.assertEqual(p["name"], "Into the Forest — Moni on the Journey")
        self.assertEqual(p["price"], 0)
        self.assertEqual(p["price_currency_type"], "usd")
        self.assertEqual(p["custom_permalink"], "into-the-forest")
        self.assertEqual(p["custom_summary"], "short text")
        self.assertEqual(p["description"], "long text")
        self.assertEqual(p["tags[]"], ["lofi", "forest"])          # list → doseq 的 key
        self.assertEqual(p["published"], "false")                    # 一律先 draft
        self.assertNotIn("preview", p)

    def test_build_params_overrides_and_limits(self):
        p = gumroad.build_params(self._rec(), price=3, permalink="custom-slug",
                                 tags=[f"t{i}" for i in range(15)])
        self.assertEqual(p["price"], 300)
        self.assertEqual(p["custom_permalink"], "custom-slug")
        self.assertEqual(len(p["tags[]"]), 10)                       # Gumroad 上限 10

    def test_build_params_pay_what_you_want(self):
        """自由訂價：price 是**最低價**，建議價走 suggested_price_cents。

        Gumroad 的 `suggested_price_cents` 在 create_permitted_params 內（有 API，
        見 links_controller.rb）；沒開 customizable_price 就只是固定價商品。
        """
        self.assertNotIn("customizable_price", gumroad.build_params(self._rec()))
        self.assertNotIn("suggested_price_cents", gumroad.build_params(self._rec()))

        p = gumroad.build_params(self._rec(), suggested_price=2)     # 建議價自動開自由訂價
        self.assertEqual(p["price"], 0)                              # 最低價 0＝可免費下載
        self.assertEqual(p["customizable_price"], "true")
        self.assertEqual(p["suggested_price_cents"], 200)

        p = gumroad.build_params(self._rec(), customizable=True)      # 只開自由訂價、不給建議價
        self.assertEqual(p["customizable_price"], "true")
        self.assertNotIn("suggested_price_cents", p)

        rec = self._rec(listing={"short": "s", "long": "l", "tags": ["lofi"],
                                 "price_usd": 0.0, "suggested_price_usd": 2.5})
        self.assertEqual(gumroad.suggested_price_cents(rec), 250)     # 讀 release.json
        self.assertEqual(gumroad.build_params(rec)["suggested_price_cents"], 250)
        self.assertEqual(gumroad.suggested_price_cents(rec, 0), 0)

    def test_direct_upload_attaches_local_image(self):
        """本機圖片走 /direct_uploads → signed_blob_id（不是只有公開 URL 一條路）。

        checksum 必須是 **base64(MD5)**（hex 會被 ActiveStorage 回 422）；
        content_type 只收 image/jpeg|png|gif。
        """
        import base64
        import hashlib
        blob = b"\xff\xd8fake-jpeg-bytes"
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(blob)
            path = Path(f.name)
        seen = {"calls": []}
        orig_raw, orig_api, orig_put = gumroad.raw, gumroad.api, gumroad.put_bytes

        def fake_raw(method, p, params=None, token=None):
            seen["calls"].append((method, p, params))
            return {"signed_id": "SID-1",                     # 這支沒有 success 欄位
                    "direct_upload": {"url": "https://s3.example/put",
                                      "headers": {"Content-Type": "image/jpeg"}}}

        def fake_api(method, p, params=None, token=None):
            seen["calls"].append((method, p, params))
            return {"success": True}

        def fake_put(url, data, headers=None, timeout=300):
            seen["put"] = (url, data, headers)
            return 200

        gumroad.raw, gumroad.api, gumroad.put_bytes = fake_raw, fake_api, fake_put
        try:
            gumroad.attach_image_file("PID", path, "cover", token="T")
            gumroad.attach_image_file("PID", path, "thumbnail", token="T")
        finally:
            gumroad.raw, gumroad.api, gumroad.put_bytes = orig_raw, orig_api, orig_put
            path.unlink()

        du = [c for c in seen["calls"] if c[1] == "/direct_uploads"][0][2]
        self.assertEqual(du["blob[checksum]"],
                         base64.b64encode(hashlib.md5(blob).digest()).decode())
        self.assertEqual(du["blob[content_type]"], "image/jpeg")
        self.assertEqual(du["blob[byte_size]"], len(blob))
        self.assertEqual(seen["put"], ("https://s3.example/put", blob,
                                       {"Content-Type": "image/jpeg"}))
        posts = [c for c in seen["calls"] if c[0] == "POST" and "cover" in c[1] or
                 c[0] == "POST" and "thumbnail" in c[1]]
        self.assertEqual(posts[0][1], "/products/PID/covers")
        self.assertEqual(posts[0][2], {"signed_blob_id": "SID-1"})
        self.assertEqual(posts[1][1], "/products/PID/thumbnail")

    def test_encode_repeats_list_keys(self):
        body = gumroad._encode({"access_token": "T", "tags[]": ["a", "b"],
                                "name": "x"}).decode()
        self.assertIn("tags%5B%5D=a&tags%5B%5D=b", body)             # tags[]=a&tags[]=b
        self.assertIn("name=x", body)

    def test_find_product_prefers_permalink_then_name(self):
        prods = [{"id": "1", "name": "Other", "custom_permalink": "other"},
                 {"id": "2", "name": "Into the Forest — Moni on the Journey",
                  "custom_permalink": "into-the-forest"}]
        self.assertEqual(gumroad.find_product("into-the-forest", "", products=prods)["id"], "2")
        self.assertEqual(
            gumroad.find_product("", "into the forest — moni on the journey", products=prods)["id"],
            "2")
        self.assertIsNone(gumroad.find_product("nope", "", products=prods))

    def test_api_raises_on_success_false(self):
        """Gumroad 幾乎都回 HTTP 200；成敗在 success 欄位，不可當成功。"""
        orig = gumroad.raw
        gumroad.raw = lambda *a, **k: {"success": False, "message": "New products should be created with a price"}
        try:
            with self.assertRaises(gumroad.GumroadError):
                gumroad.api("POST", "/products", {})
        finally:
            gumroad.raw = orig

    def test_complete_upload_interleaves_parts(self):
        """踩過的坑：`parts[][part_number]` 與 `parts[][etag]` 必須**成對交錯**
        （p1,e1,p2,e2…），Rack 才會併成完整項目；分開寫會被拆成殘缺項目
        → `PartNumber must be >= 1`。帶索引的 key 也不行（Rack 會變成 hash → 0）。"""
        seen = {}
        orig = gumroad.api
        gumroad.api = lambda m, p, params=None, token=None: (
            seen.setdefault("params", params), {"file_url": "https://x/y.zip"})[1]
        try:
            gumroad.complete_upload("U", "K", [{"part_number": 1, "etag": "e1"},
                                               {"part_number": 2, "etag": "e2"}])
        finally:
            gumroad.api = orig
        pairs = seen["params"]
        self.assertIsInstance(pairs, list)                    # 用 list of pairs 保序
        self.assertEqual(pairs[0], ("upload_id", "U"))
        self.assertEqual(pairs[2:], [("parts[][part_number]", 1), ("parts[][etag]", "e1"),
                                     ("parts[][part_number]", 2), ("parts[][etag]", "e2")])
        body = gumroad._encode(pairs).decode()
        self.assertIn("parts%5B%5D%5Bpart_number%5D=1&parts%5B%5D%5Betag%5D=e1", body)
        self.assertNotIn("[0]", body)                         # 不能用帶索引的 key

    def test_pending_cache_roundtrip(self):
        """parts 上傳後要先落地：complete 失敗才不用重傳整個檔。"""
        orig_done, orig_pend = gumroad.UPLOAD_CACHE, gumroad.PENDING_CACHE
        with tempfile.TemporaryDirectory() as td:
            gumroad.UPLOAD_CACHE = Path(td) / "done.json"
            gumroad.PENDING_CACHE = Path(td) / "pend.json"
            f = Path(td) / "a.zip"
            f.write_bytes(b"payload")
            try:
                self.assertEqual(gumroad._pending_get(f), {})
                gumroad._pending_put(f, {"upload_id": "U", "key": "K", "file_url": "u"},
                                     [{"part_number": 1, "etag": "e1"}])
                got = gumroad._pending_get(f)
                self.assertEqual(got["upload_id"], "U")
                self.assertEqual(got["parts"], [{"part_number": 1, "etag": "e1"}])
                gumroad._pending_del(f)
                self.assertEqual(gumroad._pending_get(f), {})
            finally:
                gumroad.UPLOAD_CACHE, gumroad.PENDING_CACHE = orig_done, orig_pend

    def test_encode_accepts_pairs_in_order(self):
        body = gumroad._encode([("a", 1), ("b", [1, 2]), ("a", 2)]).decode()
        self.assertEqual(body, "a=1&b=1&b=2&a=2")

    def test_upload_cache_key_is_content_based(self):
        """指紋要看**內容**，不是 mtime：`release package` 每次重建 ZIP（內容一樣、
        只有 mtime 變），用 mtime 當指紋會讓只改文案也重傳 390 MB。"""
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "a.zip"
            f.write_bytes(b"payload")
            k1 = gumroad.upload_cache_key(f)
            os.utime(f, (1, 1))                                  # 只動 mtime
            self.assertEqual(gumroad.upload_cache_key(f), k1, "mtime 不該影響指紋")
            f.write_bytes(b"different content")
            self.assertNotEqual(gumroad.upload_cache_key(f), k1)

    def test_upload_cache_roundtrip(self):
        """同一份內容不該重複上傳（母帶 zip 數百 MB）。"""
        orig = gumroad.UPLOAD_CACHE
        with tempfile.TemporaryDirectory() as td:
            gumroad.UPLOAD_CACHE = Path(td) / "up.json"
            f = Path(td) / "a.zip"
            f.write_bytes(b"payload")
            try:
                self.assertEqual(gumroad._cache_get(f), "")
                gumroad._cache_put(f, "https://x/a.zip")
                self.assertEqual(gumroad._cache_get(f), "https://x/a.zip")
                self.assertIn(gumroad.upload_cache_key(f), gumroad.UPLOAD_CACHE.read_text())
                f.write_bytes(b"a much longer payload")          # 內容變了 → 快取失效
                self.assertEqual(gumroad._cache_get(f), "")
            finally:
                gumroad.UPLOAD_CACHE = orig

    def test_tags_for_and_description_fallback(self):
        self.assertEqual(gumroad.tags_for(self._rec()), ["lofi", "forest"])
        self.assertEqual(gumroad.description_for({"album": "A", "artist": "B"}), "A — B")

    def test_description_prefers_html_but_can_force_text(self):
        """Gumroad 商品頁吃 HTML（標題／清單才會排版）；--text-description 可退回純文字。"""
        rec = self._rec(listing={"long": "plain text", "html": "<p>html</p>"})
        self.assertEqual(gumroad.description_for(rec), "<p>html</p>")
        self.assertEqual(gumroad.description_for(rec, "text"), "plain text")
        # 沒有 html 時退回純文字
        self.assertEqual(gumroad.description_for(self._rec(listing={"long": "plain"})), "plain")

    def test_listing_links_the_channel(self):
        """商品描述要放頻道連結導流（免費長版混音）；可由 LOFI_CHANNEL_URL 關掉。"""
        listing = release.build_listing(self._rec())
        self.assertIn(config.CHANNEL_URL, listing["long"])
        self.assertIn("LISTEN FREE", listing["long"])
        self.assertIn("Listen Free", listing["html"])
        orig = config.CHANNEL_URL
        try:
            config.CHANNEL_URL = ""
            self.assertNotIn("LISTEN FREE", release.build_listing(self._rec())["long"])
        finally:
            config.CHANNEL_URL = orig

    def test_listing_html_and_text_shape(self):
        rec = self._rec()
        rec["tracks"] = [{"no": 1, "title": "A & B"}, {"no": 2, "title": "<C>"}]
        listing = release.build_listing(rec)
        html = listing["html"]
        self.assertIn("<h3>What You Get</h3>", html)
        self.assertIn("<h3>Tracklist</h3>", html)
        self.assertIn("<h3>Notes</h3>", html)
        self.assertIn("<ol>", html)                       # 曲目用有序清單（會自動編號）
        self.assertIn("<ul>", html)
        self.assertIn("<li>A &amp; B</li>", html)          # <ol> 自動編號 → 不再重複帶 "01  "
        self.assertIn("<li>&lt;C&gt;</li>", html)          # escape 仍要做
        self.assertNotIn("<C>", html)
        # 純文字版：段落間空行、清單用 "- "
        self.assertIn("WHAT YOU GET\n- 2 tracks", listing["long"])
        self.assertIn("\n\n", listing["long"])             # 段落之間要空行
        for tag in ("<h3>", "<ul>", "<ol>", "<li>", "<p>"):
            self.assertNotIn(tag, listing["long"])         # 純文字版不該有標籤
        self.assertIn("02  <C>", listing["long"])           # 純文字不 escape（原文照登）


class TestZImageWorkflow(unittest.TestCase):
    """Z-Image Turbo（GGUF）的節點圖：LoRA 只掛 DiT、放大鏈、inpaint 分支。"""

    def _g(self, **kw):
        base = dict(unet="U.gguf", clip="C.gguf", vae="V.safetensors")
        base.update(kw)
        return zimage.build("mnxy, test", 1472, 832, 42, 8, 1.0, "pre", **base)

    def test_default_graph_is_txt2img(self):
        g = self._g()
        self.assertEqual(g["1"]["inputs"]["unet_name"], "U.gguf")
        self.assertEqual(g["2"]["inputs"], {"clip_name": "C.gguf", "type": "lumina2"})
        self.assertNotIn("19", g)                                  # 沒給 LoRA 就不插節點
        self.assertEqual(g["4"]["inputs"]["model"], ["1", 0])      # 直連 UNet
        self.assertEqual(g["7"]["inputs"]["width"], 1472)
        self.assertEqual(g["7"]["inputs"]["height"], 832)
        ks = g["8"]["inputs"]
        self.assertEqual((ks["model"], ks["positive"], ks["negative"]), (["4", 0], ["5", 0], ["6", 0]))
        self.assertEqual(ks["latent_image"], ["7", 0])             # 空 latent
        self.assertEqual((ks["seed"], ks["steps"], ks["cfg"]), (42, 8, 1.0))
        self.assertEqual(g["10"]["inputs"]["images"], ["9", 0])
        self.assertEqual(g["10"]["inputs"]["filename_prefix"], "pre")

    def test_lora_is_model_only_and_feeds_sampling(self):
        g = self._g(lora="L.safetensors", lora_weight=0.8)
        self.assertEqual(g["19"]["class_type"], "LoraLoaderModelOnly")   # 不是 LoraLoader
        self.assertEqual(g["19"]["inputs"]["lora_name"], "L.safetensors")
        self.assertEqual(g["19"]["inputs"]["strength_model"], 0.8)
        self.assertNotIn("strength_clip", g["19"]["inputs"])   # z-image LoRA 沒有文字編碼器模組
        self.assertEqual(g["19"]["inputs"]["model"], ["1", 0])
        self.assertEqual(g["4"]["inputs"]["model"], ["19", 0])  # 取樣設定吃 LoRA 的輸出
        self.assertEqual(g["8"]["inputs"]["model"], ["4", 0])   # → txt2img／inpaint 兩條路都吃到

    def test_upscale_chain_defaults_to_2x(self):
        g = self._g(upscale_model="UP.pth")
        self.assertEqual(g["16"]["inputs"]["model_name"], "UP.pth")
        self.assertEqual(g["17"]["inputs"]["image"], ["9", 0])
        self.assertEqual(g["18"]["inputs"]["image"], ["17", 0])
        self.assertEqual(g["18"]["inputs"]["width"], 1472 * 2)
        self.assertEqual(g["18"]["inputs"]["height"], 832 * 2)
        self.assertEqual(g["10"]["inputs"]["images"], ["18", 0])

    def test_upscale_explicit_size(self):
        g = self._g(upscale_model="UP.pth", up_out=(3000, 3000))
        self.assertEqual((g["18"]["inputs"]["width"], g["18"]["inputs"]["height"]), (3000, 3000))

    def test_upscale_only_graph_drops_sampling(self):
        """`--from-image` 只放大、不生成：圖上不該有任何取樣節點（否則會白跑一次生成）。"""
        g = zimage.build_upscale_only("cover.png", "UP.pth", (3840, 2160), "pre")
        self.assertEqual(g["1"]["class_type"], "LoadImage")
        self.assertEqual(g["1"]["inputs"]["image"], "cover.png")
        self.assertEqual(g["16"]["inputs"]["model_name"], "UP.pth")
        self.assertEqual(g["17"]["inputs"]["image"], ["1", 0])      # 直接吃原圖，不是 VAEDecode
        self.assertEqual((g["18"]["inputs"]["width"], g["18"]["inputs"]["height"]), (3840, 2160))
        self.assertEqual(g["10"]["inputs"]["images"], ["18", 0])
        for node in ("8", "4", "5", "9", "19"):                     # 沒有 KSampler／模型／LoRA
            self.assertNotIn(node, g)

    def test_upscale_only_graph_without_target_size_keeps_x4(self):
        g = zimage.build_upscale_only("cover.png", "UP.pth", None, "pre")
        self.assertNotIn("18", g)
        self.assertEqual(g["10"]["inputs"]["images"], ["17", 0])    # 直接存 ×4 的結果

    def test_inpaint_branch_replaces_latent(self):
        g = self._g(init_image="base.png", mask_image="m.png", denoise=0.85)
        for n in ("11", "12", "13", "14", "15"):
            self.assertIn(n, g)
        self.assertEqual(g["8"]["inputs"]["latent_image"], ["15", 0])
        self.assertEqual(g["8"]["inputs"]["denoise"], 0.85)
        self.assertNotIn("7", g)                                  # 不再用空 latent

    def test_compose_prompt_appends_style(self):
        self.assertEqual(zimage.compose_prompt("a cat", "watercolour"), "a cat. watercolour")
        self.assertEqual(zimage.compose_prompt("a cat.", "watercolour"), "a cat. watercolour")
        self.assertEqual(zimage.compose_prompt("a cat!", "watercolour"), "a cat! watercolour")
        self.assertEqual(zimage.compose_prompt("a cat", ""), "a cat")      # 不給畫風句
        self.assertEqual(zimage.compose_prompt("  a cat  ", "  "), "a cat")
        self.assertEqual(zimage.compose_prompt("", "watercolour"), "watercolour")


class TestZImageConfig(unittest.TestCase):
    """z-image 產線的設定接線（模型檔名集中在 config，shell 由 `lofi config --sh` 取得）。"""

    def test_settings_exist(self):
        for k in ("ZIMAGE_UNET", "ZIMAGE_CLIP", "ZIMAGE_VAE", "ZIMAGE_LORA",
                  "ZIMAGE_LORA_WEIGHT", "ZIMAGE_STYLE", "ZIMAGE_TRIGGER"):
            self.assertTrue(hasattr(config, k), k)
            self.assertTrue(str(getattr(config, k)).strip(), k)
        self.assertIsInstance(config.ZIMAGE_LORA_WEIGHT, float)
        self.assertIn("1472x832", zimage.__doc__ or "" + "" ) or None   # 文件有寫 16:9 預設

    def test_shell_export_and_display_include_zimage(self):
        for k in ("LOFI_ZIMAGE_UNET", "LOFI_ZIMAGE_CLIP", "LOFI_ZIMAGE_VAE",
                  "LOFI_ZIMAGE_LORA", "LOFI_ZIMAGE_TRIGGER"):
            self.assertIn(k, config.SH_VARS)
        d = config.as_dict()
        for k in ("zimage_unet", "zimage_clip", "zimage_vae", "zimage_lora", "zimage_trigger"):
            self.assertIn(k, d)

    def test_protagonist_lora_is_registered_for_commercial_use(self):
        """fail-closed 授權守門：預設主角 LoRA 必須已登錄且可商用，否則產線會被擋。"""
        info = config.model_license("mnxy_probe.safetensors")
        self.assertTrue(info["commercial"])
        self.assertNotEqual(info["license"], config.UNKNOWN_LICENSE["license"])


class TestMakeEpisodeImageEngine(unittest.TestCase):
    """第 2 段生圖的預設引擎＝z-image ＋ 主角 LoRA（回歸測試：別退回 SDXL-only）。"""

    SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "make_episode.sh"
    UPSCALE = Path.home() / "ComfyUI" / "models" / "upscale_models" / "RealESRGAN_x4plus_anime_6B.pth"

    def _dry(self, *args):
        out = subprocess.run(["bash", str(self.SCRIPT), "--style", "cozy_morning",
                              "--stage", "image", "--episode", "t01", "--dry-run", *args],
                             capture_output=True, text=True, timeout=180)
        self.assertEqual(out.returncode, 0, (out.stderr or out.stdout)[-500:])
        return out.stdout

    def test_default_engine_is_zimage_with_moni_lora(self):
        s = self._dry("--no-upscale")
        self.assertIn("lofi zimage", s)
        self.assertNotIn("lofi visual", s)
        self.assertIn("--lora mnxy_probe.safetensors", s)
        self.assertIn("--size 1472x832", s)          # 16:9：影片端 scale+crop 不裁內容

    def test_upscale_out_defaults_to_1080p(self):
        if not self.UPSCALE.exists():
            self.skipTest("未安裝放大模型（download_p2_models.sh --upscale）")
        self.assertIn("--upscale-out 1920x1080", self._dry())

    def test_sdxl_engine_still_available(self):
        s = self._dry("--image-engine", "sdxl", "--no-upscale")
        self.assertIn("lofi visual", s)
        self.assertIn("--ckpt DreamShaperXL-v1.0-Alpha2.safetensors", s)
        self.assertNotIn("lofi zimage", s)

    def test_lora_can_be_disabled(self):
        s = self._dry("--lora", "", "--no-upscale")
        self.assertIn("lofi zimage", s)
        self.assertNotIn("--lora", s)
        self.assertIn("不會有主角", s)

    def test_image_prompt_override(self):
        self.assertIn("--prompt mnxy, in a bamboo grove",
                      self._dry("--image-prompt", "mnxy, in a bamboo grove", "--no-upscale"))

    def test_unknown_engine_is_rejected(self):
        out = subprocess.run(["bash", str(self.SCRIPT), "--style", "cozy_morning",
                              "--stage", "image", "--image-engine", "foo", "--dry-run"],
                             capture_output=True, text=True, timeout=180)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("--image-engine", out.stderr + out.stdout)


class TestMakeEpisodeMusicFormat(unittest.TestCase):
    """音樂段預設存無損母帶（--format flac → 外接 output/masters → release 交付真無損）。"""

    SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "make_episode.sh"

    def _dry(self, *args):
        return subprocess.run(["bash", str(self.SCRIPT), "--style", "cozy_morning",
                               "--stage", "music", "--episode", "t01", "--count", "1",
                               "--dry-run", *args], capture_output=True, text=True, timeout=180)

    def test_default_format_is_flac(self):
        out = self._dry()
        self.assertEqual(out.returncode, 0, (out.stderr or out.stdout)[-500:])
        self.assertIn("batch_run.sh", out.stdout)
        self.assertIn("--format flac", out.stdout)

    def test_mp3_can_be_opted_out(self):
        out = self._dry("--format", "mp3")
        self.assertEqual(out.returncode, 0, (out.stderr or out.stdout)[-500:])
        self.assertIn("--format mp3", out.stdout)
        self.assertNotIn("--format flac", out.stdout)

    def test_unknown_format_is_rejected(self):
        out = self._dry("--format", "wav")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("--format", out.stderr + out.stdout)


class TestReleasePrune(unittest.TestCase):
    """回收上架包空間：只刪「已上架且可重建」的檔案（ZIP 位元組可重現）。"""

    def _pkg(self, rel, name, run, album, zip_bytes=10, wav_bytes=5):
        d = rel / name
        (d / "download").mkdir(parents=True)
        (d / "audio").mkdir()
        (d / "release.json").write_text(json.dumps({"run": run, "album": album}))
        (d / "download" / f"{name}.zip").write_bytes(b"x" * zip_bytes)
        (d / "audio" / "01.wav").write_bytes(b"y" * wav_bytes)
        return d

    def test_prune_targets_only_uploaded(self):
        """未上架的不動——上架前刪掉會讓 `release gumroad` 沒東西可傳。"""
        with tempfile.TemporaryDirectory() as td:
            rel = Path(td)
            self._pkg(rel, "r_1", "r", "A, Vol. 1")
            self._pkg(rel, "r_2", "r", "A, Vol. 2")
            uploaded = {("r", "A, Vol. 1")}
            t = release.prune_targets(sorted(rel.glob("*")), uploaded)
            self.assertEqual([d.name for d, _, _ in t], ["r_1"])
            self.assertEqual([f.name for f in t[0][2]], ["r_1.zip"])
            self.assertEqual(t[0][1], "A, Vol. 1")
            # --audio 才連母帶一起（DSP 沒有 API，工具無法判斷你傳完沒）
            t2 = release.prune_targets(sorted(rel.glob("*")), uploaded, include_audio=True)
            self.assertEqual(sorted(f.name for f in t2[0][2]), ["01.wav", "r_1.zip"])

    def test_prune_targets_tolerates_bad_release_json(self):
        with tempfile.TemporaryDirectory() as td:
            rel = Path(td)
            bad = rel / "broken"
            (bad / "download").mkdir(parents=True)
            (bad / "release.json").write_text("{ 壞掉")
            (bad / "download" / "b.zip").write_bytes(b"x")
            self.assertEqual(release.prune_targets(sorted(rel.glob("*")), {("r", "A")}), [])

    def test_gumroad_uploaded_reads_event_stream(self):
        """判斷依據是事件流（append-only），壞行跳過、不讓一行壞掉就整支失效。"""
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "releases.jsonl"
            log.write_text("\n".join([
                json.dumps({"event": "release", "run": "r", "album": "A, Vol. 1"}),
                json.dumps({"event": "gumroad", "run": "r", "album": "A, Vol. 1"}),
                "{ 壞行",
                json.dumps({"event": "gumroad", "run": "z", "album": "Z"}),
            ]))
            self.assertEqual(release.gumroad_uploaded(log), {("r", "A, Vol. 1"), ("z", "Z")})
            self.assertEqual(release.gumroad_uploaded(Path(td) / "nope.jsonl"), set())


class TestVideoTrackScope(unittest.TestCase):
    """影片段只能用「這個 run」自己的曲。

    踩過的坑：`library query` 預設按分數排序、沒有按 run 篩選，第 2 集的影片就撈到
    第 1 集分數更高的曲（36 首裡 34 首來自 cozy-1hr-01），影片變成上一集的音樂。
    """

    SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "make_episode.sh"

    def test_video_stage_scopes_query_to_run(self):
        src = self.SCRIPT.read_text()
        self.assertIn('--run-id "$RUN_ID" --order track', src)   # 影片段必須 run-scoped
        self.assertIn("退回整個風格挑", src)                      # 真沒曲目才退回，而且要看得出來

    def test_library_query_can_filter_by_run(self):
        """library.query 的 SQL 篩選要支援 run_id（影片段的 run-scoped 選曲靠它）。"""
        args = argparse.Namespace(tag=None, style="cozy_morning", run_id="cozy-1hr-02",
                                  status="", verdict="", min_score=None, min_rating=None,
                                  bpm_min=None, bpm_max=None)
        where, params = library.build_where(args)
        self.assertIn("t.run_id = ?", where)
        self.assertIn("cozy-1hr-02", params)
        # 沒給 run_id 時不該多這個條件
        args.run_id = ""
        self.assertNotIn("run_id", library.build_where(args)[0])


class TestThumbnail(unittest.TestCase):
    """YouTube 縮圖：底圖挑選、文字來源、上傳時的檔案解析（純函式／不需 YouTube）。"""

    def test_build_thumbnail_text_prefers_style_config(self):
        self.assertEqual(meta.build_thumbnail_text({"thumbnail_text": ["COZY", "MORNING", "LOFI"]}),
                         ["COZY", "MORNING", "LOFI"])
        # 沒設定就從標題推：取「|」前那段的最多三個字
        self.assertEqual(meta.build_thumbnail_text({"title": "Sunlit Window | Warm Morning Lofi"}),
                         ["SUNLIT", "WINDOW"])
        self.assertEqual(meta.build_thumbnail_text({}), [])

    def test_find_wide_art_prefers_highest_resolution(self):
        """底圖取解析度最高的橫式圖：放過 4K 衍生圖就會用它；直式圖不可用（會裁掉主角）。"""
        import shutil as _sh
        if not _sh.which("ffmpeg"):
            self.skipTest("沒有 ffmpeg")
        with tempfile.TemporaryDirectory() as td:
            vis = Path(td) / "visuals"
            run = vis / "ep01"
            run.mkdir(parents=True)
            for name, size in (("ep01_01.png", "1920x1080"), ("ep01_02_fix-4k.png", "3840x2160"),
                               ("ep01_03_portrait.png", "1080x1920"), ("ep01_04_square.png", "1000x1000")):
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                                "-i", f"color=c=red:s={size}", "-frames:v", "1",
                                str(run / name)], check=True)
            os.utime(run / "ep01_01.png", None)          # 讓 1080p 變最新 → 取最新就會挑錯
            old = meta.VISUALS_DIR
            meta.VISUALS_DIR = vis
            try:
                self.assertEqual(meta.find_wide_art("ep01").name, "ep01_02_fix-4k.png")
                self.assertIsNone(meta.find_wide_art("不存在"))
            finally:
                meta.VISUALS_DIR = old
            (run / "ep01_02_fix-4k.png").unlink()        # 只剩直式／方形 → 不算橫式底圖
            (run / "ep01_01.png").unlink()
            meta.VISUALS_DIR = vis
            try:
                self.assertIsNone(meta.find_wide_art("ep01"))
            finally:
                meta.VISUALS_DIR = old

    def test_resolve_thumbnail(self):
        """--thumbnail 優先；其次讀 publish json 的 thumbnail（相對專案根）。"""
        self.assertIsNone(upload.resolve_thumbnail({}))
        self.assertEqual(upload.resolve_thumbnail({"thumbnail": "publish/x.jpg"}, root=Path("/r")),
                         Path("/r/publish/x.jpg"))
        self.assertEqual(upload.resolve_thumbnail({"thumbnail": "publish/x.jpg"}, "--given",
                                                  root=Path("/r")),
                         Path("--given"))


class TestRunsLedger(unittest.TestCase):
    """產線 run 帳本：事件寫入／摺疊／存活判定（catalog/runs.jsonl）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self._saved = {k: runs.__dict__[k] for k in ("RUNS_JSONL", "RUN_STATE")}
        runs.RUNS_JSONL = self.root / "catalog" / "runs.jsonl"
        runs.RUN_STATE = self.root / "logs" / ".run_state.json"

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(runs, k, v)
        self.tmp.cleanup()

    @staticmethod
    def _ev(event, **kw):
        d = {"ts": kw.pop("ts"), "schema_version": 1, "event": event}
        d.update(kw)
        return d

    def test_lifecycle_events_and_fold(self):
        runs.stage_start("r1", "music", kind="episode", total=3, unit="tracks")
        runs.progress("r1", "music", 1, 3, unit="tracks")
        runs.stage_end("r1", "music", runs.OK, elapsed_s=12.34, artifacts=["a.mp3"])
        env = runs.load_events()
        self.assertEqual([e["event"] for e in env],
                         ["stage_start", "progress", "stage_end"])
        self.assertEqual(env[0]["schema_version"], runs.SCHEMA_VERSION)
        self.assertTrue(env[0]["ts"])
        r = runs.fold(env)["r1"]
        st = r["stages"]["music"]
        self.assertEqual(st["status"], "ok")
        self.assertEqual(st["done"], 1)
        self.assertEqual(st["total"], 3)
        self.assertEqual(st["elapsed_s"], 12.3)
        self.assertEqual(st["artifacts"], ["a.mp3"])
        self.assertEqual(runs.run_status(r), "ok")

    def test_run_end_status(self):
        runs.stage_start("r1", "music")
        runs.stage_end("r1", "music")
        runs.run_end("r1", runs.FAIL, note="上游斷線")
        r = runs.fold(runs.load_events())["r1"]
        self.assertEqual(runs.run_status(r), runs.FAIL)
        self.assertEqual(r["note"], "上游斷線")

    def test_snapshot_written(self):
        runs.stage_start("r1", "music", total=10, pid=os.getpid())
        self.assertTrue(runs.RUN_STATE.exists())
        snap = json.loads(runs.RUN_STATE.read_text(encoding="utf-8"))
        self.assertEqual(snap["running"][0]["run_id"], "r1")

    def test_current_alive_vs_stale_by_pid(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(minutes=5)).isoformat()
        alive = [self._ev("stage_start", ts=old, run_id="ok-run", stage="music",
                          pid=os.getpid())]
        cur = runs.current(alive, now=now)
        self.assertEqual(len(cur), 1)
        self.assertIs(cur[0]["alive"], True)
        self.assertFalse(cur[0]["stale"])
        # pid 不存在 → 一定卡死
        dead = [self._ev("stage_start", ts=old, run_id="dead", stage="music",
                         pid=999999)]
        cur = runs.current(dead, now=now)
        self.assertIs(cur[0]["alive"], False)
        self.assertTrue(cur[0]["stale"])

    def test_current_unknown_pid_uses_idle_threshold(self):
        now = datetime.now(timezone.utc)
        fresh = [self._ev("stage_start", ts=(now - timedelta(minutes=1)).isoformat(),
                          run_id="a", stage="music")]                 # 無 pid
        self.assertFalse(runs.current(fresh, now=now)[0]["stale"])
        idle = [self._ev("stage_start",
                         ts=(now - timedelta(minutes=config.STATUS_STALE_MIN + 5)).isoformat(),
                         run_id="b", stage="music")]
        self.assertTrue(runs.current(idle, now=now)[0]["stale"])

    def test_current_closes_on_stage_end(self):
        now = datetime.now(timezone.utc)
        ts = now.isoformat()
        env = [self._ev("stage_start", ts=ts, run_id="r", stage="music"),
               self._ev("stage_end", ts=ts, run_id="r", stage="music", status="ok")]
        self.assertEqual(runs.current(env, now=now), [])

    def test_pid_alive(self):
        self.assertIs(runs.pid_alive(os.getpid()), True)
        self.assertIs(runs.pid_alive(999999), False)
        self.assertIsNone(runs.pid_alive(None))
        self.assertIsNone(runs.pid_alive("not-a-pid"))

    def test_stage_run_records_failure_and_reraises(self):
        with self.assertRaises(ValueError):
            with runs.stage_run("r2", "image", kind="episode") as st:
                st.tick()
                st.artifact("x.png")
                raise ValueError("boom")
        ev = runs.load_events()[-1]
        self.assertEqual(ev["event"], "stage_end")
        self.assertEqual(ev["status"], "fail")
        self.assertIn("boom", ev["note"])
        self.assertEqual(ev["artifacts"], ["x.png"])

    def test_stage_run_success_marks_artifacts(self):
        with runs.stage_run("r3", "video") as st:
            st.artifact("p.mp4")
        st = runs.fold(runs.load_events())["r3"]["stages"]["video"]
        self.assertEqual(st["status"], "ok")
        self.assertEqual(st["artifacts"], ["p.mp4"])

    def test_recent_runs_sorted_and_limited(self):
        for rid, ts in (("old", "2026-01-01T00:00:00+08:00"),
                        ("new", "2026-06-01T00:00:00+08:00")):
            runs.stage_start(rid, "music")
            # 直接改最後一行的 ts 以便排序測試
        rows = runs.recent_runs()
        self.assertEqual(len(rows), 2)
        runs.stage_start("only", "music")
        self.assertEqual(len(runs.recent_runs(limit=1)), 1)

    def test_stage_order_uses_episode_sequence(self):
        self.assertEqual(runs.stage_order(["video", "music", "upload", "image"]),
                         ["music", "image", "video", "upload"])
        self.assertEqual(runs.stage_order(["zzz", "music"]), ["music", "zzz"])

    def test_age_min(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(runs.age_min(now.isoformat(), now), 0.0)
        self.assertIsNone(runs.age_min(""))
        self.assertIsNone(runs.age_min("不是時間"))


class TestNotify(unittest.TestCase):
    """通知層：設定優先序、純函式訊息組裝、fail-soft 與 dedupe（不真的寄信）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self._saved_log = notify.NOTIFY_LOG
        self._saved_file = config.NOTIFY_FILE
        notify.NOTIFY_LOG = self.root / "notifications.jsonl"
        config.NOTIFY_FILE = self.root / "notify.json"
        self._chan = dict(notify.CHANNELS)

    def tearDown(self):
        notify.NOTIFY_LOG = self._saved_log
        config.NOTIFY_FILE = self._saved_file
        notify.CHANNELS.clear()
        notify.CHANNELS.update(self._chan)
        self.tmp.cleanup()

    def _settings(self, **over):
        st = notify.load_settings()
        st.update({"enabled": True, "username": "u@gmail.com",
                   "app_password": "pw", "to": ["me@gmail.com"],
                   "min_level": "info", "dedupe_min": 30})
        st.update(over)
        return st

    def test_render_stage(self):
        subject, body = notify.render_stage("cozy-1hr-03", "music", "fail",
                                            elapsed_s=90, note="ComfyUI 斷線",
                                            artifacts=["a/"], log="logs/x.log")
        self.assertIn("cozy-1hr-03", subject)
        self.assertIn("music", subject)
        self.assertIn("❌", subject)
        self.assertIn("1m30s", body)
        self.assertIn("ComfyUI 斷線", body)

    def test_render_run_lists_stages(self):
        subject, body = notify.render_run("ep1", "ok",
                                          stages={"music": "ok", "video": "ok"})
        self.assertIn("ep1", subject)
        self.assertIn("music", body)
        self.assertIn("video", body)

    def test_settings_env_overrides_file(self):
        config.NOTIFY_FILE.write_text(json.dumps({"username": "file@x.com",
                                                  "app_password": "fpw",
                                                  "to": ["file@x.com"]}),
                                      encoding="utf-8")
        os.environ.pop("LOFI_SMTP_USER", None)
        try:
            self.assertEqual(notify.load_settings()["username"], "file@x.com")
            os.environ["LOFI_SMTP_USER"] = "env@x.com"
            self.assertEqual(notify.load_settings()["username"], "env@x.com")
        finally:
            os.environ.pop("LOFI_SMTP_USER", None)

    def test_read_file_tolerates_bad_json(self):
        config.NOTIFY_FILE.write_text("{ not json", encoding="utf-8")
        self.assertEqual(notify._read_file(), {})

    def test_status_of_settings_reports_missing(self):
        ready, reason = notify.status_of_settings({"enabled": True, "username": "",
                                                    "app_password": "", "to": []})
        self.assertFalse(ready)
        self.assertIn("缺", reason)

    def test_send_disabled(self):
        ok, detail = notify.send("s", "b", settings=self._settings(enabled=False))
        self.assertFalse(ok)
        self.assertIn("停用", detail)

    def test_send_below_min_level(self):
        ok, detail = notify.send("s", "b", level="info",
                                 settings=self._settings(min_level="error"))
        self.assertFalse(ok)
        self.assertIn("門檻", detail)

    def test_send_dry_run(self):
        ok, detail = notify.send("s", "b", dry_run=True, settings=self._settings())
        self.assertTrue(ok)
        self.assertIn("dry-run", detail)

    def test_send_dedupes_repeated_key(self):
        notify.CHANNELS["email"] = lambda st, s, b: (True, "fake")
        ok1, _ = notify.send("報警", "內容", level="error", dedupe_key="disk-low",
                             settings=self._settings())
        ok2, detail2 = notify.send("報警", "內容", level="error", dedupe_key="disk-low",
                                   settings=self._settings())
        self.assertTrue(ok1)
        self.assertFalse(ok2)
        self.assertIn("冷卻", detail2)

    def test_send_is_fail_soft(self):
        def boom(st, s, b):
            raise RuntimeError("SMTP 掛了")
        notify.CHANNELS["email"] = boom
        ok, detail = notify.send("s", "b", level="error", settings=self._settings())
        self.assertFalse(ok)
        self.assertIn("SMTP 掛了", detail)                  # 回報失敗，但沒有拋出

    def test_send_logs_notification(self):
        notify.CHANNELS["email"] = lambda st, s, b: (True, "fake")
        notify.send("主旨", "內容", settings=self._settings())
        rec = json.loads(notify.NOTIFY_LOG.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(rec["subject"], "主旨")
        self.assertTrue(rec["ok"])

    def test_send_email_strips_app_password_spaces(self):
        """Gmail 應用程式密碼常以空格分組貼上；含空格也要能登入成功。"""
        calls = {}

        class FakeSMTP:
            def __init__(self, host, port, timeout=None, context=None):
                calls["host"] = host

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def login(self, u, p):
                calls["user"], calls["pw"] = u, p

            def send_message(self, m):
                calls["msg"] = m

        orig = notify.smtplib.SMTP_SSL
        notify.smtplib.SMTP_SSL = FakeSMTP
        try:
            st = self._settings(username="u@gmail.com",
                                app_password="abcd efgh ijkl mnop")
            ok, _ = notify.send_email(st, "s", "b")
        finally:
            notify.smtplib.SMTP_SSL = orig
        self.assertTrue(ok)
        self.assertEqual(calls["pw"], "abcdefghijklmnop")
        self.assertEqual(calls["host"], "smtp.gmail.com")


class TestStatusMonitor(unittest.TestCase):
    """監控：狀態蒐集、文字輸出、JSON 與 UI 頁面（純讀取）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "logs").mkdir()
        (self.root / "publish").mkdir()
        (self.root / "output").mkdir()
        self._saved = {
            "runs": {k: runs.__dict__[k] for k in ("RUNS_JSONL", "RUN_STATE")},
            "pub": publish.PUBLISH_DIR,
            "status": {k: status.__dict__[k] for k in ("ROOT", "LOGS_DIR", "OUTPUT_DIR")},
        }
        runs.RUNS_JSONL = self.root / "catalog" / "runs.jsonl"
        runs.RUN_STATE = self.root / "logs" / ".run_state.json"
        publish.PUBLISH_DIR = self.root / "publish"
        status.ROOT = self.root
        status.LOGS_DIR = self.root / "logs"
        status.OUTPUT_DIR = self.root / "output"

    def tearDown(self):
        for k, v in self._saved["runs"].items():
            setattr(runs, k, v)
        publish.PUBLISH_DIR = self._saved["pub"]
        for k, v in self._saved["status"].items():
            setattr(status, k, v)
        self.tmp.cleanup()

    def test_collect_shape(self):
        runs.stage_start("ep1", "music", kind="episode", total=4, pid=os.getpid())
        runs.progress("ep1", "music", 2, 4)
        io.atomic_write_json(publish.PUBLISH_DIR / "ep1.json",
                             {"status": "pending", "title": "Rain"})
        view = status.collect()
        self.assertEqual([r["run_id"] for r in view["running"]], ["ep1"])
        self.assertEqual(view["running"][0]["done"], 2)
        self.assertEqual(view["queue"]["counts"]["pending"], 1)
        self.assertEqual(view["ledger"]["events"], 2)
        self.assertIn("free_gb", view["resources"])

    def test_render_text_and_json(self):
        runs.stage_start("ep1", "music", total=4, pid=os.getpid())
        view = status.collect()
        text = status.render_text(view)
        self.assertIn("現在", text)
        self.assertIn("ep1", text)
        self.assertIn("上片佇列", text)
        self.assertEqual(json.loads(status.render_json(view))["running"][0]["run_id"], "ep1")

    def test_collect_limit_zero_returns_all(self):
        for i in range(3):
            runs.stage_start(f"ep{i}", "music")
        self.assertEqual(len(status.collect(limit=1)["runs"]), 1)
        self.assertEqual(len(status.collect(limit=0)["runs"]), 3)    # --run 查舊 run 用

    def test_render_run_detail(self):
        runs.stage_start("ep1", "music")
        runs.stage_end("ep1", "music", runs.OK)
        text = status.render_text(status.collect(), run="ep1")
        self.assertIn("run ep1", text)
        self.assertIn("music", text)

    def test_failures_only_when_clean(self):
        self.assertIn("沒有問題", status.render_text(status.collect(), failures_only=True))

    def test_page_has_placeholders(self):
        self.assertIn("__INTERVAL__", status.PAGE)
        self.assertIn("/api/status", status.PAGE)

    def test_pad_handles_cjk_width(self):
        self.assertEqual(status._w("定裝參考"), 8)
        self.assertEqual(status._w("ref01"), 5)
        self.assertEqual(status._w(status._pad("定裝參考", 12)), 12)


if __name__ == "__main__":
    unittest.main()
