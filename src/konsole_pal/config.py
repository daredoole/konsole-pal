from __future__ import annotations

import os
import re
import tempfile
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    preset: str = "balanced"
    focus: str = "attention"
    sound: bool = True
    volume: float = 0.7
    sound_theme: str = "soft"
    animation: str = "pulse"
    animation_interval: float = 0.85
    sprite_size: str = "medium"
    theme: str = "rabbit"
    notification: str = "auto"
    quiet_hours: str = "off"
    attention_timeout: float = 86_400.0
    label_max_cells: int = 26
    working_timeout: float = 14_400.0

    def is_quiet(self, now: time.struct_time | None = None) -> bool:
        if self.quiet_hours == "off":
            return False
        current = now or time.localtime()
        start, end = self.quiet_hours.split("-", 1)
        start_minutes = _clock_minutes(start)
        end_minutes = _clock_minutes(end)
        current_minutes = current.tm_hour * 60 + current.tm_min
        if start_minutes == end_minutes:
            return True
        if start_minutes < end_minutes:
            return start_minutes <= current_minutes < end_minutes
        return current_minutes >= start_minutes or current_minutes < end_minutes

    def allows_focus(
        self,
        state: str,
        now: time.struct_time | None = None,
    ) -> bool:
        if self.is_quiet(now):
            return False
        if self.focus == "never":
            return False
        if self.focus == "attention":
            return state in {"attention", "error"}
        return state in {"done", "attention", "error"}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SETTING_KEYS = tuple(Settings.__dataclass_fields__)
PRESET_VALUES: dict[str, dict[str, Any]] = {
    "minimal": {
        "focus": "attention",
        "sound": False,
        "animation": "static",
        "sprite_size": "small",
        "notification": "auto",
    },
    "balanced": {
        "focus": "attention",
        "sound": True,
        "animation": "pulse",
        "sprite_size": "medium",
        "notification": "auto",
    },
    "playful": {
        "focus": "attention",
        "sound": True,
        "animation": "pulse",
        "sprite_size": "large",
        "notification": "auto",
    },
}
ENVIRONMENT_KEYS = {
    "preset": "KONSOLE_PAL_PRESET",
    "focus": "KONSOLE_PAL_FOCUS",
    "sound": "KONSOLE_PAL_SOUND",
    "volume": "KONSOLE_PAL_VOLUME",
    "sound_theme": "KONSOLE_PAL_SOUND_THEME",
    "animation": "KONSOLE_PAL_ANIMATION",
    "animation_interval": "KONSOLE_PAL_ANIMATION_INTERVAL",
    "sprite_size": "KONSOLE_PAL_SPRITE_SIZE",
    "theme": "KONSOLE_PAL_THEME",
    "notification": "KONSOLE_PAL_NOTIFICATION",
    "quiet_hours": "KONSOLE_PAL_QUIET_HOURS",
    "attention_timeout": "KONSOLE_PAL_ATTENTION_TIMEOUT",
    "label_max_cells": "KONSOLE_PAL_LABEL_MAX_CELLS",
    "working_timeout": "KONSOLE_PAL_WORKING_TIMEOUT",
}


def config_dir(env: Mapping[str, str] = os.environ) -> Path:
    base = env.get("XDG_CONFIG_HOME")
    return Path(base) / "konsole-pal" if base else Path.home() / ".config" / "konsole-pal"


def user_config_path(env: Mapping[str, str] = os.environ) -> Path:
    return config_dir(env) / "config.toml"


def project_config_path(cwd: str | Path | None = None) -> Path | None:
    current = Path(cwd or os.getcwd()).resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        candidate = directory / ".konsole-pal.toml"
        if candidate.is_file():
            return candidate
        if (directory / ".git").exists():
            break
    return None


def _parse_bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ConfigError(f"{key} must be true or false")


def _clock_minutes(value: str) -> int:
    hours, minutes = value.split(":", 1)
    return int(hours) * 60 + int(minutes)


