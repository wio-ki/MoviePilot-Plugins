"""
封面标题的测量与绘制，所有风格共用。

- 放不下时先缩小、再折行，长标题不会盖住画面或超出画布
- 上下位置和主副标题间距都按笔画的实际范围计算，不受字体行高影响，毛笔字也能对齐居中
- 内部尺寸乘以 TEXT_SUPERSAMPLE，绘制后再缩小，低分辨率动图上的文字也能保持清晰
"""
from PIL import Image, ImageDraw, ImageFilter, ImageFont

TEXT_SUPERSAMPLE = 2
LINE_HEIGHT = 1.06
# 主标题缩到这个比例还放不下时才折行
MAIN_MIN_SCALE = 0.56
# 主标题字数越少，字距越开：两个字的标题撑得起更宽的字距
MAIN_TRACKING_BY_LENGTH = {1: 0.0, 2: 1.6, 3: 1.0}
MAIN_TRACKING_LONG = 0.7
# 字距是为方块字设计的，拉丁字母只保留一小部分
MAIN_TRACKING_LATIN = 0.25


def load_font(path, size):
    return ImageFont.truetype(str(path), max(1, int(round(size))))


def tracked_width(font, text, tracking):
    return sum(font.getlength(char) for char in text) + tracking * max(0, len(text) - 1)


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
    words = text.split(" ")
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and tracked_width(font, candidate, tracking) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def main_tracking_ratio(text, base):
    """把设置里的字距换算成这个标题实际使用的字距，单位是字号的倍数。"""
    base = max(0.0, float(base))
    length = len(text.replace(" ", ""))
    ratio = base * MAIN_TRACKING_BY_LENGTH.get(length, MAIN_TRACKING_LONG)
    if not any(ord(char) >= 0x2E80 for char in text):
        ratio *= MAIN_TRACKING_LATIN
    return ratio


def soft_shadow(color, canvas_height, alpha=110):
    """封面通用的柔和投影，返回 render 的 shadow 参数。"""
    return tuple(color[:3]) + (alpha,), canvas_height * 0.012, canvas_height * 0.005


