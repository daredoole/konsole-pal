import json
import tempfile
import unittest
from pathlib import Path

from konsole_pal.hooks import (
    HookConfigError,
    hook_path,
    hooks_status,
    install_hooks,
    uninstall_hooks,
)


class HookManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.executable = "/opt/konsole pal/bin/konsole-pal"

    def test_install_is_private_idempotent_and_preserves_other_hooks(self):
        path = hook_path("codex", self.home)
        path.parent.mkdir()
        unrelated = {"type": "command", "command": "notify-me"}
        path.write_text(
            json.dumps({"hooks": {"Stop": [{"hooks": [unrelated]}]}}),
            encoding="utf-8",
        )

        changed = install_hooks(
            ["codex"], home=self.home, executable=self.executable
        )
        self.assertEqual(changed, [path])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(path.with_suffix(".json.bak").exists())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["hooks"]["Stop"][0]["hooks"][0], unrelated)
        commands = json.dumps(data)
        self.assertIn("'/opt/konsole pal/bin/konsole-pal' hook --agent codex", commands)

        before = path.read_bytes()
        self.assertEqual(
            install_hooks(["codex"], home=self.home, executable=self.executable), []
        )
        self.assertEqual(path.read_bytes(), before)

    def test_install_and_uninstall_both_agents_only_removes_ours(self):
        install_hooks(
            ["codex", "cursor"], home=self.home, executable="/bin/konsole-pal"
        )
        statuses = hooks_status(home=self.home)
        self.assertTrue(all(item.valid and item.installed and item.private for item in statuses))

        cursor_path = hook_path("cursor", self.home)
        cursor_data = json.loads(cursor_path.read_text(encoding="utf-8"))
        cursor_data["hooks"]["stop"].append({"command": "keep-me"})
        cursor_path.write_text(json.dumps(cursor_data), encoding="utf-8")
        changed = uninstall_hooks(home=self.home)
        self.assertEqual(set(changed), {hook_path("codex", self.home), cursor_path})
        remaining = json.loads(cursor_path.read_text(encoding="utf-8"))
        self.assertEqual(remaining["hooks"]["stop"], [{"command": "keep-me"}])
        self.assertTrue(all(not item.installed for item in hooks_status(home=self.home)))

    def test_install_replaces_legacy_misspelled_hook(self):
        path = hook_path("codex", self.home)
        path.parent.mkdir()
        legacy = "/old/bin/knsole-pal hook --agent codex"
        path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {"hooks": [{"type": "command", "command": legacy}]},
                            {"hooks": [{"type": "command", "command": "keep-me"}]},
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        install_hooks(["codex"], home=self.home, executable=self.executable)
        data = json.loads(path.read_text(encoding="utf-8"))
        serialized = json.dumps(data)
        self.assertNotIn(legacy, serialized)
        self.assertIn("bin/konsole-pal", serialized)
        self.assertIn("hook --agent codex", serialized)
        self.assertIn("keep-me", serialized)

    def test_invalid_json_requires_force_and_is_backed_up(self):
        path = hook_path("cursor", self.home)
        path.parent.mkdir()
        path.write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(HookConfigError, "invalid hook config"):
            install_hooks(
                ["cursor"], home=self.home, executable="/bin/konsole-pal"
            )

        install_hooks(
            ["cursor"],
            home=self.home,
            executable="/bin/konsole-pal",
            force=True,
        )
        self.assertEqual(path.with_suffix(".json.bak").read_text(), "{broken")
        self.assertTrue(hooks_status(["cursor"], home=self.home)[0].installed)


if __name__ == "__main__":
    unittest.main()
