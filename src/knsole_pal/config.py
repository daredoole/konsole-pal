from __future__ import annotations

import os
import tempfile
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
    focus: str = "all"
    sound: bool = True
    volume: float = 0.7
    animation: str = "pulse"
    animation_interval: float = 0.85
    label_max_cells: int = 26
    working_timeout: float = 86_400.0

    def allows_focus(self, state: str) -> bool:
        if self.focus == "never":
            return False
        if self.focus == "attention":
            return state in {"attention", "error"}
        return state in {"done", "attention", "error"}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SETTING_KEYS = tuple(Settings.__dataclass_fields__)
ENVIRONMENT_KEYS = {
    "focus": "KNSOLE_PAL_FOCUS",
    "sound": "KNSOLE_PAL_SOUND",
    "volume": "KNSOLE_PAL_VOLUME",
    "animation": "KNSOLE_PAL_ANIMATION",
    "animation_interval": "KNSOLE_PAL_ANIMATION_INTERVAL",
    "label_max_cells": "KNSOLE_PAL_LABEL_MAX_CELLS",
    "working_timeout": "KNSOLE_PAL_WORKING_TIMEOUT",
}


def config_dir(env: Mapping[str, str] = os.environ) -> Path:
    base = env.get("XDG_CONFIG_HOME")
    return Path(base) / "knsole-pal" if base else Path.home() / ".config" / "knsole-pal"


def user_config_path(env: Mapping[str, str] = os.environ) -> Path:
    return config_dir(env) / "config.toml"


def project_config_path(cwd: str | Path | None = None) -> Path | None:
    current = Path(cwd or os.getcwd()).resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        candidate = directory / ".knsole-pal.toml"
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


def parse_setting(key: str, value: Any) -> Any:
    if key not in SETTING_KEYS:
        raise ConfigError(f"unknown setting: {key}")
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
    raise ConfigError(f"unsupported setting: {key}")


def _read_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"invalid config {path}: {error}") from error
    values = data.get("settings", data)
    if not isinstance(values, dict):
        raise ConfigError(f"invalid config {path}: settings must be a table")
    unknown = set(values) - set(SETTING_KEYS)
    if unknown:
        raise ConfigError(f"invalid config {path}: unknown setting {sorted(unknown)[0]}")
    return {key: parse_setting(key, value) for key, value in values.items()}


def load_settings(
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] = os.environ,
    overrides: Mapping[str, Any] | None = None,
) -> Settings:
    values = Settings().to_dict()
    values.update(_read_config(user_config_path(env)))
    project = project_config_path(cwd)
    if project:
        values.update(_read_config(project))
    for key, env_key in ENVIRONMENT_KEYS.items():
        if env_key in env:
            values[key] = parse_setting(key, env[env_key])
    for key, value in (overrides or {}).items():
        if value is not None:
            values[key] = parse_setting(key, value)
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
        temporary.write_text(_format_values(values), encoding="utf-8")
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
    current = _read_config(path)
    current[key] = parsed
    _write_config(path, current)
    return path


def reset_setting(path: Path, key: str | None = None) -> Path:
    if key is None:
        path.unlink(missing_ok=True)
        return path
    if key not in SETTING_KEYS:
        raise ConfigError(f"unknown setting: {key}")
    current = _read_config(path)
    current.pop(key, None)
    if not current:
        path.unlink(missing_ok=True)
        return path
    _write_config(path, current)
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
