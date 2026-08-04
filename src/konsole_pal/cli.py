from __future__ import annotations

import argparse
import json
import shutil
import subprocess  # nosec B404
import sys
import time
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
from .presentation import brand, compact_path, heading, message, rows
from .sounds import SOUND_KINDS, find_audio_player, play_sound

STATE_PRESENTATION = {
    "working": ("Working", "info"),
    "idle": ("Resting", "idle"),
    "done": ("Complete", "ok"),
    "attention": ("Needs you", "warn"),
    "error": ("Failed", "error"),
}


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


def _terminal_label() -> str:
    target = target_from_env()
    if target is None:
        return "Not detected"
    return {
        "konsole": "Konsole",
        "zellij": "Zellij",
        "tmux": "tmux",
        "osc": "SSH / terminal",
    }.get(target.backend, target.backend.title())


def _settings_rows(settings: Settings) -> list[tuple[str, str, str]]:
    return [
        ("Focus", {"all": "Completions + attention", "attention": "Attention only", "never": "Never"}[settings.focus], "info"),
        ("Sound", "On" if settings.sound else "Off", "ok" if settings.sound else "idle"),
        ("Volume", f"{round(settings.volume * 100)}%", "info"),
        ("Motion", "Gentle pulse" if settings.animation == "pulse" else "Still", "info"),
        ("Sprite", settings.sprite_size.title(), "info"),
        ("Cadence", f"{settings.animation_interval:g}s", "info"),
        ("Label width", f"{settings.label_max_cells} cells", "info"),
        ("Work timeout", "Never" if settings.working_timeout == 0 else f"{settings.working_timeout:g}s", "info"),
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


def command_status(args: argparse.Namespace) -> int:
    directory = runtime_dir()
    items = []
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        try:
            state = read_state(path)
        except (OSError, ValueError, KeyError, TypeError):
            state = None
        if state is None:
            continue
        items.append(
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
    for item in items:
        state_label, state_style = STATE_PRESENTATION.get(
            item["state"], (item["state"].title(), "warn")
        )
        detail = f"{state_label} · {item['backend'].title()}"
        if args.verbose:
            target = item["pane"] or item["tab"] or item["session"] or "current tab"
            detail += f" · {target}"
        display.append((item["label"], detail, state_style))
    print(rows(display))
    return 0


def command_sound(args: argparse.Namespace) -> int:
    settings = _load_settings_for(args)
    if not settings.sound or not play_sound(args.kind, volume=settings.volume):
        raise SystemExit("konsole-pal: no supported audio player or sound is disabled")
    return 0


def command_config(args: argparse.Namespace) -> int:
    project_path = Path.cwd() / ".konsole-pal.toml"
    if args.config_action == "show":
        settings = load_settings(cwd=Path.cwd())
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
                print(message(f"User config: {compact_path(user_config_path())}", state="info"))
                print(message(f"Project config: {compact_path(project_path)}", state="info"))
        return 0
    target_path = project_path if args.project else user_config_path()
    if args.config_action == "set":
        path = (
            write_setting(target_path, args.key, args.value)
            if args.project
            else write_user_setting(args.key, args.value)
        )
        print(message(f"Updated {args.key.replace('_', ' ')} in {compact_path(path)}."))
        return 0
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
        print(message(f"Connected {', '.join(agent.title() for agent in agents)}; {len(changed)} file(s) changed."))
        return 0
    if args.hooks_action == "uninstall":
        changed = uninstall_hooks(agents)
        print(message(f"Disconnected {', '.join(agent.title() for agent in agents)}; {len(changed)} file(s) changed."))
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
            style = "ok" if status.installed and status.valid and status.private else "warn"
            display.append((status.agent.title(), detail, style))
        okay = okay and status.installed and status.valid and status.private
    if display:
        print(rows(display))
    return 0 if okay else 1


def command_reset(_args: argparse.Namespace) -> int:
    restored, removed = reset_runtime()
    print(message(f"Restored {restored} title(s) and cleared {removed} runtime file(s)."))
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    repair_messages = []
    if args.repair:
        try:
            install_hooks(
                [status.agent for status in hooks_status() if not status.installed]
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
    for status in hooks_status():
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
                        "Ready" if okay else ("Optional" if name in optional else "Not detected"),
                        "ok" if okay else ("idle" if name in optional else "warn"),
                    )
                    for name, okay in checks
                ]
            )
        )
    essential = [okay for name, okay in checks if name in {"Python >= 3.10", "settings"}]
    return 0 if all(essential) else 1


