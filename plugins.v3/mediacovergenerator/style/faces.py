"""
人脸检测：用 numpy 执行 OpenCV 格式的 LBP 级联模型，不需要额外安装 OpenCV。

模型放在 style/cascades 目录：真人正脸、真人侧脸来自 OpenCV，动画脸来自 lbpcascade_animeface。
检测结果只用于决定怎样取景，宁可漏检（退回按内容取景）也不要误检，所以合并框时要求足够多的相邻命中。
"""
import threading
import xml.etree.ElementTree as ElementTree
from collections import OrderedDict
from pathlib import Path

import numpy as np
from PIL import Image

CASCADE_DIR = Path(__file__).parent / "cascades"
# 检测用的图长边。够看清画面里偏小的脸，又不至于太慢
DETECT_SIZE = 512
# 模型文件、水平翻转再检测一遍（模型只认一个朝向）、合并所需的最少相邻命中、缩放步长
CASCADES = {
    "anime": ("lbpcascade_animeface.xml", False, 3, 1.15),
    "frontal": ("lbpcascade_frontalface_improved.xml", False, 3, 1.15),
    "profile": ("lbpcascade_profileface.xml", True, 4, 1.15),
}
# 什么都没找到时放宽到这个命中数
WEAK_NEIGHBORS = 2
# 同一张图生成多款风格时不重复检测
RESULT_CACHE_SIZE = 256

_cache = {}
_cache_lock = threading.Lock()
_results = OrderedDict()


class Cascade:
    """解析后的级联模型。每个弱分类器用一个 LBP 特征查 256 位的表。"""

    def __init__(self, path):
        root = ElementTree.parse(path).getroot().find("cascade")
        self.width = int(root.findtext("width"))
        self.height = int(root.findtext("height"))
        features = [[int(value) for value in item.findtext("rect").split()] for item in root.find("features")]
        self.features = np.array(features, dtype=np.int32)
        self.stages = []
        for stage in root.find("stages"):
            threshold = float(stage.findtext("stageThreshold")) - 1e-5
            feature_ids, subsets, leaves = [], [], []
            for weak in stage.find("weakClassifiers"):
                nodes = weak.findtext("internalNodes").split()
                feature_ids.append(int(nodes[2]))
                subsets.append([int(value) & 0xFFFFFFFF for value in nodes[3:11]])
                leaves.append([float(value) for value in weak.findtext("leafValues").split()])
            self.stages.append((threshold, feature_ids, np.array(subsets, dtype=np.uint32), np.array(leaves, dtype=np.float32)))


def _load(name):
    with _cache_lock:
        if name not in _cache:
            path = CASCADE_DIR / CASCADES[name][0]
            _cache[name] = Cascade(path) if path.is_file() else None
        return _cache[name]


def _lbp_codes(integral, xs, ys, feature):
    """窗口内一个特征的 LBP 编码：3×3 宫格，周围八格和中间一格比大小。"""
    fx, fy, fw, fh = feature
    # 宫格的 16 个交点只查一次积分图，九个格子的和都由它们算出
    left = xs + fx
    top = ys + fy
    x1, x2, x3 = left + fw, left + fw * 2, left + fw * 3
    y1, y2, y3 = top + fh, top + fh * 2, top + fh * 3
    p00, p01, p02, p03 = integral[top, left], integral[top, x1], integral[top, x2], integral[top, x3]
    p10, p11, p12, p13 = integral[y1, left], integral[y1, x1], integral[y1, x2], integral[y1, x3]
    p20, p21, p22, p23 = integral[y2, left], integral[y2, x1], integral[y2, x2], integral[y2, x3]
    p30, p31, p32, p33 = integral[y3, left], integral[y3, x1], integral[y3, x2], integral[y3, x3]
    center = p22 - p12 - p21 + p11
    return (((p11 - p01 - p10 + p00) >= center) * 128
            + ((p12 - p02 - p11 + p01) >= center) * 64
            + ((p13 - p03 - p12 + p02) >= center) * 32
            + ((p23 - p13 - p22 + p12) >= center) * 16
            + ((p33 - p23 - p32 + p22) >= center) * 8
            + ((p32 - p22 - p31 + p21) >= center) * 4
            + ((p31 - p21 - p30 + p20) >= center) * 2
            + ((p21 - p11 - p20 + p10) >= center) * 1)


def _scan(cascade, gray, step):
    """在一个尺度上滑动窗口，逐级淘汰，返回通过全部级联的窗口左上角。"""
    height, width = gray.shape
    if height < cascade.height + 1 or width < cascade.width + 1:
        return np.empty(0, dtype=np.int32), np.empty(0, dtype=np.int32)
    integral = np.zeros((height + 1, width + 1), dtype=np.int64)
    integral[1:, 1:] = gray.cumsum(axis=0).cumsum(axis=1)
    grid_y, grid_x = np.mgrid[0:height - cascade.height + 1:step, 0:width - cascade.width + 1:step]
    xs, ys = grid_x.ravel().astype(np.int32), grid_y.ravel().astype(np.int32)
    for threshold, feature_ids, subsets, leaves in cascade.stages:
        if not xs.size:
            break
        scores = np.zeros(xs.size, dtype=np.float32)
        for weak, feature_id in enumerate(feature_ids):
            codes = _lbp_codes(integral, xs, ys, cascade.features[feature_id])
            hit = (subsets[weak][codes >> 5] >> (codes & 31).astype(np.uint32)) & 1
            scores += np.where(hit == 1, leaves[weak, 0], leaves[weak, 1])
        keep = scores >= threshold
        xs, ys = xs[keep], ys[keep]
    return xs, ys


