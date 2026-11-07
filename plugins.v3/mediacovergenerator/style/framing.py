"""
取景：决定一个画框里放哪张图、露出图的哪一部分。

三条规则，都是为了避免画面有切割感：
1. 一个条目通常同时有竖版海报和横版背景图，每个画框选比例最接近的那张，能不裁就不裁
2. 必须裁的时候围绕人脸取景。半张脸比完全不拍进去更难看，所以切到头的取景分数最低
3. 海报放进宽画框又保不住人物时不裁：完整显示海报，两侧用它自己的虚化图延伸
"""
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from app.log import logger
from app.plugins.mediacovergenerator.style.faces import detect_faces

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
# 备用素材的文件名：1.jpg 是首选图，1.alt.jpg 是同一条目另一种比例的图
ALT_SUFFIX = ".alt"
# 裁掉的部分在这个比例以内时看不出差别，不必检测人脸
TRIVIAL_LOSS = 0.04
# 竖图放进宽画框，保留比例低于这个值时，保不住人物就改为完整显示
MIN_KEEP_FOR_WIDE = 0.6
# 窗口滑动的候选位置数
WINDOW_STEPS = 81


def _ease(values):
    values = np.clip(values, 0.0, 1.0)
    return values * values * (3.0 - 2.0 * values)


