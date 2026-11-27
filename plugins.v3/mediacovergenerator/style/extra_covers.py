"""
精修新增 4 款高定动态封面（13–16）：
13 焦距 (Aperture) · 14 叠影 (LayerCard) · 15 胶卷 (Filmstrip) · 16 窥视 (GazeVeins)

每款均经过双片库真机实测与逐帧精调：
- phase=0 即完整静态封面，phase∈[0, 1) 首尾无缝闭合
- 320×180 / 400×225 缩略图下依然轮廓锐利、标题清晰、转场极具辨识度
"""
import math

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from app.plugins.mediacovergenerator.style.covers import (
    GHOST_WHITE, SILVER, Cover, axes, beats, border_layer, composite_at,
    ease_back, ease_in_out, ease_out, glass_sheen, rgba_from_alpha, rounded_mask,
    slid, smooth, sweep, with_alpha,
)
from app.plugins.mediacovergenerator.style.poster_covers import (
    PosterCover, clamp, drop_shadow, gradient_fill, grain_over, palette_at,
    settled_hue, streak, wall_tooth,
)
from app.plugins.mediacovergenerator.style.quality import EYE_BOX_FRAC, find_best_eye_window
from app.plugins.mediacovergenerator.style.typography import is_cjk_text, load_font


def _lerp(a, b, t):
    return a + (b - a) * t


def _warp_bilinear(arr, sx, sy):
    H, W = arr.shape[:2]
    sx = np.clip(sx, 0.0, W - 1.001)
    sy = np.clip(sy, 0.0, H - 1.001)
    x0 = np.floor(sx).astype(np.int32)
    y0 = np.floor(sy).astype(np.int32)
    fx = sx - x0
    fy = sy - y0
    if arr.ndim == 3:
        fx, fy = fx[..., None], fy[..., None]
    return (
        arr[y0, x0] * (1.0 - fx) * (1.0 - fy)
        + arr[y0, x0 + 1] * fx * (1.0 - fy)
        + arr[y0 + 1, x0] * (1.0 - fx) * fy
        + arr[y0 + 1, x0 + 1] * fx * fy
    )


def _smooth_noise(h, w, sigma, rng):
    raw = (rng.random((h, w)) * 255.0).astype(np.uint8)
    sm = Image.fromarray(raw, mode="L").filter(ImageFilter.GaussianBlur(max(0.5, float(sigma))))
    arr = np.asarray(sm, dtype=np.float32) / 255.0
    lo, hi = float(arr.min()), float(arr.max())
    if hi > lo + 1e-4:
        arr = (arr - lo) / (hi - lo)
    return arr


# ---------------------------------------------------------------- 风格 13：焦距


