from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import time
import unicodedata
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .sounds import play_sound
from .themes import BUILTIN_THEMES, load_theme

# Every size has one fixed terminal-cell width across every frame and state.
# This changes the sprite's footprint without asking Konsole to resize its font,
# which would affect every tab and cannot be scoped to one title safely.
SPRITE_FRAMES = BUILTIN_THEMES["rabbit"].frames
SPRITE_FINAL_ICONS = BUILTIN_THEMES["rabbit"].finals
WORKING_FRAMES = SPRITE_FRAMES["medium"]
FINAL_ICONS = SPRITE_FINAL_ICONS["medium"]
VALID_STATES = frozenset(("working", *FINAL_ICONS))
LABEL_MAX_CELLS = 26
FINAL_STATE_TTL = 7 * 24 * 60 * 60
WORKING_STATE_TTL = 4 * 60 * 60
FOCUS_EVENT_TTL = 30.0
FOCUS_PRIORITY = {"idle": 0, "done": 1, "error": 2, "attention": 3}
ANSI_ESCAPE_RE = re.compile(
    r"(?:\x1b\][^\x07]*(?:\x07|\x1b\\))|(?:\x1b\[[0-?]*[ -/]*[@-~])"
)


@dataclass(frozen=True)
class KonsoleTarget:
    service: str
    session_path: str
    window_path: str
    backend: str = "konsole"
    tty_path: str = ""
    multiplexer_session: str = ""
    pane_id: str = ""
    tab_id: str = ""
    remote: bool = False

    @property
    def session_id(self) -> int | None:
        match = re.search(r"(\d+)$", self.session_path)
        return int(match.group(1)) if match else None

    @property
    def key(self) -> str:
        raw = (
            f"{self.backend}\0{self.service}\0{self.session_path}\0"
            f"{self.tty_path}\0{self.multiplexer_session}\0"
            f"{self.pane_id}\0{self.tab_id}"
        ).encode()
        return hashlib.sha256(raw).hexdigest()[:16]


@dataclass
class SessionState:
    state: str
    label: str
    agent: str
    target: KonsoleTarget
    focus: bool = False
    updated_at: float = 0.0
    animation: str = "pulse"
    animation_interval: float = 0.85
    sprite_size: str = "medium"
    theme: str = "rabbit"
    label_max_cells: int = LABEL_MAX_CELLS
    working_timeout: float = WORKING_STATE_TTL
    attention_timeout: float = 86_400.0

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["updated_at"] = self.updated_at or time.time()
        return data

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> SessionState:
        target = KonsoleTarget(**data["target"])
        return cls(
            state=str(data["state"]),
            label=str(data["label"]),
            agent=str(data.get("agent", "agent")),
            target=target,
            focus=bool(data.get("focus", False)),
            updated_at=float(data.get("updated_at", 0)),
            animation=str(data.get("animation", "pulse")),
            animation_interval=float(data.get("animation_interval", 0.85)),
            sprite_size=str(data.get("sprite_size", "medium")),
            theme=str(data.get("theme", "rabbit")),
            label_max_cells=int(data.get("label_max_cells", LABEL_MAX_CELLS)),
            working_timeout=float(data.get("working_timeout", WORKING_STATE_TTL)),
            attention_timeout=float(data.get("attention_timeout", 86_400.0)),
        )


def runtime_dir(env: Mapping[str, str] = os.environ) -> Path:
    base = env.get("XDG_RUNTIME_DIR")
    if base:
        return Path(base) / "konsole-pal"
    return Path(tempfile.gettempdir()) / f"konsole-pal-{os.getuid()}"


def _controlling_tty_path() -> str:
    try:
        fd = os.open("/dev/tty", os.O_WRONLY | os.O_NOCTTY)
    except OSError:
        return ""
    try:
        device = os.fstat(fd).st_rdev
    finally:
        os.close(fd)
    pts = Path("/dev/pts")
    try:
        for candidate in pts.iterdir():
            if candidate.name.isdigit() and candidate.stat().st_rdev == device:
                return str(candidate)
    except OSError:
        pass
    return "/dev/tty"


