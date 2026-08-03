# knsole-pal 🐰

`knsole-pal` gives every Konsole AI-agent tab a tiny state indicator:

- `🐇` stays anchored while a gentle activity sparkle pulses beside it.
- `🐇❗` marks a permission request or question.
- `🐇💤` rests when the turn is complete.
- The relevant Konsole tab is selected when it needs attention or finishes.
- A soft ascending sparkle plays on completion; questions and approvals say “meeep.”

It is a small Python/stdlib CLI. It uses Konsole's own D-Bus session and window
interfaces, with an OSC title fallback, so there is no tray app, Electron UI, or
Konsole fork.

## Supported terminal layers

| Environment | Status display | Attention handoff |
| --- | --- | --- |
| Konsole | Native D-Bus tab title | Selects the originating Konsole tab |
| Zellij | Stable tab ID, with pane-title fallback | Focuses the originating pane/tab |
| tmux | Stable window title and server socket | Selects the originating window/pane |
| SSH / other terminals | OSC title sent through the originating PTY | Terminal bell; remote code cannot force-focus a local GUI |

Detection is automatic, including nested sessions. The nearest UI layer wins in
this order: Zellij, tmux, Konsole, then generic OSC.

## Install

Python 3.10+, Konsole, and one of `qdbus6`, `qdbus-qt6`, or `qdbus` are required.

```sh
pipx install .
knsole-pal hooks install
knsole-pal doctor
```

For development:

```sh
python -m venv .venv
.venv/bin/pip install -e .
make verify
```

The hook installer merges with existing Codex and Cursor hooks, creates
`hooks.json.bak` before a change, and writes private mode-0600 files. Restart the
agents after installation. Use `knsole-pal hooks status` to verify them.

## Connect Codex

`knsole-pal hooks install codex` manages `~/.codex/hooks.json`. The hooks map:

- `UserPromptSubmit` → working
- `PreToolUse(request_user_input)` → attention sound + select tab
- `PermissionRequest` → attention + select tab
- `Stop` → resting/attention + select tab
- `SessionStart` → resting

Codex project hooks can also live at `<repo>/.codex/hooks.json`, but Codex only
loads them after the project is trusted.

## Connect Cursor

`knsole-pal hooks install cursor` manages `~/.cursor/hooks.json`. Project-local
configuration can live at `<repo>/.cursor/hooks.json`.

Cursor's `beforeSubmitPrompt` starts the animation and `stop` rests the bunny and
selects the tab. Cursor's stop payload does not reliably distinguish a completed
answer from a question, so both receive the focus handoff.

## SSH

When the agent runs on a remote host, install `knsole-pal` and the relevant hook
file on that host too. The remote animator writes title escapes to its original
`/dev/pts/*`; SSH carries them back to Konsole or another compatible local
terminal. Custom WAV playback remains local-only, so remote completion and
attention also emit a terminal bell.

No SSH daemon changes, port forwarding, or remote GUI access are required.

## Manual use

Any long command can participate:

```sh
knsole-pal run --agent tests --label "Test suite" -- pytest
knsole-pal set attention --agent codex --focus
knsole-pal status
knsole-pal sound done
knsole-pal sound attention
```

To keep the title compact, use `--label`, for example:

```json
{ "type": "command", "command": "knsole-pal hook --agent codex --label API" }
```

## Settings

Settings are layered in this order: built-in defaults, user config, nearest
project config, environment variables, then command flags. User config lives at
`$XDG_CONFIG_HOME/knsole-pal/config.toml` (normally
`~/.config/knsole-pal/config.toml`); project config is `.knsole-pal.toml`.

```sh
knsole-pal config show
knsole-pal config set focus attention
knsole-pal config set volume 0.45
knsole-pal config set animation static --project
knsole-pal config reset volume
```

Available keys are `focus` (`all`, `attention`, `never`), `sound`, `volume`
(`0..1`), `animation` (`pulse`, `static`), `animation_interval`,
`label_max_cells`, and `working_timeout`. Uppercase `KNSOLE_PAL_*` environment
variables provide the same controls. See [`examples/config.toml`](examples/config.toml).

`focus=attention` is a calmer recommended setup: questions, approvals, and
errors switch tabs, while ordinary completions only rest the bunny and chime.
`animation=static` removes ongoing animation and lets the worker exit immediately.

## Maintenance

```sh
knsole-pal doctor
knsole-pal doctor --repair
knsole-pal hooks status
knsole-pal reset
knsole-pal hooks uninstall
```

`doctor --repair` safely reinstalls missing managed hooks and clears stale worker
metadata. `reset` stops the animator, restores resting titles, and clears runtime
state; it does not remove settings or hooks.

## Behavior and limits

- Focus handoff selects a tab inside its existing Konsole window. It intentionally
  does not force the whole Konsole application above other desktop windows.
- A Codex final message that looks like a question is shown as `🐇❗`; other stop
  events use `🐇💤`.
- Focus requests are one-shot events with a short freshness window. They are
  atomically consumed, so animator restarts cannot select tabs for old completed
  work; simultaneous requests prioritize input/approval, then the newest event.
- Hook state is kept in a private directory under `$XDG_RUNTIME_DIR` and contains
  only the agent label and Konsole D-Bus target—never prompts or transcripts.
- If D-Bus title setting is unavailable, the tool falls back to the standard
  terminal title escape sequence.
- Sound playback tries PipeWire (`pw-play`), PulseAudio (`paplay`), then ALSA
  (`aplay`). It is asynchronous and never holds up an agent hook.
- All tabs share one sleeping animator process. Frames update at 0.85-second
  intervals and use one D-Bus call per active tab; the low-priority worker exits
  when idle.
- Every animated prefix has the same code-point and terminal-cell width, keeping
  the rabbit, project label, and Konsole tab width fixed. Labels are
  control-character sanitized and capped at 26 terminal cells.
- Set `KNSOLE_PAL_SOUND=off` to silence sounds, or add `--no-sound` to an
  individual hook command. Volume-adjusted WAV files are generated once and
  cached privately.

## Release checks

`make verify` runs lint, the complete unit/integration suite, bytecode
compilation, wheel/sdist builds, and metadata validation. Real tmux and Zellij
handoffs are exercised when both programs are installed. CI repeats this on
Python 3.10 through 3.14 and publishes signed-in-by-identity PyPI releases only
from version tags.

## Why this approach

Konsole officially exposes the current session and window over D-Bus, and its tab
titles support the shell-set window title. Codex and Cursor both expose lifecycle
hooks, so hook events are more reliable and lighter than scraping terminal output
or guessing from process CPU usage.

## References

- [KDE: Scripting Konsole](https://docs.kde.org/trunk_kf6/en/konsole/konsole/scripting.html)
- [KDE: Konsole tab-title settings](https://docs.kde.org/trunk_kf6/en/konsole/konsole/console-dialogs.html)
- [OpenAI: Codex hooks](https://developers.openai.com/codex/hooks)
- [OpenAI: Codex advanced configuration](https://developers.openai.com/codex/config-advanced)
- [Cursor: Agent best practices and stop-hook example](https://cursor.com/blog/agent-best-practices)
- [Zellij: CLI recipes and stable pane/tab targeting](https://zellij.dev/documentation/cli-recipes.html)
- [tmux manual](https://man7.org/linux/man-pages/man1/tmux.1.html)
