import os
import pty
import select
import tempfile
import time
import unicodedata
import unittest
from pathlib import Path
from unittest.mock import patch

from konsole_pal.config import Settings
from konsole_pal.core import (
    WORKING_FRAMES,
    KonsoleTarget,
    SessionState,
    emit_bell,
    emit_osc_title,
    emit_terminal_notification,
    event_to_state,
    focus_tab,
    looks_like_question,
    notify_state,
    read_state,
    reset_runtime,
    set_title,
    spawn_animator,
    target_from_env,
    text_cell_width,
    title_for,
    update_session,
)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.target = KonsoleTarget(
            service="org.kde.konsole-123",
            session_path="/Sessions/7",
            window_path="/Windows/2",
        )

    def test_target_session_id_and_key_are_stable(self):
        self.assertEqual(self.target.session_id, 7)
        self.assertEqual(self.target.key, self.target.key)
        self.assertEqual(len(self.target.key), 16)

    @patch("konsole_pal.core._discover_zellij_tab", return_value="8")
    def test_zellij_target_uses_stable_pane_and_tab_ids(self, discover):
        target = target_from_env(
            {
                "ZELLIJ": "0",
                "ZELLIJ_SESSION_NAME": "dev",
                "ZELLIJ_PANE_ID": "terminal_4",
            }
        )
        self.assertIsNotNone(target)
        self.assertEqual(target.backend, "zellij")
        self.assertEqual(target.pane_id, "terminal_4")
        self.assertEqual(target.tab_id, "8")
        discover.assert_called_once_with("dev", "terminal_4")

    def test_tmux_target_uses_stable_pane_id(self):
        target = target_from_env({"TMUX": "/tmp/tmux-1000/default,1,0", "TMUX_PANE": "%7"})
        self.assertIsNotNone(target)
        self.assertEqual(target.backend, "tmux")
        self.assertEqual(target.pane_id, "%7")
        self.assertEqual(target.multiplexer_session, "/tmp/tmux-1000/default")

    @patch("konsole_pal.core._controlling_tty_path", return_value="/dev/pts/9")
    def test_ssh_and_generic_terminals_fall_back_to_osc(self, _tty):
        target = target_from_env(
            {"SSH_CONNECTION": "host 1 host 2", "TERM": "xterm-256color"}
        )
        self.assertIsNotNone(target)
        self.assertEqual(target.backend, "osc")
        self.assertEqual(target.tty_path, "/dev/pts/9")
        self.assertTrue(target.remote)

    @patch("konsole_pal.core._controlling_tty_path", return_value="/dev/pts/9")
    def test_common_terminals_receive_native_targets(self, _tty):
        kitty = target_from_env({"KITTY_WINDOW_ID": "14", "TERM": "xterm-kitty"})
        wezterm = target_from_env({"WEZTERM_PANE": "22", "TERM": "xterm-256color"})
        ghostty = target_from_env({"TERM_PROGRAM": "ghostty", "TERM": "xterm-ghostty"})

        self.assertEqual((kitty.backend, kitty.pane_id), ("kitty", "14"))
        self.assertEqual((wezterm.backend, wezterm.pane_id), ("wezterm", "22"))
        self.assertEqual(ghostty.backend, "ghostty")

    def test_event_mapping(self):
        self.assertEqual(
            event_to_state("codex", {"hook_event_name": "UserPromptSubmit"}),
            ("working", False),
        )
        self.assertEqual(
            event_to_state("codex", {"hook_event_name": "PermissionRequest"}),
            ("attention", True),
        )
        self.assertEqual(
            event_to_state(
                "codex",
                {"hook_event_name": "PreToolUse", "tool_name": "request_user_input"},
            ),
            ("attention", True),
        )
        self.assertEqual(
            event_to_state(
                "codex",
                {"hook_event_name": "Stop", "last_assistant_message": "Pick one?"},
            ),
            ("attention", True),
        )
        self.assertEqual(
            event_to_state("cursor", {"hook_event_name": "stop", "status": "completed"}),
            ("done", True),
        )
        self.assertEqual(
            event_to_state("gemini", {"hook_event_name": "AfterAgent"}),
            ("done", True),
        )
        self.assertEqual(
            event_to_state("opencode", {"event": {"type": "permission.asked"}}),
            ("attention", True),
        )
        self.assertEqual(
            event_to_state("copilot", {"hook_event_name": "errorOccurred"}),
            ("error", True),
        )

    @patch("konsole_pal.core._write_tty", return_value=True)
    def test_kitty_notification_is_clickable_and_sanitized(self, write):
        target = KonsoleTarget(
            "", "", "", backend="kitty", tty_path="/dev/pts/9", pane_id="12"
        )

        self.assertTrue(
            emit_terminal_notification(target, "Needs you\x1b", "Agent\x07 tab")
        )

        payload = write.call_args.args[0]
        self.assertIn(b"\x1b]99;", payload)
        self.assertIn(b"a=focus", payload)
        self.assertEqual(payload.count(b"\x1b"), 4)
        self.assertNotIn(b"\x07", payload)

    @patch("konsole_pal.core.emit_terminal_notification", return_value=True)
    def test_remote_auto_notification_stays_in_terminal(self, terminal_notify):
        target = KonsoleTarget("", "", "", backend="osc", remote=True)

        self.assertTrue(notify_state(target, "attention", "Codex demo", "auto"))

        terminal_notify.assert_called_once_with(
            target, "Agent needs you", "Codex demo"
        )

    @patch("konsole_pal.core._run_quiet", return_value=True)
    @patch("konsole_pal.core.shutil.which")
    def test_kitty_and_wezterm_focus_use_argument_arrays(self, which, run):
        which.side_effect = lambda name: f"/usr/bin/{name}"
        kitty = KonsoleTarget("", "", "", backend="kitty", pane_id="12")
        wezterm = KonsoleTarget("", "", "", backend="wezterm", pane_id="22")

        self.assertTrue(focus_tab(kitty))
        self.assertTrue(focus_tab(wezterm))

        self.assertEqual(
            run.call_args_list[0].args[0],
            ["/usr/bin/kitten", "@", "focus-window", "--match", "id:12"],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["/usr/bin/wezterm", "cli", "activate-pane", "--pane-id", "22"],
        )

    def test_question_detection(self):
        self.assertTrue(looks_like_question("Please choose a database."))
        self.assertFalse(looks_like_question("Implemented and tests pass."))

    def test_titles(self):
        working = SessionState("working", "Codex · demo", "codex", self.target)
        done = SessionState("done", "Codex · demo", "codex", self.target)
        self.assertIn("🐇", title_for(working, 2))
        self.assertEqual(title_for(done), "🐇💤 Codex · demo")

    def test_working_frames_keep_the_label_at_one_fixed_position(self):
        state = SessionState("working", "Codex · demo", "codex", self.target)
        titles = [title_for(state, frame) for frame in range(len(WORKING_FRAMES))]
        label_offsets = {
            text_cell_width(title[: title.index("Codex")]) for title in titles
        }
        self.assertEqual(label_offsets, {5})
        self.assertEqual({len(frame) for frame in WORKING_FRAMES}, {2})
        cell_widths = {
            sum(2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1 for char in frame)
            for frame in WORKING_FRAMES
        }
        self.assertEqual(cell_widths, {4})

    def test_update_writes_private_state_and_spawns(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {"XDG_RUNTIME_DIR": directory}
            with (
                patch("konsole_pal.core.spawn_animator") as spawn,
                patch("konsole_pal.core.play_sound") as sound,
            ):
                path = update_session(
                    "working",
                    agent="codex",
                    label="Codex · demo",
                    target=self.target,
                    env=env,
                )
            self.assertTrue(path.exists())
            self.assertEqual(read_state(path).state, "working")
            spawn.assert_called_once_with(path)
            sound.assert_not_called()

    def test_settings_are_snapshotted_and_focus_policy_is_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {"XDG_RUNTIME_DIR": directory}
            settings = Settings(
                focus="attention",
                sound=False,
                animation="static",
                animation_interval=1.25,
                sprite_size="large",
                label_max_cells=12,
                working_timeout=120,
            )
            with patch("konsole_pal.core.spawn_animator"):
                path = update_session(
                    "done",
                    agent="codex",
                    label="a label that is long",
                    target=self.target,
                    focus=True,
                    settings=settings,
                    env=env,
                )
            state = read_state(path)
            self.assertFalse(state.focus)
            self.assertEqual(state.animation, "static")
            self.assertEqual(state.animation_interval, 1.25)
            self.assertEqual(state.sprite_size, "large")
            self.assertEqual(state.label_max_cells, 12)
            self.assertEqual(state.working_timeout, 120)

    def test_final_states_play_the_matching_sound(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {"XDG_RUNTIME_DIR": directory}
            with (
                patch("konsole_pal.core.spawn_animator"),
                patch("konsole_pal.core.play_sound") as sound,
            ):
                update_session(
                    "done",
                    agent="codex",
                    label="Codex · demo",
                    target=self.target,
                    env=env,
                )
                update_session(
                    "attention",
                    agent="codex",
                    label="Codex · demo",
                    target=self.target,
                    env=env,
                )
            self.assertEqual(sound.call_args_list[0].args, ("done", env))
            self.assertEqual(sound.call_args_list[1].args, ("attention", env))
            self.assertEqual(sound.call_args_list[0].kwargs, {"volume": 0.7})

    @patch("konsole_pal.core._animator_process_is_live", return_value=False)
    @patch("konsole_pal.core.set_title", return_value=True)
    def test_reset_restores_resting_title_and_removes_runtime_state(
        self, set_title_mock, _live
    ):
        with tempfile.TemporaryDirectory() as directory:
            env = {"XDG_RUNTIME_DIR": directory}
            with patch("konsole_pal.core.spawn_animator"):
                path = update_session(
                    "working",
                    agent="codex",
                    label="Codex · demo",
                    target=self.target,
                    sound=False,
                    env=env,
                )
            path.parent.joinpath(".animator.pid").write_text("999999", encoding="ascii")
            restored, removed = reset_runtime(env)
            self.assertEqual(restored, 1)
            self.assertGreaterEqual(removed, 2)
            self.assertFalse(path.exists())
            self.assertEqual(set_title_mock.call_args.args[1], "🐇💤 Codex · demo")

    @patch("konsole_pal.core._run_quiet", return_value=True)
    @patch("konsole_pal.core.find_qdbus", return_value="/usr/bin/qdbus")
    def test_set_title_uses_displayed_and_initial_roles(self, _find, run):
        self.assertTrue(set_title(self.target, "🐇 Codex"))
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0][-2:], ["1", "🐇 Codex"])
        self.assertEqual(run.call_args_list[1].args[0][-2:], ["0", "🐇 Codex"])

    @patch("konsole_pal.core._run_quiet", return_value=True)
    @patch("konsole_pal.core.find_qdbus", return_value="/usr/bin/qdbus")
    def test_animation_frames_use_one_dbus_call_after_initial_title(self, _find, run):
        self.assertTrue(set_title(self.target, "🐇⠂  Codex", persist=False))
        run.assert_called_once()

    @patch("konsole_pal.core._run_quiet", return_value=True)
    @patch("konsole_pal.core.shutil.which", return_value="/usr/bin/zellij")
    def test_zellij_updates_and_focuses_the_originating_tab(self, _which, run):
        target = KonsoleTarget(
            "",
            "",
            "",
            backend="zellij",
            multiplexer_session="dev",
            pane_id="terminal_4",
            tab_id="8",
        )
        self.assertTrue(set_title(target, "🐇⠂  Codex"))
        self.assertTrue(focus_tab(target))
        self.assertEqual(
            run.call_args_list[0].args[0],
            [
                "/usr/bin/zellij",
                "--session",
                "dev",
                "action",
                "rename-tab",
                "--tab-id",
                "8",
                "🐇⠂  Codex",
            ],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "/usr/bin/zellij",
                "--session",
                "dev",
                "action",
                "focus-pane-id",
                "terminal_4",
            ],
        )

    @patch("konsole_pal.core._run_quiet", return_value=True)
    @patch("konsole_pal.core.shutil.which", return_value="/usr/bin/tmux")
    def test_tmux_updates_and_focuses_the_originating_window(self, _which, run):
        target = KonsoleTarget(
            "",
            "",
            "",
            backend="tmux",
            multiplexer_session="/tmp/tmux.sock",
            pane_id="%7",
        )
        self.assertTrue(set_title(target, "🐇⠂  Codex"))
        self.assertTrue(focus_tab(target))
        self.assertEqual(
            run.call_args_list[0].args[0],
            [
                "/usr/bin/tmux",
                "-S",
                "/tmp/tmux.sock",
                "rename-window",
                "-t",
                "%7",
                "🐇⠂  Codex",
            ],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "/usr/bin/tmux",
                "-S",
                "/tmp/tmux.sock",
                "select-window",
                "-t",
                "%7",
            ],
        )
        self.assertEqual(
            run.call_args_list[2].args[0],
            [
                "/usr/bin/tmux",
                "-S",
                "/tmp/tmux.sock",
                "select-pane",
                "-t",
                "%7",
            ],
        )

    @patch("konsole_pal.core._write_tty", return_value=True)
    def test_ssh_osc_title_and_bell_use_the_originating_pty(self, write):
        target = KonsoleTarget("", "", "", backend="osc", tty_path="/dev/pts/9")
        self.assertTrue(emit_osc_title("🐇 Codex", target.tty_path))
        self.assertTrue(emit_bell(target))
        self.assertEqual(write.call_args_list[0].args[1], "/dev/pts/9")
        self.assertEqual(write.call_args_list[1].args, (b"\x07", "/dev/pts/9"))

    def test_real_pty_carries_ssh_title_notification_and_bell_bytes(self):
        master, slave = pty.openpty()
        try:
            tty_path = os.ttyname(slave)
            self.assertTrue(emit_osc_title("🐇 remote", tty_path))
            self.assertTrue(emit_bell(KonsoleTarget("", "", "", backend="osc", tty_path=tty_path)))
            self.assertTrue(
                emit_terminal_notification(
                    KonsoleTarget("", "", "", backend="osc", tty_path=tty_path),
                    "Needs you",
                    "Remote agent",
                )
            )
            payload = b""
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and b"\x1b]9;Needs you" not in payload:
                readable, _, _ = select.select([master], [], [], 0.05)
                if readable:
                    payload += os.read(master, 1024)
            self.assertIn(b"\x1b]0;", payload)
            self.assertIn(b"\x07\x07", payload)
            self.assertIn(b"\x1b]9;Needs you: Remote agent\x1b\\", payload)
        finally:
            os.close(master)
            os.close(slave)

    @patch("konsole_pal.core.subprocess.Popen")
    @patch("konsole_pal.core._animator_process_is_live", return_value=True)
    def test_all_tabs_reuse_the_live_shared_animator(self, live, popen):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.parent.joinpath(".animator.pid").write_text("321", encoding="ascii")
            spawn_animator(path)
        live.assert_called_once_with(321, path)
        popen.assert_not_called()

    @patch("konsole_pal.core.subprocess.Popen")
    @patch("konsole_pal.core._animator_process_is_live", return_value=False)
    def test_stale_or_reused_pid_starts_a_replacement_animator(self, live, popen):
        popen.return_value.pid = 654
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            pid_path = path.parent / ".animator.pid"
            pid_path.write_text("321", encoding="ascii")
            spawn_animator(path)
            self.assertEqual(pid_path.read_text(encoding="ascii"), "654")
        live.assert_called_once_with(321, path)
        popen.assert_called_once()

    @patch("konsole_pal.core._run_quiet", return_value=True)
    @patch("konsole_pal.core.find_qdbus", return_value="/usr/bin/qdbus")
    def test_focus_targets_the_originating_window_and_session(self, _find, run):
        self.assertTrue(focus_tab(self.target))
        command = run.call_args.args[0]
        self.assertIn("/Windows/2", command)
        self.assertEqual(command[-1], "7")


if __name__ == "__main__":
    unittest.main()