def parse_setting(key: str, value: Any) -> Any:
    if key not in SETTING_KEYS:
        raise ConfigError(f"unknown setting: {key}")
    if key == "preset":
        normalized = str(value).strip().lower()
        if normalized not in PRESET_VALUES:
            raise ConfigError("preset must be minimal, balanced, or playful")
        return normalized
    if key == "focus":
        normalized = str(value).strip().lower()
        if normalized not in {"all", "attention", "never"}:
            raise ConfigError("focus must be all, attention, or never")
        return normalized
    if key == "animation":
        normalized = str(value).strip().lower()
        if normalized not in {"pulse", "static"}:
            raise ConfigError("animation must be pulse or static")
        return normalized
    if key == "sprite_size":
        normalized = str(value).strip().lower()
        if normalized not in {"small", "medium", "large"}:
            raise ConfigError("sprite_size must be small, medium, or large")
        return normalized
    if key == "theme":
        normalized = str(value).strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", normalized):
            raise ConfigError(
                "theme must be a name containing letters, numbers, - or _"
            )
        return normalized
    if key == "sound_theme":
        normalized = str(value).strip().lower()
        if normalized not in {"soft", "arcade", "silent"}:
            raise ConfigError("sound_theme must be soft, arcade, or silent")
        return normalized
    if key == "notification":
        normalized = str(value).strip().lower()
        if normalized not in {"auto", "off", "terminal", "desktop", "bell"}:
            raise ConfigError(
                "notification must be auto, off, terminal, desktop, or bell"
            )
        return normalized
    if key == "quiet_hours":
        normalized = str(value).strip().lower()
        if normalized == "off":
            return normalized
        if not re.fullmatch(
            r"(?:[01]\d|2[0-3]):[0-5]\d-(?:[01]\d|2[0-3]):[0-5]\d",
            normalized,
        ):
            raise ConfigError("quiet_hours must be off or HH:MM-HH:MM")
        return normalized
    if key == "sound":
        return _parse_bool(value, key)
    if key == "volume":
        parsed = float(value)
        if not 0.0 <= parsed <= 1.0:
            raise ConfigError("volume must be between 0 and 1")
        return parsed
    if key == "animation_interval":
        parsed = float(value)
        if not 0.2 <= parsed <= 10.0:
            raise ConfigError("animation_interval must be between 0.2 and 10 seconds")
        return parsed
    if key == "label_max_cells":
        parsed = int(value)
        if not 8 <= parsed <= 80:
            raise ConfigError("label_max_cells must be between 8 and 80")
        return parsed
    if key == "working_timeout":
        parsed = float(value)
        if parsed != 0 and not 60 <= parsed <= 604_800:
            raise ConfigError("working_timeout must be 0 or between 60 and 604800 seconds")
        return parsed
    if key == "attention_timeout":
        parsed = float(value)
        if parsed != 0 and not 60 <= parsed <= 2_592_000:
            raise ConfigError(
                "attention_timeout must be 0 or between 60 and 2592000 seconds"
            )
        return parsed
    raise ConfigError(f"unsupported setting: {key}")


def _read_document(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"invalid config {path}: {error}") from error
    if not isinstance(data, dict):
        raise ConfigError(f"invalid config {path}: expected a table")
    return data


def _read_config(path: Path, agent: str | None = None) -> dict[str, Any]:
    data = _read_document(path)
    if not data:
        return {}
    structured = "settings" in data or "agents" in data
    values = data.get("settings", {}) if structured else data
    if not isinstance(values, dict):
        raise ConfigError(f"invalid config {path}: settings must be a table")
    unknown = set(values) - set(SETTING_KEYS)
    if unknown:
        raise ConfigError(f"invalid config {path}: unknown setting {sorted(unknown)[0]}")
    parsed = {key: parse_setting(key, value) for key, value in values.items()}
    agents = data.get("agents", {}) if structured else {}
    if not isinstance(agents, dict):
        raise ConfigError(f"invalid config {path}: agents must be a table")
    if agent and agent in agents:
        scoped = agents[agent]
        if not isinstance(scoped, dict):
            raise ConfigError(f"invalid config {path}: agents.{agent} must be a table")
        unknown = set(scoped) - set(SETTING_KEYS)
        if unknown:
            raise ConfigError(
                f"invalid config {path}: unknown setting {sorted(unknown)[0]}"
            )
        parsed_scoped = {
            key: parse_setting(key, value) for key, value in scoped.items()
        }
        if parsed_scoped.get("preset"):
            parsed.update(PRESET_VALUES[str(parsed_scoped["preset"])])
        parsed.update(parsed_scoped)
    return parsed


