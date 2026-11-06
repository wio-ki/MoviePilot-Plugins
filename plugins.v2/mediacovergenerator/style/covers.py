"""
八款封面风格的构图。

每款风格是一个 Cover 子类：prepare() 预先算好不变的图层，frame(phase) 画出循环中某一时刻的画面。
静态封面就是 phase=0 的高清帧，动图是同一套构图的低分辨率逐帧输出，所以两者外观一致。

动图的几条约定：
- 输出 GIF，这是各家 Emby 客户端都能显示的格式
- phase=0 就是完整封面，只显示首帧的客户端看到的也是成品
- 大部分时间画面静止、只在转场时运动。GIF 按帧差压缩，这样体积最小，文字也不会闪
"""
import base64
import math
import random
import subprocess
import tempfile
import time
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from app.log import logger
from app.plugins.mediacovergenerator.style.catalog import style_info
from app.plugins.mediacovergenerator.style.typography import TitleLayout, outline_from_mask
from app.plugins.mediacovergenerator.utils.color_helper import ColorHelper

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
GHOST_WHITE = (248, 248, 255)
INK = (17, 17, 19)
CARD = (29, 29, 32)


# ---------------------------------------------------------------- 时间轴


def ease(value):
    value = max(0.0, min(1.0, value))
    return 0.5 - 0.5 * math.cos(math.pi * value)


def beats(phase, count, hold=0.72):
    """把循环分成 count 拍，每拍先停留再转场。返回 (当前拍, 下一拍, 转场进度 0~1)。"""
    position = (phase % 1.0) * count
    current = int(position) % count
    local = position - int(position)
    progress = ease((local - hold) / (1.0 - hold)) if local > hold else 0.0
    return current, (current + 1) % count, progress


# ---------------------------------------------------------------- 图像工具


def load_images(library_dir, count):
    """按文件名序号读取素材，保证多次生成的顺序稳定。"""
    folder = Path(library_dir)
    files = [path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS]

    def order(path):
        return (0, int(path.stem)) if path.stem.isdigit() else (1, path.name)

    images = []
    for path in sorted(files, key=order):
        if len(images) >= count:
            break
        try:
            with Image.open(path) as source:
                images.append(ImageOps.exif_transpose(source).convert("RGB"))
        except Exception as error:
            logger.warning(f"跳过无法读取的素材 {path}: {error}")
    return images


def _focus(energy, window):
    """在一维显著度上找窗口中心最有内容的位置，返回 0~1 的取景位置。"""
    length = len(energy)
    span = max(1, int(round(window * length)))
    if span >= length:
        return 0.5
    totals = np.convolve(energy, np.ones(span), mode="valid")
    # 略偏向中间，避免被画面边缘的高频纹理带偏
    middle = (len(totals) - 1) / 2.0
    weights = 1.0 - 0.35 * np.abs(np.arange(len(totals)) - middle) / max(1.0, middle)
    return float(np.argmax(totals * weights)) / max(1, len(totals) - 1)


def smart_fit(image, size):
    """裁切到目标比例，取景位置落在画面内容最丰富的地方。"""
    width, height = size
    src_w, src_h = image.size
    target = width / float(height)
    source = src_w / float(src_h)
    thumb = image.convert("HSV").resize((64, 64), Image.Resampling.BILINEAR)
    data = np.asarray(thumb, dtype=np.float32)
    value, saturation = data[..., 2], data[..., 1]
    grad_x = np.abs(np.diff(value, axis=1, prepend=value[:, :1]))
    grad_y = np.abs(np.diff(value, axis=0, prepend=value[:1, :]))
    energy = grad_x + grad_y + saturation * 0.12
    centering = [0.5, 0.5]
    if source > target:
        centering[0] = _focus(energy.sum(axis=0), target / source)
    elif source < target:
        # 人脸通常在画面上半部分，竖向取景偏上
        rows = energy.sum(axis=1) * np.linspace(1.25, 0.75, energy.shape[0])
        centering[1] = _focus(rows, source / target)
    return ImageOps.fit(image, size, method=Image.Resampling.LANCZOS, centering=tuple(centering))


