"""
封面标题的测量与绘制。

- 放不下时先缩小、再折行，长标题不会盖住画面或超出画布
- 上下位置和主副标题间距都按笔画的实际范围计算，不受字体行高影响
- 左右也按笔画对齐：去掉首字的字侧留白，标题才能和旁边的线条、图片对齐
- 内部尺寸乘以 SUPERSAMPLE，绘制后再缩小，低分辨率动图上的文字也能保持清晰
"""
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

SUPERSAMPLE = 2
# 主标题缩到这个比例还放不下时才折行
MAIN_MIN_SCALE = 0.56
# 主标题字数越少，字距越开：两个字的标题撑得起更宽的字距
WIDE_TRACKING_BY_LENGTH = {1: 0.0, 2: 1.5, 3: 1.15}
_GLYPH_SPANS = {}


def load_font(path, size):
    return ImageFont.truetype(str(path), max(1, int(round(size))))


def is_cjk_text(text):
    return any(ord(char) >= 0x2E80 for char in str(text or ""))


def _advances(font, text, tracking):
    """每个字的步进宽度，含字距和字偶距。逐字绘制时用它定位。"""
    result = []
    for index, char in enumerate(text):
        advance = font.getlength(char) + tracking
        if index + 1 < len(text):
            pair = text[index:index + 2]
            advance += font.getlength(pair) - font.getlength(pair[0]) - font.getlength(pair[1])
        result.append(advance)
    return result


def tracked_width(font, text, tracking):
    if not text:
        return 0.0
    return sum(_advances(font, text, tracking)) - tracking


def split_title(text):
    words = text.split(" ")
    if len(words) > 1:
        best, best_gap = None, None
        for index in range(1, len(words)):
            first, second = " ".join(words[:index]), " ".join(words[index:])
            gap = abs(len(first) - len(second))
            if best_gap is None or gap < best_gap:
                best, best_gap = (first, second), gap
        return list(best)
    middle = (len(text) + 1) // 2
    return [text[:middle], text[middle:]]


def wrap_words(font, text, tracking, max_width):
    lines, current = [], ""
    for word in text.split(" "):
        candidate = f"{current} {word}".strip()
        if current and tracked_width(font, candidate, tracking) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


