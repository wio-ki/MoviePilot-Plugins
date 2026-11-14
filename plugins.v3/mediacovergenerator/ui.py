"""
插件界面：设置页（FormRender）与详情页（PageRender）的 Vuetify JSON。

这里只有纯函数，不依赖 MoviePilot，所需数据都由主类组装后传入。
封面本身色彩很满，界面就保持安静：没有横幅和卡片底，靠细线、留白和字重分层，只有一个点缀色（咔嗒眼睛的薄荷绿）。
颜色都从 Vuetify 的主题变量取，浅色、深色、玻璃主题下都能读。
"""
import json
from typing import Any, Dict, List, Optional

from app.plugins.mediacovergenerator.style.catalog import STYLE_CATALOG, STYLE_COUNT, style_info
from app.plugins.mediacovergenerator.ui_presets import TYPOGRAPHY_PRESETS

# Vuetify 组件自带主题类，会重置 CSS 变量，所以点缀色要用具体色值传给 color 属性。
# 这个绿在浅色和深色底上都够对比度
ACCENT = "#2FA97C"

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
  --k-ink: rgb(var(--v-theme-on-surface));
  --k-ink-2: rgba(var(--v-theme-on-surface), .68);
  --k-ink-3: rgba(var(--v-theme-on-surface), .48);
  --k-rule: rgba(var(--v-theme-on-surface), .12);
  --k-rule-2: rgba(var(--v-theme-on-surface), .24);
  --k-fill: rgba(var(--v-theme-on-surface), .045);
  --k-fill-2: rgba(var(--v-theme-on-surface), .09);
  --k-mint: #58DDA3;
  --k-mint-ink: #05281A;
  --k-accent: #2FA97C;
  --k-bad: #D9604A;
  --k-warn: #C98A1B;
  --k-mono: ui-monospace, "SF Mono", "Cascadia Mono", "JetBrains Mono", Menlo, Consolas, monospace;
  --v-theme-primary: 47, 169, 124;
  --v-theme-on-primary: 255, 255, 255;
  color: var(--k-ink); font-size: 14px; line-height: 1.5;
  font-feature-settings: "palt";
}
/* 文字和线条用的点缀色跟着主题走：深色底上偏亮，浅色底上偏深，两边都读得清 */
@supports (color: color-mix(in srgb, red, blue)) {
  .mcg { --k-accent: color-mix(in srgb, #58DDA3 50%, rgb(var(--v-theme-on-surface))); }
}
.mcg * { box-sizing: border-box; }
.mcg-radio { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }

/* 页头。窄屏：一行状态和操作，下面场记板。电脑端：左边状态、说明和操作，右边一排各媒体库现在的封面 */
.mcg-top { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 0 16px; padding-top: 8px; }
.mcg-top h2 { order: 1; display: flex; align-items: center; font-size: 24px; line-height: 1.2; font-weight: 700; letter-spacing: .01em; margin: 0 0 14px; font-variant-numeric: tabular-nums; }
.mcg-top-actions { order: 2; display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; }
.mcg-top .mcg-clap { order: 3; flex: 0 0 100%; }
.mcg-top .mcg-slate { order: 4; flex: 0 0 100%; }
.mcg-top-note { order: 5; flex: 0 0 100%; }
/* 看台：页头下方一整行等分的格子，每个媒体库一格：空心序号、库名、它自己的风格，底下一条线连成墙裙。
   挤不下的库折成第二行，每行的线各自连起来。没有封面图，只靠字和线分层；点一格就去给那个库换风格。窄屏不显示 */
.mcg-reel {
  display: none;
  /* 一行放几格由 --mcg-cols 决定，窄了就用更少的列；每格至少 84px，多出来的库折到下一行 */
  grid-template-columns: repeat(var(--mcg-cols, 10), minmax(84px, 1fr));
}
.mcg-frame {
  position: relative; min-width: 0; display: flex; flex-direction: column;
  padding: 0 14px 0; cursor: pointer;
}
.mcg-frame:first-child { padding-left: 0; }
.mcg-frame:last-child { padding-right: 0; }
/* 格与格之间的竖线，上面一段空着，和格子里第一行的字齐平 */
.mcg-frame + .mcg-frame::before {
  content: ""; position: absolute; left: 0; top: 4px; bottom: 18px; width: 1px; background: var(--k-rule);
}
/* 序号：空心字，笔画只有 1px，选中时描边变成点缀色 */
.mcg-frame-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 6px; }
.mcg-frame-no {
  font-family: var(--k-mono); font-size: 30px; line-height: 1; font-weight: 700; letter-spacing: -.03em;
  font-variant-numeric: tabular-nums; color: transparent; -webkit-text-stroke: 1px var(--k-rule-2);
  transition: -webkit-text-stroke-color .2s ease;
}
/* 格右上角的状态：生成过的是一个实心点，失败的偏红，正在生成的是一个转着的圈，没生成过只有一个空圈 */
.mcg-frame-flag { flex: none; width: 6px; height: 6px; margin-top: 6px; border-radius: 50%; box-shadow: inset 0 0 0 1px var(--k-rule-2); background: transparent; }
.mcg-frame[data-ok] .mcg-frame-flag { background: var(--k-mint); box-shadow: none; }
.mcg-frame[data-fail] .mcg-frame-flag { background: var(--k-bad); box-shadow: none; }
.mcg-frame[data-now] .mcg-frame-flag {
  width: 8px; height: 8px; margin-top: 5px; background: transparent; box-shadow: none;
  border: 1.5px solid var(--k-mint); border-right-color: transparent; animation: mcg-spin 1s linear infinite;
}
@keyframes mcg-spin { to { transform: rotate(360deg); } }
.mcg-frame-text { margin-top: 13px; font-size: 14px; font-weight: 700; line-height: 1.3; letter-spacing: .01em;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mcg-frame-sub { margin-top: 5px; font-family: var(--k-mono); font-size: 11px; line-height: 1.4; color: var(--k-ink-3);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
/* 格底一条线，连起来就是这一行的墙裙；还没生成过的库，那条线是虚的 */
.mcg-frame-rule { margin-top: auto; padding-top: 12px; }
.mcg-frame-rule span { display: block; height: 2px; background: var(--k-rule); transition: background-color .2s ease; }
.mcg-frame:hover .mcg-frame-rule span { background: var(--k-rule-2); }
.mcg-frame-on .mcg-frame-no { -webkit-text-stroke-color: var(--k-accent); }
.mcg-frame-on .mcg-frame-text { color: var(--k-accent); }
.mcg-frame-on .mcg-frame-rule span { background: var(--k-accent); }
/* 正在拍的那一格：线变成薄荷色，一道光从左往右扫 */
.mcg-frame[data-now] .mcg-frame-rule span { position: relative; overflow: hidden; background: var(--k-mint); }
.mcg-frame[data-now] .mcg-frame-rule span::after {
  content: ""; position: absolute; inset: 0; width: 45%;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, .75), transparent);
  animation: mcg-sweep 1.5s ease-in-out infinite;
}
@keyframes mcg-sweep { 0% { transform: translateX(-100%); } 100% { transform: translateX(320%); } }
/* 库里还没有封面：库名和风格淡一些，读起来是「待办」而不是「坏了」 */
.mcg-frame-empty .mcg-frame-text { color: var(--k-ink-2); font-weight: 600; }
/* 场记板：进度条下面是四个字段，细线分隔。电脑端收成一条，和进度条同高 */
.mcg-slate { display: grid; grid-template-columns: repeat(var(--mcg-n, 4), minmax(0, 1fr)); overflow: hidden; border-bottom: 1px solid var(--k-rule); }
/* 拍板条：空闲时是静止的斜纹，生成时薄荷色的部分就是进度 */
.mcg-clap {
  position: relative; height: 8px; border-radius: 2px; overflow: hidden;
  background: repeating-linear-gradient(-45deg, var(--k-rule-2) 0 6px, transparent 6px 12px);
}
.mcg-clap-fill { position: relative; height: 100%; overflow: hidden; border-radius: 2px; background: var(--k-mint); transition: width .4s ease; }
.mcg-clap-fill::before {
  content: ""; position: absolute; inset: 0 -34px 0 0;
  background: repeating-linear-gradient(-45deg, rgba(5,40,26,.34) 0 6px, transparent 6px 12px);
  animation: mcg-roll .9s linear infinite;
}
@keyframes mcg-roll { to { transform: translateX(-16.97px); } }
.mcg-field { padding: 12px 16px; min-width: 0; box-shadow: -1px 0 0 var(--k-rule), 0 -1px 0 var(--k-rule); }
.mcg-field:first-child { padding-left: 0; }
.mcg-field-label { font-size: 12px; color: var(--k-ink-3); letter-spacing: .06em; }
.mcg-field-value { font-size: 14px; font-weight: 600; margin-top: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-variant-numeric: tabular-nums; }
.mcg-dot { flex: none; width: 10px; height: 10px; border-radius: 50%; margin-right: 12px; background: var(--k-ink-3); }
.mcg-dot-ok { background: var(--k-mint); }
.mcg-dot-off { background: transparent; box-shadow: inset 0 0 0 2px var(--k-ink-3); }
.mcg-dot-run { background: var(--k-mint); animation: mcg-pulse 1.6s ease-out infinite; }
@keyframes mcg-pulse { 0% { box-shadow: 0 0 0 0 rgba(88,221,163,.55); } 100% { box-shadow: 0 0 0 8px rgba(88,221,163,0); } }
.mcg-top-note { font-size: 12px; color: var(--k-ink-2); padding-top: 8px; line-height: 1.6; }

/* 按钮。重复类名是为了压过主题里 html[data-theme] .v-btn.bg-primary 这类高优先级规则 */
.mcg .v-btn.mcg-btn.mcg-btn {
  text-transform: none; letter-spacing: 0; font-weight: 600; border-radius: 8px; box-shadow: none; backdrop-filter: none;
  transition: transform .15s ease, filter .15s ease, background-color .15s ease;
}
.mcg .v-btn.mcg-btn.mcg-btn-solid.mcg-btn-solid { background-color: var(--k-mint) !important; color: var(--k-mint-ink) !important; border-color: transparent !important; }
.mcg .v-btn.mcg-btn.mcg-btn-line.mcg-btn-line { background-color: transparent !important; color: var(--k-ink) !important; border: 1px solid var(--k-rule-2) !important; }
.mcg .v-btn.mcg-btn.mcg-btn-line.mcg-btn-line:hover { background-color: var(--k-fill) !important; }
.mcg .v-btn.mcg-btn.mcg-btn-quiet.mcg-btn-quiet { background-color: var(--k-fill-2) !important; color: var(--k-ink) !important; border-color: transparent !important; }
.mcg .v-btn.mcg-btn.mcg-btn-danger.mcg-btn-danger { background-color: transparent !important; color: var(--k-bad) !important; border: 1px solid rgba(217,96,74,.45) !important; }
.mcg .v-btn.mcg-btn.mcg-btn:hover { filter: brightness(1.06); }
.mcg .v-btn.mcg-btn.mcg-btn:active { transform: scale(.98); }
.mcg .v-btn.mcg-btn.mcg-btn:focus-visible { outline: 2px solid var(--k-accent); outline-offset: 2px; }

/* 页签：单选框 + CSS，切换无需请求后端 */
.mcg-tabs { display: flex; gap: 24px; margin: 16px 0 24px; border-bottom: 1px solid var(--k-rule); overflow-x: auto; scrollbar-width: none; }
.mcg-tabs::-webkit-scrollbar { display: none; }
.mcg-tab {
  display: flex; align-items: baseline; gap: 8px; padding: 12px 0; margin: 0 0 -1px; cursor: pointer; white-space: nowrap;
  font-size: 14px; color: var(--k-ink-2); border-bottom: 2px solid transparent; transition: color .2s ease, border-color .2s ease;
}
.mcg-tab:hover { color: var(--k-ink); }
.mcg-tab-n { font-family: var(--k-mono); font-size: 12px; color: var(--k-ink-3); }
.mcg-panel, .mcg-when { display: none; }
/* 弹窗默认垂直居中，页签内容一变高，页签栏就会上下跳。改成贴着顶部，切换时位置不动 */
.v-dialog > .v-overlay__content:has(.mcg) { align-self: flex-start; margin-top: 24px; }
/* 右下角的设置齿轮浮在内容上，底部留出位置，不挡最后一行的按钮 */
.mcg:not(.mcg-form) { container: mcg / inline-size; padding-bottom: 64px; }
#mcg-tab-style:checked ~ .mcg-tabs label[for="mcg-tab-style"],
#mcg-tab-libraries:checked ~ .mcg-tabs label[for="mcg-tab-libraries"],
#mcg-tab-history:checked ~ .mcg-tabs label[for="mcg-tab-history"],
#mcg-tab-maintain:checked ~ .mcg-tabs label[for="mcg-tab-maintain"] { color: var(--k-ink); font-weight: 600; border-bottom-color: var(--k-accent); }
#mcg-tab-style:checked ~ .mcg-panel-style,
#mcg-tab-libraries:checked ~ .mcg-panel-libraries,
#mcg-tab-history:checked ~ .mcg-panel-history,
#mcg-tab-maintain:checked ~ .mcg-panel-maintain { display: block; animation: mcg-fade .2s ease; }
#mcg-tab-style:checked ~ .mcg-top .mcg-when-style,
#mcg-tab-libraries:checked ~ .mcg-top .mcg-when-libraries,
#mcg-tab-history:checked ~ .mcg-top .mcg-when-history,
#mcg-tab-maintain:checked ~ .mcg-top .mcg-when-maintain { display: inline-flex; gap: 8px; flex-wrap: wrap; }
@keyframes mcg-fade { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }

/* 通用小部件 */
.mcg-bar { display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between; gap: 12px 16px; margin-bottom: 16px; }
.mcg-lead { flex: 1 1 280px; font-size: 14px; color: var(--k-ink-2); line-height: 1.6; max-width: 46rem; }
.mcg-hint {
  display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; justify-content: space-between;
  padding: 12px 16px; border-radius: 8px; background: var(--k-fill); font-size: 14px; line-height: 1.6; margin-bottom: 16px;
}
.mcg-hint-text { flex: 1 1 280px; min-width: 0; }
.mcg-hint-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.mcg-hint-foot { margin: 32px 0 0; }
/* 等待确认的操作：左边一道点缀色，和普通提示区分开 */
.mcg-hint-ask { background: rgba(88,221,163,.12); box-shadow: inset 2px 0 0 var(--k-accent); border-radius: 2px 8px 8px 2px; }
.mcg-notice {
  display: flex; gap: 12px; align-items: flex-start; font-size: 14px; line-height: 1.6;
  padding: 12px 16px; margin-top: 16px; border-radius: 2px 8px 8px 2px; background: rgba(201,138,27,.12); box-shadow: inset 2px 0 0 var(--k-warn);
}
.mcg-notice .v-icon { color: var(--k-warn); margin-top: 2px; }
.mcg-notice-info { background: var(--k-fill); box-shadow: inset 2px 0 0 var(--k-rule-2); }
.mcg-notice-info .v-icon { color: var(--k-ink-3); }
.mcg-empty { text-align: center; color: var(--k-ink-2); font-size: 14px; padding: 48px 16px; border-top: 1px solid var(--k-rule); border-bottom: 1px solid var(--k-rule); }
.mcg-empty .v-icon { color: var(--k-ink-3); margin-bottom: 8px; }
.mcg-chip { display: inline-flex; align-items: center; gap: 6px; height: 20px; padding: 0 8px; border-radius: 4px; font-size: 12px; line-height: 1; background: var(--k-fill-2); color: var(--k-ink-2); white-space: nowrap; }
.mcg-chip-accent { background: rgba(88,221,163,.16); color: var(--k-accent); font-weight: 600; }
.mcg-chip-ok::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: var(--k-mint); }
.mcg-chip-bad { background: rgba(217,96,74,.14); color: var(--k-bad); }
.mcg-chip-bad::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: var(--k-bad); }

