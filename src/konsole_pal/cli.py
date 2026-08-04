from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess  # nosec B404
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import __version__
from .config import (
    SETTING_KEYS,
    ConfigError,
    Settings,
    format_settings,
    load_settings,
    reset_agent_setting,
    reset_setting,
    reset_user_setting,
    user_config_path,
    write_agent_setting,
    write_setting,
    write_user_setting,
)
from .core import (
    VALID_STATES,
    acknowledge_attention,
    agent_display_name,
    animate,
    attention_states,
    default_label,
    event_to_state,
    find_qdbus,
    focus_tab,
    read_state,
    rename_session,
    repair_runtime,
    reset_runtime,
    runtime_dir,
    sanitize_label,
    target_from_env,
    update_session,
)
from .hooks import (
    AGENTS,
    HookConfigError,
    hooks_status,
    install_hooks,
    uninstall_hooks,
)
from .presentation import (
    brand,
    cell_width,
    compact_path,
    heading,
    message,
    rows,
    truncate,
)
from .sounds import SOUND_KINDS, SOUND_THEMES, find_audio_player, play_sound
from .themes import install_theme, list_themes, load_theme

STATE_PRESENTATION = {
    "working": ("Working", "info"),
    "idle": ("Resting", "idle"),
    "done": ("Complete", "ok"),
    "attention": ("Needs you", "warn"),
    "error": ("Failed", "error"),
}

BACKEND_LABELS = {
    "konsole": "Konsole",
    "zellij": "Zellij",
    "tmux": "tmux",
    "kitty": "Kitty",
    "wezterm": "WezTerm",
    "ghostty": "Ghostty",
    "osc": "SSH / terminal",
}

WATCH_ENTER = "\x1b[?1049h\x1b[?25l"
WATCH_REDRAW = "\x1b[H\x1b[J"
WATCH_EXIT = "\x1b[?25h\x1b[?1049l"


class PalHelpFormatter(argparse.RawDescriptionHelpFormatter):
    def _format_action(self, action: argparse.Action) -> str:
        if action.help == argparse.SUPPRESS:
            return ""
        return super()._format_action(action)


def _print_brand(subtitle: str = "Your AI tabs, quietly alive.") -> None:
    print(brand(subtitle), end="")


def _prompt_choice(label: str, choices: tuple[str, ...], default: str) -> str:
    rendered = "/".join(
        choice.upper() if choice == default else choice for choice in choices
    )
    while True:
        answer = input(f"  {label} [{rendered}]: ").strip().lower()
        if not answer:
            return default
        if answer in choices:
            return answer
        print(message(f"Choose one of: {', '.join(choices)}.", state="warn"))


def _demo_speed(value: str) -> float:
    parsed = float(value)
    if not 0 <= parsed <= 10:
        raise argparse.ArgumentTypeError("speed must be between 0 and 10 seconds")
    return parsed


def _watch_interval(value: str) -> float:
    parsed = float(value)
    if not 0.25 <= parsed <= 60:
        raise argparse.ArgumentTypeError("interval must be between 0.25 and 60 seconds")
    return parsed


def _terminal_label() -> str:
    target = target_from_env()
    if target is None:
        return "Not detected"
    return _backend_label(target.backend)


def _backend_label(backend: str) -> str:
    return BACKEND_LABELS.get(backend, backend.title())


def _agent_label(agent: str) -> str:
    return agent_display_name(agent)


def _task_label(state: Any) -> str:
    label = sanitize_label(state.label, 160)
    prefix = f"{_agent_label(state.agent)} · "
    return label[len(prefix) :] if label.startswith(prefix) else label


def _age_label(updated_at: float, *, now: float | None = None) -> str:
    age = max(0, int((time.time() if now is None else now) - updated_at))
    if age < 60:
        return "now"
    if age < 3_600:
        return f"{age // 60}m"
    if age < 86_400:
        return f"{age // 3_600}h"
    return f"{age // 86_400}d"


