from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

# A quiet, terminal-native palette. Backgrounds are deliberately inherited.
PALETTE = {
    "porcelain": (247, 244, 237),
    "moon": (154, 167, 199),
    "clover": (115, 201, 139),
    "marigold": (245, 196, 81),
    "sakura": (239, 145, 170),
}
ANSI_COLORS = {
    "porcelain": "37",
    "moon": "36",
    "clover": "32",
    "marigold": "33",
    "sakura": "35",
}


def supports_color(
    stream: TextIO = sys.stdout,
    env: Mapping[str, str] = os.environ,
) -> bool:
    return (
        hasattr(stream, "isatty")
        and stream.isatty()
        and "NO_COLOR" not in env
        and env.get("TERM", "") != "dumb"
    )


def supports_unicode(stream: TextIO = sys.stdout) -> bool:
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        "✓🐇".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def paint(
    text: str,
    color: str | None = None,
    *,
    bold: bool = False,
    dim: bool = False,
    stream: TextIO = sys.stdout,
    env: Mapping[str, str] = os.environ,
) -> str:
    if not supports_color(stream, env):
        return text
    codes: list[str] = []
    if bold:
        codes.append("1")
    if dim:
        codes.append("2")
    if color:
        color_term = env.get("COLORTERM", "").lower()
        truecolor = color_term in {"truecolor", "24bit"} or "direct" in env.get(
            "TERM", ""
        )
        if truecolor:
            red, green, blue = PALETTE[color]
            codes.append(f"38;2;{red};{green};{blue}")
        else:
            codes.append(ANSI_COLORS[color])
    return f"\x1b[{';'.join(codes)}m{text}\x1b[0m"


def glyph(kind: str, stream: TextIO = sys.stdout) -> str:
    unicode_glyphs = {
        "ok": "✓",
        "warn": "!",
        "error": "×",
        "info": "•",
        "idle": "◦",
    }
    ascii_glyphs = {"ok": "+", "warn": "!", "error": "x", "info": "*", "idle": "-"}
    return (unicode_glyphs if supports_unicode(stream) else ascii_glyphs)[kind]


def brand(
    subtitle: str = "Your AI tabs, quietly alive.",
    *,
    stream: TextIO = sys.stdout,
    env: Mapping[str, str] = os.environ,
) -> str:
    rabbit = "🐇" if supports_unicode(stream) else r"/)/)"
    title = paint("Konsole Pal", "porcelain", bold=True, stream=stream, env=env)
    mark = paint(rabbit, "sakura", bold=True, stream=stream, env=env)
    quiet = paint(subtitle, "moon", dim=True, stream=stream, env=env)
    return f"  {mark}  {title}\n       {quiet}\n"


def heading(
    title: str,
    *,
    stream: TextIO = sys.stdout,
    env: Mapping[str, str] = os.environ,
) -> str:
    return paint(title, "moon", bold=True, stream=stream, env=env)


def row(
    label: str,
    value: str,
    *,
    state: str = "info",
    width: int = 13,
    stream: TextIO = sys.stdout,
    env: Mapping[str, str] = os.environ,
) -> str:
    colors = {
        "ok": "clover",
        "warn": "marigold",
        "error": "sakura",
        "info": "moon",
        "idle": "moon",
    }
    marker = paint(glyph(state, stream), colors[state], bold=True, stream=stream, env=env)
    name = paint(label.ljust(width), "moon", dim=True, stream=stream, env=env)
    return f"  {marker} {name} {value}"


def rows(
    values: Sequence[tuple[str, str, str]],
    *,
    stream: TextIO = sys.stdout,
    env: Mapping[str, str] = os.environ,
) -> str:
    width = max((len(label) for label, _, _ in values), default=0)
    return "\n".join(
        row(label, value, state=state, width=width, stream=stream, env=env)
        for label, value, state in values
    )


def message(
    text: str,
    *,
    state: str = "ok",
    stream: TextIO = sys.stdout,
    env: Mapping[str, str] = os.environ,
) -> str:
    colors = {
        "ok": "clover",
        "warn": "marigold",
        "error": "sakura",
        "info": "moon",
        "idle": "moon",
    }
    marker = paint(glyph(state, stream), colors[state], bold=True, stream=stream, env=env)
    return f"  {marker} {text}"


def compact_path(path: Path) -> str:
    try:
        return str(Path("~") / path.relative_to(Path.home()))
    except ValueError:
        return str(path)
