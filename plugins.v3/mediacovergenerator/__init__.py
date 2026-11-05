import base64
import datetime
import hashlib
import json
import mimetypes
import os
import re
import threading
import time
import shutil
import random
from collections import OrderedDict, defaultdict
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse, quote
from typing import Any, Dict, List, Optional, Tuple
import pytz
import yaml

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from PIL import Image, ImageFont

from app import schemas
from app.chain.mediaserver import MediaServerChain
from app.plugins import _PluginBase
from app.schemas.types import EventType, NotificationType

try:
    # MoviePilot V3
    from app.sdk.config import settings
    from app.sdk.events import eventmanager, Event
    from app.sdk.services import MediaServerHelper
    from app.sdk.logging import logger
    from app.sdk.media import MediaInfo
    from app.sdk.network import RequestUtils, UrlUtils
except ImportError:
    # MoviePilot V2
    from app.core.config import settings
    from app.core.event import eventmanager, Event
    from app.helper.mediaserver import MediaServerHelper
    from app.log import logger
    from app.schemas import MediaInfo
    from app.utils.http import RequestUtils
    from app.utils.url import UrlUtils

from app.plugins.mediacovergenerator.style.style_static_1 import create_style_static_1
from app.plugins.mediacovergenerator.style.style_static_2 import create_style_static_2
from app.plugins.mediacovergenerator.style.style_static_3  import create_style_static_3
from app.plugins.mediacovergenerator.style.style_static_4 import create_style_static_4
from app.plugins.mediacovergenerator.style.style_animated_1 import create_style_animated_1
from app.plugins.mediacovergenerator.style.style_animated_2 import create_style_animated_2
from app.plugins.mediacovergenerator.style.style_animated_3 import create_style_animated_3
from app.plugins.mediacovergenerator.style.style_animated_4 import create_style_animated_4
from app.plugins.mediacovergenerator.style.style_premium import (
    create_style_static_5,
    create_style_static_6,
    create_style_static_7,
    create_style_static_8,
    create_style_animated_5,
    create_style_animated_6,
    create_style_animated_7,
    create_style_animated_8,
)
from app.plugins.mediacovergenerator.ui import (
    STYLE_COUNT,
    PAGE_TABS,
    build_form,
    build_page,
    default_form_model,
    style_label,
)
from app.plugins.mediacovergenerator.utils.image_manager import ResolutionConfig
from app.plugins.mediacovergenerator.utils.network_helper import NetworkHelper, validate_font_file
from app.plugins.mediacovergenerator.utils.performance_helper import memory_efficient_operation

# 单图风格：入参为一张图片的路径
SINGLE_IMAGE_STYLES = {
    "static_1": create_style_static_1,
    "static_2": create_style_static_2,
    "static_4": create_style_static_4,
    "static_5": create_style_static_5,
    "static_6": create_style_static_6,
    "static_8": create_style_static_8,
}
# 多图静态风格：入参为素材目录
MULTI_IMAGE_STYLES = {
    "static_3": create_style_static_3,
    "static_7": create_style_static_7,
}
# 动态风格：入参为素材目录
ANIMATED_STYLES = {
    "animated_1": create_style_animated_1,
    "animated_2": create_style_animated_2,
    "animated_3": create_style_animated_3,
    "animated_4": create_style_animated_4,
    "animated_5": create_style_animated_5,
    "animated_6": create_style_animated_6,
    "animated_7": create_style_animated_7,
    "animated_8": create_style_animated_8,
}
# 这些风格固定需要 9 张素材，其余动态风格按「轮播图片数量」取图
NINE_IMAGE_STYLES = {"static_3", "static_7", "animated_2", "animated_3", "animated_7"}
ALL_STYLES = {*SINGLE_IMAGE_STYLES, *MULTI_IMAGE_STYLES, *ANIMATED_STYLES}

# 插件保存的封面文件名：时间戳[__媒体库名].扩展名（旧版本只有时间戳）
COVER_NAME_PATTERN = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})(?:__(.+))?\.(jpg|jpeg|png|gif|apng|webp)$",
    re.IGNORECASE,
)
LIVE_TV_TYPE = "livetv"
NUMBERED_IMAGE_PATTERN = re.compile(r"^[1-9][0-9]*\.jpg$", re.IGNORECASE)
DEFAULT_ZH_FONT = "pangmen"
DEFAULT_EN_FONT = "CaveatBrush"
DEFAULT_ZH_FONT_SIZE = 290
DEFAULT_EN_FONT_SIZE = 165
DEFAULT_TITLE_SPACING = 44
# 字距是字号的百分比
DEFAULT_ZH_LETTER_SPACING = 20
DEFAULT_EN_LETTER_SPACING = 4
# 新字体下载失败时退回这些字体，不让封面因为缺字体而生成失败
FONT_FALLBACKS = {DEFAULT_ZH_FONT: "chaohei", DEFAULT_EN_FONT: "EmblemaOne"}
HISTORY_PAGE_SIZE = 8
LIBRARY_CACHE_SECONDS = 120
THUMBNAIL_CACHE_SIZE = 256


