"""
插件界面：设置页（FormRender）与详情页（PageRender）的 Vuetify JSON。

这里只有纯函数，不依赖 MoviePilot，所需数据都由主类组装后传入。
颜色通过 Vuetify 的主题变量取透明度，浅色、深色、玻璃主题都能保持可读；只有点缀色固定。
"""
import json
from typing import Any, Dict, List, Optional

from app.plugins.mediacovergenerator.style.catalog import STYLE_CATALOG, STYLE_COUNT, style_info
from app.plugins.mediacovergenerator.ui_presets import TYPOGRAPHY_PRESETS
from app.plugins.mediacovergenerator.ui_previews import PREVIEWS, TYPOGRAPHY_PREVIEWS

# Vuetify 组件自带主题类，会重置 CSS 变量，所以点缀色要用具体色值传给 color 属性
ACCENT = "#B0843F"
# 动态预览放在仓库里，不进页面数据：页面每次操作都会整个重新下载。加载失败时露出下面的静态图
PREVIEW_GIF = "https://raw.githubusercontent.com/wio-ki/MoviePilot-Plugins/main/images/style_{index}.gif"

PAGE_TABS = [
    ("style", "风格", "mdi-palette-swatch-outline"),
    ("libraries", "媒体库", "mdi-folder-multiple-image"),
    ("history", "历史", "mdi-history"),
    ("maintain", "维护", "mdi-wrench-outline"),
]

TITLE_EXAMPLE = """# 按媒体库名称配置封面标题，名称需与媒体库完全一致
# 第三行点缀色可选，仅在「点缀色」选择「从标题配置读取」时生效
电影:
  - 电影
  - MOVIES
  - "#B5845C"
电视剧:
  - 电视剧
  - TV SHOWS
"""

