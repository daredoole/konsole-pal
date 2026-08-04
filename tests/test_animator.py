import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from konsole_pal.core import (
    FINAL_STATE_TTL,
    WORKING_STATE_TTL,
    KonsoleTarget,
    SessionState,
    _consume_focus,
    animate,
    prune_stale_states,
    read_state,
    write_state,
)


def target(number: int) -> KonsoleTarget:
    return KonsoleTarget(
        service="org.kde.konsole-test",
        session_path=f"/Sessions/{number}",
        window_path="/Windows/1",
    )


class AnimatorFocusTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.env = {"XDG_RUNTIME_DIR": self.temporary.name}

    def state(self, number, name, *, focus, updated_at=None):
        value = SessionState(
            name,
            f"agent-{number}",
            "codex",
            target(number),
            focus=focus,
            updated_at=time.time() if updated_at is None else updated_at,
        )
        return write_state(value, self.env)

    def run_animator(self, path):
        with (
            patch("konsole_pal.core.os.nice"),
            patch("konsole_pal.core.time.sleep"),
            patch("konsole_pal.core.set_title") as titles,
            patch("konsole_pal.core.focus_tab") as focuses,
        ):
            self.assertEqual(animate(path, interval=0), 0)
        return titles, focuses

    def test_old_trigger_path_cannot_replay_completed_tab_focus(self):
        old_path = self.state(1, "done", focus=True, updated_at=time.time() - 3600)

        titles, focuses = self.run_animator(old_path)

        titles.assert_not_called()
        focuses.assert_not_called()
        self.assertFalse(read_state(old_path).focus)

    def test_fresh_resting_state_renders_once_without_animating(self):
        path = self.state(14, "done", focus=False)

        titles, focuses = self.run_animator(path)

        titles.assert_called_once()
        self.assertEqual(titles.call_args.args[1], "🐇💤 agent-14")
        focuses.assert_not_called()

    def test_fresh_completion_focuses_once_across_animator_restarts(self):
        path = self.state(2, "done", focus=True)

        _, first_focuses = self.run_animator(path)
        _, second_focuses = self.run_animator(path)

        first_focuses.assert_called_once_with(target(2))
        second_focuses.assert_not_called()
        self.assertFalse(read_state(path).focus)

    def test_attention_beats_newer_ordinary_completion_at_startup(self):
        now = time.time()
        done_path = self.state(3, "done", focus=True, updated_at=now)
        attention_path = self.state(
            4, "attention", focus=True, updated_at=now - 1
        )

        _, focuses = self.run_animator(done_path)

        focuses.assert_called_once_with(target(4))
        self.assertFalse(read_state(done_path).focus)
        self.assertFalse(read_state(attention_path).focus)

    def test_newest_event_wins_when_priorities_match(self):
        now = time.time()
        older_path = self.state(5, "done", focus=True, updated_at=now - 1)
        newer_path = self.state(6, "done", focus=True, updated_at=now)

        _, focuses = self.run_animator(older_path)

        focuses.assert_called_once_with(target(6))
        self.assertFalse(read_state(older_path).focus)
        self.assertFalse(read_state(newer_path).focus)

    def test_focus_consume_never_overwrites_new_work(self):
        completed_at = time.time()
        path = self.state(7, "done", focus=True, updated_at=completed_at)
        self.state(7, "working", focus=False, updated_at=completed_at + 1)

        claimed, _ = _consume_focus(path, completed_at, activate=True)

        self.assertFalse(claimed)
        self.assertEqual(read_state(path).state, "working")

    def test_stale_state_pruning_uses_shorter_working_ttl(self):
        now = time.time()
        old_working = self.state(
            8, "working", focus=False, updated_at=now - WORKING_STATE_TTL - 1
        )
        old_done = self.state(
            9, "done", focus=False, updated_at=now - FINAL_STATE_TTL - 1
        )
        fresh = self.state(10, "done", focus=False, updated_at=now)

        self.assertEqual(prune_stale_states(fresh.parent, now=now), 2)
        self.assertFalse(old_working.exists())
        self.assertFalse(old_done.exists())
        self.assertTrue(fresh.exists())

    def test_corrupt_state_does_not_break_animator(self):
        path = self.state(11, "done", focus=False)
        corrupt = path.parent / "corrupt.json"
        corrupt.write_text("{broken", encoding="utf-8")

        _, focuses = self.run_animator(path)

        focuses.assert_not_called()

    def test_exit_recheck_prevents_lost_work_wakeup(self):
        path = self.state(13, "done", focus=False)
        with (
            patch("konsole_pal.core.os.nice"),
            patch("konsole_pal.core.time.sleep"),
            patch("konsole_pal.core.set_title"),
            patch(
                "konsole_pal.core._directory_has_animated_state",
                side_effect=(True, False),
            ) as recheck,
        ):
            self.assertEqual(animate(path, interval=0), 0)
        self.assertEqual(recheck.call_count, 2)

    def test_static_working_state_renders_once_then_worker_exits(self):
        value = SessionState(
            "working",
            "steady rabbit",
            "codex",
            target(15),
            animation="static",
        )
        path = write_state(value, self.env)

        titles, focuses = self.run_animator(path)

        titles.assert_called_once_with(target(15), "🐇✨ steady rabbit", persist=True)
        focuses.assert_not_called()

    def test_concurrent_writers_always_leave_valid_private_json(self):
        shared_target = target(12)

        def write(index):
            write_state(
                SessionState(
                    "working",
                    f"agent-{index}",
                    "codex",
                    shared_target,
                    updated_at=time.time(),
                ),
                self.env,
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write, range(200)))

        path = write_state(
            SessionState("done", "final", "codex", shared_target), self.env
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["label"], "final")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
