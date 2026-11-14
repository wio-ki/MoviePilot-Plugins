"""
海报系列 9–12：百叶、对开、月洞、展墙。

每张海报单独取色，画面只保留海报和主副标题；静态图与动画首帧使用相同构图。
prepare() 缓存不变的图层，frame(phase) 只计算当前转场的运动。
"""
import colorsys
import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from app.plugins.mediacovergenerator.style.covers import (
    GHOST_WHITE, Cover, axes, composite_at, ease_in_out, ease_out, rgba_from_alpha, warp_onto, with_alpha,
)
from app.plugins.mediacovergenerator.style.typography import load_font, tracked_width


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


# ---------------------------------------------------------------- 取色


def _hsv(hue, saturation, value):
    return tuple(int(round(channel * 255)) for channel in colorsys.hsv_to_rgb(hue % 1.0, clamp(saturation), clamp(value)))


def _to_linear(value):
    value = value / 255.0
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _to_srgb(value):
    value = clamp(value)
    value = value * 12.92 if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055
    return int(round(value * 255))


def oklab(rgb):
    red, green, blue = (_to_linear(float(channel)) for channel in rgb[:3])
    long_ = 0.4122214708 * red + 0.5363325363 * green + 0.0514459929 * blue
    medium = 0.2119034982 * red + 0.6806995451 * green + 0.1073969566 * blue
    short = 0.0883024619 * red + 0.2817188376 * green + 0.6299787005 * blue
    long_, medium, short = (math.copysign(abs(value) ** (1.0 / 3.0), value) for value in (long_, medium, short))
    return (0.2104542553 * long_ + 0.7936177850 * medium - 0.0040720468 * short,
            1.9779984951 * long_ - 2.4285922050 * medium + 0.4505937099 * short,
            0.0259040371 * long_ + 0.7827717662 * medium - 0.8086757660 * short)


def oklch(lightness, chroma, hue):
    """OKLCH 转 sRGB。超出色域时只降低彩度，明度和色相不变，所以同一套明度做出来的颜色深浅一致。"""
    angle = math.radians(hue)
    for _ in range(30):
        a_axis, b_axis = chroma * math.cos(angle), chroma * math.sin(angle)
        long_ = (lightness + 0.3963377774 * a_axis + 0.2158037573 * b_axis) ** 3
        medium = (lightness - 0.1055613458 * a_axis - 0.0638541728 * b_axis) ** 3
        short = (lightness - 0.0894841775 * a_axis - 1.2914855480 * b_axis) ** 3
        rgb = (4.0767416621 * long_ - 3.3077115913 * medium + 0.2309699292 * short,
               -1.2684380046 * long_ + 2.6097574011 * medium - 0.3413193965 * short,
               -0.0041960863 * long_ - 0.7034186147 * medium + 1.7076147010 * short)
        if all(-0.001 <= value <= 1.001 for value in rgb):
            break
        chroma *= 0.9
    return tuple(_to_srgb(value) for value in rgb)