CSS = """
.mcg {
  --mcg-ink: rgb(var(--v-theme-on-surface));
  --mcg-muted: rgba(var(--v-theme-on-surface), .64);
  --mcg-faint: rgba(var(--v-theme-on-surface), .44);
  --mcg-line: rgba(var(--v-theme-on-surface), .11);
  --mcg-raise: rgba(var(--v-theme-on-surface), .035);
  --mcg-raise-strong: rgba(var(--v-theme-on-surface), .07);
  --mcg-gold: #C49A5C;
  --mcg-gold-soft: rgba(196, 154, 92, .16);
  --mcg-ok: #4E9B7A;
  --mcg-bad: #C8664F;
  --v-theme-primary: 196, 154, 92;
  --v-theme-on-primary: 28, 22, 14;
  color: var(--mcg-ink);
  font-feature-settings: "palt";
}
.mcg * { box-sizing: border-box; }
.mcg-radio { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }

/* 顶部横幅：内容固定浅色，不随主题变化 */
.mcg-hero {
  position: relative; overflow: hidden; border-radius: 18px; color: #F5F0E6;
  background: linear-gradient(120deg, #17130F 0%, #221B14 55%, #15191A 100%);
  padding: 26px 28px 22px; margin-bottom: 18px;
}
.mcg-hero-art {
  position: absolute; inset: -24px; background-size: cover; background-position: center;
  filter: blur(16px) saturate(1.15); opacity: .5;
}
.mcg-hero-shade {
  position: absolute; inset: 0;
  background: linear-gradient(100deg, rgba(14,11,8,.92) 0%, rgba(14,11,8,.68) 52%, rgba(14,11,8,.42) 100%);
}
.mcg-hero-body { position: relative; display: flex; flex-wrap: wrap; gap: 18px; align-items: flex-end; justify-content: space-between; }
.mcg-eyebrow { font-size: 11px; letter-spacing: .28em; color: var(--mcg-gold); font-weight: 600; margin-bottom: 8px; }
.mcg-hero h2 { font-size: 28px; line-height: 1.2; font-weight: 700; letter-spacing: .02em; margin: 0; color: #FBF7EF; }
.mcg-hero-sub { font-size: 13px; color: rgba(245,240,230,.70); margin-top: 8px; line-height: 1.6; max-width: 34rem; }
.mcg-hero-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.mcg-stats {
  position: relative; display: grid; grid-template-columns: repeat(auto-fit, minmax(128px, 1fr));
  gap: 1px; margin-top: 20px; border-radius: 12px; overflow: hidden; background: rgba(255,255,255,.10);
}
.mcg-stat { background: rgba(18,14,10,.62); padding: 11px 14px; min-width: 0; }
.mcg-stat-label { font-size: 11px; color: rgba(245,240,230,.58); letter-spacing: .06em; }
.mcg-stat-value { font-size: 14px; font-weight: 600; color: #FBF7EF; margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mcg-progress { position: relative; height: 3px; border-radius: 3px; background: rgba(255,255,255,.14); margin-top: 14px; overflow: hidden; }
.mcg-progress-bar { height: 100%; background: var(--mcg-gold); transition: width .4s ease; }
.mcg-progress-note { position: relative; font-size: 12px; color: rgba(245,240,230,.72); margin-top: 8px; }

/* 按钮 */
.mcg .v-btn.mcg-btn.mcg-btn { text-transform: none; letter-spacing: .02em; font-weight: 600; border-radius: 10px; box-shadow: none; border-color: transparent; backdrop-filter: none; }
/* 重复类名是为了压过主题里 html[data-theme] .v-btn.bg-primary 这类高优先级规则 */
.mcg .v-btn.mcg-btn.mcg-btn-gold.mcg-btn-gold { background-color: var(--mcg-gold) !important; color: #1C160E !important; }
.mcg .v-btn.mcg-btn.mcg-btn-glass.mcg-btn-glass { background-color: rgba(255,255,255,.13) !important; color: #FBF7EF !important; }
.mcg .v-btn.mcg-btn.mcg-btn-quiet.mcg-btn-quiet { background-color: var(--mcg-raise-strong) !important; color: var(--mcg-ink) !important; }
.mcg .v-btn.mcg-btn.mcg-btn-danger.mcg-btn-danger { background-color: rgba(200,102,79,.14) !important; color: var(--mcg-bad) !important; }

/* 页签：单选框 + CSS，切换无需请求后端 */
.mcg-tabs { display: flex; gap: 4px; padding: 4px; border-radius: 12px; background: var(--mcg-raise-strong); margin-bottom: 18px; overflow-x: auto; }
.mcg-tab {
  flex: 1 1 0; min-width: 84px; display: flex; align-items: center; justify-content: center; gap: 6px;
  padding: 8px 12px; border-radius: 9px; cursor: pointer; font-size: 13.5px; font-weight: 500;
  color: var(--mcg-muted); transition: background .18s ease, color .18s ease; white-space: nowrap; margin: 0;
}
.mcg-tab:hover { color: var(--mcg-ink); }
.mcg-panel, .mcg-when { display: none; }
/* 弹窗默认垂直居中，页签内容一变高，页签栏就会上下跳。改成贴着顶部，切换时位置不动 */
.v-dialog > .v-overlay__content:has(.mcg) { align-self: flex-start; margin-top: 24px; }
/* 右下角的设置齿轮浮在内容上，底部留出位置，不挡最后一行的按钮 */
.mcg:not(.mcg-form) { padding-bottom: 64px; }
#mcg-tab-style:checked ~ .mcg-tabs label[for="mcg-tab-style"],
#mcg-tab-libraries:checked ~ .mcg-tabs label[for="mcg-tab-libraries"],
#mcg-tab-history:checked ~ .mcg-tabs label[for="mcg-tab-history"],
#mcg-tab-maintain:checked ~ .mcg-tabs label[for="mcg-tab-maintain"] {
  background: rgb(var(--v-theme-surface)); color: var(--mcg-ink); font-weight: 600;
  box-shadow: 0 1px 3px rgba(0,0,0,.14);
}
#mcg-tab-style:checked ~ .mcg-panel-style,
#mcg-tab-libraries:checked ~ .mcg-panel-libraries,
#mcg-tab-history:checked ~ .mcg-panel-history,
#mcg-tab-maintain:checked ~ .mcg-panel-maintain { display: block; animation: mcg-fade .22s ease; }
#mcg-tab-style:checked ~ .mcg-hero .mcg-when-style,
#mcg-tab-libraries:checked ~ .mcg-hero .mcg-when-libraries,
#mcg-tab-history:checked ~ .mcg-hero .mcg-when-history,
#mcg-tab-maintain:checked ~ .mcg-hero .mcg-when-maintain { display: inline-flex; gap: 8px; flex-wrap: wrap; }
@keyframes mcg-fade { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }

/* 通用小部件 */
.mcg-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
.mcg-heading-title { font-size: 15px; font-weight: 600; }
.mcg-heading-note { font-size: 12.5px; color: var(--mcg-muted); margin-top: 2px; }
.mcg-notice {
  display: flex; gap: 10px; align-items: flex-start; font-size: 13px; line-height: 1.6; color: var(--mcg-ink);
  background: var(--mcg-gold-soft); border-radius: 12px; padding: 11px 14px; margin-bottom: 16px;
}
.mcg-notice .v-icon { color: var(--mcg-gold); }
.mcg-empty { text-align: center; color: var(--mcg-muted); font-size: 13.5px; padding: 44px 16px; border: 1px dashed var(--mcg-line); border-radius: 14px; }
.mcg-empty .v-icon { color: var(--mcg-faint); margin-bottom: 8px; }
.mcg-chip { display: inline-flex; align-items: center; font-size: 11px; line-height: 1; padding: 4px 7px; border-radius: 6px; background: var(--mcg-raise-strong); color: var(--mcg-muted); white-space: nowrap; }
.mcg-chip-gold { background: var(--mcg-gold-soft); color: var(--mcg-gold); }
.mcg-chip-ok { background: rgba(78,155,122,.15); color: var(--mcg-ok); }
.mcg-chip-bad { background: rgba(200,102,79,.15); color: var(--mcg-bad); }

/* 分段选择 */
.mcg-seg { display: inline-flex; padding: 3px; border-radius: 10px; background: var(--mcg-raise-strong); }
.mcg-seg-item { display: inline-flex; align-items: center; font-size: 13px; padding: 5px 14px; border-radius: 8px; cursor: pointer; color: var(--mcg-muted); }
.mcg-rec {
  display: inline-flex; align-items: center; flex: none; margin-left: 6px; padding: 3px 7px; border-radius: 6px;
  font-size: 11px; line-height: 1; font-weight: 700; letter-spacing: .04em; white-space: nowrap;
  background: linear-gradient(135deg, #F7BE62, #EE7F3F); color: #2A1606; box-shadow: 0 1px 6px rgba(238,127,63,.42);
}

/* 选择要设置风格的媒体库 */
.mcg-targets { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-bottom: 14px; }
.mcg-targets-label { font-size: 12.5px; color: var(--mcg-muted); margin-right: 4px; }
.mcg-target {
  display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; padding: 5px 11px; border-radius: 8px; cursor: pointer;
  background: var(--mcg-raise-strong); color: var(--mcg-muted); max-width: 240px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.mcg-target:hover { color: var(--mcg-ink); }
.mcg-target-on { background: var(--mcg-gold); color: #1C160E; font-weight: 600; }
.mcg-target-on:hover { color: #1C160E; }
.mcg-target-dot { flex: none; width: 6px; height: 6px; border-radius: 50%; background: var(--mcg-gold); }
.mcg-target-on .mcg-target-dot { background: #1C160E; }
.mcg-target-bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; justify-content: space-between; font-size: 13px; line-height: 1.6; background: var(--mcg-gold-soft); border-radius: 12px; padding: 9px 12px 9px 14px; margin-bottom: 14px; }
.mcg-target-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.mcg-seg-item:hover { color: var(--mcg-ink); }
.mcg-seg-on { background: rgb(var(--v-theme-surface)); color: var(--mcg-ink); font-weight: 600; box-shadow: 0 1px 3px rgba(0,0,0,.14); }

/* 风格画廊 */
.mcg-gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(245px, 1fr)); gap: 14px; }
.mcg-form .mcg-gallery { grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); }
.mcg-form .mcg-gallery.mcg-gallery-three { grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); }
.mcg-style {
  position: relative; display: block; border-radius: 14px; overflow: hidden; cursor: pointer; background: var(--mcg-raise);
  outline: 1px solid var(--mcg-line); outline-offset: -1px; transition: transform .2s ease, box-shadow .2s ease, outline-color .2s ease;
}
.mcg-style:hover { transform: translateY(-2px); box-shadow: 0 10px 26px rgba(0,0,0,.20); }
.mcg-style-on { outline: 2px solid var(--mcg-gold); outline-offset: -2px; }
.mcg-style img { display: block; width: 100%; aspect-ratio: 16 / 9; object-fit: cover; }
.mcg-style-media { position: relative; overflow: hidden; }
.mcg-style-motion { position: absolute; inset: 0; background-size: cover; background-position: center; }
.mcg-gif-tag { position: absolute; right: 8px; bottom: 8px; padding: 3px 6px; border-radius: 5px; font-size: 10px; line-height: 1; font-weight: 700; letter-spacing: .08em; background: rgba(12,10,8,.74); color: #F7D9A4; }
.mcg-style-meta { padding: 10px 12px 12px; }
.mcg-style-name { display: flex; align-items: center; justify-content: space-between; gap: 6px; font-size: 14px; font-weight: 600; }
.mcg-style-desc { font-size: 12px; color: var(--mcg-muted); margin-top: 4px; line-height: 1.5; }
.mcg-style-tick {
  position: absolute; top: 8px; right: 8px; width: 24px; height: 24px; border-radius: 50%; display: none;
  align-items: center; justify-content: center; background: var(--mcg-gold); color: #1C160E;
}
.mcg-style-on .mcg-style-tick { display: flex; }

/* 媒体库列表 */
.mcg-libs { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
.mcg-lib { display: flex; gap: 12px; align-items: center; padding: 10px; border-radius: 14px; background: var(--mcg-raise); border: 1px solid var(--mcg-line); min-width: 0; }
.mcg-lib-art { flex: none; width: 112px; aspect-ratio: 16 / 9; border-radius: 9px; object-fit: cover; background: var(--mcg-raise-strong); display: flex; align-items: center; justify-content: center; color: var(--mcg-faint); }
.mcg-lib-info { flex: 1; min-width: 0; }
.mcg-lib-name { font-size: 14px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mcg-lib-note { font-size: 12px; color: var(--mcg-muted); margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mcg-lib-chips { display: flex; gap: 4px; margin-top: 6px; flex-wrap: wrap; }
.mcg-lib-actions { flex: none; display: flex; flex-direction: column; gap: 6px; }

/* 历史 */
.mcg-history { display: grid; grid-template-columns: repeat(auto-fill, minmax(205px, 1fr)); gap: 14px; }
.mcg-cover { border-radius: 14px; overflow: hidden; background: var(--mcg-raise); border: 1px solid var(--mcg-line); }
.mcg-cover img { display: block; width: 100%; aspect-ratio: 16 / 9; object-fit: cover; }
.mcg-cover-meta { display: flex; align-items: center; gap: 6px; padding: 8px 6px 8px 12px; }
.mcg-cover-text { flex: 1; min-width: 0; }
.mcg-cover-name { font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mcg-cover-sub { font-size: 11.5px; color: var(--mcg-muted); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mcg-more { display: flex; justify-content: center; margin-top: 16px; }

/* 维护 */
.mcg-grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }
.mcg-box { padding: 16px; border-radius: 14px; background: var(--mcg-raise); border: 1px solid var(--mcg-line); }
.mcg-box-title { font-size: 14px; font-weight: 600; display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.mcg-box-note { font-size: 12.5px; color: var(--mcg-muted); margin: 6px 0 12px; line-height: 1.6; }
.mcg-confirm { display: flex; flex-wrap: wrap; gap: 8px; }
.mcg-check { display: flex; gap: 10px; align-items: flex-start; font-size: 13px; padding: 8px 0; border-top: 1px solid var(--mcg-line); }
.mcg-check:first-of-type { border-top: 0; }
.mcg-check-ok .v-icon { color: var(--mcg-ok); }
.mcg-check-bad .v-icon { color: var(--mcg-bad); }
.mcg-check-note { color: var(--mcg-muted); font-size: 12px; margin-top: 2px; word-break: break-all; }

/* 设置页 */
.mcg-form .v-tabs { margin-bottom: 18px; border-bottom: 1px solid var(--mcg-line); }
.mcg-form .v-tab { text-transform: none; letter-spacing: .02em; }
.mcg-section { padding: 16px 18px; border-radius: 14px; background: var(--mcg-raise); border: 1px solid var(--mcg-line); margin-bottom: 14px; }
.mcg-section-head { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
.mcg-section-mark { width: 30px; height: 30px; border-radius: 9px; display: flex; align-items: center; justify-content: center; background: var(--mcg-gold-soft); color: var(--mcg-gold); flex: none; }
.mcg-section-title { font-size: 14px; font-weight: 600; }
.mcg-section-note { font-size: 12px; color: var(--mcg-muted); margin-top: 1px; }
.mcg-editor { border-radius: 12px; overflow: hidden; border: 1px solid var(--mcg-line); }
.mcg-editor-bar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.mcg-toggle-label { font-size: 12px; color: var(--mcg-muted); margin-bottom: 6px; }
@media (max-width: 600px) {
  .mcg-hero { padding: 20px 18px 18px; }
  .mcg-hero h2 { font-size: 23px; }
  .mcg-libs { grid-template-columns: 1fr; }
}
"""


