from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

AGENTS = ("codex", "cursor", "claude", "gemini", "opencode", "copilot", "aider")
COMMAND_NAMES = ("konsole-pal", "knsole-pal")
CODEX_TITLE_COMMENT = (
    "# konsole-pal: keep plain-SSH agent status visible instead of Codex's OSC title"
)


class HookConfigError(ValueError):
    pass


@dataclass(frozen=True)
class HookStatus:
    agent: str
    path: Path
    valid: bool
    installed: bool
    private: bool


def hook_path(agent: str, home: Path | None = None) -> Path:
    if agent not in AGENTS:
        raise HookConfigError(f"unsupported agent: {agent}")
    root = home or Path.home()
    paths = {
        "codex": root / ".codex" / "hooks.json",
        "cursor": root / ".cursor" / "hooks.json",
        "claude": root / ".claude" / "settings.json",
        "gemini": root / ".gemini" / "settings.json",
        "opencode": root / ".config" / "opencode" / "plugins" / "konsole-pal.js",
        "copilot": root / ".copilot" / "hooks" / "konsole-pal.json",
        "aider": root / ".aider.conf.yml",
    }
    return paths[agent]


def _is_ours(value: Any, agent: str) -> bool:
    if isinstance(value, dict):
        return any(_is_ours(item, agent) for item in value.values())
    if isinstance(value, list):
        return any(_is_ours(item, agent) for item in value)
    if isinstance(value, str):
        return any(name in value for name in COMMAND_NAMES) and (
            f"--agent {agent}" in value or f'"{agent}"' in value
        )
    return False


def _read(path: Path, *, force: bool = False) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        if force:
            return {}
        raise HookConfigError(f"invalid hook config {path}: {error}") from error
    if not isinstance(data, dict):
        if force:
            return {}
        raise HookConfigError(f"invalid hook config {path}: expected an object")
    return data


def _command(agent: str, executable: str | None = None) -> str:
    binary = executable or shutil.which("konsole-pal") or "konsole-pal"
    return f"{shlex.quote(str(Path(binary).resolve()))} hook --agent {agent}"


def _nested(command: str) -> dict[str, list[dict[str, Any]]]:
    plain = {"hooks": [{"type": "command", "command": command}]}
    return {
        "SessionStart": [plain],
        "UserPromptSubmit": [plain],
        "PreToolUse": [
            {
                "matcher": "request_user_input|AskUserQuestion",
                "hooks": [{"type": "command", "command": command}],
            }
        ],
        "PermissionRequest": [plain],
        "Notification": [
            {
                "matcher": "permission_prompt|idle_prompt|elicitation_dialog",
                "hooks": [{"type": "command", "command": command}],
            }
        ],
        "Stop": [plain],
        "StopFailure": [plain],
    }


def _entries(agent: str, executable: str | None = None) -> dict[str, list[dict[str, Any]]]:
    command = _command(agent, executable)
    if agent in {"codex", "claude"}:
        return _nested(command)
    if agent == "gemini":
        plain = {
            "hooks": [
                {
                    "name": "konsole-pal",
                    "type": "command",
                    "command": command,
                    "timeout": 5_000,
                }
            ]
        }
        return {
            "SessionStart": [plain],
            "BeforeAgent": [plain],
            "AfterAgent": [plain],
            "Notification": [plain],
            "SessionEnd": [plain],
        }
    if agent == "cursor":
        return {
            "sessionStart": [{"command": command}],
            "beforeSubmitPrompt": [{"command": command}],
            "stop": [{"command": command}],
        }
    if agent == "copilot":
        plain = {"type": "command", "command": command, "timeoutSec": 5}
        return {
            "sessionStart": [plain],
            "userPromptSubmitted": [plain],
            "agentStop": [plain],
            "errorOccurred": [plain],
            "notification": [
                {
                    **plain,
                    "matcher": "permission_prompt|elicitation_dialog|agent_idle",
                }
            ],
        }
    raise HookConfigError(f"{agent} does not use JSON hooks")


def _without_ours(data: dict[str, Any], agent: str) -> dict[str, Any]:
    result = dict(data)
    hooks = result.get("hooks")
    if not isinstance(hooks, dict):
        return result
    cleaned: dict[str, Any] = {}
    for event, entries in hooks.items():
        if isinstance(entries, list):
            remaining = [entry for entry in entries if not _is_ours(entry, agent)]
            if remaining:
                cleaned[event] = remaining
        else:
            cleaned[event] = entries
    result["hooks"] = cleaned
    return result


