"""
选图：给候选素材做画质与构图深度评估，并按具体封面风格做「1 + 1 > 2」协同匹配与多图色相多样性重排。

只用 numpy 和 Pillow，兼顾速度与准确度：
一、通用美感基线（无论静图/动图、新旧样式，先筛出真正好看的图）：
  1. 清晰度与分辨率：过滤低清老图、发糊剧集截图；奖励刀锋清晰的高清剧照/设计海报
  2. 曝光与动态范围：淘汰过暗糊底、大面积过曝发白、灰蒙蒙低对比度画面；奖励明暗层次丰富的影院布光
  3. 色彩吸引力：基于 Hasler-Süsstrunk 色彩丰富度 + 肤色降权主色相提取，偏爱鲜活高级色调
  4. 人物构图与黑边检测：奖励自然中景/特写人物，惩罚过近裁脸与横图上下电影黑边（Letterbox）

二、按 16 款风格几何与气质定制协同加分（style_index）：
  - 16 窥视（中央杏仁巨眼窗 + 漆黑血脉）—— 专属精确眼窗几何选图 + 最佳眼窗平移取景：
    使用与渲染器完全一致的杏仁眼弧线可见度积分图，先搜索每个候选图的最佳眼窗取景框，
    严格校验人物完整头部（含额头头发与下巴）与双眼是否 100% 完整落在眼眶安全区内、不被上眼睑切头、
    不被眼角夹断半张脸，并过滤死黑中心、刺眼白底宣传图与空洞无主体的画面！
  - 海报取色派（9 百叶 / 10 对开 / 11 月洞 / 12 展墙 / 14 叠影 / 8 扇面）：
    偏爱标准 2:3 竖版比例、主色相鲜明饱满、高对比锐利的海报，充分发挥 OKLCH 配色系统优势
  - 13 焦距（现代微单取景器 + 左下标题 + 中央对焦框）：
    偏爱刀锋高锐度、具备光学浅景深虚化（中心锐/周边柔）且主角偏中右（避开左下标题）的大片剧照
  - 15 胶卷（斜倾 35mm 透光齿孔胶片）：
    偏爱宽银幕电影比例与饱和通透色彩，让虚化彩光从镂空齿孔里惊艳透出
  - 3 404（黑底巨字镂空）：
    强偏爱明亮高饱和、中心视觉丰盈的画面，彻底杜绝暗图融进黑底导致 404 字形发散
  - 1 序幕 / 2 巨幕 / 4 预告 / 5 环幕 / 6 景深 / 7 详情：
    各自按竖条切割 / 左下标题留白 / 多卡层叠区分度精细匹配

三、多图组合 MMR 色相与光影多样性重排（rank）：
  - 第 1 张（静态封面 & 动图首帧）严格锁定该风格综合最高分的王牌神图；
  - 后续轮换位在确保高分的前提下，最大化色相分离度（避免连续 3~4 张全是同色灰蓝/棕黄）。
"""
from dataclasses import dataclass, field
import math
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image, ImageFilter

from app.plugins.mediacovergenerator.style.faces import detect_faces

# 评估用的尺寸：清晰度和特征检测都和尺寸有关，统一缩到这个宽度再算
ASSESS_WIDTH = 480
# 普通风格快速人脸检测宽度；风格 16 窥视采用更高精度（含侧脸与中景小脸）避免漏检导致切头
FACE_WIDTH = 256
EYE_FACE_WIDTH = 384
# 低于这个分数的图，候选够用时不用
ACCEPT_SCORE = -0.8
# 竖版海报的基础加分
POSTER_BONUS = 0.8
# 背景图到这个分数才算好看：没有毛病，并且至少有一样长处（人物大小合适，或者色彩饱满）。
GOOD_WIDE_SCORE = 0.3
# 动画的背景图：没有明显毛病就用
GOOD_WIDE_SCORE_ANIME = -0.2
# 原图长边低于前一个值算分辨率低，低于后一个值略微扣分：(海报, 横图)
LOW_RESOLUTION = ((600, 900), (960, 1280))
# 宽高比低于这个值算竖版海报
POSTER_ASPECT = 0.9

# 风格 16「窥视」精确杏仁眼几何参数（选图评估与 GazeVeins 渲染共用同一套几何！）
EYE_CX = 0.50
EYE_CY = 0.385
EYE_RX = 0.415
EYE_RY = 0.290
EYE_POW = 1.18
# 眼窗外接裁剪框（在 16:9 画布上的比例，四周带 1.5% 出血避免抗锯齿露缝）
EYE_BOX_FRAC = (0.070, 0.080, 0.930, 0.690)
EYE_BOX_ASPECT = ((EYE_BOX_FRAC[2] - EYE_BOX_FRAC[0]) * 16.0) / ((EYE_BOX_FRAC[3] - EYE_BOX_FRAC[1]) * 9.0)

# 海报取色派与多画幅色相多样性权重更高的风格
_HIGH_DIVERSITY_STYLES = frozenset({1, 5, 6, 8, 9, 10, 11, 12, 14, 15})
_POSTER_PALETTE_STYLES = frozenset({9, 10, 11, 12, 14})

_EYE_GRIDS = None