def _fit_rows(values: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    columns = max(20, shutil.get_terminal_size(fallback=(100, 24)).columns)
    available = columns - 5  # indent, marker, and separators added by row()
    label_cells = min(26, max(6, available // 3))
    detail_cells = max(1, available - label_cells)
    return [
        (
            truncate(sanitize_label(label, 160), label_cells),
            truncate(sanitize_label(detail, 320), detail_cells),
            state,
        )
        for label, detail, state in values
    ]


def _detail_line(label: str, value: str) -> str:
    prefix = f"      {label:<9} "
    columns = max(20, shutil.get_terminal_size(fallback=(100, 24)).columns)
    safe = sanitize_label(value, 320)
    return prefix + truncate(safe, max(1, columns - cell_width(prefix)))


def _detected_agents() -> list[str]:
    detected = [agent for agent in AGENTS if shutil.which(agent)]
    installed_hooks = [status.agent for status in hooks_status() if status.installed]
    return list(dict.fromkeys([*detected, *installed_hooks]))


def _settings_rows(settings: Settings) -> list[tuple[str, str, str]]:
    return [
        ("Preset", settings.preset.title(), "info"),
        (
            "Focus",
            {
                "all": "Completions + attention",
                "attention": "Attention only",
                "never": "Never",
            }[settings.focus],
            "info",
        ),
        (
            "Sound",
            "On" if settings.sound else "Off",
            "ok" if settings.sound else "idle",
        ),
        ("Volume", f"{round(settings.volume * 100)}%", "info"),
        ("Sound theme", settings.sound_theme.title(), "info"),
        (
            "Motion",
            "Gentle pulse" if settings.animation == "pulse" else "Still",
            "info",
        ),
        ("Sprite", settings.sprite_size.title(), "info"),
        ("Theme", settings.theme, "info"),
        ("Notifications", settings.notification.title(), "info"),
        ("Quiet hours", settings.quiet_hours, "info"),
        ("Cadence", f"{settings.animation_interval:g}s", "info"),
        ("Label width", f"{settings.label_max_cells} cells", "info"),
        (
            "Work timeout",
            "Never"
            if settings.working_timeout == 0
            else f"{settings.working_timeout:g}s",
            "info",
        ),
        (
            "Attention expiry",
            "Never"
            if settings.attention_timeout == 0
            else f"{settings.attention_timeout:g}s",
            "info",
        ),
    ]


def _payload_from_stdin() -> dict[str, Any]:
    try:
        if sys.stdin.isatty():
            return {}
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _target_or_error():
    target = target_from_env()
    if target is None:
        raise SystemExit(
            "konsole-pal: no supported terminal target found "
            "(Konsole, Zellij, tmux, or a controlling TTY is required)"
        )
    return target


def _load_settings_for(
    args: argparse.Namespace,
    payload: dict[str, Any] | None = None,
) -> Settings:
    overrides = {}
    if getattr(args, "no_sound", False):
        overrides["sound"] = False
    return load_settings(
        cwd=(payload or {}).get("cwd"),
        overrides=overrides,
        agent=getattr(args, "agent", None),
    )


def command_set(args: argparse.Namespace) -> int:
    target = _target_or_error()
    settings = _load_settings_for(args)
    update_session(
        args.state,
        agent=args.agent,
        label=args.label or default_label(args.agent),
        target=target,
        focus=args.focus,
        sound=False if args.no_sound else None,
        force_focus=args.focus,
        settings=settings,
    )
    return 0


def command_hook(args: argparse.Namespace) -> int:
    payload = _payload_from_stdin()
    target = target_from_env()
    mapped = event_to_state(args.agent, payload)
    if target is not None and mapped is not None:
        state, focus = mapped
        try:
            settings = _load_settings_for(args, payload)
        except ConfigError:
            settings = Settings(sound=not args.no_sound)
        update_session(
            state,
            agent=args.agent,
            label=args.label or default_label(args.agent, payload),
            target=target,
            focus=focus and not args.no_focus,
            sound=False if args.no_sound else None,
            settings=settings,
        )
    # Both Codex Stop and Cursor stop require JSON if a hook writes stdout.
    print("{}")
    return 0


def command_run(args: argparse.Namespace) -> int:
    if not args.command:
        raise SystemExit("konsole-pal run: provide a command after --")
    target = _target_or_error()
    label = args.label or default_label(args.agent)
    settings = _load_settings_for(args)
    update_session(
        "working", agent=args.agent, label=label, target=target, settings=settings
    )
    try:
        # `run` intentionally executes the command supplied by the local user.
        result = subprocess.run(args.command, check=False)  # nosec B603
    except KeyboardInterrupt:
        update_session(
            "attention",
            agent=args.agent,
            label=label,
            target=target,
            focus=not args.no_focus,
            settings=settings,
        )
        return 130
    final = "done" if result.returncode == 0 else "error"
    update_session(
        final,
        agent=args.agent,
        label=label,
        target=target,
        focus=not args.no_focus,
        settings=settings,
    )
    return result.returncode


def _active_items() -> list[tuple[Path, Any]]:
    directory = runtime_dir()
    items: list[tuple[Path, Any]] = []
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        try:
            state = read_state(path)
        except (OSError, ValueError, KeyError, TypeError):
            state = None
        if state is None:
            continue
        items.append((path, state))
    priority = {"attention": 0, "error": 0, "working": 1, "done": 2, "idle": 3}
    return sorted(
        items,
        key=lambda item: (
            priority.get(item[1].state, 4),
            -item[1].updated_at,
            item[1].label.casefold(),
        ),
    )


def _session_json(items: list[tuple[Path, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "state": state.state,
            "agent": state.agent,
            "label": state.label,
            "session": state.target.session_path,
            "service": state.target.service,
            "backend": state.target.backend,
            "pane": state.target.pane_id or None,
            "tab": state.target.tab_id or None,
        }
        for _path, state in items
    ]


def command_status(args: argparse.Namespace) -> int:
    states = _active_items()
    items = _session_json(states)
    if args.json:
        print(json.dumps(items, indent=2))
        return 0
    _print_brand("A calm view of every agent tab.")
    print()
    print(heading("Active tabs"))
    if not items:
        print(message("The burrow is quiet — no active agents.", state="idle"))
        print(message("Run `konsole-pal demo` to see every state.", state="info"))
        return 0
    display = []
    for item, (_path, state) in zip(items, states, strict=True):
        state_label, state_style = STATE_PRESENTATION.get(
            item["state"], (item["state"].title(), "warn")
        )
        detail = " · ".join(
            (
                state_label,
                item["agent"].title(),
                _backend_label(item["backend"]),
                _age_label(state.updated_at),
            )
        )
        if args.verbose:
            target = item["pane"] or item["tab"] or item["session"] or "current tab"
            detail += f" · {target}"
        display.append((item["label"], detail, state_style))
    print(rows(_fit_rows(display)))
    return 0


def _attention_items() -> list[tuple[Path, Any]]:
    return attention_states()


def _attention_json(items: list[tuple[Path, Any]]) -> list[dict[str, Any]]:
    now = time.time()
    return [
        {
            "id": state.target.key,
            "state": state.state,
            "agent": state.agent,
            "label": state.label,
            "backend": state.target.backend,
            "age_seconds": max(0, round(now - state.updated_at, 3)),
            "focusable": state.target.backend
            in {"konsole", "zellij", "tmux", "kitty", "wezterm"},
        }
        for _path, state in items
    ]


def _print_inbox(items: list[tuple[Path, Any]], *, verbose: bool = False) -> None:
    _print_brand("Only the agents that genuinely need you.")
    print()
    print(heading(f"Attention inbox · {len(items)} unread"))
    if not items:
        print(message("Nothing needs you right now.", state="idle"))
        return
    display = []
    now = time.time()
    for _path, state in items:
        detail = (
            f"{state.agent.title()} · {_backend_label(state.target.backend)}"
            f" · {_age_label(state.updated_at, now=now)}"
        )
        if verbose:
            detail += f" · {state.target.key}"
        display.append(
            (state.label, detail, "error" if state.state == "error" else "warn")
        )
    print(rows(_fit_rows(display)))


def _select_attention(identifier: str | None) -> tuple[Path, Any] | None:
    items = _attention_items()
    if not items:
        return None
    if not identifier:
        return items[0]
    needle = identifier.casefold()
    matches = [
        item
        for item in items
        if item[1].target.key.startswith(needle)
        or item[1].agent.casefold() == needle
        or needle in item[1].label.casefold()
    ]
    if len(matches) > 1:
        raise SystemExit(
            f"konsole-pal: {identifier!r} matches multiple items; use the displayed id"
        )
    return matches[0] if matches else None


def command_inbox(args: argparse.Namespace) -> int:
    items = _attention_items()
    if args.json:
        print(json.dumps(_attention_json(items), indent=2))
    else:
        _print_inbox(items, verbose=args.verbose)
    return 0


def command_next(args: argparse.Namespace) -> int:
    if args.target:
        selected = _select_attention(args.target)
    else:
        items = _attention_items()
        selected = next(
            (
                item
                for item in items
                if item[1].target.backend
                in {"konsole", "zellij", "tmux", "kitty", "wezterm"}
            ),
            items[0] if items else None,
        )
    if selected is None:
        print(message("Nothing needs you right now.", state="idle"))
        return 0
    path, state = selected
    if not acknowledge_attention(path, activate=True):
        print(
            message(
                f"{state.label} cannot be focused automatically on {state.target.backend}; it remains unread.",
                state="warn",
            )
        )
        return 1
    print(message(f"Jumped to {state.label} and cleared its unread state."))
    return 0


def command_ack(args: argparse.Namespace) -> int:
    items = _attention_items()
    selected = items if args.all else []
    if not args.all:
        item = _select_attention(args.target)
        selected = [item] if item else []
    changed = sum(
        1 for path, _state in selected if acknowledge_attention(path, activate=False)
    )
    print(message(f"Cleared {changed} attention item(s)."))
    return 0 if changed or not items else 1


@contextmanager
def _watch_input(enabled: bool):
    descriptor = None
    original = None
    termios_module = None
    if enabled:
        try:
            import termios
            import tty

            descriptor = sys.stdin.fileno()
            original = termios.tcgetattr(descriptor)
            tty.setcbreak(descriptor, termios.TCSANOW)
            termios_module = termios
        except (ImportError, OSError, ValueError):
            descriptor = None
            original = None
    try:
        yield descriptor
    finally:
        if descriptor is not None and original is not None and termios_module is not None:
            try:
                termios_module.tcsetattr(
                    descriptor, termios_module.TCSADRAIN, original
                )
            except OSError:
                pass


def _read_watch_key(descriptor: int | None, timeout: float) -> str | None:
    if descriptor is None:
        time.sleep(timeout)
        return None
    try:
        import select

        ready, _, _ = select.select([descriptor], [], [], timeout)
        if not ready:
            return None
        raw = os.read(descriptor, 1)
        if raw == b"\x1b":
            continuation, _, _ = select.select([descriptor], [], [], 0.02)
            if continuation:
                raw += os.read(descriptor, 2)
    except OSError:
        return None
    if raw in {b"q", b"Q", b"\x03"}:
        return "quit"
    if raw in {b"j", b"J", b"\x1b[B"}:
        return "down"
    if raw in {b"k", b"K", b"\x1b[A"}:
        return "up"
    if raw in {b"\r", b"\n", b" "}:
        return "toggle"
    if raw in {b"f", b"F"}:
        return "focus"
    if raw in {b"a", b"A"}:
        return "acknowledge"
    if raw in {b"t", b"T"}:
        return "rename"
    if raw == b"/":
        return "filter"
    if raw == b"\x1b":
        return "clear_filter"
    return None


def _move_watch_selection(
    keys: list[str], selected: str | None, direction: int
) -> str | None:
    if not keys:
        return None
    if selected not in keys:
        return keys[0]
    return keys[(keys.index(selected) + direction) % len(keys)]


def _filter_watch_items(
    items: list[tuple[Path, Any]], query: str
) -> list[tuple[Path, Any]]:
    needle = query.strip().casefold()
    if not needle:
        return items
    aliases = {
        "waiting": "attention",
        "failed": "error",
        "completed": "done",
        "complete": "done",
        "resting": "idle",
    }
    needle = aliases.get(needle, needle)
    filtered = []
    for item in items:
        state = item[1]
        state_label = STATE_PRESENTATION.get(
            state.state, (state.state.title(), "warn")
        )[0]
        fields = (
            state.agent,
            _agent_label(state.agent),
            _task_label(state),
            state.state,
            state_label,
            state.target.backend,
            _backend_label(state.target.backend),
        )
        if any(needle in field.casefold() for field in fields):
            filtered.append(item)
    return filtered


def _selected_watch_item(
    items: list[tuple[Path, Any]], selected_key: str | None
) -> tuple[Path, Any] | None:
    return next(
        (item for item in items if item[1].target.key == selected_key),
        None,
    )


def _run_watch_action(
    action: str, selected: tuple[Path, Any] | None
) -> tuple[str, str]:
    if selected is None:
        return "Select a session first.", "warn"
    path, state = selected
    agent = _agent_label(state.agent)
    if action == "focus":
        if focus_tab(state.target):
            return f"Focused {agent}.", "ok"
        return (
            f"{_backend_label(state.target.backend)} cannot be focused automatically.",
            "warn",
        )
    if action == "acknowledge":
        if state.state not in {"attention", "error"}:
            return f"{agent} has no unread request to acknowledge.", "warn"
        if acknowledge_attention(path, activate=False):
            return f"Acknowledged {agent}.", "ok"
        return f"Could not acknowledge {agent}; its state changed.", "warn"
    return "Unknown action.", "warn"


def _watch_snapshot(items: list[tuple[Path, Any]], *, now: float) -> tuple[Any, ...]:
    return tuple(
        (
            state.target.key,
            state.state,
            state.agent,
            state.label,
            state.updated_at,
            _age_label(state.updated_at, now=now),
        )
        for _path, state in items
    )


def _watch_footer(text: str) -> str:
    columns = max(20, shutil.get_terminal_size(fallback=(100, 24)).columns)
    return message(truncate(text, max(1, columns - 4)), state="info")


def _watch_prompt(title: str, current: str, prompt: str) -> str | None:
    print(WATCH_REDRAW, end="")
    _print_brand("A short label is enough; private agent content stays private.")
    print()
    print(heading(title))
    if current:
        print(message(f"Current: {sanitize_label(current, 160)}", state="info"))
    try:
        return input(f"  {prompt}: ").strip()
    except EOFError:
        return None


def _print_watch(
    items: list[tuple[Path, Any]],
    *,
    verbose: bool,
    once: bool,
    attention_only: bool,
    now: float,
    selected_key: str | None = None,
    expanded: set[str] | None = None,
    keyboard: bool = False,
    filter_query: str = "",
    total_count: int | None = None,
    notice: tuple[str, str] | None = None,
) -> None:
    expanded = expanded or set()
    subtitle = (
        "Only the agents that genuinely need you."
        if attention_only
        else "One live view of every agent session."
    )
    _print_brand(subtitle)
    print()
    needs_you = sum(state.state in {"attention", "error"} for _path, state in items)
    working = sum(state.state == "working" for _path, state in items)
    title = "Attention inbox" if attention_only else "Live agent view"
    if filter_query:
        title += f" · filter: {sanitize_label(filter_query, 40)}"
    print(heading(title))
    if notice:
        print(message(notice[0], state=notice[1]))
    if not items:
        if filter_query:
            empty = f"No sessions match {filter_query!r}."
        else:
            empty = (
                "Nothing needs you right now."
                if attention_only
                else "The burrow is quiet — no active agents."
            )
        print(message(empty, state="idle"))
        if not attention_only:
            print(message("Run `konsole-pal demo` to see every state.", state="info"))
        if not once:
            print()
            print(_watch_footer("/ filters · Esc clears · q closes"))
        return

    if filter_query and total_count is not None:
        summary = f"{len(items)} of {total_count} sessions"
    else:
        summary = f"{len(items)} session{'s' if len(items) != 1 else ''}"
    if working:
        summary += f" · {working} working"
    if needs_you:
        summary += f" · {needs_you} need{'s' if needs_you == 1 else ''} you"
    summary_state = "warn" if needs_you else "info" if working else "idle"
    print(message(summary, state=summary_state))
    print()

    display = []
    focusable = {"konsole", "zellij", "tmux", "kitty", "wezterm"}
    for _path, state in items:
        state_label, state_style = STATE_PRESENTATION.get(
            state.state, (state.state.title(), "warn")
        )
        detail = " · ".join(
            (
                state_label,
                _task_label(state),
                _backend_label(state.target.backend),
                _age_label(state.updated_at, now=now),
            )
        )
        if (
            state.state in {"attention", "error"}
            and state.target.backend not in focusable
        ):
            detail += " · notify only"
        if verbose:
            detail += f" · {state.target.key}"
        selector = "› " if keyboard and state.target.key == selected_key else "  "
        display.append((selector + _agent_label(state.agent), detail, state_style))

    rendered_rows = rows(_fit_rows(display)).splitlines()
    for line, (_path, state) in zip(rendered_rows, items, strict=True):
        print(line)
        if state.target.key not in expanded:
            continue
        state_label = STATE_PRESENTATION.get(
            state.state, (state.state.title(), "warn")
        )[0]
        terminal = _backend_label(state.target.backend)
        target = state.target.pane_id or state.target.tab_id
        if target:
            terminal += f" · {target}"
        print(_detail_line("Task", _task_label(state)))
        print(_detail_line("Status", f"{state_label} · {_age_label(state.updated_at, now=now)}"))
        print(_detail_line("Terminal", terminal))
        print(_detail_line("Session", state.target.key))
    if not once:
        print()
        footer = (
            "↑↓ move · Enter details · f focus · a ack · t task · / filter · q close"
            if keyboard
            else "Ctrl-C closes · `konsole-pal next` jumps to unread work."
        )
        print(_watch_footer(footer))


def command_watch(args: argparse.Namespace) -> int:
    previous: tuple[Any, ...] | None = None
    interactive = (
        sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb" and not args.once
    )
    keyboard = interactive and sys.stdin.isatty()
    attention_only = getattr(args, "attention_only", False)
    selected_key: str | None = None
    expanded: set[str] = set()
    seed_expanded = getattr(args, "expanded", False)
    filter_query = ""
    notice: tuple[str, str] | None = None
    try:
        if interactive:
            print(WATCH_ENTER, end="", flush=True)
        while True:
            pending_prompt: tuple[str, tuple[Path, Any] | None] | None = None
            with _watch_input(keyboard) as descriptor:
                while True:
                    source_items = (
                        _attention_items() if attention_only else _active_items()
                    )
                    items = _filter_watch_items(source_items, filter_query)
                    keys = [state.target.key for _path, state in items]
                    expanded.intersection_update(keys)
                    if keyboard:
                        selected_key = _move_watch_selection(keys, selected_key, 0)
                    if seed_expanded:
                        working_keys = [
                            state.target.key
                            for _path, state in items
                            if state.state == "working"
                        ]
                        expanded.update(working_keys)
                        if keyboard and working_keys:
                            selected_key = working_keys[0]
                        seed_expanded = False
                    now = time.time()
                    snapshot = (
                        _watch_snapshot(items, now=now),
                        selected_key,
                        tuple(sorted(expanded)),
                        filter_query,
                        notice,
                    )
                    if snapshot != previous:
                        if interactive:
                            print(WATCH_REDRAW, end="")
                        _print_watch(
                            items,
                            verbose=args.verbose,
                            once=args.once,
                            attention_only=attention_only,
                            now=now,
                            selected_key=selected_key,
                            expanded=expanded,
                            keyboard=keyboard,
                            filter_query=filter_query,
                            total_count=len(source_items),
                            notice=notice,
                        )
                        sys.stdout.flush()
                        previous = snapshot
                    if args.once:
                        return 0
                    action = _read_watch_key(descriptor, args.interval)
                    if action == "quit":
                        return 0
                    if action == "down":
                        selected_key = _move_watch_selection(keys, selected_key, 1)
                    elif action == "up":
                        selected_key = _move_watch_selection(keys, selected_key, -1)
                    elif action == "toggle" and selected_key is not None:
                        if selected_key in expanded:
                            expanded.remove(selected_key)
                        else:
                            expanded.add(selected_key)
                    elif action in {"focus", "acknowledge"}:
                        notice = _run_watch_action(
                            action, _selected_watch_item(items, selected_key)
                        )
                        previous = None
                    elif action == "clear_filter":
                        filter_query = ""
                        notice = ("Filter cleared.", "info")
                        previous = None
                    elif action == "filter":
                        pending_prompt = (action, None)
                        break
                    elif action == "rename":
                        selected = _selected_watch_item(items, selected_key)
                        if selected is None:
                            notice = ("Select a session first.", "warn")
                            previous = None
                        else:
                            pending_prompt = (action, selected)
                            break

            if pending_prompt is None:
                continue
            action, selected = pending_prompt
            if action == "filter":
                value = _watch_prompt(
                    "Filter sessions", filter_query, "Filter (blank clears)"
                )
                if value is not None:
                    filter_query = value
                    notice = (
                        f"Filtering by {value}." if value else "Filter cleared.",
                        "info",
                    )
            elif selected is not None:
                path, state = selected
                value = _watch_prompt(
                    f"Edit {_agent_label(state.agent)} task",
                    _task_label(state),
                    "Task label",
                )
                if value:
                    if rename_session(path, value):
                        notice = (f"Updated {_agent_label(state.agent)} task.", "ok")
                    else:
                        notice = ("Could not update that task label.", "warn")
                elif value == "":
                    notice = ("Task label unchanged.", "info")
            previous = None
    except KeyboardInterrupt:
        return 130
    finally:
        if interactive:
            print(WATCH_EXIT, end="", flush=True)


def command_emit(args: argparse.Namespace) -> int:
    target = _target_or_error()
    settings = _load_settings_for(args)
    update_session(
        args.state,
        agent=args.agent,
        label=args.label or default_label(args.agent),
        target=target,
        focus=args.focus,
        sound=False if args.no_sound else None,
        settings=settings,
    )
    if args.json:
        print(
            json.dumps(
                {
                    "state": args.state,
                    "agent": args.agent,
                    "target": target.key,
                }
            )
        )
    return 0


def _command_theme(args: argparse.Namespace) -> int:
    if args.theme_action == "install":
        theme = install_theme(Path(args.path))
        print(message(f"Installed validated theme {theme.name}."))
        return 0
    themes = list_themes()
    if args.theme_action == "list":
        if args.json:
            print(
                json.dumps(
                    [
                        {"name": theme.name, "description": theme.description}
                        for theme in themes
                    ],
                    indent=2,
                )
            )
        else:
            _print_brand("Fixed-width companions for every terminal.")
            print()
            print(rows([(theme.name, theme.description, "info") for theme in themes]))
        return 0
    theme = load_theme(args.name)
    size = args.sprite_size
    _print_brand(f"Theme preview · {theme.name}")
    print()
    preview = [
        ("Working", "  ".join(theme.frames[size]), "info"),
        *[
            (
                state.title(),
                theme.finals[size][state],
                "warn" if state == "attention" else "info",
            )
            for state in ("idle", "done", "attention", "error")
        ],
    ]
    print(rows(preview))
    return 0


def command_theme(args: argparse.Namespace) -> int:
    try:
        return _command_theme(args)
    except ValueError as error:
        raise ConfigError(str(error)) from error


def command_sound(args: argparse.Namespace) -> int:
    settings = _load_settings_for(args)
    theme = args.theme or settings.sound_theme
    if not settings.sound or not play_sound(
        args.kind,
        volume=settings.volume,
        theme=theme,
    ):
        raise SystemExit("konsole-pal: no supported audio player or sound is disabled")
    return 0


def command_config(args: argparse.Namespace) -> int:
    project_path = Path.cwd() / ".konsole-pal.toml"
    if args.config_action == "show":
        settings = load_settings(cwd=Path.cwd(), agent=args.agent)
        if args.json:
            print(json.dumps(settings.to_dict(), indent=2))
        elif args.toml:
            print(format_settings(settings), end="")
        else:
            _print_brand("Preferences that keep your workspace calm.")
            print()
            print(heading("Preferences"))
            print(rows(_settings_rows(settings)))
            if args.verbose:
                print()
                print(
                    message(
                        f"User config: {compact_path(user_config_path())}", state="info"
                    )
                )
                print(
                    message(
                        f"Project config: {compact_path(project_path)}", state="info"
                    )
                )
        return 0
    target_path = project_path if args.project else user_config_path()
    if args.config_action == "set":
        if args.agent:
            path = write_agent_setting(target_path, args.agent, args.key, args.value)
        else:
            path = (
                write_setting(target_path, args.key, args.value)
                if args.project
                else write_user_setting(args.key, args.value)
            )
        print(message(f"Updated {args.key.replace('_', ' ')} in {compact_path(path)}."))
        return 0
    if args.agent:
        path = reset_agent_setting(target_path, args.agent, args.key)
    else:
        path = (
            reset_setting(target_path, args.key)
            if args.project
            else reset_user_setting(args.key)
        )
    label = args.key.replace("_", " ") if args.key else "settings"
    print(message(f"Reset {label} in {compact_path(path)}."))
    return 0


def command_hooks(args: argparse.Namespace) -> int:
    agents = args.agents or list(AGENTS)
    invalid = sorted(set(agents) - set(AGENTS))
    if invalid:
        raise HookConfigError(f"unsupported agent: {invalid[0]}")
    if args.hooks_action == "install":
        changed = install_hooks(agents, force=args.force)
        print(
            message(
                f"Connected {', '.join(agent.title() for agent in agents)}; {len(changed)} file(s) changed."
            )
        )
        return 0
    if args.hooks_action == "uninstall":
        changed = uninstall_hooks(agents)
        print(
            message(
                f"Disconnected {', '.join(agent.title() for agent in agents)}; {len(changed)} file(s) changed."
            )
        )
        return 0
    okay = True
    statuses = hooks_status(agents)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "agent": status.agent,
                        "installed": status.installed,
                        "valid": status.valid,
                        "private": status.private,
                        "path": str(status.path),
                    }
                    for status in statuses
                ],
                indent=2,
            )
        )
    else:
        _print_brand("Agent connections and their health.")
        print()
        print(heading("Agents"))
    display = []
    for status in statuses:
        state = "installed" if status.installed else "missing"
        validity = "valid" if status.valid else "invalid"
        privacy = "private" if status.private else "permissions-warning"
        if not args.json:
            detail = f"{state.title()} · {validity} · {privacy}"
            if args.verbose:
                detail += f" · {compact_path(status.path)}"
            style = (
                "ok" if status.installed and status.valid and status.private else "warn"
            )
            display.append((status.agent.title(), detail, style))
        okay = okay and status.installed and status.valid and status.private
    if display:
        print(rows(display))
    return 0 if okay else 1


