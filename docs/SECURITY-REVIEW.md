# Security and privacy review — 0.8.0

Reviewed: 2026-08-03

## Scope

The review covered tracked source and history, package metadata, generated
release artifacts, hook merging, runtime state, title rendering, subprocess
boundaries, screenshots, and idle resource behavior.

## Data flow

Agent lifecycle JSON enters through standard input. The event name, optional
working directory basename, and terminal routing target are reduced to a small
state record. Prompts, responses, transcripts, tool input, environment dumps,
and command output are neither retained nor transmitted.

The package contains no network client. Python 3.11+ declares no runtime
dependencies; Python 3.10 uses only the `tomli` standard-library backport.
Generated sounds and state remain in the user's private runtime directory.

## Controls

- Runtime directories are created with mode 0700; state, PID, sound, config, hook,
  and backup files are mode 0600.
- State/config writes use temporary files followed by atomic replacement.
- Hook installation merges existing JSON, preserves unrelated entries, creates a
  backup, and fails closed on malformed input unless the user supplies `--force`.
- Terminal labels remove control characters, normalize whitespace, and enforce a
  terminal-cell limit before reaching D-Bus or OSC.
- OSC output strips escape and bell characters and writes only to `/dev/tty` or a
  validated `/dev/pts/*` path.
- External processes use explicit argument arrays without a shell, bounded
  timeouts where appropriate, closed file descriptors, and suppressed output.
- Focus requests expire quickly and are consumed atomically, preventing stale
  completions from selecting a tab later.
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

The shared pulse worker is unchanged from the 0.7.0 measurement of approximately
21.5 MiB RSS and effectively 0% of one CPU while sleeping at the default 0.85 s
cadence. A 30-run 0.8.0 hook benchmark averaged 48.4 ms. Measurements vary by
Python, kernel, and hardware. Static animation mode exits after its first render.

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
