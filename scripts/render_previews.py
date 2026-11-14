"""
重新生成风格预览图。调整风格后运行：

    python scripts/render_previews.py

需要 Pillow（支持 WebP 动画）和 numpy。静态示例为 640×360 WebP，同时写入 images/ 与 V3 的 examples/；
动态示例从原始帧编码为 480×270 WebP，只写入 images/style_N_motion.webp，无需 ffmpeg。
素材是吉卜力工作室官网公开的场面写真，生成时才下载，不放进仓库，来源见 images/CREDITS.md；--art-dir 指定本地素材目录后不联网。
--styles 只重画指定风格，不改其他风格、字体示例或旧版 JPEG/GIF 文件。
"""
import base64
import argparse
import logging
import shutil
import sys
import tempfile
import types
import urllib.request
from io import BytesIO
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
# 只维护 V3，V2 的预览图不再更新
PLUGIN_DIRS = [REPO / "plugins.v3" / "mediacovergenerator"]
TITLE = ("动漫", "Anime")
# 示例像一座真的片库：每款风格用不同的媒体库名
STYLE_TITLES = {
    1: ("动漫", "Anime"), 2: ("电影", "Movies"), 3: ("动画电影", "Animation"), 4: ("剧场版", "Theatrical"),
    5: ("合集", "Collections"), 6: ("收藏", "Favorites"), 7: ("电影", "Movies"), 8: ("动漫", "Anime"),
    9: ("剧场版", "Theatrical"), 10: ("动画电影", "Animation"),
    11: ("日韩剧", "Jp&Kr Drama"), 12: ("纪录片", "Documentary"),
}
STATIC_SIZE = (640, 360)
MOTION_SIZE = (480, 270)
STATIC_QUALITY = 82
MOTION_QUALITY = 70
ANIMATION_DURATION = 12
ANIMATION_FPS = 15
# 剧照不放进仓库，生成预览时才下载到系统临时目录。来源见 images/CREDITS.md
ART = Path(tempfile.gettempdir()) / "mcg-preview-art"
# 吉卜力工作室在官网公开的场面写真，文件名就是官网图库里的编号
GALLERY = "https://www.ghibli.jp/gallery/{}.jpg"
# 每款风格的预览画面，按构图逐款挑选；第一张是静态示例和动图的第一帧
STYLE_ART = {
    1: ["mononoke048", "chihiro020", "howl049", "majo011", "totoro036", "ponyo049"],
    2: ["laputa036", "chihiro043", "howl048", "totoro041", "mononoke010", "chihiro032"],
    3: ["majo001", "totoro044", "howl005", "chihiro049"],
    4: ["howl044", "chihiro045", "majo024", "mononoke033", "totoro036", "laputa004"],
    5: ["chihiro020", "totoro019", "ponyo024", "howl035", "laputa038", "majo038"],
    6: ["chihiro042", "totoro029", "howl049", "mononoke048", "majo011", "ponyo005"],
    7: ["howl049", "chihiro043", "totoro041", "mononoke010", "majo038", "ponyo049"],
    8: ["ponyo049", "chihiro045", "chihiro020", "majo011", "totoro036", "laputa004", "howl044", "mononoke048"],
    9: ["chihiro045", "totoro036", "howl048", "mononoke033", "majo024", "laputa038"],
    10: ["howl048", "chihiro032", "totoro029", "majo001", "mononoke048", "ponyo024"],
    11: ["howl044", "mononoke033", "chihiro020", "totoro036", "ponyo049", "majo011"],
    12: ["howl048", "chihiro043", "totoro041", "mononoke010"],
}
TYPOGRAPHY_ART = ["howl044", "totoro029", "chihiro020", "majo011"]
ART_SOURCES = {name: GALLERY.format(name)
               for name in sorted({name for names in STYLE_ART.values() for name in names} | set(TYPOGRAPHY_ART))}
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


def art_folder(names, folder, art_dir=None):
    for position, name in enumerate(names, 1):
        if art_dir is not None:
            path = next((Path(art_dir) / f"{name}{ext}" for ext in (".jpg", ".png", ".webp")
                         if (Path(art_dir) / f"{name}{ext}").is_file()), None)
            if path is None:
                raise FileNotFoundError(f"本地素材缺失：{name}.jpg/.png/.webp（{art_dir}）")
        else:
            ART.mkdir(parents=True, exist_ok=True)
            path = ART / f"{name}.jpg"
            if not path.exists():
                request = urllib.request.Request(ART_SOURCES[name], headers={"User-Agent": "cover-preview-render"})
                with urllib.request.urlopen(request, timeout=60) as response:
                    with Image.open(BytesIO(response.read())) as image:
                        image.convert("RGB").save(path, quality=92)
        shutil.copyfile(path, Path(folder) / f"{position}{path.suffix.lower()}")
    return Path(folder)


def style_fonts(spec, title=TITLE):
    main = spec.main_font if any(ord(char) >= 0x2E80 for char in title[0]) else spec.main_font_latin
    return font_path(main), font_path(spec.sub_font)


