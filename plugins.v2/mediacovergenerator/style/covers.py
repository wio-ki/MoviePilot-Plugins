"""
八款封面风格的构图。每款有自己的切换方式，切换也是设计的一部分，不共用同一种转场。

每款风格是一个 Cover 子类：prepare() 预先算好不变的图层，frame(phase) 画出循环中某一时刻的画面。
静态封面就是 phase=0 的高清帧，动图是同一套构图的低分辨率逐帧输出，所以两者外观一致。

动图的几条约定：
- 输出 GIF，这是各家 Emby 客户端都能显示的格式
- phase=0 就是完整封面，只显示首帧的客户端看到的也是成品
- 大部分时间画面静止、只在转场时运动。GIF 按帧差压缩，这样体积最小，文字也不会闪
"""
import base64
import math
import subprocess
import tempfile
import time
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from app.log import logger
from app.plugins.mediacovergenerator.style.catalog import style_info
from app.plugins.mediacovergenerator.style.typography import TitleLayout, outline_from_mask
from app.plugins.mediacovergenerator.utils.color_helper import ColorHelper

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
GHOST_WHITE = (248, 248, 255)
SILVER = (198, 203, 210)
INK = (15, 15, 17)
CARD = (27, 27, 29)


# ---------------------------------------------------------------- 时间轴


def ease(value):
    value = max(0.0, min(1.0, value))
    return 0.5 - 0.5 * math.cos(math.pi * value)


def ease_in_out(value):
    """起步和收尾都更缓，大幅度的运动用它。"""
    value = max(0.0, min(1.0, value))
    return 4.0 * value ** 3 if value < 0.5 else 1.0 - (-2.0 * value + 2.0) ** 3 / 2.0


def ease_out(value):
    """快速出发、慢慢停稳。"""
    value = max(0.0, min(1.0, value))
    return 1.0 - (1.0 - value) ** 3


def ease_back(value, overshoot=1.1):
    """略微冲过头再回来，带一点弹性。"""
    value = max(0.0, min(1.0, value)) - 1.0
    return 1.0 + (overshoot + 1.0) * value ** 3 + overshoot * value ** 2


def beats(phase, count, hold=0.72, curve=ease):
    """把循环分成 count 拍，每拍先停留再转场。返回 (当前拍, 下一拍, 转场进度)。"""
    position = (phase % 1.0) * count
    current = int(position) % count
    local = position - int(position)
    progress = curve((local - hold) / (1.0 - hold)) if local > hold else 0.0
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


def polygon_mask(size, points, supersample=3):
    """抗锯齿的多边形遮罩，顶点用画布坐标。"""
    mask = Image.new("L", (size[0] * supersample, size[1] * supersample), 0)
    ImageDraw.Draw(mask).polygon([(x * supersample, y * supersample) for x, y in points], fill=255)
    return mask.resize(size, Image.Resampling.LANCZOS)


def zoomed(image, factor):
    """以中心放大后裁回原尺寸。"""
    width, height = image.size
    crop_w, crop_h = width / factor, height / factor
    left, top = (width - crop_w) / 2.0, (height - crop_h) / 2.0
    return image.resize((width, height), Image.Resampling.BILINEAR, box=(left, top, left + crop_w, top + crop_h))


def with_alpha(layer, amount):
    """整体调整图层的透明度。"""
    if amount >= 1.0:
        return layer
    result = layer.copy()
    result.putalpha(layer.getchannel("A").point(lambda value: int(value * max(0.0, amount))))
    return result


def perspective_coefficients(target, width, height):
    """PIL 透视变换的系数：把输出坐标映射回源图。target 是四个角的落点，顺序为左上、右上、右下、左下。"""
    source = ((0, 0), (width, 0), (width, height), (0, height))
    matrix, values = [], []
    for (x_pos, y_pos), (u_pos, v_pos) in zip(target, source):
        matrix.append([x_pos, y_pos, 1, 0, 0, 0, -u_pos * x_pos, -u_pos * y_pos])
        matrix.append([0, 0, 0, x_pos, y_pos, 1, -v_pos * x_pos, -v_pos * y_pos])
        values += [u_pos, v_pos]
    return np.linalg.solve(np.array(matrix, dtype=np.float64), np.array(values, dtype=np.float64)).tolist()


def warp_onto(canvas, card, quad, supersample=2):
    """把 RGBA 卡片按四个角的落点贴到画布上，超采样让斜边平滑。"""
    x0 = max(0, int(math.floor(min(point[0] for point in quad))))
    y0 = max(0, int(math.floor(min(point[1] for point in quad))))
    x1 = min(canvas.width, int(math.ceil(max(point[0] for point in quad))))
    y1 = min(canvas.height, int(math.ceil(max(point[1] for point in quad))))
    if x1 <= x0 or y1 <= y0:
        return
    local = [((x_pos - x0) * supersample, (y_pos - y0) * supersample) for x_pos, y_pos in quad]
    coefficients = perspective_coefficients(local, card.width, card.height)
    warped = card.transform(((x1 - x0) * supersample, (y1 - y0) * supersample), Image.Transform.PERSPECTIVE,
                            coefficients, Image.Resampling.BILINEAR)
    canvas.alpha_composite(warped.resize((x1 - x0, y1 - y0), Image.Resampling.BOX), (x0, y0))


def composite_at(canvas, layer, x_pos, y_pos):
    """alpha_composite 不接受负坐标，超出画布左上方的部分先裁掉。"""
    x_pos, y_pos = int(round(x_pos)), int(round(y_pos))
    crop_x, crop_y = max(0, -x_pos), max(0, -y_pos)
    if crop_x >= layer.width or crop_y >= layer.height:
        return
    if crop_x or crop_y:
        layer = layer.crop((crop_x, crop_y, layer.width, layer.height))
    canvas.alpha_composite(layer, (max(0, x_pos), max(0, y_pos)))


