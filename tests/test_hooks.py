import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from konsole_pal.hooks import (
    AGENTS,
    HookConfigError,
    codex_config_path,
    codex_ssh_title_ready,
    configure_codex_ssh_title,
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

    def test_remote_codex_install_disables_competing_title_and_preserves_toml(self):
        config = codex_config_path(self.home)
        config.parent.mkdir()
        original = (
            'model = "gpt-test"\n\n'
            "[features]\n"
            "hooks = true\n\n"
            "[mcp_servers.example]\n"
            'command = "keep-me"\n'
        )
        config.write_text(original, encoding="utf-8")
        remote = {"SSH_CONNECTION": "client 1 server 22", "TERM": "linux"}

        changed = install_hooks(
            ["codex"],
            home=self.home,
            executable=self.executable,
            env=remote,
        )

        self.assertEqual(changed, [hook_path("codex", self.home), config])
        updated = config.read_text(encoding="utf-8")
        self.assertIn('model = "gpt-test"', updated)
        self.assertIn('[mcp_servers.example]\ncommand = "keep-me"', updated)
        self.assertIn("[tui]", updated)
        self.assertIn("terminal_title = []", updated)
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        backup = config.with_suffix(".toml.konsole-pal.bak")
        self.assertEqual(backup.read_text(encoding="utf-8"), original)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertTrue(codex_ssh_title_ready(home=self.home, env=remote))
        self.assertEqual(
            install_hooks(
                ["codex"],
                home=self.home,
                executable=self.executable,
                env=remote,
            ),
            [],
        )

    def test_remote_codex_title_replaces_multiline_value_inside_tui_table(self):
        config = codex_config_path(self.home)
        config.parent.mkdir()
        config.write_text(
            "[tui]\n"
            "animations = false\n"
            "terminal_title = [\n"
            '  "spinner",\n'
            '  "project",\n'
            "]\n\n"
            "[features]\n"
            "hooks = true\n",
            encoding="utf-8",
        )
        remote = {"SSH_TTY": "/dev/pts/4"}

        self.assertEqual(
            configure_codex_ssh_title(home=self.home, env=remote), config
        )

        updated = config.read_text(encoding="utf-8")
        self.assertIn("animations = false", updated)
        self.assertIn("terminal_title = []", updated)
        self.assertNotIn('"spinner"', updated)
        self.assertTrue(codex_ssh_title_ready(home=self.home, env=remote))

    def test_codex_title_config_is_unchanged_locally_or_inside_multiplexer(self):
        config = codex_config_path(self.home)
        config.parent.mkdir()
        original = '[tui]\nterminal_title = ["spinner", "project"]\n'
        config.write_text(original, encoding="utf-8")

        self.assertIsNone(configure_codex_ssh_title(home=self.home, env={}))
        self.assertIsNone(
            configure_codex_ssh_title(
                home=self.home,
                env={"SSH_TTY": "/dev/pts/2", "TMUX": "/tmp/tmux,1,0"},
            )
        )
        self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_invalid_codex_toml_is_never_overwritten(self):
        config = codex_config_path(self.home)
        config.parent.mkdir()
        config.write_text("[tui\n", encoding="utf-8")

        with self.assertRaisesRegex(HookConfigError, "invalid Codex config.toml"):
            configure_codex_ssh_title(
                home=self.home, env={"SSH_CONNECTION": "client server"}
            )
        self.assertEqual(config.read_text(encoding="utf-8"), "[tui\n")

    def test_nested_tui_table_is_never_corrupted(self):
        config = codex_config_path(self.home)
        config.parent.mkdir()
        original = '[tui.theme]\nname = "keep-me"\n'
        config.write_text(original, encoding="utf-8")

        try:
            changed = configure_codex_ssh_title(
                home=self.home, env={"SSH_CONNECTION": "client server"}
            )
        except HookConfigError:
            self.assertEqual(config.read_text(encoding="utf-8"), original)
        else:
            self.assertEqual(changed, config)
            self.assertIn('name = "keep-me"', config.read_text(encoding="utf-8"))
            self.assertTrue(
                codex_ssh_title_ready(
                    home=self.home, env={"SSH_CONNECTION": "client server"}
                )
            )

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
