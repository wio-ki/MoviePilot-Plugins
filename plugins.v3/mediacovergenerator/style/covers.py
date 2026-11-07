"""
八款封面风格的构图。每款有自己的切换方式，切换也是设计的一部分，不共用同一种转场。

每款风格是一个 Cover 子类：prepare() 预先算好不变的图层，frame(phase) 画出循环中某一时刻的画面。
静态封面就是 phase=0 的高清帧，动图是同一套构图的低分辨率逐帧输出，所以两者外观一致。

动图的几条约定：
- 输出 GIF，这是各家 Emby 客户端都能显示的格式
- phase=0 就是完整封面，只显示首帧的客户端看到的也是成品
- 大部分时间画面静止、只在转场时运动。静止的帧合并成一帧，体积才小，文字也不会闪
"""
import base64
import math
import subprocess
import tempfile
import time
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from app.log import logger
from app.plugins.mediacovergenerator.style.catalog import style_info
from app.plugins.mediacovergenerator.style.faces import DETECT_SIZE
from app.plugins.mediacovergenerator.style.framing import load_pictures
from app.plugins.mediacovergenerator.style.typography import TitleLayout, load_font
from app.plugins.mediacovergenerator.utils.color_helper import ColorHelper

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


def luminous(image, target=150.0, floor=46.0, saturation=1.3):
    """
    让暗图也足够亮：拉满对比后用伽马把平均亮度提到 target，再抬高暗部。
    只透出笔画时，图的暗部会和黑底融在一起，字形就散了。
    """
    data = np.asarray(ImageOps.autocontrast(ImageEnhance.Color(image).enhance(saturation), cutoff=1), dtype=np.float32) / 255.0
    mean = float(data.mean())
    if 0.01 < mean < target / 255.0:
        data = data ** (math.log(target / 255.0) / math.log(mean))
    data = floor / 255.0 + data * (1.0 - floor / 255.0)
    return Image.fromarray(np.clip(data * 255.0, 0, 255).astype(np.uint8))


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

    def __init__(self, size, images, title, fonts, spec, accent, tint, blur_size, color_ratio, animated, display_font=None):
        self.display_font = display_font
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

    def picture(self, index):
        return self.images[index % self.count]

    def framed(self, index, size, anchor=0.5, clean=False):
        """取好景的素材和焦点 (图, 焦点 x, 焦点 y)，同一尺寸只算一次。参数见 Picture.framed。"""
        size = (max(1, int(size[0])), max(1, int(size[1])))
        key = (index % self.count, size, anchor, clean)
        if key not in self._fits:
            self._fits[key] = self.picture(index).framed(size, anchor, clean=clean)
        return self._fits[key]

    def fit(self, index, size, anchor=0.5, clean=False):
        return self.framed(index, size, anchor, clean)[0]

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
        blurred = self.picture(index).cover(self.size).filter(ImageFilter.GaussianBlur(radius))
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
        # 窄条一定会切开海报上的片名，所以优先用没有片名的背景图，围绕人物取景
        panels = [self.framed(index, (self.active_w, self.H), clean=True) for index in range(self.STRIPS)]
        self.bright = [ImageEnhance.Contrast(panel[0]).enhance(1.04) for panel in panels]
        # 收窄时露出的那一条对准人物，不是固定取中间
        self.focus = [panel[1] * self.active_w for panel in panels]
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
            left = int(round(max(0.0, min(self.active_w - shown, self.focus[index] - shown / 2.0))))
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
        # 只有海报时，完整的海报摆在标题和小窗之间
        self.frames = [ImageEnhance.Contrast(self.fit(index, self.size, anchor=0.57)).enhance(1.05) for index in range(self.count)]
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
        # 播放键放在角上，不挡小窗里的人物
        badge = play_badge(card_h * 0.3)
        inset = int(card_h * 0.07)
        self.cards = []
        for index in range(self.count):
            card = ImageEnhance.Brightness(self.fit(index, self.card_size)).enhance(0.86).convert("RGBA")
            card.alpha_composite(badge, (card_w - badge.width - inset, card_h - badge.height - inset))
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


# ---------------------------------------------------------------- 风格 3：404