def _discover_zellij_tab(session: str, pane_id: str) -> str:
    zellij = shutil.which("zellij")
    if not zellij:
        return ""
    command = [zellij]
    if session:
        command += ["--session", session]
    command += ["action", "list-panes", "--json"]
    try:
        result = subprocess.run(  # nosec B603
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
            text=True,
        )
        panes = json.loads(result.stdout) if result.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return ""
    wanted = pane_id.removeprefix("terminal_")
    for pane in panes:
        if str(pane.get("id", "")) == wanted and not pane.get("is_plugin", False):
            return str(pane.get("tab_id", ""))
    return ""


def target_from_env(env: Mapping[str, str] = os.environ) -> KonsoleTarget | None:
    tty_path = _controlling_tty_path()
    remote = bool(env.get("SSH_CONNECTION") or env.get("SSH_TTY"))
    zellij_pane = env.get("ZELLIJ_PANE_ID", "")
    if env.get("ZELLIJ") and zellij_pane:
        session = env.get("ZELLIJ_SESSION_NAME", "")
        return KonsoleTarget(
            service="",
            session_path="",
            window_path="",
            backend="zellij",
            multiplexer_session=session,
            pane_id=zellij_pane,
            tab_id=_discover_zellij_tab(session, zellij_pane),
            tty_path=tty_path,
            remote=remote,
        )

    tmux_pane = env.get("TMUX_PANE", "")
    if env.get("TMUX") and tmux_pane:
        return KonsoleTarget(
            service="",
            session_path="",
            window_path="",
            backend="tmux",
            multiplexer_session=env["TMUX"].split(",", 1)[0],
            pane_id=tmux_pane,
            tty_path=tty_path,
            remote=remote,
        )

    session = env.get("KONSOLE_DBUS_SESSION", "")
    if session:
        return KonsoleTarget(
            service=env.get("KONSOLE_DBUS_SERVICE", "org.kde.konsole"),
            session_path=session,
            window_path=env.get("KONSOLE_DBUS_WINDOW", "/Windows/1"),
            tty_path=tty_path,
            remote=remote,
        )

    kitty_window = env.get("KITTY_WINDOW_ID", "")
    if kitty_window and tty_path:
        return KonsoleTarget(
            service="",
            session_path="",
            window_path="",
            backend="kitty",
            tty_path=tty_path,
            pane_id=kitty_window,
            remote=remote,
        )

    wezterm_pane = env.get("WEZTERM_PANE", "")
    if wezterm_pane and tty_path:
        return KonsoleTarget(
            service="",
            session_path="",
            window_path="",
            backend="wezterm",
            tty_path=tty_path,
            pane_id=wezterm_pane,
            remote=remote,
        )

    term_program = env.get("TERM_PROGRAM", "").lower()
    if tty_path and (term_program == "ghostty" or env.get("GHOSTTY_RESOURCES_DIR")):
        return KonsoleTarget(
            service="",
            session_path="",
            window_path="",
            backend="ghostty",
            tty_path=tty_path,
            remote=remote,
        )

    if tty_path and env.get("TERM", "") != "dumb":
        return KonsoleTarget(
            service="",
            session_path="",
            window_path="",
            backend="osc",
            tty_path=tty_path,
            remote=remote,
        )
    return None


def state_path(target: KonsoleTarget, env: Mapping[str, str] = os.environ) -> Path:
    return runtime_dir(env) / f"{target.key}.json"


@contextmanager
def _state_lock(path: Path):
    lock_path = path.with_suffix(".lock")
    lock = lock_path.open("a+", encoding="ascii")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def _write_state_file(path: Path, state: SessionState) -> None:
    temp = path.with_suffix(f".tmp-{os.getpid()}-{time.time_ns()}")
    temp.write_text(json.dumps(state.to_json(), separators=(",", ":")), encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)


def write_state(state: SessionState, env: Mapping[str, str] = os.environ) -> Path:
    directory = runtime_dir(env)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = state_path(state.target, env)
    with _state_lock(path):
        _write_state_file(path, state)
    return path


def read_state(path: Path) -> SessionState:
    return SessionState.from_json(json.loads(path.read_text(encoding="utf-8")))


def _consume_focus(
    path: Path,
    expected_updated_at: float,
    *,
    activate: bool = False,
) -> tuple[bool, int | None]:
    """Atomically consume one focus event and optionally activate its target."""
    try:
        with _state_lock(path):
            current = read_state(path)
            if not current.focus or current.updated_at != expected_updated_at:
                return False, path.stat().st_mtime_ns
            current.focus = False
            _write_state_file(path, current)
            # Holding the state lock ensures a new working event cannot overtake
            # this completion between validation and the external focus action.
            if activate:
                focus_tab(current.target)
            return True, path.stat().st_mtime_ns
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return False, None


