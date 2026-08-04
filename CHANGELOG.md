# Changelog

## 1.0.1 - 2026-08-04

- Keep Codex agent titles visible over plain SSH by safely disabling its
  competing terminal-title spinner while preserving unrelated TOML and hook
  trust requirements.
- Set Konsole's displayed remote tab title directly with sanitized OSC 30 after
  the portable OSC 0 title sequence.
- Resolve the real controlling PTY for detached animation workers, including
  unprivileged Proxmox/LXC sessions, without permitting cross-tab writes.
- Expand SSH, Codex configuration, PTY, security, and idempotence regression
  coverage and document the repaired setup and diagnostics.

## 1.0.0 - 2026-08-04

- Add a durable attention inbox with deterministic `next` and `ack`, plus an
  interactive unified `watch` view with expandable, privacy-safe task details,
  direct focus and acknowledge actions, editable display labels, and filtering;
  focus succeeds before unread state is cleared.
- Add one normalized event contract and safe installers for Codex, Cursor,
  Claude Code, Gemini CLI, OpenCode, Copilot CLI, and Aider.
- Add Kitty, WezTerm, and Ghostty detection, focus backends where supported,
  clickable terminal notifications, SSH propagation, and local desktop fallback.
- Add minimal, balanced, and playful presets; per-agent/project policy; quiet
  hours; attention expiry; notification selection; and three sound themes.
- Add validated rabbit, ASCII, monochrome, and data-only custom visual themes.
- Add a native Nix package and deterministic checksum-pinned Homebrew/AUR asset
  generation to signed release automation.
- Preserve the single low-frequency worker, fixed-width visual contract,
  private atomic state, local-only operation, and transcript-free data model.

## 0.8.0 - 2026-08-03

- Add a dependency-free, color-aware CLI presentation system with a distinctive
  rabbit header, aligned health rows, friendly empty states, and `NO_COLOR` and
  non-TTY fallbacks.
- Add guided `setup`, live `demo`, shell `completions`, and JSON output for
  status, doctor, hooks, settings, and setup automation.
- Add small, medium, and large fixed-width sprite footprints without moving tab
  labels between frames or states.
- Hide private paths from normal human output while retaining explicit verbose
  and machine-readable views.

## 0.7.0 - 2026-08-03

- Correct the pre-release project spelling to `konsole-pal` across the package,
  executable, Python module, configuration, hooks, documentation, and repository.
- Migrate legacy `knsole-pal` hook entries safely while preserving unrelated hooks.
- Add validated user, project, environment, and command-level settings.
- Add safe Codex/Cursor hook install, status, uninstall, backup, and repair commands.
- Add configurable sound volume, focus policy, animation policy, cadence, label width, and stale-work timeout.
- Add static zero-idle-animation mode and runtime reset/repair commands.
- Add regression coverage for stale focus events, fixed-width animation, hooks, settings, audio, SSH PTYs, tmux, and Zellij.
- Add Python 3.10–3.14 CI, real multiplexer CI, verified package builds, and trusted release automation.
- Add real isolated-Konsole screenshots, a complete three-command quick start,
  troubleshooting, uninstall guidance, and PyPI-safe documentation links.
- Add enforced secret scanning, Bandit analysis, CodeQL, pinned GitHub Actions,
  private vulnerability reporting, and a published security/privacy review.

## 0.6.1 - 2026-08-03

- Anchor the rabbit while pulsing a fixed-width activity marker.
- Prevent old completion events from switching tabs after animator restarts.
- Share one low-frequency animator across all active tabs.