def rgba_from_alpha(color, alpha):
    height, width = alpha.shape
    data = np.zeros((height, width, 4), dtype=np.uint8)
    data[..., 0], data[..., 1], data[..., 2] = color[0], color[1], color[2]
    data[..., 3] = np.clip(alpha * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(data)


def axes(size):
    width, height = size
    return np.meshgrid(np.linspace(0.0, 1.0, width), np.linspace(0.0, 1.0, height))


def smooth(values):
    values = np.clip(values, 0.0, 1.0)
    return values * values * (3.0 - 2.0 * values)


def mix(first, second, ratio):
    return tuple(int(round(a + (b - a) * ratio)) for a, b in zip(first, second))


def rounded_mask(size, radius, supersample=3):
    width, height = int(size[0]), int(size[1])
    mask = Image.new("L", (width * supersample, height * supersample), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [(0, 0), (width * supersample - 1, height * supersample - 1)],
        radius=max(1, radius) * supersample, fill=255,
    )
    return mask.resize((width, height), Image.Resampling.LANCZOS)


def shadow_layer(size, box, radius, blur, alpha, offset_y=0):
    """圆角卡片的投影，返回与画布同尺寸的 RGBA 图层。"""
    x0, y0, x1, y1 = [int(value) for value in box]
    mask = Image.new("L", size, 0)
    mask.paste(rounded_mask((x1 - x0, y1 - y0), radius), (x0, y0 + int(offset_y)))
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    layer.putalpha(mask.filter(ImageFilter.GaussianBlur(max(0.6, blur))).point(lambda v: int(v * alpha)))
    return layer


def border_layer(size, radius, color, width=1):
    """圆角卡片的内描边。"""
    scale = 3
    layer = Image.new("L", (size[0] * scale, size[1] * scale), 0)
    ImageDraw.Draw(layer).rounded_rectangle(
        [(0, 0), (size[0] * scale - 1, size[1] * scale - 1)],
        radius=max(1, radius) * scale, outline=255, width=max(1, int(round(width * scale))),
    )
    alpha = layer.resize(size, Image.Resampling.LANCZOS).point(lambda v: int(v * color[3] / 255.0))
    result = Image.new("RGBA", size, tuple(color[:3]) + (0,))
    result.putalpha(alpha)
    return result


def lift(image, floor=78, saturation=1.35):
    """提亮暗部、增加饱和度，用在镂空文字里，保证和深色底有足够反差。"""
    image = ImageEnhance.Color(image).enhance(saturation)
    data = np.asarray(image, dtype=np.float32)
    data = floor + data * (1.0 - floor / 255.0)
    return Image.fromarray(np.clip(data, 0, 255).astype(np.uint8))


def play_triangle(draw, center, radius, fill):
    x, y = center
    draw.polygon(
        [(x - radius * 0.62, y - radius), (x - radius * 0.62, y + radius), (x + radius, y)],
        fill=fill,
    )


def play_badge(diameter, ring=(255, 255, 255, 235), glass=(255, 255, 255, 42)):
    """圆形播放按钮。"""
    scale = 4
    size = int(diameter) * scale
    badge = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(badge)
    draw.ellipse([(0, 0), (size - 1, size - 1)], fill=glass, outline=ring, width=max(2, size // 34))
    play_triangle(draw, (size * 0.52, size * 0.5), size * 0.17, ring)
    return badge.resize((int(diameter), int(diameter)), Image.Resampling.LANCZOS)


def neon_base(size, intensity=1.0, seed=11):
    """霓虹背景：深色渐变、透视地面线和放射光束。线条都是程序生成的。"""
    width, height = size
    x_axis, y_axis = axes(size)
    data = np.zeros((height, width, 3), dtype=np.float32)
    top, bottom = np.array((6, 7, 18.0)), np.array((3, 3, 9.0))
    data[...] = top * (1.0 - y_axis[..., None]) + bottom * y_axis[..., None]
    # 灭点附近的辉光：紫色打底，中心偏青
    violet = np.exp(-(((x_axis - 0.64) / 0.34) ** 2 + ((y_axis - 0.3) / 0.26) ** 2))[..., None]
    teal = np.exp(-(((x_axis - 0.7) / 0.16) ** 2 + ((y_axis - 0.34) / 0.13) ** 2))[..., None]
    data += (violet * np.array((30, 12, 62.0)) + teal * np.array((0, 34, 44.0))) * intensity
    canvas = Image.fromarray(np.clip(data, 0, 255).astype(np.uint8)).convert("RGBA")

    scale = 2
    layer = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rng = random.Random(seed)
    center_x, center_y = width * scale * 0.68, height * scale * 0.34
    line = max(1, int(height * scale * 0.0018))
    cyan, magenta, violet_line = (40, 226, 255), (255, 54, 188), (150, 90, 255)

    # 透视地面
    horizon = height * scale * 0.5
    for index in range(-14, 15):
        draw.line([(center_x, horizon), (center_x + index * width * scale * 0.2, height * scale)],
                  fill=cyan + (int(46 * intensity),), width=line)
    for index in range(1, 9):
        y_pos = horizon + (height * scale - horizon) * (index / 8.0) ** 2.2
        draw.line([(0, y_pos), (width * scale, y_pos)], fill=magenta + (int(40 * intensity),), width=line)
    # 从灭点放射出去的激光：内细外粗，有纵深感
    for _ in range(46):
        angle = rng.uniform(-math.pi, 0.15)
        inner = rng.uniform(0.1, 0.62) * width * scale
        outer = inner + rng.uniform(0.04, 0.22) * width * scale
        color = rng.choice((cyan, cyan, magenta, magenta, violet_line))
        alpha = int(rng.uniform(70, 190) * intensity)
        thickness = max(1, int(line * (0.8 + 2.2 * inner / (0.62 * width * scale))))
        draw.line([(center_x + math.cos(angle) * inner, center_y + math.sin(angle) * inner * 0.62),
                   (center_x + math.cos(angle) * outer, center_y + math.sin(angle) * outer * 0.62)],
                  fill=color + (alpha,), width=thickness)

    layer = layer.resize(size, Image.Resampling.LANCZOS)
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(height * 0.006)))
    canvas.alpha_composite(layer)
    return canvas


NEON_TRIANGLES = (
    # 中心 x、y，外接圆半径，初始角度，颜色，转动圈数
    (0.665, 0.215, 0.128, 0.30, (40, 226, 255), 1),
    (0.86, 0.27, 0.074, 2.10, (255, 54, 188), -1),
    (0.955, 0.095, 0.05, 1.10, (40, 226, 255), 1),
    (0.79, 0.075, 0.034, 0.70, (178, 92, 255), -1),
    (0.52, 0.09, 0.03, 2.70, (255, 54, 188), 1),
)


def neon_triangles(size, phase=0.0):
    """缓慢转动的霓虹三角：白色灯芯加彩色辉光。每个三角转整数圈，循环首尾相接。"""
    width, height = size
    scale = 2
    tubes = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    cores = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    tube_draw, core_draw = ImageDraw.Draw(tubes), ImageDraw.Draw(cores)
    line = max(2, int(height * scale * 0.0072))
    core = max(1, int(line * 0.42))
    for center_x, center_y, radius, start, color, turns in NEON_TRIANGLES:
        points = []
        for corner in range(4):
            angle = start + turns * phase * math.tau / 3.0 + corner * math.tau / 3.0
            points.append((
                center_x * width * scale + math.cos(angle) * radius * height * scale,
                center_y * height * scale + math.sin(angle) * radius * height * scale,
            ))
        tube_draw.line(points, fill=color + (255,), width=line, joint="curve")
        core_draw.line(points, fill=mix(color, (255, 255, 255), 0.72) + (255,), width=core, joint="curve")
    tubes = tubes.resize(size, Image.Resampling.BILINEAR)
    cores = cores.resize(size, Image.Resampling.BILINEAR)
    wide = tubes.filter(ImageFilter.GaussianBlur(height * 0.022))
    near = tubes.filter(ImageFilter.GaussianBlur(height * 0.007))
    result = Image.new("RGBA", size, (0, 0, 0, 0))
    result.alpha_composite(wide)
    result.alpha_composite(wide)
    result.alpha_composite(near)
    result.alpha_composite(tubes)
    result.alpha_composite(cores)
    return result


# ---------------------------------------------------------------- 风格基类


class Cover:
    """一款风格。子类实现 prepare 和 frame。"""

    def __init__(self, size, images, title, fonts, spec, accent, tint, blur_size, color_ratio, animated):
        self.size = size
        self.W, self.H = size
        self.s = self.H / 1080.0
        self.images = images
        self.count = len(images)
        self.title = title
        self.fonts = fonts
        self.spec = spec
        self.accent = accent
        self.tint = tint
        self.blur_size = float(blur_size if blur_size is not None else 50)
        self.ratio = max(0.0, min(1.0, float(color_ratio if color_ratio is not None else 0.8)))
        self.animated = animated
        self._fits = {}
        self.prepare()

    def prepare(self):
        raise NotImplementedError

    def frame(self, phase):
        raise NotImplementedError

    def image(self, index):
        return self.images[index % self.count]

    def fit(self, index, size):
        """裁好的素材，同一尺寸只算一次。"""
        size = (max(1, int(size[0])), max(1, int(size[1])))
        key = (index % self.count, size)
        if key not in self._fits:
            self._fits[key] = smart_fit(self.image(index), size)
        return self._fits[key]

    def layout(self, max_width, part="both", size_factor=1.0):
        return TitleLayout(self.title, self.fonts, self.spec, self.s, max_width, part=part, size_factor=size_factor)

    def base_color(self):
        """近黑底色，带一点素材的色相，让不同媒体库的封面略有区别。"""
        return mix(INK, self.tint, 0.07 * self.ratio)

    def text_shadow(self, alpha=120):
        return (0, 0, 0, alpha), self.H * 0.012, self.H * 0.004

    def has_sub(self):
        return bool(str(self.title[1] or "").strip())

    def scrim_strength(self):
        """「颜色混合占比」控制压暗的力度，默认 0.8 对应设计强度。"""
        return 0.72 + 0.35 * self.ratio


# ---------------------------------------------------------------- 风格 1：切片


class Slats(Cover):
    """竖向长条切片，其中一片展开。切片轮流展开，标题固定不动。"""

    STRIPS = 6
    START = 2

    def prepare(self):
        self.active_w = int(self.W * 0.36)
        self.rest_w = (self.W - self.active_w) / float(self.STRIPS - 1)
        self.bright = [self.fit(index, (self.active_w, self.H)) for index in range(self.STRIPS)]
        self.dim = [ImageEnhance.Brightness(panel).enhance(0.7) for panel in self.bright]

        x_axis, y_axis = axes(self.size)
        strength = self.scrim_strength()
        bottom = smooth((y_axis - 0.36) / 0.64) * 0.96
        left = smooth((0.72 - x_axis) / 0.72) * smooth((y_axis - 0.3) / 0.45) * 0.72
        alpha = np.clip(1.0 - (1.0 - bottom) * (1.0 - left), 0.0, 1.0) * strength
        self.overlay = rgba_from_alpha(self.base_color(), alpha)

        layout = self.layout(self.W * 0.74)
        self.overlay.alpha_composite(layout.render(
            self.size, self.W * 0.05, self.H * 0.905 - layout.height,
            main_fill=GHOST_WHITE + (255,), sub_fill=self.accent + (255,), shadow=self.text_shadow(),
        ))
        self.gap = max(1, int(round(3 * self.s)))

    def frame(self, phase):
        current, following, progress = beats(phase, self.STRIPS)
        active = (self.START + current) % self.STRIPS
        upcoming = (self.START + following) % self.STRIPS
        canvas = Image.new("RGB", self.size, (0, 0, 0))
        cursor = 0.0
        for index in range(self.STRIPS):
            amount = (1.0 - progress if index == active else 0.0) + (progress if index == upcoming else 0.0)
            width = self.rest_w + (self.active_w - self.rest_w) * amount
            x0, x1 = int(round(cursor)), int(round(cursor + width))
            cursor += width
            shown = max(1, x1 - x0 - (self.gap if index < self.STRIPS - 1 else 0))
            left = (self.active_w - shown) // 2
            box = (left, 0, left + shown, self.H)
            if amount <= 0.0:
                strip = self.dim[index].crop(box)
            elif amount >= 1.0:
                strip = self.bright[index].crop(box)
            else:
                strip = Image.blend(self.dim[index].crop(box), self.bright[index].crop(box), amount)
            canvas.paste(strip, (x0, 0))
        canvas = canvas.convert("RGBA")
        canvas.alpha_composite(self.overlay)
        return canvas


# ---------------------------------------------------------------- 风格 2：巨幕


class Titan(Cover):
    """满幅大图、超宽细体片名，右下角的小卡片露出下一张。"""

    def prepare(self):
        self.frames = [ImageEnhance.Contrast(self.fit(index, self.size)).enhance(1.05) for index in range(self.count)]
        x_axis, y_axis = axes(self.size)
        strength = self.scrim_strength()
        side = smooth((0.6 - x_axis) / 0.6) * 0.6
        bottom = smooth((y_axis - 0.42) / 0.58) * 0.9
        top = smooth((0.16 - y_axis) / 0.16) * 0.22
        alpha = 1.0 - (1.0 - side) * (1.0 - bottom) * (1.0 - top)
        self.overlay = rgba_from_alpha(mix((8, 8, 10), self.tint, 0.1 * self.ratio), np.clip(alpha * strength, 0, 1))

        card_w, card_h = int(self.W * 0.215), int(self.H * 0.25)
        self.card_box = (self.W - int(self.W * 0.036) - card_w, self.H - int(self.H * 0.075) - card_h)
        radius = int(34 * self.s)
        self.card_size = (card_w, card_h)
        self.card_mask = rounded_mask(self.card_size, radius)
        badge = play_badge(card_h * 0.42)
        self.cards = []
        for index in range(self.count):
            card = ImageEnhance.Brightness(self.fit(index, self.card_size)).enhance(0.72).convert("RGBA")
            card.alpha_composite(badge, ((card_w - badge.width) // 2, (card_h - badge.height) // 2))
            card.alpha_composite(border_layer(self.card_size, radius, (255, 255, 255, 70), 2 * self.s))
            self.cards.append(card.convert("RGB"))
        box = (*self.card_box, self.card_box[0] + card_w, self.card_box[1] + card_h)
        self.overlay.alpha_composite(shadow_layer(self.size, box, radius, 26 * self.s, 0.55, 10 * self.s))

        layout = self.layout(self.W * 0.655)
        self.overlay.alpha_composite(layout.render(
            self.size, self.W * 0.046, self.H * 0.885 - layout.height,
            main_fill=(255, 255, 255, 255), sub_fill=(255, 255, 255, 215), shadow=self.text_shadow(150),
        ))

    def frame(self, phase):
        current, following, progress = beats(phase, self.count)
        image = self.frames[current] if progress <= 0 else Image.blend(self.frames[current], self.frames[following], progress)
        canvas = image.convert("RGBA")
        canvas.alpha_composite(self.overlay)
        card_now, card_next = self.cards[following], self.cards[(following + 1) % self.count]
        card = card_now if progress <= 0 else Image.blend(card_now, card_next, progress)
        canvas.paste(card, self.card_box, self.card_mask)
        return canvas


# ---------------------------------------------------------------- 风格 3：镂字


class Cutout(Cover):
    """深色卡片上的巨大镂空标题，图片从字里透出来；右侧放同一张图的圆角卡片。"""

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        base = self.base_color()
        canvas = Image.new("RGBA", self.size, mix(base, (0, 0, 0), 0.25) + (255,))
        card_box = (int(W * 0.024), int(H * 0.044), W - int(W * 0.024), H - int(H * 0.044))
        card_size = (card_box[2] - card_box[0], card_box[3] - card_box[1])
        card = Image.new("RGBA", card_size, mix(CARD, self.tint, 0.1 * self.ratio) + (255,))
        canvas.paste(card, card_box[:2], rounded_mask(card_size, int(46 * s)))
        draw = ImageDraw.Draw(canvas)

        pad = int(W * 0.017)
        self.photo_box = (int(W * 0.665), card_box[1] + pad, card_box[2] - pad, card_box[3] - pad)
        self.photo_size = (self.photo_box[2] - self.photo_box[0], self.photo_box[3] - self.photo_box[1])
        self.photo_mask = rounded_mask(self.photo_size, int(32 * s))

        has_sub = self.has_sub()
        zone_left = W * (0.112 if has_sub else 0.06)
        zone_right = W * 0.642
        zone_width = zone_right - zone_left
        center_y = H * 0.49

        main = self.layout(zone_width, part="main")
        center_x = (zone_left + zone_right) / 2.0
        mask_layer = main.render_centered(self.size, center_x, center_y)
        self.mask = mask_layer.getchannel("A")
        box = self.mask.getbbox() or (int(zone_left), int(H * 0.3), int(zone_right), int(H * 0.7))
        self.text_box = box

        # 文字外缘一圈淡描边，暗图透进字里也不会和底色糊在一起
        outline = outline_from_mask(self.mask, max(1, round(1.6 * s)), 0.5)
        ring = Image.new("RGBA", self.size, GHOST_WHITE + (0,))
        ring.putalpha(outline)
        canvas.alpha_composite(ring)

        rule_h = max(2, int(round(9 * s)))
        rule_y = box[3] + int(36 * s)
        draw.rounded_rectangle([(box[0], rule_y), (box[2], rule_y + rule_h)], radius=rule_h // 2,
                               fill=self.accent + (255,))

        if has_sub:
            sub = self.layout(H * 0.76, part="sub")
            vertical = sub.rotated(fill=GHOST_WHITE + (225,))
            canvas.alpha_composite(vertical, (int(W * 0.056 - vertical.width / 2.0), int(H / 2.0 - vertical.height / 2.0)))

        self.base = canvas.convert("RGB")
        self.pan_w = int((box[2] - box[0]) * 1.24)
        self.pan_h = box[3] - box[1]
        self.fills = [lift(self.fit(index, (self.pan_w, self.pan_h))) for index in range(self.count)]
        self.photos = [self.fit(index, self.photo_size) for index in range(self.count)]

    def _fill(self, index, phase):
        offset = int(round((self.pan_w - (self.text_box[2] - self.text_box[0])) * (0.5 - 0.5 * math.cos(math.tau * phase))))
        return self.fills[index].crop((offset, 0, offset + self.text_box[2] - self.text_box[0], self.pan_h))

    def frame(self, phase):
        current, following, progress = beats(phase, self.count)
        local = (phase * self.count) % 1.0
        canvas = self.base.copy()
        fill = self._fill(current, local * 0.5)
        photo = self.photos[current]
        if progress > 0:
            fill = Image.blend(fill, self._fill(following, (local - 1.0) * 0.5), progress)
            photo = Image.blend(photo, self.photos[following], progress)
        canvas.paste(fill, self.text_box[:2], self.mask.crop(self.text_box))
        canvas.paste(photo, self.photo_box[:2], self.photo_mask)
        return canvas


# ---------------------------------------------------------------- 风格 4：展墙


class Tilt(Cover):
    """倾斜的海报墙铺满左侧，标题靠右对齐。海报列轮流错位滑动一格。"""

    COLUMNS = 5
    ANGLE = 17.0

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        self.tile_w = int(W * 0.158)
        self.tile_h = int(self.tile_w * 1.42)
        self.gap = int(W * 0.0125)
        self.step = self.tile_h + self.gap
        self.wall_size = (int(W * 1.12), int(H * 1.9))
        mask = rounded_mask((self.tile_w, self.tile_h), int(24 * s))
        self.tiles = []
        for index in range(self.count):
            tile = self.fit(index, (self.tile_w, self.tile_h)).convert("RGBA")
            tile.putalpha(mask)
            self.tiles.append(tile)
        self.rows = self.wall_size[1] // self.step + 3

        base = self.base_color()
        self.ground = Image.new("RGB", self.size, base)
        x_axis, y_axis = axes(self.size)
        fade = smooth((x_axis - 0.4) / 0.25) * 0.985
        edge = smooth((0.14 - x_axis) / 0.14) * 0.3 + smooth((y_axis - 0.8) / 0.2) * 0.35
        alpha = np.clip(1.0 - (1.0 - fade) * (1.0 - edge), 0, 1)
        self.overlay = rgba_from_alpha(base, alpha)

        right = W * 0.955
        width = W * 0.42
        layout = self.layout(width)
        top = (H - layout.height) / 2.0
        self.overlay.alpha_composite(layout.render(
            self.size, right - width, top, align="right",
            main_fill=GHOST_WHITE + (255,), sub_fill=GHOST_WHITE + (225,),
        ))
        rule_w, rule_h = int(W * 0.066), max(2, int(round(9 * s)))
        rule_y = int(top + layout.height + 42 * s)
        ImageDraw.Draw(self.overlay).rounded_rectangle(
            [(int(right) - rule_w, rule_y), (int(right), rule_y + rule_h)], radius=rule_h // 2,
            fill=self.accent + (255,),
        )

    def _column_offset(self, column, phase):
        """每列每隔一段时间滑动一格，奇偶列方向相反；走完一整圈正好回到原位。"""
        current, _following, progress = beats(phase - column / float(self.COLUMNS) * 0.2, self.count, hold=0.6)
        position = current + progress
        return -position if column % 2 == 0 else position

    def frame(self, phase):
        wall = Image.new("RGBA", self.wall_size, (0, 0, 0, 0))
        for column in range(self.COLUMNS):
            offset = self._column_offset(column, phase)
            x_pos = column * (self.tile_w + self.gap) + int(self.W * 0.08)
            # 相邻列错开半张，形成砖墙节奏
            stagger = 0.5 if column % 2 else 0.0
            first = math.floor(-offset - stagger) - 1
            for row in range(self.rows):
                slot = first + row
                y_pos = (slot + offset + stagger) * self.step - self.tile_h * 0.2
                self._place(wall, self.tiles[(slot + column * 3) % self.count], x_pos, int(round(y_pos)))
        turned = wall.rotate(self.ANGLE, resample=Image.Resampling.BICUBIC, expand=False)
        canvas = self.ground.convert("RGBA")
        origin = (int(self.W * 0.3 - self.wall_size[0] / 2.0), int(self.H * 0.5 - self.wall_size[1] / 2.0))
        canvas.alpha_composite(turned.crop((-origin[0], -origin[1], -origin[0] + self.W, -origin[1] + self.H)))
        canvas.alpha_composite(self.overlay)
        return canvas

    @staticmethod
    def _place(wall, tile, x_pos, y_pos):
        """把海报贴到墙上；alpha_composite 不接受负坐标，超出上边缘的部分先裁掉。"""
        if y_pos >= 0:
            wall.alpha_composite(tile, (x_pos, y_pos))
        elif y_pos + tile.height > 0:
            wall.alpha_composite(tile.crop((0, -y_pos, tile.width, tile.height)), (x_pos, 0))


# ---------------------------------------------------------------- 风格 5：瀑布


class Masonry(Cover):
    """错落的圆角拼贴，标题占据中央一格。每次只有一格换图。"""

    # 每列从上到下的格子高度比例，None 表示标题格
    COLUMNS = ((0.4, 0.6), (0.27, None, 0.3), (0.27, None, 0.3), (0.62, 0.38))

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        gap = int(W * 0.0125)
        margin = gap
        column_w = (W - margin * 2 - gap * 3) / 4.0
        usable = H - margin * 2
        radius = int(30 * s)
        base = self.base_color()
        ground = np.full((H, W, 3), base, dtype=np.float32)
        if not self.animated:
            # 静态图加一点细微的颗粒，避免大面积纯黑显得单薄
            ground += np.random.default_rng(1492).normal(0.0, 2.2, (H, W))[..., None]
        canvas = Image.fromarray(np.clip(ground, 0, 255).astype(np.uint8)).convert("RGBA")

        self.cells = []
        text_rows = []
        for column, parts in enumerate(self.COLUMNS):
            cursor = float(margin)
            x0 = margin + column * (column_w + gap)
            filled = sum(part for part in parts if part)
            text_part = 1.0 - filled if None in parts else 0.0
            gaps = (len(parts) - 1) * gap
            for part in parts:
                height = (usable - gaps) * (part if part else text_part)
                box = (int(round(x0)), int(round(cursor)), int(round(x0 + column_w)), int(round(cursor + height)))
                if part:
                    self.cells.append(box)
                else:
                    text_rows.append(box)
                cursor += height + gap

        # 中间两列的标题区合并成一个跨两列的标题格
        left = min(box[0] for box in text_rows)
        right = max(box[2] for box in text_rows)
        top = max(box[1] for box in text_rows)
        bottom = min(box[3] for box in text_rows)
        tile_size = (right - left, bottom - top)
        tile = Image.new("RGBA", tile_size, mix(CARD, self.tint, 0.08 * self.ratio) + (255,))
        tile.alpha_composite(border_layer(tile_size, radius, (255, 255, 255, 18), 2 * s))
        canvas.paste(tile, (left, top), rounded_mask(tile_size, radius))

        layout = self.layout(tile_size[0] * 0.86)
        center_x, center_y = (left + right) / 2.0, (top + bottom) / 2.0
        canvas.alpha_composite(layout.render_centered(
            self.size, center_x, center_y, main_fill=GHOST_WHITE + (255,), sub_fill=(205, 205, 215, 255),
        ))
        if layout.main_lines:
            rule_h = max(2, int(round(8 * s)))
            block_top = center_y - layout.height / 2.0
            if layout.sub_lines:
                rule_y = block_top + (layout.main_height + layout.sub_top) / 2.0 - rule_h / 2.0
            else:
                rule_y = block_top + layout.main_height + 22 * s
            half = min(layout.main_width, tile_size[0] * 0.8) / 2.0
            ImageDraw.Draw(canvas).rounded_rectangle(
                [(int(center_x - half), int(rule_y)), (int(center_x + half), int(rule_y) + rule_h)],
                radius=rule_h // 2, fill=self.accent + (255,),
            )

        self.base = canvas.convert("RGB")
        self.masks = {}
        self.pictures = []
        total = len(self.cells)
        # 每格在两张图之间来回切换：前半圈依次换成 B，后半圈依次换回 A，首尾自然相接
        shift = max(1, min(self.count - 1, total)) if self.count > 1 else 0
        for index, box in enumerate(self.cells):
            size = (box[2] - box[0], box[3] - box[1])
            self.masks[index] = rounded_mask(size, radius)
            first = self.fit(index, size)
            second = self.fit(index + shift, size)
            self.pictures.append((first, second))
        # 换图顺序跳着走，相邻的格子不会连续变化
        self.order = [(index * 3) % total for index in range(total)]

    def frame(self, phase):
        total = len(self.cells)
        canvas = self.base.copy()
        current, _following, progress = beats(phase, total * 2, hold=0.45)
        for rank, cell in enumerate(self.order):
            # 这一格在第 rank 拍换成 B，在第 rank+total 拍换回 A
            if current == rank:
                amount = progress
            elif current == rank + total:
                amount = 1.0 - progress
            elif rank < current < rank + total:
                amount = 1.0
            else:
                amount = 0.0
            first, second = self.pictures[cell]
            if amount <= 0.0:
                picture = first
            elif amount >= 1.0:
                picture = second
            else:
                picture = Image.blend(first, second, amount)
            canvas.paste(picture, self.cells[cell][:2], self.masks[cell])
        return canvas


# ---------------------------------------------------------------- 风格 6：立柱


class Pillars(Cover):
    """左边大标题配竖排副标题，右边高窄海报错落排开，海报整排向左传送。"""

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        self.card_w = int(W * 0.178)
        self.card_h = int(H * 0.84)
        self.gap = int(W * 0.0125)
        self.x0 = W * 0.452
        self.shift = H * 0.035
        radius = int(38 * s)
        self.mask = rounded_mask((self.card_w, self.card_h), radius)
        self.cards = [self.fit(index, (self.card_w, self.card_h)) for index in range(self.count)]

        base = self.base_color()
        self.ground = Image.new("RGB", self.size, base)
        x_axis, _y_axis = axes(self.size)
        alpha = smooth((0.448 - x_axis) / 0.03)
        self.overlay = rgba_from_alpha(base, alpha)

        has_sub = self.has_sub()
        left = W * 0.05
        width = W * (0.335 if has_sub else 0.37)
        main = self.layout(width, part="main")
        top = (H - main.main_height) / 2.0
        self.overlay.alpha_composite(main.render(
            self.size, left, top, main_fill=GHOST_WHITE + (255,), shadow=None,
        ))
        if has_sub:
            sub = self.layout(H * 0.8, part="sub")
            vertical = sub.rotated(fill=self.accent + (255,))
            x_pos = int(left + main.main_width + 24 * s)
            y_pos = int(H / 2.0 - vertical.height / 2.0)
            self.overlay.alpha_composite(vertical, (x_pos, y_pos))
        else:
            rule_h = max(2, int(round(9 * s)))
            rule_y = int(top + main.main_height + 40 * s)
            ImageDraw.Draw(self.overlay).rounded_rectangle(
                [(int(left), rule_y), (int(left + main.main_width), rule_y + rule_h)], radius=rule_h // 2,
                fill=self.accent + (255,),
            )

    def frame(self, phase):
        current, _following, progress = beats(phase, self.count)
        canvas = self.ground.copy()
        step = self.card_w + self.gap
        for slot in range(5):
            position = slot - progress
            x_pos = int(round(self.x0 + position * step))
            if x_pos >= self.W or x_pos + self.card_w <= 0:
                continue
            y_pos = int(round((self.H - self.card_h) / 2.0 + self.shift * math.cos(math.pi * position)))
            canvas.paste(self.cards[(current + slot) % self.count], (x_pos, y_pos), self.mask)
        canvas = canvas.convert("RGBA")
        canvas.alpha_composite(self.overlay)
        return canvas


# ---------------------------------------------------------------- 风格 7：悬卡


class HeroCard(Cover):
    """霓虹底上的玻璃侧栏和悬浮大卡。大卡轮播，侧栏同步向上滚动一格。"""

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        margin_x, margin_y = int(W * 0.018), int(H * 0.04)
        radius = int(44 * s)
        ground = neon_base(self.size, 0.7)

        side_w = int(W * 0.178)
        side_h = H - margin_y * 2
        self.side_box = (margin_x, margin_y)
        self.side_size = (side_w, side_h)
        self.side_mask = rounded_mask(self.side_size, radius)
        frost = ground.crop((margin_x, margin_y, margin_x + side_w, margin_y + side_h)).filter(
            ImageFilter.GaussianBlur(max(2.0, self.blur_size * 0.3 * s)))
        frost.alpha_composite(Image.new("RGBA", self.side_size, (255, 255, 255, 26)))
        frost.alpha_composite(border_layer(self.side_size, radius, (255, 255, 255, 46), 2 * s))
        ground.paste(frost, self.side_box, self.side_mask)

        inner = int(W * 0.0105)
        self.thumb_w = side_w - inner * 2
        self.thumb_h = int(H * 0.262)
        self.thumb_gap = int(H * 0.018)
        self.thumb_x = inner
        self.thumb_mask = rounded_mask((self.thumb_w, self.thumb_h), int(28 * s))
        self.thumbs = [self.fit(index, (self.thumb_w, self.thumb_h)).convert("RGBA") for index in range(self.count)]
        for thumb in self.thumbs:
            thumb.putalpha(self.thumb_mask)
        self.frost = frost.convert("RGB")

        hero_x = margin_x + side_w + int(W * 0.0125)
        self.hero_box = (hero_x, margin_y)
        self.hero_size = (W - margin_x - hero_x, H - margin_y * 2)
        self.hero_mask = rounded_mask(self.hero_size, radius)
        self.heroes = [ImageEnhance.Contrast(self.fit(index, self.hero_size)).enhance(1.04) for index in range(self.count)]
        hero_box_full = (hero_x, margin_y, W - margin_x, H - margin_y)
        ground.alpha_composite(shadow_layer(self.size, hero_box_full, radius, 30 * s, 0.6, 10 * s))
        self.ground = ground.convert("RGB")

        # 大卡内部的压暗和文字
        x_axis, y_axis = axes(self.hero_size)
        strength = self.scrim_strength()
        bottom = smooth((y_axis - 0.38) / 0.62) * 0.94
        side = smooth((0.66 - x_axis) / 0.66) * 0.42
        alpha = np.clip(1.0 - (1.0 - bottom) * (1.0 - side), 0, 1) * strength
        panel = rgba_from_alpha((12, 8, 18), alpha)

        pad = self.hero_size[0] * 0.052
        main = self.layout(self.hero_size[0] * 0.84, part="main")
        pill_h = int(118 * s)
        has_sub = self.has_sub()
        floor = self.hero_size[1] - self.hero_size[1] * 0.085
        pill_top = floor - pill_h if has_sub else floor
        main_top = pill_top - self.spec.spacing * s - main.main_height if has_sub else floor - main.main_height
        panel.alpha_composite(main.render(
            self.hero_size, pad, main_top, main_fill=(255, 255, 255, 255), shadow=self.text_shadow(150),
        ))
        if has_sub:
            panel.alpha_composite(self._pill(int(pad), int(pill_top), pill_h, self.hero_size[0] * 0.8), (0, 0))
        panel.alpha_composite(border_layer(self.hero_size, radius, (255, 255, 255, 30), 2 * s))
        self.panel = panel

    def _pill(self, x_pos, y_pos, height, max_width):
        """胶囊标签：播放三角加副标题。"""
        layer = Image.new("RGBA", self.hero_size, (0, 0, 0, 0))
        sub = self.layout(max_width - height * 1.3, part="sub")
        icon_box = height * 0.92
        width = int(sub.sub_width + icon_box + height * 0.5)
        pill = Image.new("RGBA", (width, height), self.accent + (255,))
        scale = 4
        icon = Image.new("RGBA", (int(icon_box) * scale, height * scale), (0, 0, 0, 0))
        play_triangle(ImageDraw.Draw(icon), (icon_box * 0.58 * scale, height * 0.5 * scale), height * 0.19 * scale,
                      (255, 255, 255, 255))
        pill.alpha_composite(icon.resize((int(icon_box), height), Image.Resampling.LANCZOS))
        text = sub.render((width, height), icon_box, (height - sub.height) / 2.0, sub_fill=(255, 255, 255, 255))
        pill.alpha_composite(text)
        layer.paste(pill, (x_pos, y_pos), rounded_mask((width, height), height // 2))
        return layer

    def _side(self, current, progress):
        panel = self.frost.copy()
        step = self.thumb_h + self.thumb_gap
        start = -int(round(step * (0.42 + progress)))
        # 大卡正在显示的图不出现在侧栏，侧栏从下一张开始
        for slot in range(5):
            y_pos = start + slot * step
            if y_pos >= self.side_size[1] or y_pos + self.thumb_h <= 0:
                continue
            thumb = self.thumbs[(current + 1 + slot) % self.count]
            panel.paste(thumb.convert("RGB"), (self.thumb_x, y_pos), self.thumb_mask)
        return panel

    def frame(self, phase):
        current, following, progress = beats(phase, self.count)
        canvas = self.ground.copy()
        canvas.paste(self._side(current, progress), self.side_box, self.side_mask)
        hero = self.heroes[current] if progress <= 0 else Image.blend(self.heroes[current], self.heroes[following], progress)
        hero = hero.convert("RGBA")
        hero.alpha_composite(self.panel)
        canvas.paste(hero.convert("RGB"), self.hero_box, self.hero_mask)
        return canvas


# ---------------------------------------------------------------- 风格 8：霓虹


class Neon(Cover):
    """霓虹几何背景上一排海报，红色竖条领起标题。三角缓慢转动，海报整排传送。"""

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        self.ground = neon_base(self.size).convert("RGB")
        self.card_w = int(W * 0.2)
        self.card_h = int(self.card_w * 1.42)
        self.gap = int(W * 0.0145)
        self.row_x = W * 0.036
        self.row_y = int(H * 0.47)
        radius = int(34 * s)
        self.mask = rounded_mask((self.card_w, self.card_h), radius)
        # 海报底部压暗，下缘融进背景
        _x_axis, y_axis = axes((self.card_w, self.card_h))
        shade = rgba_from_alpha((6, 6, 14), smooth((y_axis - 0.45) / 0.55) * 0.5)
        border = border_layer((self.card_w, self.card_h), radius, (255, 255, 255, 40), 2 * s)
        self.cards = []
        for index in range(self.count):
            card = self.fit(index, (self.card_w, self.card_h)).convert("RGBA")
            card.alpha_composite(shade)
            card.alpha_composite(border)
            self.cards.append(card.convert("RGB"))

        self.overlay = Image.new("RGBA", self.size, (0, 0, 0, 0))
        bar_x = int(W * 0.036)
        bar_w = max(3, int(round(12 * s)))
        text_x = bar_x + bar_w + int(W * 0.0135)
        main = self.layout(W * 0.8, part="main")
        sub = self.layout(W * 0.8, part="sub")
        block_top = int(H * 0.075)
        # 长标题会碰到霓虹三角，文字背后压暗一块，三角从这里开始渐隐
        x_axis, y_axis = axes(self.size)
        reach = (text_x + max(main.main_width, sub.sub_width)) / float(W)
        behind = smooth((reach + 0.1 - x_axis) / 0.14) * smooth((0.48 - y_axis) / 0.12) * 0.86
        self.overlay.alpha_composite(rgba_from_alpha((5, 5, 14), behind))
        self.overlay.alpha_composite(main.render(
            self.size, text_x, block_top, main_fill=(255, 255, 255, 255), shadow=self.text_shadow(170),
        ))
        block_bottom = block_top + main.main_height
        if self.has_sub():
            sub_y = block_bottom + self.spec.spacing * s
            self.overlay.alpha_composite(sub.render(
                self.size, text_x, sub_y, sub_fill=(232, 232, 245, 255), shadow=self.text_shadow(170),
            ))
            block_bottom = sub_y + sub.height
        # 标题块过高时海报整排下移，不和文字重叠
        limit = int(block_bottom + 40 * s)
        if limit > self.row_y:
            self.row_y = min(limit, int(H * 0.68))
        ImageDraw.Draw(self.overlay).rounded_rectangle(
            [(bar_x, block_top), (bar_x + bar_w, int(block_bottom))], radius=bar_w // 2,
            fill=self.accent + (255,),
        )

    def frame(self, phase):
        canvas = self.ground.convert("RGBA")
        canvas.alpha_composite(neon_triangles(self.size, phase))
        current, _following, progress = beats(phase, self.count, hold=0.76)
        step = self.card_w + self.gap
        canvas = canvas.convert("RGB")
        for slot in range(6):
            x_pos = int(round(self.row_x + (slot - progress) * step))
            if x_pos >= self.W or x_pos + self.card_w <= 0:
                continue
            canvas.paste(self.cards[(current + slot) % self.count], (x_pos, self.row_y), self.mask)
        canvas = canvas.convert("RGBA")
        canvas.alpha_composite(self.overlay)
        return canvas


DESIGNS = {1: Slats, 2: Titan, 3: Cutout, 4: Tilt, 5: Masonry, 6: Pillars, 7: HeroCard, 8: Neon}


# ---------------------------------------------------------------- 输出


def encode_jpeg(image, quality=92):
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality, optimize=True, subsampling=0)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def encode_gif(render_frame, duration, fps, reduce_colors, stop_event):
    """逐帧渲染并调用 ffmpeg 合成 GIF，stop_event 触发时立即中止并返回 False。"""
    safe_fps = max(1, int(fps))
    total = max(2, int(round(safe_fps * max(1, float(duration)))))
    colors, dither = {
        "off": ("256", "bayer:bayer_scale=4"),
        "medium": ("192", "bayer:bayer_scale=3"),
        "strong": ("96", "bayer:bayer_scale=2"),
    }.get(reduce_colors, ("192", "bayer:bayer_scale=3"))

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        logger.info(f"开始生成动画帧，共 {total} 帧")
        for index in range(total):
            if stop_event and stop_event.is_set():
                return False
            render_frame(index / float(total)).convert("RGB").save(temp_path / f"frame_{index:04d}.bmp", format="BMP")

        output = temp_path / "output.gif"
        # 有序抖动不随帧变化，静止区域的像素完全相同，GIF 只记录真正变化的部分
        command = [
            "ffmpeg", "-hide_banner", "-y", "-framerate", str(safe_fps),
            "-i", str(temp_path / "frame_%04d.bmp"), "-threads", "0",
            "-filter_complex",
            f"[0:v] split [a][b]; [a] palettegen=max_colors={colors}:stats_mode=full [p]; "
            f"[b][p] paletteuse=dither={dither}:diff_mode=rectangle",
            "-loop", "0", "-f", "gif", str(output),
        ]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        while process.poll() is None:
            if stop_event and stop_event.is_set():
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
                return False
            time.sleep(0.05)
        if process.returncode != 0:
            detail = process.stderr.read().decode("utf-8", "ignore")[-400:] if process.stderr else ""
            raise RuntimeError(f"ffmpeg 退出码 {process.returncode}: {detail}")
        data = output.read_bytes()
        logger.info(f"动图生成完成，{total} 帧，{len(data) / 1024 / 1024:.1f} MB")
        return base64.b64encode(data).decode("utf-8")


def _accent_and_tint(image, info, config):
    """点缀色：默认用风格自带的颜色，也可以从图片提取或由用户指定。底色色调总是取自图片。"""
    config = config or {}
    mode = config.get("mode", "auto")
    tint = (120, 110, 100)
    try:
        tint = tuple(int(value) for value in ColorHelper.get_background_color(image, color_mode="auto")[:3])
    except Exception as error:
        logger.debug(f"提取主色失败，使用默认色: {error}")

    accent = info.accent
    if mode == "image":
        hue, saturation, _value = ColorHelper.rgb_to_hsv(tint)
        accent = ColorHelper.hsv_to_rgb(hue, min(0.72, max(0.45, saturation)), 0.95)
    elif mode in ("custom", "config"):
        try:
            accent = tuple(int(value) for value in ColorHelper.get_background_color(
                image, color_mode=mode, custom_color=config.get("custom_color"),
                config_color=config.get("config_color"),
            )[:3])
        except Exception as error:
            logger.debug(f"读取自定义颜色失败，使用风格默认色: {error}")
    return accent, tint


def create_cover(index, animated, library_dir, title, fonts, spec, size, image_count=None,
                 blur_size=50, color_ratio=0.8, bg_color_config=None,
                 animation_duration=12, animation_fps=15, animation_reduce_colors="medium", stop_event=None):
    """生成封面，返回图片的 base64：静态为 JPEG，动态为 GIF。失败时返回 False。"""
    info = style_info(index)
    try:
        count = max(info.slots, int(image_count or 0)) if animated else info.slots
        images = load_images(library_dir, count)
        if not images:
            logger.warning(f"风格「{info.name}」没有找到可用的素材")
            return False
        # 原图比画布大太多只会拖慢速度
        limit = int(max(size) * 1.6)
        for image in images:
            image.thumbnail((limit, limit), Image.Resampling.LANCZOS)
        accent, tint = _accent_and_tint(images[0], info, bg_color_config)
        cover = DESIGNS[info.index](size, images, title, fonts, spec, accent, tint, blur_size, color_ratio, animated)
        if not animated:
            return encode_jpeg(cover.frame(0.0))
        return encode_gif(cover.frame, animation_duration, animation_fps, animation_reduce_colors, stop_event)
    except Exception as error:
        logger.error(f"生成风格「{info.name}」失败: {error}", exc_info=True)
        return False