def signature(image):
    """
    海报的主色，返回 (色相, 饱和度)，色相是 HSV 的 0~1。按「鲜艳并且不太暗」给像素加权统计色相，取最集中的那一簇。
    肤色降权，否则人像海报全会取成肉色；近乎黑白的海报返回 None。
    """
    small = image.convert("RGB")
    small.thumbnail((72, 72))
    hsv = np.asarray(small.convert("HSV"), dtype=np.float32) / 255.0
    hue, sat, val = hsv[..., 0].ravel(), hsv[..., 1].ravel(), hsv[..., 2].ravel()
    weight = sat ** 1.4 * np.clip((val - 0.12) / 0.45, 0.0, 1.0)
    skin = (hue < 0.11) & (sat > 0.12) & (sat < 0.62) & (val > 0.3)
    weight = np.where(skin, weight * 0.3, weight)
    if weight.sum() < 0.035 * weight.size:
        return None
    bins = 36
    index = (hue * bins).astype(np.int64) % bins
    hist = np.bincount(index, weights=weight, minlength=bins)
    spread = hist + 0.6 * (np.roll(hist, 1) + np.roll(hist, -1)) + 0.25 * (np.roll(hist, 2) + np.roll(hist, -2))
    peak = int(np.argmax(spread))
    near = np.abs((index - peak + bins // 2) % bins - bins // 2) <= 1
    chosen = weight[near]
    angle = hue[near] * 2.0 * np.pi
    mean = math.atan2(float((chosen * np.sin(angle)).sum()), float((chosen * np.cos(angle)).sum())) / (2.0 * math.pi)
    return mean % 1.0, float((chosen * sat[near]).sum() / max(1e-6, chosen.sum()))


@dataclass(frozen=True)
class Palette:
    """一张海报衍生出来的几种颜色：同一个色相，按 OKLCH 明度分层。"""
    block: tuple   # 大面积纯色块，上面放白字
    ground: tuple  # 整幅画面的深色底
    field: tuple   # 大面积的色块，上面放白字
    stub: tuple    # 中等明度的辅助色
    dot: tuple     # 深底上的小色点
    soft: tuple    # 深底上的点缀文字
    hue: float = 70.0     # OKLCH 色相，tone() 按它调出任意明度的同色
    vivid: float = 0.4    # 海报本身的鲜艳程度，0.4~1

    def tone(self, lightness, chroma, shift=0.0):
        """同色相、指定明度的颜色；暗色自动避开发脏的色相。chroma 按海报鲜艳程度缩放。"""
        hue = (self.hue + shift) % 360.0
        if lightness < 0.6:
            hue = settled_hue(hue)
        return oklch(lightness, chroma * self.vivid, hue)


def settled_hue(hue):
    """
    压暗后会发脏的色相换到相邻的好看色相（OKLCH 角度）。
    金黄、黄色压暗是土黄和芥末，改成铁锈橙；黄绿压暗是橄榄绿，改成森林绿。浅色不用换，浅黄本来就好看。
    偏绿的黄（100° 起）归到绿色一边：绿色海报配绿底，也免得黄、橙、黄绿连着几张全是棕色。
    """
    if 72.0 <= hue < 100.0:
        return 46.0
    if 100.0 <= hue < 142.0:
        return 152.0
    return hue


def palette_from(hue_sat):
    if hue_sat is None:
        hue, vivid = 255.0, 0.7
    else:
        hsv_hue, saturation = hue_sat
        _lightness, a_axis, b_axis = oklab(_hsv(hsv_hue, max(0.55, saturation), 0.9))
        hue = math.degrees(math.atan2(b_axis, a_axis)) % 360.0
        vivid = clamp((saturation - 0.12) / 0.5, 0.4, 1.0)
    return palette_at(hue, vivid)


def palette_at(hue, vivid):
    dark = settled_hue(hue)
    return Palette(
        block=oklch(0.40, 0.12 * vivid, dark),
        ground=oklch(0.17, 0.035 * vivid, dark),
        field=oklch(0.3, 0.075 * vivid, dark),
        stub=oklch(0.5, 0.13 * vivid, dark),
        dot=oklch(0.65, 0.13 * vivid, dark),
        soft=oklch(0.87, 0.075 * vivid, hue),
        hue=hue,
        vivid=vivid,
    )


class PosterCover(Cover):
    """海报风格的公共部分：每张海报的配色。"""

    def poster_image(self, index, size, paper):
        """海报连同边缘的片名完整保留；画框比例不同时用海报色衬底，不放大裁切。"""
        width, height = (max(1, int(value)) for value in size)
        source = self.picture(index).pick(2 / 3.0).image.convert("RGB")
        if source.width / source.height >= 0.9:
            # 没有竖版海报、只能用横图时，按画框比例裁切，不在海报位里放一张带色边的小横图
            return self.fit(index, (width, height)).convert("RGB")
        ratio = min(width / source.width, height / source.height)
        shown = source.resize((max(1, min(width, int(round(source.width * ratio)))),
                               max(1, min(height, int(round(source.height * ratio))))), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (width, height), paper)
        canvas.paste(shown, ((width - shown.width) // 2, (height - shown.height) // 2))
        return canvas

    def bounded_layout(self, width, height, part="main"):
        """先按宽度排版，再收缩过高的多行文字，避免与海报重叠。"""
        layout = self.layout(width, part=part)
        factor = 1.0
        while layout.height > height and factor > 0.05:
            factor *= 0.9
            layout = self.layout(width, part=part, size_factor=factor)
        return layout

    def palette(self, index):
        if "_palettes" not in self.__dict__:
            if self.accent:
                hue, sat, _ = colorsys.rgb_to_hsv(*[c / 255.0 for c in self.accent[:3]])
                self._palettes = [palette_from((hue, sat))] * self.count
            else:
                signatures = [signature(self.picture(i).pick(2 / 3.0).image) for i in range(self.count)]
                initial = [palette_from(value) for value in signatures]
                hues = [p.hue for p in initial]
                choices = (255., 15., 165., 300., 55.)
                distance = lambda a, b: abs((a - b + 180) % 360 - 180)
                # Give neutral images a deliberate color that also avoids both source neighbors.
                for i, value in enumerate(signatures):
                    if value is None:
                        neighbors = [hues[(i - 1) % self.count], hues[(i + 1) % self.count]] if self.count > 1 else []
                        hues[i] = max(choices, key=lambda h: min([distance(h, n) for n in neighbors] or [180]))
                for i in range(1, self.count):
                    previous = hues[i - 1]
                    if distance(hues[i], previous) < 55:
                        direction = 1 if (hues[i] - previous + 180) % 360 - 180 >= 0 else -1
                        hues[i] = (previous + direction * 55) % 360
                # The closing edge is part of the cycle. Choose the nearest allowed hue
                # for the final image, keeping its already corrected previous edge intact.
                if self.count >= 3 and distance(hues[-1], hues[0]) < 55:
                    candidates = [(h + offset) % 360 for h in (hues[-2], hues[0]) for offset in (-55., 55.)]
                    allowed = [h for h in candidates if distance(h, hues[-2]) >= 55 - 1e-8
                               and distance(h, hues[0]) >= 55 - 1e-8]
                    hues[-1] = min(allowed, key=lambda h: distance(h, hues[-1]))
                self._palettes = [palette_at(h, max(0.6, p.vivid) if value is None else p.vivid)
                                  for h, p, value in zip(hues, initial, signatures)]
        return self._palettes[index % self.count]

    def beat(self, phase, hold=0.6, seconds=None):
        """(当前拍, 转场进度 0~1)，进度没有缓动，各款按需要自己加。"""
        if seconds is not None:
            # 以默认十二秒循环定节奏，增加素材数量时仍留足一次转场的时间。
            hold = clamp(1 - seconds * self.count / 12, 0.1, 0.9)
        position = (phase % 1.0) * self.count
        local = position - int(position)
        return int(position) % self.count, ((local - hold) / (1.0 - hold) if local > hold else 0.0)

    def aspect(self, index):
        """海报的宽高比；横图会被裁成 2:3，按 2:3 算。"""
        source = self.picture(index).pick(2 / 3.0).image
        aspect = source.width / source.height
        return aspect if aspect < 0.9 else 2 / 3.0

    def poster_size(self, index, height, max_width):
        """按原图比例确定画框，过宽的素材整体缩小，仍然完整保留。"""
        aspect = self.aspect(index)
        width = min(max_width, height * aspect)
        return max(1, round(width)), max(1, round(width / aspect))

    def put_poster(self, canvas, poster, x_pos, y_pos, blur, alpha, offset):
        """在画布上做投影再贴海报，虚化不受小图边缘截断。"""
        mask = Image.new("L", self.size)
        mask.paste(poster.getchannel("A"), (round(x_pos), round(y_pos + offset)))
        shadow = Image.new("RGBA", self.size)
        shadow.putalpha(mask.filter(ImageFilter.GaussianBlur(max(0.1, blur))).point(lambda value: round(value * alpha)))
        canvas.alpha_composite(shadow)
        composite_at(canvas, poster, x_pos, y_pos)


# ---------------------------------------------------------------- 动效工具


def calm_mask(size, boxes, pad, blur):
    """文字周围的柔和遮罩，float32 (H, W)，0~1。boxes 是 (left, top, right, bottom) 列表，取并集外扩 pad 后高斯模糊。"""
    mask = Image.new("L", size)
    if boxes:
        draw = ImageDraw.Draw(mask)
        for left, top, right, bottom in boxes:
            draw.rectangle((left - pad, top - pad, right + pad, bottom + pad), fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(blur))
    return np.asarray(mask, dtype=np.float32) / 255.0


def drop_shadow(canvas, layer, x_pos, y_pos, blur, alpha, dx=0.0, dy=0.0):
    """按图层轮廓投影，只在图层附近计算，不生成整幅画布大小的遮罩。"""
    if alpha <= 0:
        return
    pad = int(math.ceil(blur * 2.5)) + 1
    mask = Image.new("L", (layer.width + 2 * pad, layer.height + 2 * pad))
    mask.paste(layer.getchannel("A"), (pad, pad))
    if blur > 0.3:
        mask = mask.filter(ImageFilter.GaussianBlur(blur))
    shadow = Image.new("RGBA", mask.size)
    shadow.putalpha(mask.point(lambda value: round(value * alpha)))
    composite_at(canvas, shadow, x_pos - pad + dx, y_pos - pad + dy)


def _shifted(data, shift_x, shift_y):
    result = np.zeros_like(data)
    height, width = data.shape[:2]
    if abs(shift_x) >= width or abs(shift_y) >= height:
        return result
    src_x, dst_x = (slice(0, width - shift_x), slice(shift_x, width)) if shift_x >= 0 else \
        (slice(-shift_x, width), slice(0, width + shift_x))
    src_y, dst_y = (slice(0, height - shift_y), slice(shift_y, height)) if shift_y >= 0 else \
        (slice(-shift_y, height), slice(0, height + shift_y))
    result[dst_y, dst_x] = data[src_y, src_x]
    return result


def streak(image, dx, dy, taps=5):
    """沿运动方向的拖影，速度快的帧才有，按预乘透明度平均，边缘不发黑。"""
    if math.hypot(dx, dy) < 1.5:
        return image
    mode = image.mode
    data = np.asarray(image.convert("RGBA"), dtype=np.float32)
    alpha = data[..., 3:4] / 255.0
    premultiplied = np.concatenate((data[..., :3] * alpha, data[..., 3:4]), axis=-1)
    total = np.zeros_like(premultiplied)
    for tap in range(taps):
        ratio = tap / (taps - 1) - 0.5
        total += _shifted(premultiplied, int(round(dx * ratio)), int(round(dy * ratio)))
    total /= taps
    cover = total[..., 3:4] / 255.0
    rgb = np.where(cover > 1e-4, total[..., :3] / np.maximum(cover, 1e-4), 0)
    result = Image.fromarray(np.clip(np.concatenate((rgb, total[..., 3:4]), axis=-1), 0, 255).astype(np.uint8))
    return result if mode == "RGBA" else result.convert(mode)


def gradient_fill(alpha, left, right):
    """把字形遮罩填成左右渐变。"""
    data = np.zeros((alpha.height, alpha.width, 4), dtype=np.uint8)
    for channel in range(3):
        data[..., channel] = np.linspace(left[channel], right[channel], alpha.width).astype(np.uint8)
    data[..., 3] = np.asarray(alpha)
    return Image.fromarray(data)


def ink(layer):
    """整幅画布大小的文字图层裁成笔画范围，返回 (小图, x, y)。"""
    box = layer.getbbox()
    if not box:
        return None
    return layer.crop(box), box[0], box[1]


# ---------------------------------------------------------------- 风格 9：百叶


class Lenticular(PosterCover):
    """背景是一扇有立体感的百叶窗，叶片印着海报的三色调画面；片名在左，完整海报在右。转场时叶片逐条翻面。"""

    SLATS = 12

    def prepare(self):
        s = self.s
        self.stills = []
        xs = np.arange(self.W)
        u = 1 - xs / self.W
        ys = np.arange(self.H)
        local = (xs % (self.W / self.SLATS)) / (self.W / self.SLATS)
        # 每片叶子像一段弧面：左边迎光，右边转进阴影
        slat = 1 + 0.11 * (1 - local) ** 2 - 0.24 * local ** 3
        # 左侧放字，整体往左压暗
        scrim = 0.86 - 0.3 * smooth_step((u - 0.42) / 0.5)
        vertical = (1.05 - 0.13 * (ys / self.H))[:, None, None]
        light = (slat * scrim)[None, :, None] * vertical
        w = smooth_step((u - 0.50) / 0.30)
        right_mix = (0.18 * smooth_step((u - 0.42) / 0.5))[None, :, None]
        gx, gy = np.meshgrid(xs, ys)
        d = ((1 - gx / self.W) - 0.47) + (gy / self.H - 0.5) * 0.35
        band = np.exp(-(d / 0.16) ** 2)
        gap, lip = max(1, round(2 * s)), max(1, round(1.5 * s))
        for index in range(self.count):
            palette = self.palette(index)
            rich = palette_at(palette.hue, max(palette.vivid, 0.72))
            # 暗部已经换过色相的（黄→铁锈、黄绿→森林绿）不再分离色调：黄的亮部和片名往金色靠，黄绿保持原色
            settled = settled_hue(rich.hue) != rich.hue
            rust = settled and settled_hue(rich.hue) == 46.0
            low_shift = 0 if self.accent or settled else -22
            high_shift = 0 if self.accent else (-18 if rust else 0 if settled else 26)
            title_shift = 0 if self.accent else (-24 if rust else 0 if settled else 35)
            width, height = self.poster_size(index, 860 * s, 760 * s)
            x_pos, y_pos = self.W - 170 * s - width, (self.H - height) / 2
            rule_w, rule_gap = max(2, round(12 * s)), 44 * s
            rule_x = 150 * s
            text_left = rule_x + rule_w + rule_gap
            text_right = x_pos - 110 * s
            max_width = max(1, text_right - text_left)
            main = self.bounded_layout(max_width, 510 * s)
            sub = self.bounded_layout(max_width, 150 * s, part="sub") if self.has_sub() else None
            text_gap = 30 * s
            total = main.height + (text_gap + sub.height if sub else 0)
            top = (self.H - total) / 2
            shadow = ((0, 0, 0, 150), 22 * s, 4 * s)
            title_left = rich.tone(0.93, 0.06)
            title_right = rich.tone(0.82, 0.15, shift=title_shift)
            main_mask = main.render(self.size, text_left, top).getchannel("A")
            colored = gradient_fill(main_mask, title_left, title_right)
            title = ink(colored)
            boxes = [(title[1], title[2], title[1] + title[0].width, title[2] + title[0].height)] if title else []
            caption = None
            if sub:
                sub_y = top + main.height + text_gap
                sub_fill = rich.tone(0.86, 0.12) + (255,)
                caption = ink(sub.render(self.size, text_left, sub_y, sub_fill=sub_fill))
                caption_layer = sub.render(self.size, text_left, sub_y, sub_fill=sub_fill, shadow=shadow)
            rule = None
            if title:
                rule_top = title[2]
                rule_bottom = caption[2] + caption[0].height if caption else title[2] + title[0].height
                rule_h = max(1, round(rule_bottom - rule_top))
                rule = gradient_fill(Image.new("L", (rule_h, rule_w), 255), title_left, title_right).transpose(Image.Transpose.ROTATE_270)
                boxes.append((round(rule_x), round(rule_top), round(rule_x) + rule_w, round(rule_top) + rule_h))
            if caption:
                boxes.append((caption[1], caption[2], caption[1] + caption[0].width, caption[2] + caption[0].height))
            # 无字横版背景图转三色调：海报放大后片名大字会变成背景里的一团乱字，所以用背景图并柔化
            gray = ImageOps.autocontrast(ImageOps.grayscale(self.fit(index, self.size, clean=True).convert("RGB")),
                                         cutoff=1)
            sharp = np.asarray(gray.filter(ImageFilter.GaussianBlur(max(0.5, 4 * s))), dtype=np.float32) / 255.0
            soft = np.asarray(gray.filter(ImageFilter.GaussianBlur(max(1.0, 20 * s))), dtype=np.float32) / 255.0
            value = (sharp * (1 - w) + soft * w)[..., None]
            stops = [np.array(color, float) for color in
                     (rich.tone(0.15, 0.08, shift=low_shift), rich.tone(0.40, 0.19),
                      rich.tone(0.74, 0.16, shift=high_shift))]
            low = stops[0] + (stops[1] - stops[0]) * np.clip(value / 0.55, 0, 1)
            data = np.where(value < 0.55, low, stops[1] + (stops[2] - stops[1]) * np.clip((value - 0.55) / 0.45, 0, 1))
            data = data * (1 - right_mix) + stops[0] * right_mix
            m = calm_mask(self.size, boxes, pad=60 * s, blur=110 * s)
            mix = 0.40 * m[..., None]
            data = data * (1 - mix) + stops[0] * mix
            data *= light
            # 叶片之间的缝与迎光棱
            for strip in range(1, self.SLATS):
                x = round(strip * self.W / self.SLATS)
                data[:, x - gap:x] *= 0.5
                data[:, x:x + lip] = data[:, x:x + lip] * 1.10 + 12
            data = data + (255 - data) * (0.045 * band)[..., None]
            canvas = Image.fromarray(np.clip(data, 0, 255).astype(np.uint8)).convert("RGBA")
            # 海报背后一团海报颜色的光
            glow_x, glow_y = x_pos + width / 2, self.H / 2
            glow = np.exp(-(((gx - glow_x) / (width * 0.95)) ** 2 + ((gy - glow_y) / (height * 0.7)) ** 2)) * 0.5
            canvas.alpha_composite(rgba_from_alpha(rich.tone(0.66, 0.15), glow))
            poster = self.poster_image(index, (width, height), palette.block).convert("RGBA")
            ImageDraw.Draw(poster).rectangle((0, 0, poster.width - 1, poster.height - 1),
                                             outline=GHOST_WHITE + (70,), width=max(1, round(2 * s)))
            drop_shadow(canvas, poster, x_pos, y_pos, blur=9 * s, alpha=0.5, dx=0, dy=6 * s)
            self.put_poster(canvas, poster, x_pos, y_pos, blur=46 * s, alpha=0.55, offset=26 * s)
            drop_shadow(canvas, colored, 0, 0, 22 * s, 0.6, 4 * s, 4 * s)
            canvas.alpha_composite(colored)
            if rule is not None:
                drop_shadow(canvas, rule, rule_x, rule_top, 14 * s, 0.5, 3 * s, 3 * s)
                composite_at(canvas, rule, rule_x, rule_top)
            if sub:
                canvas.alpha_composite(caption_layer)
            self.stills.append(canvas.convert("RGB"))

    def frame(self, phase):
        current, progress = self.beat(phase, seconds=1.1)
        if progress == 0:
            return self.stills[current]
        following = (current + 1) % self.count
        if progress >= 1 - 1e-7:
            return self.stills[following]
        canvas = Image.new("RGBA", self.size, self.palette(current).tone(0.12, 0.05) + (255,))
        for index in range(self.SLATS):
            left, right = round(index * self.W / self.SLATS), round((index + 1) * self.W / self.SLATS)
            local = clamp((progress * 1.1 - index * 0.65 / (self.SLATS - 1)) / 0.45)
            if local == 0 or local == 1:
                source = self.stills[current if local == 0 else following]
                canvas.paste(source.crop((left, 0, right, self.H)), (left, 0))
                continue
            theta = math.pi * ease_in_out(local)
            width = (right - left) * abs(math.cos(theta))
            if width < 0.4:
                continue
            front = theta < math.pi / 2
            source = self.stills[current if front else following]
            strip = source.crop((left, 0, right, self.H))
            # 正面转过去时先扫过一道高光，背面转回来时从阴影里出来
            if front:
                brightness = 1 + 0.3 * math.sin(2 * theta) - 0.35 * math.sin(theta) ** 2
            else:
                brightness = 1 - 0.45 * math.sin(theta)
            strip = ImageEnhance.Brightness(strip).enhance(brightness).convert("RGBA")
            center = (left + right) / 2
            extension = self.H * 0.02 * math.sin(theta)
            quad = [(center - width / 2, -extension if front else 0),
                    (center + width / 2, 0 if front else -extension),
                    (center + width / 2, self.H if front else self.H + extension),
                    (center - width / 2, self.H + extension if front else self.H)]
            warp_onto(canvas, strip, quad)
        return canvas.convert("RGB")


def smooth_step(values):
    values = np.clip(values, 0.0, 1.0)
    return values * values * (3.0 - 2.0 * values)


# ---------------------------------------------------------------- 风格 10：对开


def hue_peaks(image, limit=3):
    """
    图里几簇主要颜色，返回 [(OKLCH 色相, HSV 饱和度, 权重)]，权重从大到小，相邻两簇至少隔 40°。
    权重算法与 signature 一致（鲜艳、不太暗、肤色降权）；近乎黑白的图返回空列表。
    """
    small = image.convert("RGB")
    small.thumbnail((72, 72))
    hsv = np.asarray(small.convert("HSV"), dtype=np.float32) / 255.0
    hue, sat, val = hsv[..., 0].ravel(), hsv[..., 1].ravel(), hsv[..., 2].ravel()
    weight = sat ** 1.4 * np.clip((val - 0.12) / 0.45, 0.0, 1.0)
    skin = (hue < 0.11) & (sat > 0.12) & (sat < 0.62) & (val > 0.3)
    weight = np.where(skin, weight * 0.3, weight)
    if weight.sum() < 0.035 * weight.size:
        return []
    bins = 36
    index = (hue * bins).astype(np.int64) % bins
    hist = np.bincount(index, weights=weight, minlength=bins)
    spread = hist + 0.6 * (np.roll(hist, 1) + np.roll(hist, -1)) + 0.25 * (np.roll(hist, 2) + np.roll(hist, -2))
    peaks = []
    for _ in range(limit):
        peak = int(np.argmax(spread))
        if spread[peak] <= 0 or (peaks and spread[peak] < 0.22 * peaks[0][2]):
            break
        near = np.abs((index - peak + bins // 2) % bins - bins // 2) <= 1
        chosen = weight[near]
        if chosen.sum() <= 0:
            break
        angle = hue[near] * 2.0 * np.pi
        mean = math.atan2(float((chosen * np.sin(angle)).sum()), float((chosen * np.cos(angle)).sum())) / (2 * math.pi)
        saturation = float((chosen * sat[near]).sum() / max(1e-6, chosen.sum()))
        _l, a_axis, b_axis = oklab(_hsv(mean % 1.0, max(0.55, saturation), 0.9))
        peaks.append((math.degrees(math.atan2(b_axis, a_axis)) % 360.0, saturation, float(spread[peak])))
        for offset in range(-4, 5):
            spread[(peak + offset) % bins] = 0
    return peaks


def oklch_array(lightness, chroma, hue):
    """oklch() 的 numpy 版本，超出色域的像素逐步降彩度。返回 0~255 的 float 数组。"""
    angle = np.radians(hue)
    chroma = np.array(chroma, dtype=np.float32) * np.ones_like(lightness)
    for _ in range(12):
        a_axis, b_axis = chroma * np.cos(angle), chroma * np.sin(angle)
        long_ = (lightness + 0.3963377774 * a_axis + 0.2158037573 * b_axis) ** 3
        medium = (lightness - 0.1055613458 * a_axis - 0.0638541728 * b_axis) ** 3
        short = (lightness - 0.0894841775 * a_axis - 1.2914855480 * b_axis) ** 3
        rgb = np.stack((4.0767416621 * long_ - 3.3077115913 * medium + 0.2309699292 * short,
                        -1.2684380046 * long_ + 2.6097574011 * medium - 0.3413193965 * short,
                        -0.0041960863 * long_ - 0.7034186147 * medium + 1.7076147010 * short), axis=-1)
        outside = ((rgb < -0.001) | (rgb > 1.001)).any(axis=-1)
        if not outside.any():
            break
        chroma = np.where(outside, chroma * 0.85, chroma)
    rgb = np.clip(rgb, 0, 1)
    return np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * np.power(rgb, 1 / 2.4) - 0.055) * 255.0


class Diptych(PosterCover):
    """
    对开：左边整幅竖版海报，右边一块取自海报的颜色，主副标题落在色块上。
    转场：下一张海报从上往下切入盖住当前这张，右边的新色块带着片名从下往上升起，两边像剪刀一样错开。
    色块的颜色从海报本身的几簇主色里挑，前后两张至少差 50°，海报里找不到合适的颜色才往旁边挪。
    """

    GAP = 50.0
    PARALLAX = 0.28
    # 这一段色相（黄、黄绿）压暗会发脏，做成亮色块配深色字
    LIGHT = (65.0, 135.0)

    def prepare(self):
        s = self.s
        self.left_w = max(1, round(self.H * 2 / 3))
        self.panel_w = self.W - self.left_w
        self.hues = self._choose_hues()
        self.light = [self.LIGHT[0] <= hue % 360 < self.LIGHT[1] for hue in self.hues]
        self.posters, self.panels, self.lines = [], [], []
        self.text_x = self.left_w + 132 * s
        text_w = max(1, self.W - self.text_x - 120 * s)
        main = self.bounded_layout(text_w, 540 * s)
        sub = self.bounded_layout(text_w, 170 * s, part="sub") if self.has_sub() else None
        bar_h, bar_w = max(2, round(10 * s)), max(4, round(92 * s))
        bar_gap = 52 * s
        gap = self.spec.spacing * s if sub else 0
        total = bar_h + bar_gap + main.height + gap + (sub.height if sub else 0)
        top = (self.H - total) / 2 - 18 * s
        self.dots_y = self.H - 118 * s
        bar_x, bar_top = round(self.text_x), round(top)
        boxes = [(bar_x, bar_top, bar_x + bar_w, bar_top + bar_h)]
        title = ink(main.render(self.size, self.text_x, top + bar_h + bar_gap))
        if title:
            boxes.append((title[1], title[2], title[1] + title[0].width, title[2] + title[0].height))
        if sub:
            caption = ink(sub.render(self.size, self.text_x, top + bar_h + bar_gap + main.height + gap))
            if caption:
                boxes.append((caption[1], caption[2], caption[1] + caption[0].width, caption[2] + caption[0].height))
        self.calm = calm_mask(self.size, boxes, pad=70 * s, blur=90 * s)[:, self.left_w:]
        for index in range(self.count):
            poster = self.fit(index, (self.left_w, self.H)).convert("RGB")
            edge_w = max(1, round(2 * s))
            edge = np.asarray(poster.crop((poster.width - edge_w, 0, poster.width, self.H)), dtype=np.float32)
            poster.paste(Image.fromarray((edge * 0.82 + 255 * 0.18).astype(np.uint8)), (poster.width - edge_w, 0))
            self.posters.append(poster)
            self.panels.append(self._panel(index))
            light = self.light[index]
            bar = gradient_fill(Image.new("L", (bar_w, bar_h), 255),
                                *((self.tone(index, 0.42, 0.14), self.tone(index, 0.55, 0.18, 28)) if light else
                                  (self.tone(index, 0.93, 0.08), self.tone(index, 0.80, 0.16, 28))))
            lines = [(bar, self.text_x, top)]
            title = ink(main.render(self.size, self.text_x, top + bar_h + bar_gap,
                                    main_fill=(self.tone(index, 0.25, 0.05) if light else self.tone(index, 0.975, 0.022)) + (255,)))
            if title:
                lines.append(title)
            if sub:
                caption = ink(sub.render(self.size, self.text_x, top + bar_h + bar_gap + main.height + gap,
                                         sub_fill=(self.tone(index, 0.40, 0.09, 20) if light else self.tone(index, 0.86, 0.10, 20)) + (255,)))
                if caption:
                    lines.append(caption)
            self.lines.append(lines)
        self.slabs = [self._slab(index) for index in range(self.count)]
        self.stills = []
        for index in range(self.count):
            canvas = Image.new("RGB", self.size)
            canvas.paste(self.posters[index], (0, 0))
            canvas.paste(self.slabs[index], (self.left_w, 0))
            self.stills.append(canvas)

    # ------------------------------------------------------------ 配色

    def _choose_hues(self):
        if self.accent:
            hue = palette_from(colorsys.rgb_to_hsv(*[c / 255.0 for c in self.accent[:3]])[:2]).hue
            self.vivids = [0.85] * self.count
            return [hue] * self.count
        distance = lambda a, b: abs((a - b + 180) % 360 - 180)
        peaks = [hue_peaks(self.picture(i).pick(2 / 3.0).image) for i in range(self.count)]
        self.vivids = [clamp((p[0][1] - 0.12) / 0.5, 0.72, 1.0) if p else 0.8 for p in peaks]
        hues = []
        for i, options in enumerate(peaks):
            avoid = [hues[-1]] if hues else []
            if i == self.count - 1 and self.count >= 3:
                avoid.append(hues[0])
            fits = [h for h, _sat, _w in options if all(distance(h, a) >= self.GAP for a in avoid)]
            if fits:
                hues.append(fits[0])
                continue
            # 海报里的颜色都和前一张太像：取主色，往远离前一张的方向挪到刚好够远
            want = options[0][0] if options else ((hues[-1] + 150) % 360 if hues else 250.0)
            candidates = [(want + step) % 360 for step in range(0, 360, 5)]
            # 前一张已经是亮色块时，尽量挪到深色块的色相上，免得连着两张都是浅色
            light_before = bool(hues) and self.LIGHT[0] <= hues[-1] < self.LIGHT[1]
            hues.append(min((h for h in candidates if all(distance(h, a) >= self.GAP for a in avoid)),
                            key=lambda h: distance(h, want) + (30 if light_before and self.LIGHT[0] <= h < self.LIGHT[1] else 0),
                            default=want))
        return hues

    def tone(self, index, lightness, chroma, shift=0.0):
        hue = (self.hues[index % self.count] + shift) % 360.0
        return oklch(lightness, chroma * self.vivids[index % self.count], hue)

    def _panel(self, index):
        """
        右边的色块：把海报镜像过来大幅虚化，只留明暗起伏，再按这一张的颜色重新上色。
        这样色块里的光影和海报连成一片，颜色却是统一的一种，不会花。
        """
        s, w, h = self.s, self.panel_w, self.H
        # 同一部作品的无字横图，转成单色印在色块里；没有横图时退回海报，虚化得更狠，片名大字只剩一团光影
        picture = self.fit(index, (w, h), clean=True).convert("L")
        clean = self.picture(index).pick(w / h, clean=True).aspect > 1.15
        picture = ImageOps.autocontrast(picture, cutoff=1).filter(ImageFilter.GaussianBlur(max(0.6, (3.5 if clean else 18) * s)))
        image = np.asarray(picture, dtype=np.float32) / 255.0
        x, y = np.meshgrid(np.arange(w, dtype=np.float32) / w, np.arange(h, dtype=np.float32) / h)
        base = np.clip(0.76 - 0.46 * y - 0.22 * x + 0.10 * np.exp(-(((x - 0.18) ** 2) / 0.10 + ((y - 0.30) ** 2) / 0.14)), 0, 1)
        # 亮色块上的影像淡一些，否则像蒙了一层灰
        amount = 0.26 if self.light[index % self.count] else 0.50
        amount *= (1 - 0.8 * self.calm) * (0.55 + 0.45 * smooth_step((x - 0.15) / 0.6))
        lum = np.clip(base + amount * (image - image.mean()), 0, 1)
        hue = self.hues[index % self.count]
        vivid = self.vivids[index % self.count]
        if self.light[index % self.count]:
            # 黄、黄绿压暗会发脏，这几种颜色做成亮色块配深色字
            lightness = 0.83 + 0.11 * lum
            chroma = (0.115 + 0.07 * lum) * vivid
            seam = 0.24
        elif 25.0 <= hue % 360 < 65.0:
            # 红橙保持鲜一点，做成砖红和焦橙
            vivid = max(vivid, 0.9)
            lightness = 0.34 + 0.27 * lum
            chroma = (0.085 + 0.09 * lum) * vivid
            seam = 0.5
        else:
            lightness = 0.22 + 0.31 * lum
            chroma = (0.07 + 0.10 * lum) * vivid
            seam = 0.5
        rgb = oklch_array(lightness, chroma, hue + (lum - 0.5) * 24.0)
        # 海报压在色块上的贴身影与环境影
        px = x * w
        rgb *= (1 - seam * (0.55 * np.exp(-px / max(1, 9 * s)) +
                            0.45 * np.exp(-px / max(1, 64 * s))))[..., None]
        return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))

    def _slab(self, index, lag=0.0):
        """右边整块：色块、文字和页码条。lag 让文字和页码条比色块晚一步到位，越往上的行晚得越多。"""
        s = self.s
        slab = self.panels[index].convert("RGBA")
        lines = self.lines[index]
        for number, (layer, x_pos, y_pos) in enumerate(lines):
            delay = lag * (120 + 80 * (len(lines) - 1 - number)) * s
            composite_at(slab, layer, x_pos - self.left_w, y_pos + delay)
        if self.count > 1:
            draw = ImageDraw.Draw(slab)
            short, long_, gap, height = 34 * s, 104 * s, 14 * s, max(2, round(5 * s))
            pen, grow = self.text_x - self.left_w, 1 - lag
            for number in range(self.count):
                lit = grow if number == index else 0.0
                width = short + (long_ - short) * lit
                draw.rounded_rectangle((pen, self.dots_y, pen + width, self.dots_y + height), radius=height / 2,
                                       fill=(self.tone(index, 0.68 - 0.40 * lit, 0.04 + 0.05 * lit) if self.light[index]
                                             else self.tone(index, 0.6 + 0.36 * lit, 0.05 + 0.07 * lit)) + (255,))
                pen += width + gap
        return slab.convert("RGB")

    # ------------------------------------------------------------ 动画

    def frame(self, phase):
        current, progress = self.beat(phase, seconds=1.45)
        if progress == 0:
            return self.stills[current]
        following = (current + 1) % self.count
        if progress >= 1 - 1e-7:
            return self.stills[following]
        s, H, L, P = self.s, self.H, self.left_w, self.panel_w
        canvas = Image.new("RGBA", self.size, (0, 0, 0, 255))
        shadow_h = max(1, round(80 * s))

        # 左：新海报从上往下切入，旧海报被压着往下退、变暗
        a = ease_in_out(clamp(progress / 0.8))
        old = ImageEnhance.Brightness(self.posters[current]).enhance(1 - 0.6 * a)
        canvas.paste(old.crop((0, 0, L, H - round(H * self.PARALLAX * a))), (0, round(H * self.PARALLAX * a)))
        edge = round(H * a)
        if edge > 0:
            ramp = np.linspace(0.6, 0, shadow_h, dtype=np.float32)[:, None].repeat(L, 1)
            composite_at(canvas, rgba_from_alpha((0, 0, 0), ramp), 0, edge)
            layer = self.posters[following].crop((0, H - edge, L, H))
            velocity = H * (ease_in_out(clamp((progress + 1 / 30) / 0.8)) - a)
            layer = streak(layer, 0, min(velocity * 0.22, 34 * s))
            canvas.paste(layer, (0, 0))

        # 右：新色块从下往上升起，旧色块被顶着往上退；文字和页码条比色块晚一步
        b = ease_in_out(clamp((progress - 0.12) / 0.8))
        old = ImageEnhance.Brightness(self.slabs[current]).enhance(1 - 0.55 * b)
        lift = round(H * self.PARALLAX * b)
        canvas.paste(old.crop((0, lift, P, H)), (L, 0))
        top = round(H * (1 - b))
        if top < H:
            ramp = np.linspace(0, 0.6, shadow_h, dtype=np.float32)[:, None].repeat(P, 1)
            composite_at(canvas, rgba_from_alpha((0, 0, 0), ramp), L, top - shadow_h)
            lag = 1 - ease_out(clamp((progress - 0.3) / 0.7))
            slab = self.slabs[following] if lag <= 0 else self._slab(following, lag)
            canvas.paste(slab.crop((0, 0, P, H - top)), (L, top))
        return canvas.convert("RGB")
# ---------------------------------------------------------------- 风格 11：月洞


def arch_mask(size, box, ratio=0.5, supersample=3):
    """上端为半圆的拱形，box 是 (left, top, width, height)。超采样画好再缩，边才平滑。"""
    left, top, width, height = [float(value) for value in box]
    mask = Image.new("L", (int(size[0] * supersample), int(size[1] * supersample)), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [left * supersample, top * supersample, (left + width) * supersample - 1, (top + height) * supersample - 1],
        radius=width * ratio * supersample, corners=(True, True, False, False), fill=255)
    return mask.resize(size, Image.Resampling.LANCZOS)


def wall_tooth(size, seed=11, coarse=0.010, fine=0.006):
    """墙面的颗粒：低频起伏加细颗粒，返回 -1~1 的浮点场，正负分开叠才不会糊成水痕。"""
    width, height = max(8, size[0] // 4), max(8, size[1] // 4)
    rng = np.random.default_rng(seed)
    field = rng.normal(0.0, 1.0, (height, width)).astype(np.float32) * fine
    small = rng.normal(0.0, 1.0, (max(2, height // 10), max(2, width // 10))).astype(np.float32)
    broad = np.asarray(Image.fromarray(((small - small.min()) / (np.ptp(small) + 1e-6) * 255).astype(np.uint8), "L")
                       .resize((width, height), Image.Resampling.BICUBIC), dtype=np.float32) / 255.0 - 0.5
    field = np.clip(field + broad * coarse, -1.0, 1.0)
    return np.asarray(Image.fromarray(((field + 1.0) * 127.5).astype(np.uint8), "L")
                      .resize(size, Image.Resampling.BILINEAR), dtype=np.float32) / 127.5 - 1.0


def grain_over(canvas, field, color, amount):
    """把颗粒场按正负分别提亮、压暗后叠上去，得到和画布同尺寸的 RGBA 图层。"""
    alpha = np.where(field >= 0, np.clip(field, 0, 1) * amount, np.clip(-field, 0, 1) * amount)
    data = np.zeros((canvas.height, canvas.width, 4), dtype=np.uint8)
    data[..., :3] = color[:3]
    data[..., 3] = np.clip(alpha * 255, 0, 255).astype(np.uint8)
    return Image.fromarray(data, "RGBA")


class MoonGate(PosterCover):
    """
    深色墙上开一扇顶天的大拱龛，完整的海报装在卡纸里，像美术馆的壁龛。
    龛口右侧是直接刻在墙上的片名：一道色标、一条细线、一块压得很低的标题。
    换片时旧画顺着龛口滑上去，下一幅从下面推上来；龛口、卡纸和墙上的字都不动，墙的颜色跟着换。
    """

    MATTE = 13.0     # 卡纸宽度，1080p 像素
    POSTER_H = 0.885
    POSTER_W = 0.43
    ARCH = 0.44      # 拱顶半径占龛口宽度的比例

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        self.walls, self.sheets, self.lips, self.blocks = [], [], [], []
        x_axis, y_axis = axes(self.size)
        field = wall_tooth(self.size)
        light = np.exp(-(((x_axis - 0.24) / 0.46) ** 2 + ((y_axis - 0.10) / 0.66) ** 2))
        floor = np.clip((y_axis - 0.62) / 0.38, 0.0, 1.0) ** 1.7

        # 龛的几何在整个循环里不变，换的只是龛里的画，所以画面不会跳
        poster_h = H * self.POSTER_H
        poster_w = min(poster_h * 2.0 / 3.0, W * self.POSTER_W)
        poster_h = poster_w * 1.5
        matte = max(2.0, self.MATTE * s)
        sheet_w, sheet_h = poster_w + matte * 2, poster_h + matte * 2
        sheet_x = W * 0.042
        sheet_y = (H - sheet_h) / 2.0
        self.sheet_box = (sheet_x, sheet_y, sheet_w, sheet_h)
        thick = max(2.0, 9.0 * s)
        self.opening = (sheet_x - thick, sheet_y - thick, sheet_w + thick * 2, sheet_h + thick * 2)
        self.clip = arch_mask(self.size, self.opening, ratio=self.ARCH)
        inner = arch_mask(self.size, (self.opening[0] + thick * 1.6, self.opening[1] + thick * 1.6,
                                      self.opening[2] - thick * 3.2, self.opening[3] - thick * 3.2),
                          ratio=self.ARCH)
        lip_shape = ImageChops.subtract(self.clip, inner)
        rim_shape = ImageChops.subtract(
            arch_mask(self.size, (self.opening[0] - thick, self.opening[1] - thick,
                                  self.opening[2] + thick * 2, self.opening[3] + thick * 2), ratio=self.ARCH),
            self.clip)
        # 光从左上来：龛壁左亮右暗，龛本身再压一层底
        side = np.clip((x_axis - 0.18) / 0.30, 0.0, 1.0)
        left_light = Image.fromarray((np.clip((0.55 - side) * 255, 0, 255)).astype(np.uint8), "L")
        right_shade = Image.fromarray((np.clip((side - 0.45) * 255, 0, 255)).astype(np.uint8), "L")

        text_left = self.opening[0] + self.opening[2] + W * 0.078
        text_right = W - W * 0.055

        for index in range(self.count):
            p = self.palette(index)
            wall = Image.new("RGBA", self.size, p.tone(0.245, 0.072) + (255,))
            wall.alpha_composite(rgba_from_alpha(p.tone(0.76, 0.045), light * 0.26))
            wall.alpha_composite(rgba_from_alpha((0, 0, 0), floor * 0.38))
            wall.alpha_composite(grain_over(wall, field, (255, 255, 255), 0.028))
            wall.alpha_composite(grain_over(wall, -field, (0, 0, 0), 0.030))
            # 右侧墙面收边：一道很淡的竖向接缝，墙才不是一块色板
            ImageDraw.Draw(wall).line([(W * 0.9845, 0), (W * 0.9845, H)],
                                      fill=p.tone(0.08, 0.02) + (46,), width=max(1, int(round(2 * s))))
            self.walls.append(wall.convert("RGB"))

            sheet = Image.new("RGBA", self.size, (0, 0, 0, 0))
            ImageDraw.Draw(sheet).rectangle(
                [sheet_x, sheet_y, sheet_x + sheet_w, sheet_y + sheet_h], fill=p.tone(0.40, 0.028) + (255,))
            poster = self.poster_image(index, (int(poster_w), int(poster_h)), p.tone(0.40, 0.028)).convert("RGBA")
            sheet.alpha_composite(poster, (int(sheet_x + matte), int(sheet_y + matte)))
            ImageDraw.Draw(sheet).rectangle(
                [sheet_x + matte, sheet_y + matte, sheet_x + matte + poster_w, sheet_y + matte + poster_h],
                outline=p.tone(0.15, 0.02) + (175,), width=max(1, int(round(1 * s))))
            self.sheets.append(sheet)

            lip = Image.new("RGBA", self.size, (0, 0, 0, 0))
            shade = Image.new("RGBA", self.size, p.tone(0.045, 0.02) + (255,))
            shade.putalpha(ImageChops.multiply(lip_shape, right_shade.point(lambda value: int(value * 0.92))))
            lip.alpha_composite(shade)
            shine = Image.new("RGBA", self.size, p.tone(0.95, 0.012) + (255,))
            shine.putalpha(ImageChops.multiply(lip_shape, left_light.point(lambda value: int(value * 0.30))))
            lip.alpha_composite(shine)
            base = Image.new("RGBA", self.size, p.tone(0.055, 0.02) + (255,))
            base.putalpha(lip_shape.point(lambda value: int(value * 0.52)))
            lip.alpha_composite(base)
            edge = Image.new("RGBA", self.size, p.tone(0.94, 0.01) + (255,))
            edge.putalpha(rim_shape.point(lambda value: int(value * 0.12)))
            lip.alpha_composite(edge)
            self.lips.append(lip)

            self.blocks.append(self._label(p, text_left, text_right))

    def _label(self, palette, left, right):
        """墙上刻的片名：一枚色标、一条通到右缘的细线，标题压在线上，下面一小段短线收尾。"""
        W, H, s = self.W, self.H, self.s
        placed = Image.new("RGBA", self.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(placed)
        block, margin, ink_h = self.title_block(
            right - left, sub_style="text",
            main_fill=palette.tone(0.972, 0.012), sub_fill=palette.tone(0.815, 0.07))
        top = H * 0.5 - ink_h / 2.0 - 6 * s
        rule_y = top - 46 * s
        draw.rectangle([left, rule_y, left + W * 0.0085, rule_y + max(3, 3.4 * s)],
                       fill=palette.tone(0.70, 0.17) + (255,))
        draw.rectangle([left + W * 0.0215, rule_y + max(1.0, 1.5 * s), right, rule_y + max(2.0, 2.6 * s)],
                       fill=palette.tone(0.90, 0.02) + (44,))
        composite_at(placed, block, left - margin, top - margin)
        low = top + ink_h + 40 * s
        draw.rectangle([left, low, left + (right - left) * 0.20, low + max(1.4, 1.8 * s)],
                       fill=palette.tone(0.88, 0.03) + (58,))
        draw.rectangle([left + (right - left) * 0.20 + W * 0.012, low, right, low + max(1.0, 1.1 * s)],
                       fill=palette.tone(0.90, 0.02) + (22,))
        return placed

    def _clip(self, layer, offset_y=0.0):
        """把整幅画布大小的图层上下移后按龛口裁好：龛口外面没有东西。"""
        moved = Image.new("RGBA", self.size, (0, 0, 0, 0))
        moved.alpha_composite(layer, (0, int(round(offset_y))))
        clipped = Image.new("RGBA", self.size, (0, 0, 0, 0))
        clipped.paste(moved, (0, 0), self.clip)
        return clipped

    def frame(self, phase):
        current, progress = self.beat(phase, seconds=1.75)
        following = (current + 1) % self.count
        blend = ease_in_out(progress)
        canvas = self.walls[current]
        if blend > 0:
            canvas = Image.blend(canvas, self.walls[following], blend)
        canvas = canvas.convert("RGBA")
        if progress <= 0:
            canvas.alpha_composite(self._clip(self.sheets[current]))
            canvas.alpha_composite(self.lips[current])
            canvas.alpha_composite(self.blocks[current])
            return canvas
        if progress >= 1:
            canvas.alpha_composite(self._clip(self.sheets[following]))
            canvas.alpha_composite(self.lips[following])
            canvas.alpha_composite(self.blocks[following])
            return canvas
        # 旧画顺着龛口滑上去，下一幅从下面推上来：龛口不动，像有人在换画
        travel = self.sheet_box[3] + 30 * self.s
        glide = ease_in_out(progress)
        canvas.alpha_composite(self._clip(self.sheets[current], -travel * glide))
        canvas.alpha_composite(self._clip(self.sheets[following], travel * (1.0 - glide)))
        canvas.alpha_composite(self.lips[current])
        canvas.alpha_composite(with_alpha(self.blocks[current], max(0.0, 1.0 - progress * 2.8)))
        canvas.alpha_composite(with_alpha(self.blocks[following], max(0.0, progress * 2.8 - 1.8)))
        return canvas


# ---------------------------------------------------------------- 风格 12：展墙


class VerticalBlock:
    """
    竖排的片名：主标题逐字成列，列从右往左排；副标题转九十度，读起来也是从上往下。
    放不下时先收字号，再让主标题折成两列；返回的图层以文字块左上角为原点。
    """

    def __init__(self, title, fonts, spec, scale, max_width, max_height, sub_tracking=0.16):
        self.main = "".join(str(title[0] or "").split())
        self.sub = " ".join(str(title[1] or "").split()).upper()
        self.main_path, self.sub_path = fonts[0], fonts[1]
        self.max_width, self.max_height = float(max_width), float(max_height)
        self.sub_tracking = sub_tracking
        self.size, self.columns, self.step, self.gap = self._fit(spec.main_size * scale)

    def _fit(self, main_size):
        size = main_size
        while size >= main_size * 0.34:
            step = size * 1.04
            rows = max(1, int(self.max_height // step)) if self.main else 1
            columns = [self.main[i:i + rows] for i in range(0, len(self.main), rows)] if self.main else []
            gap = size * 0.30
            width = len(columns) * step + max(0, len(columns) - 1) * gap
            if self.sub:
                width += size * 0.30 * 1.8 + size * 0.5
            if width <= self.max_width:
                return size, columns, step, gap
            size *= 0.96
        size = main_size * 0.34
        return size, ([self.main] if self.main else []), size * 1.04, 0.0

    def _sub_strip(self, sub_size):
        font = load_font(self.sub_path, sub_size)
        tracking = sub_size * self.sub_tracking
        total = tracked_width(font, self.sub, tracking)
        strip = Image.new("L", (int(total) + int(sub_size * 1.2), int(sub_size * 1.8)), 0)
        draw = ImageDraw.Draw(strip)
        pen = sub_size * 0.6
        for index, char in enumerate(self.sub):
            draw.text((pen, strip.height / 2), char, font=font, fill=255, anchor="lm")
            advance = font.getlength(char) + tracking
            if index + 1 < len(self.sub):
                pair = self.sub[index:index + 2]
                advance += font.getlength(pair) - font.getlength(pair[0]) - font.getlength(pair[1])
            pen += advance
        return strip.rotate(-90, expand=True)

    @property
    def height(self):
        return max(len(column) for column in self.columns) * self.step if self.columns else self.step

    def render(self, fill, sub_fill):
        """在巨大的透明图层上画好再裁到笔画，返回 (图层, 文字块高度)。"""
        size = self.size
        font = load_font(self.main_path, size)
        sub_img = self._sub_strip(size * 0.30) if self.sub else None
        column_w = len(self.columns) * self.step + max(0, len(self.columns) - 1) * self.gap
        mask = Image.new("L", (max(1, int(column_w + size * 0.8)), max(1, int(self.height + size * 1.2))), 0)
        draw = ImageDraw.Draw(mask)
        for index, column in enumerate(self.columns):
            x = column_w - index * (self.step + self.gap) - self.step / 2.0
            for row, char in enumerate(column):
                draw.text((x, size * 0.1 + row * self.step + self.step / 2.0), char,
                          font=font, fill=255, anchor="mm")
        if sub_img is not None:
            mask.paste(sub_img, (int(column_w + size * 0.20), int(size * 0.02)))
        layer = Image.new("RGBA", mask.size, fill[:3] + (0,))
        layer.putalpha(mask)
        if sub_img is not None:
            sub_layer = Image.new("RGBA", mask.size, sub_fill[:3] + (0,))
            sub_mask = Image.new("L", mask.size, 0)
            sub_mask.paste(sub_img, (int(column_w + size * 0.20), int(size * 0.02)))
            main_mask = ImageChops.subtract(mask, sub_mask)
            layer.putalpha(main_mask)
            sub_layer.putalpha(sub_mask)
            layer.alpha_composite(sub_layer)
        return layer, self.height


class Hanging(PosterCover):
    """
    浅色纸墙上一块说明板，板上竖排片名；板子右边一根横杆，挂着一排大小不一的装裱海报。
    每一帧整排向左挪一点，画从板子后面出来、在右缘淡出，一个循环正好走完一轮，接得上。
    换成动态封面时这是唯一一款一直在走的风格，静态图取第一帧。
    """

    RAIL_Y = 0.150
    POSTER_H = 0.545
    PITCH = 0.238       # 相邻两幅画的间距，占画布宽度
    BOARD = 0.286       # 说明板右缘的位置
    CORD = (0.030, 0.078, 0.048, 0.090, 0.036, 0.066)
    SCALE = (1.00, 0.88, 0.94, 0.82, 0.97, 0.90)

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        x_axis, y_axis = axes(self.size)
        self.rail_y = H * self.RAIL_Y
        field = wall_tooth(self.size, seed=5, coarse=0.008, fine=0.005)
        self.prints = [self._print(index) for index in range(self.count)]

        # 墙只用一种颜色：整排在走，墙的颜色跟着换会闪
        base = self.palette(0)
        paper = base.tone(0.960, 0.009, shift=8)
        wall = Image.new("RGBA", self.size, paper + (255,))
        wall.alpha_composite(rgba_from_alpha(
            (255, 255, 255),
            np.exp(-(((x_axis - 0.50) / 0.58) ** 2 + ((y_axis + 0.06) / 0.58) ** 2)) * 0.52))
        wall.alpha_composite(rgba_from_alpha(base.tone(0.36, 0.03),
                                             np.clip((y_axis - 0.74) / 0.26, 0.0, 1.0) ** 1.6 * 0.10))
        wall.alpha_composite(grain_over(wall, field, (255, 255, 255), 0.020))
        wall.alpha_composite(grain_over(wall, -field, (0, 0, 0), 0.014))
        # 横杆：从画面左侧一直拉到右缘，板子会盖在它上面，看起来像杆子从墙后穿过
        draw = ImageDraw.Draw(wall)
        ink = base.tone(0.20, 0.02)
        bar = max(1.2, 2.0 * s)
        draw.rectangle([0, self.rail_y, W, self.rail_y + bar], fill=ink + (255,))
        draw.rectangle([0, self.rail_y + bar, W, self.rail_y + bar + max(1.0, 2.2 * s)],
                       fill=(255, 255, 255, 150))
        self.wall = wall.convert("RGB")

        # 说明板：比墙微亮的一块竖板，四边留白，右缘一道细线和一层轻投影
        board_x0, board_x1 = W * 0.045, W * self.BOARD
        board_y0, board_y1 = H * 0.075, H * 0.925
        block = VerticalBlock(self.title, self.fonts, self.spec, s,
                              (board_x1 - board_x0) - W * 0.070, H * 0.60)
        self.boards, self.titles = [], []
        for index in range(self.count):
            p = self.palette(index)
            board = Image.new("RGBA", self.size, (0, 0, 0, 0))
            shadow = Image.new("RGBA", self.size, (0, 0, 0, 0))
            ImageDraw.Draw(shadow).rectangle(
                [board_x0 + W * 0.006, board_y0 + H * 0.012, board_x1 + W * 0.010, board_y1 + H * 0.012],
                fill=(0, 0, 0, 46))
            board.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(10 * s)))
            draw = ImageDraw.Draw(board)
            draw.rectangle([board_x0, board_y0, board_x1, board_y1],
                           fill=p.tone(0.985, 0.005, shift=8) + (255,))
            draw.rectangle([board_x1 - max(1, 1.2 * s), board_y0, board_x1, board_y1],
                           fill=p.tone(0.34, 0.03) + (46,))
            self.boards.append(board)

            layer, text_h = block.render(p.tone(0.125, 0.028) + (255,), p.tone(0.46, 0.05) + (255,))
            placed = Image.new("RGBA", self.size, (0, 0, 0, 0))
            text_x = board_x0 + W * 0.048
            text_y = board_y0 + (board_y1 - board_y0 - text_h) / 2.0 - H * 0.020
            composite_at(placed, layer, text_x, text_y)
            ImageDraw.Draw(placed).rectangle(
                [text_x, text_y + text_h + H * 0.048, text_x + W * 0.062,
                 text_y + text_h + H * 0.048 + max(2, 2.6 * s)],
                fill=p.tone(0.64, 0.16) + (255,))
            self.titles.append(placed)

        self.area_left = W * self.BOARD + W * 0.014
        self.pitch = W * self.PITCH
        # 两端渐隐：画从说明板后出来、走到右缘慢慢消失，两处都不会硬切
        ramp = np.ones((1, W), dtype=np.float32)
        edge = int(W * 0.075)
        ramp[0, W - edge:] = np.linspace(1.0, 0.0, edge) ** 1.4
        start = int(W * (self.BOARD - 0.004))
        ramp[0, :start] = 0.0
        rise = int(W * 0.045)
        ramp[0, start:start + rise] = np.linspace(0.0, 1.0, rise) ** 1.5
        self.fade = Image.fromarray((ramp * 255).astype(np.uint8), "L").resize((W, H), Image.Resampling.NEAREST)

    def _print(self, index):
        """第 index 幅画连同绳子和小夹子的图层，画布宽度只到画框外一点。"""
        W, H, s = self.W, self.H, self.s
        p = self.palette(index)
        height = H * self.POSTER_H * self.SCALE[index % len(self.SCALE)]
        width = height * 2.0 / 3.0
        top = self.rail_y + H * self.CORD[index % len(self.CORD)]
        x_pos = 46 * s
        layer = Image.new("RGBA", (int(width + 104 * s), int(H)), (0, 0, 0, 0))
        poster = self.poster_image(index, (int(width), int(height)), p.tone(0.94, 0.010, shift=6)).convert("RGBA")
        self.put_poster(layer, poster, x_pos, top, blur=18 * s, alpha=0.26, offset=9 * s)
        draw = ImageDraw.Draw(layer)
        draw.rectangle([x_pos, top, x_pos + width, top + height],
                       outline=p.tone(0.60, 0.02) + (110,), width=max(1, int(round(1 * s))))
        cord = p.tone(0.22, 0.02)
        clip_w = max(2, int(round(6.2 * s)))
        for cx in (x_pos + width * 0.20, x_pos + width * 0.80):
            draw.line([(cx, top - clip_w), (cx, self.rail_y + 2 * s)], fill=cord + (235,),
                      width=max(1, int(round(1.5 * s))))
            draw.rectangle([cx - clip_w * 0.58, top - clip_w * 1.3, cx + clip_w * 0.58, top + clip_w * 0.5],
                           fill=p.tone(0.15, 0.02) + (255,))
        return layer

    def frame(self, phase):
        W, s = self.W, self.s
        # 整排匀速向左走完 count 个间距，循环回到第一幅时位置正好接上
        drift = (phase % 1.0) * self.count * self.pitch
        base = int(drift // self.pitch) % self.count
        offset = drift - int(drift // self.pitch) * self.pitch
        canvas = self.wall.convert("RGBA")
        band = Image.new("RGBA", self.size, (0, 0, 0, 0))
        steps = int((W - self.area_left) / self.pitch) + 3
        for step in range(-1, steps):
            x = self.area_left + step * self.pitch - offset
            if x > W + 10 or x + self.pitch < -10:
                continue
            band.alpha_composite(self.prints[(base + step) % self.count], (int(x - 46 * s), 0))
        band.putalpha(ImageChops.multiply(band.getchannel("A"), self.fade))
        canvas.alpha_composite(band)
        canvas.alpha_composite(self.boards[base])
        canvas.alpha_composite(self.titles[base])
        return canvas


POSTER_DESIGNS = {9: Lenticular, 10: Diptych, 11: MoonGate, 12: Hanging}
