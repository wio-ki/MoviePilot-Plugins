"""
一键换字体的预设。设置页和 scripts/render_previews.py 共用这份数据。

只换字体，字号和间距仍然跟随风格：不同构图需要的字号差别很大，固定数值反而会破坏排版。
"""

TYPOGRAPHY_PRESETS = [
    {
        "key": "auto",
        "name": "跟随风格",
        "desc": "每款风格用设计好的字体和字号",
        "recommended": True,
        "values": {"zh_font_preset": "auto", "en_font_preset": "auto"},
    },
    {
        "key": "stream",
        "name": "清爽",
        "desc": "未来荧黑配 Inter，流媒体界面的干净感",
        "values": {"zh_font_preset": "glowbold", "en_font_preset": "intersemi"},
    },
    {
        "key": "cinema",
        "name": "影院",
        "desc": "思源宋体配 DM Serif，电影片名的厚重感",
        "values": {"zh_font_preset": "serifblack", "en_font_preset": "dmserif"},
    },
    {
        "key": "poster",
        "name": "海报",
        "desc": "未来荧黑长体配 Anton，紧凑有力",
        "values": {"zh_font_preset": "glowcond", "en_font_preset": "anton"},
    },
    {
        "key": "heavy",
        "name": "重磅",
        "desc": "未来荧黑 Heavy 配 Inter Black，分量最足",
        "values": {"zh_font_preset": "glowheavy", "en_font_preset": "interblack"},
    },
    {
        "key": "smiley",
        "name": "得意",
        "desc": "得意黑配 Inter，轻快的动漫感",
        "values": {"zh_font_preset": "smiley", "en_font_preset": "intersemi"},
    },
    {
        "key": "wide",
        "name": "细宽",
        "desc": "细体配超宽字母，科幻片头感",
        "values": {"zh_font_preset": "notolight", "en_font_preset": "michroma"},
    },
    {
        "key": "brush",
        "name": "笔墨",
        "desc": "庞门正道粗书体配笔刷字母",
        "values": {"zh_font_preset": "pangmen", "en_font_preset": "CaveatBrush"},
    },
    {
        "key": "ink",
        "name": "墨韵",
        "desc": "粗书体配细长无衬线，刚柔对比",
        "values": {"zh_font_preset": "pangmen", "en_font_preset": "JosefinSans"},
    },
]
