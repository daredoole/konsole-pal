from __future__ import annotations

import argparse
import json
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .config import (
    SETTING_KEYS,
    ConfigError,
    Settings,
    format_settings,
    load_settings,
    reset_setting,
    reset_user_setting,
    user_config_path,
    write_setting,
    write_user_setting,
)
from .core import (
    VALID_STATES,
    animate,
    default_label,
    event_to_state,
    find_qdbus,
    read_state,
    repair_runtime,
    reset_runtime,
    runtime_dir,
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
from .sounds import SOUND_KINDS, find_audio_player, play_sound


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
            "knsole-pal: no supported terminal target found "
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
        raise SystemExit("knsole-pal run: provide a command after --")
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


def command_status(_args: argparse.Namespace) -> int:
    directory = runtime_dir()
    rows = []
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        try:
            state = read_state(path)
        except (OSError, ValueError, KeyError, TypeError):
            state = None
        if state is None:
            continue
        rows.append(
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
        )
    print(json.dumps(rows, indent=2))
    return 0


def command_sound(args: argparse.Namespace) -> int:
    settings = _load_settings_for(args)
    if not settings.sound or not play_sound(args.kind, volume=settings.volume):
        raise SystemExit("knsole-pal: no supported audio player or sound is disabled")
    return 0


def command_config(args: argparse.Namespace) -> int:
    project_path = Path.cwd() / ".knsole-pal.toml"
    if args.config_action == "show":
        settings = load_settings(cwd=Path.cwd())
        print(format_settings(settings), end="")
        print(f"# user: {user_config_path()}")
        print(f"# project: {project_path}")
        return 0
    target_path = project_path if args.project else user_config_path()
    if args.config_action == "set":
        path = (
            write_setting(target_path, args.key, args.value)
            if args.project
            else write_user_setting(args.key, args.value)
        )
        print(f"updated {path}")
        return 0
    path = (
        reset_setting(target_path, args.key)
        if args.project
        else reset_user_setting(args.key)
    )
    print(f"reset {path}")
    return 0


def command_hooks(args: argparse.Namespace) -> int:
    agents = args.agents or list(AGENTS)
    if args.hooks_action == "install":
        changed = install_hooks(agents, force=args.force)
        print(f"installed hooks for {', '.join(agents)} ({len(changed)} file(s) changed)")
        return 0
    if args.hooks_action == "uninstall":
        changed = uninstall_hooks(agents)
        print(f"removed hooks for {', '.join(agents)} ({len(changed)} file(s) changed)")
        return 0
    okay = True
    for status in hooks_status(agents):
        state = "installed" if status.installed else "missing"
        validity = "valid" if status.valid else "invalid"
        privacy = "private" if status.private else "permissions-warning"
        print(f"{status.agent}: {state}, {validity}, {privacy} — {status.path}")
        okay = okay and status.installed and status.valid and status.private
    return 0 if okay else 1


def command_reset(_args: argparse.Namespace) -> int:
    restored, removed = reset_runtime()
    print(f"restored {restored} title(s); removed {removed} runtime file(s)")
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    if args.repair:
        try:
            install_hooks(
                [status.agent for status in hooks_status() if not status.installed]
            )
        except HookConfigError as error:
            print(f"[WARN] hook repair: {error}")
        stale_states, stale_pid = repair_runtime()
        print(
            f"[REPAIR] removed {stale_states} stale state(s); "
            f"stale worker pid={'removed' if stale_pid else 'not found'}"
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
    for status in hooks_status():
        checks.append(
            (
                f"{status.agent} hooks",
                status.valid and status.installed and status.private,
            )
        )
    for name, okay in checks:
        print(f"[{'OK' if okay else 'WARN'}] {name}")
    essential = [okay for name, okay in checks if name in {"Python >= 3.10", "settings"}]
    return 0 if all(essential) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knsole-pal",
        description="Bunny-powered Konsole tab status for AI agents.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command_name", required=True)

    set_parser = sub.add_parser("set", help="set this tab's state")
    set_parser.add_argument("state", choices=sorted(VALID_STATES))
    set_parser.add_argument("--agent", default="agent")
    set_parser.add_argument("--label")
    set_parser.add_argument("--focus", action="store_true")
    set_parser.add_argument("--no-sound", action="store_true")
    set_parser.set_defaults(func=command_set)

    hook_parser = sub.add_parser("hook", help="consume an agent lifecycle hook")
    hook_parser.add_argument("--agent", choices=("codex", "cursor"), required=True)
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

    status_parser = sub.add_parser("status", help="show known tab states")
    status_parser.set_defaults(func=command_status)

    sound_parser = sub.add_parser("sound", help="preview a notification sound")
    sound_parser.add_argument("kind", choices=SOUND_KINDS)
    sound_parser.set_defaults(func=command_sound)

    config_parser = sub.add_parser("config", help="show or edit layered settings")
    config_sub = config_parser.add_subparsers(dest="config_action", required=True)
    config_show = config_sub.add_parser("show", help="show effective settings")
    config_show.set_defaults(func=command_config)
    config_set = config_sub.add_parser("set", help="set one setting")
    config_set.add_argument("key", choices=SETTING_KEYS)
    config_set.add_argument("value")
    config_set.add_argument("--project", action="store_true")
    config_set.set_defaults(func=command_config)
    config_reset = config_sub.add_parser("reset", help="reset one or all settings")
    config_reset.add_argument("key", choices=SETTING_KEYS, nargs="?")
    config_reset.add_argument("--project", action="store_true")
    config_reset.set_defaults(func=command_config)

    hooks_parser = sub.add_parser("hooks", help="manage Codex and Cursor hooks")
    hooks_sub = hooks_parser.add_subparsers(dest="hooks_action", required=True)
    for action in ("install", "status", "uninstall"):
        action_parser = hooks_sub.add_parser(action)
        action_parser.add_argument("agents", choices=AGENTS, nargs="*")
        if action == "install":
            action_parser.add_argument("--force", action="store_true")
        action_parser.set_defaults(func=command_hooks, force=False)

    reset_parser = sub.add_parser("reset", help="stop workers and restore resting titles")
    reset_parser.set_defaults(func=command_reset)

    doctor_parser = sub.add_parser("doctor", help="check local integration requirements")
    doctor_parser.add_argument("--repair", action="store_true")
    doctor_parser.set_defaults(func=command_doctor)

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
