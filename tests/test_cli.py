import argparse
import io
import os
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from knsole_pal.cli import build_parser, command_run, main
from knsole_pal.core import KonsoleTarget


class CliTests(unittest.TestCase):
    def test_settings_and_hook_management_commands_parse(self):
        parser = build_parser()
        config = parser.parse_args(["config", "set", "animation", "static", "--project"])
        self.assertEqual(config.config_action, "set")
        self.assertTrue(config.project)
        hooks = parser.parse_args(["hooks", "install", "codex", "--force"])
        self.assertEqual(hooks.agents, ["codex"])
        self.assertTrue(hooks.force)

    @patch("knsole_pal.cli.subprocess.run")
    @patch("knsole_pal.cli.update_session")
    @patch("knsole_pal.cli._target_or_error")
    def test_run_flags_disable_sound_and_focus(self, get_target, update, run):
        target = KonsoleTarget("", "", "", backend="osc", tty_path="/dev/pts/9")
        get_target.return_value = target
        run.return_value.returncode = 0
        args = argparse.Namespace(
            command=["true"],
            agent="test",
            label="suite",
            no_focus=True,
            no_sound=True,
        )
        self.assertEqual(command_run(args), 0)
        self.assertFalse(update.call_args_list[-1].kwargs["focus"])
        self.assertFalse(update.call_args_list[-1].kwargs["settings"].sound)

    def test_invalid_environment_setting_is_a_clean_cli_error(self):
        stderr = io.StringIO()
        with (
            patch.dict(os.environ, {"KNSOLE_PAL_VOLUME": "9"}, clear=False),
            patch("sys.argv", ["knsole-pal", "config", "show"]),
            redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("volume must be between 0 and 1", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
