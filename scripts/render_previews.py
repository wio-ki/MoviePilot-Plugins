"""
重新生成风格预览图。调整风格后运行：

    python scripts/render_previews.py

需要 pillow 和 numpy，有 ffmpeg 时还会生成动图样张。素材由程序绘制，不依赖任何受版权保护的海报。
输出到 images/style_N.jpeg、images/style_N.gif 与两个插件版本的 ui_previews.py（界面内嵌的小图）。
"""
import base64
import logging
import math
import shutil
import sys
import tempfile
import types
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIRS = [REPO / "plugins.v3" / "mediacovergenerator", REPO / "plugins.v2" / "mediacovergenerator"]
TITLE = ("电影", "MOVIE")
FONT_FILES = {
    "notoblack": "notoblack.otf", "notolight": "notolight.otf", "smiley": "smiley.ttf",
    "pangmen": "pangmen.ttf", "chaohei": "chaohei.ttf", "yasong": "yasong.ttf",
    "poppinsxb": "poppinsxb.ttf", "poppinssemi": "poppinssemi.ttf",
    "kronaone": "kronaone.ttf", "michroma": "michroma.ttf",
    "CaveatBrush": "caveatbrush.ttf", "EmblemaOne": "EmblemaOne.woff2", "Melete": "Melete.otf",
    "Phosphate": "phosphate.ttf", "JosefinSans": "josefinsans.woff2", "LilitaOne": "lilitaone.woff2",
}

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


def font_path(preset):
    return str(REPO / "fonts" / FONT_FILES[preset])


def style_fonts(spec):
    return font_path(spec.main_font), font_path(spec.sub_font)


def render_all():
    from app.plugins.mediacovergenerator.style.catalog import STYLE_CATALOG, style_info
    from app.plugins.mediacovergenerator.style.covers import create_cover
    from app.plugins.mediacovergenerator.ui_presets import TYPOGRAPHY_PRESETS

    size = (1280, 720)
    has_ffmpeg = bool(shutil.which("ffmpeg"))
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        posters, backdrops = temp / "posters", temp / "backdrops"
        posters.mkdir()
        backdrops.mkdir()
        for index in range(9):
            draw_art(index + 3, (600, 900)).save(posters / f"{index + 1}.jpg", quality=95)
            draw_art(index + 12, (1280, 720)).save(backdrops / f"{index + 1}.jpg", quality=95)

        results, animations = {}, {}
        for info in STYLE_CATALOG:
            source = posters if info.poster else backdrops
            results[info.index] = create_cover(info.index, False, source, TITLE, style_fonts(info.type), info.type, size)
            if has_ffmpeg:
                animations[info.index] = create_cover(
                    info.index, True, source, TITLE, style_fonts(info.type), info.type, (320, 180), image_count=6,
                    animation_duration=8, animation_fps=10, animation_reduce_colors="strong",
                )

        # 字体预设统一用标题最大的立柱风格展示，差异都来自字体
        showcase = style_info(6)
        typography = {}
        for preset in TYPOGRAPHY_PRESETS:
            values = preset["values"]
            fonts = (
                font_path(showcase.type.main_font if values["zh_font_preset"] == "auto" else values["zh_font_preset"]),
                font_path(showcase.type.sub_font if values["en_font_preset"] == "auto" else values["en_font_preset"]),
            )
            typography[preset["key"]] = create_cover(6, False, posters, TITLE, fonts, showcase.type, size)

    failed = [key for key, value in {**results, **typography, **animations}.items() if not value]
    if failed:
        raise SystemExit(f"风格渲染失败: {failed}")
    return (
        {index: decode(data) for index, data in results.items()},
        {key: decode(data) for key, data in typography.items()},
        {index: base64.b64decode(data) for index, data in animations.items()},
    )


def to_data_uri(image):
    buffer = BytesIO()
    image.resize((320, 180), Image.Resampling.LANCZOS).save(buffer, format="WEBP", quality=62, method=6)
    return "data:image/webp;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def write_outputs(images, typography, animations):
    lines = ['"""风格预览小图，由 scripts/render_previews.py 生成，请勿手工修改。"""', "", "PREVIEWS = {"]
    for index, image in sorted(images.items()):
        image.resize((480, 270), Image.Resampling.LANCZOS).save(REPO / "images" / f"style_{index}.jpeg", quality=88)
        lines.append(f'    {index}: "{to_data_uri(image)}",')
    lines += ["}", "", "TYPOGRAPHY_PREVIEWS = {"]
    for key, image in typography.items():
        lines.append(f'    "{key}": "{to_data_uri(image)}",')
    lines += ["}", ""]
    text = "\n".join(lines)
    for plugin_dir in PLUGIN_DIRS:
        (plugin_dir / "ui_previews.py").write_text(text, encoding="utf-8", newline="\n")
    for index, data in animations.items():
        (REPO / "images" / f"style_{index}.gif").write_bytes(data)
    print(f"预览图已更新，ui_previews.py 大小 {len(text) // 1024} KB，"
          f"动图样张 {sum(len(data) for data in animations.values()) // 1024} KB")


if __name__ == "__main__":
    install_stubs()
    write_outputs(*render_all())
