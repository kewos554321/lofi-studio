"""lofi_ace_negative.py — ComfyUI 自訂節點：讓 ACE-Step 1.5 吃到 negative caption。

## 為什麼需要它

ComfyUI 內建的 `TextEncodeAceStepAudio1.5` **沒有 negative 輸入**（只有 tags/lyrics），
而 ACE-Step 的管線用 `ConditioningZeroOut` + `cfg=1` 當「無負面」——等於 style JSON
裡的 `negative` 完全沒送進模型。

但底層其實支援：`comfy/text_encoders/ace15.py` 的 `ACE15Tokenizer.tokenize_with_weights`
會讀 `caption_negative` 與 `use_negative_caption`，並在
`generate_audio_codes()` 做 CFG 對比（只在 `cfg_scale != 1.0` 且
`generate_audio_codes=True` 時生效）。內建節點沒把這兩個參數傳進去，所以永遠是
「負面＝正面」。本節點把它們補上。

## 安裝（改完要重啟 ComfyUI 才生效）

    mkdir -p ~/ComfyUI/custom_nodes/lofi_ace_negative
    cp scripts/comfy_nodes/lofi_ace_negative.py ~/ComfyUI/custom_nodes/lofi_ace_negative/__init__.py

節點名：`LofiAceStepNegativeEncode`（本產線 `lofi generate --negative` 會用它）。
"""
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

try:
    import torch  # noqa: F401
    import comfy.utils  # noqa: F401
except ImportError:  # 不在 ComfyUI 環境時安靜跳過
    pass
else:

    class LofiAceStepNegativeEncode:
        """ACE-Step 1.5 文字編碼，多一個 negative caption 輸入。"""

        @classmethod
        def INPUT_TYPES(cls):
            return {
                "required": {
                    "clip": ("CLIP",),
                    "tags": ("STRING", {"multiline": True, "dynamicPrompts": True}),
                    "lyrics": ("STRING", {"multiline": True, "dynamicPrompts": True, "default": ""}),
                    "negative": ("STRING", {"multiline": True, "dynamicPrompts": True, "default": ""}),
                    "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF}),
                    "bpm": ("INT", {"default": 120, "min": 10, "max": 300}),
                    "duration": ("FLOAT", {"default": 120.0, "min": 0.0, "max": 2000.0, "step": 0.1}),
                    "timesignature": ("STRING", {"default": "4"}),
                    "language": ("STRING", {"default": "en"}),
                    "keyscale": ("STRING", {"default": "C major"}),
                    "generate_audio_codes": ("BOOLEAN", {"default": True}),
                    "cfg_scale": ("FLOAT", {"default": 2.0, "min": 0.0, "max": 100.0, "step": 0.1}),
                    "temperature": ("FLOAT", {"default": 0.85, "min": 0.0, "max": 2.0, "step": 0.01}),
                    "top_p": ("FLOAT", {"default": 0.9, "min": 0.0, "max": 2000.0, "step": 0.01}),
                    "top_k": ("INT", {"default": 0, "min": 0, "max": 100}),
                    "min_p": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.001}),
                }
            }

        RETURN_TYPES = ("CONDITIONING",)
        FUNCTION = "encode"
        CATEGORY = "conditioning/ace"

        def encode(self, clip, tags, lyrics, negative, seed, bpm, duration, timesignature,
                   language, keyscale, generate_audio_codes, cfg_scale, temperature,
                   top_p, top_k, min_p):
            kw = {
                "lyrics": lyrics,
                "bpm": bpm,
                "duration": duration,
                "timesignature": int(timesignature) if str(timesignature).strip() else 4,
                "language": language,
                "keyscale": keyscale,
                "seed": seed,
                "generate_audio_codes": generate_audio_codes,
                "cfg_scale": cfg_scale,
                "temperature": temperature,
                "top_p": top_p,
                "top_k": top_k,
                "min_p": min_p,
            }
            # 只有真的要負面時才帶這兩個參數；否則維持與內建節點完全相同的行為。
            if negative and negative.strip():
                kw["caption_negative"] = negative
                kw["use_negative_caption"] = True
            tokens = clip.tokenize(tags, **kw)
            return (clip.encode_from_tokens_scheduled(tokens),)

    NODE_CLASS_MAPPINGS = {"LofiAceStepNegativeEncode": LofiAceStepNegativeEncode}
    NODE_DISPLAY_NAME_MAPPINGS = {
        "LofiAceStepNegativeEncode": "ACE-Step 1.5 Encode (with negative)"
    }
