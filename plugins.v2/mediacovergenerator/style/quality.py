"""
选图：给一张素材打分，挑出适合做封面的图。

只用 numpy 和 Pillow，扣分的依据都是看得出来的毛病：
1. 太暗：整张图压得很暗，缩成小封面后什么都看不清
2. 不清晰：剧集截图、低清图，放大后发糊
3. 特写过近：一张脸占满画面，表情多半是剧中某一瞬间，不像海报
有人脸、亮度适中、清晰的图加分，因为封面最吸引人的是人物。
没有检测背景图上的片名：不用 OCR 模型时，人群、动画线稿都会被当成文字，误伤的好图比抓到的片名多。
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


@dataclass
class Assessment:
    score: float
    reasons: List[str] = field(default_factory=list)
    brightness: float = 0.0
    sharpness: float = 0.0
    largest_face: float = 0.0

    def describe(self):
        return "、".join(self.reasons) if self.reasons else "合适"


def _prepare(image):
    image = image.convert("RGB")
    if image.width != ASSESS_WIDTH:
        height = max(1, int(round(image.height * ASSESS_WIDTH / float(image.width))))
        image = image.resize((ASSESS_WIDTH, height), Image.Resampling.BILINEAR)
    return image


def assess(image, key=None) -> Assessment:
    """给素材打分，越高越适合做封面。"""
    small = _prepare(image)
    rgb = np.asarray(small, dtype=np.float32)
    luma = rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722
    brightness = float(luma.mean())
    highlights = float(np.percentile(luma, 90))
    gray = small.convert("L")
    laplace = np.asarray(gray.filter(ImageFilter.Kernel((3, 3), (0, 1, 0, 1, -4, 1, 0, 1, 0), 1, 128)),
                         dtype=np.float32) - 128.0
    sharpness = float(laplace.var())

    score, reasons = 0.0, []
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

    if sharpness < 260:
        score -= 1.6
        reasons.append("不清晰，像剧集截图")
    elif sharpness < 500:
        score -= (500 - sharpness) / 240.0 * 0.8

    largest = 0.0
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
        elif 0.1 <= largest <= 0.45:
            score += 0.5
    return Assessment(score, reasons, brightness, sharpness, largest)


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