class MediaCoverGenerator(_PluginBase):
    # 插件名称
    plugin_name = "Emby媒体库封面生成"
    # 插件描述
    plugin_desc = "生成媒体库动态/静态封面，内置 8 款风格，支持 Emby/Jellyfin"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/wio-ki/MoviePilot-Plugins/main/icons/emby.png"
    # 插件版本
    # 插件版本
    plugin_version = "1.2.0"
    # 插件作者
    plugin_author = "Kioo"
    # 作者主页
    author_url = "https://github.com/wio-ki"
    # 插件配置项ID前缀
    plugin_config_prefix = "mediacovergenerator_"
    # 加载顺序
    plugin_order = 2
    # 可使用的用户级别
    auth_level = 1

    # 退出事件
    _event = threading.Event()

    # 私有属性
    _scheduler = None
    mschain = None
    mediaserver_helper = None
    _enabled = False
    _update_now = False
    _transfer_monitor = True
    _notify = False
    _cron = None
    _delay = 60
    _servers = None
    _selected_servers = []
    _all_libraries = []
    _include_libraries = []
    _sort_by = 'Random'
    _monitor_sort = ''
    _current_updating_items = set()
    _covers_output = ''
    _covers_input = ''
    _zh_font_url = ''
    _en_font_url = ''
    _zh_font_path = ''
    _en_font_path = ''
    _title_config = ''
    _current_config = {}
    _cover_style = 'static_1'
    _cover_style_base = 'static_1'
    _cover_style_variant = 'static'
    _font_path = ''
    _covers_path = ''
    _tab = 'style-tab'
    _multi_1_blur = True
    _zh_font_size = None
    _en_font_size = None
    _blur_size = 50
    _color_ratio = 0.8
    _use_primary = False
    _seen_keys = set()
    _zh_font_custom = ''
    _en_font_custom = ''
    _zh_font_preset = DEFAULT_ZH_FONT
    _en_font_preset = DEFAULT_EN_FONT
    _zh_font_offset = ''
    _title_spacing = ''
    _en_line_spacing = ''
    _zh_letter_spacing = None
    _en_letter_spacing = None
    _typography_v2 = False
    _title_scale = 1.0
    _resolution = '480p'
    _custom_width = 1920
    _custom_height = 1080
    _resolution_config = None
    _animation_duration = 8
    _animation_scroll = 'alternate'
    _animation_fps = 24
    _animation_format = 'apng'
    _animation_resolution = '320x180'
    _animation_reduce_colors = 'medium'
    _animated_2_image_count = 6
    _animated_2_departure_type = 'fly'
    _style_naming_v2 = True
    _sanitize_log_cache = set()
    _clean_images = False
    _clean_fonts = False
    _save_recent_covers = True
    _covers_history_limit_per_library = 10
    _covers_page_history_limit = 50
    _page_tab = "style"
    _bg_color_mode = "auto"
    _custom_bg_color = ""
    _title_config_error = ""
    _failure_reason = ""

    def __init__(self):
        super().__init__()
        self._event = threading.Event()
        self._update_lock = threading.Lock()
        self._current_updating_items = set()
        self._seen_keys = set()
        self._sanitize_log_cache = set()
        self._run_style = None
        self._task: Dict[str, Any] = {"running": False}
        self._monitor_lock = threading.Lock()
        self._monitor_pending: Dict[str, Any] = {}
        self._monitor_timer = None
        self._monitor_batch_updated: set = set()
        self._thumbnail_cache: "OrderedDict[Tuple[Any, ...], str]" = OrderedDict()
        self._thumbnail_lock = threading.Lock()
        self._library_cache: Dict[str, Tuple[float, List[Dict[str, str]]]] = {}
        self._history_visible = HISTORY_PAGE_SIZE

    def init_plugin(self, config: dict = None):
        # 重新加载前先停止旧任务；旧任务还在收尾时保留停止信号，由它结束时清除
        self.stop_service()
        self.mschain = MediaServerChain()
        self.mediaserver_helper = MediaServerHelper()
        self._servers = {}
        self._all_libraries = []
        self._library_cache = {}
        self._monitor_sort = ''
        if not self._update_lock.locked():
            self._current_updating_items.clear()
            self._event.clear()
        data_path = self.get_data_path()
        (data_path / 'fonts').mkdir(parents=True, exist_ok=True)
        (data_path / 'input').mkdir(parents=True, exist_ok=True)
        self._covers_path = data_path / 'input'
        self._font_path = data_path / 'fonts'
        if config:
            self._enabled = config.get("enabled")
            self._update_now = config.get("update_now")
            self._transfer_monitor = config.get("transfer_monitor")
            self._notify = bool(config.get("notify", False))
            self._cron = config.get("cron")
            self._delay = config.get("delay")
            self._selected_servers = config.get("selected_servers")
            self._include_libraries = config.get("include_libraries")
            self._sort_by = config.get("sort_by")
            self._covers_output = config.get("covers_output")
            self._covers_input = config.get("covers_input")
            self._title_config = config.get("title_config")
            self._zh_font_url = config.get("zh_font_url")
            self._en_font_url = config.get("en_font_url")
            self._zh_font_path = config.get("zh_font_path")
            self._en_font_path = config.get("en_font_path")
            self._cover_style = config.get("cover_style", "static_1")

            # 样式命名升级兼容（仅对旧配置执行一次迁移）
            if not config.get("style_naming_v2"):
                if self._cover_style == 'single_1':
                    self._cover_style = 'static_1'
                elif self._cover_style == 'single_2':
                    self._cover_style = 'static_2'
                elif self._cover_style == 'multi_1':
                    self._cover_style = 'static_3'
            default_base, default_variant = self.__resolve_cover_style_ui(self._cover_style)
            self._cover_style_base = config.get("cover_style_base", default_base)
            self._cover_style_variant = config.get("cover_style_variant", default_variant)
            self._cover_style = self.__compose_cover_style(self._cover_style_base, self._cover_style_variant)
            self._multi_1_blur = config.get("multi_1_blur", True)
            self._zh_font_size = config.get("zh_font_size", DEFAULT_ZH_FONT_SIZE)
            self._en_font_size = config.get("en_font_size", DEFAULT_EN_FONT_SIZE)
            self._blur_size = self.__to_number(config.get("blur_size"), 50, int)
            self._color_ratio = self.__to_number(config.get("color_ratio"), 0.8, float)
            self._use_primary = config.get("use_primary")
            self._zh_font_custom = config.get("zh_font_custom") or ""
            self._en_font_custom = config.get("en_font_custom") or ""
            self._zh_font_preset = config.get("zh_font_preset", DEFAULT_ZH_FONT)
            self._en_font_preset = config.get("en_font_preset", DEFAULT_EN_FONT)
            self._zh_font_offset = config.get("zh_font_offset")
            self._title_spacing = config.get("title_spacing")
            self._en_line_spacing = config.get("en_line_spacing")
            self._zh_letter_spacing = config.get("zh_letter_spacing")
            self._en_letter_spacing = config.get("en_letter_spacing")
            self._typography_v2 = bool(config.get("typography_v2"))
            self._title_scale = self.__to_number(config.get("title_scale"), 1.0, float)
            self._resolution = config.get("resolution", "480p")
            self._custom_width = config.get("custom_width", 1920)
            self._custom_height = config.get("custom_height", 1080)
            self._animation_duration = self.__clamp_value(
                self.__to_number(config.get("animation_duration"), 8, int), 1, 60, 8, "animation_duration", int)
            self._animation_scroll = config.get("animation_scroll", "alternate")
            self._animation_fps = self.__clamp_value(
                self.__to_number(config.get("animation_fps"), 24, int), 1, 60, 24, "animation_fps", int)
            self._animation_format = config.get("animation_format", "apng")
            if self._animation_format == "webp":
                self._animation_format = "gif"
            if self._animation_format not in ["apng", "gif"]:
                self._animation_format = "apng"
            self._animation_resolution = config.get("animation_resolution", "320x180")
            animation_reduce_colors = config.get("animation_reduce_colors", "medium")
            if isinstance(animation_reduce_colors, bool):
                self._animation_reduce_colors = "medium" if animation_reduce_colors else "off"
            elif animation_reduce_colors in ["off", "medium", "strong"]:
                self._animation_reduce_colors = animation_reduce_colors
            else:
                self._animation_reduce_colors = "medium"

            self._animated_2_image_count = config.get("animated_2_image_count", 6)
            self._animated_2_departure_type = config.get("animated_2_departure_type", "fly")
            self._clean_images = config.get("clean_images", False)
            self._clean_fonts = config.get("clean_fonts", False)
            self._save_recent_covers = config.get("save_recent_covers", True)
            self._covers_history_limit_per_library = self.__clamp_value(
                config.get("covers_history_limit_per_library", 10),
                1,
                100,
                10,
                "covers_history_limit_per_library[init_plugin]",
                int,
            )
            self._covers_page_history_limit = self.__clamp_value(
                config.get("covers_page_history_limit", 50),
                1,
                500,
                50,
                "covers_page_history_limit[init_plugin]",
                int,
            )

        self._enabled = bool(self._enabled)
        self._transfer_monitor = bool(self._transfer_monitor)
        self._selected_servers = self._selected_servers or []
        self._include_libraries = self._include_libraries or []
        try:
            self._delay = max(0, int(self._delay or 0))
        except (TypeError, ValueError):
            logger.warning(f"入库延迟配置非法 ({self._delay})，已回退为 60 秒")
            self._delay = 60

        if self._resolution not in ["1080p", "720p", "480p"]:
            self._resolution = "480p"
        self._animation_resolution = "320x180"

        self._animated_2_image_count = self.__clamp_value(
            self._animated_2_image_count,
            3,
            9,
            6,
            "animated_2 image_count[init_plugin]",
            int,
        )
        if self._animated_2_departure_type not in ["fly", "fade", "crossfade"]:
            self._animated_2_departure_type = "fly"
        if self._animation_scroll not in ["down", "up", "alternate", "alternate_reverse"]:
            self._animation_scroll = "alternate"
        self._bg_color_mode = (config or {}).get("bg_color_mode", "auto")
        self._custom_bg_color = (config or {}).get("custom_bg_color", "")

        # 旧版本提供过实际不存在的字体预设，这里回退到默认字体
        valid_fonts = {spec["value"] for spec in self.__font_specs()}
        if self._zh_font_preset not in valid_fonts:
            self._zh_font_preset = DEFAULT_ZH_FONT
        if self._en_font_preset not in valid_fonts:
            self._en_font_preset = DEFAULT_EN_FONT
        typography_migrated = bool(config) and self.__migrate_typography()

        self._title_config_error = ""
        if self._title_config:
            self.__load_title_config(self._title_config)

        # 初始化分辨率配置（确保安全初始化）
        try:
            self._resolution_config = ResolutionConfig(self._resolution)
        except Exception as e:
            logger.warning(f"分辨率配置初始化失败，使用默认配置: {e}")
            self._resolution_config = ResolutionConfig("480p")

        if self._selected_servers:
            self._servers = self.mediaserver_helper.get_services(
                name_filters=self._selected_servers
            ) or {}
            self._all_libraries = []
            for server, service in self._servers.items():
                try:
                    if not service.instance.is_inactive():
                        self._all_libraries.extend(self.__get_all_libraries(server, service))
                    else:
                        logger.info(f"媒体服务器 {server} 未连接")
                except Exception as error:
                    # 媒体服务器不可用不应该影响插件加载
                    logger.warning(f"读取媒体服务器 {server} 的媒体库失败: {error}")
        else:
            logger.info("未选择媒体服务器")

        cleanup_triggered = False
        if self._clean_images:
            self.__clean_generated_images()
            self._clean_images = False
            cleanup_triggered = True
        if self._clean_fonts:
            self.__clean_downloaded_fonts()
            self._clean_fonts = False
            cleanup_triggered = True
        if cleanup_triggered or typography_migrated:
            self.__update_config()

        if self._update_now:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            self._scheduler.add_job(func=self.__update_all_libraries, trigger='date',
                                    run_date=datetime.datetime.now(
                                        tz=pytz.timezone(settings.TZ)) + datetime.timedelta(seconds=3)
                                    )
            logger.info(f"媒体库封面更新服务启动，立即运行一次")
            # 关闭一次性开关
            self._update_now = False
            # 保存配置
            self.__update_config()
            # 启动服务
            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()

    @staticmethod
    def __to_number(value, default, cast_type):
        """把表单值转成数字；空值或非法值返回默认值。"""
        if value is None or value == "":
            return default
        try:
            return cast_type(float(value)) if cast_type is int else cast_type(value)
        except (TypeError, ValueError):
            logger.warning(f"配置值非法 ({value})，已回退默认值 {default}")
            return default

    def __clamp_value(self, value, minimum, maximum, default_value, name, cast_type):
        try:
            parsed = cast_type(value)
        except (ValueError, TypeError):
            logger.warning(f"{name} 配置值非法 ({value})，已回退默认值 {default_value}")
            return default_value

        if parsed < minimum or parsed > maximum:
            clamped = max(minimum, min(maximum, parsed))
            logger.warning(f"{name} 配置值超出范围 ({parsed})，已限制为 {clamped}")
            return clamped

        return parsed

    def __get_animated_2_required_items(self) -> int:
        self._animated_2_image_count = self.__clamp_value(
            self._animated_2_image_count,
            3,
            9,
            6,
            "animated_2 image_count[runtime]",
            int,
        )
        return int(self._animated_2_image_count)

    def __compose_cover_style(self, base_style: str, variant: str) -> str:
        base = base_style if base_style in SINGLE_IMAGE_STYLES or base_style in MULTI_IMAGE_STYLES else "static_1"
        mode = variant if variant in ["static", "animated"] else "static"
        suffix = base.split("_")[-1]
        return base if mode == "static" else f"animated_{suffix}"

    def __resolve_cover_style_ui(self, cover_style: str) -> Tuple[str, str]:
        if cover_style in ANIMATED_STYLES:
            return f"static_{cover_style.split('_')[-1]}", "animated"
        if cover_style in ALL_STYLES:
            return cover_style, "static"
        return "static_1", "static"

    def __current_style(self) -> str:
        """当前生成使用的风格。任务开始时固定下来，运行中切换风格不影响正在处理的媒体库。"""
        return self._run_style or self._cover_style

    def __is_single_image_style(self) -> bool:
        return self.__current_style() in SINGLE_IMAGE_STYLES

    def __get_required_items(self) -> int:
        style = self.__current_style()
        if style in SINGLE_IMAGE_STYLES:
            return 1
        if style in ("static_3", "static_7", "animated_3", "animated_7"):
            return 9
        return self.__get_animated_2_required_items()

    def __update_config(self):
        """
        更新配置
        """
        self._cover_style = self.__compose_cover_style(self._cover_style_base, self._cover_style_variant)
        self._animated_2_image_count = self.__clamp_value(
            self._animated_2_image_count,
            3,
            9,
            6,
            "animated_2 image_count[save]",
            int,
        )
        self.update_config({
            "enabled": self._enabled,
            "update_now": self._update_now,
            "transfer_monitor": self._transfer_monitor,
            "notify": self._notify,
            "cron": self._cron,
            "delay": self._delay,
            "selected_servers": self._selected_servers,
            "include_libraries": self._include_libraries,
            "all_libraries": self._all_libraries,
            "sort_by": self._sort_by,
            "covers_output": self._covers_output,
            "covers_input": self._covers_input,
            "title_config": self._title_config,
            "zh_font_url": str(self._zh_font_url or ""),
            "en_font_url": str(self._en_font_url or ""),
            "zh_font_path": str(self._zh_font_path or ""),
            "en_font_path": str(self._en_font_path or ""),
            "cover_style": self._cover_style,
            "cover_style_base": self._cover_style_base,
            "cover_style_variant": self._cover_style_variant,
            "multi_1_blur": self._multi_1_blur,
            "zh_font_size": self._zh_font_size,
            "en_font_size": self._en_font_size,
            "blur_size": self._blur_size,
            "color_ratio": self._color_ratio,
            "use_primary": self._use_primary,
            "zh_font_custom": self._zh_font_custom,
            "en_font_custom": self._en_font_custom,
            "zh_font_preset": self._zh_font_preset,
            "en_font_preset": self._en_font_preset,
            "zh_font_offset": self._zh_font_offset,
            "title_spacing": self._title_spacing,
            "en_line_spacing": self._en_line_spacing,
            "zh_letter_spacing": self._zh_letter_spacing,
            "en_letter_spacing": self._en_letter_spacing,
            "typography_v2": self._typography_v2,
            "title_scale": self._title_scale,
            "resolution": self._resolution,
            "animation_duration": self._animation_duration,
            "animation_scroll": self._animation_scroll,
            "animation_fps": self._animation_fps,
            "animation_format": self._animation_format,
            "animation_resolution": self._animation_resolution,
            "animation_reduce_colors": self._animation_reduce_colors,
            "animated_2_image_count": self._animated_2_image_count,
            "animated_2_departure_type": self._animated_2_departure_type,
            "bg_color_mode": self._bg_color_mode,
            "custom_bg_color": self._custom_bg_color,
            "clean_images": self._clean_images,
            "clean_fonts": self._clean_fonts,
            "save_recent_covers": self._save_recent_covers,
            "covers_history_limit_per_library": self._covers_history_limit_per_library,
            "covers_page_history_limit": self._covers_page_history_limit,
            "style_naming_v2": True,
        })

    def get_state(self) -> bool:
        return self._enabled

    def __font_search_dirs(self) -> List[Path]:
        dirs: List[Path] = []
        if self._font_path:
            dirs.append(Path(self._font_path))
        repo_font_dir = Path(__file__).resolve().parents[2] / "fonts"
        dirs.append(repo_font_dir)
        unique_dirs: List[Path] = []
        seen = set()
        for directory in dirs:
            key = str(directory)
            if key in seen:
                continue
            seen.add(key)
            if directory.exists() and directory.is_dir():
                unique_dirs.append(directory)
        return unique_dirs

    def __find_font_file(self, aliases: List[str], exts: List[str]) -> Optional[str]:
        normalized_aliases = [item.lower() for item in aliases if item]
        normalized_aliases_compact = [re.sub(r'[\s_\-]+', '', item) for item in normalized_aliases]
        normalized_exts = [item.lower() for item in exts]
        for directory in self.__font_search_dirs():
            candidates = sorted(directory.iterdir(), key=lambda p: p.name.lower())
            for font_file in candidates:
                if not font_file.is_file():
                    continue
                suffix = font_file.suffix.lower()
                if suffix not in normalized_exts:
                    continue
                stem = font_file.stem.lower()
                # 自定义链接下载的字体不能冒充预设字体
                if stem.endswith("_custom"):
                    continue
                name = font_file.name.lower()
                stem_compact = re.sub(r'[\s_\-]+', '', stem)
                name_compact = re.sub(r'[\s_\-]+', '', name)
                if any(
                    alias in stem or alias in name or compact in stem_compact or compact in name_compact
                    for alias, compact in zip(normalized_aliases, normalized_aliases_compact)
                ):
                    return str(font_file)
        return None

    @staticmethod
    def __font_specs() -> List[Dict[str, Any]]:
        return [
            {"title": "庞门正道粗书体", "value": "pangmen", "cjk": True, "aliases": ["pangmen", "庞门正道粗书体"]},
            {"title": "潮黑", "value": "chaohei", "cjk": True, "aliases": ["chaohei", "wendao", "潮黑", "chao_hei"]},
            {"title": "粗雅宋", "value": "yasong", "cjk": True, "aliases": ["yasong", "粗雅宋", "multi_1_zh", "ya_song"]},
            {"title": "CaveatBrush", "value": "CaveatBrush", "cjk": False, "aliases": ["caveatbrush", "caveat_brush"]},
            {"title": "EmblemaOne", "value": "EmblemaOne", "cjk": False, "aliases": ["emblemaone", "emblema_one"]},
            {"title": "Melete", "value": "Melete", "cjk": False, "aliases": ["melete", "multi_1_en"]},
            {"title": "Phosphate", "value": "Phosphate", "cjk": False, "aliases": ["phosphate", "phosphat"]},
            {"title": "JosefinSans", "value": "JosefinSans", "cjk": False, "aliases": ["josefinsans", "josefin_sans"]},
            {"title": "LilitaOne", "value": "LilitaOne", "cjk": False, "aliases": ["lilitaone", "lilita_one"]},
        ]

    def __migrate_typography(self) -> bool:
        """
        1.2.0 换了默认字体和字号。一直用旧默认值的配置跟随新默认；
        改过字体或字号的配置把留空的参数固定为旧默认值，封面不会因为升级而变样。
        返回配置是否有变化。
        """
        if self._typography_v2:
            return False
        self._typography_v2 = True

        def unset_or(value, old):
            return value in (None, "") or self.__to_number(value, old, float) == old

        unchanged = (
            self._zh_font_preset == "chaohei" and self._en_font_preset == "EmblemaOne"
            and not self._zh_font_custom and not self._en_font_custom
            and unset_or(self._zh_font_size, 170) and unset_or(self._en_font_size, 75)
            and unset_or(self._title_spacing, 40) and unset_or(self._en_line_spacing, 40)
        )
        if unchanged:
            self._zh_font_preset = DEFAULT_ZH_FONT
            self._en_font_preset = DEFAULT_EN_FONT
            self._zh_font_size = self._en_font_size = None
            self._title_spacing = self._en_line_spacing = None
            logger.info("封面字体已切换为新的默认搭配：庞门正道粗书体 + CaveatBrush")
            return True

        if self._zh_font_size in (None, ""):
            self._zh_font_size = 170
        if self._en_font_size in (None, ""):
            self._en_font_size = 75
        if self._title_spacing in (None, ""):
            self._title_spacing = 40
        if self._zh_letter_spacing in (None, ""):
            self._zh_letter_spacing = 0
        if self._en_letter_spacing in (None, ""):
            self._en_letter_spacing = 8
        return True

    def __get_font_presets(self) -> Tuple[List[Dict[str, str]], List[Dict[str, str]], Dict[str, Optional[str]], Dict[str, Optional[str]]]:
        items: List[Dict[str, str]] = []
        paths: Dict[str, Optional[str]] = {}
        exts = [".ttf", ".otf", ".woff2", ".woff"]
        for spec in self.__font_specs():
            suffix = "" if spec["cjk"] else "（仅英文）"
            items.append({"title": f"{spec['title']}{suffix}", "value": spec["value"]})
            aliases = [*spec["aliases"], spec["value"].lower(), spec["title"].lower()]
            paths[spec["value"]] = self.__find_font_file(aliases, exts)
        return items, list(items), paths, dict(paths)

    def __clean_generated_images(self):
        removed = 0
        data_path = self.get_data_path()
        # 插件自己的目录可以整体清空
        owned_dirs: List[Path] = [data_path / "covers", data_path / "output", data_path / "preview"]
        if self._covers_path:
            owned_dirs.append(Path(self._covers_path))

        handled = set()
        for cache_dir in owned_dirs:
            if not cache_dir.is_dir():
                continue
            cache_key = str(cache_dir.resolve())
            if cache_key in handled:
                continue
            handled.add(cache_key)
            for entry in cache_dir.iterdir():
                try:
                    if entry.is_dir():
                        shutil.rmtree(entry)
                    else:
                        entry.unlink(missing_ok=True)
                    removed += 1
                except Exception as e:
                    logger.warning(f"清理图片失败 {entry}: {e}")

        # 用户指定的保存目录里可能有别的文件，只删除插件生成的封面
        if self._covers_output:
            custom_dir = Path(self._covers_output).expanduser()
            if custom_dir.is_dir() and str(custom_dir.resolve()) not in handled:
                for entry in custom_dir.iterdir():
                    if not entry.is_file() or not COVER_NAME_PATTERN.match(entry.name):
                        continue
                    try:
                        entry.unlink(missing_ok=True)
                        removed += 1
                    except Exception as e:
                        logger.warning(f"清理图片失败 {entry}: {e}")

        with self._thumbnail_lock:
            self._thumbnail_cache.clear()
        logger.info(f"清理图片完成，共清理 {removed} 项")

    def __clean_downloaded_fonts(self):
        if not self._font_path or not Path(self._font_path).exists():
            logger.info("清理字体：未找到字体目录，跳过")
            return
        removed = 0
        for entry in Path(self._font_path).iterdir():
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_file():
                    entry.unlink(missing_ok=True)
                    removed += 1
                elif entry.is_dir():
                    shutil.rmtree(entry)
                    removed += 1
            except Exception as e:
                logger.warning(f"清理字体失败 {entry}: {e}")
        self._zh_font_path = ""
        self._en_font_path = ""
        logger.info(f"清理字体完成，共清理 {removed} 项")

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return [
            {
                "cmd": "/update_covers",
                "event": EventType.PluginAction,
                "desc": "更新媒体库封面",
                "category": "",
                "data": {"action": "update_covers"},
            }
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        """
        获取插件API
        [{
            "path": "/xx",
            "endpoint": self.xxx,
            "methods": ["GET", "POST"],
            "summary": "API说明"
        }]
        """
        routes = [
            ("/clean_images", self.api_clean_images, "立即清理封面图片缓存"),
            ("/clean_fonts", self.api_clean_fonts, "立即清理字体缓存"),
            ("/delete_saved_cover", self.api_delete_saved_cover, "删除一张已保存封面"),
            ("/generate_now", self.api_generate_now, "在后台生成媒体库封面"),
            ("/stop_task", self.api_stop_task, "停止正在运行的生成任务"),
            ("/refresh", self.api_refresh, "刷新详情页"),
            ("/history_more", self.api_history_more, "显示更多历史封面"),
            ("/set_cover_style", self.api_set_cover_style, "保存封面风格"),
            ("/select_style", self.api_select_style, "按序号选择封面风格"),
            ("/set_style_variant", self.api_set_style_variant, "切换静态或动态"),
        ]
        response_model = self.__response_model()
        result: List[Dict[str, Any]] = []
        for path, endpoint, summary in routes:
            # 部分 MoviePilot 版本要求路径不带前导斜杠，两种写法都注册
            for route_path in (path, path.lstrip("/")):
                route: Dict[str, Any] = {
                    "path": route_path,
                    "endpoint": endpoint,
                    "auth": "bear",
                    "methods": ["POST"],
                    "summary": summary,
                }
                if response_model is not None:
                    route["response_model"] = response_model
                result.append(route)
        for route_path in ("/saved_cover_image", "saved_cover_image"):
            result.append({
                "path": route_path,
                "endpoint": self.api_saved_cover_image,
                "methods": ["GET"],
                "response_model": None,
                "summary": "获取已保存封面图片",
            })
        return result

    @staticmethod
    def __response_model():
        response_model = getattr(schemas, "Response", None)
        if response_model is None:
            return None
        try:
            return response_model[Any]
        except TypeError:
            # V2 的 Response 不是泛型
            return response_model

    @classmethod
    def __api_response(cls, success: bool, message: str = "", data: Any = None):
        """同时满足 V3 的严格响应信封和 V2 的普通响应模型。"""
        return cls.__response_model()(success=success, message=message, data=data)

    def __api_prefix(self) -> str:
        return f"plugin/{self.__class__.__name__}"

    def __set_page_tab(self, tab: str):
        """记住操作发生的页签，操作后页面刷新时停留在原处。"""
        valid_tabs = {item[0] for item in PAGE_TABS}
        if tab in valid_tabs:
            self._page_tab = tab

    def api_clean_images(self, tab: str = ""):
        self.__set_page_tab(tab)
        try:
            logger.info("【MediaCoverGenerator】收到立即清理图片缓存请求")
            self.__clean_generated_images()
            self._clean_images = False
            self.__update_config()
            return self.__api_response(True, "图片缓存清理完成")
        except Exception as e:
            logger.error(f"【MediaCoverGenerator】立即清理图片失败: {e}", exc_info=True)
            return self.__api_response(False, f"图片缓存清理失败: {e}")

    def api_clean_fonts(self, tab: str = ""):
        self.__set_page_tab(tab)
        try:
            logger.info("【MediaCoverGenerator】收到立即清理字体缓存请求")
            self.__clean_downloaded_fonts()
            self._clean_fonts = False
            self.__update_config()
            return self.__api_response(True, "字体缓存清理完成")
        except Exception as e:
            logger.error(f"【MediaCoverGenerator】立即清理字体失败: {e}", exc_info=True)
            return self.__api_response(False, f"字体缓存清理失败: {e}")

    def api_delete_saved_cover(self, file: str = "", tab: str = ""):
        self.__set_page_tab(tab)
        try:
            target_file = self.__resolve_saved_cover_path(file)
            if not target_file:
                return self.__api_response(False, "文件不存在或不是插件生成的封面")
            target_file.unlink(missing_ok=True)
            logger.info(f"【MediaCoverGenerator】已删除封面文件: {target_file}")
            return self.__api_response(True, "封面文件删除成功")
        except Exception as e:
            logger.error(f"【MediaCoverGenerator】删除封面文件失败: {e}", exc_info=True)
            return self.__api_response(False, f"封面文件删除失败: {e}")

    def api_generate_now(self, library: str = "", tab: str = ""):
        """启动后台生成并立即返回，进度显示在详情页。"""
        self.__set_page_tab(tab)
        if not self._enabled:
            return self.__api_response(False, "插件未启用，请先在设置页启用插件并保存")
        if not self._selected_servers:
            return self.__api_response(False, "未选择媒体服务器，请先在设置页选择并保存")
        if not self._servers:
            return self.__api_response(False, "媒体服务器连接信息为空，请检查设置并保存后重试")
        if self.__is_running():
            return self.__api_response(False, "已有封面生成任务正在运行")

        # 先清除信号再标记运行中：任务线程启动的这段时间里点「停止」也不会丢失
        self._event.clear()
        task = {"running": True, "total": 0, "done": 0, "current": ""}
        self._task = task
        threading.Thread(
            target=self.__run_in_background,
            args=((library or "").strip() or None, task),
            name="MediaCoverGenerator",
            daemon=True,
        ).start()
        logger.info(f"【MediaCoverGenerator】已启动后台生成，风格: {self._cover_style}，媒体库: {library or '全部'}")
        return self.__api_response(True, "封面生成任务已在后台启动")

    def __run_in_background(self, library_key: Optional[str], task: Dict[str, Any]):
        try:
            self.__update_all_libraries(library_key=library_key)
        except Exception as e:
            logger.error(f"【MediaCoverGenerator】后台生成失败: {e}", exc_info=True)
        finally:
            # 任务没能真正开始时才需要复位；这里只认自己的状态，不能覆盖下一个任务
            if self._task is task:
                self._task = {"running": False}

    def __is_running(self) -> bool:
        return self._update_lock.locked() or bool(self._task.get("running"))

    def api_stop_task(self, tab: str = ""):
        self.__set_page_tab(tab)
        _, message = self.stop_task()
        return self.__api_response(True, message)

    def api_refresh(self, tab: str = ""):
        self.__set_page_tab(tab)
        return self.__api_response(True, "")

    def api_history_more(self, tab: str = ""):
        self.__set_page_tab(tab)
        self._history_visible += HISTORY_PAGE_SIZE * 2
        return self.__api_response(True, "")

    def __apply_style(self, target_style: str):
        self._cover_style = target_style
        self._cover_style_base, self._cover_style_variant = self.__resolve_cover_style_ui(target_style)
        self.__update_config()
        logger.info(f"【MediaCoverGenerator】已保存封面风格: {target_style}")

    def api_set_cover_style(self, style: str = "", tab: str = ""):
        self.__set_page_tab(tab)
        target_style = (style or "").strip()
        if target_style not in ALL_STYLES:
            return self.__api_response(False, f"不支持的风格: {target_style}")
        try:
            self.__apply_style(target_style)
            return self.__api_response(True, f"已保存风格: {target_style}")
        except Exception as e:
            logger.error(f"【MediaCoverGenerator】保存封面风格失败: {e}", exc_info=True)
            return self.__api_response(False, f"保存风格失败: {e}")

    def __get_cover_style_parts(self) -> Tuple[str, int]:
        style = (self._cover_style or "static_1").strip()
        variant = "animated" if style.startswith("animated_") else "static"
        try:
            index = int(style.split("_")[-1])
        except (TypeError, ValueError):
            index = 1
        return variant, max(1, min(STYLE_COUNT, index))

    def api_select_style(self, index: int = 1, tab: str = ""):
        self.__set_page_tab(tab)
        try:
            variant, _ = self.__get_cover_style_parts()
            self.__apply_style(f"{variant}_{max(1, min(STYLE_COUNT, int(index)))}")
            return self.__api_response(True, "风格已切换")
        except Exception as e:
            logger.error(f"【MediaCoverGenerator】选择风格失败: {e}", exc_info=True)
            return self.__api_response(False, f"选择风格失败: {e}")

    def api_set_style_variant(self, variant: str = "static", tab: str = ""):
        self.__set_page_tab(tab)
        try:
            _, index = self.__get_cover_style_parts()
            safe_variant = "animated" if variant == "animated" else "static"
            self.__apply_style(f"{safe_variant}_{index}")
            return self.__api_response(True, "已切换")
        except Exception as e:
            logger.error(f"【MediaCoverGenerator】切换静态/动态失败: {e}", exc_info=True)
            return self.__api_response(False, f"切换失败: {e}")

    def api_saved_cover_image(self, file: str = ""):
        target_file = self.__resolve_saved_cover_path(file)
        if not target_file:
            return self.__api_response(False, "图片不存在")
        mime_type, _ = mimetypes.guess_type(str(target_file))
        if not mime_type:
            mime_type = "image/jpeg"
        from fastapi.responses import FileResponse
        return FileResponse(path=str(target_file), media_type=mime_type)

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        """
        services = []
        if self._enabled and self._cron:
            try:
                trigger = CronTrigger.from_crontab(self._cron)
            except (TypeError, ValueError) as error:
                logger.error(f"定时更新封面 Cron 配置无效: {error}")
            else:
                services.append({
                    "id": "MediaCoverGenerator",
                    "name": "媒体库封面更新服务",
                    "trigger": trigger,
                    "func": self.__update_all_libraries,
                    "kwargs": {}
                })

        # 总是显示停止按钮，以便中断长时间运行的任务
        services.append({
            "id": "StopMediaCoverGenerator",
            "name": "停止当前更新任务",
            "trigger": None,
            "func": self.stop_task,
            "kwargs": {}
        })
        return services

    def stop_task(self):
        """
        手动停止当前正在执行的任务
        """
        if not self.__is_running():
            return True, "当前没有正在执行的封面更新任务"
        if not self._event.is_set():
            logger.info("正在发送停止任务信号...")
            self._event.set()
            return True, "已发送停止信号，请等待当前操作清理完成"
        return True, "任务正在停止中"

    def __server_configs(self) -> List[Any]:
        try:
            return [config for config in self.mediaserver_helper.get_configs().values()
                    if config.type in ("emby", "jellyfin")]
        except Exception as error:
            logger.warning(f"读取媒体服务器配置失败: {error}")
            return []

    def __libraries_for_form(self) -> List[Dict[str, str]]:
        """列出所有已配置服务器的媒体库，这样勾选新服务器后无需先保存就能选媒体库。"""
        result: List[Dict[str, str]] = []
        now = time.time()
        for config in self.__server_configs():
            name = config.name
            cached = self._library_cache.get(name)
            if cached and now - cached[0] < LIBRARY_CACHE_SECONDS:
                result.extend(cached[1])
                continue
            libraries: List[Dict[str, str]] = []
            try:
                services = self.mediaserver_helper.get_services(name_filters=[name]) or {}
                service = services.get(name)
                if service and not service.instance.is_inactive():
                    libraries = self.__get_all_libraries(name, service)
            except Exception as error:
                logger.warning(f"读取媒体服务器 {name} 的媒体库失败: {error}")
            self._library_cache[name] = (now, libraries)
            result.extend(libraries)
        return result

    def __title_config_from_libraries(self, libraries: List[Dict[str, str]]) -> str:
        """在现有标题配置后补上尚未配置的媒体库，已有内容保持不变。"""
        existing_text = (self._title_config or "").rstrip()
        configured = set(self.__load_title_config(existing_text)) if existing_text else set()
        blocks = [existing_text] if existing_text else []
        seen = set()
        for library in libraries:
            name = library.get("library") or library.get("name") or ""
            if not name or name in configured or name in seen:
                continue
            seen.add(name)
            quoted_name = json.dumps(name, ensure_ascii=False)
            blocks.append(f'{quoted_name}:\n  - {quoted_name}\n  - ""')
        return "\n".join(blocks) + "\n" if blocks else ""

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面
        """
        self._page_tab = "style"
        self._history_visible = HISTORY_PAGE_SIZE
        zh_font_items, en_font_items, _, _ = self.__get_font_presets()
        libraries = self.__libraries_for_form()
        library_items = [
            {"title": library["name"], "value": library["value"], "server": library["server"]}
            for library in libraries
        ]
        ctx = {
            "server_items": [{"title": config.name, "value": config.name} for config in self.__server_configs()],
            # 媒体库选项跟随已选服务器变化，表达式在前端按当前表单值计算
            "library_items_expr": "{{ %s.filter(item => (selected_servers || []).includes(item.server)) }}"
                                  % json.dumps(library_items, ensure_ascii=False),
            "title_from_libraries": self.__title_config_from_libraries(libraries),
            "zh_font_items": zh_font_items,
            "en_font_items": en_font_items,
        }
        return build_form(ctx), default_form_model()

    def __run_summary(self) -> Tuple[str, str]:
        last_run = self.get_data("last_run") or {}
        if not isinstance(last_run, dict) or not last_run.get("time"):
            return "", ""
        return str(last_run.get("time")), str(last_run.get("message") or "")

    def __page_checks(self) -> List[Dict[str, Any]]:
        checks = [{
            "ok": self._enabled,
            "title": "插件已启用" if self._enabled else "插件未启用",
            "note": "" if self._enabled else "在设置中启用后才能生成封面",
        }]
        if not self._selected_servers:
            checks.append({"ok": False, "title": "未选择媒体服务器", "note": "在设置的「基础」页选择"})
        for server in self._selected_servers:
            service = (self._servers or {}).get(server)
            connected = False
            try:
                connected = bool(service) and not service.instance.is_inactive()
            except Exception as error:
                logger.debug(f"检查媒体服务器 {server} 失败: {error}")
            checks.append({
                "ok": connected,
                "title": f"媒体服务器 {server} " + ("已连接" if connected else "未连接"),
                "note": "" if connected else "请检查 MoviePilot 的媒体服务器配置",
            })
        checks.append({
            "ok": not self._title_config_error,
            "title": "标题配置有效" if not self._title_config_error else "标题配置无法解析",
            "note": self._title_config_error,
        })
        _, _, zh_paths, en_paths = self.__get_font_presets()
        zh_ready = self._zh_font_custom or zh_paths.get(self._zh_font_preset)
        en_ready = self._en_font_custom or en_paths.get(self._en_font_preset)
        checks.append({
            "ok": True,
            "title": "字体已就绪" if zh_ready and en_ready else "字体将在首次生成时下载",
            "note": f"主标题 {self._zh_font_custom or self._zh_font_preset}，副标题 {self._en_font_custom or self._en_font_preset}",
        })
        has_ffmpeg = bool(shutil.which("ffmpeg"))
        checks.append({
            "ok": has_ffmpeg,
            "title": "ffmpeg 可用" if has_ffmpeg else "未找到 ffmpeg",
            "note": "" if has_ffmpeg else "动态风格需要 ffmpeg，静态风格不受影响",
        })
        return checks

    def __dir_size(self, directory: Path, pattern: Optional[re.Pattern] = None) -> int:
        total = 0
        try:
            for root, _dirs, files in os.walk(directory):
                for name in files:
                    if pattern and not pattern.match(name):
                        continue
                    try:
                        total += os.path.getsize(os.path.join(root, name))
                    except OSError:
                        continue
        except OSError:
            return total
        return total

    def __cache_sizes(self) -> Tuple[str, str]:
        data_path = self.get_data_path()
        image_bytes = sum(self.__dir_size(data_path / name) for name in ("input", "output", "covers", "preview"))
        if self._covers_output:
            custom_dir = Path(self._covers_output).expanduser()
            if custom_dir.is_dir() and custom_dir.resolve() != (data_path / "output").resolve():
                image_bytes += self.__dir_size(custom_dir, COVER_NAME_PATTERN)
        return self.__format_size(image_bytes), self.__format_size(self.__dir_size(data_path / "fonts"))

    def get_page(self) -> List[dict]:
        limit = self.__clamp_value(self._covers_page_history_limit, 1, 500, 50,
                                   "covers_page_history_limit[get_page]", int)
        style_variant, style_index = self.__get_cover_style_parts()

        warnings: List[str] = []
        if not self._enabled:
            warnings.append("插件尚未启用，请在设置中启用并保存")
        if not self._selected_servers:
            warnings.append("还没有选择媒体服务器，请在设置中选择并保存")
        elif not self._servers:
            warnings.append("媒体服务器配置尚未生效，请在设置中保存后重试")
        if self._title_config_error:
            warnings.append("标题配置无法解析，暂时只会使用媒体库名称作为标题")

        page_tab = self._page_tab if self._page_tab in {item[0] for item in PAGE_TABS} else "style"
        all_covers = self.__list_saved_covers()
        # 操作后整个页面都要重新下载。历史图片只在打开历史页时才带上，其他页签保持轻量
        history = []
        if page_tab == "history":
            for cover in all_covers[:min(limit, self._history_visible)]:
                source = self.__thumbnail(cover["file"])
                if source:
                    history.append({**cover, "src": source, "encoded_name": quote(cover["name"], safe="")})

        status_map = self.get_data("library_status") or {}
        if not isinstance(status_map, dict):
            status_map = {}
        latest_by_library: Dict[str, Dict[str, Any]] = {}
        for cover in all_covers:
            latest_by_library.setdefault(cover["library"], cover)

        if not self._all_libraries and self._selected_servers:
            # 插件加载时媒体服务器不可用，这里补一次（带缓存，不会每次刷新都请求）
            self._all_libraries = [library for library in self.__libraries_for_form()
                                   if library["server"] in self._selected_servers]

        libraries = []
        for library in self._all_libraries:
            key = library["value"]
            library_name = library.get("library") or library["name"]
            cover = latest_by_library.get(self.__sanitize_filename(library_name))
            libraries.append({
                "name": library_name,
                "server": library.get("server", ""),
                "key": key,
                "encoded_key": quote(key, safe=""),
                "active": self.__in_batch(key, library.get("type")),
                "status": status_map.get(key),
                "thumb": self.__thumbnail(cover["file"], (224, 126), 60) if cover else None,
            })

        # 先读运行状态再读结果：任务恰好在两次读取之间结束时，也不会显示空闲却配上一轮的结果
        task = dict(self._task)
        if self._update_lock.locked():
            task["running"] = True
        last_time, last_message = self.__run_summary()
        image_cache, font_cache = self.__cache_sizes()
        # 横幅背景会被模糊，很小的图就够了
        hero_image = self.__thumbnail(all_covers[0]["file"], (160, 90), 50) if all_covers else None
        return build_page({
            "api_prefix": self.__api_prefix(),
            "enabled": self._enabled,
            "warnings": warnings,
            "style_index": style_index,
            "style_variant": style_variant,
            "task": task,
            "page_tab": page_tab,
            "hero_image": hero_image,
            "libraries": libraries,
            "library_active": sum(1 for library in libraries if library["active"]),
            "last_run_text": last_time,
            "last_result": last_message,
            "history": history,
            "history_total": min(limit, len(all_covers)),
            "history_limit": self._covers_history_limit_per_library,
            "checks": self.__page_checks(),
            "cache_images": image_cache,
            "cache_fonts": font_cache,
        })

    def __list_saved_covers(self) -> List[Dict[str, Any]]:
        """列出插件保存的封面（不读取图片内容），按时间倒序。"""
        items: List[Dict[str, Any]] = []
        seen = set()
        for directory in self.__get_saved_cover_dirs():
            if str(directory) in seen or not directory.is_dir():
                continue
            seen.add(str(directory))
            try:
                entries = list(directory.iterdir())
            except OSError as error:
                logger.debug(f"读取封面目录失败 {directory}: {error}")
                continue
            for file_path in entries:
                match = COVER_NAME_PATTERN.match(file_path.name)
                if not match or not file_path.is_file():
                    continue
                try:
                    stat = file_path.stat()
                except OSError:
                    continue
                library = match.group(7) or ""
                items.append({
                    "name": file_path.name,
                    "file": file_path,
                    "library": library,
                    "title": library or "历史封面",
                    "mtime_ts": float(stat.st_mtime),
                    "mtime": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%m-%d %H:%M"),
                    "size": self.__format_size(stat.st_size),
                })
        items.sort(key=lambda item: item["mtime_ts"], reverse=True)
        return items

    def __thumbnail(self, file_path: Path, size: Tuple[int, int] = (320, 180), quality: int = 65) -> Optional[str]:
        """生成缩略图 data URI 并按文件修改时间缓存，页面刷新时不必重复解码原图。"""
        try:
            cache_key = (str(file_path), file_path.stat().st_mtime, size)
        except OSError:
            return None
        with self._thumbnail_lock:
            cached = self._thumbnail_cache.get(cache_key)
            if cached:
                self._thumbnail_cache.move_to_end(cache_key)
                return cached
        try:
            with Image.open(file_path) as image:
                if getattr(image, "is_animated", False):
                    image.seek(0)
                thumb = image.convert("RGB")
                thumb.thumbnail(size)
                buffer = BytesIO()
                thumb.save(buffer, format="JPEG", quality=quality)
        except Exception as error:
            logger.debug(f"生成缩略图失败 {file_path}: {error}")
            return None
        source = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("utf-8")
        with self._thumbnail_lock:
            self._thumbnail_cache[cache_key] = source
            while len(self._thumbnail_cache) > THUMBNAIL_CACHE_SIZE:
                self._thumbnail_cache.popitem(last=False)
        return source

    @staticmethod
    def __format_size(size_bytes: int) -> str:
        try:
            size = float(size_bytes)
        except (TypeError, ValueError):
            return "0 B"
        units = ["B", "KB", "MB", "GB"]
        for unit in units:
            if size < 1024 or unit == units[-1]:
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
            size /= 1024
        return f"{int(size_bytes)} B"

    def __get_saved_cover_dirs(self) -> List[Path]:
        result: List[Path] = []
        if self._covers_output:
            result.append(Path(self._covers_output))
        data_path = self.get_data_path()
        default_output = data_path / "output"
        result.append(default_output)
        unique: List[Path] = []
        seen = set()
        for directory in result:
            key = str(directory)
            if key in seen:
                continue
            seen.add(key)
            unique.append(directory)
        return unique

    def __resolve_saved_cover_path(self, raw_name: str) -> Optional[Path]:
        """只接受文件名，且必须是保存目录下插件生成的封面。"""
        name = Path(str(raw_name or "").strip()).name
        if not COVER_NAME_PATTERN.match(name):
            return None
        for directory in self.__get_saved_cover_dirs():
            candidate = directory / name
            if candidate.is_file():
                return candidate
        return None

    def __get_recent_cover_output_dir(self) -> Path:
        if self._covers_output:
            return Path(self._covers_output).expanduser()
        return self.get_data_path() / "output"

    @eventmanager.register(EventType.PluginAction)
    def update_covers(self, event: Event):
        """
        远程全量同步
        """
        if not event:
            return
        event_data = event.event_data
        if not event_data or event_data.get("action") != "update_covers":
            return
        channel, user = event_data.get("channel"), event_data.get("user")
        if not self._enabled:
            self.post_message(channel=channel, title="媒体库封面插件未启用", userid=user)
            return
        self.post_message(channel=channel, title="开始更新媒体库封面 ...", userid=user)

        def run():
            tips = self.__update_all_libraries(send_notification=False)
            self.post_message(channel=channel, title=tips, userid=user)

        # 全量更新可能持续很久，不占用事件线程
        threading.Thread(target=run, name="MediaCoverGenerator", daemon=True).start()

    def __schedule_monitor(self, key: str, handler) -> None:
        """
        入库事件先进入队列，延迟结束后统一处理。

        批量入库时事件线程不会被长时间占用，同一媒体的多次入库也只处理一次。
        """
        with self._monitor_lock:
            self._monitor_pending[key] = handler
            if self._monitor_timer is None:
                logger.info(f"将在 {self._delay} 秒后更新入库媒体所在媒体库的封面")
                timer = threading.Timer(self._delay, self.__flush_monitor)
                timer.daemon = True
                self._monitor_timer = timer
                timer.start()

    def __flush_monitor(self):
        with self._monitor_lock:
            handlers = list(self._monitor_pending.values())
            self._monitor_pending.clear()
            self._monitor_timer = None
        self._monitor_batch_updated = set()
        for handler in handlers:
            if not self._enabled or not self._transfer_monitor:
                return
            try:
                handler()
            except Exception as error:
                logger.error(f"入库监控处理失败: {error}", exc_info=True)

    @eventmanager.register(EventType.TransferComplete)
    def update_library_cover(self, event: Event):
        """
        媒体整理完成后，更新所在库封面
        """
        if not self._enabled or not self._transfer_monitor:
            return
        event_data = event.event_data
        if not event_data:
            return
        mediainfo: MediaInfo = event_data.get("mediainfo")
        if not mediainfo:
            return
        self.__schedule_monitor(f"transfer:{mediainfo.title_year}", lambda: self.__handle_transfer(mediainfo))

    def __handle_transfer(self, mediainfo: MediaInfo):
        try:
            # Query the item in media server
            existsinfo = self.mschain.media_exists(mediainfo=mediainfo)
            if not existsinfo or not existsinfo.itemid:
                self.mschain.sync()
                existsinfo = self.mschain.media_exists(mediainfo=mediainfo)
                if not existsinfo or not existsinfo.itemid:
                    logger.warning(f"{mediainfo.title_year} 不存在媒体库中，可能服务器还未扫描完成，建议设置合适的延迟时间")
                    return

            # Get item details including backdrop
            iteminfo = self.mschain.iteminfo(server=existsinfo.server, item_id=existsinfo.itemid)
            if not iteminfo:
                logger.warning(f"获取 {mediainfo.title_year} 详情失败")
                return

            self.__update_library_cover_item(
                server=existsinfo.server,
                item_id=existsinfo.itemid,
                iteminfo=iteminfo,
                title_year=mediainfo.title_year,
            )
        except Exception as error:
            logger.error(f"入库监控查询 {mediainfo.title_year} 失败: {error}", exc_info=True)

    @eventmanager.register(EventType.WebhookMessage)
    def update_library_cover_webhook(self, event: Event):
        """Update a library cover when Emby reports a new item via Webhook."""
        if not self._enabled or not self._transfer_monitor:
            return

        event_info = getattr(event, "event_data", None)
        if not event_info:
            return
        event_type = getattr(event_info, "event", None)
        if isinstance(event_info, dict):
            event_type = event_info.get("event")
            get_value = event_info.get
        else:
            get_value = lambda key, default=None: getattr(event_info, key, default)
        if event_type != "library.new":
            return

        server = get_value("server_name")
        item_id = get_value("item_id")
        if not server or not item_id or not self._servers or server not in self._servers:
            logger.debug("忽略缺少媒体服务器或媒体项 ID 的新入库 Webhook")
            return
        item_name = get_value("item_name")
        self.__schedule_monitor(
            f"webhook:{server}:{item_id}",
            lambda: self.__handle_webhook(server, item_id, item_name),
        )

    def __handle_webhook(self, server, item_id, item_name):
        try:
            iteminfo = self.mschain.iteminfo(server=server, item_id=item_id)
            if not iteminfo:
                logger.warning(f"Webhook 新入库媒体 {item_name or item_id} 详情获取失败")
                return

            title_year = item_name or getattr(iteminfo, "name", None) or str(item_id)
            self.__update_library_cover_item(
                server=server,
                item_id=item_id,
                iteminfo=iteminfo,
                title_year=title_year,
            )
        except Exception as error:
            logger.error(f"Webhook 入库监控更新失败: {error}", exc_info=True)

    def __update_library_cover_item(self, server, item_id, iteminfo, title_year):
        """Resolve an item to its library and update that library cover."""
        library_id = None
        library = {}
        service = self._servers.get(server) if self._servers else None
        libraries = []
        if service:
            libraries = self.__get_server_libraries(service)
        if libraries:
            # V3 MediaServerItem exposes its library ID; use it before path matching.
            item_library_id = getattr(iteminfo, "library", None)
            if isinstance(iteminfo, dict):
                item_library_id = iteminfo.get("library") or iteminfo.get("LibraryId")
            if item_library_id:
                library = next(
                    (
                        candidate for candidate in libraries
                        if str(candidate.get("Id") or candidate.get("ItemId"))
                        == str(item_library_id)
                    ),
                    None,
                )
            if not library:
                item_path = getattr(iteminfo, "path", None)
                if isinstance(iteminfo, dict):
                    item_path = iteminfo.get("path") or iteminfo.get("Path")
                library = next(
                    (
                        candidate for candidate in libraries
                        if candidate.get('Locations', [])
                        and any(
                            self.__path_is_under(item_path, path)
                            for path in candidate.get('Locations', [])
                        )
                    ),
                    None,
                )

        if not library:
            logger.warning(f"找不到 {title_year} 所在媒体库")
            return
        if service.type == 'emby':
            library_id = library.get("Id")
        else:
            library_id = library.get("ItemId")
        if self._include_libraries and f"{server}-{library_id}" not in self._include_libraries:
            logger.info(f"{server}：{library['Name']} 不在列表中，跳过更新封面")
            return

        update_key = (server, str(library_id))
        if update_key in self._monitor_batch_updated:
            # 更新时已按最新入库排序，同一批事件里每个媒体库更新一次就够了
            logger.info(f"媒体库 {server}：{library['Name']} 本批已更新，跳过 {title_year}")
            return
        if update_key in self._current_updating_items:
            logger.info(f"媒体库 {server}：{library['Name']} 的项目 {title_year} 正在更新中，跳过此次更新")
            return
        # self.clean_cover_history(save=True)
        old_history = self.get_data('cover_history') or []
        # 新增去重判断逻辑
        latest_item = max(
            (
                item for item in old_history
                if isinstance(item, dict)
                and str(item.get("server")) == str(server)
                and str(item.get("library_id")) == str(library_id)
                and item.get("timestamp") is not None
            ),
            key=lambda x: float(x.get("timestamp", 0)),
            default=None
        )
        if latest_item and str(latest_item.get("item_id")) == str(item_id):
            logger.info(f"媒体 {title_year} 在库中是最新记录，不更新封面图")
            return

        if not self._update_lock.acquire(blocking=False):
            logger.info(f"媒体库 {server} 正在执行全量更新，跳过入库监控任务 {title_year}")
            return False
        self._monitor_sort = 'DateCreated'
        self._run_style = self._cover_style
        self._current_updating_items.add(update_key)
        self._task = {"running": True, "total": 1, "done": 0, "current": f"{server}：{library['Name']}（入库更新）"}
        self._failure_reason = ""
        message = ""
        try:
            # 安全地获取字体和翻译
            try:
                self.__get_fonts()
            except Exception as e:
                logger.error(f"初始化字体时出错: {e}")
            updated = self.__update_library(service, library)
        except Exception as error:
            logger.error(f"入库监控更新媒体库 {library['Name']} 失败: {error}", exc_info=True)
            updated = False
            message = str(error)
        finally:
            self._monitor_sort = ''
            self._run_style = None
            self._current_updating_items.discard(update_key)
            self._task = {"running": False}
            # 停止信号只针对当前任务，不能留给下一次入库更新
            self._event.clear()
            self._update_lock.release()
        self.__record_library_result(server, library_id, library['Name'], bool(updated), message or self._failure_reason)
        if updated:
            self._monitor_batch_updated.add(update_key)
            self.update_cover_history(
                server=server,
                library_id=library_id,
                item_id=item_id,
            )
            logger.info(f"媒体库 {server}：{library['Name']} 封面更新成功（入库监控）")
        else:
            logger.warning(f"媒体库 {server}：{library['Name']} 封面更新失败（入库监控）")
        return updated

    def __record_library_result(self, server, library_id, library_name, ok: bool, message: str = ""):
        """记录每个媒体库最近一次的生成结果，显示在详情页。"""
        try:
            status_map = self.get_data("library_status") or {}
            if not isinstance(status_map, dict):
                status_map = {}
            variant, index = self.__get_cover_style_parts()
            status_map[f"{server}-{library_id}"] = {
                "ok": ok,
                "name": library_name,
                "time": datetime.datetime.now().strftime("%m-%d %H:%M"),
                "style": style_label(index, variant),
                "message": message,
            }
            self.save_data("library_status", status_map)
        except Exception as error:
            logger.debug(f"记录媒体库生成结果失败: {error}")

    def __update_all_libraries(self, library_key: Optional[str] = None, send_notification: bool = True):
        """
        更新媒体库封面。library_key 为空时更新全部（受「批量更新的媒体库」限制）。
        无论结果如何都返回一句结果说明。
        """
        if not self._enabled:
            return "插件未启用"
        # 所有媒体服务器
        if not self._servers:
            return "未配置媒体服务器"
        if not self._update_lock.acquire(blocking=False):
            logger.warning("已有媒体库封面更新任务正在执行，跳过本次任务")
            return "已有媒体库封面更新任务正在执行"
        logger.info("开始更新媒体库封面 ...")
        # 手动启动时信号已在接口里清除，这里再清一次会吞掉启动瞬间发出的停止请求
        if not self._task.get("running"):
            self._event.clear()
        self._run_style = self._cover_style
        self._task = {"running": True, "total": 0, "done": 0, "current": ""}
        total_success = 0
        total_fail = 0
        stopped = False
        try:
            logger.info("开始检查字体 ...")
            try:
                self.__get_fonts()
            except Exception as e:
                logger.error(f"初始化过程中出错: {e}")
                logger.warning("将尝试继续执行，但可能影响封面生成质量")

            variant, index = self.__get_cover_style_parts()
            logger.info(f"当前风格 {style_label(index, variant)}")
            targets = []
            for server, service in self._servers.items():
                libraries = self.__get_server_libraries(service)
                if not libraries:
                    logger.warning(f"服务器 {server} 的媒体库列表获取失败")
                    continue
                for library in libraries:
                    library_id = library.get("Id") if service.type == 'emby' else library.get("ItemId")
                    key = f"{server}-{library_id}"
                    if library_key:
                        if key != library_key:
                            continue
                    elif not self.__in_batch(key, library.get("CollectionType")):
                        logger.info(f"{server}：{library['Name']} 不在列表中，跳过更新封面")
                        continue
                    targets.append((server, service, library, library_id))
            self._task["total"] = len(targets)

            for server, service, library, library_id in targets:
                if self._event.is_set():
                    stopped = True
                    break
                self._task["current"] = f"{server}：{library['Name']}"
                self._failure_reason = ""
                message = ""
                try:
                    updated = self.__update_library(service, library)
                except Exception as error:
                    logger.error(f"媒体库 {server}：{library['Name']} 更新异常: {error}", exc_info=True)
                    updated = False
                    message = str(error)
                if self._event.is_set() and not updated:
                    stopped = True
                    break
                self.__record_library_result(server, library_id, library['Name'], bool(updated),
                                             message or self._failure_reason)
                if updated:
                    logger.info(f"媒体库 {server}：{library['Name']} 封面更新成功")
                    total_success += 1
                else:
                    logger.warning(f"媒体库 {server}：{library['Name']} 封面更新失败")
                    total_fail += 1
                self._task["done"] = total_success + total_fail
        finally:
            if stopped:
                tips = f"封面更新已停止，已完成 {total_success + total_fail} 个媒体库"
            elif total_success + total_fail == 0:
                tips = "没有需要更新的媒体库"
            else:
                tips = f"封面更新结束：成功 {total_success} 个，失败 {total_fail} 个"
            logger.info(tips)
            # 先写结果再释放锁，页面看到任务结束时一定能读到这一轮的结果
            try:
                self.save_data("last_run", {
                    "time": datetime.datetime.now().strftime("%m-%d %H:%M"),
                    "message": tips,
                })
            except Exception as error:
                logger.debug(f"记录运行结果失败: {error}")
            self._monitor_sort = ''
            self._run_style = None
            self._current_updating_items.clear()
            self._event.clear()
            self._task = {"running": False}
            self._update_lock.release()

        if send_notification and self._notify:
            self.post_message(mtype=NotificationType.Plugin, title="媒体库封面更新", text=tips)
        return tips

    def __in_batch(self, key: str, library_type: Optional[str]) -> bool:
        """
        判断媒体库是否参与批量更新。

        电视直播的封面常常是手动设置的，而自动取到的多半只是台标，
        所以「全部媒体库」不包含它，需要单独勾选或单独点生成。
        """
        if self._include_libraries:
            return key in self._include_libraries
        return library_type != LIVE_TV_TYPE

    def __update_library(self, service, library):
        library_name = library['Name']
        logger.info(f"媒体库 {service.name}：{library_name} 开始准备更新封面")
        # 从配置获取标题和背景颜色
        zh_title, en_title, config_bg_color = self.__get_title_from_config(library_name)
        title = (zh_title, en_title)
        custom_images = self.__check_custom_image(library_name)
        if custom_images:
            logger.info(f"媒体库 {service.name}：{library_name} 从自定义路径获取封面")
            if self.__is_single_image_style():
                image_path = random.choice(custom_images)
            else:
                self.__stage_custom_images(library_name, custom_images)
                image_path = None
            image_data = self.__generate_image_from_path(service.name, library_name, title, image_path, config_bg_color)
        else:
            image_data = self.__generate_from_server(service, library, title)

        if image_data:
            return self.__set_library_image(service, library, image_data)
        return False

    def __check_custom_image(self, library_name):
        if not self._covers_input:
            return None

        # 使用安全的文件名
        safe_library_name = self.__sanitize_filename(library_name)
        library_dir = os.path.join(self._covers_input, safe_library_name)
        if not os.path.isdir(library_dir):
            return None

        images = sorted([
            os.path.join(library_dir, f)
            for f in os.listdir(library_dir)
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"))
        ])

        return images if images else None

    def __library_work_dir(self, library_name) -> Path:
        return Path(self._covers_path) / self.__sanitize_filename(library_name)

    @staticmethod
    def __clear_numbered_images(directory: Path):
        """清除上一轮的编号素材，避免本轮数量不足时混入旧图。"""
        if not directory.is_dir():
            return
        for entry in directory.iterdir():
            if entry.is_file() and NUMBERED_IMAGE_PATTERN.match(entry.name):
                entry.unlink(missing_ok=True)

    def __stage_custom_images(self, library_name, images: List[str]):
        """把自定义图片随机抽样复制到工作目录，不改动用户的目录。"""
        work_dir = self.__library_work_dir(library_name)
        work_dir.mkdir(parents=True, exist_ok=True)
        self.__clear_numbered_images(work_dir)
        required = self.__get_required_items()
        chosen = random.sample(images, min(required, len(images)))
        for index, source in enumerate(chosen, start=1):
            shutil.copy(source, work_dir / f"{index}.jpg")

    def __font_supports_text(self, font_path, text: str) -> bool:
        """判断字体是否包含文字里的中日韩字符；缺字时 PIL 会绘制 .notdef 方框。"""
        characters = [char for char in str(text or "") if ord(char) >= 0x2E80][:16]
        if not characters:
            return True
        try:
            font = ImageFont.truetype(str(font_path), 48)
            missing = font.getmask("\U0010FFFF")
            missing_key = (missing.size, bytes(missing))
            return not all((font.getmask(char).size, bytes(font.getmask(char))) == missing_key
                           for char in characters)
        except Exception as error:
            logger.debug(f"检查字体字符集失败 {font_path}: {error}")
            return True

    def __resolve_title_fonts(self, title) -> Tuple[str, str]:
        """标题含中文而所选字体不支持时，换成能显示的字体，避免出现方框。"""
        zh_font, en_font = str(self._zh_font_path), str(self._en_font_path)
        main_text, sub_text = title
        # 粗书体的英文字形很细，纯英文的主标题用副标题字体更好看
        if (self._zh_font_preset == DEFAULT_ZH_FONT and not self._zh_font_custom
                and str(main_text or "").strip()
                and not any(ord(char) >= 0x2E80 for char in str(main_text))):
            zh_font = en_font
        if not self.__font_supports_text(zh_font, main_text):
            logger.warning(f"主标题字体不支持「{main_text}」，已改用副标题字体")
            if self.__font_supports_text(en_font, main_text):
                zh_font = en_font
        if not self.__font_supports_text(en_font, sub_text):
            if self.__font_supports_text(zh_font, sub_text):
                logger.info(f"副标题字体不支持「{sub_text}」，已改用主标题字体")
                en_font = zh_font
        return zh_font, en_font

    @memory_efficient_operation
    def __generate_image_from_path(self, server, library_name, title, image_path=None, config_bg_color=None):
        logger.info(f"媒体库 {server}：{library_name} 正在生成封面图 ...")

        # 执行健康检查
        if not self.health_check():
            logger.error("插件健康检查失败，无法生成封面")
            return False

        style = self.__current_style()
        is_animated = style in ANIMATED_STYLES
        base_zh_font_size = self.__to_number(self._zh_font_size, DEFAULT_ZH_FONT_SIZE, float) or DEFAULT_ZH_FONT_SIZE
        base_en_font_size = self.__to_number(self._en_font_size, DEFAULT_EN_FONT_SIZE, float) or DEFAULT_EN_FONT_SIZE
        title_scale = self.__to_number(self._title_scale, 1.0, float)
        if title_scale <= 0:
            title_scale = 1.0
        if is_animated:
            # 动态风格按自身画布换算，这里传 1080p 基准值
            zh_font_size = base_zh_font_size * title_scale
            en_font_size = base_en_font_size * title_scale
        else:
            zh_font_size = self._resolution_config.get_font_size(base_zh_font_size) * title_scale
            en_font_size = self._resolution_config.get_font_size(base_en_font_size) * title_scale

        # 0 是合法取值，只有未填写时才使用默认值
        blur_size = 50 if self._blur_size is None else self._blur_size
        color_ratio = 0.8 if self._color_ratio is None else self._color_ratio

        # 检查字体路径是否有效
        if not self._zh_font_path or not self._en_font_path:
            logger.error("字体路径未设置或无效，无法生成封面")
            return False

        # 验证字体文件是否存在
        if not validate_font_file(Path(self._zh_font_path)):
            logger.error(f"主标题字体文件无效: {self._zh_font_path}")
            return False

        if not validate_font_file(Path(self._en_font_path)):
            logger.error(f"副标题字体文件无效: {self._en_font_path}")
            return False

        font_path = self.__resolve_title_fonts(title)
        font_size = (float(zh_font_size), float(en_font_size))

        zh_font_offset = self.__to_number(self._zh_font_offset, 0.0, float)
        title_spacing = self.__to_number(self._title_spacing, DEFAULT_TITLE_SPACING, float) * title_scale
        en_line_spacing = self.__to_number(self._en_line_spacing, 40.0, float) * title_scale
        zh_letter_spacing = max(0.0, self.__to_number(self._zh_letter_spacing, DEFAULT_ZH_LETTER_SPACING, float))
        en_letter_spacing = max(0.0, self.__to_number(self._en_letter_spacing, DEFAULT_EN_LETTER_SPACING, float))
        font_offset = (zh_font_offset, title_spacing, en_line_spacing,
                       zh_letter_spacing / 100.0, en_letter_spacing / 100.0)

        logger.info(f"当前分辨率配置: {self._resolution_config}")

        common = {
            "font_size": font_size,
            "font_offset": font_offset,
            "blur_size": blur_size,
            "color_ratio": color_ratio,
            "resolution_config": self._resolution_config,
            "bg_color_config": {
                'mode': self._bg_color_mode,
                'custom_color': self._custom_bg_color,
                'config_color': config_bg_color,
            },
        }

        if style in SINGLE_IMAGE_STYLES:
            return SINGLE_IMAGE_STYLES[style](image_path, title, font_path, **common)

        library_dir = self.__library_work_dir(library_name)
        required = 9 if style in NINE_IMAGE_STYLES else self.__get_animated_2_required_items()
        if not self.prepare_library_images(library_dir, required_items=required):
            logger.warning(f"素材目录准备失败 {library_dir}")
            return False

        common["is_blur"] = self._multi_1_blur
        if style in MULTI_IMAGE_STYLES:
            return MULTI_IMAGE_STYLES[style](library_dir, title, font_path, **common)

        common.update(
            animation_duration=self._animation_duration,
            animation_fps=self._animation_fps,
            animation_format=self._animation_format,
            animation_resolution='320x180',
            animation_reduce_colors=self._animation_reduce_colors,
            stop_event=self._event,
        )
        if style == "animated_3":
            common["animation_scroll"] = self._animation_scroll
        else:
            common["image_count"] = required if style == "animated_7" else self.__get_animated_2_required_items()
        if style == "animated_1":
            common["departure_type"] = self._animated_2_departure_type
        return ANIMATED_STYLES[style](library_dir, title, font_path, **common)

    def __generate_from_server(self, service, library, title):

        logger.info(f"媒体库 {service.name}：{library['Name']} 开始筛选媒体项")
        required_items = self.__get_required_items()

        # 获取项目集合
        items = []
        offset = 0
        batch_size = 50  # 每次获取的项目数量
        max_attempts = 20  # 最大尝试次数，防止无限循环

        library_type = library.get('CollectionType')
        if service.type == 'emby':
            library_id = library.get("Id")
        else:
            library_id = library.get("ItemId")
        parent_id = library_id

        # 处理合集类型的特殊情况
        if library_type == "boxsets":
            return self.__handle_boxset_library(service, library, title)
        elif library_type == "playlists":
            return self.__handle_playlist_library(service, library, title)
        elif library_type == LIVE_TV_TYPE:
            return self.__handle_live_tv_library(service, library, title)
        elif library_type == "music":
            include_types = 'MusicAlbum,Audio'
        else:
            # 基础类型映射
            if self.__is_single_image_style():
                include_types = {
                    "PremiereDate": "Movie,Series",
                    "DateCreated": "Movie,Episode",
                    "Random": "Movie,Series"
                }.get(self._sort_by, "Movie,Series")
            else:
                # 对于多图样式，如果按最新入库排序（DateCreated），也要包含 Episode 以展示剧集的最新动态
                if self._sort_by == "DateCreated":
                    include_types = "Movie,Episode"
                else:
                    # 其他排序方式默认使用 Series 获取海报
                    include_types = "Movie,Series"
            logger.debug(f"媒体库筛选类型: {include_types}, 排序方式: {self._sort_by}")
        self._seen_keys = set()
        for attempt in range(max_attempts):
            if self._event.is_set():
                logger.info("检测到停止信号，中断媒体项获取 ...")
                return False

            batch_items = self.__get_items_batch(service, parent_id,
                                              offset=offset, limit=batch_size,
                                              include_types=include_types)

            if not batch_items:
                break  # 没有更多项目可获取

            # 筛选有效项目（有所需图片的项目）
            valid_items = self.__filter_valid_items(batch_items)
            items.extend(valid_items)

            # 如果已经有足够的有效项目，则停止获取
            if len(items) >= required_items:
                break

            offset += batch_size

        # 使用获取到的有效项目更新封面
        if len(items) > 0:
            logger.info(f"媒体库 {service.name}：{library['Name']} 找到 {len(items)} 个有效项目")
            if self.__is_single_image_style():
                return self.__update_single_image(service, library, title, items[0])
            else:
                return self.__update_grid_image(service, library, title, items[:required_items])
        else:
            self._failure_reason = "媒体库里没有可用的图片"
            logger.warning(f"媒体库 {service.name}：{library['Name']} 无法找到有效的图片项目 (筛选类型: {include_types})")
            return False

    def __handle_live_tv_library(self, service, library, title):
        """电视直播优先用正在播出节目的海报，没有时用频道图片（通常是台标）。"""
        required_items = self.__get_required_items()
        self._seen_keys = set()
        valid_items = []
        common = 'api_key=[APIKEY]&UserId=[USER]&EnableImages=true&Limit=100'
        for url in (
            f'[HOST]emby/LiveTv/Programs?{common}&IsAiring=true',
            f'[HOST]emby/LiveTv/Channels?{common}',
        ):
            if self._event.is_set():
                return False
            try:
                res = service.instance.get_data(url=url)
                items = res.json().get("Items", []) if res else []
            except Exception as err:
                logger.warning(f"获取电视直播内容失败：{str(err)}")
                items = []
            random.shuffle(items)
            valid_items.extend(self.__filter_valid_items(items))
            if len(valid_items) >= required_items:
                break

        if not valid_items:
            self._failure_reason = "没有找到节目或频道图片，可以使用自定义图片目录"
            logger.warning(f"媒体库 {service.name}：{library['Name']} {self._failure_reason}")
            return False
        if self.__is_single_image_style():
            return self.__update_single_image(service, library, title, valid_items[0])
        return self.__update_grid_image(service, library, title, valid_items[:required_items])

    def __handle_boxset_library(self, service, library, title):

        include_types = 'BoxSet,Movie'
        if service.type == 'emby':
            library_id = library.get("Id")
        else:
            library_id = library.get("ItemId")
        parent_id = library_id
        boxsets = self.__get_items_batch(service, parent_id,
                                      include_types=include_types)

        required_items = self.__get_required_items()
        valid_items = []

        # 首先检查BoxSet本身是否有合适的图片
        self._seen_keys = set()

        valid_boxsets = self.__filter_valid_items(boxsets)
        valid_items.extend(valid_boxsets)

        # 如果BoxSet本身没有足够的图片，则获取其中的电影
        if len(valid_items) < required_items:
            for boxset in boxsets:
                if len(valid_items) >= required_items:
                    break

                # 获取此BoxSet中的电影
                movies = self.__get_items_batch(service,
                                             parent_id=boxset['Id'],
                                             include_types=include_types)

                valid_movies = self.__filter_valid_items(movies)
                valid_items.extend(valid_movies)

                if len(valid_items) >= required_items:
                    break

        # 使用获取到的有效项目更新封面
        if len(valid_items) > 0:
            if self.__is_single_image_style():
                return self.__update_single_image(service, library, title, valid_items[0])
            else:
                return self.__update_grid_image(service, library, title, valid_items[:required_items])
        else:
            logger.warning(f"媒体库 {service.name}：{library['Name']} 无法找到有效的图片项目")
            return False

    def __handle_playlist_library(self, service, library, title):
        """
        播放列表图片获取
        """
        include_types = 'Playlist,Movie,Series,Episode,Audio'
        if service.type == 'emby':
            library_id = library.get("Id")
        else:
            library_id = library.get("ItemId")
        parent_id = library_id
        playlists = self.__get_items_batch(service, parent_id,
                                      include_types=include_types)

        required_items = self.__get_required_items()
        valid_items = []

        # 首先检查 playlist 本身是否有合适的图片
        self._seen_keys = set()

        valid_playlists = self.__filter_valid_items(playlists)
        valid_items.extend(valid_playlists)

        # 如果 playlist 本身没有足够的图片，则获取其中的电影
        if len(valid_items) < required_items:
            for playlist in playlists:
                if len(valid_items) >= required_items:
                    break

                # 获取此 playlist 中的电影
                movies = self.__get_items_batch(service,
                                             parent_id=playlist['Id'],
                                             include_types=include_types)

                valid_movies = self.__filter_valid_items(movies)
                valid_items.extend(valid_movies)

                if len(valid_items) >= required_items:
                    break

        # 使用获取到的有效项目更新封面
        if len(valid_items) > 0:
            if self.__is_single_image_style():
                return self.__update_single_image(service, library, title, valid_items[0])
            else:
                return self.__update_grid_image(service, library, title, valid_items[:required_items])
        else:
            logger.warning(f"无法为播放列表 {service.name}：{library['Name']} 找到有效的图片项目")
            return False

    def __get_items_batch(self, service, parent_id, offset=0, limit=20, include_types=None):
        # 调用API获取项目
        try:
            if not service:
                return []

            sort_by = self._sort_by or 'Random'
            if not include_types:
                include_types = 'Movie,Series'
            if self._monitor_sort:
                sort_by = 'DateCreated'
                # 入库更新时包含 Episode 以获取最新内容；音乐、合集、播放列表保持原有类型
                if include_types in ('Movie,Series', 'Movie,Episode'):
                    include_types = 'Movie,Episode'

            url = f'[HOST]emby/Items/?api_key=[APIKEY]' \
                  f'&ParentId={parent_id}&SortBy={sort_by}&Limit={limit}' \
                  f'&StartIndex={offset}&IncludeItemTypes={include_types}' \
                  f'&Recursive=True&SortOrder=Descending'

            res = service.instance.get_data(url=url)
            if res:
                return res.json().get("Items", [])
            return []
        except Exception as err:
            logger.error(f"获取媒体项失败：{str(err)}")
            return []

    def __filter_valid_items(self, items):
        """筛选有效的项目（包含所需图片的项目），并按图片标签去重"""
        valid_items = []

        for item in items:
            # 1) 根据当前样式计算真实会使用的图片URL
            image_url = self.__get_image_url(item)
            if not image_url:
                continue

            # 2) 两层去重：
            #    - content_key: 内容层（如同一剧集的多集使用同一Series图）
            #    - image_key:   图片层（同一图片tag或同一路径）
            content_key = self.__build_content_key(item)
            image_key = self.__build_image_key(image_url)

            if not content_key and not image_key:
                continue

            if (content_key and content_key in self._seen_keys) or (image_key and image_key in self._seen_keys):
                continue

            # 3) 加入有效列表并记录已处理的 Key
            valid_items.append(item)
            if content_key:
                self._seen_keys.add(content_key)
            if image_key:
                self._seen_keys.add(image_key)

        return valid_items

    def __build_content_key(self, item: dict) -> Optional[str]:
        """构建内容去重Key，尽量让同一来源内容只入选一次。"""
        item_type = item.get("Type")

        if item_type == "Episode":
            if item.get("SeriesId"):
                return f"series:{item.get('SeriesId')}"
            if item.get("ParentBackdropItemId"):
                return f"parent:{item.get('ParentBackdropItemId')}"

        if item_type in ["MusicAlbum", "Audio"]:
            if item.get("AlbumId"):
                return f"album:{item.get('AlbumId')}"
            if item.get("ParentBackdropItemId"):
                return f"parent:{item.get('ParentBackdropItemId')}"

        if item.get("Id"):
            return f"item:{item.get('Id')}"

        return None

    def __build_image_key(self, image_url: str) -> Optional[str]:
        """构建图片去重Key，忽略api_key，避免同图重复。"""
        if not image_url:
            return None

        try:
            # 统一移除 api_key 参数，避免同图不同密钥导致重复
            normalized = re.sub(r"([?&])api_key=[^&]*", "", image_url).rstrip("?&")

            # 优先用路径 + tag 作为去重关键字（能精准区分图像版本）
            # 例如: /Items/{id}/Images/Backdrop/0?tag=xxx
            tag_match = re.search(r"[?&]tag=([^&]+)", image_url)
            tag = tag_match.group(1) if tag_match else ""

            parsed = urlparse(normalized)
            path = parsed.path if parsed.path else normalized
            return f"img:{path}|tag:{tag}"
        except Exception:
            return f"img:{image_url}"



    def __update_single_image(self, service, library, title, item):
        """更新单图封面"""
        logger.info(f"媒体库 {service.name}：{library['Name']} 从媒体项获取图片")
        updated_item_id = ''
        image_url = self.__get_image_url(item)
        if not image_url:
            return False

        image_path = self.__download_image(service, image_url, library['Name'], count=1)
        if not image_path:
            return False
        updated_item_id = self.__get_item_id(item)
        # 从配置获取背景颜色
        title_result = self.__get_title_from_config(library['Name'])
        config_bg_color = title_result[2] if len(title_result) == 3 else None
        image_data = self.__generate_image_from_path(service.name, library['Name'], title, image_path, config_bg_color)

        if not image_data:
            return False
        if service.type == 'emby':
            library_id = library.get("Id")
        else:
            library_id = library.get("ItemId")
        # 更新id
        if updated_item_id:
            self.update_cover_history(
                server=service.name,
                library_id=library_id,
                item_id=updated_item_id
            )

        return image_data

    def __update_grid_image(self, service, library, title, items):
        """更新九宫格封面"""
        logger.info(f"媒体库 {service.name}：{library['Name']} 从媒体项获取图片")

        image_paths = []

        updated_item_ids = []
        self.__clear_numbered_images(self.__library_work_dir(library['Name']))
        for i, item in enumerate(items):
            if self._event.is_set():
                logger.info("检测到停止信号，中断图片下载 ...")
                return False
            image_url = self.__get_image_url(item)
            if image_url:
                image_path = self.__download_image(service, image_url, library['Name'], count=i+1)
                if image_path:
                    image_paths.append(image_path)
                    item_id = self.__get_item_id(item)
                    if item_id:
                        updated_item_ids.append(item_id)

        if len(image_paths) < 1:
            return False

        # 生成九宫格图片
        # 从配置获取背景颜色
        title_result = self.__get_title_from_config(library['Name'])
        config_bg_color = title_result[2] if len(title_result) == 3 else None
        image_data = self.__generate_image_from_path(service.name, library['Name'], title, None, config_bg_color)
        if not image_data:
            return False
        if service.type == 'emby':
            library_id = library.get("Id")
        else:
            library_id = library.get("ItemId")
        # 更新ids
        for item_id in reversed(updated_item_ids):
            self.update_cover_history(
                server=service.name,
                library_id=library_id,
                item_id=item_id
            )

        return image_data

    def __load_title_config(self, yaml_str: str) -> dict:
        try:
            # 先处理转义换行，再处理真实换行
            yaml_str = yaml_str.replace('\\r\\n', '\n')
            yaml_str = yaml_str.replace('\\r', '\n')
            yaml_str = yaml_str.replace('\\n', '\n')
            yaml_str = yaml_str.replace('\r\n', '\n')
            yaml_str = yaml_str.replace('\r', '\n')
            # 替换制表符为两个空格，统一缩进
            yaml_str = yaml_str.replace("\t", "  ")

            # 只调整顶格的「媒体库名:」行。列表项里的标题可以包含冒号，不能当作键处理
            processed_lines = []
            for line in yaml_str.split('\n'):
                if line and not line[0].isspace() and not line.startswith(('#', '-')):
                    normalized = line.replace("：", ":", 1) if ":" not in line else line
                    key_part, separator, value_part = normalized.partition(':')
                    key_part = key_part.strip()
                    if separator and key_part and key_part[0] not in "\"'":
                        # 数字开头或含特殊字符的媒体库名加引号，确保被解析为字符串
                        if key_part[0].isdigit() or any(char in key_part for char in " -.()[]"):
                            key_part = json.dumps(key_part, ensure_ascii=False)
                        line = f"{key_part}:{value_part}"
                processed_lines.append(line)

            title_config = yaml.safe_load('\n'.join(processed_lines)) or {}
            if not isinstance(title_config, dict):
                self._title_config_error = "标题配置应为「媒体库名称: 标题列表」的形式"
                return {}
            filtered = {}
            for key, value in title_config.items():
                if isinstance(value, list) and len(value) >= 2:
                    # YAML 会把纯数字解析为数字，这里统一转成字符串
                    texts = ["" if part is None else str(part) for part in value[:3]]
                    filtered[str(key)] = texts
                    if len(value) > 3:
                        logger.info(f"配置项 {key} 包含多行，只使用前三行")
                else:
                    logger.warning(f"标题配置项格式不正确，已忽略: {key} -> {value}")
            self._title_config_error = ""
            return filtered
        except Exception as e:
            # 整体 YAML 无法解析（比如语法错误），返回空配置
            logger.warning(f"YAML 解析失败，使用空配置: {e}")
            self._title_config_error = str(e).split("\n")[0]
            return {}

    def __get_title_from_config(self, library_name):
        """
        从 yaml 配置中获取媒体库的主副标题和背景颜色
        """
        title_config = self.__load_title_config(self._title_config) if self._title_config else {}
        target = str(library_name).strip()
        match = title_config.get(str(library_name))
        if match is None:
            for name, values in title_config.items():
                if name.strip().lower() == target.lower():
                    match = values
                    break
        if match is None:
            return library_name, '', None
        return (
            match[0] or library_name,
            match[1] if len(match) > 1 else '',
            match[2] if len(match) > 2 and match[2] else None,
        )

    def __get_server_libraries(self, service):
        if not service:
            return []
        libraries = []
        try:
            if service.type == 'emby':
                url = f'[HOST]emby/Library/VirtualFolders/Query?api_key=[APIKEY]'
            else:
                url = f'[HOST]emby/Library/VirtualFolders/?api_key=[APIKEY]'
            res = service.instance.get_data(url=url)
            if res:
                data = res.json()
                libraries = data.get("Items", []) if service.type == 'emby' else data
        except Exception as err:
            logger.error(f"获取媒体库列表失败：{str(err)}")
        return [*libraries, *self.__get_live_tv_views(service, libraries)]

    def __get_live_tv_views(self, service, libraries) -> List[Dict[str, Any]]:
        """电视直播不是虚拟文件夹，VirtualFolders 接口查不到，要从用户视图里补上。"""
        try:
            res = service.instance.get_data(url='[HOST]emby/Users/[USER]/Views?api_key=[APIKEY]')
            if not res:
                return []
            known_ids = {str(library.get("Id") or library.get("ItemId")) for library in libraries}
            views = []
            for item in res.json().get("Items", []):
                item_id = item.get("Id")
                if item.get("CollectionType") != LIVE_TV_TYPE or not item_id or str(item_id) in known_ids:
                    continue
                views.append({
                    "Name": item.get("Name") or "电视直播",
                    "Id": item_id,
                    "ItemId": item_id,
                    "CollectionType": LIVE_TV_TYPE,
                    "Locations": [],
                })
            return views
        except Exception as err:
            # 视图只用于补充电视直播，失败时不影响普通媒体库
            logger.debug(f"获取电视直播视图失败：{str(err)}")
            return []

    @staticmethod
    def __normalize_library_path(path):
        """Normalize Emby paths so UNC and local path representations can match."""
        if path is None:
            return ""

        normalized = str(path).strip().replace("\\", "/")
        if normalized.startswith("//"):
            # Emby may return //server/share/... while VirtualFolders uses /share/...
            unc_parts = [part for part in normalized[2:].split("/") if part]
            normalized = "/" + "/".join(unc_parts[1:]) if len(unc_parts) > 1 else "/"

        normalized = re.sub(r"/+", "/", normalized)
        if len(normalized) > 1:
            normalized = normalized.rstrip("/")
        return normalized

    @classmethod
    def __path_is_under(cls, item_path, library_path):
        """Return whether an item belongs to a library directory."""
        item_path = cls.__normalize_library_path(item_path)
        library_path = cls.__normalize_library_path(library_path)
        if not item_path or not library_path:
            return False
        return item_path == library_path or item_path.startswith(library_path + "/")

    def __get_all_libraries(self, server, service):
        try:
            lib_items = []
            libraries = self.__get_server_libraries(service)
            for library in libraries:
                if service.type == 'emby':
                    library_id = library.get("Id")
                else:
                    library_id = library.get("ItemId")
                if library['Name'] and library_id:
                    lib_item = {
                        "name": f"{server}: {library['Name']}",
                        "value": f"{server}-{library_id}",
                        "server": server,
                        "library": library['Name'],
                        "type": library.get("CollectionType") or "",
                    }
                    lib_items.append(lib_item)
            return lib_items
        except Exception as err:
            logger.error(f"获取所有媒体库失败：{str(err)}")
            return []

    def __image_candidates(self, item) -> List[Tuple[Any, str, Any]]:
        """按优先级列出条目可用的图片：(条目 ID, 图片路径, tag)。"""
        item_type = item.get("Type")
        parent_tags = item.get("ParentBackdropImageTags") or []
        own_backdrops = item.get("BackdropImageTags") or []
        primary_tag = (item.get("ImageTags") or {}).get("Primary")

        parent_backdrop = (item.get("ParentBackdropItemId"), "Backdrop/0", parent_tags[0] if parent_tags else None)
        own_backdrop = (item.get("Id"), "Backdrop/0", own_backdrops[0] if own_backdrops else None)
        own_primary = (item.get("Id"), "Primary", primary_tag)
        series_primary = (item.get("SeriesId"), "Primary", item.get("SeriesPrimaryImageTag"))

        if item_type in ("MusicAlbum", "Audio"):
            order = [
                parent_backdrop,
                (item.get("PrimaryImageItemId"), "Primary", item.get("PrimaryImageTag")),
                (item.get("AlbumId"), "Primary", item.get("AlbumPrimaryImageTag")),
                own_primary,
                own_backdrop,
            ]
        elif item_type == "Episode":
            preferred = [series_primary, parent_backdrop] if self._use_primary else [parent_backdrop, series_primary]
            order = [*preferred, own_primary, own_backdrop]
        elif self._use_primary:
            order = [own_primary, parent_backdrop, own_backdrop]
        else:
            order = [parent_backdrop, own_backdrop, own_primary]
        return [candidate for candidate in order if candidate[0] and candidate[2]]

    def __get_image_url(self, item):
        """
        从媒体项信息中获取图片URL
        """
        candidates = self.__image_candidates(item)
        if not candidates:
            return None
        item_id, image_path, tag = candidates[0]
        return f'[HOST]emby/Items/{item_id}/Images/{image_path}?tag={tag}&api_key=[APIKEY]'

    def __get_item_id(self, item):
        """
        从媒体项信息中获取项目ID
        """
        candidates = self.__image_candidates(item)
        return candidates[0][0] if candidates else None

    def __download_image(self, service, imageurl, library_name, count=None, retries=3, delay=1):
        """
        下载图片到 self._covers_path/library_name/，文件名为 {count}.jpg。
        只有通过校验的图片才会落盘，媒体服务器返回错误页时不会污染素材目录。
        """
        try:
            subdir = self.__library_work_dir(library_name)
            subdir.mkdir(parents=True, exist_ok=True)
            filename = f"{count}.jpg" if count is not None else f"img_{int(time.time())}.jpg"
            filepath = subdir / filename
            temp_path = subdir / f"{filename}.part"

            for attempt in range(1, retries + 1):
                if '[HOST]' in imageurl:
                    if not service:
                        return None
                    response = service.instance.get_data(url=imageurl)
                else:
                    response = RequestUtils().get_res(url=imageurl)

                if response is not None and response.status_code == 200 and response.content:
                    temp_path.write_bytes(response.content)
                    try:
                        with Image.open(temp_path) as image:
                            image.verify()
                        temp_path.replace(filepath)
                        return str(filepath)
                    except Exception as error:
                        logger.warning(f"下载到的图片无法解析：{error}")
                        temp_path.unlink(missing_ok=True)

                logger.warning(f"第 {attempt} 次尝试下载失败：{imageurl}")
                if attempt < retries:
                    time.sleep(delay)

            logger.error(f"图片下载失败（重试 {retries} 次）：{imageurl}")
            return None

        except Exception as err:
            logger.error(f"下载图片异常：{str(err)}")
            return None

    def __save_image_to_local(self, image_content, server_name: str, library_name: str, extension: str):
        """
        保存图片到本地路径
        """
        try:
            if not self._save_recent_covers:
                return
            # 确保目录存在
            local_path = self.__get_recent_cover_output_dir()
            local_path.mkdir(parents=True, exist_ok=True)

            safe_library = self.__sanitize_filename(library_name) or "library"
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
            ext = extension.strip(".").lower() if extension else "jpg"
            file_path = local_path / f"{timestamp}__{safe_library}.{ext}"
            file_path.write_bytes(image_content)
            logger.info(f"图片已保存到本地: {file_path}")
            self.__trim_saved_cover_history(local_path)
        except Exception as err:
            logger.error(f"保存图片到本地失败: {str(err)}")

    def __trim_saved_cover_history(self, local_path: Path):
        """按媒体库分别保留最新的若干张，只处理插件生成的文件。"""
        limit = self.__clamp_value(
            self._covers_history_limit_per_library,
            1,
            100,
            10,
            "covers_history_limit_per_library[trim]",
            int,
        )
        grouped: Dict[str, List[Path]] = defaultdict(list)
        try:
            for file_path in Path(local_path).iterdir():
                match = COVER_NAME_PATTERN.match(file_path.name)
                if match and file_path.is_file():
                    grouped[match.group(7) or ""].append(file_path)
            for files in grouped.values():
                if len(files) <= limit:
                    continue
                files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
                for old_file in files[limit:]:
                    old_file.unlink(missing_ok=True)
                    logger.info(f"已按历史数量限制删除旧封面: {old_file}")
        except Exception as e:
            logger.warning(f"清理历史封面失败: {e}")


    def __set_library_image(self, service, library, image_base64):
        """
        设置媒体库封面
        """

        """设置Emby媒体库封面"""
        try:
            if service.type == 'emby':
                library_id = library.get("Id")
            else:
                library_id = library.get("ItemId")

            url = f'[HOST]emby/Items/{library_id}/Images/Primary?api_key=[APIKEY]'
            # 根据 base64 前几个字节简单判断格式
            content_type = "image/png"
            extension = "png"
            if image_base64.startswith("R0lG"):
                content_type = "image/gif"
                extension = "gif"
            elif image_base64.startswith("UklG"):
                content_type = "image/webp"
                extension = "webp"
            elif image_base64.startswith("iVBOR"):
                content_type = "image/png"
                extension = "png"
            elif image_base64.startswith("/9j/"):
                content_type = "image/jpeg"
                extension = "jpg"

            # 在发送前保存一份图片到本地
            if self._save_recent_covers:
                try:
                    image_bytes = base64.b64decode(image_base64)
                    self.__save_image_to_local(image_bytes, service.name, library['Name'], extension)
                except Exception as save_err:
                    logger.error(f"保存发送前图片失败: {str(save_err)}")

            res = service.instance.post_data(
                url=url,
                data=image_base64,
                headers={
                    "Content-Type": content_type
                }
            )

            if res and res.status_code in [200, 204]:
                return True
            else:
                status_code = res.status_code if res is not None else '无响应'
                self._failure_reason = f"媒体服务器拒绝了封面上传（{status_code}）"
                logger.error(f"设置「{library['Name']}」封面失败，错误码：{status_code}")
                return False
        except Exception as err:
            logger.error(f"设置「{library['Name']}」封面失败：{str(err)}")
        return False

    def clean_cover_history(self, save=True):
        history = self.get_data('cover_history') or []
        cleaned = []

        for item in history:
            try:
                cleaned_item = {
                    "server": item["server"],
                    "library_id": str(item["library_id"]),
                    "item_id": str(item["item_id"]),
                    "timestamp": float(item["timestamp"])
                }
                cleaned.append(cleaned_item)
            except (KeyError, ValueError, TypeError):
                # 如果字段缺失或格式错误则跳过该项
                continue

        if save:
            self.save_data('cover_history', cleaned)

        return cleaned


    def update_cover_history(self, server, library_id, item_id):
        now = time.time()
        item_id = str(item_id)
        library_id = str(library_id)

        history_item = {
            "server": server,
            "library_id": library_id,
            "item_id": item_id,
            "timestamp": now
        }

        # 用于分组管理：(server, library_id) => list of items；跳过字段不完整的旧记录
        grouped = defaultdict(list)
        for item in self.clean_cover_history(save=False):
            key = (item["server"], item["library_id"])
            grouped[key].append(item)
        key = (server, library_id)
        items = grouped[key]

        # 查找是否已有该 item_id
        existing = next((i for i in items if str(i["item_id"]) == item_id), None)

        if existing:
            # 若已存在且是最新的，跳过
            if existing["timestamp"] >= max(i["timestamp"] for i in items):
                return
            else:
                existing["timestamp"] = now
        else:
            items.append(history_item)

        # 排序 + 截取前9
        grouped[key] = sorted(items, key=lambda x: x["timestamp"], reverse=True)[:9]

        # 重新整合所有分组的数据
        new_history = []
        for item_list in grouped.values():
            new_history.extend(item_list)

        self.save_data('cover_history', new_history)
        return [
            item for item in new_history
            if str(item.get("library_id")) == str(library_id)
        ]

    def prepare_library_images(self, library_dir: str, required_items: int = 9):
        """
        准备目录下的 1~required_items.jpg 图片文件:
        1. 检查已有的目标编号文件
        2. 保留已有的文件，只补足缺失的编号
        3. 补充文件时尽量避免连续使用相同的源图片
        """
        os.makedirs(library_dir, exist_ok=True)

        required_items = max(1, int(required_items))

        # 检查哪些编号的文件已存在，哪些缺失
        existing_numbers = []
        missing_numbers = []
        for i in range(1, required_items + 1):
            target_file_path = os.path.join(library_dir, f"{i}.jpg")
            if os.path.exists(target_file_path):
                existing_numbers.append(i)
            else:
                missing_numbers.append(i)

        # 如果已经存在所有文件，直接返回
        if not missing_numbers:
            return True

        logger.info(f"信息: {library_dir} 中缺少以下编号的图片: {missing_numbers}，将进行补充。")

        target_name_pattern = rf"^[1-9][0-9]*\.jpg$"

        # 获取可用作源的图片（排除已有的目标编号文件）
        # 使用 scandir 并限制采样数量，避免超大目录扫描导致长时间无日志
        source_image_filenames = []
        max_source_scan = 512
        scanned_entries = 0
        for entry in os.scandir(library_dir):
            scanned_entries += 1
            if not entry.is_file():
                continue

            f = entry.name
            # 排除 N.jpg（N 为正整数）作为源
            if re.match(target_name_pattern, f, re.IGNORECASE):
                continue
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                source_image_filenames.append(f)
                if len(source_image_filenames) >= max_source_scan:
                    break

        if scanned_entries > 2000:
            logger.info(f"信息: {library_dir} 文件较多，已快速采样 {len(source_image_filenames)} 张作为补图源")

        # 如果没有源图片可用
        if not source_image_filenames:
            # 如果已经有部分目标编号图片，可以从这些现有文件中选择
            if existing_numbers:
                logger.info(f"信息: {library_dir} 中没有其他图片可用，将从现有目标编号图片中随机选择进行复制。")
                existing_file_paths = [os.path.join(library_dir, f"{i}.jpg") for i in existing_numbers]
                source_image_paths = existing_file_paths
            else:
                logger.info(f"警告: {library_dir} 中没有任何可用的图片来生成 1-{required_items}.jpg。")
                return False
        else:
            # 将文件名转换为完整路径
            source_image_paths = [os.path.join(library_dir, f) for f in sorted(source_image_filenames)]

        # 如果源图片数量不足，需要重复使用
        if len(source_image_paths) < len(missing_numbers):
            logger.info(f"信息: 源图片数量({len(source_image_paths)})小于缺失数量({len(missing_numbers)})，某些图片将被重复使用。")

        # 为每个缺失的编号选择一个源图片，尽量避免连续重复
        last_used_source = None
        for missing_num in missing_numbers:
            target_path = os.path.join(library_dir, f"{missing_num}.jpg")

            # 如果只有一个源文件，没有选择，直接使用
            if len(source_image_paths) == 1:
                selected_source = source_image_paths[0]
            else:
                # 尝试选择一个与上次不同的源文件
                available_sources = [s for s in source_image_paths if s != last_used_source]

                # 如果没有其他选择（可能上次用了唯一的源文件），则使用所有源
                if not available_sources:
                    available_sources = source_image_paths

                # 随机选择一个源文件
                selected_source = random.choice(available_sources)

            # 记录本次使用的源文件，用于下次比较
            last_used_source = selected_source

            try:
                if not os.path.exists(selected_source):
                    logger.info(f"错误: 源文件 {selected_source} 在尝试复制前找不到了！")
                    return False

                shutil.copy(selected_source, target_path)
                logger.info(f"信息: 已创建 {missing_num}.jpg (源自: {os.path.basename(selected_source)})")

            except Exception as e:
                logger.info(f"错误: 复制文件 {selected_source} 到 {target_path} 时发生错误: {e}")
                return False

        logger.info(f"信息: {library_dir} 已成功补充所有缺失的图片，现在包含完整的 1-{required_items}.jpg")
        return True

    def __get_fonts(self):
        def detect_string_type(s: str):
            if not s:
                return None
            s = s.strip()

            # 判断是否是 HTTP(S) 链接
            if re.match(r'^https?://[^\s]+$', s, re.IGNORECASE):
                return 'url'

            # 判断是否像路径（包含 / 或 \，或以 ~、.、/ 开头）
            if os.path.isabs(s) or s.startswith(('.', '~', '/')) or re.search(r'[\\/]', s):
                return 'path'

            return None

        font_dir_path = self._font_path
        Path(font_dir_path).mkdir(parents=True, exist_ok=True)

        _, _, zh_preset_paths, en_preset_paths = self.__get_font_presets()

        if not self._zh_font_preset:
            self._zh_font_preset = DEFAULT_ZH_FONT

        default_font_url = {
            "pangmen": "https://raw.githubusercontent.com/wio-ki/MoviePilot-Plugins/main/fonts/pangmen.ttf",
            "CaveatBrush": "https://raw.githubusercontent.com/wio-ki/MoviePilot-Plugins/main/fonts/caveatbrush.ttf",
            "chaohei": "https://raw.githubusercontent.com/wio-ki/MoviePilot-Plugins/main/fonts/chaohei.ttf",
            "yasong": "https://raw.githubusercontent.com/wio-ki/MoviePilot-Plugins/main/fonts/yasong.ttf",
            "EmblemaOne": "https://raw.githubusercontent.com/wio-ki/MoviePilot-Plugins/main/fonts/EmblemaOne.woff2",
            "Melete": "https://raw.githubusercontent.com/wio-ki/MoviePilot-Plugins/main/fonts/Melete.otf",
            "Phosphate": "https://raw.githubusercontent.com/wio-ki/MoviePilot-Plugins/main/fonts/phosphate.ttf",
            "JosefinSans": "https://raw.githubusercontent.com/wio-ki/MoviePilot-Plugins/main/fonts/josefinsans.woff2",
            "LilitaOne": "https://raw.githubusercontent.com/wio-ki/MoviePilot-Plugins/main/fonts/lilitaone.woff2",
        }
        default_zh_url = default_font_url.get(self._zh_font_preset, default_font_url[DEFAULT_ZH_FONT])

        if not self._en_font_preset:
            self._en_font_preset = DEFAULT_EN_FONT

        default_en_url = default_font_url.get(self._en_font_preset, default_font_url[DEFAULT_EN_FONT])

        log_prefix = "默认"
        zh_custom_type = detect_string_type(self._zh_font_custom)
        en_custom_type = detect_string_type(self._en_font_custom)
        current_zh_font_url = self._zh_font_custom if zh_custom_type == 'url' else default_zh_url
        current_en_font_url = self._en_font_custom if en_custom_type == 'url' else default_en_url
        zh_local_path_config = self._zh_font_custom if zh_custom_type == 'path' else zh_preset_paths.get(self._zh_font_preset)
        en_local_path_config = self._en_font_custom if en_custom_type == 'path' else en_preset_paths.get(self._en_font_preset)

        downloaded_zh_font_base = f"{self._zh_font_preset}_custom" if zh_custom_type == 'url' else self._zh_font_preset
        downloaded_en_font_base = f"{self._en_font_preset}_custom" if en_custom_type == 'url' else self._en_font_preset
        hash_zh_file_name = f"{downloaded_zh_font_base}_url.hash"
        hash_en_file_name = f"{downloaded_en_font_base}_url.hash"
        final_zh_font_path_attr = "_zh_font_path"
        final_en_font_path_attr = "_en_font_path"

        logger.info(f"当前主标题字体URL: {current_zh_font_url} (本地路径: {zh_local_path_config})")

        active_fonts_to_process = [
            {
                "lang": "主标题",
                "url": current_zh_font_url,
                "local_path_config": zh_local_path_config,
                "download_base_name": downloaded_zh_font_base,
                "hash_file_name": hash_zh_file_name,
                "final_attr_name": final_zh_font_path_attr,
                "fallback_ext": ".ttf",
                "preset_path": zh_preset_paths.get(self._zh_font_preset),
                "fallback_preset": None if zh_custom_type else FONT_FALLBACKS.get(self._zh_font_preset),
            },
            {
                "lang": "副标题",
                "url": current_en_font_url,
                "local_path_config": en_local_path_config,
                "download_base_name": downloaded_en_font_base,
                "hash_file_name": hash_en_file_name,
                "final_attr_name": final_en_font_path_attr,
                "fallback_ext": ".ttf",
                "preset_path": en_preset_paths.get(self._en_font_preset),
                "fallback_preset": None if en_custom_type else FONT_FALLBACKS.get(self._en_font_preset),
            }
        ]


        for font_info in active_fonts_to_process:
            lang = font_info["lang"]
            url = font_info["url"]
            local_path_cfg = font_info["local_path_config"]
            download_base = font_info["download_base_name"]
            hash_filename = font_info["hash_file_name"]
            final_attr = font_info["final_attr_name"]
            fallback_ext = font_info["fallback_ext"]


            extension = self.get_file_extension_from_url(url, fallback_ext=fallback_ext)
            downloaded_font_file_path = Path(font_dir_path) / f"{download_base}{extension}"
            hash_file_path = Path(font_dir_path) / hash_filename

            current_font_path = None
            using_local_font = False
            if local_path_cfg:
                local_font_p = Path(local_path_cfg)
                if validate_font_file(local_font_p):
                    logger.info(f"{lang}字体: 使用本地指定路径 {local_font_p}")
                    current_font_path = local_font_p
                    using_local_font = True
                else:
                    logger.warning(f"{log_prefix}{lang}字体: 本地指定路径 {local_font_p} 无效或文件不存在。")

            if not using_local_font:
                url_hash = hashlib.md5(url.encode()).hexdigest()
                url_has_changed = True
                if hash_file_path.exists():
                    try:
                        if hash_file_path.read_text() == url_hash:
                            url_has_changed = False
                    except Exception as e:
                        logger.warning(f"读取哈希文件失败 {hash_file_path}: {e}。将重新下载。")

                font_file_is_valid = validate_font_file(downloaded_font_file_path)

                if url_has_changed or not font_file_is_valid:
                    if url_has_changed:
                        logger.info(f"{log_prefix}{lang}字体URL已更改或首次下载。")
                    if not font_file_is_valid and downloaded_font_file_path.exists():
                         logger.info(f"{log_prefix}{lang}字体文件 {downloaded_font_file_path} 无效或损坏，将重新下载。")
                    elif not downloaded_font_file_path.exists():
                         logger.info(f"{log_prefix}{lang}字体文件 {downloaded_font_file_path} 不存在，将下载。")

                    # 使用安全的字体下载方法
                    if self.download_font_safely_with_timeout(url, downloaded_font_file_path):
                        try:
                            hash_file_path.write_text(url_hash)
                        except Exception as e:
                            logger.error(f"写入哈希文件失败 {hash_file_path}: {e}")
                        current_font_path = downloaded_font_file_path
                    else:
                        logger.critical(f"无法获取必要的{log_prefix}{lang}支持字体: {url}")
                        if font_file_is_valid :
                             logger.warning(f"下载失败，但找到一个已存在的（可能旧版本）有效字体文件 {downloaded_font_file_path}，将尝试使用。")
                             current_font_path = downloaded_font_file_path
                        elif font_info.get("preset_path") and validate_font_file(Path(font_info["preset_path"])):
                             logger.warning(f"下载失败，改用本地预设字体 {font_info['preset_path']}")
                             current_font_path = Path(font_info["preset_path"])
                        else:
                             current_font_path = None
                else:
                    logger.info(f"{log_prefix}{lang}字体: 使用已下载/缓存的有效字体 {downloaded_font_file_path}")
                    current_font_path = downloaded_font_file_path

            if not current_font_path and font_info.get("fallback_preset"):
                current_font_path = self.__fallback_font(font_info["fallback_preset"], default_font_url[font_info["fallback_preset"]])

            # 安全设置字体路径
            if current_font_path and current_font_path.exists():
                setattr(self, final_attr, current_font_path)
                status_log = '(本地路径)' if using_local_font else '(已下载/缓存)'
                logger.info(f"{log_prefix}{lang}字体最终路径: {getattr(self,final_attr)} {status_log}")
            else:
                # 字体获取失败，设置为None并记录错误
                setattr(self, final_attr, None)
                logger.error(f"{log_prefix}{lang}字体获取失败，这可能导致封面生成失败")

        # 检查是否所有必要的字体都已获取
        if not self._zh_font_path or not self._en_font_path:
            logger.critical("关键字体文件缺失，插件可能无法正常工作。请检查网络连接或手动下载字体文件。")

    def __fallback_font(self, preset: str, url: str) -> Optional[Path]:
        """默认字体下载失败时改用备用字体，优先用本地已有的文件。"""
        _, _, paths, _ = self.__get_font_presets()
        local = paths.get(preset)
        if local and validate_font_file(Path(local)):
            logger.warning(f"默认字体不可用，本次改用 {preset}，下次生成时会重新尝试下载")
            return Path(local)
        target = Path(self._font_path) / f"{preset}{self.get_file_extension_from_url(url, fallback_ext='.ttf')}"
        if self.download_font_safely_with_timeout(url, target):
            logger.warning(f"默认字体不可用，本次改用 {preset}，下次生成时会重新尝试下载")
            return target
        return None

    def __sanitize_filename(self, filename: str) -> str:
        """
        将媒体库名称转换为安全的文件名，特别处理数字或字母开头的名称
        """
        if not filename:
            return "unknown"

        # 移除或替换不安全的字符
        # 替换Windows和Unix系统中不允许的字符
        unsafe_chars = r'[<>:"/\\|?*]'
        safe_name = re.sub(unsafe_chars, '_', filename)

        # 移除前后空格
        safe_name = safe_name.strip()

        # 如果名称为空，使用默认名称
        if not safe_name:
            return "unknown"

        # 确保不以点开头（在某些系统中是隐藏文件）
        if safe_name.startswith('.'):
            safe_name = '_' + safe_name[1:]

        # 限制长度（避免路径过长）
        if len(safe_name) > 100:
            safe_name = safe_name[:100]

        if safe_name != filename and filename not in self._sanitize_log_cache:
            self._sanitize_log_cache.add(filename)
            logger.debug(f"文件名安全化: '{filename}' -> '{safe_name}'")
        return safe_name

    def health_check(self) -> bool:
        """
        插件健康检查，确保关键组件正常
        """
        try:
            # 检查分辨率配置
            if not hasattr(self, '_resolution_config') or self._resolution_config is None:
                logger.warning("分辨率配置缺失，重新初始化")
                # 使用用户设置的分辨率，而不是硬编码的1080p
                if self._resolution == "custom":
                    self._resolution_config = ResolutionConfig((self._custom_width, self._custom_height))
                else:
                    self._resolution_config = ResolutionConfig(self._resolution)

            # 检查字体文件
            if not self._zh_font_path or not self._en_font_path:
                logger.warning("字体文件缺失，尝试重新获取")
                self.__get_fonts()


            # 验证字体文件有效性
            if self._zh_font_path and not validate_font_file(Path(self._zh_font_path)):
                logger.warning("主标题字体文件无效，尝试重新下载")
                return False

            if self._en_font_path and not validate_font_file(Path(self._en_font_path)):
                logger.warning("副标题字体文件无效，尝试重新下载")
                return False

            logger.info("插件健康检查通过")
            return True

        except Exception as e:
            logger.error(f"健康检查失败: {e}")
            return False

    def download_font_safely_with_timeout(self, font_url: str, font_path: Path, timeout: int = 180) -> bool:
        """
        带超时的安全字体下载方法，避免首次下载时阻塞过久
        """
        try:
            logger.info(f"开始下载字体（超时限制: {timeout}秒）: {font_url}")
            return self.download_font_safely(font_url, font_path, retries=1, timeout=timeout)

        except Exception as e:
            logger.error(f"字体下载过程中出现异常: {e}")
            return False

    def download_font_safely(self, font_url: str, font_path: Path, retries: int = 2, timeout: int = 30):
        """
        从链接下载字体文件到指定目录，使用优化的网络助手
        :param font_url: 字体文件URL
        :param font_path: 保存路径
        :param retries: 每种策略的最大重试次数（减少重试次数）
        :param timeout: 下载超时时间
        :return: 是否下载成功
        """
        logger.info(f"准备下载字体: {font_url} -> {font_path}")

        # 使用优化的网络助手进行下载
        network_helper = NetworkHelper(timeout=timeout, max_retries=retries)

        # 准备下载策略
        strategies = []

        # 判断是否为GitHub链接
        is_github_url = bool(re.search(r"(^|\.)github\.com$|(^|\.)raw\.githubusercontent\.com$", urlparse(font_url).hostname or ""))

        # 对于GitHub链接，优先使用GitHub镜像站
        if is_github_url and settings.GITHUB_PROXY:
            github_proxy_url = f"{UrlUtils.standardize_base_url(settings.GITHUB_PROXY)}{font_url}"
            strategies.append(("GitHub镜像站", github_proxy_url))

        # 直接使用原始URL
        strategies.append(("直连", font_url))

        # 遍历所有策略
        for strategy_name, target_url in strategies:
            logger.info(f"尝试使用策略：{strategy_name} 下载字体: {target_url}")

            # 创建临时文件路径
            temp_path = font_path.with_suffix('.temp')

            try:
                # 使用网络助手下载
                if network_helper.download_file_sync(target_url, temp_path):
                    # 验证下载的字体文件
                    if validate_font_file(temp_path):
                        # 验证通过后，将临时文件移动到正确位置
                        temp_path.replace(font_path)
                        logger.info(f"字体下载成功: 使用策略 {strategy_name}")
                        return True
                    else:
                        logger.warning(f"下载的字体文件验证失败，可能已损坏")
                        if temp_path.exists():
                            temp_path.unlink()
                else:
                    logger.warning(f"策略 {strategy_name} 下载失败")

            except Exception as e:
                logger.warning(f"策略 {strategy_name} 下载出错: {e}")
                # 清理可能的临时文件
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass

        # 所有策略都失败
        logger.error(f"所有下载策略均失败，无法下载字体，建议手动下载字体: {font_url}")
        return False

    def get_file_extension_from_url(self, url: str, fallback_ext: str = ".ttf") -> str:
        """
        从链接获取字体扩展名扩展名
        """
        try:
            parsed_url = urlparse(url)
            path_part = parsed_url.path
            if path_part:
                filename = os.path.basename(path_part)
                _ , ext = os.path.splitext(filename)
                return ext if ext else fallback_ext
            else:
                logger.warning(f"无法从URL中提取路径部分: {url}. 使用备用扩展名: {fallback_ext}")
                return fallback_ext
        except Exception as e:
            logger.error(f"解析URL时出错 '{url}': {e}. 使用备用扩展名: {fallback_ext}")
            return fallback_ext

    def stop_service(self):
        """
        停止服务
        """
        try:
            if self._update_lock.locked():
                self._event.set()
            with self._monitor_lock:
                if self._monitor_timer:
                    self._monitor_timer.cancel()
                    self._monitor_timer = None
                self._monitor_pending.clear()
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    # 不等待正在运行的任务，否则保存配置会被长时间阻塞
                    self._scheduler.shutdown(wait=False)
                self._scheduler = None
        except Exception as e:
            logger.error(f"停止服务失败: {str(e)}")
