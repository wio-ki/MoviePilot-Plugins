"""
绘制插件图标，输出到 icons/animated-cover.png：

    python scripts/render_icon.py

MoviePilot 用图标的主色给插件卡片上色，所以底色要有足够的饱和度。前端会按地址缓存图标，换图时要换文件名。
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

OUTPUT = Path(__file__).resolve().parent.parent / "icons" / "animated-cover.png"
SIZE = 512
SCALE = 4
FULL = SIZE * SCALE
VIOLET = (126, 52, 228)


def px(value):
    return int(value * SCALE)


def gradient(stops):
    """沿左上到右下铺一层多段渐变。"""
    ramp = np.add.outer(np.linspace(0, 0.5, FULL), np.linspace(0, 0.5, FULL))
    positions = [position for position, _ in stops]
    channels = [np.interp(ramp, positions, [color[i] for _, color in stops]) for i in range(3)]
    return Image.fromarray(np.dstack(channels).astype(np.uint8)).convert("RGBA")


def rounded_mask(box, radius, opacity=255):
    mask = Image.new("L", (FULL, FULL), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=opacity)
    return mask


def card(box, radius, colors, opacity=255):
    layer = gradient(colors)
    layer.putalpha(rounded_mask(box, radius, opacity))
    return layer


def add_with_shadow(canvas, layer, blur, strength, drop):
    shadow = Image.new("RGBA", layer.size, (34, 10, 72, 0))
    shadow.putalpha(layer.getchannel("A").point(lambda value: int(value * strength)))
    moved = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    moved.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(px(blur))), (0, px(drop)))
    canvas.alpha_composite(moved)
    canvas.alpha_composite(layer)


def rounded_triangle(points, corner, color):
    """三条粗线加三个圆，拐角才是真正的圆角。"""
    layer = Image.new("RGBA", (FULL, FULL), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.polygon(points, fill=color)
    for start, end in zip(points, points[1:] + points[:1]):
        draw.line([start, end], fill=color, width=corner * 2)
    for x_pos, y_pos in points:
        draw.ellipse([x_pos - corner, y_pos - corner, x_pos + corner, y_pos + corner], fill=color)
    return layer


def main():
    canvas = gradient([(0.0, (102, 62, 246)), (0.55, (150, 48, 214)), (1.0, (236, 78, 146))])

    # 身后两张错开的封面，转角递增，像正在滑动
    back_box = (px(116), px(150), px(420), px(322))
    warm = [(0.0, (255, 190, 104)), (1.0, (240, 92, 122))]
    for angle, opacity in ((15, 170), (7, 235)):
        layer = card(back_box, px(26), warm, opacity)
        layer = layer.rotate(angle, resample=Image.Resampling.BICUBIC, center=(px(116), px(322)))
        add_with_shadow(canvas, layer, 12, 0.32, 6)

    # 最前面的封面：米白底、播放键和进度条
    front_box = (px(72), px(186), px(412), px(378))
    cream = [(0.0, (255, 251, 243)), (1.0, (255, 226, 198))]
    add_with_shadow(canvas, card(front_box, px(30), cream), 18, 0.5, 14)

    center_x, center_y, extent = px(240), px(272), px(34)
    points = [(center_x - extent * 0.62, center_y - extent), (center_x - extent * 0.62, center_y + extent),
              (center_x + extent * 1.02, center_y)]
    canvas.alpha_composite(rounded_triangle(points, px(9), VIOLET + (255,)))

    bar = Image.new("RGBA", (FULL, FULL), (0, 0, 0, 0))
    draw = ImageDraw.Draw(bar)
    draw.rounded_rectangle([px(102), px(347), px(382), px(356)], radius=px(5), fill=VIOLET + (44,))
    draw.rounded_rectangle([px(102), px(347), px(278), px(356)], radius=px(5), fill=(240, 108, 92, 255))
    canvas.alpha_composite(bar)

    canvas.putalpha(rounded_mask((0, 0, FULL - 1, FULL - 1), px(116)))
    OUTPUT.parent.mkdir(exist_ok=True)
    canvas.resize((SIZE, SIZE), Image.Resampling.LANCZOS).save(OUTPUT, optimize=True)
    print(f"已生成 {OUTPUT}")


if __name__ == "__main__":
    main()