def render_motion(info, source, title, fonts, display):
    from app.plugins.mediacovergenerator.style.covers import design_for, _accent_and_tint
    from app.plugins.mediacovergenerator.style.faces import DETECT_SIZE
    from app.plugins.mediacovergenerator.style.framing import load_pictures

    # 与 create_cover 使用同样的素材准备与时间轴，保留原始颜色后直接编码 WebP。
    images = load_pictures(source, max(info.slots, 6), max(int(max(MOTION_SIZE) * 1.6), DETECT_SIZE))
    if not images:
        raise RuntimeError(f"风格 {info.index} 没有可用素材")
    accent, tint = _accent_and_tint(images[0].main.image, info, None)
    cover = design_for(info.index)(MOTION_SIZE, images, title, fonts, info.type, accent, tint,
                                   50, 0.8, True, display_font=display)
    return encode_motion(cover.frame)


def encode_motion(render_frame):
    total = max(2, int(round(ANIMATION_FPS * ANIMATION_DURATION)))
    frames, durations = [], []
    previous = None
    for index in range(total):
        frame = render_frame(index / float(total)).convert("RGB")
        pixels = frame.tobytes()
        # 用累计时间取整避免逐帧取整后整圈时长漂移。
        duration = round((index + 1) * 1000 / ANIMATION_FPS) - round(index * 1000 / ANIMATION_FPS)
        if pixels == previous:
            durations[-1] += duration
        else:
            frames.append(frame.copy())
            durations.append(duration)
            previous = pixels
    buffer = BytesIO()
    frames[0].save(buffer, format="WEBP", save_all=True, append_images=frames[1:], duration=durations,
                   loop=0, lossless=False, quality=MOTION_QUALITY, method=6)
    return buffer.getvalue()


def render_all(selected=None, art_dir=None):
    from app.plugins.mediacovergenerator.style.catalog import STYLE_CATALOG, style_info
    from app.plugins.mediacovergenerator.style.covers import create_cover
    from app.plugins.mediacovergenerator.ui_presets import TYPOGRAPHY_PRESETS

    size = (1280, 720)
    results, animations = {}, {}
    for info in STYLE_CATALOG:
        if selected and info.index not in selected:
            continue
        with tempfile.TemporaryDirectory() as folder:
            source = art_folder(STYLE_ART[info.index], folder, art_dir)
            title = STYLE_TITLES.get(info.index, TITLE)
            fonts = style_fonts(info.type, title)
            display = font_path(info.display) if info.display else None
            results[info.index] = create_cover(info.index, False, source, title, fonts, info.type, size,
                                               display_font=display)
            animations[info.index] = render_motion(info, source, title, fonts, display)

    # 字体预设统一用标题最清楚的预告风格展示，差异都来自字体
    showcase = style_info(4)
    typography = {}
    with tempfile.TemporaryDirectory() as folder:
        source = art_folder(TYPOGRAPHY_ART, folder, art_dir) if not selected else None
        for preset in TYPOGRAPHY_PRESETS if not selected else ():
            values = preset["values"]
            fonts = (
                font_path(showcase.type.main_font if values["zh_font_preset"] == "auto" else values["zh_font_preset"]),
                font_path(showcase.type.sub_font if values["en_font_preset"] == "auto" else values["en_font_preset"]),
            )
            typography[preset["key"]] = create_cover(4, False, source, TITLE, fonts, showcase.type, size)

    failed = [key for group in (results, typography, animations) for key, value in group.items() if not value]
    if failed:
        raise SystemExit(f"风格渲染失败: {failed}")
    return (
        {index: decode(data) for index, data in results.items()},
        {key: decode(data) for key, data in typography.items()},
        animations,
    )


def write_outputs(images, typography, animations):
    output = REPO / "images"
    output.mkdir(parents=True, exist_ok=True)
    for plugin_dir in PLUGIN_DIRS:
        (plugin_dir / "examples").mkdir(parents=True, exist_ok=True)
    static = {f"style_{index}.webp": image for index, image in images.items()}
    static.update({f"type_{key}.webp": image for key, image in typography.items()})
    for name, image in static.items():
        target = output / name
        image.resize(STATIC_SIZE, Image.Resampling.LANCZOS).save(
            target, format="WEBP", quality=STATIC_QUALITY, method=6)
        for plugin_dir in PLUGIN_DIRS:
            shutil.copyfile(target, plugin_dir / "examples" / name)
    for index, data in animations.items():
        (output / f"style_{index}_motion.webp").write_bytes(data)
    print(f"预览图已更新，静态示例 {len(static)} 张，"
          f"动图样张 {sum(len(data) for data in animations.values()) // 1024} KB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="重新生成 V3 封面预览")
    parser.add_argument("--styles", nargs="+", type=int, help="只更新指定风格，保留其他预览")
    parser.add_argument("--art-dir", type=Path, help="使用本地 <name>.jpg/.png/.webp 素材，不联网")
    args = parser.parse_args()
    install_stubs()
    write_outputs(*render_all(set(args.styles) if args.styles else None, args.art_dir))