def command_reset(_args: argparse.Namespace) -> int:
    restored, removed = reset_runtime()
    print(
        message(f"Restored {restored} title(s) and cleared {removed} runtime file(s).")
    )
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    agents = _detected_agents() or ["codex", "cursor"]
    repair_messages = []
    if args.repair:
        try:
            install_hooks(
                [
                    status.agent
                    for status in hooks_status(agents)
                    if not status.installed
                ]
            )
        except HookConfigError as error:
            repair_messages.append(("Hook repair", str(error), "warn"))
        stale_states, stale_pid = repair_runtime()
        repair_messages.append(
            (
                "Runtime repair",
                f"{stale_states} stale state(s); worker {'removed' if stale_pid else 'already clean'}",
                "ok",
            )
        )
    try:
        load_settings()
        settings_valid = True
    except ConfigError:
        settings_valid = False
    checks = [
        ("terminal target", bool(target_from_env())),
        ("qdbus (Konsole)", bool(find_qdbus())),
        ("zellij", bool(shutil.which("zellij"))),
        ("tmux", bool(shutil.which("tmux"))),
        ("audio player", bool(find_audio_player())),
        ("Python >= 3.10", sys.version_info >= (3, 10)),
        ("settings", settings_valid),
    ]
    for status in hooks_status(agents):
        checks.append(
            (
                f"{status.agent} hooks",
                status.valid and status.installed and status.private,
            )
        )
    if args.json:
        print(json.dumps({"checks": {name: okay for name, okay in checks}}, indent=2))
    else:
        _print_brand("A quick health check for your terminal companion.")
        print()
        if repair_messages:
            print(heading("Repair"))
            print(rows(repair_messages))
            print()
        print(heading("System check"))
        optional = {"zellij", "tmux"}
        target = target_from_env()
        if target is None or target.backend != "konsole":
            optional.add("qdbus (Konsole)")
        print(
            rows(
                [
                    (
                        name,
                        "Ready"
                        if okay
                        else ("Optional" if name in optional else "Not detected"),
                        "ok" if okay else ("idle" if name in optional else "warn"),
                    )
                    for name, okay in checks
                ]
            )
        )
    essential = [
        okay for name, okay in checks if name in {"Python >= 3.10", "settings"}
    ]
    return 0 if all(essential) else 1