class NotFound(Cover):
    """
    黑底上巨大的 404，一整幅图藏在三个数字后面，人物落在笔画上，外侧的 4 渐渐融回白色。主副标题不镂空。
    一阵信号不良的闪烁之后，整个画面变成这张完整的大图，没有 404，也没有文字；再闪烁一次，回到下一张图的 404。
    第一帧是 404 加主副标题，只显示首帧的客户端和静态封面也能一眼认出是哪个媒体库。
    """

    TEXT = "404"
    BEATS = 4
    # 一拍里的时间分配：404 停留、闪烁变成大图、大图停留，其余是闪烁回到 404
    HOLD = 0.38
    OUT = 0.2
    FULL = 0.3
    # 数字的字高占画面高度的比例
    DIGIT_HEIGHT = 0.5
    # 透图的范围，是以 0 为中心的椭圆。soft 是融回白色，arc 是清晰的曲线，full 是全部透图
    WASH = "soft"
    WASH_X = 0.92
    WASH_Y = 1.5

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        self.beat_count = max(1, min(self.BEATS, self.count))
        block, margin, ink_h = self.title_block(W * 0.84, sub_style="text", align="center")
        digits = self._render_digits()
        box = digits.getbbox() or (0, 0, 1, 1)
        digit_h = box[3] - box[1]
        gap = H * 0.05
        top = (H - digit_h - gap - ink_h) / 2.0 + self.spec.offset_y * s

        # 底图：底色加上主副标题，转场时完全不动
        base = Image.new("RGBA", self.size, self.base_color() + (255,))
        composite_at(base, block, W * 0.08 - margin, top + digit_h + gap - margin)
        self.base = np.asarray(base.convert("RGB"), dtype=np.float32)

        # 数字放到画布上的最终位置
        self.box = (int(round((W - (box[2] - box[0])) / 2.0)), int(round(top)))
        self.box = (*self.box, self.box[0] + box[2] - box[0], self.box[1] + digit_h)
        placed = Image.new("L", self.size, 0)
        placed.paste(digits.crop(box), self.box[:2])
        self.alpha = np.asarray(placed, dtype=np.float32) / 255.0

        x0, y0, x1, y1 = self.box
        wash = self._wash(x1 - x0, y1 - y0)
        visible = self.alpha[y0:y1, x0:x1] * wash
        # 取景只需要一张小图判断笔画在哪
        step = max(1, (x1 - x0) // 96)
        white = np.array(GHOST_WHITE, dtype=np.float32)
        self.white = np.empty((H, W, 3), dtype=np.float32)
        self.white[...] = white
        self.fills, self.full = [], []
        for index in range(self.beat_count):
            art = luminous(self.picture(index).placed((x1 - x0, y1 - y0), visible[::step, ::step]))
            fill = self.white.copy()
            fill[y0:y1, x0:x1] = white + (np.asarray(art, dtype=np.float32) - white) * wash[..., None]
            self.fills.append(fill)
            picture = ImageEnhance.Contrast(self.fit(index, self.size, clean=True)).enhance(1.05)
            self.full.append(np.asarray(picture, dtype=np.float32))
        self._still = {}

    def _render_digits(self):
        """把 404 画成字高固定的蒙版，字距略收，三个数字才是一个整体。"""
        W, H = self.W, self.H
        path = self.display_font or self.fonts[0]
        ss = 3
        target = H * self.DIGIT_HEIGHT * ss
        probe = ImageFont.truetype(str(path), 1000)
        ink = probe.getbbox(self.TEXT, anchor="ls")
        size = target / max(1.0, ink[3] - ink[1]) * 1000.0
        tracking = -0.025
        width = (probe.getlength(self.TEXT) + tracking * 1000.0 * (len(self.TEXT) - 1)) * size / 1000.0
        # 太宽时整体收小，留出左右边距
        size *= min(1.0, W * 0.86 * ss / max(1.0, width))
        font = ImageFont.truetype(str(path), max(1, int(round(size))))
        canvas = Image.new("L", (W * ss, H * 2 * ss), 0)
        draw = ImageDraw.Draw(canvas)
        pen = 0.0
        baseline = H * 1.5 * ss
        for char in self.TEXT:
            draw.text((pen, baseline), char, font=font, fill=255, anchor="ls")
            pen += font.getlength(char) + tracking * font.size
        return canvas.resize((W, H * 2), Image.Resampling.LANCZOS)

    def _wash(self, width, height):
        """透图的范围：1 是图，0 是白。"""
        columns, rows = np.meshgrid(np.linspace(-1.0, 1.0, width, dtype=np.float32),
                                    np.linspace(-1.0, 1.0, height, dtype=np.float32))
        distance = np.sqrt((columns / self.WASH_X) ** 2 + (rows / self.WASH_Y) ** 2)
        if self.WASH == "full":
            return np.ones_like(distance)
        if self.WASH == "arc":
            edge = max(0.004, 1.5 / width)
            return np.clip((1.0 - distance) / edge + 0.5, 0.0, 1.0)
        return 1.0 - smooth((distance - 0.56) / 0.44)

    def _paint(self, fill, alpha=None):
        alpha = self.alpha if alpha is None else alpha
        return self.base + (fill - self.base) * alpha[..., None]

    def _hold(self, key, fill):
        if key not in self._still:
            self._still[key] = Image.fromarray(np.clip(self._paint(fill), 0, 255).astype(np.uint8))
        return self._still[key]

    def _snow(self, rng):
        """没有信号时的雪花，颗粒按画面大小缩放。"""
        grain = max(2, self.H // 90)
        rows, columns = self.H // grain + 1, self.W // grain + 1
        field = rng.random((rows, columns), dtype=np.float32) * 255.0
        field = np.repeat(np.repeat(field, grain, axis=0), grain, axis=1)[:self.H, :self.W]
        return np.repeat(field[..., None], 3, axis=2)

    def _bands(self, arrays, top, bottom, strength, rng):
        """水平错位的条带：几个数组一起错开同样的距离。"""
        H, W = self.H, self.W
        for _ in range(int(2 + 5 * strength)):
            start = int(rng.integers(top, max(top + 1, bottom)))
            height = int(rng.integers(max(1, H // 90), max(2, H // 11)))
            shift = int(rng.integers(-1000, 1001) / 1000.0 * W * 0.07 * strength)
            end = min(H, start + height)
            for array in arrays:
                array[start:end] = np.roll(array[start:end], shift, axis=1)

    def _glitch(self, fill, strength, rng, dim=1.0):
        """信号不良的 404：数字错位成几条，轻微跳动，色边分离，偶尔扫描线。主副标题不动。"""
        H, W = self.H, self.W
        alpha = self.alpha
        layer = fill
        # 整体跳动
        jump = int(round(rng.integers(-1, 2) * H * 0.012 * strength))
        if jump:
            alpha = np.roll(alpha, jump, axis=0)
            layer = np.roll(layer, jump, axis=0)
        alpha = alpha.copy()
        layer = layer.copy()
        self._bands((alpha, layer), self.box[1], self.box[3], strength, rng)
        # 扫描线
        if rng.random() < 0.5 * strength:
            alpha[::max(2, H // 60)] *= 0.35
        alpha = alpha * dim
        # 色边：红蓝通道错开
        split = int(round(W * 0.011 * strength))
        output = np.empty_like(self.base)
        for channel, offset in ((0, split), (1, 0), (2, -split)):
            shifted = np.roll(alpha, offset, axis=1) if offset else alpha
            output[..., channel] = self.base[..., channel] + (layer[..., channel] - self.base[..., channel]) * shifted
        return Image.fromarray(np.clip(output, 0, 255).astype(np.uint8))

    def _glitch_screen(self, picture, strength, rng):
        """信号不良的大图：整屏错位、跳动、色边分离，偶尔扫描线。"""
        H, W = self.H, self.W
        jump = int(round(rng.integers(-1, 2) * H * 0.02 * strength))
        layer = np.roll(picture, jump, axis=0) if jump else picture.copy()
        self._bands((layer,), 0, H, strength, rng)
        split = int(round(W * 0.012 * strength))
        output = np.empty_like(layer)
        for channel, offset in ((0, split), (1, 0), (2, -split)):
            output[..., channel] = np.roll(layer[..., channel], offset, axis=1) if offset else layer[..., channel]
        if rng.random() < 0.6 * strength:
            output[::max(2, H // 60)] *= 0.45
        return Image.fromarray(np.clip(output, 0, 255).astype(np.uint8))

    def frame(self, phase):
        position = (phase % 1.0) * self.beat_count
        current = int(position) % self.beat_count
        following = (current + 1) % self.beat_count
        local = position - int(position)
        if local < self.HOLD:
            return self._hold(("digits", current), self.fills[current])

        rng = np.random.default_rng(current * 100000 + int(local * 90000))
        roll = rng.random()
        if local < self.HOLD + self.OUT:
            # 404 开始闪烁，雪花和白字夹杂着大图，越来越稳，最后定在大图上
            amount = (local - self.HOLD) / self.OUT
            if amount < 0.35:
                fill = self.fills[current] if roll < 0.45 else (self.white if roll < 0.7 else self._snow(rng))
                return self._glitch(fill, 0.55 + amount, rng, dim=0.4 if roll > 0.9 else 1.0)
            if amount < 0.7:
                if roll < 0.55:
                    return self._glitch_screen(self.full[current], 0.9, rng)
                return self._glitch(self._snow(rng) if roll < 0.8 else self.white, 0.9, rng)
            return self._glitch_screen(self.full[current], 0.05 + (1.0 - amount) / 0.3 * 0.65, rng)
        if local < self.HOLD + self.OUT + self.FULL:
            # 完整的大图，没有 404，也没有主副标题
            if ("full", current) not in self._still:
                self._still[("full", current)] = Image.fromarray(self.full[current].astype(np.uint8))
            return self._still[("full", current)]

        # 再闪烁一次，回到下一张图的 404
        amount = (local - self.HOLD - self.OUT - self.FULL) / (1.0 - self.HOLD - self.OUT - self.FULL)
        if amount < 0.4:
            return self._glitch_screen(self.full[current], 0.5 + amount, rng)
        if amount < 0.7:
            return self._glitch(self.fills[following] if roll < 0.7 else self._snow(rng), 0.8, rng)
        return self._glitch(self.fills[following], (1.0 - amount) / 0.3 * 0.45 + 0.02, rng)


# ---------------------------------------------------------------- 风格 4：预告


class Trailer(Cover):
    """虚化背景上的预告横幅：左上角斜角飘带，右边平行四边形的大图和一条细斜栏。换图时斜切成几条错开滑入。"""

    BANDS = 5
    # 播放键放在大图的角上；留空就不放
    BADGE = True
    # 左下角刻度的高度占画面高度的比例，以及实际显示很小时不能再低的像素数
    TICK_HEIGHT = 0.085
    TICK_MIN_HEIGHT = 15.0
    # 没点亮的刻度的不透明度
    TICK_IDLE_ALPHA = 190

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
        # 斜框和细条都会切开海报上的片名，优先用背景图
        self.windows = [ImageEnhance.Contrast(self.fit(index, window_size, clean=True)).enhance(1.05) for index in range(self.count)]
        self.bars = [self.fit(index, bar_size, clean=True) for index in range(self.count)]
        self.grounds = [self.backdrop(index, 0.36) for index in range(self.count)]

        x_axis, _y_axis = axes(self.size)
        self.overlay = Image.new("RGBA", self.size, (0, 0, 0, 0))
        shade = np.clip(smooth((0.62 - x_axis) / 0.5) * 0.92 + 0.1, 0, 1) * self.scrim_strength()
        self.shade = rgba_from_alpha(self.base_color(), np.clip(shade, 0, 1))

        margin_x = W * 0.05
        block, margin, ink_h = self.title_block(W * 0.345, sub_style=None)
        ribbon_bottom = int(H * 0.085)
        if self.has_sub():
            ribbon_bottom = self._ribbon(margin_x, int(H * 0.085))
        self._ticks(margin_x, bottom, W * 0.16)
        # 标题放在飘带和刻度中间
        center = (ribbon_bottom + self.ticks_top) / 2.0 + self.spec.offset_y * s
        composite_at(self.overlay, block, margin_x - margin, center - ink_h / 2.0 - margin)
        if self.BADGE:
            # 播放键压在大图右下角，不挡人物，也不和标题抢位置
            badge = play_badge(H * 0.135, glass=(0, 0, 0, 96))
            inset = H * 0.035
            composite_at(self.overlay, badge, right - skew - badge.width - inset, bottom - badge.height - inset)

    def _ticks(self, left, bottom, width):
        """
        左下角一排细斜刻度，斜度和大图一致，第几段预告就点亮第几根；旁边是编号。
        封面常常只显示 320×180，所以线宽和高度都按像素保底，细到糊掉就等于没有。
        """
        W, H = self.W, self.H
        height = max(self.TICK_MIN_HEIGHT, H * self.TICK_HEIGHT)
        self.tick_height = height
        self.tick_idle_height = height * 0.68
        self.tick_idle = max(2.4, W * 0.0034)
        self.tick_active = max(4.6, W * 0.0085)
        # 图片多的时候收紧间距，但两根之间至少留出一根的宽度
        span = max(1, self.count - 1)
        self.tick_step = max(self.tick_idle * 2, min(W * 0.032, width / span))
        # 点亮的那根向两边长粗，左边留出余量，让第一根也对齐左边距
        self.tick_pad = (self.tick_active - self.tick_idle) / 2.0
        ticks_width = self.tick_step * span + self.tick_active + height * self.skew_ratio
        strip_size = (int(ticks_width + 3), int(height + 2))
        self.tick_size = strip_size
        self.tick_at = (int(left - self.tick_pad), int(bottom - strip_size[1]))
        self.tick_baseline = strip_size[1] - 1.0
        self.tick_cache = {}
        self.ticks_top = bottom - height
        self._counters(left + ticks_width + W * 0.016, bottom, max(9.5, H * 0.05))

    def _counters(self, left, bottom, height):
        """编号比刻度矮，让刻度当主角；总数再小一号。底边都和大图对齐。"""
        ss = 3
        probe = load_font(self.fonts[1], 1000)
        ink = probe.getbbox("0", anchor="ls")
        large = load_font(self.fonts[1], height * ss / max(1, ink[3] - ink[1]) * 1000)
        small = load_font(self.fonts[1], height * ss / max(1, ink[3] - ink[1]) * 1000 * 0.7)
        base = large.getbbox("0", anchor="ls")[3]
        size = (int(self.W * 0.2), int(height * 1.4))
        at = (int(left), int(bottom - height * 1.2))
        base_y = int(height * 1.2 * ss) - base
        self.counters = []
        for index in range(self.count):
            layer = Image.new("RGBA", (size[0] * ss, size[1] * ss), (0, 0, 0, 0))
            draw = ImageDraw.Draw(layer)
            text = f"{index + 1:02d}"
            draw.text((0, base_y), text, font=large, fill=GHOST_WHITE + (255,), anchor="ls")
            draw.text((large.getlength(text) + height * ss * 0.32, base_y), f"/ {self.count:02d}", font=small,
                      fill=SILVER + (215,), anchor="ls")
            self.counters.append(layer.resize(size, Image.Resampling.LANCZOS))
        self.counter_at = at

    def _tick_strip(self, current, following, progress):
        """画这一帧的刻度。停留时内容不变，GIF 才能把相同的帧合并。"""
        key = (current, following, round(progress, 3))
        strip = self.tick_cache.get(key)
        if strip is not None:
            return strip
        strip = Image.new("RGBA", self.tick_size, (0, 0, 0, 0))
        for index in range(self.count):
            amount = (1.0 - progress if index == current else 0.0) + (progress if index == following else 0.0)
            stroke = self.tick_idle + (self.tick_active - self.tick_idle) * amount
            height = self.tick_idle_height + (self.tick_height - self.tick_idle_height) * amount
            slant = height * self.skew_ratio
            x_pos = self.tick_pad + index * self.tick_step + (self.tick_idle - stroke) / 2.0
            bottom, top = self.tick_baseline, self.tick_baseline - height
            mask = polygon_mask(self.tick_size, [(x_pos + slant, top), (x_pos + slant + stroke, top),
                                                 (x_pos + stroke, bottom), (x_pos, bottom)], supersample=4)
            color = mix((255, 255, 255), self.accent, amount)
            alpha = self.TICK_IDLE_ALPHA + (255 - self.TICK_IDLE_ALPHA) * amount
            tick = Image.new("RGBA", self.tick_size, color + (0,))
            tick.putalpha(mask.point(lambda value, level=alpha: int(value * level / 255.0)))
            strip.alpha_composite(tick)
        self.tick_cache[key] = strip
        return strip

    def _ticks_frame(self, canvas, current, following, progress):
        """换段时点亮的刻度移到下一根。编号走到一半直接换，这么小的字叠化只会糊成一团。"""
        canvas.alpha_composite(self._tick_strip(current, following, progress), self.tick_at)
        canvas.alpha_composite(self.counters[following if progress >= 0.5 else current], self.counter_at)

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
        return y_pos + height

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
        self._ticks_frame(canvas, current, following, progress)
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
            # 片名会和底部的大标题撞在一起，优先用背景图
            card = ImageEnhance.Contrast(self.fit(index, self.card_size, clean=True)).enhance(1.05).convert("RGBA")
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
        # 取整方式要和 composite_at 一致，错开一像素时亮图的底边会露出一条白线
        left = int(round(self.center[0] - self.front_size[0] / 2.0))
        top = int(round(self.center[1] - self.front_size[1] / 2.0))
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
        # 播放键放在主卡右下角，不挡人物
        badge = play_badge(self.hero_size[1] * 0.17, glass=(0, 0, 0, 90))
        inset = int(self.hero_size[1] * 0.05)
        self.badge = badge
        self.badge_at = (self.hero_box[0] + self.hero_size[0] - badge.width - inset,
                         self.hero_box[1] + self.hero_size[1] - badge.height - inset)

        text_x = W * 0.588
        block, margin, ink_h = self.title_block(W * 0.37, sub_style="text", shadow=self.text_shadow(120))
        chip = play_chip(self.spec.sub_size * s * 1.72, self.spec.sub_size * s * 3.0)
        total = ink_h + 44 * s + chip.height
        top = hero_full[1] + (self.hero_size[1] - total) / 2.0 + self.spec.offset_y * s
        composite_at(self.overlay, block, text_x - margin, top - margin)
        composite_at(self.overlay, chip, text_x, top + ink_h + 44 * s)

        # 底排四张完整地摆在画面里，左右和主卡对齐，不被画面边缘切掉
        self.thumb_gap = int(W * 0.0125)
        self.thumb_size = (int((W - self.hero_box[0] * 2 - self.thumb_gap * 3) / 4.0), int(H * 0.235))
        self.thumb_y = int(H * 0.715)
        self.thumb_x = self.hero_box[0]
        self.thumb_mask = rounded_mask(self.thumb_size, int(26 * s))
        self.thumbs = [ImageEnhance.Brightness(self.fit(index, self.thumb_size)).enhance(0.86) for index in range(self.count)]

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
        # 静止时只有四张；转场时第五张从右边进来
        for slot in range(1, 5) if progress > 0 else range(0, 4):
            x_pos = int(round(self.thumb_x + (slot - progress) * step))
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

        # 正中那张完整地落在画面里，两边的才滑出画面
        self.poster_h = int(H * 0.65)
        self.poster_w = int(self.poster_h * 0.68)
        self.orbit = H * 1.42
        self.pivot = (W / 2.0, H * 0.65 + self.orbit)
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


DESIGNS = {1: Gallery, 2: Titan, 3: NotFound, 4: Trailer, 5: Surround, 6: Depth, 7: Detail, 8: Fan}


# ---------------------------------------------------------------- 输出


def encode_jpeg(image, quality=92):
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality, optimize=True, subsampling=0)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _run_ffmpeg(command, stop_event):
    """运行 ffmpeg，可被停止信号打断。返回 (退出码, 错误输出)，被打断时退出码为 None。"""
    # 错误输出写到文件：写进管道又没人读的话，管道一满 ffmpeg 就会卡死
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors)
        while process.poll() is None:
            if stop_event and stop_event.is_set():
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
                return None, ""
            time.sleep(0.05)
        errors.seek(0)
        return process.returncode, errors.read().decode("utf-8", "ignore")[-400:]


def encode_gif(render_frame, duration, fps, reduce_colors, stop_event):
    """
    逐帧渲染并调用 ffmpeg 合成 GIF，stop_event 触发时立即中止并返回 False。

    全片共用一张调色板再抖动，暗部和渐变上会有一层点状噪点。这里每帧用自己的调色板、不抖动，画面干净得多。
    每帧调色板不能靠帧差压缩，所以静止的帧要合并成一帧，用延长显示时间代替。
    """
    safe_fps = max(1, int(fps))
    total = max(2, int(round(safe_fps * max(1, float(duration)))))
    step = 1.0 / safe_fps
    colors = {"off": 256, "medium": 192, "strong": 96}.get(reduce_colors, 192)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        logger.info(f"开始生成动画帧，共 {total} 帧")
        kept = []
        previous = None
        for index in range(total):
            if stop_event and stop_event.is_set():
                return False
            frame = render_frame(index / float(total)).convert("RGB")
            pixels = frame.tobytes()
            if pixels == previous:
                kept[-1][1] += step
                continue
            previous = pixels
            name = f"frame_{index:04d}.bmp"
            frame.save(temp_path / name, format="BMP")
            kept.append([name, step])

        listing = temp_path / "frames.txt"
        lines = []
        for name, shown in kept:
            lines += [f"file '{name}'", f"duration {shown:.4f}"]
        # concat 要求最后一个文件重复一次，否则最后一帧的时长会被忽略
        lines.append(f"file '{kept[-1][0]}'")
        listing.write_text("\n".join(lines) + "\n", encoding="utf-8")

        data = _compose_gif(listing, temp_path / "output.gif", colors, stop_event)
        if data is None:
            return False
        logger.info(f"动图生成完成，{len(kept)}/{total} 帧，{len(data) / 1024 / 1024:.1f} MB")
        return base64.b64encode(data).decode("utf-8")


def _compose_gif(listing, output, colors, stop_event):
    """
    合成 GIF，返回文件内容，被停止时返回 None。
    不能只看退出码：线上出现过 ffmpeg 退出码为 0 却没有输出文件的情况，一次失败不该让整个媒体库失败。
    旧版 ffmpeg 不认识 -fps_mode；每帧调色板都失败时退回全片一张调色板，画质差一点但一定能出图。
    """
    per_frame = (f"[0:v] split [a][b]; [a] palettegen=max_colors={colors}:stats_mode=single [p]; "
                 f"[b][p] paletteuse=new=1:dither=none")
    shared = (f"[0:v] split [a][b]; [a] palettegen=max_colors={colors}:stats_mode=full [p]; "
              f"[b][p] paletteuse=dither=bayer:bayer_scale=4")
    attempts = ((["-fps_mode", "vfr"], per_frame), (["-vsync", "vfr"], per_frame), (["-fps_mode", "vfr"], per_frame),
                (["-fps_mode", "vfr"], shared), (["-vsync", "vfr"], shared))
    failure = ""
    for sync, graph in attempts:
        output.unlink(missing_ok=True)
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                   "-threads", "0", *sync, "-filter_complex", graph, "-loop", "0", "-f", "gif", str(output)]
        code, detail = _run_ffmpeg(command, stop_event)
        if code is None:
            return None
        if code == 0 and output.is_file() and output.stat().st_size > 0:
            return output.read_bytes()
        failure = f"ffmpeg 退出码 {code}，{'没有生成文件' if code == 0 else '合成失败'}：{detail}"
        logger.warning(f"合成 GIF 失败，换一种方式再试。{failure}")
    raise RuntimeError(failure)


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
                 animation_duration=12, animation_fps=15, animation_reduce_colors="medium", stop_event=None,
                 display_font=None):
    """生成封面，返回图片的 base64：静态为 JPEG，动态为 GIF。失败时返回 False。"""
    info = style_info(index)
    try:
        count = max(info.slots, int(image_count or 0)) if animated else info.slots
        # 原图比画布大太多只会拖慢速度，但要留够人脸检测需要的清晰度
        limit = max(int(max(size) * 1.6), DETECT_SIZE)
        images = load_pictures(library_dir, count, limit)
        if not images:
            logger.warning(f"风格「{info.name}」没有找到可用的素材")
            return False
        accent, tint = _accent_and_tint(images[0].main.image, info, bg_color_config)
        cover = DESIGNS[info.index](size, images, title, fonts, spec, accent, tint, blur_size, color_ratio, animated,
                                   display_font=display_font)
        if not animated:
            return encode_jpeg(cover.frame(0.0))
        return encode_gif(cover.frame, animation_duration, animation_fps, animation_reduce_colors, stop_event)
    except Exception as error:
        logger.error(f"生成风格「{info.name}」失败: {error}", exc_info=True)
        return False
