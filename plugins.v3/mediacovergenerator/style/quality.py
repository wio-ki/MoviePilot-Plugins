"""
选图：给一张素材打分，挑出适合做封面的图。

只用 numpy 和 Pillow，扣分的依据都是看得出来的毛病：
1. 太暗：整张图压得很暗，缩成小封面后什么都看不清
2. 不清晰：剧集截图、低清图，放大后发糊
3. 特写过近：一张脸占满画面，表情多半是剧中某一瞬间，不像海报
4. 颜色寡淡、发灰：灰蒙蒙的剧照、褪色的老海报，缩小后没有吸引力
5. 分辨率低：老剧常见几百像素宽的小海报和截图，放大到封面上全是马赛克
有人脸、亮度适中、清晰、色彩饱满的图加分，因为封面最吸引人的是人物。

海报和背景图分开看：竖版海报是专门设计的画面，构图、调色、片名都排好了，默认就比背景图可靠，
所以海报有一个基础加分，也不检查特写（海报上的大头像是设计）。背景图要到 GOOD_WIDE_SCORE 才算「确实好看」，
达不到时调用方会改用同一条目的海报。
动画的背景图多是官方宣传图，和海报一样好看，调用方对动画用更低的 GOOD_WIDE_SCORE_ANIME。
没有检测背景图上的片名：不用 OCR 模型时，人群、动画线稿都会被当成文字，误伤的好图比抓到的片名多。
也没有检测边框和黑边：在真实片库里试过，抓到的几乎都是深色或白底的设计款海报，误伤比命中多。
"""
from dataclasses import dataclass, field
from typing import List

import numpy as np
from PIL import Image, ImageFilter

from app.plugins.mediacovergenerator.style.faces import detect_faces

# 评估用的尺寸：清晰度和文字检测都和尺寸有关，统一缩到这个宽度再算
ASSESS_WIDTH = 480
# 判断特写只需要找大脸，在更小的图上检测，快好几倍
FACE_WIDTH = 256
# 低于这个分数的图，候选够用时不用
ACCEPT_SCORE = -0.8
# 竖版海报的基础加分
POSTER_BONUS = 0.8
# 背景图到这个分数才算好看：没有毛病，并且至少有一样长处（人物大小合适，或者色彩饱满）。
# 用真实媒体库的 73 张背景图校准：表情特写的剧照、灰蒙蒙的合影、切掉一半的片名都落在这条线以下
GOOD_WIDE_SCORE = 0.3
# 动画的背景图：没有明显毛病就用
GOOD_WIDE_SCORE_ANIME = -0.2
# 原图长边低于前一个值算分辨率低，低于后一个值略微扣分：(海报, 横图)
LOW_RESOLUTION = ((600, 900), (960, 1280))
# 宽高比低于这个值算竖版海报
POSTER_ASPECT = 0.9


@dataclass
class Assessment:
    score: float
    reasons: List[str] = field(default_factory=list)
    brightness: float = 0.0
    sharpness: float = 0.0
    largest_face: float = 0.0
    colorfulness: float = 0.0
    poster: bool = False

    def describe(self):
        return "、".join(self.reasons) if self.reasons else "合适"


def _prepare(image):
    image = image.convert("RGB")
    # 竖版海报按高度对齐：小图是按长边缩的，把它放大到同样的宽度会让清晰度偏低
    width = ASSESS_WIDTH
    if image.width / float(max(1, image.height)) < POSTER_ASPECT:
        width = max(1, int(round(ASSESS_WIDTH * image.width / float(image.height))))
    if image.width != width:
        height = max(1, int(round(image.height * width / float(image.width))))
        image = image.resize((width, height), Image.Resampling.BILINEAR)
    return image


def _resolution(image, requested, native):
    """原图的长边。不知道原图尺寸时，下载的小图比请求的尺寸还小，说明原图就这么大。"""
    if native and min(native) > 0:
        return max(native)
    if requested and max(image.size) < requested * 0.96:
        return max(image.size)
    return None


