"""在装有 MoviePilot V3 的环境中运行 python -m unittest discover -s tests。"""
import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app.plugins

# 使用待测仓库的插件及其子模块。
app.plugins.__path__.insert(0, str(Path(__file__).resolve().parents[1] / "plugins.v3"))
from app.plugins.mediacovergenerator import MediaCoverGenerator


class ZSpaceCoverTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.output = Path(directory.name)
        self.service = SimpleNamespace(name="NAS", type="zspace", config={"host": "nas"}, instance=Mock())
        self.service.instance._host = "http://nas/"
        self.service.instance.user = "user"
        self.service.instance.get_data.return_value = SimpleNamespace(
            status_code=200, content=b"image", headers={"Content-Type": "image/png"}
        )
        self.cover = "R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
        self.data = {}
        self.p = self.plugin()

    def plugin(self):
        with patch("app.plugins._PluginBase.__init__", return_value=None):
            plugin = MediaCoverGenerator()
        plugin.get_data = self.data.get
        plugin.save_data = self.data.__setitem__
        plugin.get_data_path = lambda: self.output
        plugin._covers_output = str(self.output)
        plugin._MediaCoverGenerator__get_fonts = Mock()
        plugin._MediaCoverGenerator__build_library_image = Mock(return_value=self.cover)
        return plugin

    def update(self, library_id="one", mode="update", status=404):
        self.service.instance.post_data.return_value = None if status is None else SimpleNamespace(status_code=status)
        library = {"Id": library_id, "Name": library_id}
        return self.p._MediaCoverGenerator__update_library(self.service, library, mode)[0]

    def test_two_readable_libraries_pause_new_instance_until_config_changes(self):
        self.assertEqual(self.update(), "failed")
        self.assertEqual(self.update(), "skipped")
        self.assertEqual(self.update("two"), "failed")
        self.p = self.plugin()
        self.service.instance.reset_mock()
        self.assertEqual(self.update("three", "scheduled"), "skipped")
        self.p._MediaCoverGenerator__build_library_image.assert_not_called()
        self.service.instance.post_data.assert_not_called()
        self.service.instance.get_data.assert_not_called()
        self.service.config["host"] = "new-nas"
        self.assertEqual(self.update("three", status=204), "uploaded")

    def test_auth_transport_and_unreadable_cover_do_not_pause(self):
        for status in (401, 403, 500, None):
            with self.subTest(status=status):
                self.assertEqual(self.update(status=status), "failed")
                self.service.instance.get_data.assert_not_called()
        self.service.instance.get_data.return_value = SimpleNamespace(status_code=404)
        self.assertEqual(self.update(), "failed")
        self.assertEqual(self.update(status=200), "uploaded")

    def test_local_and_retry_use_same_artifact_without_duplicate_files(self):
        self.p._save_recent_covers = False
        self.p._run_style = "static_3"
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            self.assertEqual(self.update(mode="local"), "local")
        self.assertEqual(self.p._run_style, "animated_3")
        self.service.instance.post_data.assert_not_called()
        saved, = self.output.glob("*.gif")
        self.p._MediaCoverGenerator__build_library_image.reset_mock()
        self.assertEqual(self.update(mode="retry", status=204), "uploaded")
        self.p._MediaCoverGenerator__build_library_image.assert_not_called()
        sent = self.service.instance.post_data.call_args.kwargs["data"]
        self.assertEqual(base64.b64decode(sent), saved.read_bytes())
        self.assertEqual(list(self.output.glob("*.gif")), [saved])