class Aperture(Cover):
    """
    现代微单取景器 × 2.6× 清脆上下暗钛双帘快门（Mirrorless EVF & Twin-Curtain Snap Shutter）：
    - 纤细高级的现代微单四角对焦括号 + 中心留空精微十字准星（配同心柔暗光晕，任何明暗背景皆清晰克制）；
    - 顶部曝光标尺（EV Scale）、左上片目编号、右上定焦/光圈/快门参数、右下 AF-S 合焦指示与右侧焦距标尺；
    - 2.6× 精确节拍转场（半按合焦绿框锁定 -> 2 帧暗钛双帘高速压合 -> 1 帧正中刀锋合缝闭锁 -> 2 帧疾速弹开闪拍 -> 刀锋收锐落稳）。
    """

    META_SPECS = (
        ("24mm", "f/1.4", "1/500"),
        ("35mm", "f/1.4", "1/250"),
        ("50mm", "f/1.2", "1/1000"),
        ("85mm", "f/1.4", "1/500"),
        ("105mm", "f/2.0", "1/250"),
        ("135mm", "f/1.8", "1/640"),
    )

    # 定稿 2.6× 清脆抓拍快门节拍（15fps 基准 45 帧/拍：闭帘 2 帧 · 中缝闭锁 1 帧 · 弹开 2 帧 ≈ 333ms）
    close_frames = 2
    lock_frames = 1
    open_frames = 2

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        self.frames = [
            ImageEnhance.Contrast(self.fit(i, self.size, clean=True)).enhance(1.07).convert("RGB")
            for i in range(self.count)
        ]
        self.soft_blur = [im.filter(ImageFilter.GaussianBlur(max(1.2, 5.2 * s))) for im in self.frames]

        x_axis, y_axis = axes(self.size)
        self.x_axis = x_axis.astype(np.float32)
        self.y_axis = y_axis.astype(np.float32)
        self.cx_f, self.cy_f = 0.50, 0.485

        radial = np.sqrt(((x_axis - 0.50) / 0.62) ** 2 + ((y_axis - 0.48) / 0.62) ** 2)
        vignette = smooth((radial - 0.54) / 0.62) * 0.54
        bot_left = smooth((0.55 - x_axis) / 0.55) * smooth((y_axis - 0.36) / 0.64) * 0.84
        top_band = smooth((0.15 - y_axis) / 0.15) * 0.36
        bot_band = smooth((y_axis - 0.85) / 0.15) * 0.34
        alpha = np.clip(
            (1.0 - (1.0 - vignette) * (1.0 - bot_left) * (1.0 - top_band) * (1.0 - bot_band)) * self.scrim_strength(),
            0, 1,
        )
        self.scrim = rgba_from_alpha((5, 6, 9), alpha)

        # 阳极氧化暗钛拉丝金属快门帘底纹
        rng = np.random.default_rng(1319)
        row_noise = rng.normal(0.0, 1.0, (H, 1)).astype(np.float32)
        fine_noise = rng.normal(0.0, 0.42, (H, W)).astype(np.float32)
        hor_sheen = 15.0 + 14.0 * np.exp(-(((self.x_axis - 0.42) / 0.42) ** 2))
        vert_grad = 1.0 - 0.16 * np.abs(self.y_axis - 0.5)
        metal = hor_sheen * vert_grad + row_noise * 1.25 + fine_noise * 0.55
        self.curtain_metal = np.stack([metal * 0.94, metal * 0.99, metal * 1.10], axis=-1).astype(np.float32)

        self.pad_x, self.pad_y = W * 0.044, H * 0.070
        self.arm = H * 0.056
        self.hud = self._render_corners_layer((248, 250, 255, 212), shadow=True)

        # 顶部精密曝光补偿标尺（EV Scale）
        ev_layer = Image.new("RGBA", (W * 2, H * 2), (0, 0, 0, 0))
        ed = ImageDraw.Draw(ev_layer)
        ev_y = (self.pad_y + 22 * s) * 2
        ev_cx = W
        ev_span = W * 0.13 * 2
        sh_d = max(1, round(1.3 * s * 2))
        for k in range(-6, 7):
            tx = ev_cx + (k / 6.0) * (ev_span * 0.5)
            major = (k % 3 == 0)
            th = (10.5 if major else 6.0) * s * 2
            a = 198 if major else 118
            w_ev = max(2, round((3.2 if k == 0 else 2.2) * s))
            ed.line([(tx + sh_d, ev_y - th * 0.6 + sh_d), (tx + sh_d, ev_y + th * 0.6 + sh_d)], fill=(0, 0, 0, 120), width=w_ev)
            if k == 0:
                ed.line([(tx, ev_y - th * 0.72), (tx, ev_y + th * 0.72)], fill=self.accent + (248,), width=w_ev)
            else:
                ed.line([(tx, ev_y - th * 0.52), (tx, ev_y + th * 0.52)], fill=(246, 249, 255, a), width=w_ev)
        self.hud.alpha_composite(ev_layer.resize(self.size, Image.Resampling.LANCZOS))

        block, margin, ink_h = self.title_block(
            W * 0.54, sub_style="text", main_fill=GHOST_WHITE, sub_fill=SILVER,
            shadow=self.text_shadow(170),
        )
        composite_at(
            self.hud, block,
            self.pad_x + 34 * s - margin,
            H - self.pad_y - 34 * s - ink_h - margin + self.spec.offset_y * s,
        )

        num_font = load_font(self.fonts[1], max(10, int(round(36 * s))))
        tot_font = load_font(self.fonts[1], max(9, int(round(27 * s))))
        meta_font = load_font(self.fonts[1], max(9, int(round(27 * s))))
        tag_font = load_font(self.fonts[1], max(8, int(round(24 * s))))

        self.counters = []
        cx0, cy0 = self.pad_x + 28 * s, self.pad_y + 22 * s
        dot_r = max(2.5, round(5.5 * s))
        rx_meta = W - self.pad_x - 24 * s
        ry_bot = H - self.pad_y - 22 * s

        for idx in range(self.count):
            c_layer = Image.new("RGBA", self.size, (0, 0, 0, 0))
            cd = ImageDraw.Draw(c_layer)
            cd.ellipse((cx0 - dot_r * 1.9, cy0 - dot_r * 1.9, cx0 + dot_r * 1.9, cy0 + dot_r * 1.9), fill=self.accent + (56,))
            cd.ellipse((cx0 - dot_r, cy0 - dot_r, cx0 + dot_r, cy0 + dot_r), fill=self.accent + (250,))
            txt_x = cx0 + dot_r + 14 * s
            cur_s = f"{idx + 1:02d}"
            cd.text((txt_x + 1, cy0 + 2), cur_s, font=num_font, fill=(0, 0, 0, 150), anchor="lm")
            cd.text((txt_x, cy0 + 1), cur_s, font=num_font, fill=GHOST_WHITE + (248,), anchor="lm")
            nw = num_font.getlength(cur_s)
            cd.text((txt_x + nw + 2 * s, cy0 + 2), f" / {self.count:02d}", font=tot_font, fill=SILVER + (195,), anchor="lm")

            focal, ap, spd = self.META_SPECS[idx % len(self.META_SPECS)]
            meta_txt = f"{focal}  {ap}  {spd}"
            cd.text((rx_meta + 1, cy0 + 2), meta_txt, font=meta_font, fill=(0, 0, 0, 150), anchor="rm")
            cd.text((rx_meta, cy0 + 1), meta_txt, font=meta_font, fill=(238, 242, 248, 212), anchor="rm")

            cd.text((rx_meta + 1, ry_bot + 1), "AF-S", font=tag_font, fill=(0, 0, 0, 140), anchor="rm")
            cd.text((rx_meta, ry_bot), "AF-S", font=tag_font, fill=(215, 222, 232, 138), anchor="rm")
            self.counters.append(c_layer)

        af_lit = Image.new("RGBA", self.size, (0, 0, 0, 0))
        ImageDraw.Draw(af_lit).text((rx_meta, ry_bot), "AF-S", font=tag_font, fill=self.accent + (255,), anchor="rm")
        self.af_lit = af_lit

        self.scale_x = W - self.pad_x - 20 * s
        self.scale_top = H * 0.30
        self.scale_bot = H * 0.70
        self._rest_cache = {}
        self._curtain_cache = {}

    def _render_corners_layer(self, col, shadow=False, lw_mult=1.0):
        W, H, s = self.W, self.H, self.s
        ss = 2
        layer = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        px, py, arm = self.pad_x * ss, self.pad_y * ss, self.arm * ss
        lw = max(2, int(round(2.5 * s * lw_mult * ss)))
        passes = [((0, 0, 0, 118), max(2, round(1.5 * s * ss))), (col, 0)] if shadow else [(col, 0)]
        for c, off in passes:
            for sx, sy, dx, dy in (
                (px, py, 1, 1),
                (W * ss - px, py, -1, 1),
                (px, H * ss - py, 1, -1),
                (W * ss - px, H * ss - py, -1, -1),
            ):
                draw.line([(sx + off, sy + off), (sx + dx * arm + off, sy + off)], fill=c, width=lw)
                draw.line([(sx + off, sy + off), (sx + off, sy + dy * arm + off)], fill=c, width=lw)
        return layer.resize(self.size, Image.Resampling.LANCZOS)

    def _draw_reticle(self, canvas, box_scale=1.0, lock_glow=0.0, flash_white=0.0):
        """纤细高级微单四角对焦括号 + 中心留空十字准星（同心柔暗光晕，绝不发黑生硬）。"""
        W, H, s = self.W, self.H, self.s
        ss = 2
        cx, cy = W * self.cx_f * ss, H * self.cy_f * ss
        bw = H * 0.168 * box_scale * ss
        bh = H * 0.120 * box_scale * ss
        arm = H * 0.036 * (0.86 + 0.14 * box_scale) * ss
        cross = H * 0.0155 * ss
        gap = max(3.5, 5.2 * s * ss)

        if flash_white > 0.05:
            col = (255, 255, 255, int(round(min(255, 205 + 50 * flash_white))))
            glow_col = (255, 255, 255, int(round(100 * flash_white)))
        elif lock_glow > 0.02:
            col = tuple(int(round(_lerp(248, c, lock_glow))) for c in self.accent) + (int(round(165 + 90 * lock_glow)),)
            glow_col = self.accent + (int(round(92 * lock_glow)),)
        else:
            col = (250, 252, 255, 196)
            glow_col = None

        x0, x1 = cx - bw, cx + bw
        y0, y1 = cy - bh, cy + bh
        lw = max(2, round((2.05 + 0.95 * max(lock_glow, flash_white)) * s * ss))
        cw = max(2, round(1.65 * s * ss))

        halo = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
        hd = ImageDraw.Draw(halo)
        h_col = (0, 0, 0, 108)
        for sx, sy, dx, dy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
            hd.line([(sx, sy), (sx + dx * arm, sy)], fill=h_col, width=lw + max(2, round(2.8 * s * ss)))
            hd.line([(sx, sy), (sx, sy + dy * arm)], fill=h_col, width=lw + max(2, round(2.8 * s * ss)))
        for ax, ay, bx, by in (
            (cx - cross - gap, cy, cx - gap, cy),
            (cx + gap, cy, cx + cross + gap, cy),
            (cx, cy - cross - gap, cx, cy - gap),
            (cx, cy + gap, cx, cy + cross + gap),
        ):
            hd.line([(ax, ay), (bx, by)], fill=h_col, width=cw + max(2, round(2.4 * s * ss)))
        halo = halo.filter(ImageFilter.GaussianBlur(max(1.8, 2.8 * s * ss)))

        layer = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        if glow_col is not None:
            gl = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
            gd = ImageDraw.Draw(gl)
            glw = max(4, round(6.0 * s * ss))
            for sx, sy, dx, dy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
                gd.line([(sx, sy), (sx + dx * arm, sy)], fill=glow_col, width=glw)
                gd.line([(sx, sy), (sx, sy + dy * arm)], fill=glow_col, width=glw)
            gl = gl.filter(ImageFilter.GaussianBlur(max(1.5, 2.4 * s * ss)))
            halo.alpha_composite(gl)

        for sx, sy, dx, dy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
            draw.line([(sx, sy), (sx + dx * arm, sy)], fill=col, width=lw)
            draw.line([(sx, sy), (sx, sy + dy * arm)], fill=col, width=lw)

        for ax, ay, bx, by in (
            (cx - cross - gap, cy, cx - gap, cy),
            (cx + gap, cy, cx + cross + gap, cy),
            (cx, cy - cross - gap, cx, cy - gap),
            (cx, cy + gap, cx, cy + cross + gap),
        ):
            draw.line([(ax, ay), (bx, by)], fill=col, width=cw)

        if lock_glow > 0.35:
            dr = max(2.0, 2.6 * s * ss)
            draw.ellipse((cx - dr, cy - dr, cx + dr, cy + dr), fill=self.accent + (int(round(245 * lock_glow)),))

        halo.alpha_composite(layer)
        canvas.alpha_composite(halo.resize(self.size, Image.Resampling.LANCZOS))

    def _curtain_overlay(self, close_amount, speed_blur=0.0):
        key = (round(close_amount, 3), round(speed_blur, 2))
        cached = self._curtain_cache.get(key)
        if cached is not None:
            return cached

        H, W, s = self.H, self.W, self.s
        cy_px = H * self.cy_f
        top_edge = _lerp(-6.0 * s, cy_px, close_amount)
        bot_edge = _lerp(H + 6.0 * s, cy_px, close_amount)

        y_px = np.arange(H, dtype=np.float32)[:, None]
        feather = max(1.1, (1.8 + 6.4 * speed_blur) * s)
        top_cov = smooth((top_edge - y_px) / feather)
        bot_cov = smooth((y_px - bot_edge) / feather)
        curtain_a = np.ones((H, W), dtype=np.float32) if close_amount >= 0.995 else np.clip(np.maximum(top_cov, bot_cov), 0.0, 1.0)

        rgb = self.curtain_metal.copy()
        panel_step = H * 0.165
        d_top = np.maximum(0.0, top_edge - y_px)
        d_bot = np.maximum(0.0, y_px - bot_edge)
        rel_d = np.where(y_px <= cy_px, d_top, d_bot) if close_amount >= 0.995 else np.where(top_cov >= bot_cov, d_top, d_bot)

        mod_p = np.mod(rel_d, panel_step)
        valid_rib = smooth((rel_d - 5.0 * s) / (5.0 * s))
        seam_shadow = np.exp(-((mod_p / max(1.8, 4.2 * s)) ** 1.4)) * 0.42 * valid_rib
        seam_glint = np.exp(-(((mod_p - 1.3 * s) / max(0.8, 1.5 * s)) ** 2)) * 16.0 * valid_rib
        rgb = rgb * (1.0 - seam_shadow[..., None]) + seam_glint[..., None]

        mid_focus = 0.48 + 0.52 * np.exp(-(((self.x_axis - 0.50) / 0.44) ** 2))
        lip_rgb = np.array([186.0, 212.0, 224.0], dtype=np.float32)

        if close_amount < 0.995:
            lip_w = max(1.1, 2.3 * s)
            lip = np.maximum(
                np.exp(-(((y_px - (top_edge - 1.3 * s)) / lip_w) ** 2)),
                np.exp(-(((y_px - (bot_edge + 1.3 * s)) / lip_w) ** 2)),
            )
            rgb = rgb + lip_rgb * (lip * mid_focus * 0.85)[..., None]

            sh_w = max(4.0, 14.0 * s)
            sh_top = np.where(y_px > top_edge, np.exp(-((np.maximum(0.0, y_px - top_edge) / sh_w) ** 1.2)), 0.0)
            sh_bot = np.where(y_px < bot_edge, np.exp(-((np.maximum(0.0, bot_edge - y_px) / sh_w) ** 1.2)), 0.0)
            drop_a = np.maximum(sh_top, sh_bot) * 0.60 * (1.0 - curtain_a)
            total_a = np.clip(curtain_a + drop_a, 0.0, 1.0)
            rgb = rgb * (curtain_a / np.maximum(total_a, 1e-4))[..., None]
        else:
            # 严格中心刀锋对撞合缝 + 双冷银刃边
            center_gap = np.exp(-(((y_px - cy_px) / max(1.0, 2.0 * s)) ** 2))
            lip_hit = np.maximum(
                np.exp(-(((y_px - (cy_px - 3.2 * s)) / max(1.0, 1.8 * s)) ** 2)),
                np.exp(-(((y_px - (cy_px + 3.2 * s)) / max(1.0, 1.8 * s)) ** 2)),
            )
            rgb = rgb * (1.0 - center_gap[..., None] * 0.88) + lip_rgb * (lip_hit * mid_focus * 0.62)[..., None]
            total_a = np.ones((H, W), dtype=np.float32)

        rgba = np.empty((H, W, 4), dtype=np.uint8)
        rgba[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
        rgba[..., 3] = np.clip(total_a * 255.0, 0, 255).astype(np.uint8)
        img = Image.fromarray(rgba, mode="RGBA")
        if len(self._curtain_cache) < 64:
            self._curtain_cache[key] = img
        return img

    def _apply_exposure_flash(self, img, strength):
        if strength <= 0.01:
            return img
        arr = np.asarray(img, dtype=np.float32)
        lifted = arr * (1.0 + strength * 1.28)
        out = lifted + (np.array([255.0, 253.0, 248.0], dtype=np.float32) - np.minimum(lifted, 255.0)) * (strength * 0.50)
        return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), mode="RGB")

    def _shift_image(self, img, dy_px):
        if abs(dy_px) < 0.4:
            return img
        W, H = img.size
        pad = max(1, int(round(abs(dy_px))))
        zoom = 1.0 + (pad * 2.2) / float(H)
        crop_w, crop_h = W / zoom, H / zoom
        left = (W - crop_w) / 2.0
        top = max(0.0, min(H - crop_h, (H - crop_h) / 2.0 - dy_px))
        return img.resize((W, H), Image.Resampling.BILINEAR, box=(left, top, left + crop_w, top + crop_h))

    def _draw_scale(self, draw, step_p):
        s = self.s
        sx = self.scale_x
        ticks = max(self.count * 2 - 1, 7)
        for k in range(ticks):
            ty = _lerp(self.scale_top, self.scale_bot, k / max(1, ticks - 1))
            major = k % 2 == 0
            tw = 19 * s if major else 9.5 * s
            draw.line([(sx - tw, ty), (sx, ty)], fill=(248, 250, 255, 162 if major else 90), width=max(1, round(2 * s)))
        dot_y = _lerp(self.scale_top, self.scale_bot, (step_p % self.count) / max(1, self.count - 1)) if self.count > 1 else self.scale_top
        r = max(2.8, int(round(5.2 * s)))
        dx = sx - 29 * s
        draw.ellipse((dx - r * 1.9, dot_y - r * 1.9, dx + r * 1.9, dot_y + r * 1.9), fill=self.accent + (62,))
        draw.ellipse((dx - r, dot_y - r, dx + r, dot_y + r), fill=self.accent + (255,))

    def frame(self, phase):
        beat_pos = (phase % 1.0) * self.count
        current = int(beat_pos) % self.count
        following = (current + 1) % self.count
        u = beat_pos - int(beat_pos)

        beat_f = 45.0
        af_f = 4.0
        cf = float(self.close_frames)
        lf = float(self.lock_frames)
        of = float(self.open_frames)
        settle_f = 5.0
        trans_f = af_f + cf + lf + of + settle_f
        hold_f = max(8.0, beat_f - trans_f)

        f = u * beat_f
        if f < hold_f or self.count <= 1:
            cached = self._rest_cache.get(current)
            if cached is None:
                canvas = self.frames[current].convert("RGBA")
                canvas.alpha_composite(self.scrim)
                canvas.alpha_composite(self.hud)
                canvas.alpha_composite(self.counters[current])
                self._draw_reticle(canvas, box_scale=1.0, lock_glow=0.0, flash_white=0.0)
                self._draw_scale(ImageDraw.Draw(canvas), float(current))
                self._rest_cache[current] = canvas
                cached = canvas
            return cached.copy()

        s = self.s
        t_f = f - hold_f
        close = speed_blur = flash = shake_y = lock_glow = flash_white_hud = 0.0
        box_scale = 1.0
        scale_pos = float(current)
        counter = self.counters[current]
        blackout = False

        if t_f < af_f:
            p = t_f / af_f
            if p < 0.45:
                q = p / 0.45
                img = slid(Image.blend(self.frames[current], self.soft_blur[current], 0.50 * math.sin(q * math.pi)),
                           1.0 + 0.020 * ease_out(q))
                box_scale = _lerp(1.07, 0.82, ease_out(q))
                lock_glow = 0.40 * q
            else:
                q = (p - 0.45) / 0.55
                img = slid(self.frames[current], 1.020)
                box_scale = 0.82 - 0.022 * math.sin(q * math.pi)
                lock_glow = 1.0
        elif t_f < af_f + cf:
            p = (t_f - af_f + 0.5) / cf
            p_c = clamp(p, 0.15, 0.94)
            img = slid(self.frames[current], _lerp(1.020, 1.028, p_c))
            close = p_c ** 1.15
            speed_blur = 0.72 + 0.28 * p_c
            flash = 0.54 * max(0.0, 1.0 - p_c * 1.4)
            shake_y = 6.0 * s * math.sin(p_c * math.pi)
            box_scale = 0.80
            flash_white_hud = max(0.0, 1.0 - p_c * 1.3)
            scale_pos = _lerp(float(current), current + 0.45, p_c)
        elif t_f < af_f + cf + lf:
            img = self.frames[following]
            close = 1.0
            speed_blur = 0.0
            blackout = True
            counter = self.counters[following]
            scale_pos = current + 0.55
        elif t_f < af_f + cf + lf + of:
            p = (t_f - af_f - cf - lf + 0.5) / of
            p_o = clamp(p, 0.08, 0.92)
            e = 1.0 - (1.0 - p_o) ** 1.25
            img = slid(Image.blend(self.soft_blur[following], self.frames[following], clamp(0.50 + 0.50 * e)),
                       _lerp(1.034, 1.008, e))
            close = 1.0 - e
            speed_blur = (1.0 - p_o) * 0.92
            flash = 0.68 * ((1.0 - p_o) ** 1.2)
            shake_y = -4.8 * s * math.sin(p_o * math.pi)
            counter = self.counters[following]
            box_scale = _lerp(0.84, 1.02, e)
            flash_white_hud = max(0.0, 0.88 * (1.0 - p_o * 1.3))
            scale_pos = _lerp(current + 0.55, float(current + 1), e)
        else:
            p = clamp((t_f - af_f - cf - lf - of) / settle_f, 0.0, 1.0)
            ep = ease_out(p)
            img = slid(self.frames[following], max(1.0, _lerp(1.008, 1.0, ep) - math.sin(p * math.pi) * (1.0 - p) * 0.006))
            flash = 0.14 * ((1.0 - ep) ** 2.0)
            counter = self.counters[following]
            box_scale = _lerp(1.02, 1.0, ep)
            lock_glow = math.sin(((p - 0.15) / 0.55) * math.pi) * 0.50 if 0.15 <= p <= 0.70 else 0.0
            scale_pos = float(current + 1)

        if not blackout:
            img = self._shift_image(img, shake_y)
            img = self._apply_exposure_flash(img, flash)

        canvas = img.convert("RGBA")
        if not blackout:
            canvas.alpha_composite(self.scrim)
        if close > 0.005:
            canvas.alpha_composite(self._curtain_overlay(close, speed_blur))

        canvas.alpha_composite(self.hud)
        canvas.alpha_composite(with_alpha(counter, 0.52) if blackout else counter)

        if not blackout:
            if flash_white_hud > 0.05:
                canvas.alpha_composite(self._render_corners_layer((255, 255, 255, int(round(238 * flash_white_hud))), lw_mult=1.42))
                bw = max(2, int(round(3.8 * s)))
                ImageDraw.Draw(canvas).rectangle((0, 0, self.W - 1, self.H - 1),
                                                 outline=(255, 255, 255, int(round(148 * flash_white_hud))), width=bw)
            elif lock_glow > 0.04:
                canvas.alpha_composite(self._render_corners_layer(self.accent + (int(round(220 * lock_glow)),), lw_mult=1.20))
                canvas.alpha_composite(with_alpha(self.af_lit, lock_glow))
            self._draw_reticle(canvas, box_scale=box_scale, lock_glow=lock_glow, flash_white=flash_white_hud)
            self._draw_scale(ImageDraw.Draw(canvas), scale_pos)
        return canvas


