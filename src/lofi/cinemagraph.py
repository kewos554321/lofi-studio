#!/usr/bin/env python3
"""
make_cinemagraph.py — 從一張 lofi 靜圖產生「無縫循環的局部微動」影片（Cinemagraph）。

為什麼用這支而不是 AnimateDiff / SVD：
  Cinemagraph 的本質是「90% 畫面靜止、只有極小局部在動」，而這需要**逐像素的座標控制**。
  在 Mac M4 / 16GB 上，AI 影片模型（AnimateDiff/SVD）又慢又容易爆記憶體，且無法精準鎖定
  「只有窗上的雨、只有檯燈的光暈」在動。這支腳本用程序化方式做到又快又準。

效果模組（全部以 loop 週期 D 為週期，數學上保證頭尾無縫；強度設 0 即關閉）：

預設只開**顆粒＋靜態調色**（暗角/對比/飽和）；所有「會動的」效果預設關閉，
避免換圖時分區錯位。要局部微動再逐一開啟並調分區。

  光線類
    --glow            檯燈/光源暖光呼吸
    --flicker         燈光高頻微閃（燈泡/燭火/霓虹）
    --carlight        窗外車燈掃過（一道暖光橫越窗面）
    --temp            整體色溫呼吸（暖↔冷緩慢漂移）

  動態物體類
    --steam           蒸氣/熱氣上升（咖啡杯、湯）
    --drops           玻璃水珠滑落（貼在玻璃上的水痕，非室內下雨）
    --dust            灰塵微粒在光束中飄浮
    --sway            盆栽葉片微晃
    --curtain-sway    窗簾布幔輕擺
    --rain            窗內雨絲（預設 0=關；易被看成室內下雨）

  表面/材質類
    --sheen           書頁/桌面柔光緩慢掃過

  基礎
    --zoom --drift    全域呼吸（極慢縮放+微飄移；嚴格版設 0）
    --grain --vignette --contrast --saturation

輸入/輸出不需要 Pillow/scipy：影像解碼與編碼都透過 ffmpeg rawvideo 管線，合成用 numpy。

用法:
  python3 scripts/make_cinemagraph.py IMAGE OUT.mp4 [--duration 15] [--fps 30]
  python3 scripts/make_cinemagraph.py IMAGE OUT.mp4 --zoom 0 --drift 0        # 嚴格局部
  python3 scripts/make_cinemagraph.py IMAGE OUT.mp4 --check-loop             # 只驗證無縫
  python3 scripts/make_cinemagraph.py --help

分區座標（比例 0~1，換圖只要重調這裡）:
  --window x0,y0,x1,y1        雨滴/車燈/灰塵的矩形（預設 0.33,0.045,0.70,0.585）
  --lamp cx,cy,r              檯燈光暈/微閃（預設 0.88,0.48,0.22）
  --plant x0,y0,x1,y1         盆栽微晃（預設 0.04,0.52,0.70,0.82）
  --curtain-region x0,y0,x1,y1 窗簾輕擺（預設 0.51,0.15,0.63,0.66）
  --steam-pos cx,cy,r         蒸氣來源與範圍（預設 0.62,0.72,0.10）
  --drop-region x0,y0,x1,y1   玻璃水珠（預設同 --window）
  --dust-region x0,y0,x1,y1   灰塵（預設同 --window）
  --sheen-region cx,cy,r      書頁柔光（預設 0.55,0.87,0.30）
"""
import argparse
import math
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

TAU = 2.0 * math.pi


# ----------------------------- ffmpeg I/O -----------------------------

def read_image(path: Path) -> np.ndarray:
    """用 ffmpeg 把任意圖檔解成 (H, W, 3) float32 [0,1]，不依賴 PIL。"""
    dim = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    w, h = (int(x) for x in dim.split("x"))
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path),
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True).stdout
    arr = np.frombuffer(raw, np.uint8, count=h * w * 3).reshape(h, w, 3)
    return arr.astype(np.float32) / 255.0


