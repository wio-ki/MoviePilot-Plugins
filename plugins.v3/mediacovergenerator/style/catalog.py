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
    # 这款构图完整展示竖版海报（否则优先横版背景图，避免切开海报上的片名）
    poster: bool
    # 点缀色；None 表示从每张海报取色
    accent: Optional[Tuple[int, int, int]]
    type: TypeSpec
    # 这款风格自带的展示字体，和标题字体无关
    display: Optional[str] = None


STYLE_CATALOG = [
    StyleInfo(
        1, "序幕", "满屏竖幅画廊，一幅展开，标题和标签落在展开的画面里", "画幅舒展换位，标题淡出再升起", 6, False, MINT,
        TypeSpec("glowbold", "interbold", "intersemi", 192, 60,
                 main_tracking=0.01, main_tracking_latin=-0.02, spacing=34),
    ),
    StyleInfo(
        2, "巨幕", "满幅大图配粗宋体片名，角落的玻璃小窗预告下一张", "镜头推近叠化，小窗上滑换片", 4, False, MINT,
        TypeSpec("serifblack", "dmserif", "intersemi", 240, 60,
                 main_tracking=0.03, main_tracking_latin=-0.005, spacing=40),
    ),
    StyleInfo(
        3, "404", "黑底上巨大的 404 透出一整幅图，主副标题清楚地写在下面", "一阵信号不良的闪烁后变成完整大图，再闪回下一张的 404", 4, False, MINT,
        TypeSpec("glowbold", "interbold", "intersemi", 150, 58,
                 main_tracking=0.02, main_tracking_latin=-0.02, sub_tracking=0.04, spacing=22),
        display="interblack",
    ),
    StyleInfo(
        4, "预告", "右侧平行四边形的大图，左下角一排同样斜度的细刻度和编号", "斜切成条错开滑入，刻度点亮下一根", 4, False, MINT,
        TypeSpec("glowbold", "interbold", "intersemi", 250, 60,
                 main_tracking=0.01, main_tracking_latin=-0.02, spacing=38),
    ),
    StyleInfo(
        5, "环幕", "站在弧形巨幕中间，两侧画面朝你包过来，标题居中", "整圈弧幕平稳转动", 6, False, MINT,
        TypeSpec("glowheavy", "interblack", "intersemi", 214, 60,
                 main_tracking=0.02, main_tracking_latin=-0.025, spacing=30),
    ),
    StyleInfo(
        6, "景深", "大卡后面层层后退的卡片，标题落在最前面一张上", "前卡飞向镜头，后卡依次上前", 5, False, MINT,
        TypeSpec("glowbold", "interbold", "intersemi", 186, 60,
                 main_tracking=0.01, main_tracking_latin=-0.02, spacing=30),
    ),
    StyleInfo(
        7, "详情", "虚化背景上的预告大卡，右侧标题和播放键，下方露出同系列", "缩略图放大成主卡，其余左移补位", 6, False, MINT,
        TypeSpec("glowbold", "interbold", "intersemi", 186, 60,
                 main_tracking=0.01, main_tracking_latin=-0.02, spacing=30),
    ),
    StyleInfo(
        8, "扇面", "海报沿大圆弧排成扇形，粗宋体标题居中在上方", "扇面转动一格，带一点回弹", 8, True, MINT,
        TypeSpec("serifblack", "dmserif", "intersemi", 196, 58,
                 main_tracking=0.03, main_tracking_latin=-0.005, spacing=18),
    ),
    # 海报系列：完整展示竖版海报；None 表示逐张取色。
    StyleInfo(
        9, "百叶", "海报三色调印在立体百叶窗上，片名在左，完整海报在右", "整幅画面像百叶窗一样逐条翻面", 3, True, None,
        TypeSpec("glowheavy", "interblack", "intersemi", 220, 56,
                 main_tracking=0.02, main_tracking_latin=-0.025, sub_tracking=0.16, spacing=34, sub_upper=True),
    ),
    StyleInfo(
        10, "对开", "整幅海报在左，右边是取自海报的色块和片名，前后两张颜色不重样", "新海报从上往下切入，色块带着片名从下往上升起", 3, True, None,
        TypeSpec("glowheavy", "interblack", "intersemi", 256, 56,
                 main_tracking=0.02, main_tracking_latin=-0.025, sub_tracking=0.16, spacing=40, sub_upper=True),
    ),
    StyleInfo(
        11, "月洞", "深色墙上开一扇顶天的大拱龛，整幅海报装在卡纸里，片名刻在右侧墙上", "旧画顺着龛口滑上去，下一幅从下面推上来，墙的颜色跟着换", 3, True, None,
        TypeSpec("serifblack", "dmserif", "intersemi", 228, 56,
                 main_tracking=0.02, main_tracking_latin=-0.025, sub_tracking=0.16, spacing=32, sub_upper=True),
    ),
    StyleInfo(
        12, "展墙", "纸墙上一块说明板写着竖排片名，横杆上挂着一排大小不一的画", "整排挂画缓缓向左滑过，一幅接一幅从板后出来", 3, True, None,
        TypeSpec("serifblack", "dmserif", "intersemi", 236, 54,
                 main_tracking=0.02, main_tracking_latin=-0.025, sub_tracking=0.16, spacing=30, sub_upper=True),
    ),
    # 新增 4 款精修风格（13–16）：焦距、叠影、胶卷、窥视
    StyleInfo(
        13, "焦距", "现代微单取景器构图：纤细四角对焦括号、中心留空十字准星、曝光标尺与镜头参数", "半按快门绿框合焦锁定，上下暗钛双帘快门 2.6× 疾速中缝合拢闪拍切出新画", 4, False, MINT,
        TypeSpec("glowbold", "interbold", "intersemi", 206, 58,
                 main_tracking=0.02, main_tracking_latin=-0.02, sub_tracking=0.12, spacing=28, sub_upper=True),
    ),
    StyleInfo(
        14, "叠影", "美术馆典藏展墙光影与渐层粗宋体，右侧三张精装棱边海报错层悬浮带景深微光", "前卡向右上方弧线飞旋钻回牌底，后方海报弹性扶正迎光扫过镜面高光", 3, True, None,
        TypeSpec("serifblack", "dmserif", "intersemi", 228, 56,
                 main_tracking=0.03, main_tracking_latin=-0.01, sub_tracking=0.14, spacing=32, sub_upper=True),
    ),
    StyleInfo(
        15, "胶卷", "大斜角 35mm 电影胶片带斜贯画布，镂空透光齿孔映出暗房彩色流光", "整条胶片带连同齿孔疾速斜向过片，带运动拖影与抓片回弹", 4, False, MINT,
        TypeSpec("glowheavy", "interblack", "intersemi", 204, 56,
                 main_tracking=0.02, main_tracking_latin=-0.02, sub_tracking=0.14, spacing=24, sub_upper=True),
    ),
    StyleInfo(
        16, "窥视", "暗红血脉缠绕的漆黑深渊中央，一只巨眼透视片库剧照，底部为枯骨白指甲划刻渗血片名与高对比骨绯副标", "血脉脉动蔓延，巨眼闭合成猩红血缝切出新画再骤然睁开", 4, False, MINT,
        TypeSpec("serifblack", "dmserif", "interbold", 184, 54,
                 main_tracking=0.06, main_tracking_latin=0.02, sub_tracking=0.22, spacing=18, sub_upper=True),
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
