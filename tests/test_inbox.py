import tempfile
import unittest
from unittest.mock import patch

from konsole_pal.core import (
    KonsoleTarget,
    SessionState,
    acknowledge_attention,
    attention_states,
    read_state,
    write_state,
)


class AttentionInboxTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.env = {"XDG_RUNTIME_DIR": self.temporary.name}

    @staticmethod
    def target(number: int) -> KonsoleTarget:
        return KonsoleTarget(
            "org.kde.konsole",
            f"/Sessions/{number}",
            "/Windows/1",
        )

    def write(self, number: int, state: str, updated_at: float, timeout=86_400):
        item = SessionState(
            state,
            f"Agent {number}",
            "codex",
            self.target(number),
            updated_at=updated_at,
            attention_timeout=timeout,
        )
        return write_state(item, self.env)

    def test_only_fresh_attention_is_unread_and_newest_comes_first(self):
        self.write(1, "done", 990)
        self.write(2, "attention", 980)
        self.write(3, "error", 995)
        self.write(4, "attention", 800, timeout=60)

        items = attention_states(self.env, now=1_000)

        self.assertEqual([state.label for _path, state in items], ["Agent 3", "Agent 2"])

    @patch("konsole_pal.core.set_title", return_value=True)
    @patch("konsole_pal.core.focus_tab", return_value=True)
    def test_jump_focuses_then_changes_attention_to_resting(self, focus, _title):
        path = self.write(1, "attention", 1_000)

        self.assertTrue(acknowledge_attention(path, activate=True))

        focus.assert_called_once_with(self.target(1))
        state = read_state(path)
        self.assertEqual(state.state, "idle")
        self.assertFalse(state.focus)

    @patch("konsole_pal.core.focus_tab", return_value=False)
    def test_failed_jump_remains_unread(self, _focus):
        path = self.write(1, "attention", 1_000)

        self.assertFalse(acknowledge_attention(path, activate=True))

        self.assertEqual(read_state(path).state, "attention")


if __name__ == "__main__":
    unittest.main()
