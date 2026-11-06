"""
八款封面风格的构图，取自流媒体站点常见的版块：竖幅画廊、满幅大图、预告横幅、榜单长卡、详情页和轮播。

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
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from app.log import logger
from app.plugins.mediacovergenerator.style.catalog import style_info
from app.plugins.mediacovergenerator.style.typography import SUPERSAMPLE, TitleLayout, load_font, outline_from_mask
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


def polygon_mask(size, points, supersample=3):
    """抗锯齿的多边形遮罩，顶点用画布坐标。"""
    mask = Image.new("L", (size[0] * supersample, size[1] * supersample), 0)
    ImageDraw.Draw(mask).polygon([(x * supersample, y * supersample) for x, y in points], fill=255)
    return mask.resize(size, Image.Resampling.LANCZOS)


def diagonal_wipe(size, progress, slant=0.3, feather=0.14):
    """从左向右的斜向划变遮罩，progress 从 0 到 1 时由全黑变全白。"""
    x_axis, y_axis = axes(size)
    travel = x_axis + (1.0 - y_axis) * slant
    edge = progress * (1.0 + slant + feather) - feather
    return Image.fromarray((smooth((edge - travel) / feather + 1.0) * 255.0).astype(np.uint8))


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


def arrow_chip(size, direction):
    """白底圆角的左右箭头。"""
    scale = 4
    size = int(size)
    chip = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(chip)
    full = size * scale
    draw.rounded_rectangle([(0, 0), (full - 1, full - 1)], radius=int(full * 0.26), fill=GHOST_WHITE + (255,))
    arm = full * 0.17
    tip = full * (0.40 if direction < 0 else 0.60)
    back = tip + arm * 0.8 * (1 if direction < 0 else -1)
    draw.line([(back, full / 2 - arm), (tip, full / 2), (back, full / 2 + arm)], fill=CARD + (255,),
              width=max(2, int(full * 0.075)), joint="curve")
    return chip.resize((size, size), Image.Resampling.LANCZOS)


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
                    main_factor=1.0):
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
            layer.alpha_composite(main.render((width, height), margin, margin - main.offset_y,
                                              main_fill=main_fill + (255,), shadow=shadow))
        row_y = margin + main.main_height + gap
        cursor = float(margin)
        for item in items:
            layer.alpha_composite(item, (int(cursor), int(row_y + (row_h - item.height) / 2.0)))
            cursor += item.width + 18 * s
        if text:
            color = sub_fill or SILVER
            layer.alpha_composite(text.render((width, height), cursor, row_y - text.offset_y,
                                              sub_fill=color + (255,), shadow=shadow))
        return layer, margin, ink_h


# ---------------------------------------------------------------- 风格 1：序幕


class Gallery(Cover):
    """满屏竖幅画廊，一幅展开。标题在展开的画幅里，随它一起移动。"""

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
        start, finish = self._block_x(active), self._block_x(upcoming)
        composite_at(canvas, self.block, start + (finish - start) * progress, self.block_y)
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
        current, following, progress = beats(phase, self.count)
        image = self.frames[current]
        if progress > 0:
            image = Image.composite(self.frames[following], image, diagonal_wipe(self.size, progress))
        canvas = image.convert("RGBA")
        canvas.alpha_composite(self.overlay)
        card_now, card_next = self.cards[following], self.cards[(following + 1) % self.count]
        card = card_now if progress <= 0 else Image.blend(card_now, card_next, progress)
        canvas.paste(card, self.card_box, self.card_mask)
        return canvas


# ---------------------------------------------------------------- 风格 3：流光


class Glint(Cover):
    """黑底上的超大标题：左边白字，右边透出画面。分界线来回走动，走到尽头时悄悄换图。"""

    SLANT = 0.22

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        canvas = self.backdrop(0, 0.2).convert("RGBA")
        x_axis, y_axis = axes(self.size)
        vignette = np.sqrt(((x_axis - 0.5) / 0.62) ** 2 + ((y_axis - 0.5) / 0.62) ** 2)
        canvas.alpha_composite(rgba_from_alpha(INK, np.clip(0.55 + vignette * 0.45, 0, 1)))

        main = self.layout(W * 0.86, part="main")
        sub = self.layout(W * 0.8, part="sub")
        has_sub = self.has_sub()
        gap = self.spec.spacing * s if has_sub else 0.0
        total = main.main_height + gap + (sub.height if has_sub else 0.0)
        top = (H - total) / 2.0 + self.spec.offset_y * s
        if has_sub:
            canvas.alpha_composite(sub.render(self.size, W * 0.1, top - sub.offset_y, align="center",
                                              sub_fill=GHOST_WHITE + (235,)))
        main_top = top + (sub.height + gap if has_sub else 0.0)
        self.mask = main.render(self.size, W * 0.07, main_top - main.offset_y, align="center").getchannel("A")
        self.box = self.mask.getbbox() or (int(W * 0.2), int(H * 0.3), int(W * 0.8), int(H * 0.7))
        # 文字外缘一圈淡描边，暗图透进字里也能看清字形
        ring = Image.new("RGBA", self.size, GHOST_WHITE + (0,))
        ring.putalpha(outline_from_mask(self.mask, max(1, round(1.4 * s)), 0.3))
        canvas.alpha_composite(ring)
        self.base = canvas.convert("RGB")

        self.span = (self.box[2] - self.box[0], self.box[3] - self.box[1])
        self.text_mask = np.asarray(self.mask.crop(self.box), dtype=np.float32) / 255.0
        self.pan_w = int(self.span[0] * 1.22)
        self.fills = [lift(self.fit(index, (self.pan_w, self.span[1])), floor=88, saturation=1.45)
                      for index in range(self.count)]
        columns, rows = np.meshgrid(np.arange(self.span[0], dtype=np.float32), np.arange(self.span[1], dtype=np.float32))
        self.columns, self.rows = columns, rows
        self.feather = max(1.0, self.span[0] * 0.012)

    def frame(self, phase):
        current, following, progress = beats(phase, self.count, hold=0.62)
        local = (phase * self.count) % 1.0
        shown = current if progress < 0.5 else following
        # 分界线先向右走到尽头（全白），换图后再退回一半的位置
        boundary = 0.5 + 0.68 * math.sin(math.pi * progress)
        edge = boundary * self.span[0] + (self.rows - self.span[1] / 2.0) * self.SLANT
        reveal = np.clip((self.columns - edge) / self.feather + 0.5, 0.0, 1.0)

        # 画面在字里慢慢平移，换图时已经全白，看不出跳变
        pan = local if progress < 0.5 else local - 1.0
        offset = int(round((self.pan_w - self.span[0]) * (0.5 + 0.5 * pan)))
        fill = self.fills[shown].crop((offset, 0, offset + self.span[0], self.span[1]))

        canvas = self.base.copy()
        canvas.paste(GHOST_WHITE, self.box[:2], self.mask.crop(self.box))
        alpha = Image.fromarray((self.text_mask * reveal * 255.0).astype(np.uint8))
        canvas.paste(fill, self.box[:2], alpha)
        return canvas


# ---------------------------------------------------------------- 风格 4：预告


class Trailer(Cover):
    """虚化背景上的预告横幅：左上角斜角飘带，右边平行四边形的大图和一条细斜栏。"""

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        top, bottom = int(H * 0.075), int(H * 0.925)
        skew = W * 0.098
        left, right = W * 0.492, W * 0.872
        main_points = [(left, top), (right, top), (right - skew, bottom), (left - skew, bottom)]
        bar_left, bar_right = right + W * 0.022, right + W * 0.06
        bar_points = [(bar_left, top), (bar_right, top), (bar_right - skew, bottom), (bar_left - skew, bottom)]
        self.window_mask = polygon_mask(self.size, main_points)
        self.bar_mask = polygon_mask(self.size, bar_points)
        self.full = [ImageEnhance.Contrast(self.fit(index, self.size)).enhance(1.05) for index in range(self.count)]
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
        current, following, progress = beats(phase, self.count)
        ground, window = self.grounds[current], self.full[current]
        bar = self.full[following]
        if progress > 0:
            wipe = diagonal_wipe(self.size, progress, slant=0.1, feather=0.1)
            ground = Image.blend(ground, self.grounds[following], progress)
            window = Image.composite(self.full[following], window, wipe)
            bar = Image.blend(bar, self.full[(following + 1) % self.count], progress)
        canvas = ground.convert("RGBA")
        canvas.alpha_composite(self.shade)
        canvas.paste(window, (0, 0), self.window_mask)
        canvas.paste(bar, (0, 0), self.bar_mask)
        canvas.alpha_composite(self.overlay)
        return canvas


# ---------------------------------------------------------------- 风格 5：拼贴


class Bento(Cover):
    """错落的圆角拼贴，左下角一张深色卡片放标题。每次只有一格换图。"""

    # (列, 上边, 下边)，上下边是可用高度的比例
    CELLS = ((0, 0.0, 0.625), (1, 0.0, 0.25), (1, 0.25, 0.625), (2, 0.0, 0.43), (2, 0.43, 1.0),
             (3, 0.0, 0.64), (3, 0.64, 1.0))
    TITLE = (0, 1, 0.625, 1.0)

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        gap = int(W * 0.0115)
        margin = gap
        column_w = (W - margin * 2 - gap * 3) / 4.0
        usable = H - margin * 2
        radius = int(30 * s)

        def box_of(first, last, upper, lower):
            x0 = margin + first * (column_w + gap)
            x1 = margin + last * (column_w + gap) + column_w
            y0 = margin + usable * upper + (gap / 2.0 if upper > 0 else 0.0)
            y1 = margin + usable * lower - (gap / 2.0 if lower < 1 else 0.0)
            return tuple(int(round(value)) for value in (x0, y0, x1, y1))

        ground = np.full((H, W, 3), self.base_color(), dtype=np.float32)
        if not self.animated:
            # 静态图加一点细微的颗粒，避免大面积纯黑显得单薄
            ground += np.random.default_rng(1492).normal(0.0, 2.0, (H, W))[..., None]
        canvas = Image.fromarray(np.clip(ground, 0, 255).astype(np.uint8)).convert("RGBA")

        first, last, upper, lower = self.TITLE
        title_box = box_of(first, last, upper, lower)
        plate_size = (title_box[2] - title_box[0], title_box[3] - title_box[1])
        plate = Image.new("RGBA", plate_size, self.card_color() + (255,))
        plate.alpha_composite(border_layer(plate_size, radius, (255, 255, 255, 20), 2 * s))
        canvas.paste(plate, title_box[:2], rounded_mask(plate_size, radius))

        pad = plate_size[0] * 0.075
        # 卡片高度有限，长标题折行后放不下时继续缩小主标题
        for factor in (1.0, 0.88, 0.76, 0.64, 0.54, 0.46):
            block, block_margin, ink_h = self.title_block(plate_size[0] - pad * 2, sub_style="text",
                                                          sub_fill=self.accent, main_factor=factor)
            if ink_h <= plate_size[1] * 0.84:
                break
        composite_at(canvas, block, title_box[0] + pad - block_margin,
                     title_box[1] + (plate_size[1] - ink_h) / 2.0 - block_margin + self.spec.offset_y * s)

        self.base = canvas.convert("RGB")
        self.cells = [box_of(column, column, top, bottom) for column, top, bottom in self.CELLS]
        self.masks, self.pictures = {}, []
        total = len(self.cells)
        # 每格在两张图之间来回切换：前半圈依次换成 B，后半圈依次换回 A，首尾自然相接
        shift = max(1, min(self.count - 1, total)) if self.count > 1 else 0
        for index, box in enumerate(self.cells):
            size = (box[2] - box[0], box[3] - box[1])
            self.masks[index] = rounded_mask(size, radius)
            self.pictures.append((self.fit(index, size), self.fit(index + shift, size)))
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


# ---------------------------------------------------------------- 风格 6：榜单


class Ranking(Cover):
    """深色长卡：竖排副标题、透出画面的大字和圆角大图并排。"""

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        canvas = Image.new("RGBA", self.size, self.base_color() + (255,))
        card_box = (int(W * 0.022), int(H * 0.04), W - int(W * 0.022), H - int(H * 0.04))
        card_size = (card_box[2] - card_box[0], card_box[3] - card_box[1])
        canvas.paste(self.card_color() + (255,), card_box[:2], rounded_mask(card_size, int(44 * s)))

        pad = int(W * 0.016)
        self.photo_box = (int(W * 0.615), card_box[1] + pad, card_box[2] - pad, card_box[3] - pad)
        self.photo_size = (self.photo_box[2] - self.photo_box[0], self.photo_box[3] - self.photo_box[1])
        self.photo_mask = rounded_mask(self.photo_size, int(32 * s))

        has_sub = self.has_sub()
        zone_left = W * (0.098 if has_sub else 0.055)
        zone_right = W * 0.595
        center_y = H * 0.5 + self.spec.offset_y * s

        main = self.layout(zone_right - zone_left, part="main")
        self.mask = main.render_centered(self.size, (zone_left + zone_right) / 2.0, center_y).getchannel("A")
        box = self.mask.getbbox() or (int(zone_left), int(H * 0.3), int(zone_right), int(H * 0.7))
        self.text_box = box

        # 文字外缘一圈淡描边，暗图透进字里也不会和底色糊在一起
        ring = Image.new("RGBA", self.size, GHOST_WHITE + (0,))
        ring.putalpha(outline_from_mask(self.mask, max(1, round(1.4 * s)), 0.16))
        canvas.alpha_composite(ring)

        if has_sub:
            vertical = self.layout(H * 0.76, part="sub").rotated(fill=SILVER + (255,))
            canvas.alpha_composite(vertical, (int(W * 0.06 - vertical.width / 2.0), int(H / 2.0 - vertical.height / 2.0)))

        self.base = canvas.convert("RGB")
        self.pan_w = int((box[2] - box[0]) * 1.24)
        self.pan_h = box[3] - box[1]
        self.fills = [lift(self.fit(index, (self.pan_w, self.pan_h)), floor=72, saturation=1.45) for index in range(self.count)]
        self.photos = [ImageEnhance.Contrast(self.fit(index, self.photo_size)).enhance(1.05) for index in range(self.count)]

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
            photo = Image.composite(self.photos[following], photo, diagonal_wipe(self.photo_size, progress, slant=0.2))
        canvas.paste(fill, self.text_box[:2], self.mask.crop(self.text_box))
        canvas.paste(photo, self.photo_box[:2], self.photo_mask)
        return canvas


# ---------------------------------------------------------------- 风格 7：详情


class Detail(Cover):
    """虚化背景上一张带播放键的大卡，右边标题和播放按钮，下方露出一排同系列的缩略图。"""

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        self.grounds = [self.backdrop(index, 0.4) for index in range(self.count)]
        _x_axis, y_axis = axes(self.size)
        shade = 0.42 + smooth((y_axis - 0.45) / 0.45) * 0.56
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
        current, following, progress = beats(phase, self.count)
        ground = self.grounds[current] if progress <= 0 else Image.blend(self.grounds[current], self.grounds[following], progress)
        canvas = ground.convert("RGBA")
        canvas.alpha_composite(self.shade)
        canvas.alpha_composite(self.overlay)
        hero = self.heroes[current]
        if progress > 0:
            hero = Image.composite(self.heroes[following], hero, diagonal_wipe(self.hero_size, progress, slant=0.2))
        canvas.paste(hero, self.hero_box, self.hero_mask)
        canvas.alpha_composite(self.badge, self.badge_at)
        step = self.thumb_size[0] + self.thumb_gap
        for slot in range(6):
            x_pos = int(round(self.thumb_x + (slot - progress) * step))
            if x_pos >= self.W or x_pos + self.thumb_size[0] <= 0:
                continue
            canvas.paste(self.thumbs[(current + 1 + slot) % self.count], (x_pos, self.thumb_y), self.thumb_mask)
        return canvas


# ---------------------------------------------------------------- 风格 8：轮播


class Carousel(Cover):
    """栏目标题配左右箭头，下方一排带序号的横版卡片，两端露出半张。卡片整排滑动。"""

    def prepare(self):
        W, H, s = self.W, self.H, self.s
        self.ground = Image.new("RGB", self.size, self.base_color())
        self.overlay = Image.new("RGBA", self.size, (0, 0, 0, 0))

        margin_x = W * 0.036
        block, margin, ink_h = self.title_block(W * 0.74, sub_style="text")
        title_top = H * 0.07 + self.spec.offset_y * s
        composite_at(self.overlay, block, margin_x - margin, title_top - margin)

        arrow = int(92 * s)
        arrow_gap = int(18 * s)
        arrow_y = int(H * 0.075)
        right = int(W - margin_x)
        self.overlay.alpha_composite(arrow_chip(arrow, 1), (right - arrow, arrow_y))
        self.overlay.alpha_composite(arrow_chip(arrow, -1), (right - arrow * 2 - arrow_gap, arrow_y))

        self.card_w = int(W * 0.452)
        self.card_h = int(H * 0.585)
        self.gap = int(W * 0.019)
        row_top = max(int(H * 0.36), int(title_top + ink_h + 44 * s))
        self.row_y = min(row_top, H - int(H * 0.05) - self.card_h)
        self.row_x = margin_x
        radius = int(36 * s)
        self.mask = rounded_mask((self.card_w, self.card_h), radius)

        notch_w, notch_h = int(142 * s), int(92 * s)
        font = load_font(self.fonts[1], 54 * s * SUPERSAMPLE)
        self.cards = []
        for index in range(self.count):
            card = ImageEnhance.Contrast(self.fit(index, (self.card_w, self.card_h))).enhance(1.05).convert("RGBA")
            notch = Image.new("RGBA", (notch_w * SUPERSAMPLE, notch_h * SUPERSAMPLE), (0, 0, 0, 0))
            draw = ImageDraw.Draw(notch)
            # 只有右下角是圆角，贴着卡片的左上角
            draw.rounded_rectangle([(-200, -200), (notch.width - 1, notch.height - 1)],
                                   radius=int(26 * s * SUPERSAMPLE), fill=self.card_color() + (255,))
            draw.text((notch.width / 2.0, notch.height / 2.0), f"{index + 1:02d}", font=font,
                      fill=GHOST_WHITE + (255,), anchor="mm")
            card.alpha_composite(notch.resize((notch_w, notch_h), Image.Resampling.LANCZOS))
            card.alpha_composite(border_layer((self.card_w, self.card_h), radius, (255, 255, 255, 26), 2 * s))
            self.cards.append(card.convert("RGB"))

    def frame(self, phase):
        current, _following, progress = beats(phase, self.count, hold=0.74)
        canvas = self.ground.copy()
        step = self.card_w + self.gap
        for slot in range(4):
            x_pos = int(round(self.row_x + (slot - progress) * step))
            if x_pos >= self.W or x_pos + self.card_w <= 0:
                continue
            canvas.paste(self.cards[(current + slot) % self.count], (x_pos, self.row_y), self.mask)
        canvas = canvas.convert("RGBA")
        canvas.alpha_composite(self.overlay)
        return canvas


DESIGNS = {1: Gallery, 2: Titan, 3: Glint, 4: Trailer, 5: Bento, 6: Ranking, 7: Detail, 8: Carousel}


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
