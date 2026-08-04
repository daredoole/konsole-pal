#!/usr/bin/env python3
"""Repeatable local hook-latency and sleeping-worker resource benchmark."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from konsole_pal.core import KonsoleTarget, SessionState, read_state, write_state


def hook_latency(runs: int) -> dict[str, float]:
    payload = json.dumps({"hook_event_name": "SessionStart"})
    environment = {**os.environ, "KONSOLE_PAL_SOUND": "off", "TERM": "dumb"}
    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        result = subprocess.run(
            [sys.executable, "-m", "konsole_pal.cli", "hook", "--agent", "codex"],
            input=payload,
            text=True,
            capture_output=True,
            env=environment,
            timeout=5,
            check=False,
        )
        if result.returncode != 0 or result.stdout.strip() != "{}":
            raise RuntimeError("hook benchmark command failed")
        samples.append((time.perf_counter() - start) * 1_000)
    ordered = sorted(samples)
    percentile_index = min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))
    return {
        "runs": float(runs),
        "average_ms": round(statistics.mean(samples), 3),
        "p95_ms": round(ordered[percentile_index], 3),
        "max_ms": round(max(samples), 3),
    }


def _cpu_ticks(pid: int) -> int:
    fields = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").split()
    return int(fields[13]) + int(fields[14])


def _rss_kib(pid: int) -> int:
    for line in Path(f"/proc/{pid}/status").read_text(encoding="ascii").splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    return 0


def worker_resource(sample_seconds: float) -> dict[str, float]:
    if not Path("/proc").exists():
        return {"rss_mib": 0.0, "sleeping_cpu_percent": 0.0}
    with tempfile.TemporaryDirectory(prefix="konsole-pal-benchmark-") as temporary:
        environment = {**os.environ, "XDG_RUNTIME_DIR": temporary}
        target = KonsoleTarget("", "", "", backend="osc", tty_path="")
        state = SessionState(
            "working",
            "Benchmark",
            "benchmark",
            target,
            updated_at=time.time(),
        )
        path = write_state(state, environment)
        process = subprocess.Popen(
            [sys.executable, "-m", "konsole_pal.cli", "_animate", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 3
            pid_path = path.parent / ".animator.pid"
            while True:
                try:
                    ready = pid_path.read_text(encoding="ascii") == str(process.pid)
                except OSError:
                    ready = False
                if ready:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("worker did not start")
                time.sleep(0.02)
            time.sleep(0.25)
            rss = _rss_kib(process.pid)
            ticks_before = _cpu_ticks(process.pid)
            started = time.monotonic()
            time.sleep(sample_seconds)
            elapsed = time.monotonic() - started
            ticks_after = _cpu_ticks(process.pid)
            ticks_per_second = os.sysconf("SC_CLK_TCK")
            cpu = ((ticks_after - ticks_before) / ticks_per_second) / elapsed * 100
            final = read_state(path)
            final.state = "done"
            final.updated_at = time.time()
            write_state(final, environment)
            process.wait(timeout=5)
            return {
                "rss_mib": round(rss / 1024, 3),
                "sleeping_cpu_percent": round(cpu, 4),
                "sample_seconds": round(elapsed, 3),
            }
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--seconds", type=float, default=2.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not 5 <= args.runs <= 1_000:
        parser.error("--runs must be between 5 and 1000")
    if not 0.5 <= args.seconds <= 60:
        parser.error("--seconds must be between 0.5 and 60")
    result = {
        "hook": hook_latency(args.runs),
        "worker": worker_resource(args.seconds),
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"hook average {result['hook']['average_ms']} ms "
            f"(p95 {result['hook']['p95_ms']} ms); "
            f"worker {result['worker']['rss_mib']} MiB RSS, "
            f"{result['worker']['sleeping_cpu_percent']}% sleeping CPU"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
