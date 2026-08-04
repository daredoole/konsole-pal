from __future__ import annotations

import json
import os
import tempfile
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SIZES = ("small", "medium", "large")
FINAL_STATES = ("idle", "done", "attention", "error")


@dataclass(frozen=True)
class Theme:
    name: str
    description: str
    frames: Mapping[str, tuple[str, ...]]
    finals: Mapping[str, Mapping[str, str]]


BUILTIN_THEMES: dict[str, Theme] = {
    "rabbit": Theme(
        "rabbit",
        "The original softly animated rabbit.",
        {
            "small": ("🐇*", "🐇+"),
            "medium": ("🐇✨", "🐇⭐"),
            "large": ("🐇✨✨", "🐇⭐⭐"),
        },
        {
            "small": {"idle": "🐇z", "done": "🐇z", "attention": "🐇!", "error": "🐇×"},
            "medium": {"idle": "🐇💤", "done": "🐇💤", "attention": "🐇❗", "error": "🐇💥"},
            "large": {"idle": "🐇💤💤", "done": "🐇💤💤", "attention": "🐇❗❗", "error": "🐇💥💥"},
        },
    ),
    "ascii": Theme(
        "ascii",
        "Portable rabbit faces for conservative terminals.",
        {
            "small": ("r>*", "r>+"),
            "medium": ("(\\_/)*", "(\\_/)+"),
            "large": ("/(\\_/)*", "/(\\_/)+"),
        },
        {
            "small": {"idle": "r>z", "done": "r>z", "attention": "r>!", "error": "r>x"},
            "medium": {"idle": "(\\_/)z", "done": "(\\_/)z", "attention": "(\\_/)!", "error": "(\\_/)x"},
            "large": {"idle": "/(\\_/)z", "done": "/(\\_/)z", "attention": "/(\\_/)!", "error": "/(\\_/)x"},
        },
    ),
    "mono": Theme(
        "mono",
        "A quiet monochrome status badge.",
        {
            "small": ("[·]", "[*]"),
            "medium": ("[pal·]", "[pal*]"),
            "large": ("[pal··]", "[pal**]"),
        },
        {
            "small": {"idle": "[z]", "done": "[z]", "attention": "[!]", "error": "[x]"},
            "medium": {"idle": "[palz]", "done": "[palz]", "attention": "[pal!]", "error": "[palx]"},
            "large": {"idle": "[palzz]", "done": "[palzz]", "attention": "[pal!!]", "error": "[palxx]"},
        },
    ),
}


def cell_width(text: str) -> int:
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return width


def validate_theme(theme: Theme) -> None:
    if not theme.name or not theme.name.replace("-", "").replace("_", "").isalnum():
        raise ValueError("theme name must contain only letters, numbers, - or _")
    for size in SIZES:
        frames = theme.frames.get(size, ())
        finals = theme.finals.get(size, {})
        if not frames or set(finals) != set(FINAL_STATES):
            raise ValueError(f"theme {theme.name}: incomplete {size} sprite set")
        sprites = [*frames, *(finals[state] for state in FINAL_STATES)]
        if any(
            unicodedata.category(char).startswith("C")
            for sprite in sprites
            for char in sprite
        ):
            raise ValueError(f"theme {theme.name}: control characters are not allowed")
        widths = {cell_width(sprite) for sprite in sprites}
        if len(widths) != 1:
            raise ValueError(f"theme {theme.name}: {size} sprites must have one cell width")


def theme_dir(env: Mapping[str, str] = os.environ) -> Path:
    base = env.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "konsole-pal" / "themes"


def _theme_from_data(data: Mapping[str, Any]) -> Theme:
    try:
        theme = Theme(
            name=str(data["name"]),
            description=str(data.get("description", "Custom theme")),
            frames={
                size: tuple(str(value) for value in data["frames"][size])
                for size in SIZES
            },
            finals={
                size: {
                    state: str(data["finals"][size][state])
                    for state in FINAL_STATES
                }
                for size in SIZES
            },
        )
    except (KeyError, TypeError) as error:
        raise ValueError("invalid theme manifest") from error
    validate_theme(theme)
    return theme


def load_theme(name: str, env: Mapping[str, str] = os.environ) -> Theme:
    if name in BUILTIN_THEMES:
        return BUILTIN_THEMES[name]
    path = theme_dir(env) / f"{name}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"unknown or invalid theme: {name}") from error
    if not isinstance(data, dict):
        raise ValueError(f"invalid theme: {name}")
    theme = _theme_from_data(data)
    if theme.name != name:
        raise ValueError(f"theme filename and name differ: {name}")
    return theme


def list_themes(env: Mapping[str, str] = os.environ) -> list[Theme]:
    themes = list(BUILTIN_THEMES.values())
    directory = theme_dir(env)
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        try:
            theme = load_theme(path.stem, env)
        except ValueError:
            continue
        if theme.name not in BUILTIN_THEMES:
            themes.append(theme)
    return themes


def install_theme(source: Path, env: Mapping[str, str] = os.environ) -> Theme:
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read theme manifest: {source}") from error
    if not isinstance(data, dict):
        raise ValueError("theme manifest must be a JSON object")
    theme = _theme_from_data(data)
    if theme.name in BUILTIN_THEMES:
        raise ValueError("built-in themes cannot be replaced")
    directory = theme_dir(env)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="theme-", suffix=".json", dir=directory
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    target = directory / f"{theme.name}.json"
    try:
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return theme


for _theme in BUILTIN_THEMES.values():
    validate_theme(_theme)
