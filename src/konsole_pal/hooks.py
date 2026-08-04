from __future__ import annotations

import json
import os
import shlex
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

AGENTS = ("codex", "cursor")
COMMAND_NAMES = ("konsole-pal", "knsole-pal")


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
    directory = ".codex" if agent == "codex" else ".cursor"
    return root / directory / "hooks.json"


def _is_ours(value: Any, agent: str) -> bool:
    if isinstance(value, dict):
        return any(_is_ours(item, agent) for item in value.values())
    if isinstance(value, list):
        return any(_is_ours(item, agent) for item in value)
    if isinstance(value, str):
        return any(name in value for name in COMMAND_NAMES) and f"--agent {agent}" in value
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


def _entries(agent: str, executable: str | None = None) -> dict[str, list[dict[str, Any]]]:
    command = _command(agent, executable)
    if agent == "codex":
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
            "Stop": [plain],
        }
    return {
        "sessionStart": [{"command": command}],
        "beforeSubmitPrompt": [{"command": command}],
        "stop": [{"command": command}],
    }


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
        shutil.copy2(path, path.with_suffix(".json.bak"))
        os.chmod(path.with_suffix(".json.bak"), 0o600)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="hooks-", suffix=".json", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def install_hooks(
    agents: Iterable[str] = AGENTS,
    *,
    home: Path | None = None,
    executable: str | None = None,
    force: bool = False,
) -> list[Path]:
    changed = []
    for agent in agents:
        path = hook_path(agent, home)
        original = _read(path, force=force)
        data = _without_ours(original, agent)
        hooks = data.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            if not force:
                raise HookConfigError(f"invalid hook config {path}: hooks must be an object")
            hooks = {}
            data["hooks"] = hooks
        if agent == "cursor":
            data.setdefault("version", 1)
        for event, entries in _entries(agent, executable).items():
            hooks.setdefault(event, []).extend(entries)
        if data != original:
            _write(path, data)
            changed.append(path)
        elif path.exists() and path.stat().st_mode & 0o777 != 0o600:
            os.chmod(path, 0o600)
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
            data = _read(path)
            installed = _is_ours(data, agent)
        except HookConfigError:
            valid = False
        private = not path.exists() or path.stat().st_mode & 0o777 == 0o600
        statuses.append(HookStatus(agent, path, valid, installed, private))
    return statuses