class VideoWriter:
    """把 numpy frame 串流給 ffmpeg 編碼成 H.264 mp4。"""
    def __init__(self, path: Path, w: int, h: int, fps: int, crf: int = 18):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps),
             "-i", "-", "-an", "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)],
            stdin=subprocess.PIPE)

    def write(self, frame: np.ndarray):
        self.proc.stdin.write((np.clip(frame, 0, 1) * 255 + 0.5).astype(np.uint8).tobytes())

    def close(self):
        self.proc.stdin.close()
        self.proc.wait()


# ----------------------------- 小工具 -----------------------------

def sstep(v, a, b):
    """平滑步階：v<=a 得 0，v>=b 得 1，中間用 3t²-2t³。"""
    if b == a:
        return (v >= b).astype(np.float32)
    t = np.clip((v - a) / (b - a), 0.0, 1.0)
    return (t * t * (3.0 - 2.0 * t)).astype(np.float32)


def bilinear(img, ys, xs):
    """依浮點座標 (ys, xs) 取樣，形狀同 ys。"""
    h, w = img.shape[:2]
    y0 = np.floor(ys).astype(np.int32)
    x0 = np.floor(xs).astype(np.int32)
    wy = (ys - y0)[..., None].astype(np.float32)
    wx = (xs - x0)[..., None].astype(np.float32)
    y0c = np.clip(y0, 0, h - 1); y1c = np.clip(y0 + 1, 0, h - 1)
    x0c = np.clip(x0, 0, w - 1); x1c = np.clip(x0 + 1, 0, w - 1)
    top = img[y0c, x0c] * (1 - wx) + img[y0c, x1c] * wx
    bot = img[y1c, x0c] * (1 - wx) + img[y1c, x1c] * wx
    return top * (1 - wy) + bot * wy


