import argparse
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from konsole_pal.cli import (
    build_parser,
    command_completions,
    command_demo,
    command_next,
    command_run,
    command_setup,
    command_status,
    main,
)
from konsole_pal.config import Settings
from konsole_pal.core import KonsoleTarget, SessionState


class CliTests(unittest.TestCase):
    def test_settings_and_hook_management_commands_parse(self):
        parser = build_parser()
        config = parser.parse_args(["config", "set", "animation", "static", "--project"])
        self.assertEqual(config.config_action, "set")
        self.assertTrue(config.project)
        hooks = parser.parse_args(["hooks", "install", "codex", "--force"])
        self.assertEqual(hooks.agents, ["codex"])
        self.assertTrue(hooks.force)
        setup = parser.parse_args(["setup", "--sprite-size", "large", "--yes"])
        self.assertEqual(setup.sprite_size, "large")
        completion = parser.parse_args(["completions", "fish"])
        self.assertEqual(completion.shell, "fish")
        inbox = parser.parse_args(["inbox", "--json"])
        self.assertTrue(inbox.json)
        emit = parser.parse_args(["emit", "attention", "--agent", "gemini"])
        self.assertEqual((emit.state, emit.agent), ("attention", "gemini"))
        scoped = parser.parse_args(
            ["config", "set", "sound", "false", "--agent", "claude"]
        )
        self.assertEqual(scoped.agent, "claude")
        theme = parser.parse_args(["theme", "preview", "ascii"])
        self.assertEqual(theme.theme_action, "preview")

    def test_help_prioritizes_onboarding_and_hides_internal_bridge(self):
        help_text = build_parser().format_help()
        self.assertIn("setup", help_text)
        self.assertIn("demo", help_text)
        self.assertNotIn("==SUPPRESS==", help_text)
        self.assertNotIn("consume an agent lifecycle hook", help_text)

    @patch("konsole_pal.cli.subprocess.run")
    @patch("konsole_pal.cli.update_session")
    @patch("konsole_pal.cli._target_or_error")
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
            patch.dict(os.environ, {"KONSOLE_PAL_VOLUME": "9"}, clear=False),
            patch("sys.argv", ["konsole-pal", "config", "show"]),
            redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("volume must be between 0 and 1", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_human_status_has_a_useful_empty_state_and_json_stays_clean(self):
        with tempfile.TemporaryDirectory() as directory:
            human = io.StringIO()
            with patch("konsole_pal.cli.runtime_dir", return_value=Path(directory)), redirect_stdout(human):
                self.assertEqual(
                    command_status(argparse.Namespace(json=False, verbose=False)), 0
                )
            self.assertIn("The burrow is quiet", human.getvalue())
            self.assertIn("konsole-pal demo", human.getvalue())

            machine = io.StringIO()
            with patch("konsole_pal.cli.runtime_dir", return_value=Path(directory)), redirect_stdout(machine):
                command_status(argparse.Namespace(json=True, verbose=False))
            self.assertEqual(machine.getvalue().strip(), "[]")

    @patch("konsole_pal.cli.install_hooks", return_value=[])
    @patch("konsole_pal.cli.write_user_setting")
    @patch("konsole_pal.cli.find_audio_player", return_value="pw-play")
    @patch("konsole_pal.cli.load_settings", return_value=Settings(focus="attention"))
    def test_noninteractive_setup_uses_calm_defaults(
        self, _settings, _audio, write, install
    ):
        args = argparse.Namespace(
            yes=False,
            focus=None,
            sprite_size="large",
            animation=None,
            agents=[],
            force=False,
            preview_sound=False,
            json=True,
        )
        output = io.StringIO()
        with redirect_stdout(output), patch("builtins.input") as prompt:
            self.assertEqual(command_setup(args), 0)
        prompt.assert_not_called()
        self.assertIn('"sprite_size": "large"', output.getvalue())
        write.assert_any_call("focus", "attention")
        write.assert_any_call("sprite_size", "large")
        install.assert_called_once()

    @patch("konsole_pal.cli.time.sleep")
    @patch("konsole_pal.cli.update_session")
    @patch("konsole_pal.cli._target_or_error")
    @patch("konsole_pal.cli.load_settings", return_value=Settings(sound=False))
    def test_demo_visits_each_state_without_focus(
        self, _settings, target, update, sleep
    ):
        target.return_value = KonsoleTarget("", "", "", backend="osc")
        args = argparse.Namespace(
            speed=0.01,
            sound=False,
            sprite_size="small",
            label=None,
        )
        with redirect_stdout(io.StringIO()):
            self.assertEqual(command_demo(args), 0)
        self.assertEqual([call.args[0] for call in update.call_args_list], ["working", "attention", "done"])
        self.assertTrue(all(not call.kwargs["focus"] for call in update.call_args_list))
        self.assertEqual(sleep.call_count, 2)

    def test_completion_output_is_script_only(self):
        output = io.StringIO()
        with redirect_stdout(output):
            command_completions(argparse.Namespace(shell="bash"))
        self.assertIn("complete -F _konsole_pal", output.getvalue())
        self.assertNotIn("Konsole Pal\n", output.getvalue())

    @patch("konsole_pal.cli.acknowledge_attention", return_value=True)
    @patch("konsole_pal.cli._attention_items")
    def test_next_skips_newer_unfocusable_remote_item(self, items, acknowledge):
        remote = SessionState(
            "attention",
            "Remote",
            "codex",
            KonsoleTarget("", "", "", backend="osc", remote=True),
            updated_at=20,
        )
        local = SessionState(
            "attention",
            "Local",
            "claude",
            KonsoleTarget("service", "/Sessions/2", "/Windows/1"),
            updated_at=10,
        )
        items.return_value = [(Path("remote.json"), remote), (Path("local.json"), local)]

        with redirect_stdout(io.StringIO()):
            self.assertEqual(command_next(argparse.Namespace(target=None)), 0)

        acknowledge.assert_called_once_with(Path("local.json"), activate=True)


if __name__ == "__main__":
    unittest.main()