def play_chip(height, width=None, fill=GHOST_WHITE, icon=CARD):
    """白底圆角的播放键。"""
    scale = 4
    width = int(width or height * 1.5)
    height = int(height)
    chip = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(chip)
    draw.rounded_rectangle([(0, 0), (width * scale - 1, height * scale - 1)], radius=int(height * scale * 0.28),
                           fill=fill + (255,))
    play_triangle(draw, (width * scale * 0.51, height * scale * 0.5), height * scale * 0.2, icon + (255,))
    return chip.resize((width, height), Image.Resampling.LANCZOS)


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
        return mix(INK, self.tint, 0.05 * self.ratio)

    def card_color(self):
        return mix(CARD, self.tint, 0.05 * self.ratio)

    def text_shadow(self, alpha=120):
        return (0, 0, 0, alpha), self.H * 0.012, self.H * 0.004

    def has_sub(self):
        return bool(str(self.title[1] or "").strip())

    def scrim_strength(self):
        """「颜色混合占比」控制压暗的力度，默认 0.8 对应设计强度。"""
        return 0.72 + 0.35 * self.ratio

    def backdrop(self, index, brightness):
        """虚化压暗的背景图。「玻璃模糊强度」控制虚化的程度。"""
        radius = max(2.0, self.blur_size * 0.9 * self.s)
        blurred = self.fit(index, self.size).filter(ImageFilter.GaussianBlur(radius))
        return ImageEnhance.Brightness(blurred).enhance(brightness)

    def label(self, style="outline", max_width=None, fill=None, ink=None):
        """
        副标题做成的小标签：outline 是细描边，solid 是实心底。
        没有副标题时返回 None。文字位置不受「标题上下偏移」影响，偏移由整块标题统一处理。
        """
        if not self.has_sub():
            return None
        sub = self.layout(max_width or self.W * 0.6, part="sub")
        font_size = self.spec.sub_size * self.s
        pad_x, pad_y = font_size * 0.52, font_size * 0.36
        width, height = int(sub.sub_width + pad_x * 2), int(sub.height + pad_y * 2)
        radius = max(2, int(height * 0.26))
        color = fill or GHOST_WHITE
        text_color = ink or color
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        if style == "solid":
            layer.paste(color + (255,), (0, 0), rounded_mask((width, height), radius))
            text_color = ink or CARD
        else:
            layer.alpha_composite(border_layer((width, height), radius, color + (215,), max(1.0, 3 * self.s)))
        layer.alpha_composite(sub.render((width, height), pad_x, pad_y - sub.offset_y, sub_fill=text_color + (255,)))
        return layer

    def title_block(self, max_width, sub_style="tag", button=False, main_fill=GHOST_WHITE, sub_fill=None, shadow=None,
                    main_factor=1.0, align="left"):
        """
        主标题加一行标签或副标题，返回 (RGBA 图层, 笔画左边距, 笔画高度)。
        图层四周留了边，投影不会被切掉；上下偏移由调用方加上。
        """
        s = self.s
        margin = int(60 * s)
        main = self.layout(max_width, part="main", size_factor=main_factor)
        gap = self.spec.spacing * s if main.main_lines else 0.0
        chip_h = int(self.spec.sub_size * s * 1.72)

        items = []
        tag = self.label(fill=sub_fill or GHOST_WHITE, max_width=max_width) if sub_style == "tag" else None
        if tag is not None:
            chip_h = tag.height
        if button:
            items.append(play_chip(chip_h))
        if tag is not None:
            items.append(tag)
        text = self.layout(max_width, part="sub") if sub_style == "text" and self.has_sub() else None

        row_h = max([item.height for item in items] + [int(text.height) if text else 0] + [0])
        ink_h = main.main_height + (gap + row_h if row_h else 0.0)
        width = int(max_width) + margin * 2
        height = int(ink_h) + margin * 2
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        if main.main_lines:
            layer.alpha_composite(main.render((width, height), margin, margin - main.offset_y, align=align,
                                              main_fill=main_fill + (255,), shadow=shadow))
        row_y = margin + main.main_height + gap
        cursor = float(margin)
        if align == "center" and items:
            total = sum(item.width for item in items) + 18 * s * (len(items) - 1)
            cursor += (max_width - total) / 2.0
        for item in items:
            layer.alpha_composite(item, (int(cursor), int(row_y + (row_h - item.height) / 2.0)))
            cursor += item.width + 18 * s
        if text:
            color = sub_fill or SILVER
            layer.alpha_composite(text.render((width, height), cursor, row_y - text.offset_y, align=align,
                                              sub_fill=color + (255,), shadow=shadow))
        return layer, margin, ink_h


# ---------------------------------------------------------------- 风格 1：序幕


