# knsole-pal 🐰

[![CI](https://github.com/daredoole/knsole-pal/actions/workflows/ci.yml/badge.svg)](https://github.com/daredoole/knsole-pal/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/knsole-pal.svg)](https://pypi.org/project/knsole-pal/)
[![Python](https://img.shields.io/pypi/pyversions/knsole-pal.svg)](https://pypi.org/project/knsole-pal/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/daredoole/knsole-pal/blob/main/LICENSE)

A tiny, low-resource tab companion for terminal AI agents. It shows when Codex
or Cursor is working, rests when the task is done, calls attention to questions
and approvals, and returns you to the right tab without scraping terminal output.

<p align="center">
  <img src="https://raw.githubusercontent.com/daredoole/knsole-pal/main/docs/images/working.gif" alt="Real Konsole window showing the anchored knsole-pal rabbit and pulsing activity sparkle" width="768">
</p>

The rabbit stays in one fixed position. Only the equal-width activity symbol
changes, so the tab label never shifts and the tab never grows or shrinks.

## What you get

- **Working:** `🐇✨` / `🐇⭐` — a gentle fixed-width pulse.
- **Completed:** `🐇💤` — a resting bunny plus a soft ascending chime.
- **Needs you:** `🐇❗` — tab handoff plus a short “meeep.”
- **Failed:** `🐇💥` — a visible error state and attention sound.
- One sleeping animator for every tab, not one process per tab.
- Native Konsole, Zellij, tmux, SSH, and generic terminal support.
- Safe hook installation that merges existing configuration and creates backups.
- No network access, telemetry, prompt storage, or transcript storage.

## Real state previews

These screenshots were captured from a real Konsole 26.04 session on an isolated
display containing only the controlled demo. They contain no username, hostname,
filesystem path, notifications, or device information.

<p align="center">
  <img src="https://raw.githubusercontent.com/daredoole/knsole-pal/main/docs/images/done.png" alt="Konsole tab showing the resting completion bunny" width="49%">
  <img src="https://raw.githubusercontent.com/daredoole/knsole-pal/main/docs/images/attention.png" alt="Konsole tab showing the attention bunny for a permission request" width="49%">
</p>

## Install in three commands

Python 3.10 or newer is required. [pipx](https://pipx.pypa.io/) is recommended
because it installs the CLI in an isolated environment.

```sh
pipx install knsole-pal
knsole-pal hooks install
knsole-pal doctor
```

Restart Codex and Cursor after installing the hooks. That is it.

Prefer `uv`?

```sh
uv tool install knsole-pal
knsole-pal hooks install
```

For native Konsole control, install whichever Qt D-Bus command your distribution
provides: `qdbus6`, `qdbus-qt6`, or `qdbus`. Without it, knsole-pal safely
falls back to the standard terminal-title escape sequence.

For sound, it automatically uses the first available player: PipeWire
(`pw-play`), PulseAudio (`paplay`), or ALSA (`aplay`).

## Agent setup

The installer preserves unrelated hooks, writes mode-0600 files, and makes a
`hooks.json.bak` before changing an existing file.

```sh
# Both agents
knsole-pal hooks install

# Or one agent
knsole-pal hooks install codex
knsole-pal hooks install cursor

# Confirm the active configuration
knsole-pal hooks status
```

Codex events map as follows:

| Event | Result |
| --- | --- |
| `SessionStart` | Resting |
| `UserPromptSubmit` | Working |
| `PreToolUse(request_user_input)` | Needs you |
| `PermissionRequest` | Needs you |
| `Stop` | Completed, or needs you when the final message is a question |

Cursor's `beforeSubmitPrompt` starts work and `stop` completes it. Example hook
files are available in [examples](https://github.com/daredoole/knsole-pal/tree/main/examples)
for users who prefer manual setup.

## Terminal support

| Environment | Status display | Attention handoff |
| --- | --- | --- |
| Konsole | Native D-Bus tab title | Selects the originating Konsole tab |
| Zellij | Stable tab ID; pane fallback | Focuses the originating pane/tab |
| tmux | Stable window title and socket | Selects the originating window/pane |
| SSH / other terminals | OSC title on the original PTY | Terminal bell |

Nested environments are detected automatically in this order: Zellij, tmux,
Konsole, then generic OSC. Remote agents cannot and should not force-focus a
local GUI window; SSH uses the title plus terminal bell instead.

## Settings

Settings are layered from lowest to highest priority:

1. Built-in defaults
2. User config: `~/.config/knsole-pal/config.toml`
3. Nearest project config: `.knsole-pal.toml`
4. `KNSOLE_PAL_*` environment variables
5. Command flags

```sh
knsole-pal config show
knsole-pal config set focus attention
knsole-pal config set volume 0.45
knsole-pal config set animation static --project
knsole-pal config reset volume
```

| Setting | Values | Default |
| --- | --- | --- |
| `focus` | `all`, `attention`, `never` | `all` |
| `sound` | `true`, `false` | `true` |
| `volume` | `0.0`–`1.0` | `0.7` |
| `animation` | `pulse`, `static` | `pulse` |
| `animation_interval` | `0.2`–`10.0` seconds | `0.85` |
| `label_max_cells` | `8`–`80` | `26` |
| `working_timeout` | `0` or `60`–`604800` seconds | `86400` |

`focus=attention` is a calmer setup: questions, approvals, and errors select
their tabs, while normal completions only rest and chime. `animation=static`
shows one working icon and immediately exits the animator for effectively zero
idle resource use.

See [examples/config.toml](https://github.com/daredoole/knsole-pal/blob/main/examples/config.toml)
for a complete file.

## Manual use

Wrap any command:

```sh
knsole-pal run --agent tests --label "Test suite" -- pytest
```

Or update the current tab directly:

```sh
knsole-pal set working --agent codex
knsole-pal set attention --agent codex --focus
knsole-pal status
knsole-pal sound done
knsole-pal sound attention
```

## Maintenance and troubleshooting

```sh
knsole-pal doctor
knsole-pal doctor --repair
knsole-pal hooks status
knsole-pal reset
```

- **No bunny:** run `knsole-pal doctor --repair`, restart the agent, and submit a prompt.
- **No sound:** run `knsole-pal sound done` and install PipeWire, PulseAudio, or ALSA tools.
- **Too much tab switching:** run `knsole-pal config set focus attention`.
- **Prefer no motion:** run `knsole-pal config set animation static`.
- **Stale title:** run `knsole-pal reset`.
- **SSH:** install knsole-pal and its hooks on the remote host as well.

`reset` stops the shared animator, restores resting titles, and removes runtime
state. It does not remove settings or agent hooks.

## Privacy and security

- Runtime files live under `$XDG_RUNTIME_DIR/knsole-pal` in a mode-0700 directory.
- State, configuration, generated sounds, hook files, and backups use mode 0600.
- State contains only a short display label and terminal routing identifiers.
- Prompts, responses, transcripts, command output, and environment dumps are never stored.
- Labels are stripped of control characters and width-limited before reaching a title.
- External programs are invoked with argument arrays and `shell=False`.
- Python 3.11+ has no runtime dependencies; Python 3.10 uses only the small
  `tomli` standard-library backport. The package performs no network requests or telemetry.

The `knsole-pal run -- ...` command intentionally executes exactly the local
command placed after `--`; it does not interpret it through a shell.

See [SECURITY.md](https://github.com/daredoole/knsole-pal/blob/main/SECURITY.md)
for reporting and
[docs/SECURITY-REVIEW.md](https://github.com/daredoole/knsole-pal/blob/main/docs/SECURITY-REVIEW.md)
for the release threat model
and audit results.

## Resource use

All tabs share one low-priority Python worker. It sleeps between 0.85-second
updates and exits after work ends. The 0.7.0 release measurement showed
effectively 0% CPU while sleeping, approximately 21.5 MiB RSS for the single
shared worker, and approximately 59 ms hook startup. Results vary by system.

Static animation mode exits immediately after rendering the working title.
Sounds are synthesized once, cached privately by volume, and played asynchronously.

## Uninstall

```sh
knsole-pal reset
knsole-pal hooks uninstall
pipx uninstall knsole-pal
```

If installed with uv, replace the last command with
`uv tool uninstall knsole-pal`. Settings remain in
`~/.config/knsole-pal/config.toml` until you remove them.

## Development

```sh
git clone https://github.com/daredoole/knsole-pal.git
cd knsole-pal
uv sync --extra dev
make verify
```

`make verify` runs Ruff, all unit tests, real tmux/Zellij integration tests
when available, bytecode compilation, wheel/sdist builds, and package metadata
validation. CI repeats the suite on Python 3.10 through 3.14.

Please read [CONTRIBUTING.md](https://github.com/daredoole/knsole-pal/blob/main/CONTRIBUTING.md),
the [changelog](https://github.com/daredoole/knsole-pal/blob/main/CHANGELOG.md),
and the [MIT license](https://github.com/daredoole/knsole-pal/blob/main/LICENSE).

## Design limits

- Focus handoff selects a tab inside its existing Konsole window. It does not
  force the Konsole application above unrelated desktop windows.
- Generic terminals and SSH receive an OSC title and bell, not local GUI control.
- Cursor's stop event does not always distinguish an answer from a question, so
  it receives the configured completion handoff.

## References

- [KDE: Scripting Konsole](https://docs.kde.org/trunk_kf6/en/konsole/konsole/scripting.html)
- [OpenAI: Codex hooks](https://developers.openai.com/codex/hooks)
- [Cursor: Agent best practices](https://cursor.com/blog/agent-best-practices)
- [Zellij: CLI recipes](https://zellij.dev/documentation/cli-recipes.html)
- [tmux manual](https://man7.org/linux/man-pages/man1/tmux.1.html)
