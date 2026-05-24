import sys
import os
from pathlib import Path
import types

repo_root = Path(r"e:\AI-workspace\MoviePilot-Plugins")
sys.path.insert(0, str(repo_root / "plugins.v2"))

# 修复 app 模块模拟
class MockPackage(types.ModuleType):
    __path__ = []

app_pkg = MockPackage("app")
sys.modules["app"] = app_pkg
app_plugins_pkg = MockPackage("app.plugins")
sys.modules["app.plugins"] = app_plugins_pkg

# 映射到真实的 mediacovergenerator 目录
sys.modules["app.plugins.mediacovergenerator"] = types.ModuleType("app.plugins.mediacovergenerator")

# mock logger
app_log = types.ModuleType("app.log")
app_log.logger = type("DummyLogger", (), {"info": print, "error": print, "debug": print, "warning": print})()
sys.modules["app.log"] = app_log

from style.style_static_1 import create_style_static_1
from style.style_static_2 import create_style_static_2
from style.style_static_4 import create_style_static_4
from utils.image_manager import ResolutionConfig

def test_styles():
    image_path = str(repo_root / "icons" / "emby.png")
    title = ("测试标题", "TEST TITLE")
    
    font_path = ("C:\\Windows\\Fonts\\msyh.ttc", "C:\\Windows\\Fonts\\arial.ttf")
    if not os.path.exists(font_path[0]):
        font_path = ("C:\\Windows\\Fonts\\simhei.ttf", "C:\\Windows\\Fonts\\arial.ttf")
        
    font_size = (150.0, 70.0)
    font_offset = (0.0, 30.0, 30.0)
    bg_color_config = {
        'mode': 'auto',
        'custom_color': None,
        'config_color': ""
    }
    resolution_config = ResolutionConfig("1000x1500")

    print("Testing Style 1...")
    try:
        b64_1 = create_style_static_1(image_path, title, font_path, font_size=font_size, font_offset=font_offset, blur_size=50, color_ratio=0.8, resolution_config=resolution_config, bg_color_config=bg_color_config)
        print("Style 1 generated. Length:", len(b64_1) if isinstance(b64_1, str) else type(b64_1))
    except Exception as e:
        import traceback
        traceback.print_exc()

    print("Testing Style 2...")
    try:
        b64_2 = create_style_static_2(image_path, title, font_path, font_size=font_size, font_offset=font_offset, blur_size=50, color_ratio=0.8, resolution_config=resolution_config, bg_color_config=bg_color_config)
        print("Style 2 generated. Length:", len(b64_2) if isinstance(b64_2, str) else type(b64_2))
    except Exception as e:
        import traceback
        traceback.print_exc()

    print("Testing Style 4...")
    try:
        b64_4 = create_style_static_4(image_path, title, font_path, font_size=font_size, font_offset=font_offset, blur_size=50, color_ratio=0.8, resolution_config=resolution_config, bg_color_config=bg_color_config)
        print("Style 4 generated. Length:", len(b64_4) if isinstance(b64_4, str) else type(b64_4))
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_styles()