def _overlap(first, second):
    """两个框的相交面积占较小框的比例。"""
    x0, y0 = max(first[0], second[0]), max(first[1], second[1])
    x1, y1 = min(first[0] + first[2], second[0] + second[2]), min(first[1] + first[3], second[1] + second[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0) / float(min(first[2] * first[3], second[2] * second[3]))


def _group(boxes):
    """把位置和大小都接近的命中框归成一组，和 OpenCV 的 groupRectangles 思路相同。返回 [(框, 命中数)]。"""
    groups = []
    for box in boxes:
        for group in groups:
            mean = group["sum"] / group["count"]
            delta = 0.2 * (min(box[2], mean[2]) + min(box[3], mean[3])) * 0.5
            if (abs(box[0] - mean[0]) <= delta and abs(box[1] - mean[1]) <= delta
                    and abs(box[0] + box[2] - mean[0] - mean[2]) <= delta
                    and abs(box[1] + box[3] - mean[1] - mean[3]) <= delta):
                group["sum"] += box
                group["count"] += 1
                break
        else:
            groups.append({"sum": np.array(box, dtype=np.float64), "count": 1})
    return [(group["sum"] / group["count"], group["count"]) for group in groups]


def _detect_one(cascade, source, scale_step, min_face, max_face):
    """多尺度检测，返回 [(x, y, w, h, 命中数)]，坐标对应 source。"""
    width, height = source.size
    boxes = []
    scale = max(1.0, min_face / float(cascade.width))
    while True:
        window = cascade.width * scale, cascade.height * scale
        if window[0] > min(width, max_face) or window[1] > height:
            break
        scaled_w, scaled_h = int(round(width / scale)), int(round(height / scale))
        if scale == 1.0:
            scaled = source
        else:
            scaled = source.resize((scaled_w, scaled_h), Image.Resampling.BILINEAR)
        gray = np.asarray(scaled, dtype=np.int32)
        xs, ys = _scan(cascade, gray, 1 if scale >= 2.0 else 2)
        for x_pos, y_pos in zip(xs.tolist(), ys.tolist()):
            boxes.append((x_pos * width / float(scaled_w), y_pos * height / float(scaled_h), window[0], window[1]))
        scale *= scale_step
    return [(*box, count) for box, count in _group(boxes)]


def detect_faces(image, key=None, strong_only=False):
    """
    返回画面里的脸：[(x0, y0, x1, y1, kind)]，坐标是 0~1 的比例。kind 为 anime、frontal 或 profile。
    模型文件不存在时返回空列表，调用方退回按内容取景。key 标识图片内容，相同的 key 不重复检测。
    strong_only 为真时不接受命中少的结果，也不找侧脸：取景时宁可有个线索，评判一张图时误检会冤枉好图，
    而且要评判的候选多，得快。
    """
    if key is not None:
        key = (key, strong_only)
        with _cache_lock:
            if key in _results:
                _results.move_to_end(key)
                return _results[key]
    result = _detect(image, strong_only)
    if key is not None:
        with _cache_lock:
            _results[key] = result
            while len(_results) > RESULT_CACHE_SIZE:
                _results.popitem(last=False)
    return result


def _detect(image, strong_only=False):
    width, height = image.size
    ratio = min(1.0, DETECT_SIZE / float(max(width, height)))
    gray = image.convert("L").resize((max(1, int(width * ratio)), max(1, int(height * ratio))), Image.Resampling.BILINEAR)
    gray_width, gray_height = gray.size
    # 只找占短边 7% 以上的脸：更小的不影响取景，还容易误检
    min_face = max(24, min(gray_width, gray_height) * 0.07)
    found = []
    weak = []
    for name, (_file, mirror, neighbors, step) in CASCADES.items():
        cascade = _load(name)
        # 侧脸模型要左右各跑一遍，已经找到脸时不值得再花这个时间；只评判图片时用不到侧脸
        if cascade is None or (mirror and (found or strong_only)):
            continue
        for flipped in (False, True) if mirror else (False,):
            source = gray.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if flipped else gray
            for x_pos, y_pos, box_w, box_h, count in _detect_one(cascade, source, step, min_face, gray_width):
                if flipped:
                    x_pos = gray_width - x_pos - box_w
                entry = ((x_pos, y_pos, box_w, box_h), count, name)
                if count >= neighbors:
                    found.append(entry)
                elif count >= WEAK_NEIGHBORS and name != "anime":
                    weak.append(entry)
    # 一张脸都没找到时才接受命中少的结果：误检最多让取景偏一点，总比完全没有线索好。
    # 动画脸模型在纹理上容易误报，不放宽
    found = found or ([] if strong_only else weak)
    # 不同模型找到同一张脸时，留命中更多的那个
    found.sort(key=lambda item: -item[1])
    result = []
    for box, _count, name in found:
        if any(_overlap(box, kept[0]) > 0.4 for kept in result):
            continue
        result.append((box, name))
    return [((box[0] / gray_width), (box[1] / gray_height), (box[0] + box[2]) / gray_width, (box[1] + box[3]) / gray_height, name)
            for box, name in result]
