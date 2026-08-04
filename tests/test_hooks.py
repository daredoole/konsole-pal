import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from konsole_pal.hooks import (
    AGENTS,
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
        statuses = hooks_status(["codex", "cursor"], home=self.home)
        self.assertTrue(all(item.valid and item.installed and item.private for item in statuses))

        cursor_path = hook_path("cursor", self.home)
        cursor_data = json.loads(cursor_path.read_text(encoding="utf-8"))
        cursor_data["hooks"]["stop"].append({"command": "keep-me"})
        cursor_path.write_text(json.dumps(cursor_data), encoding="utf-8")
        changed = uninstall_hooks(["codex", "cursor"], home=self.home)
        self.assertEqual(set(changed), {hook_path("codex", self.home), cursor_path})
        remaining = json.loads(cursor_path.read_text(encoding="utf-8"))
        self.assertEqual(remaining["hooks"]["stop"], [{"command": "keep-me"}])
        self.assertTrue(
            all(
                not item.installed
                for item in hooks_status(["codex", "cursor"], home=self.home)
            )
        )

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

    def test_all_supported_agent_adapters_install_idempotently(self):
        changed = install_hooks(
            AGENTS,
            home=self.home,
            executable="/bin/konsole-pal",
        )

        self.assertEqual(set(changed), {hook_path(agent, self.home) for agent in AGENTS})
        statuses = hooks_status(AGENTS, home=self.home)
        self.assertTrue(all(item.valid and item.installed and item.private for item in statuses))
        self.assertEqual(
            install_hooks(AGENTS, home=self.home, executable="/bin/konsole-pal"),
            [],
        )

        claude = json.loads(hook_path("claude", self.home).read_text())
        self.assertIn("PermissionRequest", claude["hooks"])
        gemini = json.loads(hook_path("gemini", self.home).read_text())
        self.assertIn("AfterAgent", gemini["hooks"])
        copilot = json.loads(hook_path("copilot", self.home).read_text())
        self.assertIn("agentStop", copilot["hooks"])
        self.assertIn("Bun.spawn", hook_path("opencode", self.home).read_text())
        self.assertIn(
            "notifications-command:", hook_path("aider", self.home).read_text()
        )

    def test_uninstall_all_adapters_removes_only_our_entries(self):
        install_hooks(AGENTS, home=self.home, executable="/bin/konsole-pal")

        changed = uninstall_hooks(AGENTS, home=self.home)

        self.assertEqual(set(changed), {hook_path(agent, self.home) for agent in AGENTS})
        self.assertTrue(all(not item.installed for item in hooks_status(AGENTS, home=self.home)))

    @unittest.skipUnless(shutil.which("bun"), "Bun is required for plugin syntax QA")
    def test_generated_opencode_plugin_has_valid_javascript_syntax(self):
        install_hooks(
            ["opencode"], home=self.home, executable="/bin/konsole-pal"
        )

        result = subprocess.run(
            [shutil.which("bun"), "--check", str(hook_path("opencode", self.home))],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