# ---------------------------------------------------------------- 风格 14：叠影（美术馆典藏精修版）


def _directional_bevel_border(size, radius, border_w, top_alpha=165, bot_alpha=38):
    """制作上左迎光、下右隐暗的美术馆典藏卡定向斜角高光棱边。"""
    W, H = size
    ring = border_layer(size, radius, (255, 255, 255, 255), border_w)
    ra = np.asarray(ring.getchannel("A"), dtype=np.float32) / 255.0
    xs = np.linspace(0.0, 1.0, W, dtype=np.float32)[None, :]
    ys = np.linspace(0.0, 1.0, H, dtype=np.float32)[:, None]
    # 从左上展厅射灯沿对角线柔滑过渡到右下背光
    diag = np.clip(1.0 - (xs * 0.52 + ys * 0.48), 0.0, 1.0)
    light = (bot_alpha + (top_alpha - bot_alpha) * (diag ** 1.35)) / 255.0
    out = np.zeros((H, W, 4), dtype=np.uint8)
    out[..., 0] = 252
    out[..., 1] = 250
    out[..., 2] = 247
    out[..., 3] = np.clip(ra * light * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(out, mode="RGBA")


class LayerCard(PosterCover):
    """
    美术馆 3D 立体典藏牌堆（高定精修版）：
    - 展墙级双色调柔雾肌理 + 右上斜射美术馆聚光光柱 + 细腻纸墙肌理；
    - 左侧为典藏序目编号（01 / 03）、金属渐层细饰线、双层深影丝绸渐变粗宋体与精工胶囊进度轨；
    - 右侧三张完整竖版海报呈优雅舒展的阶梯扇形悬浮：
      * 定向左上迎光精装棱边 + 1px 外裁黑线，宛如亚克力装裱典藏卡片；
      * 后排卡片融入环境色调与微弱光学景深虚化，前卡投下「近景贴合深影 + 远景墙面柔影」双层立体阴影；
      * 扶正落稳时一道柔和玻璃光泽轻轻掠过前卡表面！
    - 转场：前卡向右上方弧线飞旋并带细腻运动拖影钻回牌底，后方卡片以轻盈弹性扶正迎光接管 C 位！
    """

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        x_axis, y_axis = axes(self.size)
        field = wall_tooth(self.size, seed=14, coarse=0.009, fine=0.005)

        self.ph = H * 0.875
        self.pw = min(self.ph * 2.0 / 3.0, W * 0.342)
        self.ph = self.pw * 1.5
        self.card_cx = W * 0.616
        self.card_cy = H * 0.502

        # 舒展优雅的三层典藏扇形：第 2、3 张露出更充盈和谐的画面边幅，同时彻底填满右侧视觉平衡
        self.poses = [
            (0.0, 0.0, -2.6, 1.00, 1.00),
            (0.086, 0.018, 4.4, 0.915, 0.84),
            (0.166, 0.040, 11.0, 0.835, 0.66),
        ]
        self.posters_hi = []
        self.posters_soft = []
        self.card_masks_hi = []
        self.grounds, self.labels, self.rich_palettes = [], [], []

        # 美术馆斜射聚光 + 牌堆背后极柔和的竖向建筑光柱
        key_spot = np.exp(-(((x_axis - 0.67) / 0.36) ** 2 + ((y_axis - 0.42) / 0.50) ** 2))
        pillar_glow = np.exp(-(((x_axis - 0.62) / 0.22) ** 2)) * np.exp(-(((y_axis - 0.50) / 0.68) ** 2))
        diag_beam = np.exp(-((((x_axis - 0.72) + (y_axis - 0.30) * 0.55) / 0.26) ** 2)) * smooth((0.92 - y_axis) / 0.55)

        text_left = W * 0.058
        text_w = W * 0.340
        self.text_left = text_left

        hi_w, hi_h = max(2, int(round(self.pw * 2))), max(2, int(round(self.ph * 2)))
        rad = max(4, int(round(18 * s * 2)))
        base_mask_hi = rounded_mask((hi_w, hi_h), rad, supersample=2)
        bevel_hi = _directional_bevel_border((hi_w, hi_h), rad, max(2, 3.6 * s), top_alpha=172, bot_alpha=36)
        dark_rim_hi = border_layer((hi_w, hi_h), rad, (8, 9, 12, 125), max(1, 1.6 * s))
        self.card_mask_hi = base_mask_hi

        idx_font = load_font(self.fonts[1], max(9, int(round(30 * s))))
        tot_font = load_font(self.fonts[1], max(8, int(round(24 * s))))

        for index in range(self.count):
            p = self.palette(index)
            rich = palette_at(p.hue, max(p.vivid, 0.78))
            self.rich_palettes.append(rich)
            settled = settled_hue(rich.hue) != rich.hue
            rust = settled and settled_hue(rich.hue) == 46.0
            hi_shift = 0 if self.accent else (-18 if rust else 0 if settled else 26)
            low_shift = 0 if self.accent or settled else -16

            # 展墙双色调柔雾肌理 + 建筑展厅斜射主光 + 左侧丝绒压暗保护片名
            gray = ImageOps.autocontrast(ImageOps.grayscale(self.fit(index, self.size, clean=True).convert("RGB")), cutoff=1)
            blur_g = np.asarray(gray.filter(ImageFilter.GaussianBlur(max(2.0, 28 * s))), dtype=np.float32) / 255.0
            c_low = np.array(rich.tone(0.105, 0.055, shift=low_shift), float)
            c_hi = np.array(rich.tone(0.36, 0.145, shift=hi_shift), float)
            bg = c_low + (c_hi - c_low) * (blur_g * 0.62)[..., None]
            left_calm = 0.56 + 0.44 * smooth((x_axis - 0.10) / 0.54)
            vert_vig = 1.0 - 0.18 * (np.abs(y_axis - 0.48) / 0.52) ** 1.6
            bg *= (left_calm * vert_vig)[..., None]

            ground = Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8)).convert("RGBA")
            ground.alpha_composite(rgba_from_alpha(rich.tone(0.58, 0.16, shift=hi_shift), key_spot * 0.38))
            ground.alpha_composite(rgba_from_alpha(rich.tone(0.72, 0.12, shift=hi_shift), pillar_glow * 0.16))
            ground.alpha_composite(rgba_from_alpha(rich.tone(0.86, 0.08, shift=hi_shift), diag_beam * 0.085))
            ground.alpha_composite(grain_over(ground, field, (255, 255, 255), 0.022))
            self.grounds.append(ground.convert("RGB"))

            # 2× 超采样精装海报卡片（外黑裁边 + 定向左上迎光亚克力棱边 + 轻微对比润色）
            poster_rgb = ImageEnhance.Contrast(
                self.poster_image(index, (hi_w, hi_h), rich.tone(0.16, 0.04))
            ).enhance(1.04)
            card = poster_rgb.convert("RGBA")
            card.alpha_composite(bevel_hi)
            card.alpha_composite(dark_rim_hi)
            card.putalpha(base_mask_hi)
            self.posters_hi.append(card)

            # 后排景深柔焦缓存（极克制光学柔化 + 一丝展墙同色调环境漫反射，告别死黑发脏！）
            rear_tint = Image.new("RGB", (hi_w, hi_h), rich.tone(0.15, 0.06, shift=low_shift))
            soft_rgb = Image.blend(poster_rgb, rear_tint, 0.22).filter(ImageFilter.GaussianBlur(max(0.6, 1.3 * s)))
            soft_card = soft_rgb.convert("RGBA")
            soft_card.alpha_composite(with_alpha(bevel_hi, 0.55))
            soft_card.alpha_composite(dark_rim_hi)
            soft_card.putalpha(base_mask_hi)
            self.posters_soft.append(soft_card)

            # 左侧美术馆高定排版：序目编号 -> 精工渐变细饰线 -> 丝绸双色粗宋体 -> 副标
            lbl = Image.new("RGBA", self.size, (0, 0, 0, 0))
            main = self.bounded_layout(text_w, 460 * s)
            sub = self.bounded_layout(text_w, 136 * s, part="sub") if self.has_sub() else None
            gap = 28 * s if sub else 0
            tot_h = main.height + gap + (sub.height if sub else 0)
            top = (H - tot_h) * 0.49 + self.spec.offset_y * s
            self.dots_y = top + tot_h + 48 * s

            # 顶部精工渐变细线 + 上方典藏编号 01 / 03
            bar_w, bar_h = max(16, int(round(108 * s))), max(2, int(round(4.5 * s)))
            bar_y = top - 34 * s
            bar = gradient_fill(
                Image.new("L", (bar_w, bar_h), 255),
                rich.tone(0.96, 0.08),
                rich.tone(0.56, 0.16, shift=hi_shift),
            )
            drop_shadow(lbl, bar, text_left, bar_y, 10 * s, 0.45, 0, 2 * s)
            composite_at(lbl, bar, text_left, bar_y)

            ld = ImageDraw.Draw(lbl)
            cur_num = f"{index + 1:02d}"
            idx_y = bar_y - 16 * s
            ld.text((text_left + 1, idx_y + 1), cur_num, font=idx_font, fill=(0, 0, 0, 140), anchor="ls")
            ld.text((text_left, idx_y), cur_num, font=idx_font, fill=rich.tone(0.96, 0.04) + (242,), anchor="ls")
            nw = idx_font.getlength(cur_num)
            tot_txt = f"  /  {self.count:02d}"
            ld.text((text_left + nw, idx_y), tot_txt, font=tot_font, fill=rich.tone(0.76, 0.09, shift=hi_shift) + (175,), anchor="ls")

            # 主标题：丝绸双色高定渐变 + 双层立体投影（近景锐影 + 远景柔影）
            t_mask = main.render(self.size, text_left, top).getchannel("A")
            colored = gradient_fill(t_mask, rich.tone(0.985, 0.015), rich.tone(0.84, 0.15, shift=hi_shift))
            drop_shadow(lbl, colored, 0, 0, 6 * s, 0.55, 2 * s, 3 * s)
            drop_shadow(lbl, colored, 0, 0, 26 * s, 0.52, 4 * s, 8 * s)
            lbl.alpha_composite(colored)

            if sub:
                lbl.alpha_composite(sub.render(
                    self.size, text_left, top + main.height + gap,
                    sub_fill=rich.tone(0.87, 0.11, shift=hi_shift) + (248,),
                    shadow=((0, 0, 0, 155), 14 * s, 3 * s),
                ))
            self.labels.append(lbl)

    def _draw_dots(self, canvas, current, following, t):
        """底部典藏胶囊进度轨：当前项带柔光晕的拉长胶囊，其余为精工凹点。"""
        s = self.s
        draw = ImageDraw.Draw(canvas)
        r = max(2, round(4.2 * s))
        dx = self.text_left
        dy = self.dots_y
        p_cur = self.rich_palettes[current]
        p_nxt = self.rich_palettes[following]
        active_col = p_nxt.tone(0.88, 0.15) if t >= 0.5 else p_cur.tone(0.88, 0.15)
        for k in range(self.count):
            lit = (1.0 - t if k == current else 0.0) + (t if k == following else 0.0)
            rw = r * (2.0 + 4.4 * lit)
            if lit > 0.25:
                # 极轻高定微光外晕
                pad = max(1, round(2.5 * s))
                draw.rounded_rectangle(
                    (dx - pad, dy - pad, dx + rw + pad, dy + r * 2.0 + pad),
                    radius=r + pad,
                    fill=active_col + (int(round(48 * lit)),),
                )
            alpha = int(round(68 + 184 * lit))
            draw.rounded_rectangle((dx, dy, dx + rw, dy + r * 2.0), radius=r, fill=active_col + (alpha,))
            dx += rw + 10 * s

    def _draw_card(self, canvas, index, cx, cy, angle, scale, brightness, alpha=1.0, motion_blur=0.0,
                   rear_soft=0.0, sheen_t=None):
        idx = index % self.count
        if rear_soft > 0.35:
            card = self.posters_soft[idx]
        else:
            card = self.posters_hi[idx]
        if brightness < 0.999:
            card = ImageEnhance.Brightness(card).enhance(max(0.28, brightness))
        if sheen_t is not None:
            sheen = glass_sheen(card.size, self.card_mask_hi, sheen_t, period=1.0, offset=0.0, strength=0.14)
            if sheen is not None:
                card = card.copy()
                card.alpha_composite(sheen)
        turned = card.rotate(-angle, resample=Image.Resampling.BILINEAR, expand=True)
        out_w = max(2, int(round(turned.width * scale * 0.5)))
        out_h = max(2, int(round(turned.height * scale * 0.5)))
        final = turned.resize((out_w, out_h), Image.Resampling.LANCZOS)
        if motion_blur >= 2.0:
            final = streak(final, motion_blur, -motion_blur * 0.45, taps=5)
        if alpha < 0.999:
            final = with_alpha(final, alpha)
        x = cx - final.width / 2.0
        y = cy - final.height / 2.0
        # 双层物理实体阴影：近景贴合接触深影（精准压在下一张卡片上！）+ 远景展墙环境柔影
        drop_shadow(canvas, final, x, y, blur=10 * self.s * scale, alpha=0.58 * alpha, dx=-6 * self.s * scale, dy=9 * self.s * scale)
        drop_shadow(canvas, final, x, y, blur=34 * self.s * scale, alpha=0.46 * alpha, dx=4 * self.s * scale, dy=20 * self.s * scale)
        composite_at(canvas, final, x, y)

    def frame(self, phase):
        W, H, s = self.W, self.H, self.s
        current, progress = self.beat(phase, seconds=1.55)
        following = (current + 1) % self.count
        t = ease_in_out(progress)

        ground = self.grounds[current] if t <= 0 else Image.blend(self.grounds[current], self.grounds[following], t)
        canvas = ground.convert("RGBA")

        def pose_at(slot):
            dx, dy, ang, sc, br = self.poses[slot]
            return self.card_cx + dx * W, self.card_cy + dy * H, ang, sc, br

        if t <= 0 or self.count <= 1:
            for slot in (2, 1, 0):
                cx, cy, ang, sc, br = pose_at(slot)
                self._draw_card(
                    canvas, current + slot, cx, cy, ang, sc, br,
                    rear_soft=(1.0 if slot == 2 else 0.0),
                    sheen_t=(0.14 if slot == 0 else None),
                )
            canvas.alpha_composite(self.labels[current])
            self._draw_dots(canvas, current, following, 0.0)
            return canvas

        p0, p1, p2 = pose_at(0), pose_at(1), pose_at(2)
        arc = math.sin(t * math.pi)
        fly_cx = _lerp(p0[0], p2[0], t) + arc * W * 0.165
        fly_cy = _lerp(p0[1], p2[1], t) - arc * H * 0.140
        fly_ang = _lerp(p0[2], p2[2], t) + arc * 15.5
        fly_sc = _lerp(p0[3], p2[3], t) + arc * 0.032
        fly_br = _lerp(p0[4], p2[4], t) + 0.08 * arc
        blur_px = arc * 11.5 * s

        # 后方新主角带一丝物理弹性扶正落稳，并在迎光接管 C 位时闪过一缕轻柔镜面高光！
        settle = ease_back(progress, overshoot=0.45)
        c1 = tuple(_lerp(a, b, settle) for a, b in zip(p1, p0))
        c2 = tuple(_lerp(a, b, t) for a, b in zip(p2, p1))
        hero_sheen = (0.04 + 0.24 * progress) if progress > 0.35 else None

        if t < 0.5:
            if self.count > 3:
                self._draw_card(canvas, current + 3, p2[0], p2[1], p2[2], p2[3], p2[4], alpha=t * 2.0, rear_soft=1.0)
            self._draw_card(canvas, current + 2, *c2, rear_soft=1.0 - t)
            self._draw_card(canvas, current + 1, *c1, sheen_t=hero_sheen)
            self._draw_card(canvas, current, fly_cx, fly_cy, fly_ang, fly_sc, fly_br, motion_blur=blur_px)
        else:
            if self.count == 3:
                self._draw_card(canvas, current, fly_cx, fly_cy, fly_ang, fly_sc, fly_br,
                                motion_blur=blur_px * 0.55, rear_soft=(t - 0.5) * 2.0)
            else:
                self._draw_card(canvas, current, fly_cx, fly_cy, fly_ang, fly_sc, fly_br,
                                alpha=max(0.0, 1.0 - (t - 0.5) * 2.0), motion_blur=blur_px * 0.55, rear_soft=1.0)
                self._draw_card(canvas, current + 3, p2[0], p2[1], p2[2], p2[3], p2[4],
                                alpha=min(1.0, (t - 0.5) * 2.0 + 0.3), rear_soft=1.0)
            self._draw_card(canvas, current + 2, *c2, rear_soft=max(0.0, 0.8 - t))
            self._draw_card(canvas, current + 1, *c1, sheen_t=hero_sheen)

        canvas.alpha_composite(with_alpha(self.labels[current], 1.0 - t))
        canvas.alpha_composite(with_alpha(self.labels[following], t))
        self._draw_dots(canvas, current, following, t)
        return canvas