def command_setup(args: argparse.Namespace) -> int:
    agents = args.agents or list(AGENTS)
    invalid = sorted(set(agents) - set(AGENTS))
    if invalid:
        raise HookConfigError(f"unsupported agent: {invalid[0]}")
    interactive = not (args.yes or args.json) and sys.stdin.isatty()
    if interactive:
        _print_brand("Three small choices, then you're ready.")
        print()
        print(heading("Choose your setup"))
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
    write_user_setting("focus", focus)
    write_user_setting("sprite_size", sprite_size)
    write_user_setting("animation", animation)
    changed = install_hooks(agents, force=args.force)
    settings = load_settings()
    sound_ready = bool(find_audio_player())
    if args.preview_sound and settings.sound:
        play_sound("done", volume=settings.volume)
    result = {
        "terminal": _terminal_label(),
        "agents": agents,
        "focus": focus,
        "sprite_size": sprite_size,
        "animation": animation,
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
                ("Terminal", result["terminal"], "ok" if result["terminal"] != "Not detected" else "warn"),
                ("Agents", " · ".join(agent.title() for agent in agents), "ok"),
                ("Focus", {"attention": "Attention only", "all": "Completions + attention", "never": "Never"}[focus], "ok"),
                ("Sprite", sprite_size.title(), "ok"),
                ("Motion", "Gentle pulse" if animation == "pulse" else "Still", "ok"),
                ("Sound", "Ready" if sound_ready else "Player not found", "ok" if sound_ready else "warn"),
            ]
        )
    )
    print()
    print(message("Restart Codex and Cursor, then let the rabbit work."))
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
    commands = "setup status doctor demo run config hooks sound completions reset set"
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
        return f'''#compdef konsole-pal kp
_arguments '1:command:({commands})' '*::argument:->args' '''
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
        description=(
            "  🐇  Konsole Pal\n"
            "       Your AI tabs, quietly alive."
        ),
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
    setup_parser.add_argument("--animation", choices=("pulse", "static"))
    setup_parser.add_argument("--sprite-size", choices=("small", "medium", "large"))
    setup_parser.add_argument("--preview-sound", action="store_true")
    setup_parser.add_argument("--yes", action="store_true", help="accept calm defaults")
    setup_parser.add_argument("--force", action="store_true")
    setup_parser.add_argument("--json", action="store_true")
    setup_parser.set_defaults(func=command_setup)

    status_parser = sub.add_parser("status", help="show active agent tabs")
    status_parser.add_argument("--json", action="store_true")
    status_parser.add_argument("--verbose", action="store_true")
    status_parser.set_defaults(func=command_status)

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

    hook_parser = sub.add_parser("hook", help=argparse.SUPPRESS)
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

    sound_parser = sub.add_parser("sound", help="preview a notification sound")
    sound_parser.add_argument("kind", choices=SOUND_KINDS)
    sound_parser.set_defaults(func=command_sound)

    config_parser = sub.add_parser("config", help="show or edit layered settings")
    config_sub = config_parser.add_subparsers(dest="config_action", required=True)
    config_show = config_sub.add_parser("show", help="show effective settings")
    config_show.add_argument("--json", action="store_true")
    config_show.add_argument("--toml", action="store_true")
    config_show.add_argument("--verbose", action="store_true")
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

    reset_parser = sub.add_parser("reset", help="stop workers and restore resting titles")
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