def _state_is_stale(state: SessionState, mtime_ns: int, now: float) -> bool:
    timestamp = state.updated_at or (mtime_ns / 1_000_000_000)
    if state.state == "working":
        ttl = state.working_timeout
    elif state.state in {"attention", "error"}:
        ttl = state.attention_timeout
    else:
        ttl = FINAL_STATE_TTL
    if ttl == 0:
        return False
    return now - timestamp > ttl


def prune_stale_states(directory: Path, now: float | None = None) -> int:
    now = time.time() if now is None else now
    removed = 0
    for path in directory.glob("*.json"):
        try:
            mtime = path.stat().st_mtime_ns
            state = read_state(path)
            if not _state_is_stale(state, mtime, now):
                continue
            with _state_lock(path):
                current = read_state(path)
                if current.updated_at != state.updated_at:
                    continue
                path.unlink(missing_ok=True)
                removed += 1
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
    return removed


def attention_states(
    env: Mapping[str, str] = os.environ,
    *,
    now: float | None = None,
) -> list[tuple[Path, SessionState]]:
    directory = runtime_dir(env)
    current_time = time.time() if now is None else now
    items: list[tuple[Path, SessionState]] = []
    for path in directory.glob("*.json") if directory.exists() else []:
        try:
            state = read_state(path)
            if state.state not in {"attention", "error"}:
                continue
            timestamp = state.updated_at or path.stat().st_mtime
            if state.attention_timeout and current_time - timestamp > state.attention_timeout:
                continue
            items.append((path, state))
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
    return sorted(items, key=lambda item: item[1].updated_at, reverse=True)


def acknowledge_attention(path: Path, *, activate: bool = False) -> bool:
    try:
        with _state_lock(path):
            state = read_state(path)
            if state.state not in {"attention", "error"}:
                return False
            if activate and not focus_tab(state.target):
                return False
            state.state = "idle"
            state.focus = False
            state.updated_at = time.time()
            _write_state_file(path, state)
            set_title(state.target, title_for(state), persist=True)
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return False
    return True


def rename_session(path: Path, label: str) -> bool:
    safe = sanitize_label(label, 80)
    try:
        with _state_lock(path):
            state = read_state(path)
            state.label = f"{agent_display_name(state.agent)} · {safe}"
            _write_state_file(path, state)
        set_title(state.target, title_for(state), persist=True)
        return True
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return False


