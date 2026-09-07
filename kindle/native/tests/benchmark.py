#!/usr/bin/env python3
"""Measure the host-native one-shot path against an existing compatible bundle."""

from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path


def fields(line: str) -> dict[str, str]:
    return dict(token.split("=", 1) for token in line.split() if "=" in token)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=100)
    arguments = parser.parse_args()
    bundle = arguments.bundle.resolve()
    binary = arguments.binary.resolve()
    if arguments.iterations < 1:
        parser.error("--iterations must be positive")

    elapsed: list[float] = []
    metrics: dict[str, list[float]] = {
        name: []
        for name in ("state_read_ms", "manifest_ms", "selector_ms", "state_ms", "duration_ms")
    }
    with tempfile.TemporaryDirectory(prefix="litclock-native-benchmark-") as temporary:
        root = Path(temporary)
        release = root / "release"
        release.mkdir()
        (release / "bundle").symlink_to(bundle, target_is_directory=True)
        (release / "release.meta").write_text(
            "release_format_version\t1\n"
            "runtime_version\t2\n"
            "release_version\tphase4c-host-benchmark\n"
        )
        state = root / "state/state.tsv"
        log = root / "runtime.log"
        start = datetime(2026, 9, 6, 0, 0).astimezone()
        environment = {**os.environ, "LITCLOCK_FAKE_DISPLAY_STATUS": "0"}
        for index in range(arguments.iterations):
            local = start + timedelta(minutes=index)
            snapshot = f"{int(local.timestamp())}|{local:%Y-%m-%d|%H%M|%w-%m-%d}"
            command = [
                str(binary),
                "--release-root",
                str(release),
                "--state",
                str(state),
                "--log",
                str(log),
                "--timestamp",
                snapshot,
                "--fake-display-success",
            ]
            before = time.perf_counter_ns()
            result = subprocess.run(
                command, env=environment, text=True, capture_output=True, check=False
            )
            elapsed.append((time.perf_counter_ns() - before) / 1_000_000)
            if result.returncode != 0:
                raise RuntimeError(result.stderr)
            event = fields(log.read_text().splitlines()[-1])
            for name in metrics:
                metrics[name].append(float(event[name]))

    print(f"iterations={arguments.iterations}")
    print(f"wall_ms_median={statistics.median(elapsed):.3f}")
    print(f"wall_ms_p95={percentile(elapsed, 0.95):.3f}")
    for name, values in metrics.items():
        print(f"{name}_mean={statistics.mean(values):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