class Source:
    """一张素材图和它的取景信息。"""

    def __init__(self, image, key=None):
        self.image = image
        self.width, self.height = image.size
        self.aspect = self.width / float(self.height)
        self.key = key
        self._heads = None
        self._profiles = None

    @property
    def heads(self):
        """画面里的头部区域：[(x0, y0, x1, y1, 权重)]，按权重从大到小。背景里的小脸权重很低。"""
        if self._heads is None:
            heads = []
            try:
                faces = detect_faces(self.image, self.key)
            except Exception as error:
                logger.warning(f"人脸检测失败，改为按内容取景: {error}")
                faces = []
            if faces:
                largest = max(face[2] - face[0] for face in faces) or 1.0
                for x0, y0, x1, y1, _kind in faces:
                    width, height = x1 - x0, y1 - y0
                    # 检测框只到额头和下巴，头发和脖子也要留出来
                    box = (max(0.0, x0 - width * 0.22), max(0.0, y0 - height * 0.42),
                           min(1.0, x1 + width * 0.22), min(1.0, y1 + height * 0.2))
                    heads.append((*box, (width / largest) ** 1.5))
                heads.sort(key=lambda head: -head[4])
            self._heads = heads
        return self._heads

    @property
    def major_heads(self):
        """主要人物：大小至少是最大那张脸的一半。"""
        return [head for head in self.heads if head[4] >= 0.35]

    def _profile(self, axis):
        """一维的内容分布：边缘和饱和度沿某个方向累加，竖图的底部多半是片名，权重降低。"""
        if self._profiles is None:
            thumb = self.image.convert("HSV").resize((64, 64), Image.Resampling.BILINEAR)
            data = np.asarray(thumb, dtype=np.float32)
            value, saturation = data[..., 2], data[..., 1]
            grad_x = np.abs(np.diff(value, axis=1, prepend=value[:, :1]))
            grad_y = np.abs(np.diff(value, axis=0, prepend=value[:1, :]))
            energy = grad_x + grad_y + saturation * 0.12
            if self.aspect < 1.0:
                energy = energy * np.linspace(1.0, 0.45, 64).reshape(64, 1)
            self._profiles = (energy.sum(axis=0), energy.sum(axis=1))
        return self._profiles[axis]

    def window(self, aspect, ideal_y=0.36):
        """
        找出保留比例为 aspect 的最佳窗口。返回 (x0, y0, x1, y1, 焦点 x, 焦点 y, 主要人物是否都在窗口里)，
        窗口坐标是原图比例，焦点是窗口内的比例。
        """
        axis = 0 if self.aspect >= aspect else 1
        length = aspect / self.aspect if axis == 0 else self.aspect / aspect
        if length >= 1.0 - TRIVIAL_LOSS:
            # 几乎不裁，人脸靠边也切不到多少，这里不做检测以节省时间
            margin = (1.0 - length) / 2.0
            box = (margin, 0.0, 1.0 - margin, 1.0) if axis == 0 else (0.0, margin, 1.0, 1.0 - margin)
            return (*box, 0.5, 0.42, True)

        positions = np.linspace(0.0, 1.0 - length, WINDOW_STEPS)
        heads = self.heads
        profile = self._profile(axis)
        cumulative = np.concatenate([[0.0], np.cumsum(profile)])
        total = max(1e-6, float(cumulative[-1]))
        ends = np.clip(positions + length, 0.0, 1.0)
        content = (np.interp(ends * len(profile), np.arange(len(cumulative)), cumulative)
                   - np.interp(positions * len(profile), np.arange(len(cumulative)), cumulative)) / total

        if heads:
            ideal = 0.5 if axis == 0 else ideal_y
            score = content * 0.1
            weight_sum = sum(head[4] for head in heads)
            lows = np.array([head[axis] for head in heads])
            highs = np.array([head[axis + 2] for head in heads])
            weights = np.array([head[4] for head in heads]) / weight_sum
            spans = np.maximum(1e-6, highs - lows)
            for low, high, span, weight in zip(lows, highs, spans, weights):
                covered = np.clip(np.minimum(ends, high) - np.maximum(positions, low), 0.0, None) / span
                # 完整拍进去得分最高，完全不拍是 0，切到一半是负分
                score += weight * (covered ** 2 - 2.0 * covered * (1.0 - covered))
            # 分数相同时，让主要人物落在画面的舒服位置
            top = heads[0]
            center = (top[axis] + top[axis + 2]) / 2.0
            score -= 0.2 * np.abs((center - positions) / length - ideal)
        else:
            # 没有人脸：看内容分布，略偏向中间，竖图的窗口略偏上
            middle = (1.0 - length) * (0.5 if axis == 0 else 0.4)
            score = content - 0.15 * np.abs(positions - middle) / max(1e-6, 1.0 - length)

        start = float(positions[int(np.argmax(score))])
        end = start + length
        box = (start, 0.0, end, 1.0) if axis == 0 else (0.0, start, 1.0, end)
        focus = [0.5, 0.42]
        inside = [head for head in heads
                  if head[axis] >= start - 0.005 and head[axis + 2] <= end + 0.005]
        if inside:
            main = inside[0]
            focus = [((main[0] + main[2]) / 2.0 - box[0]) / (box[2] - box[0]),
                     ((main[1] + main[3]) / 2.0 - box[1]) / (box[3] - box[1])]
        elif not heads and axis == 0:
            # 没有人脸时，横向的焦点放在窗口内容最集中的位置
            first = int(start * len(profile))
            last = max(first + 1, int(math.ceil(end * len(profile))))
            segment = profile[first:last]
            focus[0] = float((np.argmax(np.convolve(segment, np.ones(5), mode="same")) + 0.5) / len(segment))
        major_inside = all(head[axis] >= start - 0.005 and head[axis + 2] <= end + 0.005 for head in self.major_heads)
        return (*box, focus[0], focus[1], bool(self.major_heads) and major_inside)

    def crop(self, size, window):
        width, height = size
        box = (window[0] * self.width, window[1] * self.height, window[2] * self.width, window[3] * self.height)
        return self.image.resize((width, height), Image.Resampling.LANCZOS, box=box)

    def blurred_fill(self, size, brightness=0.5):
        """铺满画框的虚化图，用来延伸完整显示的海报。"""
        width, height = size
        small = ImageOps.fit(self.image, (max(1, width // 4), max(1, height // 4)), Image.Resampling.BILINEAR)
        small = small.filter(ImageFilter.GaussianBlur(max(2.0, height / 4 * 0.07)))
        ground = small.resize(size, Image.Resampling.BICUBIC)
        return ImageEnhance.Brightness(ground).enhance(brightness)

    def contained(self, size, anchor=0.5):
        """完整显示这张图，高度占满画框，左右边缘柔和地融进它自己的虚化图。返回 (图, 焦点 x)。"""
        width, height = size
        shown_w = min(width, max(1, int(round(height * self.aspect))))
        shown = self.image.resize((shown_w, height), Image.Resampling.LANCZOS)
        left = int(round(max(0.0, min(width - shown_w, anchor * width - shown_w / 2.0))))
        canvas = self.blurred_fill(size)
        feather = max(1.0, shown_w * 0.07)
        columns = np.arange(shown_w, dtype=np.float32)
        ramp = _ease(np.minimum(columns, shown_w - 1 - columns) / feather)
        mask = Image.fromarray((np.tile(ramp, (height, 1)) * 255).astype(np.uint8))
        canvas.paste(shown, (left, 0), mask)
        return canvas, (left + shown_w / 2.0) / width


class Picture:
    """一个媒体条目的所有素材。"""

    def __init__(self, sources):
        self.sources = [source if isinstance(source, Source) else Source(source) for source in sources]

    @property
    def main(self):
        return self.sources[0]

    def pick(self, aspect, clean=False):
        """选比例最接近画框的素材。clean 表示这个画框会切开海报上的片名，有横版背景图时优先用它。"""
        if clean:
            wide = [source for source in self.sources if source.aspect > 1.15]
            if wide:
                return wide[0]
        return min(self.sources, key=lambda source: abs(math.log(source.aspect / aspect)))

    def framed(self, size, anchor=0.5, ideal_y=0.36, clean=False):
        """
        把素材放进画框，返回 (图, 焦点 x, 焦点 y)，焦点是画框内的比例，也就是最不该被挡住的位置。
        anchor 是完整显示海报时海报中心的横向位置，每款风格会避开自己的文字和小窗。
        """
        width, height = size
        aspect = width / float(height)
        source = self.pick(aspect, clean)
        window = source.window(aspect, ideal_y)
        tall_in_wide = source.aspect <= 1.05 and source.aspect / aspect < MIN_KEEP_FOR_WIDE
        if tall_in_wide and not window[6]:
            image, focus_x = source.contained(size, anchor)
            return image, focus_x, 0.42
        return source.crop(size, window), window[4], window[5]

    def placed(self, size, visible, clean=True):
        """
        图只透过笔画露出时的取景。visible 是 0~1 的二维数组，表示画框里哪些地方看得见。
        搜索缩放和位置，让主要人物的眼睛和脸尽量落在笔画上，而不是落在字的中空里。没有人脸时按普通方式取景。
        """
        width, height = size
        aspect = width / float(height)
        source = self.pick(aspect, clean)
        heads = source.major_heads[:4]
        if not heads:
            return source.crop(size, source.window(aspect))

        rows, columns = visible.shape
        integral = np.zeros((rows + 1, columns + 1), dtype=np.float64)
        integral[1:, 1:] = visible.cumsum(axis=0).cumsum(axis=1)

        def seen(x0, y0, x1, y1):
            """框内可见的总量，坐标是画框比例，超出画框的部分算看不见。"""
            left, right = int(max(0.0, min(1.0, x0)) * columns), int(max(0.0, min(1.0, x1)) * columns)
            top, bottom = int(max(0.0, min(1.0, y0)) * rows), int(max(0.0, min(1.0, y1)) * rows)
            return (integral[bottom, right] - integral[top, right] - integral[bottom, left] + integral[top, left])

        base_w = min(1.0, aspect / source.aspect)
        base_h = min(1.0, source.aspect / aspect)
        total_weight = sum(head[4] for head in heads)
        best, best_score = None, -1e9
        for zoom in (1.0, 1.12, 1.25, 1.4, 1.6, 1.85, 2.15, 2.5):
            win_w, win_h = base_w / zoom, base_h / zoom
            for start_x in np.linspace(0.0, 1.0 - win_w, 15):
                for start_y in np.linspace(0.0, 1.0 - win_h, 11):
                    score = 0.0
                    for x0, y0, x1, y1, weight in heads:
                        hx0, hx1 = (x0 - start_x) / win_w, (x1 - start_x) / win_w
                        hy0, hy1 = (y0 - start_y) / win_h, (y1 - start_y) / win_h
                        head_cells = max(1e-6, (hx1 - hx0) * (hy1 - hy0) * rows * columns)
                        # 头部框里眼睛大约在中间偏上
                        ex0, ex1 = hx0 + (hx1 - hx0) * 0.2, hx0 + (hx1 - hx0) * 0.8
                        ey0, ey1 = hy0 + (hy1 - hy0) * 0.42, hy0 + (hy1 - hy0) * 0.62
                        eye_cells = max(1e-6, (ex1 - ex0) * (ey1 - ey0) * rows * columns)
                        score += (weight / total_weight) * (0.6 * seen(ex0, ey0, ex1, ey1) / eye_cells
                                                            + 0.4 * seen(hx0, hy0, hx1, hy1) / head_cells)
                    main = heads[0]
                    height_in_frame = (main[3] - main[1]) / win_h
                    center_x = ((main[0] + main[2]) / 2.0 - start_x) / win_w
                    # 主要人物大小接近字高，并且靠近中间
                    score -= 0.3 * abs(height_in_frame - 0.85) + 0.18 * abs(center_x - 0.5)
                    if score > best_score:
                        best_score, best = score, (start_x, start_y, start_x + win_w, start_y + win_h)
        return source.crop(size, best)

    def cover(self, size):
        """一定铺满画框的取景，用在之后会虚化的底图。"""
        aspect = size[0] / float(size[1])
        source = self.pick(aspect)
        return source.crop(size, source.window(aspect))


def load_pictures(library_dir, count, limit):
    """按文件名序号读取素材。N.jpg 是首选图，N.alt.jpg 是同一条目的另一种比例。"""
    folder = Path(library_dir)
    files = {}
    others = []
    for path in folder.iterdir():
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        stem = path.name[:-len(path.suffix)]
        if stem.isdigit():
            files.setdefault(int(stem), [None, None])[0] = path
        elif stem.endswith(ALT_SUFFIX) and stem[:-len(ALT_SUFFIX)].isdigit():
            files.setdefault(int(stem[:-len(ALT_SUFFIX)]), [None, None])[1] = path
        else:
            others.append(path)
    entries = [files[number] for number in sorted(files) if files[number][0]]
    entries += [[path, None] for path in sorted(others, key=lambda item: item.name)]

    pictures = []
    for paths in entries:
        if len(pictures) >= count:
            break
        sources = []
        for path in paths:
            if path is None:
                continue
            try:
                with Image.open(path) as opened:
                    image = ImageOps.exif_transpose(opened).convert("RGB")
                image.thumbnail((limit, limit), Image.Resampling.LANCZOS)
                stat = path.stat()
                sources.append(Source(image, (str(path), stat.st_size, int(stat.st_mtime))))
            except Exception as error:
                logger.warning(f"跳过无法读取的素材 {path}: {error}")
        if sources:
            pictures.append(Picture(sources))
    return pictures
