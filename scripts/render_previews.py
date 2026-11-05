"""
重新生成风格预览图。新增或调整风格后运行：

    python scripts/render_previews.py

需要 pillow 和 numpy。素材由程序绘制，不依赖任何受版权保护的海报。
输出到 images/style_N.jpeg 与两个插件版本的 ui_previews.py（界面内嵌的小图）。
"""
import base64
import logging
import math
import sys
import tempfile
import types
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIRS = [REPO / "plugins.v3" / "mediacovergenerator", REPO / "plugins.v2" / "mediacovergenerator"]
FONTS = (str(REPO / "fonts" / "chaohei.ttf"), str(REPO / "fonts" / "EmblemaOne.woff2"))
TITLE = ("光影典藏", "CINEMA COLLECTION")

PALETTES = [
    ((16, 24, 58), (178, 62, 96), (252, 176, 92)),
    ((10, 44, 58), (28, 122, 128), (214, 226, 170)),
    ((38, 16, 62), (122, 56, 158), (246, 148, 170)),
    ((58, 24, 16), (186, 84, 44), (246, 206, 128)),
    ((12, 30, 40), (52, 96, 132), (236, 196, 152)),
    ((20, 44, 34), (74, 134, 92), (232, 220, 142)),
    ((52, 14, 34), (178, 44, 78), (250, 190, 150)),
    ((14, 20, 44), (66, 74, 170), (150, 210, 226)),
    ((40, 32, 24), (150, 112, 74), (232, 214, 182)),
]


def install_stubs():
    """让样式模块脱离 MoviePilot 运行。"""
    for name in ("app", "app.plugins"):
        module = types.ModuleType(name)
        module.__path__ = []
        sys.modules[name] = module
    log_module = types.ModuleType("app.log")
    log_module.logger = logging.getLogger("preview")
    sys.modules["app.log"] = log_module
    package = types.ModuleType("app.plugins.mediacovergenerator")
    package.__path__ = [str(PLUGIN_DIRS[0])]
    sys.modules["app.plugins.mediacovergenerator"] = package


def gradient(size, colors):
    width, height = size
    ramp = np.linspace(0.0, 1.0, height)[:, None]
    top, middle, bottom = (np.array(color, dtype=np.float32) for color in colors)
    upper = np.clip(ramp * 2.0, 0.0, 1.0)[..., None]
    lower = np.clip(ramp * 2.0 - 1.0, 0.0, 1.0)[..., None]
    rows = (top * (1 - upper) + middle * upper) * (1 - lower) + bottom * lower
    return Image.fromarray(np.repeat(rows, width, axis=1).astype(np.uint8))


def draw_art(seed, size):
    """用渐变和几何形状画一张抽象图。"""
    rng = np.random.default_rng(seed)
    width, height = size
    colors = PALETTES[seed % len(PALETTES)]
    image = gradient(size, colors).convert("RGB")
    scale = 2
    layer = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    kind = seed % 3

    orb_x = int(width * scale * rng.uniform(0.25, 0.75))
    orb_y = int(height * scale * rng.uniform(0.22, 0.5))
    orb_r = int(min(width, height) * scale * rng.uniform(0.16, 0.3))
    draw.ellipse([orb_x - orb_r, orb_y - orb_r, orb_x + orb_r, orb_y + orb_r], fill=colors[2] + (235,))

    if kind == 0:
        # 层叠沙丘
        for layer_index in range(5):
            base = height * scale * (0.5 + layer_index * 0.1)
            amplitude = height * scale * rng.uniform(0.03, 0.07)
            frequency = rng.uniform(1.0, 2.6)
            phase = rng.uniform(0, math.tau)
            points = [(x, base + amplitude * math.sin(frequency * math.tau * x / (width * scale) + phase))
                      for x in range(0, width * scale + 8, 8)]
            shade = tuple(int(colors[0][i] * (1 - layer_index * 0.13) + colors[1][i] * 0.26) for i in range(3))
            draw.polygon(points + [(width * scale, height * scale), (0, height * scale)], fill=shade + (255,))
    elif kind == 1:
        # 山峰剪影
        for layer_index in range(4):
            base = height * scale * (0.56 + layer_index * 0.11)
            points = [(0, height * scale)]
            x_pos = 0
            while x_pos < width * scale:
                x_pos += int(width * scale * rng.uniform(0.1, 0.24))
                points.append((x_pos, base - height * scale * rng.uniform(0.0, 0.17)))
            points.append((width * scale, height * scale))
            shade = tuple(int(colors[0][i] * (0.85 - layer_index * 0.14) + colors[1][i] * 0.2) for i in range(3))
            draw.polygon(points, fill=shade + (255,))
    else:
        # 斜向光带
        for band in range(7):
            offset = int(width * scale * (band / 6.0) - height * scale * 0.5)
            thickness = int(width * scale * rng.uniform(0.03, 0.09))
            color = colors[band % 2 + 1] + (int(rng.uniform(50, 130)),)
            draw.polygon(
                [(offset, height * scale), (offset + thickness, height * scale),
                 (offset + thickness + height * scale, 0), (offset + height * scale, 0)],
                fill=color,
            )

    layer = layer.resize(size, Image.Resampling.LANCZOS)
    glow = layer.filter(ImageFilter.GaussianBlur(min(width, height) * 0.03))
    image = image.convert("RGBA")
    image.alpha_composite(glow)
    image.alpha_composite(layer)
    return image.convert("RGB")