class Cinemagraph:
    def __init__(self, img, args):
        self.img = img
        self.h, self.w = img.shape[:2]
        self.D = args.duration
        self.args = args
        self.rng = np.random.default_rng(args.seed)

        yy = np.arange(self.h, dtype=np.float32)[:, None]
        xx = np.arange(self.w, dtype=np.float32)[None, :]
        self.yy, self.xx = yy, xx
        f = max(4.0, 0.03 * self.w)

        # --- 矩形分區 ---
        self.win = self._rect(args.window)
        self.plant = self._rect(args.plant)
        self.dust_rect = self._rect(args.dust_region)
        self.drop_rect = self._rect(args.drop_region)
        self.curtain = self._rect(args.curtain_region)
        self.win_mask = self._rect_mask(self.win, f)
        self.drop_mask = self._rect_mask(self.drop_rect, f)

        # 盆栽擺幅權重（上緣葉尖最大）
        self.plant_mask = self._rect_mask(self.plant, f)
        tip = 1.0 - sstep(yy, self.plant[1], self.plant[3])
        self.sway_w = (self.plant_mask * (0.35 + 0.65 * tip)).astype(np.float32)
        self.sway_phx = (1.2 * np.sin(xx * 0.02)).astype(np.float32)
        self.sway_phy = (0.8 * np.sin(yy * 0.03)).astype(np.float32)

        # 窗簾擺幅權重（中下緣較大）＋沿 y 的波動相位
        self.curtain_mask = self._rect_mask(self.curtain, f)
        cy0, cy1 = self.curtain[1], self.curtain[3]
        span = max(1.0, cy1 - cy0)
        self.curtain_w = (self.curtain_mask * (0.45 + 0.55 * np.clip((yy - cy0) / span, 0, 1))).astype(np.float32)
        self.curtain_ph = (2.5 * math.pi * np.clip((yy - cy0) / span, 0, 1)).astype(np.float32)

        # 光暈 / 柔光 / 微閃 falloff（橢圓）
        self.lamp_falloff = self._ellipse(*args.lamp)
        self.sheen_falloff = self._ellipse(*args.sheen_region)
        self.flicker_falloff = self._ellipse(*args.flicker_region)

        # 粒子
        self.particles = self._make_rain(args.rain_count)
        self.motes = self._make_motes(args.dust_count)
        self.steam = self._make_steam(args.steam_count)
        self.drops = self._make_drops(args.drop_count)

        # 車燈
        self.car_cycles = int(args.carlight_cycles)

        # 暗角
        rmax = math.hypot(self.w, self.h) / 2.0
        rr = np.sqrt((xx - self.w / 2.0) ** 2 + (yy - self.h / 2.0) ** 2) / rmax
        self.vignette = (1.0 - args.vignette * np.clip(rr, 0, 1) ** 2).astype(np.float32)

    # ---------- 遮罩工具 ----------
    def _rect(self, fr):
        x0, y0, x1, y1 = fr
        return (x0 * self.w, y0 * self.h, x1 * self.w, y1 * self.h)

    def _rect_mask(self, rect, f):
        x0, y0, x1, y1 = rect
        mx = sstep(self.xx, x0, x0 + f) * (1 - sstep(self.xx, x1 - f, x1))
        my = sstep(self.yy, y0, y0 + f) * (1 - sstep(self.yy, y1 - f, y1))
        return (mx * my).astype(np.float32)

    def _ellipse(self, cx, cy, r, power=2.0):
        cx, cy, r = cx * self.w, cy * self.h, max(1e-3, r * self.w)
        gx = (self.xx - cx) / r
        gy = (self.yy - cy) / r
        d = np.sqrt(gx * gx + gy * gy)
        return (np.clip(1.0 - d, 0.0, 1.0) ** power).astype(np.float32)

    def _add_blob(self, layer, x, y, sx, sy, amp):
        """在 layer（單通道）上加一顆高斯柔點。"""
        r = int(3.0 * max(sx, sy)) + 1
        x0 = max(0, int(x) - r); x1 = min(self.w, int(x) + r + 1)
        y0 = max(0, int(y) - r); y1 = min(self.h, int(y) + r + 1)
        if x1 <= x0 or y1 <= y0:
            return
        gy = (np.arange(y0, y1, dtype=np.float32)[:, None] - y) / sy
        gx = (np.arange(x0, x1, dtype=np.float32)[None, :] - x) / sx
        layer[y0:y1, x0:x1] += np.exp(-0.5 * (gx * gx + gy * gy)).astype(np.float32) * amp

    # ---------- 粒子產生 ----------
    def _make_rain(self, n):
        x0, y0, x1, y1 = self.win
        period = y1 - y0
        px = self.rng.uniform(x0, x1, n).astype(np.float32)
        py = self.rng.uniform(0.0, period, n).astype(np.float32)
        length = self.rng.uniform(0.02, 0.07, n).astype(np.float32) * period
        k = self.rng.integers(1, 5, n).astype(np.float32)
        speed = k * period / self.D
        alpha = self.rng.uniform(0.10, 0.40, n).astype(np.float32)
        width = self.rng.choice([1.0, 1.0, 2.0], n).astype(np.float32)
        return px, py, length, speed, alpha, width

    def _make_motes(self, n):
        x0, y0, x1, y1 = self.dust_rect
        cx = self.rng.uniform(x0, x1, n).astype(np.float32)
        cy = self.rng.uniform(y0, y1, n).astype(np.float32)
        ax = self.rng.uniform(0.01, 0.035, n).astype(np.float32) * self.w
        ay = self.rng.uniform(0.01, 0.035, n).astype(np.float32) * self.h
        phx = self.rng.uniform(0.0, TAU, n).astype(np.float32)
        phy = self.rng.uniform(0.0, TAU, n).astype(np.float32)
        base = self.rng.uniform(0.15, 0.55, n).astype(np.float32)
        tw = self.rng.uniform(0.0, TAU, n).astype(np.float32)
        return cx, cy, ax, ay, phx, phy, base, tw

    def _make_steam(self, n):
        cycles = self.rng.integers(1, 3, n).astype(np.float32)
        phase = self.rng.uniform(0.0, 1.0, n).astype(np.float32)
        xoff = self.rng.uniform(-1.0, 1.0, n).astype(np.float32)
        swirl = self.rng.uniform(0.4, 1.2, n).astype(np.float32)
        swph = self.rng.uniform(0.0, TAU, n).astype(np.float32)
        base = self.rng.uniform(0.35, 0.8, n).astype(np.float32)
        size = self.rng.uniform(0.6, 1.3, n).astype(np.float32)
        return cycles, phase, xoff, swirl, swph, base, size

    def _make_drops(self, n):
        x0, y0, x1, y1 = self.drop_rect
        x = self.rng.uniform(x0, x1, n).astype(np.float32)
        phase = self.rng.uniform(0.0, 1.0, n).astype(np.float32)
        cycles = self.rng.integers(1, 3, n).astype(np.float32)
        drift = self.rng.uniform(-1.0, 1.0, n).astype(np.float32) * max(2.0, 0.02 * self.w)
        size = self.rng.uniform(0.8, 1.8, n).astype(np.float32)
        alpha = self.rng.uniform(0.12, 0.32, n).astype(np.float32)
        trail = self.rng.integers(3, 9, n).astype(np.float32)
        return x, phase, cycles, drift, size, alpha, trail

    # ---------- 幾何：呼吸 + 盆栽微晃 + 窗簾輕擺（單次重取樣）----------
    def warp(self, t):
        a = self.args
        phase = TAU * t / self.D
        z = 1.0 + a.zoom * (0.5 - 0.5 * math.cos(phase))
        dx = a.drift * math.sin(phase)
        dy = a.drift * 0.6 * math.sin(phase + math.pi / 3.0)
        ch, cw = self.h / z, self.w / z
        cy = self.h / 2.0 + dy * self.h
        cx = self.w / 2.0 + dx * self.w
        ys = np.linspace(cy - ch / 2.0, cy + ch / 2.0, self.h, dtype=np.float32)
        xs = np.linspace(cx - cw / 2.0, cx + cw / 2.0, self.w, dtype=np.float32)
        XS, YS = np.meshgrid(xs, ys)

        tot_x = None
        tot_y = None
        if a.sway > 0:
            tot_x = (a.sway * self.w) * np.sin(phase + self.sway_phx) * self.sway_w
            tot_y = (a.sway * 0.4 * self.h) * np.cos(phase + self.sway_phy) * self.sway_w
        if a.curtain_sway > 0:
            cd = (a.curtain_sway * self.w) * np.sin(phase + self.curtain_ph) * self.curtain_w
            tot_x = cd if tot_x is None else tot_x + cd
        if tot_x is not None:
            return bilinear(self.img, YS + (tot_y if tot_y is not None else 0.0), XS + tot_x)
        return bilinear(self.img, YS, XS)

    # ---------- 各效果層（單通道，回傳 None 表示關閉）----------
    def rain_layer(self, t):
        if self.args.rain <= 0:
            return None
        px, py, length, speed, alpha, width = self.particles
        x0, y0, x1, y1 = self.win
        period = y1 - y0
        layer = np.zeros((self.h, self.w), np.float32)
        ys_all = y0 + np.mod(py + speed * t, period)
        for i in range(len(px)):
            xi = int(round(px[i]))
            if xi < 0 or xi >= self.w:
                continue
            n_seg = max(1, int(round(length[i])))
            frac = np.linspace(1.0, 0.0, n_seg, dtype=np.float32)
            y_from = int(round(ys_all[i]))
            y_to = min(self.h, y_from + n_seg)
            if y_to <= y_from:
                continue
            vals = alpha[i] * frac[:y_to - y_from]
            layer[y_from:y_to, xi] = np.maximum(layer[y_from:y_to, xi], vals)
            if width[i] > 1.5 and xi + 1 < self.w:
                layer[y_from:y_to, xi + 1] = np.maximum(layer[y_from:y_to, xi + 1], vals * 0.6)
        return layer * self.win_mask

    def steam_layer(self, t):
        a = self.args
        if a.steam <= 0:
            return None
        sx, sy, r = a.steam_pos
        cx, cy = sx * self.w, sy * self.h
        rise = r * self.h
        plume = r * self.w * 0.6
        cycles, phase, xoff, swirl, swph, base, size = self.steam
        layer = np.zeros((self.h, self.w), np.float32)
        for i in range(len(cycles)):
            v = (cycles[i] * t / self.D + phase[i]) % 1.0
            env = math.sin(math.pi * v) ** 1.3                      # 頭尾歸零 → 無縫
            y = cy - rise * v
            x = cx + plume * swirl[i] * math.sin(TAU * v + swph[i]) + plume * 0.5 * xoff[i] * v
            rad = (r * self.w * 0.06) * (0.5 + size[i]) * (0.6 + 1.8 * v)
            self._add_blob(layer, x, y, rad * 0.7, rad * 1.5, base[i] * env * a.steam)
        return layer

    def drops_layer(self, t):
        a = self.args
        if a.drops <= 0:
            return None
        x0, y0, x1, y1 = self.drop_rect
        span = y1 - y0
        x, phase, cycles, drift, size, alpha, trail = self.drops
        layer = np.zeros((self.h, self.w), np.float32)
        for i in range(len(x)):
            p = (cycles[i] * t / self.D + phase[i]) % 1.0
            y = y0 + p * span
            xi = x[i] + drift[i] * math.sin(TAU * p)
            tw = 0.6 + 0.4 * math.sin(TAU * p + phase[i])          # 週期性微閃
            self._add_blob(layer, xi, y, size[i], size[i] * 1.1, alpha[i] * tw)   # 亮頭
            for k in range(1, int(trail[i]) + 1):                  # 向上的短拖尾
                self._add_blob(layer, xi, y - k, size[i] * 0.5, size[i] * 0.6,
                               alpha[i] * tw * 0.35 * (1.0 - k / (trail[i] + 1)))
        return layer * self.drop_mask

    def dust_layer(self, t):
        if self.args.dust <= 0:
            return None
        cx, cy, ax, ay, phx, phy, base, tw = self.motes
        phase = TAU * t / self.D
        layer = np.zeros((self.h, self.w), np.float32)
        xs = cx + ax * np.sin(phase + phx)
        ys = cy + ay * np.cos(phase + phy)
        al = base * (0.35 + 0.65 * (0.5 + 0.5 * np.sin(phase + tw)))
        for i in range(len(cx)):
            xi, yi = int(round(xs[i])), int(round(ys[i]))
            if 0 < xi < self.w - 1 and 0 < yi < self.h - 1:
                v = al[i]
                layer[yi, xi] = max(layer[yi, xi], v)
                for ddx, ddy, w in ((-1, 0, .45), (1, 0, .45), (0, -1, .45), (0, 1, .45)):
                    layer[yi + ddy, xi + ddx] = max(layer[yi + ddy, xi + ddx], v * w)
        return layer * self.args.dust

    def carlight_layer(self, t):
        a = self.args
        if a.carlight <= 0:
            return None
        x0, y0, x1, y1 = self.win
        wspan = x1 - x0
        margin = wspan * 0.35
        p = ((self.car_cycles * t / self.D) % 1.0)
        center = (x0 - margin) + p * (wspan + 2 * margin)
        sigma = max(3.0, wspan * a.carlight_width)
        env = math.sin(math.pi * p) ** 2                            # 頭尾歸零 → 無縫
        sep = max(0.0, a.carlight_sep) * wspan                      # 兩道車頭燈的間距
        gx = np.exp(-0.5 * ((self.xx - (center - sep / 2.0)) / sigma) ** 2)
        if sep > 0:
            gx = gx + np.exp(-0.5 * ((self.xx - (center + sep / 2.0)) / sigma) ** 2)
        gy = np.exp(-0.5 * ((self.yy - (y0 + y1) / 2.0) / ((y1 - y0) * 0.42)) ** 2)
        return (gx * gy * env * a.carlight).astype(np.float32)

    def glow_layer(self, t):
        a = self.args
        phase = TAU * t / self.D
        pulse = 0.6 + 0.4 * math.sin(phase) + 0.06 * math.sin(2 * phase)
        return (self.lamp_falloff * pulse * a.glow)[..., None] * np.array([1.00, 0.72, 0.42], np.float32)

    def flicker_layer(self, t):
        a = self.args
        if a.flicker <= 0:
            return None
        phase = TAU * t / self.D
        # 7 倍與 13 倍頻（整數）→ t=D 與 t=0 完全相同
        f = 0.6 + 0.4 * (0.6 * math.sin(7 * phase + 1.1) + 0.4 * math.sin(13 * phase + 2.3))
        return (self.flicker_falloff * max(f, 0.0) * a.flicker)[..., None] * np.array([1.00, 0.82, 0.55], np.float32)

    def sheen_layer(self, t):
        a = self.args
        if a.sheen <= 0:
            return None
        pulse = 0.5 + 0.5 * math.sin(TAU * t / self.D + 1.0)
        return (self.sheen_falloff * pulse * a.sheen)[..., None] * np.array([1.00, 0.90, 0.72], np.float32)

    def temp_shift(self, img, t):
        a = self.args
        if a.temp <= 0:
            return img
        s = a.temp * math.sin(TAU * t / self.D)                     # 0→+→0→-→0，無縫
        out = img.copy()
        out[..., 0] *= (1.0 + s)
        out[..., 2] *= (1.0 - s)
        return out

    # ---------- 合成 ----------
    def render_frame(self, t, grain: bool = True):
        a = self.args
        img = self.warp(t)

        for layer, col in (
            (self.dust_layer(t),      (1.00, 0.97, 0.88)),
            (self.steam_layer(t),     (1.00, 0.96, 0.90)),
            (self.rain_layer(t),      (0.85, 0.90, 1.00)),
            (self.drops_layer(t),     (0.90, 0.95, 1.00)),
            (self.carlight_layer(t),  (1.00, 0.85, 0.60)),
        ):
            if layer is not None:
                img = img + layer[..., None] * np.array(col, np.float32)
        for lay in (self.glow_layer(t), self.flicker_layer(t), self.sheen_layer(t)):
            if lay is not None:
                img = img + lay

        img = (img - 0.5) * a.contrast + 0.5
        gray = img.mean(axis=2, keepdims=True)
        img = gray + (img - gray) * a.saturation
        img = self.temp_shift(img, t)
        img = img * self.vignette[..., None]
        if grain and a.grain > 0:
            img = img + self.rng.normal(0.0, a.grain, (self.h, self.w, 1)).astype(np.float32)
        return np.clip(img, 0.0, 1.0)