/* 动态 / 静态 */
.mcg-seg { display: inline-flex; padding: 2px; border-radius: 8px; background: var(--k-fill-2); flex: none; }
.mcg-seg-item { display: inline-flex; align-items: center; height: 28px; padding: 0 12px; border-radius: 6px; font-size: 14px; color: var(--k-ink-2); cursor: pointer; white-space: nowrap; }
.mcg-seg-item:hover { color: var(--k-ink); }
.mcg-seg-on { background: rgb(var(--v-theme-surface)); color: var(--k-ink); font-weight: 600; box-shadow: 0 1px 2px rgba(0,0,0,.18); }
.mcg-rec { font-size: 12px; font-weight: 600; color: var(--k-accent); margin-left: 6px; white-space: nowrap; }

/* 选择要设置风格的媒体库 */
.mcg-targets { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; flex: 1 1 320px; min-width: 0; }
.mcg-targets-label { font-size: 12px; color: var(--k-ink-3); letter-spacing: .06em; margin-right: 4px; white-space: nowrap; }
.mcg-target {
  display: inline-flex; align-items: center; gap: 6px; flex: none; height: 32px; padding: 0 12px; border-radius: 16px; cursor: pointer;
  font-size: 14px; color: var(--k-ink-2); box-shadow: inset 0 0 0 1px var(--k-rule-2); max-width: 240px; white-space: nowrap;
  transition: color .2s ease, background-color .2s ease;
}
.mcg-target > span:first-child { overflow: hidden; text-overflow: ellipsis; }
.mcg-target:hover { color: var(--k-ink); background: var(--k-fill); }
.mcg-target-on, .mcg-target-on:hover { background: var(--k-mint); color: var(--k-mint-ink); font-weight: 600; box-shadow: none; }
.mcg-target-no { font-family: var(--k-mono); font-size: 12px; color: var(--k-accent); }
.mcg-target-on .mcg-target-no { color: var(--k-mint-ink); }