class TitleLayout:
    """
    按 TypeSpec 测量并绘制主副标题。

    part 为 main 或 sub 时只排其中一部分，构图可以把主副标题放在不同位置。
    size_factor 用来在同一规格下整体放大缩小。
    """

    def __init__(self, title, fonts, spec, scale, max_width, part="both", size_factor=1.0):
        main_text, sub_text = title
        ss = SUPERSAMPLE
        self.main_text = str(main_text or "").strip() if part != "sub" else ""
        self.sub_text = str(sub_text or "").strip() if part != "main" else ""
        self.line_height = spec.line_height
        self.spacing = max(0.0, spec.spacing) * scale * ss
        self.line_gap = max(0.0, spec.sub_line_gap) * scale * ss
        self.offset_y = spec.offset_y * scale
        self.max_width = float(max_width) * ss

        is_cjk = is_cjk_text(self.main_text)
        if spec.main_upper and not is_cjk:
            self.main_text = self.main_text.upper()
        if spec.sub_upper:
            self.sub_text = self.sub_text.upper()

        self.main_tracking_ratio = self._main_ratio(spec, is_cjk)
        main_size = max(1.0, spec.main_size * scale * size_factor) * ss
        sub_size = max(1.0, spec.sub_size * scale * size_factor) * ss
        self.main_lines, self.main_font = self._fit_main(fonts[0], main_size)
        self.main_tracking = self.main_font.size * self.main_tracking_ratio
        self.sub_font = load_font(fonts[1], sub_size)
        self.tracking = sub_size * spec.sub_tracking
        self.sub_lines = self._fit_sub(fonts[1], sub_size)

        self.main_width = max(
            [self._line_width(self.main_font, line, self.main_tracking) for line in self.main_lines] + [0.0]
        ) / ss
        self.sub_width = max(
            [self._line_width(self.sub_font, line, self.tracking) for line in self.sub_lines] + [0.0]
        ) / ss
        self.width = max(self.main_width, self.sub_width, 1.0)
        self._measure()

    def _main_ratio(self, spec, is_cjk):
        if not is_cjk:
            return spec.main_tracking_latin
        ratio = spec.main_tracking
        if ratio > 0:
            ratio *= WIDE_TRACKING_BY_LENGTH.get(len(self.main_text.replace(" ", "")), 1.0)
        return ratio

    def _fits_main(self, font, lines, ratio):
        tracking = font.size * ratio
        return max(tracked_width(font, line, tracking) for line in lines) <= self.max_width

    def _fit_main(self, path, size):
        text = self.main_text
        if not text:
            return [], load_font(path, size)
        full_ratio = self.main_tracking_ratio
        current = size
        while current > size * MAIN_MIN_SCALE:
            # 越接近缩放下限，正字距收得越紧，长标题尽量排在一行
            progress = (size - current) / (size * (1.0 - MAIN_MIN_SCALE))
            ratio = full_ratio * (1.0 - 0.8 * progress) if full_ratio > 0 else full_ratio
            font = load_font(path, current)
            if self._fits_main(font, [text], ratio):
                self.main_tracking_ratio = ratio
                return [text], font
            current *= 0.95
        # 单个西文词不能拆开，只继续缩小
        if " " not in text and not is_cjk_text(text):
            while True:
                font = load_font(path, current)
                if self._fits_main(font, [text], full_ratio) or current < 8:
                    return [text], font
                current *= 0.95
        # 缩到下限仍放不下时折成两行，再按最长行继续收缩
        lines = split_title(text)
        current = size * 0.8
        while True:
            font = load_font(path, current)
            if self._fits_main(font, lines, full_ratio) or current < 8:
                return lines, font
            current *= 0.95

    def _fit_sub(self, path, size):
        if not self.sub_text:
            return []
        base_tracking = self.tracking / size
        lines = [self.sub_text]
        # 先在小幅缩放内争取单行，放不下才允许折成两行
        for line_limit, floor in ((1, 0.72), (2, 0.4)):
            current = size
            while current >= size * floor:
                self.sub_font = load_font(path, current)
                self.tracking = current * base_tracking
                lines = wrap_words(self.sub_font, self.sub_text, self.tracking, self.max_width)
                widest = max(tracked_width(self.sub_font, line, self.tracking) for line in lines)
                if len(lines) <= line_limit and widest <= self.max_width:
                    return lines
                current *= 0.95
        return lines

    @staticmethod
    def _ink(font, text):
        box = font.getbbox(text, anchor="ls")
        return box[1], box[3]

    @staticmethod
    def _glyph_span(font, char):
        """单字笔画的左右范围。getbbox 的横向范围是步进宽度，不含字侧留白，只有画出来才量得准。"""
        key = (font.path, font.size, char)
        if key not in _GLYPH_SPANS:
            size = int(font.size)
            canvas = Image.new("L", (size * 3, size * 3), 0)
            ImageDraw.Draw(canvas).text((size, size * 2), char, font=font, fill=255, anchor="ls")
            box = canvas.getbbox()
            _GLYPH_SPANS[key] = (box[0] - size, box[2] - size) if box else (0, font.getlength(char))
        return _GLYPH_SPANS[key]

    @classmethod
    def _bearings(cls, font, line):
        """首字左侧和末字右侧的空白，用于按笔画对齐。"""
        left = cls._glyph_span(font, line[0])[0]
        last = line[-1]
        right = font.getlength(last) - cls._glyph_span(font, last)[1]
        return left, right

    def _line_width(self, font, line, tracking):
        left, right = self._bearings(font, line)
        return tracked_width(font, line, tracking) - left - right

    def _measure(self):
        """算出每行基线到文字块顶部的距离，以及文字块的总高度。"""
        self.main_baselines, self.sub_baselines = [], []
        cursor = 0.0
        ink_bottom = 0.0
        step = self.main_font.size * self.line_height
        for index, line in enumerate(self.main_lines):
            top, bottom = self._ink(self.main_font, line)
            cursor = -top if index == 0 else cursor + step
            self.main_baselines.append(cursor)
            ink_bottom = cursor + bottom

        self.main_bottom_ss = ink_bottom
        block_bottom = ink_bottom
        self.sub_top_ss = block_bottom
        if self.sub_lines:
            step = self.sub_font.size * 1.06 + self.line_gap
            for index, line in enumerate(self.sub_lines):
                top, bottom = self._ink(self.sub_font, line)
                if index == 0:
                    self.sub_top_ss = block_bottom + (self.spacing if self.main_lines else 0.0)
                    cursor = self.sub_top_ss - top
                else:
                    cursor += step
                self.sub_baselines.append(cursor)
                ink_bottom = cursor + bottom
            block_bottom = ink_bottom
        self.height = block_bottom / SUPERSAMPLE
        self.main_height = self.main_bottom_ss / SUPERSAMPLE
        self.sub_top = self.sub_top_ss / SUPERSAMPLE

    def _line_pen(self, font, line, tracking, x_origin, align):
        left, right = self._bearings(font, line)
        ink_width = tracked_width(font, line, tracking) - left - right
        if align == "center":
            return x_origin + (self.max_width - ink_width) / 2.0 - left
        if align == "right":
            return x_origin + self.max_width - ink_width - left
        return x_origin - left

    @staticmethod
    def _draw_line(draw, pen, baseline, line, font, tracking, fill):
        for char, advance in zip(line, _advances(font, line, tracking)):
            draw.text((pen, baseline), char, font=font, fill=fill, anchor="ls")
            pen += advance

    def render(self, size, x, y, align="left", main_fill=(255, 255, 255, 255),
               sub_fill=(255, 255, 255, 255), shadow=None):
        """
        在 (x, y) 处绘制文字块，返回与画布同尺寸的 RGBA 图层。
        x 是文字区左边界，文字区宽度为 max_width，center、right 都在这个范围内对齐。
        shadow 为 (颜色 RGBA, 模糊半径, 偏移)。
        """
        ss = SUPERSAMPLE
        y = y + self.offset_y
        # 只在文字所在区域超采样，避免高分辨率下占用大量内存
        pad = int(max(self.main_font.size, self.sub_font.size) / ss * 0.4) + 2
        left = max(0, int(x) - pad)
        top = max(0, int(y) - pad)
        right = min(size[0], int(x + self.max_width / ss) + pad + 1)
        bottom = min(size[1], int(y + self.height) + pad + 1)
        full = Image.new("RGBA", size, (0, 0, 0, 0))
        if right <= left or bottom <= top:
            return full

        layer = Image.new("RGBA", ((right - left) * ss, (bottom - top) * ss), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        x_origin = (x - left) * ss
        y_origin = (y - top) * ss

        for line, baseline in zip(self.main_lines, self.main_baselines):
            pen = self._line_pen(self.main_font, line, self.main_tracking, x_origin, align)
            self._draw_line(draw, pen, y_origin + baseline, line, self.main_font, self.main_tracking, main_fill)
        for line, baseline in zip(self.sub_lines, self.sub_baselines):
            pen = self._line_pen(self.sub_font, line, self.tracking, x_origin, align)
            self._draw_line(draw, pen, y_origin + baseline, line, self.sub_font, self.tracking, sub_fill)

        full.paste(layer.resize((right - left, bottom - top), Image.Resampling.LANCZOS), (left, top))
        if not shadow:
            return full

        color, radius, offset = shadow
        blurred = full.getchannel("A").filter(ImageFilter.GaussianBlur(max(0.6, radius)))
        ink = Image.new("RGBA", size, tuple(color[:3]) + (0,))
        ink.putalpha(blurred.point(lambda value: int(value * color[3] / 255.0)))
        shadowed = Image.new("RGBA", size, (0, 0, 0, 0))
        shadowed.alpha_composite(ink, (int(offset), int(offset)))
        shadowed.alpha_composite(full)
        return shadowed

    def render_centered(self, size, center_x, center_y, **kwargs):
        x = center_x - self.max_width / SUPERSAMPLE / 2.0
        y = center_y - self.height / 2.0
        return self.render(size, x, y, align="center", **kwargs)

    def rotated(self, fill=(255, 255, 255, 255), clockwise=False):
        """把文字竖排：绘制后旋转 90°，返回紧贴笔画的 RGBA 小图。"""
        size = (int(self.max_width / SUPERSAMPLE) + 4, int(self.height) + 4)
        layer = self.render(size, 2, 2, align="left", main_fill=fill, sub_fill=fill)
        box = layer.getbbox()
        if box:
            layer = layer.crop(box)
        return layer.transpose(Image.Transpose.ROTATE_270 if clockwise else Image.Transpose.ROTATE_90)


def outline_from_mask(mask, width, alpha):
    """沿文字外缘生成一圈描边的透明度，暗图透进镂空字时靠它保住字形。"""
    grown = mask.filter(ImageFilter.MaxFilter(max(3, int(width) * 2 + 1)))
    return ImageChops.subtract(grown, mask).point(lambda value: int(value * alpha))