def assess(image, key=None, requested=None, native=None) -> Assessment:
    """
    给素材打分，越高越适合做封面。
    requested 是下载这张小图时请求的最大边长，native 是媒体服务器报告的原图 (宽, 高)，都可以不给。
    """
    small = _prepare(image)
    rgb = np.asarray(small, dtype=np.float32)
    luma = rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722
    brightness = float(luma.mean())
    highlights = float(np.percentile(luma, 90))
    gray = small.convert("L")
    laplace = np.asarray(gray.filter(ImageFilter.Kernel((3, 3), (0, 1, 0, 1, -4, 1, 0, 1, 0), 1, 128)),
                         dtype=np.float32) - 128.0
    sharpness = float(laplace.var())

    # Hasler-Süsstrunk 色彩丰富度：灰蒙蒙的剧照在 15 以下，设计过的海报和动画多在 35 以上
    red_green = rgb[..., 0] - rgb[..., 1]
    yellow_blue = (rgb[..., 0] + rgb[..., 1]) / 2.0 - rgb[..., 2]
    colorfulness = float(np.hypot(red_green.std(), yellow_blue.std())
                         + 0.3 * np.hypot(red_green.mean(), yellow_blue.mean()))
    poster = image.width / float(max(1, image.height)) < POSTER_ASPECT
    contrast = float(luma.std())

    score, reasons = (POSTER_BONUS if poster else 0.0), []
    long_side = _resolution(image, requested, native)
    if long_side:
        bad, meh = LOW_RESOLUTION[0 if poster else 1]
        if long_side < bad:
            score -= 1.0
            reasons.append(f"分辨率低（{int(long_side)}px）")
        elif long_side < meh:
            score -= 0.3 * (meh - long_side) / float(meh - bad)
    if colorfulness < 14:
        score -= 0.6
        reasons.append("颜色寡淡")
    elif colorfulness < 24:
        score -= (24 - colorfulness) / 10.0 * 0.4
    elif colorfulness > 42:
        score += 0.3
    if brightness < 38:
        score -= 3.0
        reasons.append("太暗")
    elif brightness < 62:
        score -= (62 - brightness) / 24.0 * 1.5
        reasons.append("偏暗")
    elif brightness > 205:
        score -= 0.6
        reasons.append("底色太白")
    if highlights < 90 and brightness >= 38:
        score -= 0.5
        reasons.append("没有亮部")
    # 亮度的标准差：真实片库的海报中位数约 64，最低 33；褪色、起雾的图在 30 以下
    if contrast < 28 and brightness >= 38:
        score -= 0.6
        reasons.append("发灰")
    elif contrast < 34:
        score -= (34 - contrast) / 6.0 * 0.3

    if sharpness < 260:
        score -= 1.6
        reasons.append("不清晰，像剧集截图")
    elif sharpness < 500:
        score -= (500 - sharpness) / 240.0 * 0.8

    largest = 0.0
    if poster:
        return Assessment(score, reasons, brightness, sharpness, largest, colorfulness, True)
    try:
        thumb = small.resize((FACE_WIDTH, max(1, int(round(small.height * FACE_WIDTH / float(small.width))))),
                             Image.Resampling.BILINEAR)
        faces = detect_faces(thumb, key, strong_only=True)
    except Exception:
        faces = []
    if faces:
        largest = max(y1 - y0 for _x0, y0, _x1, y1, _kind in faces)
        # 0.55 以上才算：《汉尼拔》那种近景海报大约 0.5，仍是好封面
        if largest > 0.55:
            score -= 1.2
            reasons.append("脸部特写过近")
        # 0.4 以上多半是剧照里的近景，不扣分也不加分
        elif 0.1 <= largest <= 0.4:
            score += 0.5
    return Assessment(score, reasons, brightness, sharpness, largest, colorfulness, False)


def rank(candidates, required, keep_order=False, minimum=None):
    """
    candidates 是 [(条目, Assessment 或 None)]，没有评估结果的当作合格。
    最多选 required 个合格的；合格的不到 minimum 个时，才用分数最高的不合格图补到 minimum，宁可少用几张也不拿差图凑数。
    keep_order 为真时保持原来的先后顺序（按入库时间排序时），否则按分数从高到低，第一张就是静态封面和动图的第一帧。
    """
    minimum = required if minimum is None else min(minimum, required)
    indexed = list(enumerate(candidates))
    good = [entry for entry in indexed if entry[1][1] is None or entry[1][1].score >= ACCEPT_SCORE]
    rest = [entry for entry in indexed if entry not in good]

    def by_score(entry):
        assessment = entry[1][1]
        return -(assessment.score if assessment else 0.0)

    if keep_order:
        chosen = good[:required]
    else:
        chosen = sorted(good, key=by_score)[:required]
    if len(chosen) < minimum:
        chosen += sorted(rest, key=by_score)[:minimum - len(chosen)]
    if keep_order:
        chosen.sort(key=lambda entry: entry[0])
    skipped = [entry[1] for entry in indexed if entry not in chosen]
    return [entry[1] for entry in chosen], skipped