def find_qdbus() -> str | None:
    for name in ("qdbus6", "qdbus-qt6", "qdbus"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _run_quiet(command: list[str]) -> bool:
    try:
        result = subprocess.run(  # nosec B603
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def set_title(target: KonsoleTarget, title: str, *, persist: bool = True) -> bool:
    if target.backend == "zellij":
        zellij = shutil.which("zellij")
        if not zellij:
            return False
        command = [zellij]
        if target.multiplexer_session:
            command += ["--session", target.multiplexer_session]
        if target.tab_id:
            command += [
                "action",
                "rename-tab",
                "--tab-id",
                target.tab_id,
                title,
            ]
        else:
            command += [
                "action",
                "rename-pane",
                "--pane-id",
                target.pane_id,
                title,
            ]
        return _run_quiet(command)

    if target.backend == "tmux":
        tmux = shutil.which("tmux")
        if not tmux:
            return False
        command = [tmux]
        if target.multiplexer_session:
            command += ["-S", target.multiplexer_session]
        command += ["rename-window", "-t", target.pane_id, title]
        return _run_quiet(command)

    if target.backend in {"osc", "kitty", "wezterm", "ghostty"}:
        return emit_osc_title(title, target.tty_path)

    qdbus = find_qdbus()
    if qdbus:
        method = "org.kde.konsole.Session.setTitle"
        # Role 1 is the displayed title; role 0 keeps it stable across older
        # Konsole versions that separate the initial and displayed titles.
        displayed = _run_quiet(
            [qdbus, target.service, target.session_path, method, "1", title]
        )
        if displayed:
            if persist:
                _run_quiet(
                    [qdbus, target.service, target.session_path, method, "0", title]
                )
            return True
    return emit_osc_title(title)


def _write_tty(payload: bytes, tty_path: str = "") -> bool:
    path = tty_path or "/dev/tty"
    if path != "/dev/tty" and not path.startswith("/dev/pts/"):
        return False
    try:
        fd = os.open(path, os.O_WRONLY | os.O_NOCTTY)
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)
        return True
    except OSError:
        return False


def emit_osc_title(title: str, tty_path: str = "") -> bool:
    safe = title.replace("\x1b", "").replace("\x07", "")
    payload = f"\x1b]0;{safe}\x07".encode()
    return _write_tty(payload, tty_path)


def emit_bell(target: KonsoleTarget) -> bool:
    return _write_tty(b"\x07", target.tty_path)


def emit_terminal_notification(
    target: KonsoleTarget,
    title: str,
    body: str,
) -> bool:
    safe_title = sanitize_label(title, 48)
    safe_body = sanitize_label(body, 80)
    if target.backend == "kitty":
        identifier = f"konsole-pal-{target.key}"
        payload = (
            f"\x1b]99;i={identifier}:p=title:a=focus:o=unfocused;{safe_title}\x1b\\"
            f"\x1b]99;i={identifier}:p=body;{safe_body}\x1b\\"
        ).encode()
    else:
        payload = f"\x1b]9;{safe_title}: {safe_body}\x1b\\".encode()
    return _write_tty(payload, target.tty_path)


def emit_desktop_notification(title: str, body: str) -> bool:
    notifier = shutil.which("notify-send")
    if not notifier:
        return False
    return _run_quiet(
        [
            notifier,
            "--app-name=Konsole Pal",
            "--",
            sanitize_label(title, 48),
            sanitize_label(body, 80),
        ]
    )


def notify_state(
    target: KonsoleTarget,
    state_name: str,
    label: str,
    mode: str,
) -> bool:
    if mode == "off":
        return False
    title = {
        "done": "Task complete",
        "attention": "Agent needs you",
        "error": "Agent stopped with an error",
    }.get(state_name, "Agent update")
    if mode == "bell":
        return emit_bell(target)
    if mode == "terminal":
        return emit_terminal_notification(target, title, label)
    if mode == "desktop":
        return emit_desktop_notification(title, label)
    if target.remote or target.backend in {"kitty", "wezterm", "ghostty", "osc"}:
        return emit_terminal_notification(target, title, label) or emit_bell(target)
    return emit_desktop_notification(title, label) or emit_bell(target)


def focus_tab(target: KonsoleTarget) -> bool:
    if target.backend == "zellij":
        zellij = shutil.which("zellij")
        if not zellij:
            return False
        command = [zellij]
        if target.multiplexer_session:
            command += ["--session", target.multiplexer_session]
        command += ["action", "focus-pane-id", target.pane_id]
        return _run_quiet(command)

    if target.backend == "tmux":
        tmux = shutil.which("tmux")
        if not tmux:
            return False
        command = [tmux]
        if target.multiplexer_session:
            command += ["-S", target.multiplexer_session]
        selected = _run_quiet(command + ["select-window", "-t", target.pane_id])
        focused = _run_quiet(command + ["select-pane", "-t", target.pane_id])
        return selected and focused

    if target.backend == "kitty":
        kitten = shutil.which("kitten")
        if not kitten or not target.pane_id:
            return False
        return _run_quiet(
            [kitten, "@", "focus-window", "--match", f"id:{target.pane_id}"]
        )

    if target.backend == "wezterm":
        wezterm = shutil.which("wezterm")
        if not wezterm or not target.pane_id:
            return False
        return _run_quiet(
            [wezterm, "cli", "activate-pane", "--pane-id", target.pane_id]
        )

    if target.backend != "konsole":
        return False

    qdbus = find_qdbus()
    session_id = target.session_id
    if not qdbus or session_id is None:
        return False
    return _run_quiet(
        [
            qdbus,
            target.service,
            target.window_path,
            "org.kde.konsole.Window.setCurrentSession",
            str(session_id),
        ]
    )


def text_cell_width(text: str) -> int:
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return width


def sanitize_label(label: str, max_cells: int = LABEL_MAX_CELLS) -> str:
    label = ANSI_ESCAPE_RE.sub("", label)
    cleaned = "".join(
        " " if char.isspace() or unicodedata.category(char).startswith("C") else char
        for char in label
    )
    cleaned = " ".join(cleaned.split()) or "agent"
    if text_cell_width(cleaned) <= max_cells:
        return cleaned
    result = ""
    for char in cleaned:
        if text_cell_width(result + char + "…") > max_cells:
            break
        result += char
    return result.rstrip() + "…"


def title_for(state: SessionState, frame: int = 0) -> str:
    try:
        theme = load_theme(state.theme)
    except ValueError:
        theme = BUILTIN_THEMES["rabbit"]
    size = state.sprite_size if state.sprite_size in theme.frames else "medium"
    icon = (
        theme.frames[size][frame % len(theme.frames[size])]
        if state.state == "working"
        else theme.finals[size][state.state]
    )
    return f"{icon} {sanitize_label(state.label, state.label_max_cells)}"


def looks_like_question(message: str | None) -> bool:
    if not message:
        return False
    text = message.strip().lower()
    cues = (
        "?",
        "need your input",
        "need you to",
        "please choose",
        "please provide",
        "which option",
        "would you like",
    )
    return any(cue in text for cue in cues)


def event_to_state(agent: str, payload: Mapping[str, Any]) -> tuple[str, bool] | None:
    event_data = payload.get("event")
    nested_event = event_data.get("type") if isinstance(event_data, Mapping) else ""
    event = str(
        payload.get("hook_event_name")
        or payload.get("type")
        or nested_event
        or ""
    )
    normalized = event.replace("-", "").replace("_", "").lower()
    normalized = normalized.replace(".", "")

    if normalized in {
        "userpromptsubmit",
        "userpromptsubmitted",
        "beforesubmitprompt",
        "turnstarted",
        "beforeagent",
        "sessionbusy",
    }:
        return "working", False
    if normalized == "pretooluse":
        tool_name = str(payload.get("tool_name") or "").replace("-", "").replace("_", "").lower()
        if tool_name in {"requestuserinput", "askuserquestion"}:
            return "attention", True
        return None
    notification_type = str(
        payload.get("notification_type") or payload.get("notificationType") or ""
    ).replace("_", "").lower()
    if normalized in {
        "permissionrequest",
        "permissionasked",
        "approvalrequested",
        "questionasked",
    } or (
        normalized == "notification"
        and notification_type
        in {"toolpermission", "permissionprompt", "idleprompt"}
    ):
        return "attention", True
    if normalized in {"sessionstart"}:
        return "idle", False
    if normalized in {"sessionend"}:
        return "idle", False
    if normalized in {
        "stop",
        "agentstop",
        "agentturncomplete",
        "turncompleted",
        "afteragent",
        "sessionidle",
        "taskcompleted",
    }:
        last_message = payload.get("last_assistant_message") or payload.get(
            "last-assistant-message"
        )
        state = "attention" if looks_like_question(str(last_message or "")) else "done"
        return state, True
    if normalized in {
        "stopfailure",
        "erroroccurred",
        "agenterror",
        "sessionerror",
        "toolerror",
    }:
        return "error", True
    return None


def agent_display_name(agent: str) -> str:
    return {
        "codex": "Codex",
        "cursor": "Cursor",
        "claude": "Claude",
        "gemini": "Gemini",
        "opencode": "OpenCode",
        "aider": "Aider",
        "copilot": "Copilot",
    }.get(agent, agent.title())


def default_label(
    agent: str,
    payload: Mapping[str, Any] | None = None,
    env: Mapping[str, str] = os.environ,
) -> str:
    payload = payload or {}
    cwd = str(payload.get("cwd") or os.getcwd())
    project = Path(cwd).name or "terminal"
    pretty_agent = agent_display_name(agent)
    task = env.get("KONSOLE_PAL_TASK", "").strip()
    if task:
        return f"{pretty_agent} · {sanitize_label(task, 80)}"
    return f"{pretty_agent} · {project}"


def _animator_process_is_live(pid: int, path: Path) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    command_line = Path(f"/proc/{pid}/cmdline")
    if not command_line.exists():
        # Non-Linux fallback: signal probing is the best portable check.
        return True
    try:
        arguments = command_line.read_bytes().split(b"\0")
    except OSError:
        return False
    try:
        animate_index = arguments.index(b"_animate")
        worker_path = Path(os.fsdecode(arguments[animate_index + 1]))
    except (ValueError, IndexError):
        return False
    return b"konsole_pal.cli" in arguments and worker_path.parent == path.parent


def spawn_animator(path: Path) -> None:
    pid_path = path.parent / ".animator.pid"
    spawn_lock_path = path.parent / ".animator.spawn.lock"
    with spawn_lock_path.open("a+", encoding="ascii") as spawn_lock:
        fcntl.flock(spawn_lock.fileno(), fcntl.LOCK_EX)
        try:
            pid = int(pid_path.read_text(encoding="ascii"))
            if _animator_process_is_live(pid, path):
                return
        except (OSError, ValueError):
            pass
        process = subprocess.Popen(  # nosec B603
            [sys.executable, "-m", "konsole_pal.cli", "_animate", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        pid_path.write_text(str(process.pid), encoding="ascii")
        os.chmod(pid_path, 0o600)


def _directory_has_animated_state(directory: Path) -> bool:
    for state_file in directory.glob("*.json"):
        try:
            state = read_state(state_file)
            if state.state == "working" and state.animation == "pulse":
                return True
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
    return False


def repair_runtime(env: Mapping[str, str] = os.environ) -> tuple[int, bool]:
    directory = runtime_dir(env)
    if not directory.exists():
        return 0, False
    removed = prune_stale_states(directory)
    pid_path = directory / ".animator.pid"
    stale_pid_removed = False
    try:
        pid = int(pid_path.read_text(encoding="ascii"))
        probe = next(directory.glob("*.json"), directory / "state.json")
        if not _animator_process_is_live(pid, probe):
            pid_path.unlink(missing_ok=True)
            stale_pid_removed = True
    except (OSError, ValueError):
        if pid_path.exists():
            pid_path.unlink(missing_ok=True)
            stale_pid_removed = True
    return removed, stale_pid_removed


def reset_runtime(env: Mapping[str, str] = os.environ) -> tuple[int, int]:
    directory = runtime_dir(env)
    if not directory.exists():
        return 0, 0
    state_files = list(directory.glob("*.json"))
    restored = 0
    for path in state_files:
        try:
            state = read_state(path)
            state.state = "idle"
            state.focus = False
            if set_title(state.target, title_for(state), persist=True):
                restored += 1
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue

    pid_path = directory / ".animator.pid"
    try:
        pid = int(pid_path.read_text(encoding="ascii"))
        probe = state_files[0] if state_files else directory / "state.json"
        if _animator_process_is_live(pid, probe):
            os.kill(pid, 15)
    except (OSError, ValueError):
        pass

    removed = 0
    targets = [
        *state_files,
        *directory.glob("*.lock"),
        *directory.glob("*.tmp-*"),
        directory / ".animator.pid",
        directory / ".animator.lock",
        directory / ".animator.spawn.lock",
    ]
    for target in dict.fromkeys(targets):
        try:
            existed = target.exists()
            target.unlink(missing_ok=True)
            removed += int(existed)
        except OSError:
            continue
    return restored, removed


def update_session(
    state_name: str,
    *,
    agent: str,
    label: str,
    target: KonsoleTarget,
    focus: bool = False,
    sound: bool | None = None,
    force_focus: bool = False,
    settings: Settings | None = None,
    env: Mapping[str, str] = os.environ,
) -> Path:
    if state_name not in VALID_STATES:
        raise ValueError(f"invalid state: {state_name}")
    settings = settings or Settings()
    state = SessionState(
        state=state_name,
        label=label,
        agent=agent,
        target=target,
        focus=focus and (force_focus or settings.allows_focus(state_name)),
        updated_at=time.time(),
        animation=settings.animation,
        animation_interval=settings.animation_interval,
        sprite_size=settings.sprite_size,
        theme=settings.theme,
        label_max_cells=settings.label_max_cells,
        working_timeout=settings.working_timeout,
        attention_timeout=settings.attention_timeout,
    )
    path = write_state(state, env)
    quiet = settings.is_quiet()
    sound_enabled = (settings.sound if sound is None else sound) and not quiet
    if sound_enabled and state_name in {"done", "attention", "error"}:
        sound_kind = "done" if state_name == "done" else "attention"
        if settings.sound_theme == "soft":
            play_sound(sound_kind, env, volume=settings.volume)
        else:
            play_sound(
                sound_kind,
                env,
                volume=settings.volume,
                theme=settings.sound_theme,
            )
    if not quiet and state_name in {"done", "attention", "error"}:
        notify_state(target, state_name, label, settings.notification)
    spawn_animator(path)
    return path


def animate(path: Path, interval: float | None = None) -> int:
    """Render every active tab from one low-frequency shared worker."""
    try:
        os.nice(10)
    except OSError:
        pass
    directory = path.parent
    lock_path = directory / ".animator.lock"
    spawn_lock_path = directory / ".animator.spawn.lock"
    pid_path = directory / ".animator.pid"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    try:
        lock = lock_path.open("a+", encoding="ascii")
    except OSError:
        return 0
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return 0

    pid_path.write_text(str(os.getpid()), encoding="ascii")
    os.chmod(pid_path, 0o600)
    frame = 0
    sweeps = 0
    seen: dict[Path, int] = {}
    idle_rounds = 0
    try:
        prune_stale_states(directory)
        # Completed tabs are durable display state, but focus is a short-lived
        # event. Keep fresh events for the first render; consume old ones so a
        # delayed/restarted animator can never jump to an ancient completion.
        startup_now = time.time()
        for existing in directory.glob("*.json"):
            try:
                mtime = existing.stat().st_mtime_ns
                state = read_state(existing)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue
            if state.state != "working":
                event_is_fresh = startup_now - state.updated_at <= FOCUS_EVENT_TTL
                if not event_is_fresh:
                    if state.focus:
                        _, consumed_mtime = _consume_focus(existing, state.updated_at)
                        mtime = consumed_mtime or mtime
                    seen[existing] = mtime

        while True:
            sweeps += 1
            if sweeps % 64 == 0:
                prune_stale_states(directory)
            has_working_tab = False
            animation_intervals: list[float] = []
            focus_candidates: list[tuple[Path, SessionState]] = []
            for state_file in directory.glob("*.json"):
                try:
                    mtime = state_file.stat().st_mtime_ns
                    state = read_state(state_file)
                except (OSError, ValueError, KeyError, json.JSONDecodeError):
                    continue

                changed = seen.get(state_file) != mtime
                if state.state == "working":
                    if state.animation == "pulse":
                        has_working_tab = True
                        animation_intervals.append(state.animation_interval)
                        set_title(
                            state.target,
                            title_for(state, frame),
                            persist=changed,
                        )
                    elif changed:
                        set_title(state.target, title_for(state, 0), persist=True)
                elif changed:
                    set_title(state.target, title_for(state), persist=True)
                    if (
                        state.focus
                        and time.time() - state.updated_at <= FOCUS_EVENT_TTL
                    ):
                        focus_candidates.append((state_file, state))
                    elif state.focus:
                        _, consumed_mtime = _consume_focus(
                            state_file, state.updated_at
                        )
                        if consumed_mtime is not None:
                            mtime = consumed_mtime
                seen[state_file] = mtime

            winner = None
            if focus_candidates:
                winner = max(
                    focus_candidates,
                    key=lambda item: (
                        FOCUS_PRIORITY.get(item[1].state, 0),
                        item[1].updated_at,
                    ),
                )
            for state_file, state in focus_candidates:
                _, consumed_mtime = _consume_focus(
                    state_file,
                    state.updated_at,
                    activate=winner is not None and state_file == winner[0],
                )
                if consumed_mtime is not None:
                    seen[state_file] = consumed_mtime

            if has_working_tab:
                idle_rounds = 0
                frame = (frame + 1) % 1024
            else:
                idle_rounds += 1
                if idle_rounds >= 2:
                    # Serialize the final recheck with spawn_animator. Either
                    # this worker sees new work, or the hook sees no live PID
                    # and starts its replacement; neither wakeup can be lost.
                    with spawn_lock_path.open("a+", encoding="ascii") as spawn_lock:
                        fcntl.flock(spawn_lock.fileno(), fcntl.LOCK_EX)
                        if _directory_has_animated_state(directory):
                            idle_rounds = 0
                            continue
                        try:
                            if pid_path.read_text(encoding="ascii") == str(os.getpid()):
                                pid_path.unlink(missing_ok=True)
                        except OSError:
                            pass
                        return 0
            sleep_interval = interval
            if sleep_interval is None:
                sleep_interval = min(animation_intervals, default=0.85)
            time.sleep(sleep_interval)
    finally:
        try:
            if pid_path.read_text(encoding="ascii") == str(os.getpid()):
                pid_path.unlink(missing_ok=True)
        except OSError:
            pass
        lock.close()