def _get_eye_grids():
    """预计算 64×36 杏仁眼窗精确可见度积分图（全进程仅算一次，耗时 <0.2ms）。"""
    global _EYE_GRIDS
    if _EYE_GRIDS is not None:
        return _EYE_GRIDS
    rows, cols = 36, 64
    u = (np.arange(cols, dtype=np.float32) + 0.5) / cols
    v = (np.arange(rows, dtype=np.float32) + 0.5) / rows
    uu, vv = np.meshgrid(u, v)

    # 还原到全画布坐标并与 GazeVeins 的杏仁眼方程精确对齐
    x0_b, y0_b, x1_b, y1_b = EYE_BOX_FRAC
    cx_p = x0_b + uu * (x1_b - x0_b)
    cy_p = y0_b + vv * (y1_b - y0_b)
    nx = (cx_p - EYE_CX) / EYE_RX
    dy = cy_p - EYE_CY
    inside_x = np.abs(nx) < 1.0
    bound = EYE_RY * np.power(np.maximum(0.0, 1.0 - nx * nx), EYE_POW)
    inward = np.where(inside_x, bound - np.abs(dy), -1.0)

    raw_m = (inward > 0.0).astype(np.float32)
    # 安全内区：向内收缩 0.032H（比红晕更宽裕）与 12% 尖角，确保发顶/帽子/额头留足呼吸空间、绝不贴边切头！
    safe_m = ((inward >= 0.032) & (np.abs(nx) <= 0.88)).astype(np.float32)
    rim_m = np.clip(raw_m - safe_m, 0.0, 1.0)
    sweet_m = safe_m * np.exp(-(((uu - 0.50) / 0.28) ** 2 + ((vv - 0.50) / 0.32) ** 2))

    def _integral(mat):
        ig = np.zeros((rows + 1, cols + 1), dtype=np.float64)
        ig[1:, 1:] = mat.cumsum(axis=0).cumsum(axis=1)
        return ig

    _EYE_GRIDS = (rows, cols, raw_m, safe_m, rim_m, sweet_m, _integral(raw_m), _integral(safe_m))
    return _EYE_GRIDS


@dataclass
class EyePlacement:
    """风格 16「窥视」最佳杏仁眼窗取景与适配结果。"""
    box: Tuple[float, float, float, float]  # 原图裁剪比例 (x0, y0, x1, y1)
    has_face: bool = False
    head_vis: float = 0.0        # 主角完整头部（含头发与下巴）落在安全眼窗内的比例 (0~1)
    raw_vis: float = 0.0         # 主角完整头部落在可见眼窗内的比例 (0~1)
    eye_vis: float = 0.0         # 主角双眼区域落在安全眼窗内的比例 (0~1)
    head_h_eye: float = 0.0      # 主角头部占眼窗高度的比例（理想 0.28~0.66）
    head_clipped: bool = False   # 是否存在主角或主要配角头部被眼睑拦腰切半
    face_count: int = 0
    eye_brightness: float = 0.0  # 实际眼窗内部像素均亮
    eye_contrast: float = 0.0    # 实际眼窗内部像素对比度
    white_frac: float = 0.0      # 实际眼窗内部过白占比（过滤白底宣传卡片/拼贴图）
    salience_focus: float = 1.0  # 无脸时：眼窗安全核心区与边缘环带的视觉显著度比值
    zoom: float = 1.0


