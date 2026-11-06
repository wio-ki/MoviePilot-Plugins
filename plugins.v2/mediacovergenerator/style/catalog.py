"""
风格清单。每款风格都是背景构图和文字排版的整体设计，文字规格写在这里，构图在 covers.py。

字号、间距都以 1080p 为基准；字距是字号的倍数，可以为负。设置页里填写的值会逐项覆盖这里的值，
留空就跟随风格。这个模块不依赖 PIL 和 MoviePilot，设置页也能直接读取。
"""
from dataclasses import dataclass, replace
from typing import Optional, Tuple

MINT = (88, 221, 163)
ORANGE = (234, 152, 43)
CRIMSON = (214, 34, 70)


@dataclass(frozen=True)
class TypeSpec:
    main_font: str
    main_font_latin: str
    sub_font: str
    main_size: float
    sub_size: float
    # 主标题字距：中文、纯西文标题分开设置
    main_tracking: float = 0.0
    main_tracking_latin: float = 0.0
    sub_tracking: float = 0.1
    # 主副标题间距
    spacing: float = 30.0
    # 主标题折行时的行高
    line_height: float = 1.06
    # 副标题折行时的额外行距
    sub_line_gap: float = 12.0
    offset_y: float = 0.0
    main_upper: bool = False
    sub_upper: bool = True


@dataclass(frozen=True)
class StyleInfo:
    index: int
    name: str
    desc: str
    motion: str
    # 一屏同时展示的图片数量，也是静态封面需要的素材数
    slots: int
    # 这款构图更适合竖版海报（否则优先横版背景图）
    poster: bool
    accent: Tuple[int, int, int]
    type: TypeSpec


STYLE_CATALOG = [
    StyleInfo(
        1, "切片", "竖向长条切片，一片展开，粗体大标题压在底部", "切片轮流展开", 6, True, MINT,
        TypeSpec("notoblack", "poppinsxb", "poppinssemi", 240, 76,
                 main_tracking=-0.015, main_tracking_latin=-0.035, sub_tracking=0.2, spacing=34),
    ),
    StyleInfo(
        2, "巨幕", "满幅大图配超宽细体片名，角落露出下一张", "大图渐变轮播", 4, False, MINT,
        TypeSpec("notolight", "michroma", "michroma", 232, 54,
                 main_tracking=0.2, main_tracking_latin=0.0, sub_tracking=0.34, spacing=46, main_upper=True),
    ),
    StyleInfo(
        3, "镂字", "巨大的镂空标题透出图片，副标题竖排在侧", "字中画面流动", 3, False, MINT,
        TypeSpec("notoblack", "poppinsxb", "poppinssemi", 400, 62,
                 main_tracking=-0.03, main_tracking_latin=-0.045, sub_tracking=0.34),
    ),
    StyleInfo(
        4, "展墙", "倾斜的海报墙铺满左侧，标题靠右对齐", "海报墙错位滑动", 9, True, MINT,
        TypeSpec("notoblack", "poppinsxb", "poppinssemi", 280, 80,
                 main_tracking=-0.015, main_tracking_latin=-0.035, sub_tracking=0.2, spacing=36),
    ),
    StyleInfo(
        5, "瀑布", "错落的圆角拼贴，标题占据中央一格", "拼贴逐格换图", 8, True, MINT,
        TypeSpec("notoblack", "poppinsxb", "poppinssemi", 198, 58,
                 main_tracking=-0.01, main_tracking_latin=-0.03, sub_tracking=0.24, spacing=62),
    ),
    StyleInfo(
        6, "立柱", "高窄海报错落排开，大标题配竖排副标题", "海报横向传送", 5, True, MINT,
        TypeSpec("notoblack", "poppinsxb", "poppinssemi", 300, 70,
                 main_tracking=-0.02, main_tracking_latin=-0.04, sub_tracking=0.24),
    ),
    StyleInfo(
        7, "悬卡", "玻璃侧栏配悬浮大卡，橙色胶囊标签", "大卡轮播侧栏滚动", 5, False, ORANGE,
        TypeSpec("smiley", "kronaone", "kronaone", 270, 54,
                 main_tracking=0.0, main_tracking_latin=-0.02, sub_tracking=0.1, spacing=38),
    ),
    StyleInfo(
        8, "霓虹", "霓虹几何背景上一排海报，红色竖条领起标题", "霓虹流转海报传送", 6, True, CRIMSON,
        TypeSpec("smiley", "kronaone", "kronaone", 232, 50,
                 main_tracking=0.0, main_tracking_latin=-0.02, sub_tracking=0.14, spacing=24),
    ),
]
STYLE_COUNT = len(STYLE_CATALOG)


def style_info(index: int) -> StyleInfo:
    return next((item for item in STYLE_CATALOG if item.index == index), STYLE_CATALOG[0])


def apply_overrides(spec: TypeSpec, main_size: Optional[float] = None, sub_size: Optional[float] = None,
                    scale: float = 1.0, offset_y: Optional[float] = None, spacing: Optional[float] = None,
                    sub_line_gap: Optional[float] = None, main_tracking: Optional[float] = None,
                    sub_tracking: Optional[float] = None) -> TypeSpec:
    """用设置页填写的值覆盖风格的文字规格，None 表示跟随风格。"""
    scale = scale if scale and scale > 0 else 1.0
    changes = {
        "main_size": (spec.main_size if main_size is None else main_size) * scale,
        "sub_size": (spec.sub_size if sub_size is None else sub_size) * scale,
        "spacing": (spec.spacing if spacing is None else spacing) * scale,
        "sub_line_gap": (spec.sub_line_gap if sub_line_gap is None else sub_line_gap) * scale,
    }
    if offset_y is not None:
        changes["offset_y"] = offset_y
    if main_tracking is not None:
        changes["main_tracking"] = main_tracking
        changes["main_tracking_latin"] = main_tracking
    if sub_tracking is not None:
        changes["sub_tracking"] = sub_tracking
    return replace(spec, **changes)
