import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from say_cli.config import config_path, save_output, selected_output


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.environment = patch.dict(os.environ, {"XDG_CONFIG_HOME": self.directory.name})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_default_and_override_precedence_are_read_on_each_request(self):
        self.assertIsNone(selected_output(None))
        self.assertFalse(config_path().exists())
        save_output("headphones")
        self.assertEqual(selected_output(None), "headphones")
        self.assertEqual(selected_output("speakers"), "speakers")
        self.assertIsNone(selected_output("default"))
        save_output("monitor")
        self.assertEqual(selected_output(None), "monitor")
        save_output("default")
        self.assertIsNone(selected_output(None))

    def test_save_preserves_comments_other_settings_and_symlink(self):
        target = Path(self.directory.name) / "preferences.toml"
        target.write_text('# My preferences\noutput = "old" # wired\n\n[future]\nvalue = 7\n')
        config_path().parent.mkdir()
        config_path().symlink_to(target)
        save_output('usb.speaker "one" \\ café')
        self.assertTrue(config_path().is_symlink())
        self.assertEqual(selected_output(None), 'usb.speaker "one" \\ café')
        written = target.read_text()
        self.assertIn("# My preferences", written)
        self.assertIn("# wired", written)
        self.assertIn("[future]\nvalue = 7", written)
        self.assertEqual(list(target.parent.glob(".config-*")), [])

    def test_invalid_config_is_not_overwritten_and_override_can_bypass_it(self):
        config_path().parent.mkdir()
        config_path().write_text("output = [broken")
        with self.assertRaisesRegex(ValueError, "config.toml"):
            selected_output(None)
        with self.assertRaises(ValueError):
            save_output("speakers")
        self.assertEqual(config_path().read_text(), "output = [broken")
        self.assertIsNone(selected_output("default"))
        self.assertEqual(selected_output("headphones"), "headphones")

    def test_failed_atomic_replace_keeps_original(self):
        save_output("headphones")
        previous = config_path().read_bytes()
        with (
            patch.object(Path, "replace", side_effect=OSError("read-only")),
            self.assertRaises(OSError),
        ):
            save_output("speakers")
        self.assertEqual(config_path().read_bytes(), previous)
        self.assertEqual(list(config_path().parent.glob(".config-*")), [])

    def test_default_config_directory(self):
        with (
            patch.dict(os.environ, {"XDG_CONFIG_HOME": ""}),
            patch.object(Path, "home", return_value=Path(self.directory.name)),
        ):
            self.assertEqual(config_path(), Path(self.directory.name) / ".config/say/config.toml")

    def test_invalid_output_values(self):
        for value in ("", " ", "a\nb", 12, False):
            with self.subTest(value=value), self.assertRaises(ValueError):
                save_output(value)
