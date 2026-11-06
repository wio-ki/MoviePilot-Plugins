"""
重新生成风格预览图。调整风格后运行：

    python scripts/render_previews.py

需要 pillow 和 numpy，有 ffmpeg 时还会生成动图样张。素材在 scripts/preview_art，都是 NASA 的公开照片。
输出到 images/style_N.jpeg、images/style_N.gif 与两个插件版本的 ui_previews.py（界面内嵌的小图）。
"""
import base64
import logging
import shutil
import sys
import types
from io import BytesIO
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIRS = [REPO / "plugins.v3" / "mediacovergenerator", REPO / "plugins.v2" / "mediacovergenerator"]
TITLE = ("电影", "Movies")
ART = REPO / "scripts" / "preview_art"
FONT_FILES = {
    "glowbold": "glowbold.otf", "glowheavy": "glowheavy.otf", "glowcond": "glowcond.otf",
    "serifblack": "serifblack.otf", "notoblack": "notoblack.otf", "notolight": "notolight.otf",
    "smiley": "smiley.ttf", "pangmen": "pangmen.ttf",
    "interbold": "interbold.ttf", "interblack": "interblack.ttf", "intersemi": "intersemi.ttf",
    "anton": "anton.ttf", "dmserif": "dmserif.ttf", "poppinsxb": "poppinsxb.ttf", "poppinssemi": "poppinssemi.ttf",
    "kronaone": "kronaone.ttf", "michroma": "michroma.ttf",
    "CaveatBrush": "caveatbrush.ttf", "EmblemaOne": "EmblemaOne.woff2", "Melete": "Melete.otf",
    "JosefinSans": "josefinsans.woff2", "LilitaOne": "lilitaone.woff2",
}


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
    posters, backdrops = ART / "poster", ART / "backdrop"

    results, animations = {}, {}
    for info in STYLE_CATALOG:
        source = posters if info.poster else backdrops
        results[info.index] = create_cover(info.index, False, source, TITLE, style_fonts(info.type), info.type, size)
        if has_ffmpeg:
            animations[info.index] = create_cover(
                info.index, True, source, TITLE, style_fonts(info.type), info.type, (320, 180), image_count=6,
                animation_duration=8, animation_fps=10, animation_reduce_colors="strong",
            )

    # 字体预设统一用标题最清楚的预告风格展示，差异都来自字体
    showcase = style_info(4)
    typography = {}
    for preset in TYPOGRAPHY_PRESETS:
        values = preset["values"]
        fonts = (
            font_path(showcase.type.main_font if values["zh_font_preset"] == "auto" else values["zh_font_preset"]),
            font_path(showcase.type.sub_font if values["en_font_preset"] == "auto" else values["en_font_preset"]),
        )
        typography[preset["key"]] = create_cover(4, False, backdrops, TITLE, fonts, showcase.type, size)

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
