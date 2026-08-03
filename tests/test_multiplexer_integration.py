import json
import os
import pty
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from knsole_pal.core import KonsoleTarget, focus_tab, set_title

RUN_INTEGRATION = os.environ.get("KNSOLE_PAL_INTEGRATION") == "1"


def run(command, *, timeout=10):
    return subprocess.run(
        command,
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    ).stdout.strip()


@unittest.skipUnless(RUN_INTEGRATION, "set KNSOLE_PAL_INTEGRATION=1")
class MultiplexerIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("tmux"), "tmux is not installed")
    def test_real_tmux_title_and_focus_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            socket = str(Path(directory) / "tmux.sock")
            tmux = shutil.which("tmux")
            try:
                run([tmux, "-S", socket, "new-session", "-d", "-s", "kp", "-n", "origin"])
                pane = run(
                    [tmux, "-S", socket, "display-message", "-p", "-t", "kp:origin", "#{pane_id}"]
                )
                run([tmux, "-S", socket, "new-window", "-d", "-t", "kp", "-n", "other"])
                target = KonsoleTarget(
                    "", "", "", backend="tmux", multiplexer_session=socket, pane_id=pane
                )

                self.assertTrue(set_title(target, "🐇💤 integration"))
                self.assertTrue(focus_tab(target))

                title = run(
                    [tmux, "-S", socket, "display-message", "-p", "-t", pane, "#{window_name}"]
                )
                active = run(
                    [tmux, "-S", socket, "display-message", "-p", "-t", "kp", "#{window_name}"]
                )
                self.assertEqual(title, "🐇💤 integration")
                self.assertEqual(active, title)
            finally:
                subprocess.run(
                    [tmux, "-S", socket, "kill-server"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )

    @unittest.skipUnless(shutil.which("zellij"), "zellij is not installed")
    def test_real_zellij_title_and_focus_handoff(self):
        zellij = shutil.which("zellij")
        session = f"kp-integration-{uuid.uuid4().hex[:12]}"
        client = None
        master = None
        slave = None
        try:
            run([zellij, "attach", "--create-background", session])
            master, slave = pty.openpty()
            client = subprocess.Popen(
                [zellij, "attach", session],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env={**os.environ, "TERM": "xterm-256color"},
                close_fds=True,
            )
            time.sleep(0.5)
            pane_name = run(
                [
                    zellij,
                    "--session",
                    session,
                    "action",
                    "new-pane",
                    "--name",
                    "worker",
                    "--",
                    "sleep",
                    "20",
                ]
            )
            pane_id = pane_name.removeprefix("terminal_")
            panes = json.loads(
                run([zellij, "--session", session, "action", "list-panes", "--json"])
            )
            pane = next(item for item in panes if str(item["id"]) == pane_id)
            target = KonsoleTarget(
                "",
                "",
                "",
                backend="zellij",
                multiplexer_session=session,
                pane_id=pane_name,
                tab_id=str(pane["tab_id"]),
            )

            run(
                [
                    zellij,
                    "--session",
                    session,
                    "action",
                    "new-pane",
                    "--",
                    "sleep",
                    "20",
                ]
            )
            self.assertTrue(set_title(target, "🐇💤 integration"))
            self.assertTrue(focus_tab(target))

            after = json.loads(
                run([zellij, "--session", session, "action", "list-panes", "--json"])
            )
            selected = next(item for item in after if str(item["id"]) == pane_id)
            self.assertEqual(selected["tab_name"], "🐇💤 integration")
            self.assertTrue(selected["is_focused"])
        finally:
            if client is not None:
                client.terminate()
                try:
                    client.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    client.kill()
                    client.wait(timeout=2)
            for descriptor in (master, slave):
                if descriptor is not None:
                    os.close(descriptor)
            subprocess.run(
                [zellij, "kill-session", session],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )


if __name__ == "__main__":
    unittest.main()