# ---------------------------------------------------------------- 节点工具


def node(component: str, cls: Optional[str] = None, text: Optional[str] = None,
         content: Optional[List[dict]] = None, events: Optional[dict] = None, **props) -> Dict[str, Any]:
    result: Dict[str, Any] = {"component": component}
    if cls:
        props["class"] = cls
    if props:
        result["props"] = props
    if text is not None:
        result["text"] = text
    if content is not None:
        result["content"] = content
    if events:
        result["events"] = events
    return result


def div(cls: Optional[str] = None, *children, text: Optional[str] = None, **props) -> Dict[str, Any]:
    return node("div", cls=cls, text=text, content=list(children) if children else None, **props)


def icon(name: str, size: int = 18, cls: Optional[str] = None) -> Dict[str, Any]:
    # 渲染器总会传默认插槽，VIcon 有插槽时忽略 icon 属性，所以图标名要放在 text 里
    return node("VIcon", cls=cls, text=name, size=size)


def post(api_prefix: str, path: str) -> Dict[str, Any]:
    return {"click": {"api": f"{api_prefix}/{path}", "method": "post"}}


def button(label: str, kind: str, api_prefix: str, path: str, prepend: Optional[str] = None,
           size: str = "default") -> Dict[str, Any]:
    props: Dict[str, Any] = {"variant": "flat", "size": size}
    if prepend:
        props["prepend-icon"] = prepend
    return node("VBtn", cls=f"mcg-btn mcg-btn-{kind}", text=label, events=post(api_prefix, path), **props)


def style_label(index: int, variant: str) -> str:
    return f"{style_info(index).name} · {'动态' if variant == 'animated' else '静态'}"


def recommend_badge() -> Dict[str, Any]:
    return node("span", cls="mcg-rec", text="推荐")