class Gallery(Cover):
    """满屏竖幅画廊，一幅展开。画幅舒展的同时，标题淡出再从新画幅里升起。"""

    STRIPS = 6
    START = 2

    def prepare(self):
        s = self.s
        self.active_w = int(self.W * 0.34)
        self.rest_w = (self.W - self.active_w) / float(self.STRIPS - 1)
        self.bright = [ImageEnhance.Contrast(self.fit(index, (self.active_w, self.H))).enhance(1.04)
                       for index in range(self.STRIPS)]
        self.dim = [ImageEnhance.Brightness(panel).enhance(0.84) for panel in self.bright]

        _x_axis, y_axis = axes(self.size)
        alpha = smooth((y_axis - 0.4) / 0.6) * 0.98 * self.scrim_strength()
        self.overlay = rgba_from_alpha((4, 4, 6), np.clip(alpha, 0, 1))

        self.block, self.margin, ink_h = self.title_block(
            self.W * 0.62, button=True, shadow=self.text_shadow(110))
        self.block_y = int(self.H * 0.925 - ink_h - self.margin + self.spec.offset_y * s)
        self.pad = int(self.W * 0.024)
        box = self.block.getbbox() or (0, 0, 1, 1)
        self.block_right = box[2]

    def _panel_left(self, strip):
        """这一幅完全展开时的左边缘。"""
        return strip * self.rest_w

    def _block_x(self, strip):
        limit = self.W - self.block_right - self.W * 0.024
        return min(self._panel_left(strip) + self.pad - self.margin, limit)

    def frame(self, phase):
        current, following, progress = beats(phase, self.STRIPS, hold=0.6, curve=ease_in_out)
        active = (self.START + current) % self.STRIPS
        upcoming = (self.START + following) % self.STRIPS
        canvas = Image.new("RGB", self.size, (0, 0, 0))
        cursor = 0.0
        for index in range(self.STRIPS):
            amount = (1.0 - progress if index == active else 0.0) + (progress if index == upcoming else 0.0)
            width = self.rest_w + (self.active_w - self.rest_w) * amount
            x0, x1 = int(round(cursor)), int(round(cursor + width))
            cursor += width
            shown = max(1, x1 - x0)
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
        # 标题不横穿画面：先淡出，再在新展开的画幅里淡入并轻轻升起
        if progress < 0.42:
            amount, strip, rise = 1.0 - progress / 0.42, active, 0.0
        elif progress > 0.58:
            amount = (progress - 0.58) / 0.42
            strip, rise = upcoming, (1.0 - amount) * 34 * self.s
        else:
            return canvas
        composite_at(canvas, with_alpha(self.block, amount), self._block_x(strip), self.block_y + rise)
        return canvas


# ---------------------------------------------------------------- 风格 2：巨幕