def _write(path: Path, data: dict[str, Any], *, backup: bool = True) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        os.chmod(path.with_suffix(path.suffix + ".bak"), 0o600)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="hooks-", suffix=path.suffix or ".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_text(
    path: Path,
    content: str,
    *,
    backup: bool = True,
    backup_suffix: str = ".bak",
) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if backup and path.exists():
        backup_path = path.with_suffix(path.suffix + backup_suffix)
        shutil.copy2(path, backup_path)
        os.chmod(backup_path, 0o600)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="konsole-pal-", suffix=path.suffix or ".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(content, encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def codex_config_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".codex" / "config.toml"


def _plain_ssh(env: Mapping[str, str]) -> bool:
    remote = bool(env.get("SSH_CONNECTION") or env.get("SSH_TTY"))
    multiplexed = bool(env.get("TMUX") or env.get("ZELLIJ"))
    return remote and not multiplexed


def _toml_assignment_end(lines: list[str], start: int, table_header: str) -> int:
    """Return the first line that completes one TOML assignment."""
    for end in range(start + 1, len(lines) + 1):
        candidate = table_header + "\n" + "\n".join(lines[start:end]) + "\n"
        try:
            parsed = tomllib.loads(candidate)
        except tomllib.TOMLDecodeError:
            continue
        table = parsed.get("tui", {})
        if isinstance(table, dict) and "terminal_title" in table:
            return end
    raise HookConfigError("could not safely update Codex terminal_title")


def _validated_codex_title_config(content: str) -> str:
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError as error:
        raise HookConfigError(
            f"could not safely update Codex config.toml: {error}"
        ) from error
    tui = data.get("tui", {})
    if not isinstance(tui, dict) or tui.get("terminal_title") != []:
        raise HookConfigError("could not safely disable Codex terminal_title")
    return content


def _codex_title_config(original: str) -> str:
    try:
        parsed = tomllib.loads(original) if original.strip() else {}
    except tomllib.TOMLDecodeError as error:
        raise HookConfigError(f"invalid Codex config.toml: {error}") from error

    tui = parsed.get("tui", {})
    if tui and not isinstance(tui, dict):
        raise HookConfigError("invalid Codex config.toml: tui must be a table")
    if isinstance(tui, dict) and tui.get("terminal_title") == []:
        return original

    lines = original.splitlines()
    table_start = None
    table_end = len(lines)
    for index, line in enumerate(lines):
        if re.match(r"^\s*\[\s*tui\s*\]\s*(?:#.*)?$", line):
            table_start = index
            continue
        if table_start is not None and index > table_start and re.match(
            r"^\s*\[", line
        ):
            table_end = index
            break

    dotted = re.compile(r"^(\s*)tui\.terminal_title\s*=")
    direct = re.compile(r"^(\s*)terminal_title\s*=")
    for index, line in enumerate(lines):
        match = dotted.match(line) if table_start is None else None
        if match:
            end = _toml_assignment_end(lines, index, "")
            lines[index:end] = [CODEX_TITLE_COMMENT, "tui.terminal_title = []"]
            return _validated_codex_title_config(
                "\n".join(lines).rstrip() + "\n"
            )
        if table_start is not None and table_start < index < table_end:
            match = direct.match(line)
            if match:
                end = _toml_assignment_end(lines, index, "[tui]")
                indent = match.group(1)
                lines[index:end] = [
                    f"{indent}{CODEX_TITLE_COMMENT}",
                    f"{indent}terminal_title = []",
                ]
                return _validated_codex_title_config(
                    "\n".join(lines).rstrip() + "\n"
                )

    if isinstance(tui, dict) and "terminal_title" in tui:
        raise HookConfigError(
            "Codex terminal_title uses an unsupported inline TOML form; "
            "set tui.terminal_title = [] manually"
        )
    if table_start is not None:
        insertion = table_start + 1
        lines[insertion:insertion] = [CODEX_TITLE_COMMENT, "terminal_title = []"]
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["[tui]", CODEX_TITLE_COMMENT, "terminal_title = []"])
    return _validated_codex_title_config("\n".join(lines).rstrip() + "\n")


def configure_codex_ssh_title(
    *,
    home: Path | None = None,
    env: Mapping[str, str] = os.environ,
) -> Path | None:
    """Disable Codex's competing OSC title only for plain SSH sessions."""
    if not _plain_ssh(env):
        return None
    path = codex_config_path(home)
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    content = _codex_title_config(original)
    if content == original:
        if path.exists() and path.stat().st_mode & 0o777 != 0o600:
            os.chmod(path, 0o600)
        return None
    _write_text(path, content, backup_suffix=".konsole-pal.bak")
    return path


def codex_ssh_title_ready(
    *,
    home: Path | None = None,
    env: Mapping[str, str] = os.environ,
) -> bool:
    if not _plain_ssh(env):
        return True
    path = codex_config_path(home)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    tui = data.get("tui", {})
    return isinstance(tui, dict) and tui.get("terminal_title") == []