/* 风格画廊：图就是主角，没有卡片底，选中的那张外面一圈点缀色 */
.mcg-gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(232px, 1fr)); gap: 24px 16px; }
.mcg-form .mcg-gallery { grid-template-columns: repeat(auto-fill, minmax(184px, 1fr)); }
.mcg-form .mcg-gallery.mcg-gallery-three { grid-template-columns: repeat(auto-fill, minmax(232px, 1fr)); }
.mcg-style { display: block; min-width: 0; cursor: pointer; }
.mcg-style-media {
  position: relative; aspect-ratio: 16 / 9; border-radius: 8px; overflow: hidden; background: var(--k-fill-2);
  transition: transform .2s ease, box-shadow .2s ease;
}
.mcg-style-media img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; display: block; }
.mcg-style:hover .mcg-style-media { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,.24); }
.mcg-style:active .mcg-style-media { transform: scale(.985); }
.mcg-style-on .mcg-style-media { outline: 2px solid var(--k-accent); outline-offset: 3px; }
.mcg-flag {
  position: absolute; left: 8px; top: 8px; display: none; align-items: center; height: 20px; padding: 0 8px; border-radius: 4px;
  background: var(--k-mint); color: var(--k-mint-ink); font-size: 12px; line-height: 1; font-weight: 700;
}
.mcg-style-on .mcg-flag { display: inline-flex; }
.mcg-style-meta { padding-top: 12px; }
.mcg-style-name { display: flex; align-items: baseline; gap: 8px; font-size: 14px; font-weight: 600; }
.mcg-style-on .mcg-style-name { color: var(--k-accent); }
.mcg-no { font-family: var(--k-mono); font-size: 12px; font-weight: 500; color: var(--k-ink-3); font-variant-numeric: tabular-nums; }
.mcg-new { font-family: var(--k-mono); font-size: 12px; line-height: 1; font-weight: 700; letter-spacing: .06em; color: var(--k-accent); padding: 2px 4px; border-radius: 4px; box-shadow: inset 0 0 0 1px currentColor; align-self: center; }
.mcg-style-desc { font-size: 12px; color: var(--k-ink-2); margin-top: 4px; line-height: 1.5; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

/* 媒体库：一行一个，细线分隔 */
.mcg-libs { border-top: 1px solid var(--k-rule); }
.mcg-lib { display: flex; align-items: center; gap: 16px; padding: 12px 8px; border-bottom: 1px solid var(--k-rule); min-width: 0; transition: background-color .2s ease; }
.mcg-lib:hover { background: var(--k-fill); }
.mcg-lib-art { flex: none; width: 128px; aspect-ratio: 16 / 9; border-radius: 4px; object-fit: cover; background: var(--k-fill-2); display: flex; align-items: center; justify-content: center; color: var(--k-ink-3); }
.mcg-lib-info { flex: 1 1 0; min-width: 0; }
.mcg-lib-name { font-size: 14px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mcg-lib-note { font-size: 12px; color: var(--k-ink-2); margin-top: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mcg-lib-tags { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
.mcg-lib-actions { flex: none; display: flex; gap: 8px; }

/* 历史 */
.mcg-history { display: grid; grid-template-columns: repeat(auto-fill, minmax(208px, 1fr)); gap: 24px 16px; }
.mcg-cover { min-width: 0; }
.mcg-cover img { display: block; width: 100%; aspect-ratio: 16 / 9; object-fit: cover; border-radius: 8px; background: var(--k-fill-2); }
.mcg-cover-meta { display: flex; align-items: center; gap: 4px; padding-top: 8px; }
.mcg-cover-text { flex: 1; min-width: 0; }
.mcg-cover-name { font-size: 14px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mcg-cover-sub { font-size: 12px; color: var(--k-ink-2); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-variant-numeric: tabular-nums; }
.mcg-more { display: flex; justify-content: center; margin-top: 24px; }

/* 维护 */
.mcg-cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 32px; }
.mcg-sub { font-size: 14px; font-weight: 700; letter-spacing: .04em; padding-bottom: 12px; border-bottom: 1px solid var(--k-rule); }
.mcg-check { display: flex; gap: 12px; align-items: flex-start; font-size: 14px; padding: 12px 0; border-bottom: 1px solid var(--k-rule); }
.mcg-check-ok .v-icon { color: var(--k-accent); }
.mcg-check-bad .v-icon { color: var(--k-bad); }
.mcg-check-note { color: var(--k-ink-2); font-size: 12px; margin-top: 2px; word-break: break-all; }
.mcg-care { padding: 12px 0 16px; border-bottom: 1px solid var(--k-rule); }
.mcg-care-title { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; font-size: 14px; font-weight: 600; }
.mcg-care-size { font-family: var(--k-mono); font-size: 12px; font-weight: 500; color: var(--k-ink-2); }
.mcg-care-note { font-size: 12px; color: var(--k-ink-2); margin: 4px 0 12px; line-height: 1.6; }
.mcg-confirm { display: flex; flex-wrap: wrap; gap: 8px; }

/* 插件宠物的加载器：看不见的 iframe，宠物本身由它放到外层页面里 */
.mcg-pet-frame { position: absolute; width: 0; height: 0; border: 0; visibility: hidden; pointer-events: none; }

/* 设置页：分节不用卡片，靠一条细线和留白 */
.mcg-form .v-tabs { margin-bottom: 8px; border-bottom: 1px solid var(--k-rule); }
.mcg-form .v-tab { text-transform: none; letter-spacing: 0; }
.mcg-section { padding: 24px 0; border-top: 1px solid var(--k-rule); }
.mcg-section:first-child { border-top: 0; padding-top: 16px; }
.mcg-notice + .mcg-section { border-top: 0; }
.mcg-form .mcg-notice { margin: 16px 0 0; }
.mcg-section-head { margin-bottom: 16px; }
.mcg-section-title { font-size: 16px; font-weight: 700; }
.mcg-section-note { font-size: 12px; color: var(--k-ink-2); margin-top: 4px; line-height: 1.6; max-width: 46rem; }
.mcg-editor { border-radius: 8px; overflow: hidden; border: 1px solid var(--k-rule); }
.mcg-editor-bar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.mcg-toggle-label { font-size: 12px; color: var(--k-ink-2); margin-bottom: 8px; }

/* 电脑端。弹窗宽度和窗口宽度无关，所以按容器宽度判断。有媒体库时页头分两栏；没有时保持上面那一行 */
@container mcg (min-width: 760px) {
  /* 看台占满页头整行：上面一行是状态和操作，下面整面墙，再下是场记板和字段 */
  .mcg-top-rail {
    display: grid; grid-template-columns: minmax(0, 1fr) auto;
    grid-template-areas: "status actions" "note note" "rail rail" "clap clap" "slate slate"; gap: 0 24px; align-items: start;
  }
  .mcg-top-rail h2 { grid-area: status; font-size: 28px; margin: 0; }
  .mcg-top-rail .mcg-top-note { grid-area: note; padding-top: 8px; }
  .mcg-top-rail .mcg-top-actions { grid-area: actions; align-self: center; margin: 0; }
  .mcg-top-rail .mcg-clap { grid-area: clap; margin-top: 26px; }
  .mcg-top-rail .mcg-slate { grid-area: slate; }
  /* 看台占满页头整行：上面一行是状态和操作，下面整行格子，再下是场记板和字段 */
  .mcg-reel { grid-area: rail; display: grid; margin: 14px 0 2px; }
  .mcg-reel .mcg-frame { padding-bottom: 2px; }
  .mcg-reel .mcg-frame-no { font-size: 32px; }
  .mcg-reel .mcg-frame-text { font-size: 14px; }
  .mcg-field { padding: 10px 16px; }
  .mcg-field-label { font-size: 12px; }
  .mcg-field-value { margin-top: 2px; }
}
@property --mcg-fade-l { syntax: "<length>"; inherits: false; initial-value: 0px; }
@property --mcg-fade-r { syntax: "<length>"; inherits: false; initial-value: 0px; }
@keyframes mcg-fade-l { from { --mcg-fade-l: 0px; } to { --mcg-fade-l: 56px; } }
@keyframes mcg-fade-r { from { --mcg-fade-r: 56px; } to { --mcg-fade-r: 0px; } }

@media (max-width: 600px) {
  .mcg-top h2 { font-size: 20px; }
  .mcg-slate { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .mcg-field:nth-child(odd) { padding-left: 0; }
  .mcg-field:nth-child(even) { padding-right: 0; }
  /* 字段是单数时，最后一个占满一行 */
  .mcg-field:last-child:nth-child(odd) { grid-column: 1 / -1; }
  .mcg-tabs { gap: 20px; margin: 12px 0 16px; }
  /* 手机上两列：一屏能比较更多款，图也不会被拉大到发糊 */
  .mcg-gallery, .mcg-form .mcg-gallery, .mcg-form .mcg-gallery.mcg-gallery-three, .mcg-history { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px 12px; }
  .mcg-targets { flex-wrap: nowrap; overflow-x: auto; flex-basis: 100%; scrollbar-width: none; padding: 2px 0; }
  .mcg-targets::-webkit-scrollbar { display: none; }
  .mcg-lib { gap: 12px; padding: 12px 0; }
  .mcg-lib-art { width: 96px; }
  .mcg-lib-actions { flex-direction: column; }
  .mcg-style-desc { -webkit-line-clamp: 3; }
}
@media (prefers-reduced-motion: reduce) {
  .mcg *, .mcg *::before, .mcg *::after { transition: none !important; animation: none !important; }
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


def span(cls: Optional[str] = None, text: Optional[str] = None, **props) -> Dict[str, Any]:
    return node("span", cls=cls, text=text, **props)


def b(text: str, cls: Optional[str] = None) -> Dict[str, Any]:
    return node("b", cls=cls, text=text)


def icon(name: str, size: int = 18, cls: Optional[str] = None) -> Dict[str, Any]:
    # 渲染器总会传默认插槽，VIcon 有插槽时忽略 icon 属性，所以图标名要放在 text 里
    return node("VIcon", cls=cls, text=name, size=size)


def post(api_prefix: str, path: str) -> Dict[str, Any]:
    return {"click": {"api": f"{api_prefix}/{path}", "method": "post"}}


def button(label: str, kind: str, api_prefix: str, path: str, prepend: Optional[str] = None,
           size: str = "default", mark: str = "") -> Dict[str, Any]:
    props: Dict[str, Any] = {"variant": "flat", "size": size}
    if prepend:
        props["prepend-icon"] = prepend
    # mark 是给咔嗒认按钮用的类名，不带样式
    return node("VBtn", cls=f"mcg-btn mcg-btn-{kind}" + (f" {mark}" if mark else ""), text=label,
                events=post(api_prefix, path), **props)


def style_label(index: int, variant: str) -> str:
    return f"{style_info(index).name} · {'动态' if variant == 'animated' else '静态'}"


def numbered_label(index: int, variant: str) -> str:
    return f"{index:02d} {style_label(index, variant)}"


def recommend_badge() -> Dict[str, Any]:
    return span("mcg-rec", "推荐")


def style_name(entry) -> List[dict]:
    """卡片上的风格名：序号、名字，新增的风格带一个标记。"""
    children = [span("mcg-no", f"{entry.index:02d}"), span(None, entry.name)]
    if entry.index >= 9:
        children.append(span("mcg-new", "NEW"))
    return children


def style_gallery(card_of) -> Dict[str, Any]:
    """全部风格按序号排成一个画廊。card_of(entry) 返回一张卡片。"""
    return div("mcg-gallery", *[card_of(entry) for entry in sorted(STYLE_CATALOG, key=lambda entry: entry.index)])


def _preview_media(entry, show_motion: Any, asset_base: str, local: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    风格卡片的预览图。静态图是 640 像素宽的文件，手机的高密度屏上也不发糊；
    动态预览叠在上面，滚动到附近才加载，加载不出来时看到的仍是静态图。local 是用媒体库生成的预览。
    """
    still = local["still"] if local else f"{asset_base}style_{entry.index}.webp"
    motion = local["gif"] if local else f"{asset_base}style_{entry.index}_motion.webp"
    children = [node("img", src=still, alt=entry.name, loading="lazy", decoding="async")]
    if show_motion and motion:
        moving = node("img", cls="mcg-style-motion", src=motion, alt="", loading="lazy", decoding="async")
        # 渲染器处理 show 时会改写 style.display，条件放在单独的外层节点上，这个节点不能再带字符串 style
        children.append(div(None, moving, show=show_motion) if isinstance(show_motion, str) else moving)
    children.append(span("mcg-flag", "使用中"))
    return div("mcg-style-media", *children)


# ---------------------------------------------------------------- 详情页


def _task_library(task: Dict[str, Any]) -> str:
    """正在处理的媒体库名。任务里记的是「服务器：媒体库」，入库触发时后面还带一个说明。"""
    return str(task.get("current") or "").split("：", 1)[-1].replace("（入库更新）", "").strip()


def _reel(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    页头的看台：一整行等分的格子，每个媒体库一格。格子里只有字和线，没有封面图：
    一个空心的大号序号、右上角一个表示上次结果的小点、库名和它自己的风格，格底一条线连成墙裙。
    点一格就去给那个库换风格。类名沿用 mcg-reel / mcg-frame，咔嗒靠它们找到这一排和它站上去的沿。
    """
    libraries = ctx["libraries"]
    if not libraries:
        return None
    prefix = ctx["api_prefix"]
    current = _task_library(ctx["task"]) if ctx["task"].get("running") else ""
    frames = []
    for position, library in enumerate(libraries, 1):
        name = library["name"]
        status = library.get("status") or {}
        props: Dict[str, Any] = {"title": f"{name}：点这里给它换风格", "data-lib": name}
        if current and name == current:
            props["data-now"] = ""
        elif status:
            props["data-ok" if status.get("ok") else "data-fail"] = ""
        # 单独设过风格就写出来，跟随默认时写「跟随默认」
        note = (numbered_label(library["own_index"], library["own_variant"])
                if library.get("own_style") else "跟随默认")
        cls = "mcg-frame"
        if not library.get("thumb") and not status:
            cls += " mcg-frame-empty"
            note = "等待第一次生成"
        if library["key"] == ctx.get("style_target"):
            cls += " mcg-frame-on"
        frames.append(node("span", cls=cls, events=post(prefix, f"style_target?library={library['encoded_key']}&tab=style"),
                           content=[
                               div("mcg-frame-head", span("mcg-frame-no", f"{position:02d}"), span("mcg-frame-flag")),
                               div("mcg-frame-text", text=name, title=name),
                               div("mcg-frame-sub", text=note, title=note),
                               div("mcg-frame-rule", span(None)),
                           ], **props))
    # 行内直接给出列数：库里少时格子不撑满一行，多了才会折到第二行
    return div("mcg-reel", *frames, style=f"--mcg-cols:{min(len(libraries), 10)}")


def _top(ctx: Dict[str, Any]) -> Dict[str, Any]:
    task = ctx["task"]
    prefix = ctx["api_prefix"]
    running = bool(task.get("running"))

    action_groups = []
    for tab, _name, _icon in PAGE_TABS:
        # 同一组按钮按当前页签各渲染一份，操作后刷新页面时能留在原页签
        if running:
            actions = [
                button("刷新进度", "line", prefix, f"refresh?tab={tab}", "mdi-refresh", mark="mcg-act-refresh"),
                button("停止", "danger", prefix, f"stop_task?tab={tab}", "mdi-stop"),
            ]
        else:
            actions = [
                button("刷新", "line", prefix, f"refresh?tab={tab}", "mdi-refresh", mark="mcg-act-refresh"),
                button("生成全部封面", "solid", prefix, f"generate_now?tab={tab}", "mdi-play", mark="mcg-act-go"),
            ]
        action_groups.append(div(f"mcg-when mcg-when-{tab}", *actions))

    total = max(1, int(task.get("total") or 1))
    done = min(total, int(task.get("done") or 0))
    # 弹窗的标题栏已经写着插件名，页头不重复，最大的字留给当前状态
    if running:
        status = [span("mcg-dot mcg-dot-run"),
                  span("mcg-status-text", f"正在生成 {done}/{total}" if task.get("total") else "正在生成")]
    elif not ctx["enabled"]:
        status = [span("mcg-dot mcg-dot-off"), span("mcg-status-text", "未启用")]
    else:
        status = [span("mcg-dot mcg-dot-ok"), span("mcg-status-text", "空闲")]

    libraries = ctx["libraries"]
    own_styles = sum(1 for library in libraries if library.get("own_style"))
    library_value = "—"
    if libraries:
        library_value = f"{ctx['library_active']} / {len(libraries)}" + (f" · {own_styles} 个单独设置" if own_styles else "")
    fields = [
        ("默认风格", numbered_label(ctx["style_index"], ctx["style_variant"])),
        ("媒体库", library_value),
        ("上次运行", ctx.get("last_run_text") or "尚未运行"),
        ("定时轮换", ("每次定时更新换下一款" if ctx.get("cron") else "已打开，未设置定时更新") if ctx.get("rotate_style")
         else "未开启"),
    ]

    def field(label, value):
        return div("mcg-field", div("mcg-field-label", text=label), div("mcg-field-value", text=value, title=value))

    note = None
    if running:
        current = _task_library(task)
        text = f"正在处理：{current}" if current else "正在准备…"
        # 咔嗒开着时，这一行、上面的进度和数字都由它实时更新，不用手动刷新
        note = div("mcg-top-note mcg-top-now", text=f"{text}（可以关掉窗口，后台继续）")
    elif ctx.get("last_result"):
        note = div("mcg-top-note", text=ctx["last_result"])
    elif ctx["enabled"] and libraries and not ctx["warnings"] and not ctx.get("last_run_text"):
        # 保存设置不会自动生成，第一次要手动点一下，否则看起来像插件没反应
        note = div("mcg-top-note mcg-top-first",
                   text="设置已生效。点「生成全部封面」开始第一次生成，之后会自动更新。")

    # 顺序是电脑端左栏从上到下的阅读顺序；窄屏用 order 把说明放回场记板下面
    reel = _reel(ctx)
    children: List[dict] = [node("h2", content=status)]
    if note:
        children.append(note)
    children.append(div("mcg-top-actions", *action_groups))
    if reel:
        children.append(reel)
    if running:
        percent = int(min(100, done * 100 / total)) if task.get("total") else 0
        children.append(div("mcg-clap", div("mcg-clap-fill", style=f"width:{max(percent, 3)}%")))
    else:
        children.append(div("mcg-clap"))
    children.append(div("mcg-slate", *[field(*item) for item in fields], style=f"--mcg-n:{len(fields)}"))
    return div("mcg-top mcg-top-rail" if reel else "mcg-top", *children)


BULK_ARMED = ("reset_styles", "assign_styles", "assign_generate")


def _bulk_confirm(ctx: Dict[str, Any], tab: str) -> List[dict]:
    """批量改媒体库风格前的确认条。第一次点只是询问，这里说明后果并给出确认按钮。"""
    armed = ctx.get("clean_armed")
    if armed not in BULK_ARMED:
        return []
    prefix = ctx["api_prefix"]
    if armed == "reset_styles":
        own = sum(1 for library in ctx["libraries"] if library.get("own_style"))
        default_label = numbered_label(ctx["style_index"], ctx["style_variant"])
        note = (f"让 {own} 个单独设置的媒体库全部恢复跟随默认风格「{default_label}」？" if own
                else "现在没有单独设置风格的媒体库。")
        note += "已经生成的封面不会变，下次生成时生效。"
        confirm = button("确认，全部恢复默认", "solid", prefix, f"reset_library_styles?tab={tab}", size="small")
    else:
        active = ctx["library_active"]
        note = (f"把参与批量更新的 {active} 个媒体库，按「媒体库」页的顺序依次设为动态风格 01 到 {STYLE_COUNT:02d}"
                + (f"，第 {STYLE_COUNT + 1} 个起从 01 再排" if active > STYLE_COUNT else "")
                + "。它们现有的单独设置会被覆盖")
        if armed == "assign_generate":
            note += "，然后立即生成全部封面。"
            confirm = button("确认，分配并生成", "solid", prefix, f"assign_styles?generate=1&tab={tab}", size="small")
        else:
            note += "，下次生成时生效。"
            confirm = button("确认分配", "solid", prefix, f"assign_styles?generate=0&tab={tab}", size="small")
    return [div("mcg-hint mcg-hint-ask", div("mcg-hint-text", text=note),
                div("mcg-hint-actions", confirm, button("取消", "line", prefix, f"refresh?tab={tab}", size="small")))]


def _style_targets(ctx: Dict[str, Any], target: Optional[Dict[str, Any]]) -> List[dict]:
    """选择风格应用到哪里：所有媒体库（默认风格），或者某一个媒体库。"""
    libraries = ctx["libraries"]
    if not libraries:
        return []
    prefix = ctx["api_prefix"]
    chips = [
        span("mcg-targets-label", "应用到"),
        node("span", cls="mcg-target" + (" mcg-target-on" if not target else ""), content=[span(None, "所有媒体库")],
             events=post(prefix, "style_target?tab=style")),
    ]
    for library in libraries:
        content = [span(None, library["name"])]
        if library.get("own_style"):
            # 单独设置过的媒体库直接标出它用的是第几款
            content.append(span("mcg-target-no", f"{library['own_index']:02d}", title="已单独设置风格"))
        selected = target is not None and library["key"] == target["key"]
        chips.append(node("span", cls="mcg-target" + (" mcg-target-on" if selected else ""), content=content,
                          title=library["name"],
                          events=post(prefix, f"style_target?library={library['encoded_key']}&tab=style")))
    return chips


def _target_note(ctx: Dict[str, Any], target: Optional[Dict[str, Any]]) -> List[dict]:
    if not target:
        return []
    prefix = ctx["api_prefix"]
    if target.get("own_style"):
        label = numbered_label(target["own_index"], target["own_variant"])
        note = f"「{target['name']}」单独使用「{label}」，不跟随默认风格。"
        actions = [button("恢复跟随默认", "line", prefix,
                          f"clear_library_style?library={target['encoded_key']}&tab=style", size="small")]
    else:
        label = numbered_label(ctx["style_index"], ctx["style_variant"])
        note = f"「{target['name']}」现在跟随默认风格「{label}」。点下面的图可以给它单独设置。"
        actions = []
    if not ctx["task"].get("running"):
        actions.append(button("生成这个媒体库", "solid", prefix,
                              f"generate_now?library={target['encoded_key']}&tab=style", size="small"))
    return [div("mcg-hint", div("mcg-hint-text", text=note), div("mcg-hint-actions", *actions))]


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
        if local.get("stale"):
            note = f"下面是「{local['library']}」在 {local['time']} 生成的预览，风格在那之后更新过，重新生成才是现在的效果。"
        actions = [button("看示例图", "line", prefix, "clear_preview?tab=style", size="small"),
                   button(f"用「{source['name']}」重新生成", "quiet", prefix, path, size="small")]
    else:
        note = "上面是示例图。想看自己的片库用上这些风格的样子，可以生成一份预览，不会改动媒体库封面。"
        actions = [button(f"用「{source['name']}」生成预览", "quiet", prefix, path, "mdi-image-multiple-outline", size="small")]
    if ctx["task"].get("running"):
        actions = []
    return [div("mcg-hint" + ("" if local else " mcg-hint-foot"), div("mcg-hint-text", text=note),
                div("mcg-hint-actions", *actions))]


def _style_panel(ctx: Dict[str, Any]) -> Dict[str, Any]:
    prefix = ctx["api_prefix"]
    target = next((item for item in ctx["libraries"] if item["key"] == ctx["style_target"]), None)
    if target and target.get("own_style"):
        variant, style_index = target["own_variant"], target["own_index"]
    else:
        variant, style_index = ctx["style_variant"], ctx["style_index"]
    target_query = f"&library={target['encoded_key']}" if target else ""
    segment = div(
        "mcg-seg",
        node("span", cls="mcg-seg-item" + (" mcg-seg-on" if variant == "animated" else ""),
             content=[span(None, "动态"), recommend_badge()],
             events=post(prefix, f"set_style_variant?variant=animated&tab=style{target_query}")),
        node("span", cls="mcg-seg-item" + (" mcg-seg-on" if variant == "static" else ""), content=[span(None, "静态")],
             events=post(prefix, f"set_style_variant?variant=static&tab=style{target_query}")),
    )
    children: List[dict] = _bulk_confirm(ctx, "style")
    children.append(div("mcg-bar", div("mcg-targets", *_style_targets(ctx, target)), segment))
    children.extend(_target_note(ctx, target))
    # 正在看自己片库的预览时，说明放在图的上面；看示例图时，「生成预览」是次要的，放到画廊下面
    local = ctx.get("local_preview")
    if local:
        children.extend(_local_preview_bar(ctx, target))
    local_styles = (local or {}).get("styles") or {}

    def card(entry):
        selected = entry.index == style_index
        motion_note = entry.motion if variant == "animated" else entry.desc
        return div(
            "mcg-style" + (" mcg-style-on" if selected else ""),
            _preview_media(entry, variant == "animated", ctx["asset_base"], local_styles.get(entry.index)),
            div("mcg-style-meta", div("mcg-style-name", *style_name(entry)), div("mcg-style-desc", text=motion_note)),
            events=post(prefix, f"select_style?index={entry.index}&tab=style{target_query}"),
        )

    children.append(style_gallery(card))
    if not local:
        children.extend(_local_preview_bar(ctx, target))
    return div("mcg-panel mcg-panel-style", *children)


def _libraries_panel(ctx: Dict[str, Any]) -> Dict[str, Any]:
    prefix = ctx["api_prefix"]
    libraries = ctx["libraries"]
    if not libraries:
        return div("mcg-panel mcg-panel-libraries",
                   div("mcg-empty", icon("mdi-folder-off-outline", 32),
                       div(None, text="还没有可用的媒体库。请在设置中选择媒体服务器并保存。")))

    own = sum(1 for library in libraries if library.get("own_style"))
    default_label = numbered_label(ctx["style_index"], ctx["style_variant"])
    lead = (f"{own} 个媒体库单独设置了风格，其余跟随默认风格「{default_label}」。" if own
            else f"所有媒体库都跟随默认风格「{default_label}」。")
    if ctx.get("rotate_style"):
        lead += ("定时轮换已开启：每次定时更新，默认风格和单独设置的媒体库各换下一款。" if ctx.get("cron")
                 else "定时轮换已打开，但还没有设置定时更新，所以不会轮换。")
    running = bool(ctx["task"].get("running"))
    bulk = []
    if ctx.get("clean_armed") not in BULK_ARMED:
        if own:
            bulk.append(button("全部恢复默认", "line", prefix, "reset_library_styles?tab=libraries", "mdi-restore", "small"))
        bulk.append(button(f"依次分配 01–{STYLE_COUNT:02d}", "line", prefix, "assign_styles?generate=0&tab=libraries",
                           "mdi-format-list-numbered", "small"))
        if not running:
            bulk.append(button("分配并生成", "quiet", prefix, "assign_styles?generate=1&tab=libraries",
                               "mdi-play", "small"))
    children: List[dict] = [div("mcg-bar", div("mcg-lead", text=lead), div("mcg-hint-actions", *bulk))]
    children.extend(_bulk_confirm(ctx, "libraries"))

    rows = []
    for library in libraries:
        if library.get("own_style"):
            tags = [span("mcg-chip mcg-chip-accent", numbered_label(library["own_index"], library["own_variant"]))]
        else:
            tags = [span("mcg-chip", "跟随默认")]
        status = library.get("status") or {}
        if status:
            tags.append(span("mcg-chip " + ("mcg-chip-ok" if status.get("ok") else "mcg-chip-bad"),
                             "成功" if status.get("ok") else "失败"))
        if not library.get("active"):
            tags.append(span("mcg-chip", "不参与批量更新"))
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
        actions = [button("换风格", "line", prefix, f"style_target?library={library['encoded_key']}&tab=style",
                          size="small")]
        if not running:
            actions.append(button("生成", "quiet", prefix,
                                  f"generate_now?library={library['encoded_key']}&tab=libraries", size="small"))
        rows.append(div(
            "mcg-lib", art,
            div("mcg-lib-info", div("mcg-lib-name", text=library["name"], title=library["name"]),
                div("mcg-lib-note", text=note, title=status.get("message") or note), div("mcg-lib-tags", *tags)),
            div("mcg-lib-actions", *actions),
        ))
    children.append(div("mcg-libs", *rows))
    return div("mcg-panel mcg-panel-libraries", *children)


def _history_panel(ctx: Dict[str, Any]) -> Dict[str, Any]:
    prefix = ctx["api_prefix"]
    items = ctx["history"]
    total = ctx["history_total"]
    ready = ctx["page_tab"] == "history" or bool(items)
    children: List[dict] = [
        div("mcg-bar", div("mcg-lead", text=f"每个媒体库最多保留 {ctx['history_limit']} 张，可以在设置的「目录与历史」里调整。")),
    ]
    if not items:
        note = "还没有生成记录。生成一次封面后会出现在这里。" if ready or not total else "正在载入…"
        children.append(div("mcg-empty", icon("mdi-image-multiple-outline", 32), div(None, text=note)))
        return div("mcg-panel mcg-panel-history", *children)

    cards = []
    for item in items:
        cards.append(div(
            "mcg-cover",
            node("img", src=item["src"], alt=item["title"], loading="lazy", decoding="async"),
            div(
                "mcg-cover-meta",
                div("mcg-cover-text", div("mcg-cover-name", text=item["title"], title=item["title"]),
                    div("mcg-cover-sub", text=f"{item['mtime']} · {item['size']}")),
                node("VBtn", icon="mdi-delete-outline", variant="text", size="small", title="删除", color="#D9604A",
                     events=post(prefix, f"delete_saved_cover?file={item['encoded_name']}&tab=history")),
            ),
        ))
    children.append(div("mcg-history", *cards))
    if total > len(items):
        children.append(div("mcg-more", button(f"显示更多（还有 {total - len(items)} 张）", "line", prefix,
                                               "history_more?tab=history")))
    return div("mcg-panel mcg-panel-history", *children)


def _clean_actions(ctx: Dict[str, Any], what: str, path: str, prepend: str) -> List[dict]:
    """清理按钮。删除不能撤销，第一次点只是询问。"""
    prefix = ctx["api_prefix"]
    if ctx.get("clean_armed") != what:
        return [button("清理", "danger", prefix, path, prepend, "small")]
    return [div(
        "mcg-confirm",
        button("确认清理，不能恢复", "danger", prefix, path, prepend, "small"),
        button("取消", "line", prefix, "refresh?tab=maintain", size="small"),
    )]


def _maintain_panel(ctx: Dict[str, Any]) -> Dict[str, Any]:
    checks = []
    for check in ctx["checks"]:
        state = "ok" if check["ok"] else "bad"
        mark = "mdi-check" if check["ok"] else "mdi-alert-circle-outline"
        body = [div(None, text=check["title"])]
        if check.get("note"):
            body.append(div("mcg-check-note", text=check["note"]))
        checks.append(div(f"mcg-check mcg-check-{state}", icon(mark), div(None, *body)))

    def care(title, size, note, actions):
        return div("mcg-care", div("mcg-care-title", span(None, title), span("mcg-care-size", size)),
                   div("mcg-care-note", text=note), *actions)

    return div(
        "mcg-panel mcg-panel-maintain",
        div(
            "mcg-cols",
            div(None, div("mcg-sub", text="运行检查"), *checks),
            div(
                None,
                div("mcg-sub", text="缓存"),
                care("封面与素材", ctx["cache_images"],
                     "删除下载的素材和历史封面。媒体库里已经生效的封面不受影响；自定义保存目录中只会删除插件生成的文件。",
                     _clean_actions(ctx, "images", "clean_images?tab=maintain", "mdi-image-remove-outline")),
                care("字体", ctx["cache_fonts"], "字体损坏或想重新下载时使用，下次生成时自动重新获取。",
                     _clean_actions(ctx, "fonts", "clean_fonts?tab=maintain", "mdi-format-font")),
            ),
        ),
    )


def build_page(ctx: Dict[str, Any]) -> List[dict]:
    active_tab = ctx["page_tab"]
    counts = {"libraries": len(ctx["libraries"]), "history": ctx["history_total"]}
    radios = [
        node("input", cls="mcg-radio", type="radio", name="mcg-tab", id=f"mcg-tab-{tab}", checked=tab == active_tab)
        for tab, _name, _icon in PAGE_TABS
    ]

    def tab_label(tab, name):
        content = [span(None, name)]
        if counts.get(tab):
            content.append(span("mcg-tab-n", str(counts[tab])))
        # 不支持按需加载图片的旧版本里，历史图片只在切到历史页时才请求一次
        lazy = tab == "history" and active_tab != "history" and ctx["history_total"] and not ctx["history"]
        return node("label", cls="mcg-tab", content=content,
                    events=post(ctx["api_prefix"], "refresh?tab=history") if lazy else None,
                    **{"for": f"mcg-tab-{tab}"})

    return [
        div(
            "mcg",
            node("style", text=CSS),
            *radios,
            _top(ctx),
            # 提示放在页签外面，停在哪个页签都看得到
            *([div("mcg-notice", icon("mdi-alert-outline"), div(None, text="；".join(ctx["warnings"])))]
              if ctx["warnings"] else []),
            div("mcg-tabs", *[tab_label(tab, name) for tab, name, _icon in PAGE_TABS]),
            _style_panel(ctx),
            _libraries_panel(ctx),
            _history_panel(ctx),
            _maintain_panel(ctx),
            *([node("iframe", cls="mcg-pet-frame", src=ctx["pet"]["url"], title="咔嗒", tabindex="-1",
                    **{"aria-hidden": "true", "data-state": json.dumps(ctx["pet"]["state"], ensure_ascii=False)})]
              if ctx.get("pet") else []),
        )
    ]


# ---------------------------------------------------------------- 设置页


def _section(mark: str, title: str, note: str, *children, show: Optional[str] = None) -> Dict[str, Any]:
    props: Dict[str, Any] = {}
    if show:
        props["show"] = show
    # mark 是以前分节标题前的图标，新版面不用图标装饰，参数留着免得改动所有调用处
    return div(
        "mcg-section",
        div("mcg-section-head", div("mcg-section-title", text=title), div("mcg-section-note", text=note)),
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


def _style_picker(asset_base: str) -> Dict[str, Any]:
    def card(entry):
        value = f"static_{entry.index}"
        return (
            node(
                "div",
                content=[
                    _preview_media(entry, "{{ cover_style_variant === 'animated' }}", asset_base),
                    div(
                        "mcg-style-meta",
                        div("mcg-style-name", *style_name(entry)),
                        div("mcg-style-desc", text=entry.motion,
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
    return style_gallery(card)


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
                             "每次定时更新，默认风格换下一款；单独设置过风格的媒体库也各自换下一款，12 款轮完回到第 1 款"), 8),
                _col(_switch("pet_enabled", "插件宠物「咔嗒」",
                             "一块会走路的场记板。生成时替你盯进度、拍完自动刷新页面；摸它、喂它爆米花、把它放到页头那排封面上都有反应，右键它有菜单"), 4),
            ),
        ),
        _section(
            "mdi-server-network", "媒体服务器", "选择要更新封面的服务器和媒体库",
            _row(
                _col(_select("selected_servers", "媒体服务器", ctx["server_items"], multiple=True, chips=True,
                             clearable=True, hint=ctx.get("server_hint") or "支持 Emby、Jellyfin 和极影视", icon_name="mdi-server"), 6),
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


def _style_tab(ctx: Dict[str, Any]) -> List[dict]:
    return [
        _section(
            "mdi-palette-swatch-outline", "封面风格",
            "这里是所有媒体库的默认风格。想给某个媒体库单独设置、让各媒体库依次用 01 到 10，或一键全部恢复默认，都在插件详情页里",
            div(None, _toggle("cover_style_variant", "输出类型",
                              [("animated", "GIF 动态封面"), ("static", "静态图片")], recommended="animated"),
                style="margin-bottom:16px;max-width:380px;"),
            _style_picker(ctx["asset_base"]),
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
            "mdi-palette-outline", "点缀色", "标题线条、胶囊标签等处使用的颜色。默认每款风格有自己的颜色；海报系列默认跟着每张海报的主色走",
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


def _typography_picker(asset_base: str) -> Dict[str, Any]:
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
                    div("mcg-style-media",
                        node("img", src=f"{asset_base}type_{preset['key']}.webp", alt=preset["name"], loading="lazy",
                             decoding="async"),
                        span("mcg-flag", "使用中")),
                    div(
                        "mcg-style-meta",
                        div("mcg-style-name", span(None, preset["name"]),
                            *([recommend_badge()] if preset.get("recommended") else [])),
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
            _typography_picker(ctx["asset_base"]),
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
            "mcg-notice mcg-notice-info", icon("mdi-information-outline"),
            div(None, text="Hills、yamby、小幻影视都能播放。客户端请求的宽度小于动图宽度时，Emby 会把它压成静态图，所以尺寸越小越容易动起来。首帧就是完整封面，只显示静态图的客户端也能看到成品。需要容器内有 ffmpeg，没有时自动生成静态封面。"),
        ),
        _section(
            "mdi-play-box-multiple-outline", "GIF 动图", "只有使用动态封面时才生效。画面大部分时间静止、只在转场时运动，320×180 一张通常 1.5 到 3.5 MB",
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
        "pet_enabled": True,
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
        ("style-tab", "风格", "mdi-palette-swatch-outline", _style_tab(ctx)),
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
                content=[node("VTab", value=key, cls="text-none", content=[node("span", text=label)])
                         for key, label, _icon, _body in tabs],
            ),
            node(
                "VWindow", model="tab",
                content=[node("VWindowItem", value=key, content=body) for key, _label, _icon, body in tabs],
            ),
        )
    ]
