# Changelog

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