class Titan(Cover):
    """满幅大图、粗宋体片名，右下角的玻璃小窗预告下一张。"""

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        self.frames = [ImageEnhance.Contrast(self.fit(index, self.size)).enhance(1.05) for index in range(self.count)]
        x_axis, y_axis = axes(self.size)
        strength = self.scrim_strength()
        side = smooth((0.62 - x_axis) / 0.62) * 0.72
        bottom = smooth((y_axis - 0.38) / 0.62) * 0.92
        alpha = 1.0 - (1.0 - side) * (1.0 - bottom)
        self.overlay = rgba_from_alpha((6, 6, 8), np.clip(alpha * strength, 0, 1))

        card_w, card_h = int(W * 0.2), int(H * 0.235)
        self.card_box = (W - int(W * 0.036) - card_w, H - int(H * 0.075) - card_h)
        radius = int(32 * s)
        self.card_size = (card_w, card_h)
        self.card_mask = rounded_mask(self.card_size, radius)
        badge = play_badge(card_h * 0.4)
        self.cards = []
        for index in range(self.count):
            card = ImageEnhance.Brightness(self.fit(index, self.card_size)).enhance(0.74).convert("RGBA")
            card.alpha_composite(badge, ((card_w - badge.width) // 2, (card_h - badge.height) // 2))
            card.alpha_composite(border_layer(self.card_size, radius, (255, 255, 255, 80), 2 * s))
            self.cards.append(card.convert("RGB"))
        box = (*self.card_box, self.card_box[0] + card_w, self.card_box[1] + card_h)
        self.overlay.alpha_composite(shadow_layer(self.size, box, radius, 26 * s, 0.55, 10 * s))

        block, margin, ink_h = self.title_block(W * 0.68, button=True, shadow=self.text_shadow(150))
        composite_at(self.overlay, block, W * 0.046 - margin,
                     H * 0.915 - ink_h - margin + self.spec.offset_y * s)

    def frame(self, phase):
        current, following, progress = beats(phase, self.count, hold=0.58, curve=ease_in_out)
        image = self.frames[current]
        if progress > 0:
            # 旧画面继续推近，新画面从稍近处退回原位，两者叠化
            image = Image.blend(zoomed(image, 1.0 + 0.09 * progress),
                                zoomed(self.frames[following], 1.09 - 0.09 * progress), progress)
        canvas = image.convert("RGBA")
        canvas.alpha_composite(self.overlay)
        card_now, card_next = self.cards[following], self.cards[(following + 1) % self.count]
        if progress <= 0:
            card = card_now
        else:
            # 小窗里下一张从下方推上来
            shift = int(round(self.card_size[1] * progress))
            card = Image.new("RGB", self.card_size)
            card.paste(card_now, (0, -shift))
            card.paste(card_next, (0, self.card_size[1] - shift))
        canvas.paste(card, self.card_box, self.card_mask)
        return canvas


# ---------------------------------------------------------------- 风格 3：流光


class Glint(Cover):
    """
    整幅画面压暗虚化，巨大的标题像窗口一样透出明亮清晰的同一张图，字和背景严丝合缝。
    停留时一道流光扫过文字；转场时字里先换成下一张，镜头穿过笔画冲进画面，全屏亮相后再退回来。
    """

    BEATS = 4
    HOLD = 0.5
    SWEEP_WIDTH = 0.11

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        self.beat_count = max(1, min(self.BEATS, self.count))
        blur = ImageFilter.GaussianBlur(max(1.0, self.blur_size * 0.16 * s))
        x_axis, y_axis = axes(self.size)
        vignette = np.sqrt(((x_axis - 0.5) / 0.72) ** 2 + ((y_axis - 0.5) / 0.72) ** 2)
        shade = rgba_from_alpha((3, 3, 6), np.clip(0.22 + vignette * 0.44, 0, 1) * self.scrim_strength())

        main = self.layout(W * 0.88, part="main")
        tag = self.label(fill=GHOST_WHITE)
        gap = self.spec.spacing * s if tag is not None else 0.0
        total = main.main_height + (gap + tag.height if tag is not None else 0.0)
        top = (H - total) / 2.0 + self.spec.offset_y * s
        # 文字蒙版画大一倍，推近时边缘才不糊
        self.mask_scale = 2
        large = (W * self.mask_scale, H * self.mask_scale)
        big = TitleLayout(self.title, self.fonts, self.spec, self.s * self.mask_scale, W * 0.88 * self.mask_scale, part="main")
        self.mask_large = big.render(large, W * 0.06 * self.mask_scale, (top - main.offset_y) * self.mask_scale, align="center").getchannel("A")
        self.mask = self.mask_large.resize(self.size, Image.Resampling.LANCZOS)
        self.box = self.mask.getbbox() or (int(W * 0.2), int(H * 0.3), int(W * 0.8), int(H * 0.7))

        # 字外缘的亮边和字后的暗影，让字从画面里浮出来
        self.edge = Image.new("RGBA", self.size, GHOST_WHITE + (0,))
        self.edge.putalpha(outline_from_mask(self.mask, max(1, round(2.2 * s)), 0.82))
        glow = self.mask.filter(ImageFilter.GaussianBlur(max(2.0, 44 * s))).point(lambda value: int(min(255, value * 1.2)))
        soften = ImageFilter.GaussianBlur(max(0.5, 1.6 * s))

        self.full, self.bright, self.dim, self.plain = [], [], [], []
        for index in range(self.beat_count):
            picture = ImageEnhance.Contrast(self.fit(index, self.size)).enhance(1.06)
            self.full.append(picture)
            # 字里的画面提亮、提纯并去掉碎细节，字形始终清楚
            stretched = ImageOps.autocontrast(picture.filter(soften), cutoff=2)
            self.bright.append(ImageEnhance.Brightness(lift(stretched, floor=74, saturation=1.55)).enhance(1.12))
            dark = ImageEnhance.Brightness(ImageEnhance.Color(picture.filter(blur)).enhance(0.8)).enhance(0.36).convert("RGBA")
            dark.alpha_composite(shade)
            dark = dark.convert("RGB")
            self.plain.append(dark)
            # 字后面透出一层取自这张图色调的背光
            lit = dark.copy()
            lit.paste(self._glow_color(picture), (0, 0), glow.point(lambda value: int(value * 0.62)))
            self.dim.append(lit)

        self.tag = tag
        self.tag_at = None
        if tag is not None:
            self.tag_at = (int((W - tag.width) / 2.0), int(top + main.main_height + gap))
        self.focus = self._thickest_point()

        columns, rows = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
        self.columns, self.rows = columns, rows

    @staticmethod
    def _glow_color(picture):
        """这张图最鲜艳的颜色，提亮后做背光。"""
        small = picture.convert("HSV").resize((32, 18), Image.Resampling.BILINEAR)
        data = np.asarray(small, dtype=np.float32).reshape(-1, 3)
        weights = (data[:, 1] / 255.0) * (data[:, 2] / 255.0) + 0.01
        hue_x = np.sum(np.cos(data[:, 0] / 255.0 * math.tau) * weights)
        hue_y = np.sum(np.sin(data[:, 0] / 255.0 * math.tau) * weights)
        hue = (math.atan2(hue_y, hue_x) / math.tau) % 1.0
        swatch = Image.new("HSV", (1, 1), (int(hue * 255), 190, 215)).convert("RGB")
        return swatch.getpixel((0, 0))

    def _thickest_point(self):
        """找到笔画最粗的地方，镜头从这里穿过去。同时算出穿透需要的放大倍数。"""
        probe = self.mask.point(lambda value: 255 if value > 127 else 0)
        radius = 0
        current = probe
        while radius < 200:
            shrunk = current.filter(ImageFilter.MinFilter(3))
            if not shrunk.getbbox():
                break
            current = shrunk
            radius += 1
        rows, columns = np.nonzero(np.asarray(current))
        if len(rows):
            pick = len(rows) // 2
            center = (float(columns[pick]), float(rows[pick]))
        else:
            center = ((self.box[0] + self.box[2]) / 2.0, (self.box[1] + self.box[3]) / 2.0)
        diagonal = math.hypot(self.W, self.H)
        self.zoom_peak = min(90.0, diagonal / max(1.0, radius + 1.0))
        return center

    def _zoomed_mask(self, zoom, shift):
        """把文字蒙版以 focus 为中心放大，并让 focus 向画面中心移动 shift（0~1）。"""
        focus_x, focus_y = self.focus
        target_x = focus_x + (self.W / 2.0 - focus_x) * shift
        target_y = focus_y + (self.H / 2.0 - focus_y) * shift
        unit = self.mask_scale / zoom
        coefficients = (unit, 0, (focus_x - target_x / zoom) * self.mask_scale, 0, unit, (focus_y - target_y / zoom) * self.mask_scale)
        layer = self.mask_large.transform(self.size, Image.Transform.AFFINE, coefficients, Image.Resampling.BICUBIC)
        # 放大后边缘会变软，收紧一下
        gain = min(6.0, 1.0 + zoom * 0.25)
        return layer.point(lambda value: int(max(0, min(255, (value - 128) * gain + 128))))

    def _sweep(self, canvas, position):
        """沿斜线扫过文字的一道高光，只落在字上。"""
        left, top, right, bottom = self.box
        width = right - left
        center = left + (position * 1.5 - 0.25) * width
        slanted = self.columns[top:bottom, left:right] + (self.rows[top:bottom, left:right] - top) * 0.32
        band = np.exp(-(((slanted - center) / max(1.0, width * self.SWEEP_WIDTH)) ** 2))
        strength = np.asarray(self.mask.crop(self.box), dtype=np.float32) * band * 0.62
        canvas.paste(GHOST_WHITE, (left, top), Image.fromarray(strength.astype(np.uint8)))

    def frame(self, phase):
        position = (phase % 1.0) * self.beat_count
        current = int(position) % self.beat_count
        following = (current + 1) % self.beat_count
        local = position - int(position)

        if local <= self.HOLD:
            canvas = self.dim[current].copy()
            canvas.paste(self.bright[current], (0, 0), self.mask)
            if 0.08 < local / self.HOLD < 0.82:
                self._sweep(canvas, (local / self.HOLD - 0.08) / 0.74)
            canvas = canvas.convert("RGBA")
            canvas.alpha_composite(self.edge)
            if self.tag is not None:
                canvas.alpha_composite(self.tag, self.tag_at)
            return canvas

        raw = (local - self.HOLD) / (1.0 - self.HOLD)
        peak = math.log(max(1.01, self.zoom_peak))
        tag_alpha = 0.0
        if raw < 0.16:
            # 第一段：字里换成下一张，字不动
            mix_amount = ease(raw / 0.16)
            ground, inside = self.dim[current], Image.blend(self.bright[current], self.bright[following], mix_amount)
            zoom, shift, reveal, tag_alpha = 1.0, 0.0, 0.0, 1.0
        elif raw < 0.46:
            # 第二段：镜头穿过笔画，冲进下一张
            move = ease_in_out((raw - 0.16) / 0.3)
            ground, inside = Image.blend(self.dim[current], self.plain[current], min(1.0, move * 4.0)), self.bright[following]
            zoom, shift, reveal = math.exp(peak * move), ease(move * 1.3), move
            tag_alpha = max(0.0, 1.0 - move * 5.0)
        elif raw < 0.7:
            # 第三段：全屏亮相
            return self.full[following].copy()
        else:
            # 第四段：退回来，背景也换成下一张
            move = ease_out((raw - 0.7) / 0.3)
            ground, inside = Image.blend(self.plain[following], self.dim[following], max(0.0, move * 2.0 - 1.0)), self.bright[following]
            zoom, shift, reveal = math.exp(peak * (1.0 - move)), ease((1.0 - move) * 1.3), 1.0 - move
            tag_alpha = max(0.0, move * 3.0 - 2.0)

        picture = Image.blend(inside, self.full[following], reveal) if reveal > 0 else inside
        layer = self.mask if zoom <= 1.0 else self._zoomed_mask(zoom, shift)
        canvas = ground.copy()
        canvas.paste(picture, (0, 0), layer)
        canvas = canvas.convert("RGBA")
        if zoom <= 1.02:
            canvas.alpha_composite(self.edge)
        if self.tag is not None and tag_alpha > 0:
            canvas.alpha_composite(with_alpha(self.tag, tag_alpha), self.tag_at)
        return canvas


# ---------------------------------------------------------------- 风格 4：预告


class Trailer(Cover):
    """虚化背景上的预告横幅：左上角斜角飘带，右边平行四边形的大图和一条细斜栏。换图时斜切成几条错开滑入。"""

    BANDS = 5

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        top, bottom = int(H * 0.075), int(H * 0.925)
        skew = W * 0.098
        left, right = W * 0.492, W * 0.872
        main_points = [(left, top), (right, top), (right - skew, bottom), (left - skew, bottom)]
        bar_left, bar_right = right + W * 0.022, right + W * 0.06
        bar_points = [(bar_left, top), (bar_right, top), (bar_right - skew, bottom), (bar_left - skew, bottom)]
        self.window_mask = polygon_mask(self.size, main_points)
        # 把窗口沿斜边切成几条，换图时一条条错开滑入，方向交替
        self.skew_ratio = skew / float(bottom - top)
        self.bands = []
        for band in range(self.BANDS):
            first = left + (right - left) * band / self.BANDS
            second = left + (right - left) * (band + 1) / self.BANDS
            self.bands.append(polygon_mask(self.size, [(first, top), (second, top), (second - skew, bottom), (first - skew, bottom)]))
        self.bar_mask = polygon_mask(self.size, bar_points)
        self.window_at = (int(left - skew), top)
        window_size = (int(right - left + skew) + 1, bottom - top)
        self.bar_at = (int(bar_left - skew), top)
        bar_size = (int(bar_right - bar_left + skew) + 1, bottom - top)
        self.windows = [ImageEnhance.Contrast(self.fit(index, window_size)).enhance(1.05) for index in range(self.count)]
        self.bars = [self.fit(index, bar_size) for index in range(self.count)]
        self.grounds = [self.backdrop(index, 0.36) for index in range(self.count)]

        x_axis, _y_axis = axes(self.size)
        self.overlay = Image.new("RGBA", self.size, (0, 0, 0, 0))
        shade = np.clip(smooth((0.62 - x_axis) / 0.5) * 0.92 + 0.1, 0, 1) * self.scrim_strength()
        self.shade = rgba_from_alpha(self.base_color(), np.clip(shade, 0, 1))

        margin_x = W * 0.05
        block, margin, ink_h = self.title_block(W * 0.345, sub_style=None, button=True)
        if self.has_sub():
            self._ribbon(margin_x, int(H * 0.085))
        center = H * 0.54 + self.spec.offset_y * s
        composite_at(self.overlay, block, margin_x - margin, center - ink_h / 2.0 - margin)

    def _ribbon(self, text_x, y_pos):
        """贴着左边缘的薄荷绿飘带，右端是斜角。"""
        s = self.s
        sub = self.layout(self.W * 0.33, part="sub")
        pad_y = self.spec.sub_size * s * 0.42
        height = int(sub.height + pad_y * 2)
        end = text_x + sub.sub_width + self.W * 0.018
        slant = height * 0.62
        mask = polygon_mask(self.size, [(0, y_pos), (end + slant, y_pos), (end, y_pos + height), (0, y_pos + height)])
        ribbon = Image.new("RGBA", self.size, self.accent + (0,))
        ribbon.putalpha(mask)
        self.overlay.alpha_composite(ribbon)
        self.overlay.alpha_composite(sub.render(self.size, text_x, y_pos + pad_y - sub.offset_y,
                                                sub_fill=CARD + (255,)))

    def frame(self, phase):
        position = (phase % 1.0) * self.count
        current, following = int(position) % self.count, (int(position) + 1) % self.count
        local = position - int(position)
        hold = 0.56
        raw = (local - hold) / (1.0 - hold) if local > hold else 0.0
        progress = ease(raw)
        ground = self.grounds[current]
        window = Image.new("RGB", self.size)
        window.paste(self.windows[current], self.window_at)
        bar = self.bars[following]
        if raw > 0:
            ground = Image.blend(ground, self.grounds[following], progress)
            bar = Image.blend(bar, self.bars[(following + 1) % self.count], progress)
            incoming = self.windows[following]
            for band, mask in enumerate(self.bands):
                # 每条晚一点出发，方向交替，沿斜边滑入
                start = band * 0.1
                amount = ease_out((raw - start) / (1.0 - 0.1 * (self.BANDS - 1)))
                if amount <= 0:
                    continue
                offset_y = int(round((1.0 - amount) * self.H * (1 if band % 2 else -1)))
                offset_x = int(round(-offset_y * self.skew_ratio))
                moved = window.copy()
                moved.paste(incoming, (self.window_at[0] + offset_x, self.window_at[1] + offset_y))
                window.paste(moved, (0, 0), mask)
        canvas = ground.convert("RGBA")
        canvas.alpha_composite(self.shade)
        canvas.paste(window, (0, 0), self.window_mask)
        bar_layer = Image.new("RGB", self.size)
        bar_layer.paste(bar, self.bar_at)
        canvas.paste(bar_layer, (0, 0), self.bar_mask)
        canvas.alpha_composite(self.overlay)
        return canvas


# ---------------------------------------------------------------- 风格 5：环幕


class Surround(Cover):
    """站在弧形巨幕中间看：正面一幅，两侧朝观众包过来。整圈平稳转动，标题固定在正面下方。"""

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        self.ground = Image.new("RGBA", self.size, mix((6, 6, 8), self.tint, 0.04 * self.ratio) + (255,))
        self.card_size = (int(W * 0.415), int(H * 0.87))
        radius = int(26 * s)
        mask = rounded_mask(self.card_size, radius)
        self.cards = []
        for index in range(self.count):
            card = ImageEnhance.Contrast(self.fit(index, self.card_size)).enhance(1.05).convert("RGBA")
            card.putalpha(mask)
            self.cards.append(card)
        self.radius = W * 0.62
        self.focal = W * 0.98
        self.step = (self.card_size[0] + W * 0.022) / self.radius

        _x_axis, y_axis = axes(self.size)
        self.overlay = rgba_from_alpha((4, 4, 6), np.clip(smooth((y_axis - 0.46) / 0.54) * 0.95 * self.scrim_strength(), 0, 1))
        block, margin, ink_h = self.title_block(W * 0.74, align="center", shadow=self.text_shadow(150))
        composite_at(self.overlay, block, W * 0.13 - margin, H * 0.925 - ink_h - margin + self.spec.offset_y * s)
        self._cached = None

    def _quad(self, angle):
        half_w, half_h = self.card_size[0] / 2.0, self.card_size[1] / 2.0
        center_x = self.radius * math.sin(angle)
        center_z = -self.radius * (1.0 - math.cos(angle))
        corners = []
        for local_x, local_y in ((-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)):
            world_x = center_x + local_x * math.cos(angle)
            world_z = center_z - local_x * math.sin(angle)
            scale = self.focal / max(self.focal * 0.25, self.focal + world_z)
            corners.append((self.W / 2.0 + world_x * scale, self.H / 2.0 + local_y * scale))
        return corners

    def frame(self, phase):
        current, _following, progress = beats(phase, self.count, hold=0.66, curve=ease_in_out)
        key = (current, round(progress, 4))
        if self._cached and self._cached[0] == key:
            return self._cached[1]
        canvas = self.ground.copy()
        slots = []
        for slot in range(-2, 4):
            position = slot - progress
            angle = position * self.step
            if abs(angle) < math.radians(84):
                slots.append((abs(position), slot, angle))
        # 远的先画，正面最后画
        for distance, slot, angle in sorted(slots, reverse=True):
            card = self.cards[(current + slot) % self.count]
            shade = 1.0 - min(0.5, distance * 0.36)
            warp_onto(canvas, ImageEnhance.Brightness(card).enhance(shade), self._quad(angle))
        canvas.alpha_composite(self.overlay)
        self._cached = (key, canvas)
        return canvas


# ---------------------------------------------------------------- 风格 6：景深


class Depth(Cover):
    """大卡后面层层后退的卡片。最前面的一张迎着镜头飞出并消失，后面的依次上前。"""

    LEVELS = 3

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        self.grounds = [self.backdrop(index, 0.34) for index in range(self.count)]
        self.front_size = (int(W * 0.7), int(H * 0.72))
        self.center = (W / 2.0, H * 0.595)
        radius = int(38 * s)
        mask = rounded_mask(self.front_size, radius)
        border = border_layer(self.front_size, radius, (255, 255, 255, 44), 2 * s)
        self.cards = []
        for index in range(self.count):
            card = ImageEnhance.Contrast(self.fit(index, self.front_size)).enhance(1.05).convert("RGBA")
            card.alpha_composite(border)
            card.putalpha(mask)
            self.cards.append(card)

        # 文字和压暗固定在最前面那张的位置上，每张卡上前时都正好接住
        left = int(self.center[0] - self.front_size[0] / 2.0)
        top = int(self.center[1] - self.front_size[1] / 2.0)
        self.overlay = Image.new("RGBA", self.size, (0, 0, 0, 0))
        _x_axis, y_axis = axes(self.front_size)
        scrim = rgba_from_alpha((4, 4, 6), np.clip(smooth((y_axis - 0.3) / 0.7) * 0.94 * self.scrim_strength(), 0, 1))
        scrim.putalpha(ImageChops.multiply(scrim.getchannel("A"), mask))
        self.overlay.alpha_composite(scrim, (left, top))
        pad = self.front_size[0] * 0.05
        block, margin, ink_h = self.title_block(self.front_size[0] * 0.86, button=True, shadow=self.text_shadow(130))
        composite_at(self.overlay, block, left + pad - margin,
                     top + self.front_size[1] * 0.92 - ink_h - margin + self.spec.offset_y * s)
        shadow_box = (left, top, left + self.front_size[0], top + self.front_size[1])
        self.front_shadow = shadow_layer(self.size, shadow_box, radius, 30 * s, 0.6, 12 * s)
        self.overlay_alpha = None

    def _draw(self, canvas, card, level):
        """level 为 0 是最前面，越大越靠后，负数表示已经飞向镜头。"""
        if level >= 0:
            scale, rise, shade = 1.0 - 0.105 * level, level * self.H * 0.098, 1.0 - 0.24 * level
            fade = 1.0 if level <= self.LEVELS - 1 else max(0.0, self.LEVELS - level)
        else:
            scale, rise, shade = 1.0 - level * 0.34, level * self.H * 0.05, 1.0
            fade = max(0.0, 1.0 + level * 1.15)
        if fade <= 0:
            return
        size = (max(2, int(self.front_size[0] * scale)), max(2, int(self.front_size[1] * scale)))
        layer = card.resize(size, Image.Resampling.BILINEAR)
        if shade < 1.0:
            layer = ImageEnhance.Brightness(layer).enhance(max(0.2, shade))
        composite_at(canvas, with_alpha(layer, fade), self.center[0] - size[0] / 2.0, self.center[1] - rise - size[1] / 2.0)

    def frame(self, phase):
        current, following, progress = beats(phase, self.count, hold=0.62, curve=ease_in_out)
        ground = self.grounds[current] if progress <= 0 else Image.blend(self.grounds[current], self.grounds[following], progress)
        canvas = ground.convert("RGBA")
        for depth in range(self.LEVELS, 1, -1):
            self._draw(canvas, self.cards[(current + depth) % self.count], depth - progress)
        if progress <= 0:
            self._draw(canvas, self.cards[(current + 1) % self.count], 1.0)
            canvas.alpha_composite(self.front_shadow)
            self._draw(canvas, self.cards[current], 0.0)
            canvas.alpha_composite(self.overlay)
            return canvas
        # 投影压在正在上前的那张下面。文字留在原位，飞出的卡片从文字前面掠过
        canvas.alpha_composite(self.front_shadow)
        self._draw(canvas, self.cards[(current + 1) % self.count], 1.0 - progress)
        canvas.alpha_composite(self.overlay)
        self._draw(canvas, self.cards[current], -progress)
        return canvas


# ---------------------------------------------------------------- 风格 7：详情


class Detail(Cover):
    """虚化背景上一张带播放键的大卡，右边标题和播放按钮，下方一排缩略图。第一张缩略图放大成新的主卡，其余左移补位。"""

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        self.grounds = [self.backdrop(index, 0.32) for index in range(self.count)]
        _x_axis, y_axis = axes(self.size)
        shade = 0.62 + smooth((y_axis - 0.45) / 0.45) * 0.36
        self.shade = rgba_from_alpha(self.base_color(), np.clip(shade * self.scrim_strength(), 0, 1))

        self.hero_box = (int(W * 0.046), int(H * 0.085))
        self.hero_size = (int(W * 0.5), int(H * 0.585))
        radius = int(36 * s)
        self.hero_mask = rounded_mask(self.hero_size, radius)
        self.heroes = [ImageEnhance.Contrast(self.fit(index, self.hero_size)).enhance(1.05) for index in range(self.count)]
        hero_full = (*self.hero_box, self.hero_box[0] + self.hero_size[0], self.hero_box[1] + self.hero_size[1])

        self.overlay = shadow_layer(self.size, hero_full, radius, 30 * s, 0.55, 12 * s)
        badge = play_badge(self.hero_size[1] * 0.24, glass=(0, 0, 0, 90))
        self.badge = badge
        self.badge_at = (self.hero_box[0] + (self.hero_size[0] - badge.width) // 2,
                         self.hero_box[1] + (self.hero_size[1] - badge.height) // 2)

        text_x = W * 0.588
        block, margin, ink_h = self.title_block(W * 0.37, sub_style="text", shadow=self.text_shadow(120))
        chip = play_chip(self.spec.sub_size * s * 1.72, self.spec.sub_size * s * 3.0)
        total = ink_h + 44 * s + chip.height
        top = hero_full[1] + (self.hero_size[1] - total) / 2.0 + self.spec.offset_y * s
        composite_at(self.overlay, block, text_x - margin, top - margin)
        composite_at(self.overlay, chip, text_x, top + ink_h + 44 * s)

        self.thumb_size = (int(W * 0.232), int(H * 0.36))
        self.thumb_gap = int(W * 0.0125)
        self.thumb_y = int(H * 0.73)
        self.thumb_x = self.hero_box[0]
        self.thumb_mask = rounded_mask(self.thumb_size, int(30 * s))
        fade = rgba_from_alpha(self.base_color(), smooth(axes(self.thumb_size)[1] / 0.75) * 0.72)
        self.thumbs = []
        for index in range(self.count):
            thumb = self.fit(index, self.thumb_size).convert("RGBA")
            thumb.alpha_composite(fade)
            self.thumbs.append(thumb.convert("RGB"))

    def frame(self, phase):
        current, following, progress = beats(phase, self.count, hold=0.6, curve=ease_in_out)
        ground = self.grounds[current] if progress <= 0 else Image.blend(self.grounds[current], self.grounds[following], progress)
        canvas = ground.convert("RGBA")
        canvas.alpha_composite(self.shade)
        canvas.alpha_composite(self.overlay)
        hero = self.heroes[current]
        if progress > 0:
            hero = ImageEnhance.Brightness(hero).enhance(1.0 - 0.45 * progress)
        canvas.paste(hero, self.hero_box, self.hero_mask)
        if progress <= 0:
            canvas.alpha_composite(self.badge, self.badge_at)

        # 底排整体左移一格；第一张缩略图不动，它要放大成新的主卡
        step = self.thumb_size[0] + self.thumb_gap
        for slot in range(1 if progress > 0 else 0, 7):
            x_pos = int(round(self.thumb_x + (slot - progress) * step))
            if x_pos >= self.W or x_pos + self.thumb_size[0] <= 0:
                continue
            canvas.paste(self.thumbs[(current + 1 + slot) % self.count], (x_pos, self.thumb_y), self.thumb_mask)
        if progress <= 0:
            return canvas

        grow = ease_out(progress)
        width = int(self.thumb_size[0] + (self.hero_size[0] - self.thumb_size[0]) * grow)
        height = int(self.thumb_size[1] + (self.hero_size[1] - self.thumb_size[1]) * grow)
        x_pos = int(self.thumb_x + (self.hero_box[0] - self.thumb_x) * grow)
        y_pos = int(self.thumb_y + (self.hero_box[1] - self.thumb_y) * grow)
        flying = ImageOps.fit(self.heroes[following], (width, height), method=Image.Resampling.BILINEAR)
        radius = int(30 * self.s + 6 * self.s * grow)
        canvas.alpha_composite(shadow_layer(self.size, (x_pos, y_pos, x_pos + width, y_pos + height), radius, 26 * self.s, 0.55, 10 * self.s))
        canvas.paste(flying, (x_pos, y_pos), rounded_mask((width, height), radius))
        if progress > 0.8:
            canvas.alpha_composite(with_alpha(self.badge, (progress - 0.8) / 0.2), self.badge_at)
        return canvas


# ---------------------------------------------------------------- 风格 8：扇面


class Fan(Cover):
    """海报沿大圆弧排成扇形，标题居中在上方。整把扇子转动一格，停下时带一点回弹。"""

    STEP = math.radians(17.0)

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        self.grounds = [self.backdrop(index, 0.3) for index in range(self.count)]
        _x_axis, y_axis = axes(self.size)
        self.shade = rgba_from_alpha(self.base_color(), np.clip((0.86 - y_axis * 0.5) * self.scrim_strength(), 0, 1))

        self.poster_h = int(H * 0.69)
        self.poster_w = int(self.poster_h * 0.68)
        self.orbit = H * 1.42
        self.pivot = (W / 2.0, H * 0.67 + self.orbit)
        scale = 2
        size = (self.poster_w * scale, self.poster_h * scale)
        radius = int(30 * s * scale)
        mask = rounded_mask(size, radius, supersample=2)
        border = border_layer(size, radius, (255, 255, 255, 60), 3 * s * scale)
        self.posters = []
        for index in range(self.count):
            poster = ImageEnhance.Contrast(self.fit(index, size)).enhance(1.05).convert("RGBA")
            poster.alpha_composite(border)
            poster.putalpha(mask)
            self.posters.append(poster)

        self.overlay = Image.new("RGBA", self.size, (0, 0, 0, 0))
        block, margin, _ink_h = self.title_block(W * 0.8, sub_style="text", align="center", sub_fill=self.accent,
                                                 shadow=self.text_shadow(150))
        composite_at(self.overlay, block, W * 0.1 - margin, H * 0.05 - margin + self.spec.offset_y * s)
        self._cached = None

    def _draw(self, canvas, poster, position):
        angle = position * self.STEP
        center_x = self.pivot[0] + math.sin(angle) * self.orbit
        center_y = self.pivot[1] - math.cos(angle) * self.orbit
        distance = abs(position)
        shade = 1.0 - min(0.52, distance * 0.2)
        layer = ImageEnhance.Brightness(poster).enhance(shade) if shade < 1.0 else poster
        turned = layer.rotate(-math.degrees(angle), resample=Image.Resampling.BILINEAR, expand=True)
        final = turned.resize((max(1, turned.width // 2), max(1, turned.height // 2)), Image.Resampling.BOX)
        x_pos, y_pos = center_x - final.width / 2.0, center_y - final.height / 2.0
        glow = final.getchannel("A").filter(ImageFilter.GaussianBlur(max(1.0, 14 * self.s)))
        shadow = Image.new("RGBA", final.size, (0, 0, 0, 0))
        shadow.putalpha(glow.point(lambda value: int(value * 0.5)))
        composite_at(canvas, shadow, x_pos, y_pos + 8 * self.s)
        composite_at(canvas, final, x_pos, y_pos)

    def frame(self, phase):
        current, following, progress = beats(phase, self.count, hold=0.66, curve=ease_back)
        key = (current, round(progress, 4))
        if self._cached and self._cached[0] == key:
            return self._cached[1]
        blend = max(0.0, min(1.0, progress))
        ground = self.grounds[current] if blend <= 0 else Image.blend(self.grounds[current], self.grounds[following], blend)
        canvas = ground.convert("RGBA")
        canvas.alpha_composite(self.shade)
        slots = sorted(range(-4, 6), key=lambda slot: -abs(slot - progress))
        for slot in slots:
            position = slot - progress
            if abs(position) > 4.6:
                continue
            self._draw(canvas, self.posters[(current + slot) % self.count], position)
        canvas.alpha_composite(self.overlay)
        self._cached = (key, canvas)
        return canvas


DESIGNS = {1: Gallery, 2: Titan, 3: Glint, 4: Trailer, 5: Surround, 6: Depth, 7: Detail, 8: Fan}


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
