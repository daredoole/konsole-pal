from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess  # nosec B404
import tempfile
import wave
from collections.abc import Callable, Mapping
from pathlib import Path

SAMPLE_RATE = 44_100
SOUND_KINDS = ("done", "attention")


def find_audio_player() -> str | None:
    for name in ("pw-play", "paplay", "aplay"):
        found = shutil.which(name)
        if found:
            return found
    return None


def sounds_enabled(env: Mapping[str, str] = os.environ) -> bool:
    value = env.get("KONSOLE_PAL_SOUND", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def sound_dir(env: Mapping[str, str] = os.environ) -> Path:
    base = env.get("XDG_RUNTIME_DIR")
    if base:
        return Path(base) / "konsole-pal" / "sounds"
    return Path(tempfile.gettempdir()) / f"konsole-pal-{os.getuid()}" / "sounds"


def _envelope(position: float, attack: float = 0.08, release: float = 0.42) -> float:
    if position < attack:
        return position / attack
    if position > 1.0 - release:
        return max(0.0, (1.0 - position) / release) ** 1.7
    return 1.0


def _completion_samples() -> list[float]:
    # An ascending pentatonic sparkle with overlapping bell-like harmonics.
    notes = (
        (659.25, 0.00, 0.22, 0.19),
        (783.99, 0.10, 0.25, 0.18),
        (987.77, 0.21, 0.28, 0.17),
        (1318.51, 0.34, 0.42, 0.16),
    )
    total = int(SAMPLE_RATE * 0.82)
    samples = [0.0] * total
    for frequency, start, duration, volume in notes:
        start_index = int(start * SAMPLE_RATE)
        count = int(duration * SAMPLE_RATE)
        for index in range(count):
            position = index / max(1, count - 1)
            phase = 2.0 * math.pi * frequency * (index / SAMPLE_RATE)
            bell = math.sin(phase) + 0.32 * math.sin(phase * 2.01)
            bell += 0.12 * math.sin(phase * 3.98)
            samples[start_index + index] += volume * _envelope(position) * bell
    return samples


def _attention_samples() -> list[float]:
    # A compact up-and-down chirp: recognizably "meeep", not an alarm.
    duration = 0.43
    count = int(SAMPLE_RATE * duration)
    samples: list[float] = []
    phase = 0.0
    for index in range(count):
        position = index / max(1, count - 1)
        if position < 0.58:
            frequency = 470.0 + (position / 0.58) * 330.0
        else:
            frequency = 800.0 - ((position - 0.58) / 0.42) * 145.0
        phase += 2.0 * math.pi * frequency / SAMPLE_RATE
        nasal = math.sin(phase) + 0.24 * math.sin(phase * 2.0)
        wobble = 0.92 + 0.08 * math.sin(2.0 * math.pi * 7.0 * position)
        samples.append(0.20 * _envelope(position, attack=0.05, release=0.25) * wobble * nasal)
    return samples


def synthesize_sound(kind: str, path: Path, volume: float = 1.0) -> Path:
    generators: dict[str, Callable[[], list[float]]] = {
        "done": _completion_samples,
        "attention": _attention_samples,
    }
    if kind not in generators:
        raise ValueError(f"unknown sound: {kind}")
    if not 0.0 <= volume <= 1.0:
        raise ValueError("volume must be between 0 and 1")
    samples = generators[kind]()
    peak = max(1.0, max(abs(sample) for sample in samples))
    pcm = b"".join(
        struct.pack("<h", int(max(-1.0, min(1.0, sample / peak)) * volume * 32767))
        for sample in samples
    )
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_suffix(f".tmp-{os.getpid()}.wav")
    with wave.open(str(temp), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(pcm)
    os.chmod(temp, 0o600)
    temp.replace(path)
    return path


def sound_path(
    kind: str,
    env: Mapping[str, str] = os.environ,
    volume: float = 1.0,
) -> Path:
    if kind not in SOUND_KINDS:
        raise ValueError(f"unknown sound: {kind}")
    volume_key = round(volume * 100)
    path = sound_dir(env) / f"{kind}-v2-{volume_key:03d}.wav"
    if not path.exists():
        synthesize_sound(kind, path, volume)
    return path


def play_sound(
    kind: str,
    env: Mapping[str, str] = os.environ,
    *,
    volume: float = 1.0,
) -> bool:
    if not sounds_enabled(env):
        return False
    player = find_audio_player()
    if not player:
        return False
    try:
        subprocess.Popen(  # nosec B603
            [player, str(sound_path(kind, env, volume))],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        return True
    except OSError:
        return False
