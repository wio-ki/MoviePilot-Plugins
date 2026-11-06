"""
一键换字体的预设。设置页和 scripts/render_previews.py 共用这份数据。

只换字体，字号和间距仍然跟随风格：不同构图需要的字号差别很大，固定数值反而会破坏排版。
"""

TYPOGRAPHY_PRESETS = [
    {
        "key": "auto",
        "name": "跟随风格",
        "desc": "每款风格用设计好的字体和字号，推荐",
        "values": {"zh_font_preset": "auto", "en_font_preset": "auto"},
    },
    {
        "key": "gothic",
        "name": "粗黑",
        "desc": "思源黑体配 Poppins，现代干净",
        "values": {"zh_font_preset": "notoblack", "en_font_preset": "poppinssemi"},
    },
    {
        "key": "wide",
        "name": "细宽",
        "desc": "细体配超宽字母，电影片头感",
        "values": {"zh_font_preset": "notolight", "en_font_preset": "michroma"},
    },
    {
        "key": "smiley",
        "name": "得意",
        "desc": "得意黑配宽体字母，动漫海报感",
        "values": {"zh_font_preset": "smiley", "en_font_preset": "kronaone"},
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
    {
        "key": "literary",
        "name": "文艺",
        "desc": "粗雅宋配细长无衬线，书卷气",
        "values": {"zh_font_preset": "yasong", "en_font_preset": "JosefinSans"},
    },
    {
        "key": "marquee",
        "name": "招牌",
        "desc": "粗雅宋配复古招牌字，老影院味",
        "values": {"zh_font_preset": "yasong", "en_font_preset": "Phosphate"},
    },
    {
        "key": "classic",
        "name": "经典",
        "desc": "潮黑配装饰衬线，早期版本的默认",
        "values": {"zh_font_preset": "chaohei", "en_font_preset": "EmblemaOne"},
    },
]