def _preview_media(entry, show_motion: Any, local: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """风格卡片的预览图。动态预览盖在静态图上，加载不出来时用户看到的仍是静态图。local 是用媒体库生成的预览。"""
    still = local["still"] if local else PREVIEWS.get(entry.index, "")
    motion = local["gif"] if local else PREVIEW_GIF.format(index=entry.index)
    children = [node("img", src=still, alt=entry.name, loading="lazy")]
    if show_motion and motion:
        layers = [
            div("mcg-style-motion", style=f"background-image:url({motion})"),
            node("span", cls="mcg-gif-tag", text="GIF"),
        ]
        # 渲染器处理 show 时会改写 style.display，同一节点上再有字符串 style 就会抛错，整个页签都不显示。
        # 所以条件放在外层，样式留在内层
        children += [div(None, *layers, show=show_motion)] if isinstance(show_motion, str) else layers
    return div("mcg-style-media", *children)


# ---------------------------------------------------------------- 详情页


def _hero(ctx: Dict[str, Any]) -> Dict[str, Any]:
    task = ctx["task"]
    prefix = ctx["api_prefix"]
    running = bool(task.get("running"))

    action_groups = []
    for tab, _name, _icon in PAGE_TABS:
        # 同一组按钮按当前页签各渲染一份，操作后刷新页面时能留在原页签
        if running:
            actions = [
                button("刷新进度", "gold", prefix, f"refresh?tab={tab}", "mdi-refresh"),
                button("停止", "glass", prefix, f"stop_task?tab={tab}", "mdi-stop-circle-outline"),
            ]
        else:
            actions = [
                button("生成全部封面", "gold", prefix, f"generate_now?tab={tab}", "mdi-play"),
                button("刷新", "glass", prefix, f"refresh?tab={tab}", "mdi-refresh"),
            ]
        action_groups.append(div(f"mcg-when mcg-when-{tab}", *actions))

    if running:
        total = max(1, int(task.get("total") or 1))
        done = min(total, int(task.get("done") or 0))
        status_value = f"生成中 {done}/{total}"
    elif not ctx["enabled"]:
        status_value = "未启用"
    else:
        status_value = "空闲"

    children: List[dict] = []
    if ctx.get("hero_image"):
        children.append(div("mcg-hero-art", style=f"background-image:url({ctx['hero_image']})"))
    children.append(div("mcg-hero-shade"))
    children.append(
        div(
            "mcg-hero-body",
            div(
                None,
                div("mcg-eyebrow", text="ANIMATED COVER STUDIO"),
                node("h2", text="动态封面工坊"),
                div("mcg-hero-sub", text="让媒体库封面动起来。每款风格都能生成 GIF 动态封面，详细参数在右下角的齿轮设置里。"),
            ),
            div("mcg-hero-actions", *action_groups),
        )
    )
    own_styles = sum(1 for library in ctx["libraries"] if library.get("own_style"))
    stats = [
        ("状态", status_value),
        ("默认风格", style_label(ctx["style_index"], ctx["style_variant"])),
        ("媒体库", (f"{ctx['library_active']} / {len(ctx['libraries'])}"
                    + (f" · {own_styles} 个单独设置" if own_styles else "")) if ctx["libraries"] else "—"),
        ("上次运行", ctx.get("last_run_text") or "尚未运行"),
    ]
    children.append(
        div(
            "mcg-stats",
            *[div("mcg-stat", div("mcg-stat-label", text=label), div("mcg-stat-value", text=value, title=value))
              for label, value in stats],
        )
    )
    if running:
        total = max(1, int(task.get("total") or 1))
        percent = int(min(100, int(task.get("done") or 0) * 100 / total))
        children.append(div("mcg-progress", div("mcg-progress-bar", style=f"width:{max(percent, 4)}%")))
        current = task.get("current")
        note = f"正在处理：{current}" if current else "正在准备…"
        children.append(div("mcg-progress-note", text=f"{note}（生成在后台进行，可随时关闭此窗口）"))
    elif ctx.get("last_result"):
        children.append(div("mcg-progress-note", text=ctx["last_result"]))
    return div("mcg-hero", *children)


def _style_targets(ctx: Dict[str, Any], target: Optional[Dict[str, Any]]) -> List[dict]:
    """选择风格应用到哪里：所有媒体库（默认风格），或者某一个媒体库。"""
    libraries = ctx["libraries"]
    if not libraries:
        return []
    prefix = ctx["api_prefix"]
    chips = [
        div("mcg-targets-label", text="应用到"),
        node("span", cls="mcg-target" + (" mcg-target-on" if not target else ""), text="所有媒体库",
             events=post(prefix, "style_target?tab=style")),
    ]
    for library in libraries:
        content = [node("span", text=library["name"])]
        if library.get("own_style"):
            content.append(node("span", cls="mcg-target-dot", title="已单独设置风格"))
        selected = target is not None and library["key"] == target["key"]
        chips.append(node("span", cls="mcg-target" + (" mcg-target-on" if selected else ""), content=content,
                          title=library["name"],
                          events=post(prefix, f"style_target?library={library['encoded_key']}&tab=style")))
    result = [div("mcg-targets", *chips)]
    if target:
        if target.get("own_style"):
            label = style_label(target["own_index"], target["own_variant"])
            note = f"「{target['name']}」单独使用「{label}」，不受默认风格影响。"
            actions = [button("恢复跟随默认", "quiet", prefix,
                              f"clear_library_style?library={target['encoded_key']}&tab=style", size="small")]
        else:
            label = style_label(ctx["style_index"], ctx["style_variant"])
            note = f"「{target['name']}」现在跟随默认风格「{label}」。点下面的卡片可以给它单独设置。"
            actions = []
        actions.append(button("生成这个媒体库", "gold", prefix,
                              f"generate_now?library={target['encoded_key']}&tab=style", size="small"))
        result.append(div("mcg-target-bar", div(None, text=note), div("mcg-target-actions", *actions)))
    return result


def _local_preview_bar(ctx: Dict[str, Any], target: Optional[Dict[str, Any]]) -> List[dict]:
    """用媒体库的真实图片生成预览。默认用当前选中的媒体库，否则用第一个参与更新的媒体库。"""
    libraries = ctx["libraries"]
    source = target or next((item for item in libraries if item.get("active")), None)
    local = ctx.get("local_preview")
    # 生成中不能操作。没有预览时这一条只剩一句话，不如不显示
    if not source or (ctx["task"].get("running") and not local):
        return []
    prefix = ctx["api_prefix"]
    path = f"generate_preview?library={source['encoded_key']}&tab=style"
    if local:
        note = f"下面是你的媒体库「{local['library']}」的真实效果（{local['time']} 生成）。"
        actions = [button("看示例图", "quiet", prefix, "clear_preview?tab=style", size="small"),
                   button(f"用「{source['name']}」重新生成", "gold", prefix, path, size="small")]
    else:
        note = "想看看自己的片库用上这些风格的样子？只生成预览，不会改动媒体库封面。"
        actions = [button(f"用「{source['name']}」生成预览", "gold", prefix, path, "mdi-image-multiple-outline", size="small")]
    if ctx["task"].get("running"):
        actions = []
    return [div("mcg-target-bar mcg-local-bar", div(None, text=note), div("mcg-target-actions", *actions))]


def _style_panel(ctx: Dict[str, Any]) -> Dict[str, Any]:
    prefix = ctx["api_prefix"]
    target = next((item for item in ctx["libraries"] if item["key"] == ctx["style_target"]), None)
    if target and target.get("own_style"):
        variant, style_index = target["own_variant"], target["own_index"]
    else:
        variant, style_index = ctx["style_variant"], ctx["style_index"]
    target_query = f"&library={target['encoded_key']}" if target else ""
    children: List[dict] = []
    if ctx["warnings"]:
        children.append(
            div("mcg-notice", icon("mdi-information-outline"), div(None, text="；".join(ctx["warnings"])))
        )
    children.append(
        div(
            "mcg-heading",
            div(
                None,
                div("mcg-heading-title", text="封面风格"),
                div("mcg-heading-note", text="点击卡片即可切换，下次生成时生效。"
                    + ("" if ctx.get("local_preview") else "预览图为示例素材。")),
            ),
            div(
                "mcg-seg",
                node("span", cls="mcg-seg-item" + (" mcg-seg-on" if variant == "animated" else ""),
                     content=[node("span", text="动态"), recommend_badge()],
                     events=post(prefix, f"set_style_variant?variant=animated&tab=style{target_query}")),
                node("span", cls="mcg-seg-item" + (" mcg-seg-on" if variant == "static" else ""), text="静态",
                     events=post(prefix, f"set_style_variant?variant=static&tab=style{target_query}")),
            ),
        )
    )
    children.extend(_style_targets(ctx, target))
    children.extend(_local_preview_bar(ctx, target))
    local_styles = (ctx.get("local_preview") or {}).get("styles") or {}
    cards = []
    for entry in STYLE_CATALOG:
        selected = entry.index == style_index
        motion_note = f"动态：{entry.motion}" if variant == "animated" else entry.desc
        cards.append(
            div(
                "mcg-style" + (" mcg-style-on" if selected else ""),
                _preview_media(entry, variant == "animated", local_styles.get(entry.index)),
                div("mcg-style-tick", icon("mdi-check", 16)),
                div(
                    "mcg-style-meta",
                    div("mcg-style-name", node("span", text=entry.name)),
                    div("mcg-style-desc", text=motion_note),
                ),
                events=post(prefix, f"select_style?index={entry.index}&tab=style{target_query}"),
            )
        )
    children.append(div("mcg-gallery", *cards))
    return div("mcg-panel mcg-panel-style", *children)


def _libraries_panel(ctx: Dict[str, Any]) -> Dict[str, Any]:
    prefix = ctx["api_prefix"]
    libraries = ctx["libraries"]
    children: List[dict] = [
        div(
            "mcg-heading",
            div(
                None,
                div("mcg-heading-title", text="媒体库"),
                div("mcg-heading-note", text="每个媒体库可以用同一款风格，也可以单独设置；还可以只更新其中一个，方便试用新风格。"),
            ),
        )
    ]
    if not libraries:
        children.append(
            div("mcg-empty", icon("mdi-folder-off-outline", 32),
                div(None, text="还没有可用的媒体库。请在设置中选择媒体服务器并保存。"))
        )
        return div("mcg-panel mcg-panel-libraries", *children)

    rows = []
    for library in libraries:
        if library.get("own_style"):
            chips = [node("span", cls="mcg-chip mcg-chip-gold",
                          text=style_label(library["own_index"], library["own_variant"]))]
        else:
            chips = [node("span", cls="mcg-chip", text="跟随默认风格")]
        status = library.get("status") or {}
        if status:
            chips.append(node("span", cls="mcg-chip " + ("mcg-chip-ok" if status.get("ok") else "mcg-chip-bad"),
                              text="成功" if status.get("ok") else "失败"))
        if not library.get("active"):
            chips.append(node("span", cls="mcg-chip", text="未纳入批量更新"))
        if status and not status.get("ok") and status.get("message"):
            note = status["message"]
        elif status:
            note = f"{status.get('time', '')} · {status.get('style', '')}"
        else:
            note = f"{library.get('server', '')} · 尚未生成"
        if library.get("thumb"):
            art = node("img", cls="mcg-lib-art", src=library["thumb"], alt=library["name"], loading="lazy")
        else:
            art = div("mcg-lib-art", icon("mdi-image-outline", 22))
        info = [div("mcg-lib-name", text=library["name"], title=library["name"]),
                div("mcg-lib-note", text=note, title=status.get("message") or note),
                div("mcg-lib-chips", *chips)]
        rows.append(
            div(
                "mcg-lib",
                art,
                div("mcg-lib-info", *info),
                div(
                    "mcg-lib-actions",
                    button("生成", "quiet", prefix, f"generate_now?library={library['encoded_key']}&tab=libraries",
                           size="small"),
                    button("换风格", "quiet", prefix, f"style_target?library={library['encoded_key']}&tab=style",
                           size="small"),
                ),
            )
        )
    children.append(div("mcg-libs", *rows))
    return div("mcg-panel mcg-panel-libraries", *children)


def _history_panel(ctx: Dict[str, Any]) -> Dict[str, Any]:
    prefix = ctx["api_prefix"]
    items = ctx["history"]
    total = ctx["history_total"]
    ready = ctx["page_tab"] == "history"
    children: List[dict] = [
        div(
            "mcg-heading",
            div(
                None,
                div("mcg-heading-title", text="历史封面"),
                div("mcg-heading-note", text=f"共 {total} 张。每个媒体库最多保留 {ctx['history_limit']} 张，可在设置中调整。"),
            ),
        )
    ]
    if not items:
        note = "还没有生成记录。生成一次封面后会出现在这里。" if ready or not total else "正在载入…"
        children.append(div("mcg-empty", icon("mdi-image-multiple-outline", 32), div(None, text=note)))
        return div("mcg-panel mcg-panel-history", *children)

    cards = []
    for item in items:
        cards.append(
            div(
                "mcg-cover",
                node("img", src=item["src"], alt=item["title"], loading="lazy"),
                div(
                    "mcg-cover-meta",
                    div(
                        "mcg-cover-text",
                        div("mcg-cover-name", text=item["title"], title=item["title"]),
                        div("mcg-cover-sub", text=f"{item['mtime']} · {item['size']}"),
                    ),
                    node("VBtn", icon="mdi-delete-outline", variant="text", size="small", title="删除",
                         color="#C8664F",
                         events=post(prefix, f"delete_saved_cover?file={item['encoded_name']}&tab=history")),
                ),
            )
        )
    children.append(div("mcg-history", *cards))
    if total > len(items):
        children.append(
            div("mcg-more", button(f"显示更多（还有 {total - len(items)} 张）", "quiet", prefix,
                                   "history_more?tab=history"))
        )
    return div("mcg-panel mcg-panel-history", *children)


def _clean_actions(ctx: Dict[str, Any], what: str, path: str, prepend: str) -> List[dict]:
    """清理按钮。删除不能撤销，第一次点只是询问。"""
    prefix = ctx["api_prefix"]
    if ctx.get("clean_armed") != what:
        return [button("清理", "danger", prefix, path, prepend, "small")]
    return [div(
        "mcg-confirm",
        button("确认清理，不能恢复", "danger", prefix, path, prepend, "small"),
        button("取消", "quiet", prefix, "refresh?tab=maintain", size="small"),
    )]


def _maintain_panel(ctx: Dict[str, Any]) -> Dict[str, Any]:
    prefix = ctx["api_prefix"]
    checks = []
    for check in ctx["checks"]:
        state = "ok" if check["ok"] else "bad"
        mark = "mdi-check-circle-outline" if check["ok"] else "mdi-alert-circle-outline"
        body = [div(None, text=check["title"])]
        if check.get("note"):
            body.append(div("mcg-check-note", text=check["note"]))
        checks.append(div(f"mcg-check mcg-check-{state}", icon(mark), div(None, *body)))
    return div(
        "mcg-panel mcg-panel-maintain",
        div(
            "mcg-grid-2",
            div("mcg-box", div("mcg-box-title", text="运行检查"), div("mcg-box-note", text="生成失败时可以先看这里。"), *checks),
            div(
                None,
                div(
                    "mcg-box",
                    div("mcg-box-title", node("span", text="封面与素材缓存"),
                        node("span", cls="mcg-chip", text=ctx["cache_images"])),
                    div("mcg-box-note", text="删除下载的素材和历史封面。媒体库里已经生效的封面不受影响；"
                                             "自定义保存目录中只会删除插件生成的文件。"),
                    *_clean_actions(ctx, "images", "clean_images?tab=maintain", "mdi-image-remove-outline"),
                ),
                div(
                    "mcg-box",
                    div("mcg-box-title", node("span", text="字体缓存"),
                        node("span", cls="mcg-chip", text=ctx["cache_fonts"])),
                    div("mcg-box-note", text="字体损坏或想重新下载时使用，下次生成时自动重新获取。"),
                    *_clean_actions(ctx, "fonts", "clean_fonts?tab=maintain", "mdi-format-font"),
                    style="margin-top:14px;",
                ),
            ),
        ),
    )


def build_page(ctx: Dict[str, Any]) -> List[dict]:
    active_tab = ctx["page_tab"]
    radios = [
        node("input", cls="mcg-radio", type="radio", name="mcg-tab", id=f"mcg-tab-{tab}", checked=tab == active_tab)
        for tab, _name, _icon in PAGE_TABS
    ]
    tabs = div(
        "mcg-tabs",
        *[
            node("label", cls="mcg-tab", content=[icon(tab_icon, 16), node("span", text=name)],
                 # 历史页的图片按需加载：切换过去时请求一次
                 events=post(ctx["api_prefix"], "refresh?tab=history")
                 if tab == "history" and active_tab != "history" and ctx["history_total"] else None,
                 **{"for": f"mcg-tab-{tab}"})
            for tab, name, tab_icon in PAGE_TABS
        ],
    )
    return [
        div(
            "mcg",
            node("style", text=CSS),
            *radios,
            _hero(ctx),
            tabs,
            _style_panel(ctx),
            _libraries_panel(ctx),
            _history_panel(ctx),
            _maintain_panel(ctx),
        )
    ]


# ---------------------------------------------------------------- 设置页


def _section(mark: str, title: str, note: str, *children, show: Optional[str] = None) -> Dict[str, Any]:
    props: Dict[str, Any] = {}
    if show:
        props["show"] = show
    return div(
        "mcg-section",
        div("mcg-section-head", div("mcg-section-mark", icon(mark, 17)),
            div(None, div("mcg-section-title", text=title), div("mcg-section-note", text=note))),
        *children,
        **props,
    )


def _row(*columns) -> Dict[str, Any]:
    return node("VRow", content=list(columns))


def _col(child: Dict[str, Any], md: int = 6) -> Dict[str, Any]:
    return node("VCol", content=[child], cols=12, md=md)


def _text(model: str, label: str, hint: str = "", number: bool = False, icon_name: Optional[str] = None,
          placeholder: Optional[str] = None, **extra) -> Dict[str, Any]:
    props: Dict[str, Any] = {"model": model, "label": label, "density": "comfortable", "variant": "outlined",
                             "color": ACCENT}
    if hint:
        props.update({"hint": hint, "persistentHint": True})
    if number:
        props["type"] = "number"
    if icon_name:
        props["prependInnerIcon"] = icon_name
    if placeholder:
        props["placeholder"] = placeholder
    props.update(extra)
    return node("VTextField", **props)


def _select(model: str, label: str, items: Any, hint: str = "", icon_name: Optional[str] = None, **extra) -> Dict[str, Any]:
    props: Dict[str, Any] = {"model": model, "label": label, "items": items, "density": "comfortable",
                             "variant": "outlined", "color": ACCENT}
    if hint:
        props.update({"hint": hint, "persistentHint": True})
    if icon_name:
        props["prependInnerIcon"] = icon_name
    props.update(extra)
    return node("VSelect", **props)


def _switch(model: str, label: str, hint: str = "") -> Dict[str, Any]:
    props: Dict[str, Any] = {"model": model, "label": label, "color": ACCENT}
    if hint:
        props.update({"hint": hint, "persistentHint": True})
    return node("VSwitch", **props)


def _toggle(model: str, label: str, options: List[tuple], recommended: Any = None) -> Dict[str, Any]:
    buttons = []
    for value, title in options:
        content = [node("span", text=title)]
        if recommended is not None and value == recommended:
            content.append(recommend_badge())
        buttons.append(node("VBtn", value=value, style="flex:1;", cls="text-none", content=content))
    return div(
        None,
        div("mcg-toggle-label", text=label),
        node(
            "VBtnToggle",
            model=model, mandatory=True, divided=True, density="comfortable", color=ACCENT,
            variant="outlined", style="width:100%;",
            content=buttons,
        ),
    )


def _style_picker() -> Dict[str, Any]:
    cards = []
    for entry in STYLE_CATALOG:
        value = f"static_{entry.index}"
        cards.append(
            node(
                "div",
                content=[
                    _preview_media(entry, "{{ cover_style_variant === 'animated' }}"),
                    div("mcg-style-tick", icon("mdi-check", 16)),
                    div(
                        "mcg-style-meta",
                        div("mcg-style-name", node("span", text=entry.name)),
                        div("mcg-style-desc", text=f"动态：{entry.motion}",
                            show="{{ cover_style_variant === 'animated' }}"),
                        div("mcg-style-desc", text=entry.desc, show="{{ cover_style_variant !== 'animated' }}"),
                    ),
                ],
                **{
                    "class": "{{ cover_style_base === '%s' ? 'mcg-style mcg-style-on' : 'mcg-style' }}" % value,
                    "onClick": "event => { cover_style_base = '%s' }" % value,
                },
            )
        )
    return div("mcg-gallery", *cards)


def _basic_tab(ctx: Dict[str, Any]) -> List[dict]:
    return [
        _section(
            "mdi-power", "运行", "启用插件并决定什么时候更新封面",
            _row(
                _col(_switch("enabled", "启用插件"), 4),
                _col(_switch("transfer_monitor", "入库后自动更新", "媒体入库后刷新其所在媒体库的封面"), 4),
                _col(_switch("notify", "任务完成时通知", "手动或定时任务结束后发送消息"), 4),
                _col(_switch("update_now", "保存后立即更新一次"), 4),
                _col(_text("delay", "入库后等待（秒）", "等待媒体服务器扫描完成，多次入库会合并处理", number=True,
                           icon_name="mdi-timer-sand", min=0), 4),
                _col(node("VCronField", model="cron", label="定时更新", placeholder="5 位 cron 表达式，留空不启用",
                          density="comfortable", variant="outlined", color=ACCENT), 4),
                _col(_switch("rotate_style", "定时轮换风格",
                             "每次定时更新换下一款风格，所有媒体库保持统一；单独设置过风格的媒体库不变"), 12),
            ),
        ),
        _section(
            "mdi-server-network", "媒体服务器", "选择要更新封面的服务器和媒体库",
            _row(
                _col(_select("selected_servers", "媒体服务器", ctx["server_items"], multiple=True, chips=True,
                             clearable=True, hint="支持 Emby 和 Jellyfin", icon_name="mdi-server"), 6),
                _col(_select("sort_by", "选图顺序", [
                    {"title": "随机", "value": "Random"},
                    {"title": "最新入库", "value": "DateCreated"},
                    {"title": "最新发行", "value": "PremiereDate"},
                ], hint="决定从媒体库里挑选哪些条目的图片", icon_name="mdi-sort"), 6),
                _col(_select("include_libraries", "批量更新的媒体库", ctx["library_items_expr"], multiple=True,
                             chips=True, clearable=True, hint="留空表示全部媒体库；电视直播不包含在内，需要时单独勾选",
                             icon_name="mdi-folder-multiple-image"), 12),
            ),
        ),
    ]


def _style_tab() -> List[dict]:
    return [
        _section(
            "mdi-palette-swatch-outline", "封面风格",
            "这里是所有媒体库的默认风格。想给某个媒体库单独设置，到插件详情页「风格」页上方选择媒体库",
            div(None, _toggle("cover_style_variant", "输出类型",
                              [("animated", "GIF 动态封面"), ("static", "静态图片")], recommended="animated"),
                style="margin-bottom:16px;max-width:380px;"),
            _style_picker(),
        ),
        _section(
            "mdi-image-outline", "选图与输出", "自动匹配：每个画框选比例合适的图，围绕人物取景。只有海报时完整显示海报，不把人物切开",
            _row(
                _col(_toggle("use_primary", "素材图片", [("auto", "自动匹配"), (True, "只用海报"), (False, "只用背景图")],
                             recommended="auto"), 6),
                _col(_select("resolution", "静态图分辨率", [
                    {"title": "1080p（1920×1080）", "value": "1080p"},
                    {"title": "720p（1280×720）", "value": "720p"},
                    {"title": "480p（854×480）", "value": "480p"},
                ], icon_name="mdi-monitor-screenshot"), 6),
            ),
        ),
        _section(
            "mdi-palette-outline", "点缀色", "标题线条、胶囊标签等处使用的颜色。默认每款风格有自己的颜色，同一排封面看起来更统一",
            _row(
                _col(_select("bg_color_mode", "颜色来源", [
                    {"title": "风格默认", "value": "auto"},
                    {"title": "从图片提取", "value": "image"},
                    {"title": "统一使用自定义颜色", "value": "custom"},
                    {"title": "从标题配置读取", "value": "config"},
                ], icon_name="mdi-eyedropper-variant"), 6),
                _col(_text("color_ratio", "底色和压暗强度", "0 到 1，默认 0.8。越大底色越贴近图片色调，图上的压暗也越重", number=True,
                           icon_name="mdi-format-color-fill", min=0, max=1, step=0.05), 6),
                node("VCol", cols=12, show="{{ bg_color_mode === 'custom' }}", content=[
                    _text("custom_bg_color", "自定义颜色", "支持 #十六进制、rgb(...)、颜色英文名",
                          icon_name="mdi-eyedropper", placeholder="#58DDA3"),
                    node("VColorPicker", model="custom_bg_color", mode="hexa", showSwatches=True, hideInputs=True,
                         elevation=0, cls="mt-3", style="background:transparent;"),
                ]),
            ),
        ),
    ]


def _title_tab(ctx: Dict[str, Any]) -> List[dict]:
    return [
        _section(
            "mdi-format-title", "封面标题", "未配置的媒体库直接使用媒体库名称，副标题留空",
            div(
                "mcg-editor-bar",
                node("VBtn", text="按我的媒体库生成", variant="tonal", color=ACCENT, size="small", cls="text-none",
                     **{"prepend-icon": "mdi-auto-fix",
                        "onClick": "event => { title_config = %s }" % json.dumps(ctx["title_from_libraries"], ensure_ascii=False)}),
                node("VBtn", text="填入示例", variant="text", color=ACCENT, size="small", cls="text-none",
                     **{"prepend-icon": "mdi-text-box-outline",
                        "onClick": "event => { title_config = %s }" % json.dumps(TITLE_EXAMPLE, ensure_ascii=False)}),
            ),
            div("mcg-editor", node("VAceEditor", modelvalue="title_config", lang="yaml", theme="tomorrow",
                                   style="height:26rem;font-size:14px;")),
        ),
    ]


def _typography_picker() -> Dict[str, Any]:
    """一键换字体的卡片。字号、字距仍由风格决定；选「跟随风格」时连手动填写的参数也一起清空。"""
    numeric_fields = ("zh_font_size", "en_font_size", "zh_font_offset", "title_spacing", "en_line_spacing",
                      "zh_letter_spacing", "en_letter_spacing")
    cards = []
    for preset in TYPOGRAPHY_PRESETS:
        values = preset["values"]
        # 同时清空自定义字体，否则自定义字体会继续优先生效
        assignments = [f"{field} = {json.dumps(value)}" for field, value in values.items()]
        assignments += ['zh_font_custom = ""', 'en_font_custom = ""']
        if preset["key"] == "auto":
            assignments += [f"{field} = null" for field in numeric_fields] + ["title_scale = 1"]
        checks = ['!zh_font_custom', '!en_font_custom']
        checks += [f"{field} === {json.dumps(value)}" for field, value in values.items()]
        cards.append(
            node(
                "div",
                content=[
                    node("img", src=TYPOGRAPHY_PREVIEWS.get(preset["key"], ""), alt=preset["name"], loading="lazy"),
                    div("mcg-style-tick", icon("mdi-check", 16)),
                    div(
                        "mcg-style-meta",
                        div("mcg-style-name",
                            node("span", content=[node("span", text=preset["name"])]
                                 + ([recommend_badge()] if preset.get("recommended") else []),
                                 style="display:inline-flex;align-items:center;")),
                        div("mcg-style-desc", text=preset["desc"]),
                    ),
                ],
                **{
                    "class": "{{ (%s) ? 'mcg-style mcg-style-on' : 'mcg-style' }}" % " && ".join(checks),
                    "onClick": "event => { %s }" % "; ".join(assignments),
                },
            )
        )
    return div("mcg-gallery mcg-gallery-three", *cards)


def _typography_tab(ctx: Dict[str, Any]) -> List[dict]:
    return [
        _section(
            "mdi-auto-fix", "字体搭配", "每款风格都有设计好的字体和字号。想换字体可以点这里，字号和间距仍然跟随风格",
            _typography_picker(),
        ),
        _section(
            "mdi-format-font", "字体", "内置字体会自动下载；也可以填字体链接或容器内的本地路径",
            _row(
                _col(_select("zh_font_preset", "主标题字体", ctx["zh_font_items"], icon_name="mdi-ideogram-cjk",
                             hint="标题含中文时请选中文字体"), 6),
                _col(_select("en_font_preset", "副标题字体", ctx["en_font_items"], icon_name="mdi-format-font",
                             hint="副标题含中文而字体不支持时，会自动换成能显示的字体"), 6),
                _col(_text("zh_font_custom", "自定义主标题字体", "链接或路径，留空使用上面的选择", icon_name="mdi-link-variant"), 6),
                _col(_text("en_font_custom", "自定义副标题字体", "链接或路径，留空使用上面的选择", icon_name="mdi-link-variant"), 6),
            ),
        ),
        _section(
            "mdi-format-size", "精细调整", "以 1080p 为基准，其他分辨率会等比缩放。全部留空就是每款风格的原始设计，填了才覆盖",
            _row(
                _col(_text("title_scale", "整体缩放", "默认 1.0，同时缩放字号和间距", number=True, icon_name="mdi-arrow-expand-all",
                           min=0.1, step=0.05), 4),
                _col(_text("zh_font_size", "主标题字号", "留空跟随风格，放不下时自动缩小", number=True, icon_name="mdi-format-size", min=1), 4),
                _col(_text("en_font_size", "副标题字号", "留空跟随风格，放不下时自动缩小", number=True, icon_name="mdi-format-size", min=1), 4),
                _col(_text("zh_letter_spacing", "主标题字距", "字号的百分比，可以为负。留空跟随风格", number=True,
                           icon_name="mdi-format-letter-spacing"), 4),
                _col(_text("en_letter_spacing", "副标题字距", "字号的百分比，可以为负。留空跟随风格", number=True,
                           icon_name="mdi-format-letter-spacing"), 4),
                _col(_text("title_spacing", "主副标题间距", "留空跟随风格", number=True, icon_name="mdi-arrow-collapse-vertical",
                           min=0), 4),
                _col(_text("en_line_spacing", "副标题行距", "副标题折行时使用。留空跟随风格", number=True, icon_name="mdi-format-line-height",
                           min=0), 4),
                _col(_text("zh_font_offset", "标题上下偏移", "上移填负值，默认 0", number=True,
                           icon_name="mdi-arrow-up-down"), 4),
                _col(_text("blur_size", "背景虚化强度", "预告、景深、详情、扇面的虚化背景使用，默认 50", number=True, icon_name="mdi-blur",
                           min=0), 4),
            ),
        ),
    ]


def _motion_tab() -> List[dict]:
    return [
        div(
            "mcg-notice", icon("mdi-information-outline"),
            div(None, text="Hills、yamby、小幻影视都能播放。客户端请求的宽度小于动图宽度时，Emby 会把它压成静态图，所以尺寸越小越容易动起来。首帧就是完整封面，只显示静态图的客户端也能看到成品。需要容器内有 ffmpeg，没有时自动生成静态封面。"),
        ),
        _section(
            "mdi-play-box-multiple-outline", "GIF 动图", "只有使用动态封面时才生效。画面大部分时间静止、只在转场时运动，320×180 一张通常 1.5 到 3 MB",
            _row(
                _col(_select("animation_resolution", "尺寸", [
                    {"title": "480×270（最清晰，部分客户端会变静态）", "value": "480x270"},
                    {"title": "400×225（推荐）", "value": "400x225"},
                    {"title": "320×180（体积最小，兼容最好）", "value": "320x180"},
                ], icon_name="mdi-aspect-ratio"), 4),
                _col(_select("animation_reduce_colors", "颜色数量", [
                    {"title": "256 色（画质最好）", "value": "off"},
                    {"title": "192 色（推荐）", "value": "medium"},
                    {"title": "96 色（体积最小）", "value": "strong"},
                ], icon_name="mdi-palette-outline"), 4),
                _col(_text("animation_image_count", "轮播图片数量", "3 到 12 张，默认 6。不少于这款风格一屏需要的数量",
                           number=True, icon_name="mdi-image-multiple", min=3, max=12), 4),
                _col(_text("animation_duration", "循环时长（秒）", "默认 12。图片多时可以加长，转场不会显得匆忙", number=True, icon_name="mdi-clock-outline", min=1, max=60), 6),
                _col(_text("animation_fps", "帧率", "默认 15，GIF 不需要更高", number=True, icon_name="mdi-speedometer", min=1, max=30), 6),
            ),
        ),
    ]


def _storage_tab() -> List[dict]:
    return [
        _section(
            "mdi-folder-image", "自定义图片", "不想用媒体库图片时，可以指定自己的素材。电视直播通常只有台标，建议在这里放图",
            _row(
                _col(_text("covers_input", "自定义图片目录",
                           "放在与媒体库同名的子目录，如 /mnt/custom/电影/a.jpg，填 /mnt/custom。插件不会改动该目录",
                           icon_name="mdi-folder-image", clearable=True), 12),
            ),
        ),
        _section(
            "mdi-history", "历史封面", "生成结果的本地备份，可在详情页查看和删除",
            _row(
                _col(_switch("save_recent_covers", "保存生成的封面"), 4),
                _col(_text("covers_history_limit_per_library", "每个媒体库保留", "1 到 100 张，默认 10", number=True,
                           icon_name="mdi-counter", min=1, max=100), 4),
                _col(_text("covers_page_history_limit", "详情页最多显示", "1 到 500 张，默认 50", number=True,
                           icon_name="mdi-image-multiple-outline", min=1, max=500), 4),
                _col(_text("covers_output", "保存目录", "留空保存到插件数据目录；清理缓存时只删除插件生成的文件",
                           icon_name="mdi-folder-download", clearable=True), 12),
            ),
        ),
    ]


def default_form_model() -> Dict[str, Any]:
    return {
        "enabled": True,
        "update_now": False,
        "transfer_monitor": True,
        "notify": False,
        "cron": "",
        "rotate_style": False,
        "delay": 60,
        "selected_servers": [],
        "include_libraries": [],
        "sort_by": "Random",
        "title_config": "",
        "tab": "basic-tab",
        "cover_style": "animated_1",
        "cover_style_base": "static_1",
        "cover_style_variant": "animated",
        "zh_font_preset": "auto",
        "en_font_preset": "auto",
        "covers_v3": True,
        "covers_v5": True,
        "covers_v6": True,
        "zh_font_custom": "",
        "en_font_custom": "",
        "zh_font_size": None,
        "en_font_size": None,
        "zh_font_offset": None,
        "title_spacing": None,
        "en_line_spacing": None,
        "zh_letter_spacing": None,
        "en_letter_spacing": None,
        "blur_size": 50,
        "color_ratio": 0.8,
        "title_scale": 1.0,
        "use_primary": "auto",
        "resolution": "720p",
        "bg_color_mode": "auto",
        "custom_bg_color": "",
        "animation_duration": 12,
        "animation_fps": 15,
        "animation_resolution": "400x225",
        "animation_reduce_colors": "medium",
        "animation_image_count": 6,
        "clean_images": False,
        "clean_fonts": False,
        "save_recent_covers": True,
        "covers_history_limit_per_library": 10,
        "covers_page_history_limit": 50,
        "style_naming_v2": True,
    }


def build_form(ctx: Dict[str, Any]) -> List[dict]:
    tabs = [
        ("basic-tab", "基础", "mdi-tune-variant", _basic_tab(ctx)),
        ("style-tab", "风格", "mdi-palette-swatch-outline", _style_tab()),
        ("title-tab", "标题", "mdi-format-title", _title_tab(ctx)),
        ("more-tab", "字体排版", "mdi-format-font", _typography_tab(ctx)),
        ("motion-tab", "GIF 动图", "mdi-play-box-multiple-outline", _motion_tab()),
        ("storage-tab", "目录与历史", "mdi-folder-outline", _storage_tab()),
    ]
    return [
        div(
            "mcg mcg-form",
            node("style", text=CSS),
            node(
                "VTabs", model="tab", color=ACCENT, showArrows=True,
                content=[node("VTab", value=key, cls="text-none",
                              content=[icon(tab_icon, 17, "mr-1"), node("span", text=label)])
                         for key, label, tab_icon, _body in tabs],
            ),
            node(
                "VWindow", model="tab",
                content=[node("VWindowItem", value=key, content=body) for key, _label, _icon, body in tabs],
            ),
        )
    ]
