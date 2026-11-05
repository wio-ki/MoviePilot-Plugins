import base64
from io import BytesIO

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from app.log import logger
from app.plugins.mediacovergenerator.style.title_layout import TitleLayout, soft_shadow
from app.plugins.mediacovergenerator.style.style_static_2 import (
    darken_color,
    find_dominant_vibrant_colors,
)
from app.plugins.mediacovergenerator.utils.color_helper import ColorHelper


def create_style_static_4(
    image_path,
    title,
    font_path,
    font_size=(170, 75),
    font_offset=(0, 40, 40),
    blur_size=50,
    color_ratio=0.8,
    resolution_config=None,
    bg_color_config=None,
):
    try:

        width = 1920
        height = 1080
        if resolution_config:
            width = int(getattr(resolution_config, "width", width))
            height = int(getattr(resolution_config, "height", height))
        canvas_size = (max(1, width), max(1, height))

        src = Image.open(image_path).convert("RGB")
        bg = ImageOps.fit(src, canvas_size, method=Image.LANCZOS)

        scaled_blur = int(max(8, float(blur_size) * (canvas_size[1] / 1080.0)))
        bg = bg.filter(ImageFilter.GaussianBlur(radius=scaled_blur))

        if bg_color_config:
            tint = ColorHelper.get_background_color(
                src,
                color_mode=bg_color_config.get('mode', 'auto'),
                custom_color=bg_color_config.get('custom_color'),
                config_color=bg_color_config.get('config_color')
            )
        else:
            dominant = find_dominant_vibrant_colors(src, num_colors=5)
            tint = dominant[0] if dominant else (120, 120, 120)
        tint = darken_color(tint, 0.82)

        ratio = float(color_ratio)
        if ratio < 0 or ratio > 1:
            ratio = 0.8

        bg_np = np.array(bg, dtype=float)
        tint_np = np.array([[tint]], dtype=float)
        mixed = bg_np * (1.0 - ratio) + tint_np * ratio
        mixed = np.clip(mixed, 0, 255).astype(np.uint8)
        canvas = Image.fromarray(mixed).convert("RGBA")

        layout = TitleLayout(
            title, font_path, font_size, font_offset, canvas_size[1] / 1080.0, canvas_size[0] * 0.78,
            uppercase_subtitle=False, tracking=0.03,
        )
        merged = Image.alpha_composite(canvas, layout.render_centered(
            canvas_size, canvas_size[0] / 2.0, canvas_size[1] / 2.0,
            main_fill=(255, 255, 255, 235), sub_fill=(255, 255, 255, 235),
            shadow=soft_shadow(darken_color(tint, 0.65), canvas_size[1]),
        ))

        buf = BytesIO()
        merged.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as e:
        logger.error(f"创建静态4封面时出错: {e}")
        return False