def command_setup(args: argparse.Namespace) -> int:
    detected = _detected_agents()
    agents = args.agents or detected or ["codex", "cursor"]
    invalid = sorted(set(agents) - set(AGENTS))
    if invalid:
        raise HookConfigError(f"unsupported agent: {invalid[0]}")
    interactive = not (args.yes or args.json) and sys.stdin.isatty()
    if interactive:
        _print_brand("Three small choices, then you're ready.")
        print()
        print(heading("Choose your setup"))
    preset = getattr(args, "preset", None) or (
        _prompt_choice(
            "Choose a personality", ("minimal", "balanced", "playful"), "balanced"
        )
        if interactive
        else "balanced"
    )
    focus = args.focus or (
        _prompt_choice("Select tab focus", ("attention", "all", "never"), "attention")
        if interactive
        else "attention"
    )
    sprite_size = args.sprite_size or (
        _prompt_choice("Choose sprite size", ("small", "medium", "large"), "medium")
        if interactive
        else "medium"
    )
    animation = args.animation or (
        _prompt_choice("Choose motion", ("pulse", "static"), "pulse")
        if interactive
        else "pulse"
    )
    theme = getattr(args, "theme", None) or (
        _prompt_choice("Choose a companion", ("rabbit", "ascii", "mono"), "rabbit")
        if interactive
        else "rabbit"
    )
    write_user_setting("preset", preset)
    write_user_setting("focus", focus)
    write_user_setting("sprite_size", sprite_size)
    write_user_setting("animation", animation)
    write_user_setting("theme", theme)
    changed = install_hooks(agents, force=args.force)
    settings = load_settings()
    sound_ready = bool(find_audio_player())
    if args.preview_sound and settings.sound:
        play_sound("done", volume=settings.volume)
    result = {
        "terminal": _terminal_label(),
        "agents": agents,
        "focus": focus,
        "preset": preset,
        "sprite_size": sprite_size,
        "animation": animation,
        "theme": theme,
        "sound": sound_ready,
        "hook_files_changed": len(changed),
    }
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    if not interactive:
        _print_brand("Your companion is ready to settle in.")
    print()
    print(heading("Setup complete"))
    print(
        rows(
            [
                (
                    "Terminal",
                    result["terminal"],
                    "ok" if result["terminal"] != "Not detected" else "warn",
                ),
                ("Agents", " · ".join(agent.title() for agent in agents), "ok"),
                ("Preset", preset.title(), "ok"),
                (
                    "Focus",
                    {
                        "attention": "Attention only",
                        "all": "Completions + attention",
                        "never": "Never",
                    }[focus],
                    "ok",
                ),
                ("Sprite", sprite_size.title(), "ok"),
                ("Motion", "Gentle pulse" if animation == "pulse" else "Still", "ok"),
                ("Theme", theme.title(), "ok"),
                (
                    "Sound",
                    "Ready" if sound_ready else "Player not found",
                    "ok" if sound_ready else "warn",
                ),
            ]
        )
    )
    print()
    print(
        message(
            f"Restart {', '.join(agent.title() for agent in agents)}, then let the rabbit work."
        )
    )
    print(message("Try `konsole-pal demo` for a quick preview.", state="info"))
    return 0