def _apply_layer(values: dict[str, Any], layer: Mapping[str, Any]) -> None:
    preset = layer.get("preset")
    if preset:
        values.update(PRESET_VALUES[str(preset)])
    values.update(layer)


def load_settings(
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] = os.environ,
    overrides: Mapping[str, Any] | None = None,
    agent: str | None = None,
) -> Settings:
    values = Settings().to_dict()
    user_values = _read_config(user_config_path(env), agent)
    _apply_layer(values, user_values)
    project = project_config_path(cwd)
    if project:
        _apply_layer(values, _read_config(project, agent))
    environment_values = {}
    for key, env_key in ENVIRONMENT_KEYS.items():
        if env_key in env:
            environment_values[key] = parse_setting(key, env[env_key])
    _apply_layer(values, environment_values)
    parsed_overrides = {
        key: parse_setting(key, value)
        for key, value in (overrides or {}).items()
        if value is not None
    }
    _apply_layer(values, parsed_overrides)
    return Settings(**values)


def _format_values(values: Mapping[str, Any]) -> str:
    lines = ["[settings]"]
    for key in SETTING_KEYS:
        if key not in values:
            continue
        value = values[key]
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, str):
            rendered = f'"{value}"'
        else:
            rendered = str(value)
        lines.append(f"{key} = {rendered}")
    return "\n".join(lines) + "\n"


def _render_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return str(value)


def _format_document(data: Mapping[str, Any]) -> str:
    structured = "settings" in data or "agents" in data
    if not structured:
        return _format_values(data)
    lines = ["[settings]"]
    settings = data.get("settings", {})
    for key in SETTING_KEYS:
        if key in settings:
            lines.append(f"{key} = {_render_value(settings[key])}")
    agents = data.get("agents", {})
    for agent in sorted(agents):
        lines.extend(["", f"[agents.{agent}]"])
        for key in SETTING_KEYS:
            if key in agents[agent]:
                lines.append(f"{key} = {_render_value(agents[agent][key])}")
    return "\n".join(lines) + "\n"


def format_settings(settings: Settings) -> str:
    return _format_values(settings.to_dict())


def _write_config(path: Path, values: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="config-", suffix=".toml", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(_format_document(values), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_setting(
    path: Path,
    key: str,
    value: Any,
) -> Path:
    parsed = parse_setting(key, value)
    document = _read_document(path)
    if "settings" in document or "agents" in document:
        settings = document.setdefault("settings", {})
        settings[key] = parsed
    else:
        document[key] = parsed
    _write_config(path, document)
    return path


def reset_setting(path: Path, key: str | None = None) -> Path:
    if key is None:
        path.unlink(missing_ok=True)
        return path
    if key not in SETTING_KEYS:
        raise ConfigError(f"unknown setting: {key}")
    document = _read_document(path)
    settings = document.get("settings", document)
    settings.pop(key, None)
    if not settings and "settings" in document:
        document.pop("settings", None)
    if not document or document == {"agents": {}}:
        path.unlink(missing_ok=True)
        return path
    _write_config(path, document)
    return path


def write_agent_setting(path: Path, agent: str, key: str, value: Any) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", agent):
        raise ConfigError("agent must contain letters, numbers, - or _")
    parsed = parse_setting(key, value)
    document = _read_document(path)
    if document and "settings" not in document and "agents" not in document:
        document = {"settings": document}
    agents = document.setdefault("agents", {})
    agents.setdefault(agent, {})[key] = parsed
    _write_config(path, document)
    return path


def reset_agent_setting(path: Path, agent: str, key: str | None = None) -> Path:
    document = _read_document(path)
    agents = document.get("agents", {})
    if agent not in agents:
        return path
    if key is None:
        agents.pop(agent, None)
    else:
        if key not in SETTING_KEYS:
            raise ConfigError(f"unknown setting: {key}")
        agents[agent].pop(key, None)
        if not agents[agent]:
            agents.pop(agent, None)
    if not agents:
        document.pop("agents", None)
    if not document:
        path.unlink(missing_ok=True)
    else:
        _write_config(path, document)
    return path


def write_user_setting(
    key: str,
    value: Any,
    env: Mapping[str, str] = os.environ,
) -> Path:
    return write_setting(user_config_path(env), key, value)


def reset_user_setting(
    key: str | None = None,
    env: Mapping[str, str] = os.environ,
) -> Path:
    return reset_setting(user_config_path(env), key)