def _opencode_plugin(executable: str | None = None) -> str:
    binary = str(Path(executable or shutil.which("konsole-pal") or "konsole-pal").resolve())
    return f'''// Generated by Konsole Pal. Local-only: no prompts or transcripts are stored.
export const KonsolePal = async () => ({{
  event: async ({{ event }}) => {{
    const names = {{
      "session.created": "SessionStart",
      "session.idle": "AfterAgent",
      "session.error": "AgentError",
      "permission.asked": "PermissionRequest",
      "question.asked": "PermissionRequest",
    }};
    let hook = names[event.type];
    if (event.type === "session.status" && event.properties?.status?.type === "busy") {{
      hook = "BeforeAgent";
    }}
    if (!hook) return;
    const payload = JSON.stringify({{
      hook_event_name: hook,
      cwd: process.cwd(),
    }});
    const child = Bun.spawn([{json.dumps(binary)}, "hook", "--agent", "opencode"], {{
      stdin: new Blob([payload]),
      stdout: "ignore",
      stderr: "ignore",
    }});
    await child.exited;
  }},
}});
'''


def _install_aider(path: Path, executable: str | None, force: bool) -> bool:
    command = f"{_command('aider', executable).replace(' hook ', ' emit attention ')} --focus"
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    if "notifications-command:" in original and not _is_ours(original, "aider") and not force:
        raise HookConfigError(
            f"{path} already has a notifications-command; use --force to replace it"
        )
    lines = [
        line
        for line in original.splitlines()
        if not re.match(r"^notifications(?:-command)?:", line)
    ]
    lines.extend(["notifications: true", f"notifications-command: {json.dumps(command)}"])
    content = "\n".join(lines).strip() + "\n"
    if content == original:
        return False
    _write_text(path, content)
    return True


def install_hooks(
    agents: Iterable[str] = AGENTS,
    *,
    home: Path | None = None,
    executable: str | None = None,
    force: bool = False,
    env: Mapping[str, str] = os.environ,
) -> list[Path]:
    selected = tuple(agents)
    changed = []
    for agent in selected:
        path = hook_path(agent, home)
        if agent == "opencode":
            content = _opencode_plugin(executable)
            original = path.read_text(encoding="utf-8") if path.exists() else ""
            if original and not _is_ours(original, agent) and not force:
                raise HookConfigError(f"refusing to replace unrelated plugin {path}")
            if original != content:
                _write_text(path, content)
                changed.append(path)
            continue
        if agent == "aider":
            if _install_aider(path, executable, force):
                changed.append(path)
            continue
        original = _read(path, force=force)
        data = _without_ours(original, agent)
        hooks = data.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            if not force:
                raise HookConfigError(f"invalid hook config {path}: hooks must be an object")
            hooks = {}
            data["hooks"] = hooks
        if agent in {"cursor", "copilot"}:
            data.setdefault("version", 1)
        for event, entries in _entries(agent, executable).items():
            hooks.setdefault(event, []).extend(entries)
        if data != original:
            _write(path, data)
            changed.append(path)
        elif path.exists() and path.stat().st_mode & 0o777 != 0o600:
            os.chmod(path, 0o600)
    if "codex" in selected:
        config_path = configure_codex_ssh_title(home=home, env=env)
        if config_path is not None:
            changed.append(config_path)
    return changed


def uninstall_hooks(
    agents: Iterable[str] = AGENTS,
    *,
    home: Path | None = None,
) -> list[Path]:
    changed = []
    for agent in agents:
        path = hook_path(agent, home)
        if not path.exists():
            continue
        if agent == "opencode":
            if _is_ours(path.read_text(encoding="utf-8"), agent):
                path.unlink()
                changed.append(path)
            continue
        if agent == "aider":
            original = path.read_text(encoding="utf-8")
            lines = [
                line
                for line in original.splitlines()
                if not (
                    line == "notifications: true"
                    or (line.startswith("notifications-command:") and _is_ours(line, agent))
                )
            ]
            content = "\n".join(lines).strip()
            content = content + "\n" if content else ""
            if content != original:
                _write_text(path, content)
                changed.append(path)
            continue
        original = _read(path)
        data = _without_ours(original, agent)
        if data != original:
            _write(path, data)
            changed.append(path)
    return changed


def hooks_status(
    agents: Iterable[str] = AGENTS,
    *,
    home: Path | None = None,
) -> list[HookStatus]:
    statuses = []
    for agent in agents:
        path = hook_path(agent, home)
        valid = True
        installed = False
        try:
            if agent in {"opencode", "aider"}:
                content = path.read_text(encoding="utf-8") if path.exists() else ""
                installed = _is_ours(content, agent)
            else:
                data = _read(path)
                installed = _is_ours(data, agent)
        except (HookConfigError, OSError, UnicodeError):
            valid = False
        private = not path.exists() or path.stat().st_mode & 0o077 == 0
        statuses.append(HookStatus(agent, path, valid, installed, private))
    return statuses
