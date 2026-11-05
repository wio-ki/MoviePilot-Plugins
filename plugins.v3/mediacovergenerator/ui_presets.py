"""字体排版的一键预设。设置页和 scripts/render_previews.py 共用这份数据。"""

# 每个预设都给出字体搭配和完整的排版参数，字号以 1080p 为基准。
TYPOGRAPHY_PRESETS = [
    {
        "key": "classic",
        "name": "经典",
        "desc": "潮黑配装饰衬线，插件默认",
        "values": {
            "zh_font_preset": "chaohei", "en_font_preset": "EmblemaOne",
            "zh_font_size": 170, "en_font_size": 75, "title_scale": 1.0, "zh_font_offset": 0,
            "title_spacing": 40, "en_line_spacing": 40, "blur_size": 50, "color_ratio": 0.8,
        },
    },
    {
        "key": "modern",
        "name": "现代",
        "desc": "潮黑配细长无衬线，干净耐看",
        "values": {
            "zh_font_preset": "chaohei", "en_font_preset": "JosefinSans",
            "zh_font_size": 170, "en_font_size": 62, "title_scale": 1.0, "zh_font_offset": 0,
            "title_spacing": 34, "en_line_spacing": 30, "blur_size": 50, "color_ratio": 0.75,
        },
    },
    {
        "key": "impact",
        "name": "冲击",
        "desc": "大字号紧凑排列，远看也醒目",
        "values": {
            "zh_font_preset": "chaohei", "en_font_preset": "LilitaOne",
            "zh_font_size": 205, "en_font_size": 72, "title_scale": 1.0, "zh_font_offset": 0,
            "title_spacing": 26, "en_line_spacing": 28, "blur_size": 60, "color_ratio": 0.85,
        },
    },
    {
        "key": "literary",
        "name": "文艺",
        "desc": "粗雅宋配细长无衬线，书卷气",
        "values": {
            "zh_font_preset": "yasong", "en_font_preset": "JosefinSans",
            "zh_font_size": 165, "en_font_size": 56, "title_scale": 1.0, "zh_font_offset": 0,
            "title_spacing": 38, "en_line_spacing": 30, "blur_size": 40, "color_ratio": 0.7,
        },
    },
    {
        "key": "marquee",
        "name": "招牌",
        "desc": "粗雅宋配复古招牌字，老影院味",
        "values": {
            "zh_font_preset": "yasong", "en_font_preset": "Phosphate",
            "zh_font_size": 180, "en_font_size": 72, "title_scale": 1.0, "zh_font_offset": 0,
            "title_spacing": 34, "en_line_spacing": 32, "blur_size": 50, "color_ratio": 0.85,
        },
    },
    {
        "key": "quiet",
        "name": "留白",
        "desc": "小字号宽松排版，让画面说话",
        "values": {
            "zh_font_preset": "chaohei", "en_font_preset": "JosefinSans",
            "zh_font_size": 125, "en_font_size": 46, "title_scale": 1.0, "zh_font_offset": 0,
            "title_spacing": 30, "en_line_spacing": 24, "blur_size": 70, "color_ratio": 0.65,
        },
    },
]
