# Security and privacy review — 1.0.0

Reviewed: 2026-08-04

## Scope

The review covered tracked source and history, package metadata, generated
release artifacts, hook merging, runtime state, title rendering, subprocess
boundaries, attention acknowledgement, terminal notifications, custom theme
validation, package-manager recipes, screenshots, and idle resource behavior.

## Data flow

Agent lifecycle JSON enters through standard input. The event name, optional
working directory basename, and terminal routing target are reduced to a small
state record. Prompts, responses, transcripts, tool input, environment dumps,
and command output are neither retained nor transmitted.

The package contains no network client. Python 3.11+ declares no runtime
dependencies; Python 3.10 uses only the `tomli` standard-library backport.
Generated sounds and state remain in the user's private runtime directory.
Notifications contain a generic state plus the already-visible, width-limited
tab label; hook payload messages and transcript paths are discarded.
The interactive watch view expands only that sanitized label and routing metadata.
Its direct actions focus an existing target, acknowledge one selected unread state,
or replace only the selected display label; they never execute agent-generated text.
An optional `KONSOLE_PAL_TASK` value is explicit user-provided display text; task
descriptions are never inferred from prompts, responses, or hook messages.

## Controls

- Runtime directories are created with mode 0700; state, PID, sound, config, hook,
  and backup files are mode 0600.
- State/config writes use temporary files followed by atomic replacement.
- Hook installation merges existing JSON, preserves unrelated entries, creates a
  backup, and fails closed on malformed input unless the user supplies `--force`.
- OpenCode receives a local event-only plugin; Aider uses its documented
  notification command. Neither adapter reads transcripts or prompt text.
- Custom themes are inert JSON. Names, control characters, completeness, and
  equal terminal-cell widths are validated before an atomic private install.
- Terminal labels remove ANSI escape sequences and control characters, normalize
  whitespace, and enforce a terminal-cell limit before reaching D-Bus or OSC.
- OSC output strips escape and bell characters and writes only to `/dev/tty` or a
  validated `/dev/pts/*` path.
- External processes use explicit argument arrays without a shell, bounded
  timeouts where appropriate, closed file descriptors, and suppressed output.
- Focus requests expire quickly and are consumed atomically, preventing stale
  completions from selecting a tab later.
- Attention remains unread until focus succeeds; unsupported targets cannot
  silently acknowledge or discard an approval request.
- Animator PID reuse is checked against `/proc` before signaling or reuse.

## Automated checks

- Repository secret scan: no tracked secrets; cache-tag false positives excluded.
- Personal/device-data scan: no tracked username, home directory, hostname, email,
  machine identifier, credential, or real device path.
- Bandit: no unresolved findings. Deliberate subprocess calls are documented
  inline; all use argument arrays and no shell.
- `pip-audit`: no known vulnerability in the Python 3.10 `tomli` backport.
- Ruff, bytecode compilation, wheel/sdist metadata checks, and the complete test
  suite must pass before release.
- Real tmux and Zellij integration tests verify stable target selection.
- Visual tests assert identical cell widths and label offsets across every icon.
- Presentation tests verify clean non-TTY output, `NO_COLOR`, JSON-only machine
  output, and aligned human-readable rows.

## Performance measurement

A five-second 1.0.0 measurement recorded approximately 19.6 MiB RSS and 0.0% of
one CPU while the shared worker slept at the default 0.85 s cadence. A 30-run
hook benchmark averaged 54.8 ms (58.2 ms p95). Measurements vary by Python,
kernel, and hardware. Static animation mode exits after its first render.

## Screenshot provenance

README images were captured from a real Konsole 26.04 process inside a dedicated
Xvfb display. The tab and CLI demos contain synthetic project text only. Every
image was visually reviewed and stripped of metadata; none contains desktop
content, account names, hostnames, filesystem paths, notifications, or device
identifiers.

## Residual risks

- Terminal/D-Bus behavior can change across upstream releases.
- A compromised local account can alter that account's hooks or runtime files;
  this tool does not attempt to defend against an already-compromised user.
- The `run` subcommand deliberately executes the command supplied by the local
  user. It should not be exposed as a privileged service or to untrusted input.
- No review can prove the absence of every vulnerability. Please use private
  vulnerability reporting for suspected issues.