def command_demo(args: argparse.Namespace) -> int:
    target = _target_or_error()
    loaded = load_settings()
    settings = replace(
        loaded,
        focus="never",
        sound=bool(args.sound),
        sprite_size=args.sprite_size or loaded.sprite_size,
    )
    label = args.label or "Konsole Pal · preview"
    sequence = (
        ("working", "Working — the activity marker pulses."),
        ("attention", "Needs you — approval or input requested."),
        ("done", "Complete — the rabbit settles down."),
    )
    _print_brand("A short tour of the rabbit's states.")
    print()
    for index, (state, description) in enumerate(sequence):
        update_session(
            state,
            agent="demo",
            label=label,
            target=target,
            focus=False,
            sound=bool(args.sound),
            settings=settings,
        )
        state_label, style = STATE_PRESENTATION[state]
        print(message(f"{state_label}: {description}", state=style))
        if index + 1 < len(sequence) and args.speed:
            time.sleep(args.speed)
    return 0


def _completion_script(shell: str) -> str:
    commands = "setup status inbox next ack watch emit doctor demo run config hooks theme sound completions reset set"
    settings = " ".join(SETTING_KEYS)
    if shell == "bash":
        return f'''_konsole_pal() {{
  local current="${{COMP_WORDS[COMP_CWORD]}}"
  if [[ $COMP_CWORD -eq 1 ]]; then
    COMPREPLY=( $(compgen -W "{commands}" -- "$current") )
  elif [[ ${{COMP_WORDS[1]}} == config && ${{COMP_WORDS[2]}} == set ]]; then
    COMPREPLY=( $(compgen -W "{settings}" -- "$current") )
  fi
}}
complete -F _konsole_pal konsole-pal kp'''
    if shell == "zsh":
        return f"""#compdef konsole-pal kp
_arguments '1:command:({commands})' '*::argument:->args' """
    if shell == "fish":
        return "\n".join(
            f"complete -c konsole-pal -n '__fish_use_subcommand' -a {command}"
            for command in commands.split()
        )
    return f'''Register-ArgumentCompleter -Native -CommandName konsole-pal,kp -ScriptBlock {{
  param($wordToComplete, $commandAst, $cursorPosition)
  "{commands}".Split() | Where-Object {{ $_ -like "$wordToComplete*" }}
}}'''


