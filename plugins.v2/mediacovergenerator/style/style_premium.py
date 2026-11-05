"""
高级封面风格（5~8），每款都有静态与动态两个版本。

- 风格 5「影院」：满幅剧照 + 色调压暗 + 左下角大标题
- 风格 6「磨砂」：满幅剧照 + 居中毛玻璃标题牌
- 风格 7「长廊」：透视海报墙 + 左侧渐隐标题区（多图）
- 风格 8「刊物」：纸张底色 + 杂志式排版 + 右侧画框

静态版返回 JPEG 的 base64，动态版返回 APNG/GIF 的 base64，失败时返回 False。
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
from app.plugins.mediacovergenerator.style.title_layout import TEXT_SUPERSAMPLE, TitleLayout
from app.plugins.mediacovergenerator.utils.color_helper import ColorHelper

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")


# ---------------------------------------------------------------- 基础工具


def _smoothstep(values):
    values = np.clip(values, 0.0, 1.0)
    return values * values * (3.0 - 2.0 * values)


def _ease(value):
    value = max(0.0, min(1.0, value))
    return 0.5 - 0.5 * math.cos(math.pi * value)


def _mix(first, second, ratio):
    return tuple(int(round(a + (b - a) * ratio)) for a, b in zip(first, second))


def _load_rgb(path):
    with Image.open(path) as source:
        return ImageOps.exif_transpose(source).convert("RGB")


def _fit(image, size):
    """裁切到目标比例；竖版海报偏上取景，人物面部通常在上半部分。"""
    centering = (0.5, 0.16) if image.height > image.width else (0.5, 0.5)
    return ImageOps.fit(image, size, method=Image.Resampling.LANCZOS, centering=centering)


def _canvas_size(resolution_config):
    width = int(getattr(resolution_config, "width", 1920) or 1920)
    height = int(getattr(resolution_config, "height", 1080) or 1080)
    return max(1, width), max(1, height)


def _animation_size(animation_resolution):
    try:
        width, height = map(int, str(animation_resolution).split("x"))
        return max(1, width), max(1, height)
    except (TypeError, ValueError):
        return 320, 180


def _list_images(library_dir, count):
    """按文件名序号读取素材，保证多次生成的顺序稳定。"""
    folder = Path(library_dir)
    files = [path for path in folder.iterdir() if path.suffix.lower() in IMAGE_EXTENSIONS and path.is_file()]

    def order(path):
        return (0, int(path.stem)) if path.stem.isdigit() else (1, path.name)

    files.sort(key=order)
    return files[:max(1, int(count))]


def _load_images(library_dir, count):
    images = []
    for path in _list_images(library_dir, count):
        try:
            images.append(_load_rgb(path))
        except Exception as error:
            logger.warning(f"跳过无法读取的素材 {path}: {error}")
    return images


def _palette(image, bg_color_config=None):
    """从素材或配置得到一组配色：deep 压暗层、accent 点缀、paper 纸色、ink 墨色。"""
    config = bg_color_config or {}
    try:
        base = ColorHelper.get_background_color(
            image,
            color_mode=config.get("mode", "auto"),
            custom_color=config.get("custom_color"),
            config_color=config.get("config_color"),
        )
    except Exception as error:
        logger.debug(f"提取主色失败，使用默认色: {error}")
        base = (120, 110, 100)
    base = tuple(int(channel) for channel in base[:3])
    hue, saturation, _value = ColorHelper.rgb_to_hsv(base)
    deep = ColorHelper.hsv_to_rgb(hue, min(0.62, max(0.28, saturation)), 0.15)
    accent = ColorHelper.hsv_to_rgb(hue, min(0.62, max(0.34, saturation)), 0.93)
    paper = _mix((244, 239, 230), base, 0.07)
    ink = ColorHelper.hsv_to_rgb(hue, min(0.55, max(0.20, saturation)), 0.17)
    frame_tint = ColorHelper.hsv_to_rgb(hue, min(0.5, max(0.22, saturation)), 0.62)
    return {"base": base, "deep": deep, "accent": accent, "paper": paper, "ink": ink, "tint": frame_tint}


def _rgba_from_alpha(color, alpha):
    height, width = alpha.shape
    data = np.zeros((height, width, 4), dtype=np.uint8)
    data[..., 0], data[..., 1], data[..., 2] = color[0], color[1], color[2]
    data[..., 3] = np.clip(alpha * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(data)


def _axes(size):
    width, height = size
    return np.meshgrid(np.linspace(0.0, 1.0, width), np.linspace(0.0, 1.0, height))


def _vignette(size, strength=0.45):
    x_axis, y_axis = _axes(size)
    distance = np.sqrt(((x_axis - 0.5) * 1.15) ** 2 + ((y_axis - 0.5) * 1.15) ** 2) / 0.81
    return _rgba_from_alpha((0, 0, 0), _smoothstep((distance - 0.45) / 0.55) * strength)


def _film_grain(image, amount=5.0):
    data = np.asarray(image.convert("RGB"), dtype=np.float32)
    # 使用固定种子，避免同一素材每次生成时文件内容都发生变化
    noise = np.random.default_rng(1492).normal(0.0, amount, data.shape[:2])[..., None]
    return Image.fromarray(np.clip(data + noise, 0, 255).astype(np.uint8))


def _rounded_mask(size, radius, supersample=3):
    width, height = size
    mask = Image.new("L", (width * supersample, height * supersample), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [(0, 0), (width * supersample - 1, height * supersample - 1)],
        radius=radius * supersample,
        fill=255,
    )
    return mask.resize(size, Image.Resampling.LANCZOS)


def _encode_jpeg(image, quality=92):
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality, optimize=True, subsampling=0)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


# ---------------------------------------------------------------- 文字排版


def _scale_text_params(font_size, font_offset, canvas_size, animated):
    """静态版字号已按分辨率换算；动态版收到的是 1080p 基准字号，这里补上换算。"""
    scale = canvas_size[1] / 1080.0
    if animated:
        font_size = (float(font_size[0]) * scale, float(font_size[1]) * scale)
    return font_size, font_offset, scale


# ---------------------------------------------------------------- 动画工具


def _prepare_motion(images, canvas_size, zoom=1.16):
    """把素材预缩放到比画布略大，供 Ken Burns 运镜使用。"""
    large = (int(math.ceil(canvas_size[0] * zoom)), int(math.ceil(canvas_size[1] * zoom)))
    return [_fit(image, large) for image in images]


def _ken_burns(prepared, canvas_size, progress, index):
    """progress 取 0~1。奇偶素材走相反方向，亚像素采样避免低分辨率下抖动。"""
    src_w, src_h = prepared.size
    width, height = canvas_size
    max_scale = src_w / float(width)
    if index % 2 == 0:
        scale = max_scale - (max_scale - 1.0) * 0.75 * progress
        pan = progress
    else:
        scale = 1.0 + (max_scale - 1.0) * 0.75 * progress
        pan = 1.0 - progress
    view_w, view_h = width * scale, height * scale
    left = (src_w - view_w) * pan
    top = (src_h - view_h) * 0.5
    return prepared.transform(
        canvas_size,
        Image.Transform.AFFINE,
        (scale, 0, left, 0, scale, top),
        resample=Image.Resampling.BICUBIC,
    )


def _crossfade_frame(prepared, canvas_size, phase, crossfade=0.3):
    """循环轮播：phase 取 [0, 1)，首尾无缝。"""
    count = len(prepared)
    if count == 1:
        return _ken_burns(prepared[0], canvas_size, _ease(1.0 - abs(phase * 2.0 - 1.0)), 0)
    position = phase * count
    current = int(position) % count
    local = position - int(position)
    span = 1.0 + crossfade

    def motion(index, local_time):
        return _ken_burns(prepared[index], canvas_size, (local_time + crossfade) / span, index)

    frame = motion(current, local)
    if local > 1.0 - crossfade:
        following = (current + 1) % count
        ratio = _ease((local - (1.0 - crossfade)) / crossfade)
        frame = Image.blend(frame, motion(following, local - 1.0), ratio)
    return frame


def _encode_animation(render_frame, duration, fps, animation_format, reduce_colors, stop_event):
    """逐帧渲染并调用 ffmpeg 合成，stop_event 触发时立即中止并返回 False。"""
    safe_fps = max(1, int(fps))
    total = max(2, int(round(safe_fps * max(1, int(duration)))))
    mode = reduce_colors
    if isinstance(mode, bool):
        mode = "strong" if mode else "off"
    if mode not in ("off", "medium", "strong"):
        mode = "medium"

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        logger.info(f"开始生成动画帧，共 {total} 帧")
        for index in range(total):
            if stop_event and stop_event.is_set():
                return False
            render_frame(index / float(total)).convert("RGB").save(
                temp_path / f"frame_{index:04d}.bmp", format="BMP"
            )

        suffix = ".gif" if animation_format == "gif" else ".png"
        output = temp_path / f"output{suffix}"
        command = [
            "ffmpeg", "-hide_banner", "-y", "-framerate", str(safe_fps),
            "-i", str(temp_path / "frame_%04d.bmp"), "-threads", "0",
        ]
        colors = {"strong": "64", "medium": "128", "off": "256"}[mode]
        dither = {"strong": "none", "medium": "bayer:bayer_scale=3", "off": "floyd_steinberg"}[mode]
        if animation_format == "gif":
            command += [
                "-filter_complex",
                f"[0:v] split [a][b]; [a] palettegen=max_colors={colors} [p]; [b][p] paletteuse=dither={dither}",
                "-loop", "0", "-f", "gif", str(output),
            ]
        elif mode == "off":
            command += ["-vcodec", "apng", "-pix_fmt", "rgba", "-plays", "0", "-f", "apng", str(output)]
        else:
            command += [
                "-filter_complex",
                f"[0:v] split [a][b]; [a] palettegen=max_colors={colors}:reserve_transparent=on [p]; "
                f"[b][p] paletteuse=dither={dither}",
                "-vcodec", "apng", "-pix_fmt", "rgba", "-plays", "0", "-f", "apng", str(output),
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
        return base64.b64encode(output.read_bytes()).decode("utf-8")


# ---------------------------------------------------------------- 风格 5：影院


def _cinema_overlay(size, palette, title, font_path, font_size, font_offset, scale, color_ratio):
    width, height = size
    strength = 0.62 + 0.38 * max(0.0, min(1.0, float(color_ratio)))
    x_axis, y_axis = _axes(size)
    side = _smoothstep((0.74 - x_axis) / 0.74) * 0.96
    bottom = _smoothstep((y_axis - 0.34) / 0.66) * 0.90
    alpha = np.clip(1.0 - (1.0 - side) * (1.0 - bottom), 0.0, 1.0) * strength
    overlay = _rgba_from_alpha(palette["deep"], alpha)
    overlay.alpha_composite(_vignette(size, 0.38))

    margin = width * 0.058
    layout = TitleLayout(title, font_path, font_size, font_offset, scale, width * 0.56, tracking=0.16)
    block_top = height * 0.885 - layout.height
    rule_h = max(1, int(round(height * 0.0052)))
    rule_w = max(4, int(round(width * 0.042)))
    rule_y = int(block_top - height * 0.052)
    ImageDraw.Draw(overlay).rectangle(
        [(int(margin), rule_y), (int(margin) + rule_w, rule_y + rule_h)],
        fill=palette["accent"] + (255,),
    )
    overlay.alpha_composite(
        layout.render(
            size, margin, block_top,
            sub_fill=(255, 255, 255, 190),
            shadow=((0, 0, 0, 120), height * 0.012, height * 0.004),
        )
    )
    return overlay


def create_style_static_5(image_path, title, font_path, font_size=(170, 75), font_offset=(0, 40, 40),
                          blur_size=50, color_ratio=0.8, resolution_config=None, bg_color_config=None):
    try:
        size = _canvas_size(resolution_config)
        source = _load_rgb(image_path)
        palette = _palette(source, bg_color_config)
        font_size, font_offset, scale = _scale_text_params(font_size, font_offset, size, False)
        frame = _fit(source, size)
        frame = ImageEnhance.Contrast(frame).enhance(1.05).convert("RGBA")
        frame.alpha_composite(
            _cinema_overlay(size, palette, title, font_path, font_size, font_offset, scale, color_ratio)
        )
        return _encode_jpeg(_film_grain(frame, 3.5))
    except Exception as error:
        logger.error(f"创建风格 5（影院）失败: {error}", exc_info=True)
        return False


def create_style_animated_5(library_dir, title, font_path, font_size=(170, 75), font_offset=(0, 40, 40),
                            is_blur=True, blur_size=50, color_ratio=0.8, resolution_config=None,
                            bg_color_config=None, animation_duration=8, animation_fps=24,
                            animation_format="apng", animation_resolution="320x180",
                            animation_reduce_colors="medium", image_count=6, stop_event=None):
    try:
        size = _animation_size(animation_resolution)
        images = _load_images(library_dir, image_count)
        if not images:
            logger.warning("风格 5 动画未找到素材")
            return False
        palette = _palette(images[0], bg_color_config)
        font_size, font_offset, scale = _scale_text_params(font_size, font_offset, size, True)
        overlay = _cinema_overlay(size, palette, title, font_path, font_size, font_offset, scale, color_ratio)
        prepared = _prepare_motion(images, size)

        def render(phase):
            frame = _crossfade_frame(prepared, size, phase).convert("RGBA")
            frame.alpha_composite(overlay)
            return frame

        return _encode_animation(render, animation_duration, animation_fps, animation_format,
                                 animation_reduce_colors, stop_event)
    except Exception as error:
        logger.error(f"创建风格 5 动画失败: {error}", exc_info=True)
        return False


# ---------------------------------------------------------------- 风格 6：磨砂


class _FrostPanel:
    """预计算面板几何与文字，逐帧只重新模糊面板区域。"""

    def __init__(self, size, palette, title, font_path, font_size, font_offset, scale, blur_size, color_ratio):
        self.size = size
        width, height = size
        self.palette = palette
        layout = TitleLayout(title, font_path, font_size, font_offset, scale, width * 0.60,
                             tracking=0.16)
        pad_x, pad_y = width * 0.052, height * 0.078
        panel_w = int(min(width * 0.84, max(layout.width + pad_x * 2, width * 0.36)))
        panel_h = int(min(height * 0.82, layout.height + pad_y * 2))
        self.box = (
            (width - panel_w) // 2,
            (height - panel_h) // 2,
            (width - panel_w) // 2 + panel_w,
            (height - panel_h) // 2 + panel_h,
        )
        self.panel_size = (panel_w, panel_h)
        radius = max(3, int(height * 0.034))
        self.mask = _rounded_mask(self.panel_size, radius)
        self.blur = max(2.0, float(blur_size or 50) * 0.62 * scale)
        self.tint_ratio = 0.20 + 0.28 * max(0.0, min(1.0, float(color_ratio)))

        self.shadow = Image.new("RGBA", size, (0, 0, 0, 0))
        shadow_mask = Image.new("L", size, 0)
        shadow_mask.paste(self.mask, (self.box[0], self.box[1] + int(height * 0.016)))
        self.shadow.putalpha(
            shadow_mask.filter(ImageFilter.GaussianBlur(height * 0.038)).point(lambda value: int(value * 0.5))
        )

        # 顶部柔光与内描边让面板更接近真实玻璃
        _x_axis, y_axis = _axes(self.panel_size)
        sheen = _rgba_from_alpha((255, 255, 255), _smoothstep((0.62 - y_axis) / 0.62) * 0.16)
        border = Image.new("RGBA", self.panel_size, (0, 0, 0, 0))
        line = max(1, int(round(height * 0.0016)))
        ImageDraw.Draw(border).rounded_rectangle(
            [(line // 2, line // 2), (panel_w - 1 - line // 2, panel_h - 1 - line // 2)],
            radius=radius, outline=(255, 255, 255, 96), width=line,
        )
        sheen.alpha_composite(border)
        sheen.putalpha(Image.fromarray(
            (np.asarray(sheen.getchannel("A"), dtype=np.float32) * (np.asarray(self.mask, np.float32) / 255.0)).astype(np.uint8)
        ))
        self.sheen = sheen

        text_x = (width - layout.max_width / TEXT_SUPERSAMPLE) / 2.0
        text_y = (height - layout.height) / 2.0
        self.text = layout.render(
            size, text_x, text_y, align="center",
            sub_fill=(255, 255, 255, 214),
            shadow=((0, 0, 0, 90), height * 0.008, height * 0.002),
        )
        self.vignette = _vignette(size, 0.30)

    def apply(self, frame):
        frame = frame.convert("RGBA")
        frame.alpha_composite(self.vignette)
        frame.alpha_composite(self.shadow)
        glass = frame.crop(self.box).convert("RGB").filter(ImageFilter.GaussianBlur(self.blur))
        glass = Image.blend(glass, Image.new("RGB", self.panel_size, self.palette["deep"]), self.tint_ratio)
        glass = glass.convert("RGBA")
        glass.alpha_composite(self.sheen)
        frame.paste(glass, self.box[:2], self.mask)
        frame.alpha_composite(self.text)
        return frame


def create_style_static_6(image_path, title, font_path, font_size=(170, 75), font_offset=(0, 40, 40),
                          blur_size=50, color_ratio=0.8, resolution_config=None, bg_color_config=None):
    try:
        size = _canvas_size(resolution_config)
        source = _load_rgb(image_path)
        palette = _palette(source, bg_color_config)
        font_size, font_offset, scale = _scale_text_params(font_size, font_offset, size, False)
        panel = _FrostPanel(size, palette, title, font_path, font_size, font_offset, scale, blur_size, color_ratio)
        return _encode_jpeg(_film_grain(panel.apply(_fit(source, size)), 2.0))
    except Exception as error:
        logger.error(f"创建风格 6（磨砂）失败: {error}", exc_info=True)
        return False


def create_style_animated_6(library_dir, title, font_path, font_size=(170, 75), font_offset=(0, 40, 40),
                            is_blur=True, blur_size=50, color_ratio=0.8, resolution_config=None,
                            bg_color_config=None, animation_duration=8, animation_fps=24,
                            animation_format="apng", animation_resolution="320x180",
                            animation_reduce_colors="medium", image_count=6, stop_event=None):
    try:
        size = _animation_size(animation_resolution)
        images = _load_images(library_dir, image_count)
        if not images:
            logger.warning("风格 6 动画未找到素材")
            return False
        palette = _palette(images[0], bg_color_config)
        font_size, font_offset, scale = _scale_text_params(font_size, font_offset, size, True)
        panel = _FrostPanel(size, palette, title, font_path, font_size, font_offset, scale, blur_size, color_ratio)
        prepared = _prepare_motion(images, size)
        return _encode_animation(
            lambda phase: panel.apply(_crossfade_frame(prepared, size, phase)),
            animation_duration, animation_fps, animation_format, animation_reduce_colors, stop_event,
        )
    except Exception as error:
        logger.error(f"创建风格 6 动画失败: {error}", exc_info=True)
        return False


# ---------------------------------------------------------------- 风格 7：长廊


def _perspective_coefficients(target_quad, source_quad):
    """求 PIL PERSPECTIVE 变换系数：把输出坐标映射回源图坐标。"""
    matrix, vector = [], []
    for (x_out, y_out), (x_src, y_src) in zip(target_quad, source_quad):
        matrix.append([x_out, y_out, 1, 0, 0, 0, -x_src * x_out, -x_src * y_out])
        matrix.append([0, 0, 0, x_out, y_out, 1, -y_src * x_out, -y_src * y_out])
        vector.extend([x_src, y_src])
    return np.linalg.solve(np.array(matrix, dtype=float), np.array(vector, dtype=float)).tolist()


class _GalleryWall:
    """
    把海报排成可循环滚动的列，再做透视投影。
    动画只需移动每列的裁剪窗口，无需逐帧重排海报。
    """

    COLUMNS = 5

    def __init__(self, size, images, palette, title, font_path, font_size, font_offset, scale, color_ratio):
        self.size = size
        width, height = size
        # 平面墙按比画布更高的分辨率绘制，避免透视压缩后出现锯齿
        self.wall_size = (int(width * 1.5), int(height * 1.72))
        wall_w, wall_h = self.wall_size
        gap = max(2, int(wall_w * 0.016))
        self.poster_w = (wall_w - gap * (self.COLUMNS + 1)) // self.COLUMNS
        self.poster_h = int(self.poster_w * 1.5)
        self.step = self.poster_h + gap
        self.gap = gap

        radius = max(2, int(self.poster_w * 0.055))
        mask = _rounded_mask((self.poster_w, self.poster_h), radius)
        posters = []
        for image in images:
            poster = _fit(image, (self.poster_w, self.poster_h)).convert("RGBA")
            poster.putalpha(mask)
            posters.append(poster)
        while len(posters) < self.COLUMNS:
            posters.extend(posters[:self.COLUMNS - len(posters)])

        rows = max(len(posters), int(math.ceil(wall_h / float(self.step))) + 2)
        self.tile_height = self.step * rows
        self.strips = []
        for column in range(self.COLUMNS):
            strip = Image.new("RGBA", (self.poster_w, self.tile_height), (0, 0, 0, 0))
            for row in range(rows):
                poster = posters[(column * 2 + row) % len(posters)]
                strip.alpha_composite(poster, (0, row * self.step))
            self.strips.append(strip)
        # 每列相对错位，形成错落的节奏
        self.stagger = [int(self.step * ratio) for ratio in (0.0, 0.42, 0.14, 0.58, 0.28)]

        # 平面墙四角映射到画面右侧的透视四边形：左侧远、右侧近
        target = [
            (width * 0.30, height * 0.04),
            (width * 1.10, height * -0.24),
            (width * 1.10, height * 1.24),
            (width * 0.30, height * 0.96),
        ]
        source = [(0, 0), (wall_w, 0), (wall_w, wall_h), (0, wall_h)]
        self.coefficients = _perspective_coefficients(target, source)

        background = np.zeros((height, width, 3), dtype=np.float32)
        x_axis, y_axis = _axes(size)
        glow = np.exp(-(((x_axis - 0.72) / 0.55) ** 2 + ((y_axis - 0.5) / 0.75) ** 2))[..., None]
        deep = np.array(palette["deep"], dtype=np.float32)
        lifted = np.array(_mix(palette["deep"], palette["tint"], 0.34), dtype=np.float32)
        background[...] = deep * (1.0 - glow) + lifted * glow
        self.background = Image.fromarray(background.astype(np.uint8)).convert("RGBA")

        strength = 0.70 + 0.30 * max(0.0, min(1.0, float(color_ratio)))
        fade = _smoothstep((0.68 - x_axis) / 0.46) * strength
        fade = np.maximum(fade, _smoothstep((0.53 - x_axis) / 0.10))
        overlay = _rgba_from_alpha(palette["deep"], np.clip(fade, 0.0, 1.0))
        overlay.alpha_composite(_vignette(size, 0.42))

        margin = width * 0.058
        layout = TitleLayout(title, font_path, font_size, font_offset, scale, width * 0.42, tracking=0.16)
        block_top = (height - layout.height) / 2.0
        rule_h = max(1, int(round(height * 0.0052)))
        rule_w = max(4, int(round(width * 0.042)))
        rule_y = int(block_top - height * 0.052)
        ImageDraw.Draw(overlay).rectangle(
            [(int(margin), rule_y), (int(margin) + rule_w, rule_y + rule_h)],
            fill=palette["accent"] + (255,),
        )
        overlay.alpha_composite(
            layout.render(
                size, margin, block_top,
                sub_fill=(255, 255, 255, 190),
                shadow=((0, 0, 0, 110), height * 0.012, height * 0.004),
            )
        )
        self.overlay = overlay

    def render(self, phase=0.0):
        wall = Image.new("RGBA", self.wall_size, (0, 0, 0, 0))
        wall_h = self.wall_size[1]
        for column, strip in enumerate(self.strips):
            direction = 1 if column % 2 == 0 else -1
            shift = (self.stagger[column] + direction * phase * self.tile_height) % self.tile_height
            top = int(round(shift))
            bottom = top + wall_h
            x_pos = self.gap + column * (self.poster_w + self.gap)
            if bottom <= self.tile_height:
                wall.alpha_composite(strip.crop((0, top, self.poster_w, bottom)), (x_pos, 0))
            else:
                first = strip.crop((0, top, self.poster_w, self.tile_height))
                wall.alpha_composite(first, (x_pos, 0))
                wall.alpha_composite(strip.crop((0, 0, self.poster_w, bottom - self.tile_height)),
                                     (x_pos, first.size[1]))
        projected = wall.transform(self.size, Image.Transform.PERSPECTIVE, self.coefficients,
                                   resample=Image.Resampling.BICUBIC)
        frame = self.background.copy()
        frame.alpha_composite(projected)
        frame.alpha_composite(self.overlay)
        return frame


def create_style_static_7(library_dir, title, font_path, font_size=(170, 75), font_offset=(0, 40, 40),
                          is_blur=True, blur_size=50, color_ratio=0.8, resolution_config=None, bg_color_config=None):
    try:
        size = _canvas_size(resolution_config)
        images = _load_images(library_dir, 9)
        if not images:
            logger.warning("风格 7 未找到素材")
            return False
        palette = _palette(images[0], bg_color_config)
        font_size, font_offset, scale = _scale_text_params(font_size, font_offset, size, False)
        wall = _GalleryWall(size, images, palette, title, font_path, font_size, font_offset, scale, color_ratio)
        return _encode_jpeg(_film_grain(wall.render(0.0), 2.5))
    except Exception as error:
        logger.error(f"创建风格 7（长廊）失败: {error}", exc_info=True)
        return False


def create_style_animated_7(library_dir, title, font_path, font_size=(170, 75), font_offset=(0, 40, 40),
                            is_blur=True, blur_size=50, color_ratio=0.8, resolution_config=None,
                            bg_color_config=None, animation_duration=8, animation_fps=24,
                            animation_format="apng", animation_resolution="320x180",
                            animation_reduce_colors="medium", image_count=9, stop_event=None):
    try:
        size = _animation_size(animation_resolution)
        images = _load_images(library_dir, max(5, int(image_count)))
        if not images:
            logger.warning("风格 7 动画未找到素材")
            return False
        palette = _palette(images[0], bg_color_config)
        font_size, font_offset, scale = _scale_text_params(font_size, font_offset, size, True)
        wall = _GalleryWall(size, images, palette, title, font_path, font_size, font_offset, scale, color_ratio)
        return _encode_animation(wall.render, animation_duration, animation_fps, animation_format,
                                 animation_reduce_colors, stop_event)
    except Exception as error:
        logger.error(f"创建风格 7 动画失败: {error}", exc_info=True)
        return False


# ---------------------------------------------------------------- 风格 8：刊物


class _EditorialPage:
    def __init__(self, size, palette, title, font_path, font_size, font_offset, scale):
        self.size = size
        width, height = size
        self.window = (int(width * 0.585), int(height * 0.095), int(width * 0.942), int(height * 0.905))
        self.window_size = (self.window[2] - self.window[0], self.window[3] - self.window[1])

        page = Image.new("RGBA", size, palette["paper"] + (255,))
        draw = ImageDraw.Draw(page)
        line = max(1, int(round(height * 0.0018)))
        ink = palette["ink"]
        margin = int(width * 0.058)
        column_right = int(width * 0.525)
        draw.rectangle([(margin, int(height * 0.095)), (column_right, int(height * 0.095) + line)], fill=ink + (235,))
        draw.rectangle([(margin, int(height * 0.905) - line), (column_right, int(height * 0.905))], fill=ink + (235,))

        # 画框后方错位的色块，增强层次
        shift = int(height * 0.024)
        draw.rectangle(
            [(self.window[0] + shift, self.window[1] + shift), (self.window[2] + shift, self.window[3] + shift)],
            fill=palette["tint"] + (255,),
        )

        layout = TitleLayout(title, font_path, font_size, font_offset, scale, column_right - margin,
                             tracking=0.20)
        block_top = int(height * 0.905) - height * 0.075 - layout.height
        page.alpha_composite(
            layout.render(
                size, margin, block_top,
                main_fill=ink + (255,),
                sub_fill=ink + (190,),
            )
        )
        self.page = page

        keyline = Image.new("RGBA", self.window_size, (0, 0, 0, 0))
        ImageDraw.Draw(keyline).rectangle(
            [(0, 0), (self.window_size[0] - 1, self.window_size[1] - 1)],
            outline=(255, 255, 255, 70), width=max(1, line),
        )
        self.keyline = keyline

    def compose(self, picture):
        frame = self.page.copy()
        picture = picture.convert("RGBA")
        picture.alpha_composite(self.keyline)
        frame.paste(picture, self.window[:2])
        return frame


def create_style_static_8(image_path, title, font_path, font_size=(170, 75), font_offset=(0, 40, 40),
                          blur_size=50, color_ratio=0.8, resolution_config=None, bg_color_config=None):
    try:
        size = _canvas_size(resolution_config)
        source = _load_rgb(image_path)
        palette = _palette(source, bg_color_config)
        font_size, font_offset, scale = _scale_text_params(font_size, font_offset, size, False)
        page = _EditorialPage(size, palette, title, font_path, font_size, font_offset, scale)
        picture = _fit(source, page.window_size)
        return _encode_jpeg(_film_grain(page.compose(picture), 2.2))
    except Exception as error:
        logger.error(f"创建风格 8（刊物）失败: {error}", exc_info=True)
        return False


def create_style_animated_8(library_dir, title, font_path, font_size=(170, 75), font_offset=(0, 40, 40),
                            is_blur=True, blur_size=50, color_ratio=0.8, resolution_config=None,
                            bg_color_config=None, animation_duration=8, animation_fps=24,
                            animation_format="apng", animation_resolution="320x180",
                            animation_reduce_colors="medium", image_count=6, stop_event=None):
    try:
        size = _animation_size(animation_resolution)
        images = _load_images(library_dir, image_count)
        if not images:
            logger.warning("风格 8 动画未找到素材")
            return False
        palette = _palette(images[0], bg_color_config)
        font_size, font_offset, scale = _scale_text_params(font_size, font_offset, size, True)
        page = _EditorialPage(size, palette, title, font_path, font_size, font_offset, scale)
        prepared = _prepare_motion(images, page.window_size)
        return _encode_animation(
            lambda phase: page.compose(_crossfade_frame(prepared, page.window_size, phase)),
            animation_duration, animation_fps, animation_format, animation_reduce_colors, stop_event,
        )
    except Exception as error:
        logger.error(f"创建风格 8 动画失败: {error}", exc_info=True)
        return False
