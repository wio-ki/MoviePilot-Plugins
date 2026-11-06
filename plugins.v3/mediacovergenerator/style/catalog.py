"""
风格清单。每款风格都是背景构图和文字排版的整体设计，文字规格写在这里，构图在 covers.py。

字号、间距都以 1080p 为基准；字距是字号的倍数，可以为负。设置页里填写的值会逐项覆盖这里的值，
留空就跟随风格。这个模块不依赖 PIL 和 MoviePilot，设置页也能直接读取。

排版的原则：标题用清爽的 Bold，字距略收，留出画面；副标题小而清楚，保留原有大小写，不再拉开字距。
副标题在 1080p 下不小于 56，缩到 400×225 的动图也能看清。
"""
from dataclasses import dataclass, replace
from typing import Optional, Tuple

MINT = (88, 221, 163)


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
    sub_tracking: float = 0.01
    # 主副标题间距
    spacing: float = 30.0
    # 主标题折行时的行高
    line_height: float = 1.08
    # 副标题折行时的额外行距
    sub_line_gap: float = 12.0
    offset_y: float = 0.0
    main_upper: bool = False
    sub_upper: bool = False


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
        1, "序幕", "满屏竖幅画廊，一幅展开，标题和标签落在展开的画面里", "画幅轮流展开，标题随之移动", 6, True, MINT,
        TypeSpec("glowbold", "interbold", "intersemi", 192, 60,
                 main_tracking=0.01, main_tracking_latin=-0.02, spacing=34),
    ),
    StyleInfo(
        2, "巨幕", "满幅大图配粗宋体片名，角落的玻璃小窗预告下一张", "大图斜向划过换幕", 4, False, MINT,
        TypeSpec("serifblack", "dmserif", "intersemi", 240, 60,
                 main_tracking=0.03, main_tracking_latin=-0.005, spacing=40),
    ),
    StyleInfo(
        3, "流光", "黑底上的超大标题，一半白字一半透出画面", "画面在字里来回流动", 3, False, MINT,
        TypeSpec("glowheavy", "interblack", "intersemi", 480, 66,
                 main_tracking=0.0, main_tracking_latin=-0.03, spacing=44),
    ),
    StyleInfo(
        4, "预告", "薄荷绿斜角飘带领起标题，右侧是平行四边形的大图", "画面沿斜边切换", 4, False, MINT,
        TypeSpec("glowbold", "interbold", "intersemi", 214, 60,
                 main_tracking=0.01, main_tracking_latin=-0.02, spacing=38),
    ),
    StyleInfo(
        5, "拼贴", "错落的圆角拼贴，长体标题放在左下的卡片里", "拼贴逐格换图", 8, True, MINT,
        TypeSpec("glowcond", "anton", "intersemi", 214, 60,
                 main_tracking=0.0, main_tracking_latin=0.0, spacing=30),
    ),
    StyleInfo(
        6, "榜单", "深色长卡，竖排副标题、镂空大字和圆角大图并排", "字中画面流动", 3, False, MINT,
        TypeSpec("glowheavy", "interblack", "intersemi", 410, 60,
                 main_tracking=-0.005, main_tracking_latin=-0.035, spacing=30),
    ),
    StyleInfo(
        7, "详情", "虚化背景上的预告大卡，右侧标题和播放键，下方露出同系列", "大卡换图，底排缩略图传送", 6, False, MINT,
        TypeSpec("glowbold", "interbold", "intersemi", 186, 60,
                 main_tracking=0.01, main_tracking_latin=-0.02, spacing=30),
    ),
    StyleInfo(
        8, "轮播", "栏目标题配左右箭头，下方一排带序号的横版卡片", "卡片整排滑动", 6, False, MINT,
        TypeSpec("glowbold", "interbold", "intersemi", 176, 60,
                 main_tracking=0.01, main_tracking_latin=-0.02, spacing=26),
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
