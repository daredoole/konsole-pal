import argparse
import io
import os
import tempfile
import unittest
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from konsole_pal.cli import (
    WATCH_ENTER,
    WATCH_EXIT,
    _active_items,
    _age_label,
    _filter_watch_items,
    _move_watch_selection,
    _read_watch_key,
    _run_watch_action,
    _task_label,
    build_parser,
    command_completions,
    command_demo,
    command_next,
    command_run,
    command_setup,
    command_status,
    command_watch,
    main,
)
from konsole_pal.config import Settings
from konsole_pal.core import KonsoleTarget, SessionState
from konsole_pal.presentation import cell_width


class TtyBuffer(io.StringIO):
    def isatty(self):
        return True


class CliTests(unittest.TestCase):
    def test_relative_ages_are_compact_and_stable(self):
        self.assertEqual(_age_label(990, now=1_000), "now")
        self.assertEqual(_age_label(910, now=1_000), "1m")
        self.assertEqual(_age_label(-6_201, now=1_000), "2h")
        self.assertEqual(_age_label(-172_000, now=1_000), "2d")

    def test_settings_and_hook_management_commands_parse(self):
        parser = build_parser()
        config = parser.parse_args(
            ["config", "set", "animation", "static", "--project"]
        )
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
        watch = parser.parse_args(["watch", "--attention-only", "--expanded"])
        self.assertTrue(watch.attention_only)
        self.assertTrue(watch.expanded)

    def test_watch_selection_wraps_and_task_labels_are_control_safe(self):
        self.assertEqual(_move_watch_selection(["a", "b"], "a", -1), "b")
        self.assertEqual(_move_watch_selection(["a", "b"], "b", 1), "a")
        state = SessionState(
            "working",
            "Codex · checkout\x1b[31m\nsecrets",
            "codex",
            KonsoleTarget("", "", ""),
        )
        task = _task_label(state)
        self.assertIn("checkout", task)
        self.assertNotIn("\x1b", task)
        self.assertNotIn("\n", task)

    def test_watch_filter_matches_task_agent_backend_and_state_aliases(self):
        items = [
            (
                Path("codex.json"),
                SessionState(
                    "working",
                    "Refactor checkout",
                    "codex",
                    KonsoleTarget("", "", "", backend="zellij"),
                ),
            ),
            (
                Path("claude.json"),
                SessionState(
                    "attention",
                    "Review release",
                    "claude",
                    KonsoleTarget("service", "/Sessions/2", "/Windows/1"),
                ),
            ),
        ]
        self.assertEqual(len(_filter_watch_items(items, "checkout")), 1)
        self.assertEqual(len(_filter_watch_items(items, "Codex")), 1)
        self.assertEqual(len(_filter_watch_items(items, "zellij")), 1)
        self.assertEqual(len(_filter_watch_items(items, "waiting")), 1)

    def test_watch_keys_decode_without_echoing_escape_sequences(self):
        cases = {
            b"\x1b[A": "up",
            b"\x1b[B": "down",
            b"\r": "toggle",
            b"f": "focus",
            b"a": "acknowledge",
            b"t": "rename",
            b"/": "filter",
            b"\x1b": "clear_filter",
            b"q": "quit",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                reader, writer = os.pipe()
                try:
                    os.write(writer, raw)
                    self.assertEqual(_read_watch_key(reader, 0.05), expected)
                finally:
                    os.close(reader)
                    os.close(writer)

    @patch("konsole_pal.cli.acknowledge_attention", return_value=True)
    @patch("konsole_pal.cli.focus_tab", return_value=True)
    def test_watch_direct_actions_target_only_the_selected_session(self, focus, ack):
        path = Path("selected.json")
        selected = (
            path,
            SessionState(
                "attention",
                "Review release",
                "claude",
                KonsoleTarget("service", "/Sessions/2", "/Windows/1"),
            ),
        )
        self.assertEqual(_run_watch_action("focus", selected)[1], "ok")
        self.assertEqual(_run_watch_action("acknowledge", selected)[1], "ok")
        focus.assert_called_once_with(selected[1].target)
        ack.assert_called_once_with(path, activate=False)

        selected[1].state = "working"
        self.assertEqual(_run_watch_action("acknowledge", selected)[1], "warn")
        ack.assert_called_once()

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
            with (
                patch("konsole_pal.cli.runtime_dir", return_value=Path(directory)),
                redirect_stdout(human),
            ):
                self.assertEqual(
                    command_status(argparse.Namespace(json=False, verbose=False)), 0
                )
            self.assertIn("The burrow is quiet", human.getvalue())
            self.assertIn("konsole-pal demo", human.getvalue())

            machine = io.StringIO()
            with (
                patch("konsole_pal.cli.runtime_dir", return_value=Path(directory)),
                redirect_stdout(machine),
            ):
                command_status(argparse.Namespace(json=True, verbose=False))
            self.assertEqual(machine.getvalue().strip(), "[]")

    @patch("konsole_pal.cli.read_state")
    @patch("konsole_pal.cli.runtime_dir")
    def test_active_view_prioritizes_attention_then_working(self, runtime, read):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / f"{name}.json" for name in ("done", "working", "attention")]
            for path in paths:
                path.touch()
            states = {
                "done.json": SessionState(
                    "done", "Done", "gemini", KonsoleTarget("", "", ""), updated_at=30
                ),
                "working.json": SessionState(
                    "working",
                    "Working",
                    "codex",
                    KonsoleTarget("", "", ""),
                    updated_at=10,
                ),
                "attention.json": SessionState(
                    "attention",
                    "Needs you",
                    "claude",
                    KonsoleTarget("", "", ""),
                    updated_at=5,
                ),
            }
            runtime.return_value = root
            read.side_effect = lambda path: states[path.name]

            ordered = _active_items()

        self.assertEqual(
            [state.state for _path, state in ordered],
            ["attention", "working", "done"],
        )

    @patch("konsole_pal.cli._active_items")
    def test_watch_once_is_a_unified_view_of_every_agent_state(self, active_items):
        active_items.return_value = [
            (
                Path("attention.json"),
                SessionState(
                    "attention",
                    "Review request with an intentionally very long project label",
                    "claude",
                    KonsoleTarget("service", "/Sessions/2", "/Windows/1"),
                    updated_at=1_000,
                ),
            ),
            (
                Path("working.json"),
                SessionState(
                    "working",
                    "Build task",
                    "codex",
                    KonsoleTarget("", "", "", backend="osc", remote=True),
                    updated_at=990,
                ),
            ),
        ]
        output = io.StringIO()
        args = argparse.Namespace(
            interval=1.0,
            verbose=False,
            once=True,
            attention_only=False,
            expanded=True,
        )
        with (
            redirect_stdout(output),
            patch("konsole_pal.cli.time.time", return_value=1_000),
            patch(
                "konsole_pal.cli.shutil.get_terminal_size",
                return_value=os.terminal_size((60, 24)),
            ),
        ):
            self.assertEqual(command_watch(args), 0)

        rendered = output.getvalue()
        self.assertIn("One live view of every agent session", rendered)
        self.assertIn("Review request", rendered)
        self.assertIn("Build task", rendered)
        self.assertIn("Needs you", rendered)
        self.assertIn("Konsole", rendered)
        self.assertIn("Working", rendered)
        self.assertIn("SSH / terminal", rendered)
        self.assertIn("Task", rendered)
        self.assertIn("Session", rendered)
        self.assertLessEqual(max(map(cell_width, rendered.splitlines())), 60)

    @patch("konsole_pal.cli._active_items", side_effect=[[], KeyboardInterrupt])
    def test_interactive_watch_restores_cursor_and_screen(self, _active_items):
        output = TtyBuffer()
        args = argparse.Namespace(
            interval=0.25,
            verbose=False,
            once=False,
            attention_only=False,
        )
        with (
            patch("sys.stdout", output),
            patch("konsole_pal.cli.time.sleep"),
            patch.dict(os.environ, {"TERM": "xterm-256color"}),
        ):
            self.assertEqual(command_watch(args), 130)

        rendered = output.getvalue()
        self.assertTrue(rendered.startswith(WATCH_ENTER))
        self.assertTrue(rendered.endswith(WATCH_EXIT))

    @patch("konsole_pal.cli._read_watch_key", side_effect=["down", "toggle", "quit"])
    @patch("konsole_pal.cli._watch_input", side_effect=lambda _enabled: nullcontext(0))
    @patch("konsole_pal.cli._active_items")
    def test_interactive_watch_moves_and_expands_a_task(
        self, active_items, _watch_input, _read_key
    ):
        active_items.return_value = [
            (
                Path("one.json"),
                SessionState(
                    "attention",
                    "Review API change",
                    "claude",
                    KonsoleTarget("service", "/Sessions/1", "/Windows/1"),
                    updated_at=1_000,
                ),
            ),
            (
                Path("two.json"),
                SessionState(
                    "working",
                    "Build checkout tests",
                    "codex",
                    KonsoleTarget("service", "/Sessions/2", "/Windows/1"),
                    updated_at=990,
                ),
            ),
        ]
        output = TtyBuffer()
        args = argparse.Namespace(
            interval=0.25,
            verbose=False,
            once=False,
            attention_only=False,
            expanded=False,
        )
        with (
            patch("sys.stdout", output),
            patch("sys.stdin", TtyBuffer()),
            patch("konsole_pal.cli.time.time", return_value=1_000),
            patch.dict(os.environ, {"TERM": "xterm-256color"}),
        ):
            self.assertEqual(command_watch(args), 0)

        rendered = output.getvalue()
        self.assertIn("› Codex", rendered)
        self.assertIn("Task", rendered)
        self.assertIn("Build checkout tests", rendered)
        self.assertIn("f focus · a ack · t task · / filter", rendered)
        self.assertTrue(rendered.endswith(WATCH_EXIT))

    @patch("konsole_pal.cli.rename_session", return_value=True)
    @patch(
        "konsole_pal.cli._watch_prompt",
        side_effect=["Release polish", "claude"],
    )
    @patch(
        "konsole_pal.cli._read_watch_key",
        side_effect=["rename", "filter", "clear_filter", "quit"],
    )
    @patch("konsole_pal.cli._watch_input", side_effect=lambda _enabled: nullcontext(0))
    @patch("konsole_pal.cli._active_items")
    def test_interactive_watch_edits_tasks_and_filters_without_leaking_keys(
        self, active_items, _watch_input, _read_key, prompt, rename
    ):
        state = SessionState(
            "working",
            "Build checkout tests",
            "claude",
            KonsoleTarget("service", "/Sessions/2", "/Windows/1"),
            updated_at=1_000,
        )
        path = Path("claude.json")
        active_items.return_value = [(path, state)]
        output = TtyBuffer()
        args = argparse.Namespace(
            interval=0.25,
            verbose=False,
            once=False,
            attention_only=False,
            expanded=False,
        )
        with (
            patch("sys.stdout", output),
            patch("sys.stdin", TtyBuffer()),
            patch("konsole_pal.cli.time.time", return_value=1_000),
            patch.dict(os.environ, {"TERM": "xterm-256color"}),
        ):
            self.assertEqual(command_watch(args), 0)

        rename.assert_called_once_with(path, "Release polish")
        self.assertEqual(prompt.call_count, 2)
        self.assertIn("filter: claude", output.getvalue())
        self.assertIn("Filter cleared", output.getvalue())
        self.assertTrue(output.getvalue().endswith(WATCH_EXIT))

    @patch("konsole_pal.cli._active_items", side_effect=[[], KeyboardInterrupt])
    def test_watch_emits_no_screen_controls_for_a_dumb_terminal(self, _active_items):
        output = TtyBuffer()
        args = argparse.Namespace(
            interval=0.25,
            verbose=False,
            once=False,
            attention_only=False,
        )
        with (
            patch("sys.stdout", output),
            patch("konsole_pal.cli.time.sleep"),
            patch.dict(os.environ, {"TERM": "dumb"}),
        ):
            self.assertEqual(command_watch(args), 130)

        self.assertNotIn("\x1b[", output.getvalue())

    @patch("konsole_pal.cli._active_items")
    @patch("konsole_pal.cli._attention_items", return_value=[])
    def test_attention_only_watch_does_not_scan_ordinary_sessions(
        self, _attention_items, active_items
    ):
        args = argparse.Namespace(
            interval=1.0,
            verbose=False,
            once=True,
            attention_only=True,
        )
        with redirect_stdout(io.StringIO()):
            self.assertEqual(command_watch(args), 0)
        _attention_items.assert_called_once_with()
        active_items.assert_not_called()

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
        self.assertEqual(
            [call.args[0] for call in update.call_args_list],
            ["working", "attention", "done"],
        )
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
        items.return_value = [
            (Path("remote.json"), remote),
            (Path("local.json"), local),
        ]

        with redirect_stdout(io.StringIO()):
            self.assertEqual(command_next(argparse.Namespace(target=None)), 0)

        acknowledge.assert_called_once_with(Path("local.json"), activate=True)


if __name__ == "__main__":
    unittest.main()