def find_best_eye_window(image: Image.Image, key=None, eye_aspect: float = EYE_BOX_ASPECT,
                         faces=None) -> EyePlacement:
    """
    为任意素材寻找最契合「16 · 窥视」杏仁眼窗的最佳裁剪窗口 (x0, y0, x1, y1)，
    确保人物完整头部（含头发与额头）和双眼居中完整呈现、不被弯曲上眼睑切掉半个脑袋！
    选图打分与最终渲染共用此函数，彻底做到「所选即所见、所见不切头」。
    """
    rows, cols, raw_m, safe_m, rim_m, sweet_m, raw_ig, safe_ig = _get_eye_grids()
    w_img, h_img = image.size
    src_aspect = w_img / float(max(1, h_img))
    is_poster = src_aspect < POSTER_ASPECT

    if faces is None:
        tw = min(EYE_FACE_WIDTH, w_img)
        th = max(1, int(round(h_img * tw / float(max(1, w_img)))))
        thumb = image.convert("RGB").resize((tw, th), Image.Resampling.BILINEAR) if tw < w_img else image.convert("RGB")
        try:
            # 窥视专用：同时启用正脸 + 动漫脸 + 侧脸，确保不漏检真实角色导致切头！
            faces = detect_faces(thumb, key, strong_only=False)
        except Exception:
            faces = []

    def _box_mean(ig, u0, v0, u1, v1):
        cu0, cu1 = max(0.0, min(1.0, u0)), max(0.0, min(1.0, u1))
        cv0, cv1 = max(0.0, min(1.0, v0)), max(0.0, min(1.0, v1))
        total_area = max(1e-6, (u1 - u0) * (v1 - v0))
        if cu1 <= cu0 or cv1 <= cv0:
            return 0.0
        in_frac = ((cu1 - cu0) * (cv1 - cv0)) / total_area
        c0, c1 = int(cu0 * cols), int(math.ceil(cu1 * cols))
        r0, r1 = int(cv0 * rows), int(math.ceil(cv1 * rows))
        c0, c1 = max(0, min(cols, c0)), max(0, min(cols, c1))
        r0, r1 = max(0, min(rows, r0)), max(0, min(rows, r1))
        if c1 <= c0 or r1 <= r0:
            return 0.0
        cell_sum = ig[r1, c1] - ig[r0, c1] - ig[r1, c0] + ig[r0, c0]
        cell_area = float((c1 - c0) * (r1 - r0))
        return float(min(1.0, (cell_sum / max(1.0, cell_area)) * in_frac))

    base_w = min(1.0, eye_aspect / max(1e-4, src_aspect))
    base_h = min(1.0, src_aspect / max(1e-4, eye_aspect))

    # 1) 构建人物完整头部与眼部列表（充足的发顶/额头留白 0.50h 与下巴留白 0.22h！）
    heads = []
    if faces:
        max_area = max((f[2] - f[0]) * (f[3] - f[1]) for f in faces) or 1e-6
        for x0, y0, x1, y1, _kind in faces:
            fw, fh = x1 - x0, y1 - y0
            area = fw * fh
            weight = (area / max_area) ** 0.75
            # 过滤极小的背景路人甲（面积不足最大脸 18% 且高度不足 8%）
            if weight < 0.25 and fh < 0.09:
                continue
            hx0 = max(0.0, x0 - fw * 0.28)
            hx1 = min(1.0, x1 + fw * 0.28)
            hy0 = max(0.0, y0 - fh * 0.50)
            hy1 = min(1.0, y1 + fh * 0.22)
            ex0, ex1 = x0 + fw * 0.12, x1 - fw * 0.12
            ey0, ey1 = y0 + fh * 0.22, y0 + fh * 0.52
            heads.append((hx0, hy0, hx1, hy1, ex0, ey0, ex1, ey1, weight))
        heads.sort(key=lambda item: -item[8])
        heads = heads[:4]

    best_box = (0.0, max(0.0, (1.0 - base_h) * 0.35), base_w, min(1.0, max(0.0, (1.0 - base_h) * 0.35) + base_h))
    best_meta = (False, 0.0, 0.0, 0.0, 0.0, False, 1.0)

    if heads:
        zooms = (1.0, 1.12, 1.24) if is_poster else (1.0, 1.08, 1.18, 1.30, 1.44)
        best_obj = -1e9
        for zoom in zooms:
            win_w, win_h = base_w / zoom, base_h / zoom
            max_sx = max(0.0, 1.0 - win_w)
            # 竖版海报尽量避开底部 22% 片名大字区
            max_sy = max(0.0, (0.78 if is_poster else 1.0) - win_h)
            xs = [0.0] if max_sx < 1e-3 else np.linspace(0.0, max_sx, 13).tolist()
            ys = [0.0] if max_sy < 1e-3 else np.linspace(0.0, max_sy, 13).tolist()
            for sx in xs:
                for sy in ys:
                    # 评估主角（第 1 脸）
                    hx0, hy0, hx1, hy1, ex0, ey0, ex1, ey1, _w0 = heads[0]
                    u0, u1 = (hx0 - sx) / win_w, (hx1 - sx) / win_w
                    v0, v1 = (hy0 - sy) / win_h, (hy1 - sy) / win_h
                    eu0, eu1 = (ex0 - sx) / win_w, (ex1 - sx) / win_w
                    ev0, ev1 = (ey0 - sy) / win_h, (ey1 - sy) / win_h

                    safe_cov = _box_mean(safe_ig, u0, v0, u1, v1)
                    raw_cov = _box_mean(raw_ig, u0, v0, u1, v1)
                    eyes_cov = _box_mean(safe_ig, eu0, ev0, eu1, ev1)
                    head_h = v1 - v0
                    head_cx = (u0 + u1) * 0.5
                    head_cy = (v0 + v1) * 0.5

                    # 严厉惩罚任何压线/切头窗口，强力奖励 100% 完整且居中凝视的窗口！
                    obj = 2.6 * safe_cov + 1.4 * eyes_cov + 0.6 * raw_cov
                    if safe_cov < 0.88:
                        obj -= 3.2 * (0.88 - safe_cov)
                    if raw_cov < 0.94:
                        obj -= 4.0 * (0.94 - raw_cov)
                    # 头部高度甜点：占眼窗高度 28%~66%（既看清表情凝视，又绝不顶天立地挤爆眼眶）
                    if head_h > 0.72:
                        obj -= 2.4 * (head_h - 0.72)
                    elif head_h < 0.24:
                        obj -= 1.2 * (0.24 - head_h)
                    else:
                        obj -= 0.35 * abs(head_h - 0.48)
                    # 人物越居中，在杏仁眼最宽处越舒适大气
                    obj -= 0.72 * abs(head_cx - 0.50) + 0.55 * abs(head_cy - 0.48)
                    # 无必要不过度放大，保留影院构图清晰度
                    obj -= 0.22 * (zoom - 1.0)

                    # 检查其他主要角色是否被眼睑“拦腰切掉半张脸”！
                    any_clipped = (safe_cov < 0.82 or raw_cov < 0.90 or eyes_cov < 0.88)
                    for ohx0, ohy0, ohx1, ohy1, _oex0, _oey0, _oex1, _oey1, ow in heads[1:]:
                        ou0, ou1 = (ohx0 - sx) / win_w, (ohx1 - sx) / win_w
                        ov0, ov1 = (ohy0 - sy) / win_h, (ohy1 - sy) / win_h
                        o_safe = _box_mean(safe_ig, ou0, ov0, ou1, ov1)
                        o_raw = _box_mean(raw_ig, ou0, ov0, ou1, ov1)
                        if o_safe >= 0.84:
                            # 双人/多人全部完整入眼，绝佳加分！
                            obj += 0.65 * ow
                        elif o_raw <= 0.08:
                            # 干净排除在眼眶外，不留半张残脸
                            pass
                        else:
                            # 拦腰切掉配角半个头 -> 严厉惩罚！
                            obj -= 1.65 * ow
                            if ow >= 0.45:
                                any_clipped = True

                    if obj > best_obj:
                        best_obj = obj
                        best_box = (sx, sy, sx + win_w, sy + win_h)
                        best_meta = (True, safe_cov, raw_cov, eyes_cov, head_h, any_clipped, zoom)
    else:
        # 2) 无检测到人脸时：基于 2D 视觉显著度能量图（边缘 + 饱和度）寻优，让核心主体居中落在眼窗内！
        sm_s = image.convert("RGB").resize((96, 54), Image.Resampling.BILINEAR)
        hsv = np.asarray(sm_s.convert("HSV"), dtype=np.float32) / 255.0
        val, sat = hsv[..., 2], hsv[..., 1]
        gx = np.abs(np.diff(val, axis=1, prepend=val[:, :1]))
        gy = np.abs(np.diff(val, axis=0, prepend=val[:1, :]))
        sal = gx + gy + sat * 0.20
        sh, sw = sal.shape
        # 无检测到人脸时（如戴头盔战士/风格化角色），温和偏好上半身黄金区 y∈[0.08, 0.55]，防止下移到胸腹手臂切掉头！
        y_bias = np.linspace(1.22, 0.72, sh, dtype=np.float32)[:, None]
        sal = sal * y_bias
        if is_poster:
            sal[int(sh * 0.72):, :] *= 0.25

        best_obj = -1e9
        for zoom in (1.0, 1.12, 1.24):
            win_w, win_h = base_w / zoom, base_h / zoom
            max_sx = max(0.0, 1.0 - win_w)
            max_sy = max(0.0, (0.78 if is_poster else 1.0) - win_h)
            xs = [0.0] if max_sx < 1e-3 else np.linspace(0.0, max_sx, 9).tolist()
            ys = [0.0] if max_sy < 1e-3 else np.linspace(0.0, max_sy, 9).tolist()
            for sx in xs:
                for sy in ys:
                    ix0, ix1 = int(round(sx * sw)), max(int(round(sx * sw)) + 1, int(round((sx + win_w) * sw)))
                    iy0, iy1 = int(round(sy * sh)), max(int(round(sy * sh)) + 1, int(round((sy + win_h) * sh)))
                    patch = Image.fromarray(np.clip(sal[iy0:iy1, ix0:ix1] * 255.0, 0, 255).astype(np.uint8), mode="L")
                    p_grid = np.asarray(patch.resize((cols, rows), Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
                    in_sweet = float((p_grid * sweet_m).sum() / max(1e-4, sweet_m.sum()))
                    on_rim = float((p_grid * rim_m).sum() / max(1e-4, rim_m.sum()))
                    # 轻微偏好靠上的 start_y，避免在人物未被识别时向下裁切导致丢头
                    obj = in_sweet - 0.45 * on_rim - 0.08 * (zoom - 1.0) - 0.06 * sy
                    if obj > best_obj:
                        best_obj = obj
                        best_box = (sx, sy, sx + win_w, sy + win_h)
                        best_meta = (False, 0.0, 0.0, 0.0, 0.0, False, zoom)

    # 3) 对最终落定的眼窗内部像素做真实曝光 / 对比度 / 过白率 / 显著度聚焦质检！
    crop_px = (
        best_box[0] * w_img, best_box[1] * h_img,
        best_box[2] * w_img, best_box[3] * h_img,
    )
    eye_patch = image.convert("RGB").resize((cols, rows), Image.Resampling.BILINEAR, box=crop_px)
    ep_rgb = np.asarray(eye_patch, dtype=np.float32)
    ep_luma = ep_rgb[..., 0] * 0.2126 + ep_rgb[..., 1] * 0.7152 + ep_rgb[..., 2] * 0.0722
    raw_bool = raw_m > 0.5
    eye_pixels = ep_luma[raw_bool]
    eye_brightness = float(eye_pixels.mean()) if eye_pixels.size else 0.0
    eye_contrast = float(eye_pixels.std()) if eye_pixels.size else 0.0
    white_frac = float((eye_pixels >= 225.0).mean()) if eye_pixels.size else 0.0

    # 评估眼窗安全核心区 vs 眼眶边缘环带的视觉显著度聚焦度（淘汰中心空洞/只有袖子边缘的图！）
    ep_gx = np.abs(np.diff(ep_luma, axis=1, prepend=ep_luma[:, :1]))
    ep_gy = np.abs(np.diff(ep_luma, axis=0, prepend=ep_luma[:1, :]))
    ep_edge = ep_gx + ep_gy
    core_e = float((ep_edge * sweet_m).sum() / max(1e-4, sweet_m.sum()))
    rim_e = float((ep_edge * rim_m).sum() / max(1e-4, rim_m.sum()))
    salience_focus = core_e / max(1.0, rim_e)

    has_face, head_vis, raw_vis, eye_vis, head_h_eye, head_clipped, used_zoom = best_meta
    return EyePlacement(
        box=best_box,
        has_face=has_face,
        head_vis=head_vis,
        raw_vis=raw_vis,
        eye_vis=eye_vis,
        head_h_eye=head_h_eye,
        head_clipped=head_clipped,
        face_count=len(heads),
        eye_brightness=eye_brightness,
        eye_contrast=eye_contrast,
        white_frac=white_frac,
        salience_focus=salience_focus,
        zoom=used_zoom,
    )


@dataclass
class Assessment:
    score: float
    reasons: List[str] = field(default_factory=list)
    brightness: float = 0.0
    sharpness: float = 0.0
    largest_face: float = 0.0
    colorfulness: float = 0.0
    poster: bool = False
    # 扩展视觉 DNA（全部提供默认值，100% 向后兼容旧调用方）
    hue: Optional[float] = None
    contrast: float = 0.0
    highlights: float = 0.0
    aspect: float = 1.0
    center_focus: float = 1.0
    center_brightness: float = 0.0
    face_cx: Optional[float] = None
    face_cy: Optional[float] = None
    bokeh_ratio: float = 1.0
    bottom_left_clutter: float = 1.0
    letterbox: float = 0.0
    base_score: float = 0.0
    # 风格 16「窥视」专属精确眼窗评估数据
    eye_fit: Optional[EyePlacement] = None

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


def _dominant_hue(image) -> Optional[float]:
    """提取图像鲜明主色相（0~360°），肤色降权；近乎黑白灰返回 None。"""
    small = image.copy()
    small.thumbnail((72, 72))
    hsv = np.asarray(small.convert("HSV"), dtype=np.float32) / 255.0
    hue, sat, val = hsv[..., 0].ravel(), hsv[..., 1].ravel(), hsv[..., 2].ravel()
    weight = (sat ** 1.4) * np.clip((val - 0.12) / 0.45, 0.0, 1.0)
    skin = (hue < 0.11) & (sat > 0.12) & (sat < 0.62) & (val > 0.30)
    weight = np.where(skin, weight * 0.32, weight)
    if float(weight.sum()) < 0.032 * weight.size:
        return None
    bins = 36
    idx = (hue * bins).astype(np.int64) % bins
    hist = np.bincount(idx, weights=weight, minlength=bins)
    spread = hist + 0.6 * (np.roll(hist, 1) + np.roll(hist, -1)) + 0.25 * (np.roll(hist, 2) + np.roll(hist, -2))
    peak = int(np.argmax(spread))
    near = np.abs((idx - peak + bins // 2) % bins - bins // 2) <= 1
    chosen = weight[near]
    angle = hue[near] * 2.0 * math.pi
    mean = math.atan2(float((chosen * np.sin(angle)).sum()), float((chosen * np.cos(angle)).sum()))
    return (math.degrees(mean)) % 360.0


def _detect_letterbox(luma: np.ndarray) -> float:
    """
    检测横图上下（或左右）对称的纯黑电影压条（Letterbox / Pillarbox），返回黑边占比。
    要求双侧同时存在平整纯黑带才触发，避免误伤暗调文艺单边阴影构图。
    """
    h, w = luma.shape
    if h < 40 or w < 40:
        return 0.0

    def _band_frac(arr_1d_mean, arr_1d_std):
        n = len(arr_1d_mean)
        lim = int(n * 0.28)
        top = 0
        while top < lim and arr_1d_mean[top] < 13.5 and arr_1d_std[top] < 6.5:
            top += 1
        bot = 0
        while bot < lim and arr_1d_mean[n - 1 - bot] < 13.5 and arr_1d_std[n - 1 - bot] < 6.5:
            bot += 1
        if top >= n * 0.065 and bot >= n * 0.065:
            return (top + bot) / float(n)
        return 0.0

    tb = _band_frac(luma.mean(axis=1), luma.std(axis=1))
    lr = _band_frac(luma.mean(axis=0), luma.std(axis=0))
    return max(tb, lr)


def _hue_distance(h1: Optional[float], h2: Optional[float]) -> float:
    if h1 is None or h2 is None:
        return 55.0
    diff = abs(h1 - h2) % 360.0
    return min(diff, 360.0 - diff)


def style_synergy(a: Assessment, style_index: Optional[int]) -> float:
    """
    计算单张素材与指定封面风格的「1 + 1 > 2」协同适配分。
    保证绝不会把烂图抬成好图，而是让最契合当前构图、光影与视觉语言的好图脱颖而出！
    """
    if not style_index:
        return 0.0
    # 带电影黑边的截图不享受任何构图/景深/光影协同加分
    if a.letterbox >= 0.14:
        return -0.35
    bonus = 0.0

    # ---- A. 海报取色系列（9 百叶 / 10 对开 / 11 月洞 / 12 展墙 / 14 叠影）----
    if style_index in _POSTER_PALETTE_STYLES:
        if 0.56 <= a.aspect <= 0.78:
            bonus += 0.36
        elif a.aspect > 1.0:
            bonus -= 0.45
        if a.hue is not None and a.colorfulness >= 32:
            bonus += 0.32 + min(0.22, (a.colorfulness - 32.0) / 80.0)
        elif a.hue is None or a.colorfulness < 20:
            bonus -= 0.32
        if 56.0 <= a.brightness <= 184.0 and a.contrast >= 45.0:
            bonus += 0.22
        if style_index == 14 and a.sharpness >= 700 and a.contrast >= 52.0:
            bonus += 0.26
        return bonus

    # ---- B. 8 扇面（8 张竖版海报沿大圆弧展开）----
    if style_index == 8:
        if 0.56 <= a.aspect <= 0.78:
            bonus += 0.34
        if a.colorfulness >= 32:
            bonus += 0.26
        if a.sharpness >= 600:
            bonus += 0.18
        return bonus

    # ---- C. 16 窥视（专属精确杏仁眼窗完整度 + 防切头 + 氛围选图算法！）----
    if style_index == 16:
        ef = a.eye_fit
        if a.aspect >= 1.35:
            bonus += 0.20
        elif a.aspect < 0.9:
            # 竖版海报放进宽扁杏仁眼窗会裁掉大量画面，优先横版干净剧照
            bonus -= 0.35

        if ef is not None:
            # 1) 实际落入眼窗内部的光影氛围校验（强偏好深邃戏剧暗调，压制白底/粉嫩高白宣传图！）
            if ef.eye_brightness < 46.0:
                bonus -= 0.95
                if "眼窗内过暗" not in a.reasons:
                    a.reasons.append("眼窗内过暗")
            elif ef.eye_brightness > 164.0 or ef.white_frac > 0.20:
                bonus -= 0.92
                if "眼窗过亮发白" not in a.reasons:
                    a.reasons.append("眼窗过亮发白")
            elif ef.eye_brightness > 148.0 or ef.white_frac > 0.12:
                bonus -= 0.38
            elif 56.0 <= ef.eye_brightness <= 138.0 and ef.eye_contrast >= 40.0:
                bonus += 0.54
            if ef.eye_contrast < 30.0:
                bonus -= 0.55

            # 2) 核心：人物头部在杏仁眼窗内的完整度校验（彻底消灭半个头/遮额头/切下巴！）
            if ef.has_face:
                if (not ef.head_clipped) and ef.head_vis >= 0.90 and ef.eye_vis >= 0.95:
                    # 完美零裁头！人物完整头部与双眼清晰落在眼窗内！
                    if 0.28 <= ef.head_h_eye <= 0.66:
                        bonus += 1.85
                    elif 0.18 <= ef.head_h_eye <= 0.74:
                        bonus += 1.25
                    else:
                        bonus += 0.45
                elif (not ef.head_clipped) and ef.head_vis >= 0.82 and ef.eye_vis >= 0.90:
                    bonus += 0.95
                else:
                    # 即使经过最佳眼窗平移缩放依然会切到头或切半个配角 -> 严厉淘汰！
                    bonus -= 1.85
                    if "人物头部易被眼眶遮挡" not in a.reasons:
                        a.reasons.append("人物头部易被眼眶遮挡")
                if ef.head_h_eye > 0.78:
                    bonus -= 0.95
                    if "脸部过大难以完整入眼" not in a.reasons:
                        a.reasons.append("脸部过大难以完整入眼")
                # 轻度扣减过大缩放（优先原生构图就完美入眼的剧照）
                if ef.zoom > 1.28:
                    bonus -= 0.22 * (ef.zoom - 1.28)
            else:
                # 无检测到清晰人物脸（如空景、静物、只露出衣袖/半截身体）：
                # 严控上限，绝不抢占「人物完整凝视眼窗」王牌剧照的位置！
                if ef.salience_focus >= 1.25:
                    bonus += 0.25
                elif ef.salience_focus < 0.95:
                    bonus -= 0.65
                    if "眼窗缺少居中主体" not in a.reasons:
                        a.reasons.append("眼窗缺少居中主体")
        if a.colorfulness >= 30:
            bonus += 0.16
        return bonus

    # ---- D. 13 焦距（现代微单取景器 + 中央对焦框 + 左下标题）----
    if style_index == 13:
        if a.aspect >= 1.45:
            bonus += 0.24
        if a.sharpness >= 820:
            bonus += 0.32
        if a.bokeh_ratio >= 1.18:
            bonus += min(0.38, (a.bokeh_ratio - 1.05) * 0.48)
        if a.face_cx is not None and a.face_cy is not None:
            if 0.30 <= a.face_cx <= 0.80 and 0.16 <= a.face_cy <= 0.66:
                bonus += 0.40
        if a.bottom_left_clutter < 0.92:
            bonus += 0.16
        if 64.0 <= a.brightness <= 178.0 and a.contrast >= 44.0:
            bonus += 0.18
        return bonus

    # ---- E. 15 胶卷（斜倾 35mm 透光齿孔胶片）----
    if style_index == 15:
        if a.aspect >= 1.50:
            bonus += 0.30
        if a.colorfulness >= 36:
            bonus += 0.34 + min(0.18, (a.colorfulness - 36.0) / 70.0)
        if 68.0 <= a.brightness <= 178.0 and a.contrast >= 44.0:
            bonus += 0.22
        if a.sharpness >= 650:
            bonus += 0.16
        return bonus

    # ---- F. 3 404（漆黑底面上巨大的镂空「404」透出画面）----
    if style_index == 3:
        if a.center_brightness < 65.0:
            bonus -= 0.85
        elif 92.0 <= a.center_brightness <= 192.0:
            bonus += 0.44
        if a.colorfulness >= 36:
            bonus += 0.38
        elif a.colorfulness < 20:
            bonus -= 0.30
        if a.center_focus >= 0.95:
            bonus += 0.24
        return bonus

    # ---- G. 1 序幕 / 5 环幕（多幅竖条/弧幕画廊展开）----
    if style_index in (1, 5):
        if a.colorfulness >= 32:
            bonus += 0.26
        if a.face_cx is not None and 0.24 <= a.face_cx <= 0.76 and 0.12 <= a.largest_face <= 0.42:
            bonus += 0.40
        if a.center_focus >= 0.95:
            bonus += 0.20
        if a.sharpness >= 680:
            bonus += 0.16
        return bonus

    # ---- H. 2 巨幕 / 4 预告 / 6 景深 / 7 详情（宽画幅电影剧照 + 左侧/底侧信息）----
    if style_index in (2, 4, 6, 7):
        if a.aspect >= 1.45:
            bonus += 0.24
        if a.sharpness >= 680 and a.contrast >= 44.0:
            bonus += 0.26
        if a.colorfulness >= 32:
            bonus += 0.20
        if a.face_cx is not None:
            if style_index in (2, 4) and a.face_cx >= 0.36:
                bonus += 0.34
            elif style_index in (6, 7) and 0.22 <= a.face_cx <= 0.78 and (a.face_cy or 0.5) <= 0.62:
                bonus += 0.30
        if style_index in (2, 4) and a.bottom_left_clutter < 0.92:
            bonus += 0.14
        return bonus

    return bonus


def assess(image, key=None, requested=None, native=None, style_index: Optional[int] = None) -> Assessment:
    """
    给素材打分，越高越适合做封面。
    requested 是下载这张小图时请求的最大边长，native 是媒体服务器报告的原图 (宽, 高)，
    style_index 是当前封面风格编号（1–16，可选）；传入时叠加对应风格的构图/光影协同加分。
    """
    small = _prepare(image)
    rgb = np.asarray(small, dtype=np.float32)
    h, w = rgb.shape[:2]
    luma = rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722
    brightness = float(luma.mean())
    p05, highlights, p95 = [float(v) for v in np.percentile(luma, (5, 90, 95))]
    dynamic_range = p95 - p05
    blown_white = float((luma >= 246.0).mean())

    gray = small.convert("L")
    laplace = np.asarray(
        gray.filter(ImageFilter.Kernel((3, 3), (0, 1, 0, 1, -4, 1, 0, 1, 0), 1, 128)),
        dtype=np.float32,
    ) - 128.0
    sharpness = float(laplace.var())
    abs_lap = np.abs(laplace)

    y0c, y1c = int(h * 0.16), max(int(h * 0.16) + 1, int(h * 0.76))
    x0c, x1c = int(w * 0.20), max(int(w * 0.20) + 1, int(w * 0.80))
    center_luma = luma[y0c:y1c, x0c:x1c]
    center_brightness = float(center_luma.mean())
    center_lap = laplace[y0c:y1c, x0c:x1c]
    center_sharp = float(center_lap.var())
    center_energy = float(abs_lap[y0c:y1c, x0c:x1c].mean())

    mask_outer = np.ones((h, w), dtype=bool)
    mask_outer[y0c:y1c, x0c:x1c] = False
    outer_energy = float(abs_lap[mask_outer].mean()) if mask_outer.any() else center_energy
    outer_sharp = float(laplace[mask_outer].var()) if mask_outer.any() else center_sharp
    center_focus = center_energy / max(1.0, outer_energy)
    bokeh_ratio = center_sharp / max(12.0, outer_sharp)

    bl_energy = float(abs_lap[int(h * 0.56):, :int(w * 0.46)].mean()) if h > 10 and w > 10 else center_energy
    bottom_left_clutter = bl_energy / max(1.0, float(abs_lap.mean()))

    red_green = rgb[..., 0] - rgb[..., 1]
    yellow_blue = (rgb[..., 0] + rgb[..., 1]) / 2.0 - rgb[..., 2]
    colorfulness = float(
        np.hypot(red_green.std(), yellow_blue.std())
        + 0.3 * np.hypot(red_green.mean(), yellow_blue.mean())
    )
    hue = _dominant_hue(small)
    aspect = image.width / float(max(1, image.height))
    poster = aspect < POSTER_ASPECT
    contrast = float(luma.std())
    letterbox = 0.0 if poster else _detect_letterbox(luma)

    score, reasons = (POSTER_BONUS if poster else 0.0), []
    long_side = _resolution(image, requested, native)
    if long_side:
        bad, meh = LOW_RESOLUTION[0 if poster else 1]
        if long_side < bad:
            score -= 1.0
            reasons.append(f"分辨率低（{int(long_side)}px）")
        elif long_side < meh:
            score -= 0.3 * (meh - long_side) / float(meh - bad)

    if letterbox >= 0.14:
        score -= 1.25
        reasons.append("带黑边")

    if colorfulness < 14:
        score -= 0.65
        reasons.append("颜色寡淡")
    elif colorfulness < 24:
        score -= (24 - colorfulness) / 10.0 * 0.4
    elif colorfulness > 38:
        score += min(0.42, 0.20 + (colorfulness - 38.0) / 90.0)

    if brightness < 38:
        score -= 3.0
        reasons.append("太暗")
    elif brightness < 62:
        score -= (62 - brightness) / 24.0 * 1.5
        reasons.append("偏暗")
    elif brightness > 205:
        score -= 0.65
        reasons.append("底色太白")

    if blown_white > 0.36 and not poster:
        score -= 0.60
        reasons.append("大面积过曝发白")

    if highlights < 90 and brightness >= 38:
        score -= 0.5
        reasons.append("没有亮部")

    if contrast < 28 and brightness >= 38:
        score -= 0.65
        reasons.append("发灰")
    elif contrast < 34:
        score -= (34 - contrast) / 6.0 * 0.3
    elif contrast >= 52 and dynamic_range >= 150 and letterbox < 0.14:
        score += 0.18

    if dynamic_range < 75 and brightness >= 38:
        score -= 0.45
        if "发灰" not in reasons:
            reasons.append("层次偏平")

    if sharpness < 260:
        score -= 1.6
        reasons.append("不清晰，像剧集截图")
    elif sharpness < 500:
        score -= (500 - sharpness) / 240.0 * 0.8
    elif sharpness > 900:
        score += 0.22

    largest = 0.0
    face_cx: Optional[float] = None
    face_cy: Optional[float] = None
    faces = []
    eye_fit: Optional[EyePlacement] = None

    if style_index == 16:
        # 风格 16 窥视：使用更高分辨率 + 全模型（正脸/动漫脸/侧脸）直接测算最佳杏仁眼窗完整度！
        fw_det = min(EYE_FACE_WIDTH, small.width)
        fh_det = max(1, int(round(small.height * fw_det / float(max(1, small.width)))))
        thumb = small.resize((fw_det, fh_det), Image.Resampling.BILINEAR) if fw_det < small.width else small
        try:
            faces = detect_faces(thumb, key, strong_only=False)
        except Exception:
            faces = []
        eye_fit = find_best_eye_window(small, key=key, faces=faces)
        if faces:
            primary = max(faces, key=lambda f: (f[3] - f[1]) * (f[2] - f[0]))
            largest = float(primary[3] - primary[1])
            face_cx = float((primary[0] + primary[2]) * 0.5)
            face_cy = float((primary[1] + primary[3]) * 0.5)
            if not poster and largest > 0.56:
                score -= 1.2
                reasons.append("脸部特写过近")
            elif not poster and 0.10 <= largest <= 0.45:
                score += 0.48
    elif not poster:
        try:
            thumb = small.resize(
                (FACE_WIDTH, max(1, int(round(small.height * FACE_WIDTH / float(small.width))))),
                Image.Resampling.BILINEAR,
            )
            faces = detect_faces(thumb, key, strong_only=True)
        except Exception:
            faces = []
        if faces:
            primary = max(faces, key=lambda f: (f[3] - f[1]) * (f[2] - f[0]))
            largest = float(primary[3] - primary[1])
            face_cx = float((primary[0] + primary[2]) * 0.5)
            face_cy = float((primary[1] + primary[3]) * 0.5)
            if largest > 0.56:
                score -= 1.2
                reasons.append("脸部特写过近")
            elif 0.10 <= largest <= 0.42:
                score += 0.52

    base_score = float(score)
    result = Assessment(
        score=base_score,
        reasons=reasons,
        brightness=brightness,
        sharpness=sharpness,
        largest_face=largest,
        colorfulness=colorfulness,
        poster=poster,
        hue=hue,
        contrast=contrast,
        highlights=highlights,
        aspect=aspect,
        center_focus=center_focus,
        center_brightness=center_brightness,
        face_cx=face_cx,
        face_cy=face_cy,
        bokeh_ratio=bokeh_ratio,
        bottom_left_clutter=bottom_left_clutter,
        letterbox=letterbox,
        base_score=base_score,
        eye_fit=eye_fit,
    )
    if style_index is not None:
        result.score = base_score + style_synergy(result, style_index)
    return result


def rank(candidates, required, keep_order=False, minimum=None, style_index: Optional[int] = None):
    """
    candidates 是 [(条目, Assessment 或 None)]，没有评估结果的当作合格。
    - 最多选 required 个合格的；合格的不到 minimum 个时，才用分数最高的不合格图补到 minimum。
    - keep_order=True：保持原有先后顺序（如按入库时间排序）。
    - keep_order=False：
        第 1 张严格锁定综合最高分（确保静态封面和动图首帧最惊艳！），
        第 2~required 张结合「综合画质风格分 + 与已选图的色相/明暗差异度（MMR）」挑选，
        让多张海报/剧照之间的色调与氛围形成高级层次反差，杜绝满屏撞色发闷！
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
    elif len(good) <= 1 or required <= 1:
        chosen = sorted(good, key=by_score)[:required]
    else:
        pool = sorted(good, key=by_score)
        chosen = [pool.pop(0)]
        high_div = style_index in _HIGH_DIVERSITY_STYLES
        div_w = 0.64 if high_div else 0.34
        dup_pen = 0.48 if high_div else 0.22

        while pool and len(chosen) < required:
            best_idx = 0
            best_val = -1e9
            for idx, entry in enumerate(pool):
                a = entry[1][1]
                q = a.score if a is not None else 0.0
                if a is None:
                    val = q
                else:
                    hue_dists = []
                    luma_dists = []
                    for sel in chosen:
                        sa = sel[1][1]
                        if sa is not None:
                            hue_dists.append(_hue_distance(a.hue, sa.hue))
                            luma_dists.append(abs(a.brightness - sa.brightness))
                    min_dh = min(hue_dists) if hue_dists else 90.0
                    min_dl = min(luma_dists) if luma_dists else 40.0

                    if min_dh < 25.0 and a.hue is not None:
                        hue_bonus = -dup_pen * (1.0 - min_dh / 25.0)
                    else:
                        hue_bonus = min(1.0, min_dh / 60.0) * div_w
                    luma_bonus = min(0.16, (min_dl / 80.0) * 0.16)
                    val = q + hue_bonus + luma_bonus
                if val > best_val:
                    best_val = val
                    best_idx = idx
            chosen.append(pool.pop(best_idx))

    if len(chosen) < minimum:
        chosen += sorted(rest, key=by_score)[:minimum - len(chosen)]
    if keep_order:
        chosen.sort(key=lambda entry: entry[0])
    chosen_ids = {entry[0] for entry in chosen}
    skipped = [entry[1] for entry in indexed if entry[0] not in chosen_ids]
    return [entry[1] for entry in chosen], skipped