def decode(data):
    return Image.open(BytesIO(base64.b64decode(data))).convert("RGB")


def render_all():
    from app.plugins.mediacovergenerator.style import style_premium as premium
    from app.plugins.mediacovergenerator.style.style_static_1 import create_style_static_1
    from app.plugins.mediacovergenerator.style.style_static_2 import create_style_static_2
    from app.plugins.mediacovergenerator.style.style_static_3 import create_style_static_3
    from app.plugins.mediacovergenerator.style.style_static_4 import create_style_static_4
    from app.plugins.mediacovergenerator.utils.image_manager import ResolutionConfig

    resolution = ResolutionConfig("720p")
    scale = resolution.height / 1080.0
    common = {
        "font_size": (170 * scale, 75 * scale),
        "font_offset": (0, 40, 40),
        "blur_size": 50,
        "color_ratio": 0.8,
        "resolution_config": resolution,
        "bg_color_config": {"mode": "auto"},
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        poster_dir = temp / "posters"
        poster_dir.mkdir()
        for index in range(9):
            draw_art(index + 3, (600, 900)).save(poster_dir / f"{index + 1}.jpg", quality=95)
        backdrop = temp / "backdrop.jpg"
        draw_art(9, (1600, 900)).save(backdrop, quality=95)
        square = temp / "square.jpg"
        draw_art(3, (1000, 1000)).save(square, quality=95)

        results = {
            1: create_style_static_1(str(square), TITLE, FONTS, **common),
            2: create_style_static_2(str(backdrop), TITLE, FONTS, **common),
            3: create_style_static_3(poster_dir, TITLE, FONTS, is_blur=True, **common),
            4: create_style_static_4(str(backdrop), TITLE, FONTS, **common),
            5: premium.create_style_static_5(backdrop, TITLE, FONTS, **common),
            6: premium.create_style_static_6(backdrop, TITLE, FONTS, **common),
            7: premium.create_style_static_7(poster_dir, TITLE, FONTS, is_blur=True, **common),
            8: premium.create_style_static_8(backdrop, TITLE, FONTS, **common),
        }
    if not all(results.values()):
        raise SystemExit(f"风格渲染失败: {[key for key, value in results.items() if not value]}")
    return {index: decode(data) for index, data in results.items()}


def write_outputs(images):
    lines = ['"""风格预览小图，由 scripts/render_previews.py 生成，请勿手工修改。"""', "", "PREVIEWS = {"]
    for index, image in sorted(images.items()):
        image.resize((480, 270), Image.Resampling.LANCZOS).save(REPO / "images" / f"style_{index}.jpeg", quality=88)
        buffer = BytesIO()
        image.resize((320, 180), Image.Resampling.LANCZOS).save(buffer, format="WEBP", quality=62, method=6)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        lines.append(f'    {index}: "data:image/webp;base64,{encoded}",')
    lines += ["}", ""]
    text = "\n".join(lines)
    for plugin_dir in PLUGIN_DIRS:
        (plugin_dir / "ui_previews.py").write_text(text, encoding="utf-8", newline="\n")
    print(f"预览图已更新，ui_previews.py 大小 {len(text) // 1024} KB")


if __name__ == "__main__":
    install_stubs()
    write_outputs(render_all())