# ----------------------------- 主程式 -----------------------------

def parse_rect(s):
    v = [float(x) for x in s.split(",")]
    if len(v) != 4:
        raise argparse.ArgumentTypeError("需要 x0,y0,x1,y1")
    return v


def parse_circle(s):
    v = [float(x) for x in s.split(",")]
    if len(v) != 3:
        raise argparse.ArgumentTypeError("需要 cx,cy,r")
    return v


def main():
    ap = argparse.ArgumentParser(description="lofi 靜圖 -> 無縫循環局部微動（Cinemagraph）")
    ap.add_argument("image")
    ap.add_argument("output", nargs="?", default="output/visual_loop_cinemagraph.mp4")
    ap.add_argument("--duration", type=float, default=15.0, help="循環秒數（建議 10~20）")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--seed", type=int, default=7)

    # 基礎
    ap.add_argument("--zoom", type=float, default=0.0, help="全域縮放幅度（預設 0=關；越小越像靜圖）")
    ap.add_argument("--drift", type=float, default=0.0, help="全域飄移幅度（預設 0=關）")
    ap.add_argument("--grain", type=float, default=0.012)
    ap.add_argument("--contrast", type=float, default=1.04)
    ap.add_argument("--saturation", type=float, default=1.08)
    ap.add_argument("--vignette", type=float, default=0.35)

    # 光線類
    ap.add_argument("--glow", type=float, default=0.0, help="檯燈暖光呼吸（預設 0=關）")
    ap.add_argument("--flicker", type=float, default=0.0, help="燈光高頻微閃（0=關）")
    ap.add_argument("--flicker-region", type=parse_circle, default=[0.88, 0.48, 0.22])
    ap.add_argument("--carlight", type=float, default=0.0, help="窗外車燈掃過（0=關）")
    ap.add_argument("--carlight-cycles", type=int, default=1, help="每個循環掃過幾次")
    ap.add_argument("--carlight-width", type=float, default=0.05, help="車燈光帶寬度（相對窗寬）")
    ap.add_argument("--carlight-sep", type=float, default=0.10, help="兩道車頭燈間距（0=單道）")
    ap.add_argument("--temp", type=float, default=0.0, help="色溫呼吸幅度（預設 0=關）")

    # 動態物體類
    ap.add_argument("--steam", type=float, default=0.0, help="蒸氣/熱氣上升（0=關）")
    ap.add_argument("--steam-pos", type=parse_circle, default=[0.62, 0.72, 0.10], help="蒸氣來源 cx,cy,r")
    ap.add_argument("--steam-count", type=int, default=40)
    ap.add_argument("--drops", type=float, default=0.0, help="玻璃水珠滑落（0=關）")
    ap.add_argument("--drop-count", type=int, default=14)
    ap.add_argument("--dust", type=float, default=0.0, help="灰塵微粒（預設 0=關）")
    ap.add_argument("--dust-count", type=int, default=40)
    ap.add_argument("--sway", type=float, default=0.0, help="盆栽葉片擺幅（預設 0=關）")
    ap.add_argument("--curtain-sway", type=float, default=0.0, help="窗簾輕擺幅度（0=關）")
    ap.add_argument("--rain", type=float, default=0.0, help="窗內雨絲（預設 0=關；易被看成室內下雨）")
    ap.add_argument("--rain-count", type=int, default=90)

    # 表面
    ap.add_argument("--sheen", type=float, default=0.0, help="書頁柔光掃過（預設 0=關）")

    # 分區
    ap.add_argument("--window", type=parse_rect, default=[0.33, 0.045, 0.70, 0.585])
    ap.add_argument("--lamp", type=parse_circle, default=[0.88, 0.48, 0.22])
    ap.add_argument("--plant", type=parse_rect, default=[0.04, 0.52, 0.70, 0.82])
    ap.add_argument("--curtain-region", type=parse_rect, default=[0.51, 0.15, 0.63, 0.66])
    ap.add_argument("--drop-region", type=parse_rect, default=[0.33, 0.045, 0.70, 0.585])
    ap.add_argument("--dust-region", type=parse_rect, default=[0.33, 0.045, 0.70, 0.585])
    ap.add_argument("--sheen-region", type=parse_circle, default=[0.55, 0.87, 0.30])
    ap.add_argument("--check-loop", action="store_true", help="只驗證頭尾無縫，不輸出影片")
    args = ap.parse_args()

    img_path = Path(args.image)
    if not img_path.is_file():
        sys.exit(f"找不到圖片：{img_path}")
    if shutil.which("ffmpeg") is None:
        sys.exit("找不到 ffmpeg，請先安裝 (brew install ffmpeg)")

    img = read_image(img_path)
    cg = Cinemagraph(img, args)
    h, w = img.shape[:2]
    frames = int(round(args.duration * args.fps))
    print(f"==> Cinemagraph {w}x{h}  {args.duration:g}s @ {args.fps}fps = {frames} frames")

    if args.check_loop:
        diff = float(np.abs(cg.render_frame(0.0, grain=False)
                            - cg.render_frame(args.duration, grain=False)).max()) * 255.0
        print(f"無縫檢查：t=0 vs t=D 最大像素差 = {diff:.4f} / 255")
        print("  ✅ 頭尾無縫" if diff < 0.5 else "  ❌ 有落差，請回報")
        return

    vw = VideoWriter(Path(args.output), w, h, args.fps, args.crf)
    for i in range(frames):
        vw.write(cg.render_frame(i / args.fps))
        if (i + 1) % 60 == 0 or i + 1 == frames:
            print(f"    {i + 1}/{frames}", end="\r", flush=True)
    vw.close()
    print(f"\n✅ 完成：{args.output}（無縫循環，可直接餵給 render_video.sh）")


if __name__ == "__main__":
    main()