def command_completions(args: argparse.Namespace) -> int:
    print(_completion_script(args.shell))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="konsole-pal",
        description=("  🐇  Konsole Pal\n       Your AI tabs, quietly alive."),
        epilog=(
            "Start here:\n"
            "  konsole-pal setup       connect your agents\n"
            "  konsole-pal demo        preview every rabbit state\n"
            "  konsole-pal status      see active agent tabs"
        ),
        formatter_class=PalHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(
        dest="command_name",
        required=True,
        title="commands",
        metavar="<command>",
    )

    setup_parser = sub.add_parser("setup", help="guided first-run setup")
    setup_parser.add_argument("agents", nargs="*", metavar="AGENT")
    setup_parser.add_argument("--focus", choices=("all", "attention", "never"))
    setup_parser.add_argument("--preset", choices=("minimal", "balanced", "playful"))
    setup_parser.add_argument("--animation", choices=("pulse", "static"))
    setup_parser.add_argument("--sprite-size", choices=("small", "medium", "large"))
    setup_parser.add_argument("--theme", choices=("rabbit", "ascii", "mono"))
    setup_parser.add_argument("--preview-sound", action="store_true")
    setup_parser.add_argument("--yes", action="store_true", help="accept calm defaults")
    setup_parser.add_argument("--force", action="store_true")
    setup_parser.add_argument("--json", action="store_true")
    setup_parser.set_defaults(func=command_setup)

    status_parser = sub.add_parser("status", help="show active agent tabs")
    status_parser.add_argument("--json", action="store_true")
    status_parser.add_argument("--verbose", action="store_true")
    status_parser.set_defaults(func=command_status)

    inbox_parser = sub.add_parser("inbox", help="show unread agent requests")
    inbox_parser.add_argument("--json", action="store_true")
    inbox_parser.add_argument("--verbose", action="store_true")
    inbox_parser.set_defaults(func=command_inbox)

    next_parser = sub.add_parser("next", help="jump to the newest unread agent")
    next_parser.add_argument("target", nargs="?")
    next_parser.set_defaults(func=command_next)

    ack_parser = sub.add_parser("ack", help="clear an unread agent request")
    ack_parser.add_argument("target", nargs="?")
    ack_parser.add_argument("--all", action="store_true")
    ack_parser.set_defaults(func=command_ack)

    watch_parser = sub.add_parser("watch", help="watch every agent session live")
    watch_parser.add_argument("--interval", type=_watch_interval, default=1.0)
    watch_parser.add_argument("--verbose", action="store_true")
    watch_parser.add_argument(
        "--attention-only",
        action="store_true",
        help="show only sessions waiting for you",
    )
    watch_parser.add_argument(
        "--expanded",
        action="store_true",
        help="start with working-session details expanded",
    )
    watch_parser.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    watch_parser.set_defaults(func=command_watch)

    doctor_parser = sub.add_parser("doctor", help="check local integration health")
    doctor_parser.add_argument("--repair", action="store_true")
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.set_defaults(func=command_doctor)

    demo_parser = sub.add_parser("demo", help="preview the rabbit's states")
    demo_parser.add_argument("--speed", type=_demo_speed, default=0.8)
    demo_parser.add_argument("--sound", action="store_true")
    demo_parser.add_argument("--sprite-size", choices=("small", "medium", "large"))
    demo_parser.add_argument("--label")
    demo_parser.set_defaults(func=command_demo)

    set_parser = sub.add_parser("set", help="set this tab's state")
    set_parser.add_argument("state", choices=sorted(VALID_STATES))
    set_parser.add_argument("--agent", default="agent")
    set_parser.add_argument("--label")
    set_parser.add_argument("--focus", action="store_true")
    set_parser.add_argument("--no-sound", action="store_true")
    set_parser.set_defaults(func=command_set)

    emit_parser = sub.add_parser("emit", help="emit a normalized agent event")
    emit_parser.add_argument("state", choices=sorted(VALID_STATES))
    emit_parser.add_argument("--agent", default="agent")
    emit_parser.add_argument("--label")
    emit_parser.add_argument("--focus", action="store_true")
    emit_parser.add_argument("--no-sound", action="store_true")
    emit_parser.add_argument("--json", action="store_true")
    emit_parser.set_defaults(func=command_emit)

    hook_parser = sub.add_parser("hook", help=argparse.SUPPRESS)
    hook_parser.add_argument("--agent", choices=AGENTS, required=True)
    hook_parser.add_argument("--label")
    hook_parser.add_argument("--no-focus", action="store_true")
    hook_parser.add_argument("--no-sound", action="store_true")
    hook_parser.set_defaults(func=command_hook)

    run_parser = sub.add_parser("run", help="wrap any process as one working task")
    run_parser.add_argument("--agent", default="agent")
    run_parser.add_argument("--label")
    run_parser.add_argument("--no-focus", action="store_true")
    run_parser.add_argument("--no-sound", action="store_true")
    run_parser.add_argument("command", nargs=argparse.REMAINDER)
    run_parser.set_defaults(func=command_run)

    sound_parser = sub.add_parser("sound", help="preview a notification sound")
    sound_parser.add_argument("kind", choices=SOUND_KINDS)
    sound_parser.add_argument("--theme", choices=SOUND_THEMES)
    sound_parser.set_defaults(func=command_sound)

    theme_parser = sub.add_parser("theme", help="list, preview, or install themes")
    theme_sub = theme_parser.add_subparsers(dest="theme_action", required=True)
    theme_list = theme_sub.add_parser("list")
    theme_list.add_argument("--json", action="store_true")
    theme_list.set_defaults(func=command_theme)
    theme_preview = theme_sub.add_parser("preview")
    theme_preview.add_argument("name")
    theme_preview.add_argument(
        "--sprite-size", choices=("small", "medium", "large"), default="medium"
    )
    theme_preview.set_defaults(func=command_theme)
    theme_install = theme_sub.add_parser("install")
    theme_install.add_argument("path")
    theme_install.set_defaults(func=command_theme)

    config_parser = sub.add_parser("config", help="show or edit layered settings")
    config_sub = config_parser.add_subparsers(dest="config_action", required=True)
    config_show = config_sub.add_parser("show", help="show effective settings")
    config_show.add_argument("--json", action="store_true")
    config_show.add_argument("--toml", action="store_true")
    config_show.add_argument("--verbose", action="store_true")
    config_show.add_argument("--agent")
    config_show.set_defaults(func=command_config)
    config_set = config_sub.add_parser("set", help="set one setting")
    config_set.add_argument("key", choices=SETTING_KEYS)
    config_set.add_argument("value")
    config_set.add_argument("--project", action="store_true")
    config_set.add_argument("--agent")
    config_set.set_defaults(func=command_config)
    config_reset = config_sub.add_parser("reset", help="reset one or all settings")
    config_reset.add_argument("key", choices=SETTING_KEYS, nargs="?")
    config_reset.add_argument("--project", action="store_true")
    config_reset.add_argument("--agent")
    config_reset.set_defaults(func=command_config)

    hooks_parser = sub.add_parser("hooks", help="manage terminal agent integrations")
    hooks_sub = hooks_parser.add_subparsers(dest="hooks_action", required=True)
    for action in ("install", "status", "uninstall"):
        action_parser = hooks_sub.add_parser(action)
        action_parser.add_argument("agents", nargs="*", metavar="AGENT")
        if action == "install":
            action_parser.add_argument("--force", action="store_true")
        if action == "status":
            action_parser.add_argument("--json", action="store_true")
            action_parser.add_argument("--verbose", action="store_true")
        action_parser.set_defaults(
            func=command_hooks,
            force=False,
            json=False,
            verbose=False,
        )

    completions_parser = sub.add_parser(
        "completions", help="generate shell completions"
    )
    completions_parser.add_argument(
        "shell", choices=("bash", "zsh", "fish", "powershell")
    )
    completions_parser.set_defaults(func=command_completions)

    reset_parser = sub.add_parser(
        "reset", help="stop workers and restore resting titles"
    )
    reset_parser.set_defaults(func=command_reset)

    return parser


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "_animate":
        return animate(Path(sys.argv[2]))
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "command", None) and args.command[0:1] == ["--"]:
        args.command = args.command[1:]
    try:
        return int(args.func(args))
    except (ConfigError, HookConfigError) as error:
        parser.error(str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
