import json
import tempfile
import unittest
from pathlib import Path

from konsole_pal.themes import (
    BUILTIN_THEMES,
    FINAL_STATES,
    SIZES,
    cell_width,
    install_theme,
    load_theme,
    validate_theme,
)


class ThemeTests(unittest.TestCase):
    def test_all_builtin_sprites_are_fixed_width_per_size(self):
        for theme in BUILTIN_THEMES.values():
            validate_theme(theme)
            for size in SIZES:
                sprites = [
                    *theme.frames[size],
                    *(theme.finals[size][state] for state in FINAL_STATES),
                ]
                self.assertEqual(len({cell_width(sprite) for sprite in sprites}), 1)

    def test_custom_theme_install_is_data_only_and_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = {
                "name": "dots",
                "description": "Tiny dots",
                "frames": {size: ["[.]", "[:] ".rstrip()] for size in SIZES},
                "finals": {
                    size: {
                        "idle": "[z]",
                        "done": "[d]",
                        "attention": "[!]",
                        "error": "[x]",
                    }
                    for size in SIZES
                },
            }
            source = root / "theme.json"
            source.write_text(json.dumps(data), encoding="utf-8")
            env = {"XDG_CONFIG_HOME": str(root / "config")}

            installed = install_theme(source, env)

            self.assertEqual(installed.name, "dots")
            self.assertEqual(load_theme("dots", env), installed)
            target = root / "config" / "konsole-pal" / "themes" / "dots.json"
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_variable_width_theme_is_rejected(self):
        theme = BUILTIN_THEMES["ascii"]
        broken = type(theme)(
            "broken",
            "broken",
            {**theme.frames, "small": ("x", "xx")},
            theme.finals,
        )
        with self.assertRaisesRegex(ValueError, "one cell width"):
            validate_theme(broken)


if __name__ == "__main__":
    unittest.main()
