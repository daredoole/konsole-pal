import tempfile
import unittest
from pathlib import Path

from konsole_pal.config import (
    ConfigError,
    Settings,
    load_settings,
    reset_setting,
    user_config_path,
    write_setting,
)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = {"XDG_CONFIG_HOME": str(self.root / "config")}
        self.project = self.root / "project"
        self.nested = self.project / "src" / "feature"
        self.nested.mkdir(parents=True)
        (self.project / ".git").mkdir()

    def test_defaults_are_safe_and_low_frequency(self):
        settings = load_settings(cwd=self.project, env=self.env)
        self.assertEqual(settings, Settings())
        self.assertGreaterEqual(settings.animation_interval, 0.8)
        self.assertTrue(settings.sound)

    def test_precedence_is_defaults_user_project_environment_override(self):
        write_setting(user_config_path(self.env), "volume", "0.2")
        write_setting(user_config_path(self.env), "focus", "never")
        write_setting(self.project / ".konsole-pal.toml", "volume", "0.4")
        settings = load_settings(
            cwd=self.nested,
            env={**self.env, "KONSOLE_PAL_VOLUME": "0.6"},
            overrides={"volume": "0.8"},
        )
        self.assertEqual(settings.volume, 0.8)
        self.assertEqual(settings.focus, "never")

    def test_sprite_size_accepts_three_safe_presets(self):
        path = user_config_path(self.env)
        for size in ("small", "medium", "large"):
            write_setting(path, "sprite_size", size)
            self.assertEqual(load_settings(cwd=self.project, env=self.env).sprite_size, size)
        with self.assertRaisesRegex(ConfigError, "sprite_size"):
            write_setting(path, "sprite_size", "enormous")

    def test_write_and_reset_preserve_only_explicit_values(self):
        path = user_config_path(self.env)
        write_setting(path, "volume", "0.25")
        write_setting(path, "animation", "static")
        text = path.read_text(encoding="utf-8")
        self.assertIn("volume = 0.25", text)
        self.assertIn('animation = "static"', text)
        self.assertNotIn("working_timeout", text)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

        reset_setting(path, "volume")
        settings = load_settings(cwd=self.project, env=self.env)
        self.assertEqual(settings.volume, Settings().volume)
        self.assertEqual(settings.animation, "static")

        reset_setting(path)
        self.assertFalse(path.exists())

    def test_invalid_config_and_values_fail_closed(self):
        path = user_config_path(self.env)
        path.parent.mkdir(parents=True)
        path.write_text("[settings]\nvolume = 8\n", encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "volume"):
            load_settings(cwd=self.project, env=self.env)
        with self.assertRaisesRegex(ConfigError, "unknown setting"):
            write_setting(path, "nope", "true")

    def test_project_discovery_stops_at_repository_root(self):
        outside = self.root / ".konsole-pal.toml"
        write_setting(outside, "focus", "never")
        self.assertEqual(load_settings(cwd=self.nested, env=self.env).focus, "all")


if __name__ == "__main__":
    unittest.main()