# ---------------------------------------------------------------- 风格 15：胶卷（原版完好保留）


class Filmstrip(Cover):
    """
    35mm 电影胶片暗房（精修版）：
    高饱和虚化暗房流光底面上，一条微倾 -7.5° 的半透明醋酸纤维 35mm 电影胶片带居中偏上斜贯全屏，
    上下双排镂空圆角齿孔带 1px 透光内壁高光边；下方深邃影院暗幕稳稳托住大号片名与薄荷竖标，绝不压主角人脸。
    转场：放映机抓片式斜向疾速过片（含定向运动拖影、柔和暗房闪光掠影与抓片微回弹）。
    """

    ANGLE = -7.5

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        self.grounds = [
            ImageEnhance.Color(self.backdrop(i, 0.48)).enhance(1.45).convert("RGB")
            for i in range(self.count)
        ]

        self.strip_h = int(round(H * 0.66))
        self.frame_h = int(round(self.strip_h * 0.72))
        self.frame_w = int(round(self.frame_h * 1.62))
        self.gap = max(6, int(round(26 * s)))
        self.pitch = self.frame_w + self.gap
        self.pad_y = (self.strip_h - self.frame_h) // 2

        self.frames = []
        f_rad = max(2, int(round(12 * s)))
        f_mask = rounded_mask((self.frame_w, self.frame_h), f_rad, supersample=2)
        f_border = border_layer((self.frame_w, self.frame_h), f_rad, (255, 255, 255, 58), max(1, round(1.6 * s)))
        for i in range(self.count):
            im = ImageEnhance.Contrast(self.fit(i, (self.frame_w, self.frame_h), clean=True)).enhance(1.07).convert("RGBA")
            im.alpha_composite(f_border)
            im.putalpha(f_mask)
            self.frames.append(im)

        self.belt_w = int(round(W * 1.56))
        self.belt_h = self.strip_h

        x_axis, y_axis = axes(self.size)
        bot = smooth((y_axis - 0.54) / 0.46) * 0.94
        left = smooth((0.50 - x_axis) / 0.50) * 0.48 * smooth((y_axis - 0.42) / 0.58)
        alpha = np.clip((1.0 - (1.0 - bot) * (1.0 - left)) * self.scrim_strength(), 0, 1)
        self.overlay = rgba_from_alpha((7, 8, 12), alpha)

        block, margin, ink_h = self.title_block(
            W * 0.64, sub_style="text", main_fill=GHOST_WHITE, sub_fill=SILVER,
            shadow=self.text_shadow(175),
        )
        tx = W * 0.048
        ty = H * 0.93 - ink_h + self.spec.offset_y * s
        bar_w = max(3, int(round(8 * s)))
        ImageDraw.Draw(self.overlay).rounded_rectangle(
            (tx, ty + 6 * s, tx + bar_w, ty + ink_h - 4 * s), radius=bar_w / 2, fill=self.accent + (250,)
        )
        composite_at(self.overlay, block, tx + bar_w + 22 * s - margin, ty - margin)

        num_font = load_font(self.fonts[1], max(10, int(round(36 * s))))
        self.num_layers = []
        for idx in range(self.count):
            nl = Image.new("RGBA", self.size, (0, 0, 0, 0))
            ImageDraw.Draw(nl).text((W - W * 0.048, H * 0.90), f"{idx + 1:02d} / {self.count:02d}",
                                    font=num_font, fill=SILVER + (195,), anchor="rs")
            self.num_layers.append(nl)

    def _render_belt(self, current, slide_px):
        s = self.s
        bw, bh = self.belt_w, self.belt_h
        belt = Image.new("RGBA", (bw, bh), (15, 16, 21, 232))
        draw = ImageDraw.Draw(belt)
        lw = max(1, round(1.8 * s))
        draw.line([(0, 1), (bw, 1)], fill=(242, 236, 224, 72), width=lw)
        draw.line([(0, bh - 2), (bw, bh - 2)], fill=(242, 236, 224, 58), width=lw)

        center_x = bw / 2.0 - self.frame_w / 2.0 - slide_px
        for slot in range(-2, 4):
            fx = int(round(center_x + slot * self.pitch))
            if fx + self.frame_w < 0 or fx > bw:
                continue
            composite_at(belt, self.frames[(current + slot) % self.count], fx, self.pad_y)
            gx = fx - self.gap // 2
            if 0 < gx < bw:
                draw.line([(gx, self.pad_y + 10 * s), (gx, bh - self.pad_y - 10 * s)],
                          fill=(255, 255, 255, 26), width=max(1, round(1.2 * s)))

        hole_w = max(4, int(round(34 * s)))
        hole_h = max(3, int(round(22 * s)))
        hole_step = max(10, int(round(64 * s)))
        hole_r = max(1, int(round(5 * s)))
        y_top = max(3, (self.pad_y - hole_h) // 2)
        y_bot = bh - self.pad_y + (self.pad_y - hole_h) // 2
        start = -int(round(slide_px)) % hole_step - hole_step

        for hx in range(start, bw + hole_step, hole_step):
            draw.rounded_rectangle((hx - 1, y_top - 1, hx + hole_w + 1, y_top + hole_h + 1),
                                   radius=hole_r + 1, outline=(255, 255, 255, 58), width=1)
            draw.rounded_rectangle((hx - 1, y_bot - 1, hx + hole_w + 1, y_bot + hole_h + 1),
                                   radius=hole_r + 1, outline=(255, 255, 255, 58), width=1)
        mask = belt.getchannel("A")
        md = ImageDraw.Draw(mask)
        for hx in range(start, bw + hole_step, hole_step):
            md.rounded_rectangle((hx, y_top, hx + hole_w, y_top + hole_h), radius=hole_r, fill=0)
            md.rounded_rectangle((hx, y_bot, hx + hole_w, y_bot + hole_h), radius=hole_r, fill=0)
        belt.putalpha(mask)
        return belt

    def frame(self, phase):
        W, H, s = self.W, self.H, self.s
        current, following, progress = beats(phase, self.count, hold=0.40, curve=lambda v: v)

        ground = self.grounds[current] if progress <= 0 else Image.blend(self.grounds[current], self.grounds[following], ease_in_out(progress))
        canvas = ground.convert("RGBA")

        if progress <= 0.0 or self.count <= 1:
            belt = self._render_belt(current, 0.0)
        else:
            move = ease_in_out(progress) + math.sin(progress * math.pi) * 0.015 * (1.0 - progress)
            slide_px = move * self.pitch
            vel = abs(ease_in_out(min(1.0, progress + 0.03)) - ease_in_out(progress)) * self.pitch
            belt = self._render_belt(current, slide_px)
            if vel * 0.22 >= 2.0:
                belt = streak(belt, min(vel * 0.22, 14 * s), 0, taps=4)

        rotated = belt.rotate(-self.ANGLE, resample=Image.Resampling.BILINEAR, expand=True)
        rx = (W - rotated.width) / 2.0
        ry = (H - rotated.height) / 2.0 - H * 0.085
        drop_shadow(canvas, rotated, rx, ry, blur=28 * s, alpha=0.54, dx=0, dy=18 * s)
        composite_at(canvas, rotated, rx, ry)

        if progress > 0.0 and self.count > 1:
            leak = sweep(self.size, progress * 0.7, 0.2, strength=0.14, period=1.0, slant=0.22)
            if leak is not None:
                canvas.alpha_composite(leak)

        canvas.alpha_composite(self.overlay)
        canvas.alpha_composite(self.num_layers[following if progress >= 0.5 else current])
        return canvas


# ---------------------------------------------------------------- 风格 16：窥视（血脉 · V10 指甲划刻渗血字定稿版）


def _render_veins_scratched_title(cover, seed=666):
    """
    V10「窥视 · 血脉」原版程序化指甲划刻渗血标题引擎（方案 3 惨红骨绯 × 漆黑隔离渊副标增强版）：
    - 主标题：枯骨象牙白粗宋体主体 + 高频手刻微颤、3 道斜向暗血抓切痕与 9 道纤细指甲下拉拖痕；
    - 副标题：高明度惨红骨绯 + 轻度防碎蚀刻 + 背后无痕漆黑丝绒隔离渊与 1px 紧实黑影，
      保留白×红惊悚悬疑撞色同时彻底解决与暗红背景融合的问题，400×225 / 320px 缩略图下字字锐利。
    """
    W, H, s = cover.W, cover.H, cover.s
    rng = np.random.default_rng(seed)
    has_sub = cover.has_sub()

    block, _margin, _ = cover.title_block(
        W * 0.56, sub_style="text",
        main_fill=(255, 255, 255), sub_fill=(255, 255, 255),
        shadow=None, align="center",
    )
    bb = block.getbbox()
    if not bb:
        return Image.new("RGBA", cover.size, (0, 0, 0, 0))
    crop_a = block.crop(bb).getchannel("A")
    cw, ch = crop_a.size

    # 若主副标题总高过高（例如多行长片库名），按底部黑幕安全高度等比温和收紧，确保不压眼睑也不切底！
    max_ch = max(16, int(round(H * 0.258)))
    if ch > max_ch:
        ratio = max_ch / float(ch)
        cw = max(8, int(round(cw * ratio)))
        ch = max(8, int(round(ch * ratio)))
        crop_a = crop_a.resize((cw, ch), Image.Resampling.LANCZOS)

    pad_x = max(12, int(round(48 * s)))
    pad_top = max(6, int(round(22 * s)))
    pad_bot = max(10, int(round(46 * s)))
    tw, th = cw + pad_x * 2, ch + pad_top + pad_bot

    mask_im = Image.new("L", (tw, th), 0)
    mask_im.paste(crop_a, (pad_x, pad_top))
    base_m = np.asarray(mask_im, dtype=np.float32) / 255.0
    yy, xx = np.mgrid[0:th, 0:tw].astype(np.float32)

    # 自动识别主/副标题之间的空白分隔行，精准定位副标区域（任意中英文长度均适配）
    sub_y0, sub_y1, sub_x0, sub_x1 = th, th, pad_x, pad_x + cw
    if has_sub:
        row_ink = base_m.max(axis=1) > 0.08
        search_lo = pad_top + int(ch * 0.45)
        search_hi = pad_top + int(ch * 0.92)
        gap_rows = [r for r in range(search_lo, min(th, search_hi)) if not row_ink[r]]
        split_y = float(gap_rows[len(gap_rows) // 2]) if gap_rows else float(pad_top + ch * 0.75)
        sub_rows = np.where(row_ink[int(split_y):])[0]
        if len(sub_rows) > 0:
            sub_y0 = int(split_y) + int(sub_rows[0])
            sub_y1 = int(split_y) + int(sub_rows[-1]) + 1
            sub_cols = np.where(base_m[sub_y0:sub_y1, :].max(axis=0) > 0.08)[0]
            if len(sub_cols) > 0:
                sub_x0, sub_x1 = int(sub_cols[0]), int(sub_cols[-1]) + 1
    else:
        split_y = float(th + 10)

    is_sub = smooth((yy - split_y) / max(1.0, 2.5 * s)) if has_sub else np.zeros((th, tw), dtype=np.float32)

    # 1. 有机手刻微颤位移 + 3 道斜向尖锐指甲抓切痕（主标题全强度粗粝；副标题收敛至 24% 抖动防断细笔画）
    amp_x = max(0.8, 2.3 * s)
    amp_y = max(0.6, 1.8 * s)
    raw_disp_x = (
        np.sin(yy * (0.32 / max(0.25, s)) + 0.7) * amp_x * 0.55
        + np.cos(yy * (0.78 / max(0.25, s)) - xx * (0.12 / max(0.25, s))) * amp_x * 0.45
    )
    raw_disp_y = np.sin(xx * (0.45 / max(0.25, s)) + 1.4) * amp_y * 0.55
    warp_scale = (1.0 - is_sub) + 0.24 * is_sub
    disp_x = raw_disp_x * warp_scale
    disp_y = raw_disp_y * warp_scale

    slash_blood = np.zeros((th, tw), dtype=np.float32)
    carve_cut = np.zeros((th, tw), dtype=np.float32)
    slashes = [
        (0.26, 0.34, -0.34, 1.0),
        (0.56, 0.40, -0.29, 0.9),
        (0.78, 0.32, -0.36, 0.85),
    ]
    main_cx0, main_cw = float(pad_x), float(cw)
    for fx, fy, slope, weight in slashes:
        sx_c = main_cx0 + main_cw * fx
        sy_c = pad_top + ch * fy
        lv = (yy - sy_c) - (xx - sx_c) * slope
        in_seg = np.exp(-(((xx - sx_c) / (main_cw * 0.24)) ** 2)) * (1.0 - is_sub)
        sl_w = max(0.8, 1.75 * s)
        cut_k = np.exp(-((lv / sl_w) ** 2)) * in_seg * weight
        carve_cut = np.maximum(carve_cut, cut_k * 0.78)
        slash_blood = np.maximum(slash_blood, np.exp(-((lv / (sl_w * 2.2)) ** 2)) * in_seg * weight)
        disp_x += np.tanh(lv / max(1.2, 3.0 * s)) * max(0.6, 1.6 * s) * in_seg * 0.45

    warped = _warp_bilinear(base_m, xx + disp_x, yy + disp_y)

    # 2. 指甲划刻干笔/纵向刮擦粗糙肌理（主标强蚀刻；副标轻蚀刻并在小尺寸下轻微加实防断字）
    fine_n = rng.random((th, tw)).astype(np.float32)
    scratch_tex = np.asarray(
        Image.fromarray((fine_n * 255).astype(np.uint8), mode="L").filter(ImageFilter.GaussianBlur(max(0.6, 0.9 * s))),
        dtype=np.float32,
    ) / 255.0
    col_streaks = rng.uniform(0.0, 1.0, (1, tw)).astype(np.float32)
    col_streaks = np.asarray(
        Image.fromarray(np.broadcast_to((col_streaks * 255).astype(np.uint8), (4, tw)), mode="L").filter(ImageFilter.GaussianBlur(max(0.5, 0.8 * s))),
        dtype=np.float32,
    )[1:2, :] / 255.0
    erode = (0.86 + 0.14 * scratch_tex) * (0.84 + 0.16 * col_streaks)
    main_carved = warped * (1.0 - carve_cut * 0.68) * (0.78 + 0.22 * smooth((erode - 0.82) / 0.14))
    sub_carved = warped * (0.92 + 0.08 * smooth((erode - 0.76) / 0.18))
    if s < 0.6:
        sub_carved = np.clip(np.power(np.clip(sub_carved, 0.0, 1.0), 0.86), 0.0, 1.0)
    carved_a = np.clip(main_carved * (1.0 - is_sub) + sub_carved * is_sub, 0.0, 1.0)

    # 3. 主标题笔画底部垂落的 9 道纤细尖锐「指甲下拉拖痕」（严格限高不戳进副标）
    main_bot_limit = int(min(split_y - max(2, round(4 * s)), pad_top + ch * 0.70)) if has_sub else (pad_top + ch)
    tail_layer = Image.new("L", (tw, th), 0)
    td = ImageDraw.Draw(tail_layer)
    cols_with_ink = np.where(warped[pad_top:max(pad_top + 2, main_bot_limit), :].max(axis=0) > 0.65)[0]
    if len(cols_with_ink) > 12:
        picks = cols_with_ink[np.linspace(2, len(cols_with_ink) - 3, 9).astype(int)]
        for k, px_c in enumerate(picks):
            col_slice = warped[pad_top:max(pad_top + 2, main_bot_limit), px_c]
            ys_ink = np.where(col_slice > 0.55)[0]
            if len(ys_ink) == 0:
                continue
            y_bot = pad_top + int(ys_ink[-1])
            tail_len = rng.uniform(10.0, 24.0) * s * (1.15 if k % 2 == 0 else 0.72)
            if has_sub:
                tail_len = min(tail_len, max(4.0 * s, (sub_y0 - y_bot) - 3.0 * s))
            slant = rng.uniform(-3.5, 2.2) * s
            w_root = max(0.9, rng.uniform(1.4, 2.8) * s)
            td.polygon([
                (px_c - w_root, y_bot - 1),
                (px_c + w_root, y_bot - 1),
                (px_c + slant, y_bot + tail_len),
            ], fill=int(round(rng.uniform(165, 230))))
    tail_a = np.asarray(tail_layer, dtype=np.float32) / 255.0
    final_a = np.clip(np.maximum(carved_a, tail_a * 0.85), 0.0, 1.0)

    # 4. 色彩上色：枯骨象牙白主标 + 方案 3 高明度惨红骨绯副标 + 主标划口/拖尾暗血渗染
    bone_v = 232.0 - 38.0 * (1.0 - scratch_tex) - 24.0 * (1.0 - col_streaks)
    main_rgb = np.stack([bone_v * 1.01, bone_v * 0.98, bone_v * 0.93], axis=-1)
    if has_sub:
        sub_rgb = np.stack(
            [242.0 - 18.0 * (1.0 - scratch_tex), 146.0 + 24.0 * scratch_tex, 134.0 + 22.0 * scratch_tex],
            axis=-1,
        )
        rgb = main_rgb * (1.0 - is_sub[..., None]) + sub_rgb * is_sub[..., None]
    else:
        rgb = main_rgb

    blood_tint = np.array([154.0, 24.0, 20.0], dtype=np.float32)
    blood_m = np.clip(slash_blood * 0.78 + tail_a * 0.55, 0.0, 0.85) * (1.0 - is_sub)
    rgb = rgb * (1.0 - blood_m[..., None]) + blood_tint * blood_m[..., None]

    fg_rgba = np.empty((th, tw, 4), dtype=np.uint8)
    fg_rgba[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    fg_rgba[..., 3] = np.clip(final_a * 255.0, 0, 255).astype(np.uint8)
    fg_im = Image.fromarray(fg_rgba, mode="RGBA")

    # 5. 无痕漆黑丝绒护字隔离渊（压暗副标背后穿过的红血丝/余烬）+ 紧实黑影 + 柔和底影
    sh_im = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    if has_sub and sub_y1 > sub_y0:
        sub_cy = 0.5 * (sub_y0 + sub_y1)
        sub_cx = 0.5 * (sub_x0 + sub_x1)
        sub_w = max(20.0, float(sub_x1 - sub_x0))
        sub_h = max(8.0, float(sub_y1 - sub_y0))
        rx_void = sub_w * 0.72 + 26.0 * s
        ry_void = sub_h * 1.15 + 14.0 * s
        void_k = np.exp(-((((xx - sub_cx) / rx_void) ** 2) + (((yy - sub_cy) / ry_void) ** 2)) * 1.45)
        void_rgba = np.zeros((th, tw, 4), dtype=np.uint8)
        void_rgba[..., 0] = 5
        void_rgba[..., 1] = 2
        void_rgba[..., 2] = 3
        void_rgba[..., 3] = np.clip(void_k * 255.0 * 0.88, 0, 255).astype(np.uint8)
        sh_im.alpha_composite(Image.fromarray(void_rgba, mode="RGBA"))

    sh_mask = Image.fromarray(np.clip(final_a * 225.0, 0, 255).astype(np.uint8), mode="L")
    sh_dark = Image.new("RGBA", (tw, th), (8, 2, 2, 255))
    sh_dark.putalpha(sh_mask)
    sh_dark = sh_dark.filter(ImageFilter.GaussianBlur(max(2.0, 5.2 * s)))
    sh_im.alpha_composite(sh_dark, (0, max(1, round(2.2 * s))))

    if has_sub:
        sub_tight = Image.new("RGBA", (tw, th), (4, 1, 1, 255))
        sub_tight.putalpha(
            Image.fromarray(np.clip(final_a * is_sub * 235.0, 0, 255).astype(np.uint8), mode="L")
        )
        sub_tight = sub_tight.filter(ImageFilter.GaussianBlur(max(0.8, 1.8 * s)))
        sh_im.alpha_composite(sub_tight, (0, max(1, round(1.0 * s))))

    sh_im.alpha_composite(fg_im)

    # 精准落在下眼睑下方黄金黑幕中央（下眼睑底沿 H*0.675，底边留安全边距绝不裁切！）
    target_y = H * 0.835 - (ch * 0.5 + pad_top) + cover.spec.offset_y * s * 0.35
    min_y = H * 0.692 - pad_top
    max_y = H - max(4, int(round(H * 0.025))) - (pad_top + ch)
    paste_y = clamp(target_y, min_y, max(min_y, max_y))

    out_layer = Image.new("RGBA", cover.size, (0, 0, 0, 0))
    composite_at(out_layer, sh_im, (W - tw) * 0.5, paste_y)
    return out_layer


class GazeVeins(Cover):
    """
    16 · 窥视（血脉 · 指甲划刻渗血字定稿版）：
    - 漆黑丝绒深渊四周密布有机分叉暗红毛细血脉（含顶部黄金加密血网与闭眼上眼皮细腻微血管）；
    - 中央巨大杏仁眼窗完整透出全彩片库剧照（配备专属防切头杏仁眼精准平移取景）；
    - 底部为 V10 原版枯骨象牙白指甲划刻渗血大字与暗红划刻副标；
    - 转场：巨眼缓缓闭合成脉动猩红血缝，血脉心跳波向外蔓延并切换下一部作品，随后骤然弹开睁眼！
    """

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        self.cx = W * 0.50
        self.cy = H * 0.385
        self.eye_rx = W * 0.415
        self.eye_ry_max = H * 0.290

        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        self.xx, self.yy = xx, yy
        self.nx = (xx - self.cx) / max(1.0, self.eye_rx)
        self.dy = yy - self.cy

        r_abyss = np.sqrt((self.nx * 0.82) ** 2 + (self.dy / (H * 0.54)) ** 2)
        red_ember = np.exp(-((r_abyss / 0.90) ** 2)) * 18.0
        abyss = np.stack([6.0 + red_ember * 1.45, 4.5 + red_ember * 0.24, 5.5 + red_ember * 0.26], axis=-1)
        rng = np.random.default_rng(1337)
        abyss += rng.normal(0.0, 2.2, (H, W, 1)).astype(np.float32)
        self.abyss_arr = np.clip(abyss, 0.0, 255.0)

        # 精准杏仁眼窗专用取景：把每张素材直接映射到杏仁眼外接框内，
        # 自动沿水平与竖直方向平移/微缩，让人物完整头部（含额头与发顶）和双眼稳稳落在眼窗宽阔中央，彻底消灭切头！
        ex0 = max(0, int(round(EYE_BOX_FRAC[0] * W)))
        ey0 = max(0, int(round(EYE_BOX_FRAC[1] * H)))
        ex1 = min(W, int(round(EYE_BOX_FRAC[2] * W)))
        ey1 = min(H, int(round(EYE_BOX_FRAC[3] * H)))
        eye_w, eye_h = max(2, ex1 - ex0), max(2, ey1 - ey0)
        eye_aspect = eye_w / float(eye_h)

        self.frames_arr = []
        for i in range(self.count):
            pic = self.picture(i)
            src = pic.pick(eye_aspect, clean=True)
            best_fit = find_best_eye_window(src.image, key=src.key, eye_aspect=eye_aspect)
            if len(pic.sources) > 1 and (not best_fit.has_face or best_fit.head_clipped or best_fit.head_vis < 0.85):
                for alt_src in pic.sources:
                    if alt_src is src:
                        continue
                    alt_fit = find_best_eye_window(alt_src.image, key=alt_src.key, eye_aspect=eye_aspect)
                    if alt_fit.has_face and (not alt_fit.head_clipped) and alt_fit.head_vis > best_fit.head_vis + 0.12:
                        src, best_fit = alt_src, alt_fit
                        break

            sw, sh = src.image.size
            bx0, by0, bx1, by1 = best_fit.box
            crop_box = (bx0 * sw, by0 * sh, bx1 * sw, by1 * sh)
            eye_crop = src.image.convert("RGB").resize((eye_w, eye_h), Image.Resampling.LANCZOS, box=crop_box)
            eye_crop = ImageEnhance.Contrast(eye_crop).enhance(1.10)
            full_frame = Image.new("RGB", (W, H), (6, 4, 5))
            full_frame.paste(eye_crop, (ex0, ey0))
            self.frames_arr.append(np.asarray(full_frame, dtype=np.float32))

        ss = 2
        W2, H2 = W * ss, H * ss
        vein_l = Image.new("L", (W2, H2), 0)
        vd = ImageDraw.Draw(vein_l)
        rng_v = np.random.default_rng(702)

        def grow_branch(draw_ctx, x, y, ang, length, width, depth, fork_prob=0.46, alpha_scale=0.96):
            if depth <= 0 or length < 7 * s * ss or width < 0.65:
                return
            segs = max(4, int(length / (16 * s * ss)))
            cx_b, cy_b = x, y
            cur_ang = ang
            for k in range(segs):
                u = k / float(segs)
                cur_ang += rng_v.uniform(-0.34, 0.34)
                step_l = length / float(segs)
                nx_b, ny_b = cx_b + math.cos(cur_ang) * step_l, cy_b + math.sin(cur_ang) * step_l
                w_now = max(1, int(round(width * (1.0 - 0.54 * u))))
                a_now = int(round(clamp(200 * alpha_scale * (1.0 - 0.32 * u) * (0.55 + 0.45 * depth / 3.0), 28, 240)))
                draw_ctx.line([(cx_b, cy_b), (nx_b, ny_b)], fill=a_now, width=w_now)
                if 0 < k < segs - 1 and rng_v.random() < fork_prob:
                    fork_sign = 1.0 if rng_v.random() < 0.5 else -1.0
                    grow_branch(draw_ctx, nx_b, ny_b, cur_ang + fork_sign * rng_v.uniform(0.32, 0.74),
                                length * rng_v.uniform(0.46, 0.74), width * 0.64, depth - 1,
                                fork_prob=fork_prob, alpha_scale=alpha_scale)
                cx_b, cy_b = nx_b, ny_b

        origins = [
            # 左眼角四簇
            (0.09, 0.385, math.pi + 0.16, 0.24, 5.2),
            (0.09, 0.385, math.pi - 0.40, 0.26, 5.2),
            (0.11, 0.345, math.pi + 0.74, 0.28, 5.0),
            (0.11, 0.435, math.pi - 0.82, 0.28, 5.0),
            # 右眼角四簇
            (0.91, 0.385, -0.16, 0.24, 5.2),
            (0.91, 0.385,  0.40, 0.26, 5.2),
            (0.89, 0.345, -0.74, 0.28, 5.0),
            (0.89, 0.435,  0.78, 0.28, 5.0),
            # 底部四簇（环抱下方字碑两侧不压字）
            (0.19, 0.59,  2.35, 0.27, 5.0),
            (0.26, 0.64,  2.12, 0.22, 4.6),
            (0.74, 0.64,  1.02, 0.22, 4.6),
            (0.81, 0.59,  0.75, 0.27, 5.0),
            # 顶部黄金加密血脉（饱满阴森、与底部疏密协调，绝不发空）
            (0.18, 0.21, -2.38, 0.24, 4.8),
            (0.27, 0.14, -2.10, 0.20, 4.4),
            (0.73, 0.14, -1.04, 0.20, 4.4),
            (0.82, 0.21, -0.76, 0.24, 4.8),
            (0.08, 0.00,  0.82, 0.22, 4.6),
            (0.22, 0.00,  1.15, 0.19, 4.2),
            (0.37, 0.00,  1.42, 0.16, 3.8),
            (0.50, 0.00,  1.58, 0.15, 3.8),
            (0.63, 0.00,  1.72, 0.16, 3.8),
            (0.78, 0.00,  1.98, 0.19, 4.2),
            (0.92, 0.00,  2.32, 0.22, 4.6),
        ]
        for ox, oy, oang, olen, w_px in origins:
            grow_branch(vd, ox * W2, oy * H2, oang, olen * W2, max(1.9, w_px * s * ss), depth=3, fork_prob=0.46, alpha_scale=0.96)

        lid_vein_l = Image.new("L", (W2, H2), 0)
        lvd = ImageDraw.Draw(lid_vein_l)
        lid_origins = [
            (0.24, 0.03, 1.25, 0.23, 3.0),
            (0.39, 0.03, 1.48, 0.22, 2.8),
            (0.61, 0.03, 1.66, 0.22, 2.8),
            (0.76, 0.03, 1.88, 0.23, 3.0),
            (0.15, 0.28, 0.22, 0.18, 2.8),
            (0.85, 0.28, 2.92, 0.18, 2.8),
        ]
        for ox, oy, oang, olen, w_px in lid_origins:
            grow_branch(lvd, ox * W2, oy * H2, oang, olen * W2, max(1.5, w_px * s * ss), depth=3, fork_prob=0.44, alpha_scale=0.68)

        vein_im = vein_l.resize((W, H), Image.Resampling.LANCZOS)
        vein_glow = vein_im.filter(ImageFilter.GaussianBlur(max(2.0, 5.2 * s)))
        self.vein_core = np.asarray(vein_im, dtype=np.float32) / 255.0
        self.vein_halo = np.asarray(vein_glow, dtype=np.float32) / 255.0

        lid_im = lid_vein_l.resize((W, H), Image.Resampling.LANCZOS)
        lid_glow = lid_im.filter(ImageFilter.GaussianBlur(max(1.8, 4.5 * s)))
        self.lid_vein_core = np.asarray(lid_im, dtype=np.float32) / 255.0
        self.lid_vein_halo = np.asarray(lid_glow, dtype=np.float32) / 255.0
        slit_clear = smooth((np.abs(self.dy) - 24.0 * s) / max(16.0, 56.0 * s))
        self.lid_vein_core *= slit_clear
        self.lid_vein_halo *= slit_clear

        self.rad_from_eye = np.sqrt(((self.xx - self.cx) / (W * 0.55)) ** 2 + ((self.yy - self.cy) / (H * 0.55)) ** 2)
        self.inside_x = np.abs(self.nx) < 1.0
        self.arch = np.power(np.maximum(0.0, 1.0 - self.nx * self.nx), 1.18)
        self.title_layer = _render_veins_scratched_title(self, seed=666)
        self._rest_cache = {}

    def _compose(self, pic_arr, main_open, pulse, progress):
        s = self.s
        bg = self.abyss_arr.copy()
        wave_r = _lerp(0.25, 1.05, clamp(progress * 1.4)) if progress > 0 else 0.0
        wave_boost = np.exp(-(((self.rad_from_eye - wave_r) / 0.22) ** 2)) * pulse * 0.72

        halo_c = np.array([118.0, 14.0, 12.0], dtype=np.float32)
        core_c = np.array([176.0, 24.0, 20.0], dtype=np.float32)
        bg = bg + halo_c * (self.vein_halo * (0.36 + 0.42 * pulse + wave_boost * 0.4))[..., None]
        bg = bg + core_c * (self.vein_core * (0.64 + 0.36 * pulse + wave_boost * 0.75))[..., None]

        lid_vis = clamp(1.0 - main_open * 0.78, 0.22, 1.0)
        bg = bg + halo_c * (self.lid_vein_halo * lid_vis * (0.24 + 0.32 * pulse))[..., None]
        bg = bg + core_c * (self.lid_vein_core * lid_vis * (0.44 + 0.30 * pulse + wave_boost * 0.4))[..., None]

        ry = self.eye_ry_max * max(0.001, main_open)
        bound = ry * self.arch
        signed_d = np.where(self.inside_x, bound - np.abs(self.dy), -100.0)
        eye_mask = smooth(signed_d / max(1.4, 2.8 * s))

        inner_rim = np.exp(-((np.maximum(0.0, signed_d) / max(10.0, 34.0 * s)) ** 1.35))
        crimson = np.array([115.0, 22.0, 18.0], dtype=np.float32)
        eye_pic = pic_arr * (1.0 - inner_rim[..., None] * 0.52) + crimson * (inner_rim[..., None] * 0.34)
        out = bg * (1.0 - eye_mask[..., None]) + eye_pic * eye_mask[..., None]

        lid_glow = np.exp(-((signed_d / max(1.3, 2.6 * s)) ** 2)) * np.where(self.inside_x, 1.0, 0.0)
        out = out + np.array([204.0, 52.0, 40.0], dtype=np.float32) * (lid_glow * (0.50 + 0.65 * pulse))[..., None]
        if pulse > 0.05:
            slit_halo = np.exp(-(((self.dy / max(4.0, 16.0 * s)) ** 2) + (self.nx / 0.65) ** 2)) * pulse
            out = out + np.array([156.0, 24.0, 18.0], dtype=np.float32) * (slit_halo * 0.58)[..., None]

        canvas = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), mode="RGB").convert("RGBA")
        canvas.alpha_composite(self.title_layer)
        return canvas

    def frame(self, phase):
        if self.count <= 1:
            return self._rest_cache.setdefault(0, self._compose(self.frames_arr[0], 1.0, 0.0, 0.0)).copy()
        current, following, progress = beats(phase, self.count, hold=0.56, curve=lambda v: v)
        if progress <= 0.0:
            cached = self._rest_cache.get(current)
            if cached is None:
                cached = self._compose(self.frames_arr[current], 1.0, 0.0, 0.0)
                self._rest_cache[current] = cached
            return cached.copy()
        if progress < 0.36:
            p = ease_in_out(progress / 0.36)
            return self._compose(self.frames_arr[current], _lerp(1.0, 0.008, p), p * 0.60, progress)
        if progress < 0.54:
            p = (progress - 0.36) / 0.18
            return self._compose(self.frames_arr[following], 0.008, 0.60 + 0.40 * math.sin(p * math.pi), progress)
        p = ease_out((progress - 0.54) / 0.46)
        op = clamp(_lerp(0.008, 1.0, p) + 0.044 * math.sin(p * math.pi) * (1.0 - p * 0.5), 0.008, 1.05)
        return self._compose(self.frames_arr[following], op, (1.0 - p) * 0.60, progress)


EXTRA_DESIGNS = {
    13: Aperture,
    14: LayerCard,
    15: Filmstrip,
    16: GazeVeins,
}