class TitleLayout:
    """
    测量并绘制主副标题。

    font_offset 依次是主标题上下偏移、主副标题间距、副标题行距、主标题字距、副标题字距。
    前三项以 1080p 为基准，由 scale 换算到当前画布；字距是字号的倍数，可以省略，
    省略时主标题不加字距，副标题用参数 tracking。
    """

    def __init__(self, title, font_path, font_size, font_offset, scale, max_width,
                 tracking=0.10, uppercase_subtitle=True, sub_indent=0.0):
        main_text, sub_text = title
        main_font_path, sub_font_path = font_path
        main_size, sub_size = float(font_size[0]), float(font_size[1])
        ss = TEXT_SUPERSAMPLE
        self.offset_y = float(font_offset[0]) * scale
        self.spacing = max(0.0, float(font_offset[1])) * scale * ss
        self.line_gap = max(0.0, float(font_offset[2])) * scale * 0.5 * ss
        self.max_width = float(max_width) * ss
        self.sub_indent = max(0.0, float(sub_indent)) * ss
        self.main_text = str(main_text or "").strip()
        self.sub_text = str(sub_text or "").strip()
        if uppercase_subtitle:
            self.sub_text = self.sub_text.upper()
        base_tracking = float(font_offset[3]) if len(font_offset) > 3 else 0.0
        if len(font_offset) > 4:
            tracking = max(0.0, float(font_offset[4]))
        self.main_tracking_ratio = main_tracking_ratio(self.main_text, base_tracking)

        main_size = max(1.0, main_size) * ss
        sub_size = max(1.0, sub_size) * ss

        self.main_lines, self.main_font = self._fit_main(main_font_path, main_size)
        self.main_tracking = self.main_font.size * self.main_tracking_ratio
        self.sub_font = load_font(sub_font_path, sub_size)
        self.tracking = sub_size * tracking
        self.sub_lines = self._fit_sub(sub_font_path, sub_size)

        self.width = max(
            [tracked_width(self.main_font, line, self.main_tracking) for line in self.main_lines]
            + [tracked_width(self.sub_font, line, self.tracking) + self.sub_indent for line in self.sub_lines]
            + [1.0]
        ) / ss
        self._measure()

    def _fits_main(self, font, lines, tracking_ratio):
        tracking = font.size * tracking_ratio
        return max(tracked_width(font, line, tracking) for line in lines) <= self.max_width

    def _fit_main(self, path, size):
        text = self.main_text
        if not text:
            return [], load_font(path, size)
        full_ratio = self.main_tracking_ratio
        current = size
        while current > size * MAIN_MIN_SCALE:
            # 越接近缩放下限字距越紧，长标题尽量排在一行
            progress = (size - current) / (size * (1.0 - MAIN_MIN_SCALE))
            ratio = full_ratio * (1.0 - 0.8 * progress)
            font = load_font(path, current)
            if self._fits_main(font, [text], ratio):
                self.main_tracking_ratio = ratio
                return [text], font
            current *= 0.94
        # 缩到下限仍放不下时折成两行，再按最长行继续收缩
        lines = split_title(text)
        current = size * 0.78
        while True:
            font = load_font(path, current)
            if self._fits_main(font, lines, full_ratio) or current < 8:
                return lines, font
            current *= 0.94

    def _fit_sub(self, path, size):
        if not self.sub_text:
            return []
        limit = self.max_width - self.sub_indent
        base_tracking = self.tracking / size
        lines = [self.sub_text]
        # 先在小幅缩放内争取单行，放不下才允许折成两行
        for line_limit, floor in ((1, 0.8), (2, 0.4)):
            current = size
            while current >= size * floor:
                self.sub_font = load_font(path, current)
                self.tracking = current * base_tracking
                lines = wrap_words(self.sub_font, self.sub_text, self.tracking, limit)
                widest = max(tracked_width(self.sub_font, line, self.tracking) for line in lines)
                if len(lines) <= line_limit and widest <= limit:
                    return lines
                current *= 0.94
        return lines

    def _measure(self):
        """算出每行基线到文字块顶部的距离，以及文字块的总高度。"""
        self.main_baselines, self.sub_baselines = [], []
        cursor = 0.0
        step = self.main_font.size * LINE_HEIGHT
        for index, line in enumerate(self.main_lines):
            top, bottom = self._ink(self.main_font, line)
            if index == 0:
                cursor = -top
            else:
                cursor += step
            self.main_baselines.append(cursor)
            ink_bottom = cursor + bottom

        block_bottom = ink_bottom if self.main_lines else 0.0
        self.sub_top = block_bottom
        self.sub_bottom = block_bottom
        if self.sub_lines:
            step = self.sub_font.size * LINE_HEIGHT + self.line_gap
            for index, line in enumerate(self.sub_lines):
                top, bottom = self._ink(self.sub_font, line)
                if index == 0:
                    self.sub_top = block_bottom + (self.spacing if self.main_lines else 0.0)
                    cursor = self.sub_top - top
                else:
                    cursor += step
                self.sub_baselines.append(cursor)
                ink_bottom = cursor + bottom
            self.sub_bottom = ink_bottom
            block_bottom = ink_bottom
        self.height = block_bottom / TEXT_SUPERSAMPLE

    @staticmethod
    def _ink(font, text):
        box = font.getbbox(text, anchor="ls")
        return box[1], box[3]

    def sub_box(self, y):
        """副标题在画布上的上下边界，用于绘制配套的装饰。"""
        top = y + self.offset_y + self.sub_top / TEXT_SUPERSAMPLE
        bottom = y + self.offset_y + self.sub_bottom / TEXT_SUPERSAMPLE
        return top, bottom

    def _line_x(self, line_width, x_origin, align):
        if align == "center":
            return x_origin + (self.max_width - line_width) / 2.0
        return x_origin

    @staticmethod
    def _draw_tracked(draw, pen, baseline, line, font, tracking, fill):
        for char in line:
            draw.text((pen, baseline), char, font=font, fill=fill, anchor="ls")
            pen += font.getlength(char) + tracking

    def render(self, size, x, y, align="left", main_fill=(255, 255, 255, 245),
               sub_fill=(255, 255, 255, 205), shadow=None):
        """在 (x, y) 处绘制文字块并返回与画布同尺寸的 RGBA 图层；center 对齐时 x 为文字区左边界。"""
        ss = TEXT_SUPERSAMPLE
        y = y + self.offset_y
        # 只在文字所在区域超采样，避免高分辨率下占用大量内存
        pad = int(max(self.main_font.size, self.sub_font.size) / ss * 0.4) + 2
        left = max(0, int(x) - pad)
        top = max(0, int(y) - pad)
        right = min(size[0], int(x + self.max_width / ss) + pad + 1)
        bottom = min(size[1], int(y + self.height) + pad + 1)
        result = Image.new("RGBA", size, (0, 0, 0, 0))
        if right <= left or bottom <= top:
            return result

        layer = Image.new("RGBA", ((right - left) * ss, (bottom - top) * ss), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        x_origin = (x - left) * ss
        y_origin = (y - top) * ss

        for line, baseline in zip(self.main_lines, self.main_baselines):
            pen = self._line_x(tracked_width(self.main_font, line, self.main_tracking), x_origin, align)
            self._draw_tracked(draw, pen, y_origin + baseline, line, self.main_font, self.main_tracking, main_fill)

        for line, baseline in zip(self.sub_lines, self.sub_baselines):
            line_width = tracked_width(self.sub_font, line, self.tracking)
            pen = self._line_x(line_width, x_origin, align) + (self.sub_indent if align != "center" else 0.0)
            self._draw_tracked(draw, pen, y_origin + baseline, line, self.sub_font, self.tracking, sub_fill)

        layer = layer.resize((right - left, bottom - top), Image.Resampling.LANCZOS)
        full = Image.new("RGBA", size, (0, 0, 0, 0))
        full.paste(layer, (left, top))
        if not shadow:
            return full

        shadow_color, radius, offset = shadow
        alpha = full.getchannel("A").filter(ImageFilter.GaussianBlur(max(0.6, radius)))
        ink = Image.new("RGBA", size, tuple(shadow_color[:3]) + (0,))
        ink.putalpha(alpha.point(lambda value: int(value * shadow_color[3] / 255.0)))
        result.alpha_composite(ink, (int(offset), int(offset)))
        result.alpha_composite(full)
        return result

    def render_centered(self, size, center_x, center_y, **kwargs):
        """以 (center_x, center_y) 为中心绘制文字块。"""
        x = center_x - self.max_width / TEXT_SUPERSAMPLE / 2.0
        y = center_y - self.height / 2.0
        return self.render(size, x, y, align="center", **kwargs)
