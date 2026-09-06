from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from PIL import Image

from litclock.runtime_bundle import (
    BundleManifest,
    DateAssetRecord,
    QuoteAssetRecord,
    sha256_file,
    write_manifest,
)

PROJECT = Path(__file__).resolve().parents[1]
RUNTIME = PROJECT / "kindle/runtime/literary-clock-runtime.sh"
DISPLAY = PROJECT / "kindle/runtime/literary-clock-display.sh"


def _png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("1", (8, 8), 1).save(path)


def _runtime_tree(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "runtime"
    bundle = root / "bundles/v1"
    frame1 = bundle / "frames/q1.png"
    frame2 = bundle / "frames/q2.png"
    date_frame = bundle / "dates/0-09-06.png"
    for path in (frame1, frame2, date_frame):
        _png(path)
    manifest = BundleManifest(
        {"format_version": "1", "asset_set_version": "v1", "complete": "0"},
        {754: (1, 2), 755: (1, 2), 819: (1, 2)},
        {
            1: QuoteAssetRecord(
                1,
                "frames/q1.png",
                "book1",
                "author1",
                sha256_file(frame1),
                frame1.stat().st_size,
                8,
                8,
            ),
            2: QuoteAssetRecord(
                2,
                "frames/q2.png",
                "book2",
                "author2",
                sha256_file(frame2),
                frame2.stat().st_size,
                8,
                8,
            ),
        },
        {
            "0-09-06": DateAssetRecord(
                "0-09-06",
                "dates/0-09-06.png",
                10,
                20,
                sha256_file(date_frame),
                date_frame.stat().st_size,
                8,
                8,
            )
        },
    )
    write_manifest(bundle, manifest)
    root.mkdir(parents=True, exist_ok=True)
    (root / "current").write_text("v1\n")
    env = {
        **os.environ,
        "LITCLOCK_RUNTIME_ROOT": str(root),
        "LITCLOCK_DISPLAY_HELPER": str(DISPLAY),
        "LITCLOCK_FAKE_DISPLAY_STATUS": "0",
        "LITCLOCK_TIMESTAMP_SNAPSHOT": "1788712440|2026-09-06|1234|0-09-06",
    }
    return root, env


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(RUNTIME)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _history(root: Path) -> list[list[str]]:
    state = root / "state/state.tsv"
    if not state.exists():
        return []
    return [line.split("\t") for line in state.read_text().splitlines() if line.startswith("H\t")]


def test_history_is_committed_only_after_display_success(tmp_path: Path) -> None:
    root, env = _runtime_tree(tmp_path)
    failed = {**env, "LITCLOCK_FAKE_DISPLAY_STATUS": "9"}

    result = _run(failed)
    assert result.returncode == 9
    assert _history(root) == []

    result = _run(env)
    assert result.returncode == 0, result.stderr
    assert len(_history(root)) == 1


def test_invalid_or_missing_frame_does_not_commit_history(tmp_path: Path) -> None:
    root, env = _runtime_tree(tmp_path)
    (root / "bundles/v1/frames/q1.png").write_bytes(b"not a PNG")
    # Fix the bag so the corrupt asset is selected first.
    state_dir = root / "state"
    state_dir.mkdir()
    (state_dir / "state.tsv").write_text("VERSION\t1\nBAG\t0754\t1,2\n")

    result = _run(env)

    assert result.returncode == 3
    assert _history(root) == []


def test_time_jump_uses_current_minute_without_replaying_missed_minutes(tmp_path: Path) -> None:
    root, env = _runtime_tree(tmp_path)
    assert _run(env).returncode == 0
    jumped = {
        **env,
        "LITCLOCK_TIMESTAMP_SNAPSHOT": "1788716340|2026-09-06|1339|0-09-06",
    }

    result = _run(jumped)

    assert result.returncode == 0, result.stderr
    assert [row[3] for row in _history(root)] == ["0754", "0819"]


def test_shared_quote_global_cooldown_prefers_alternative(tmp_path: Path) -> None:
    root, env = _runtime_tree(tmp_path)
    state = root / "state/state.tsv"
    state.parent.mkdir()
    state.write_text("VERSION\t1\nBAG\t0754\t1,2\nBAG\t0755\t1,2\n")
    assert _run(env).returncode == 0
    later = {
        **env,
        "LITCLOCK_TIMESTAMP_SNAPSHOT": "1788712500|2026-09-06|1235|0-09-06",
    }

    assert _run(later).returncode == 0

    assert [row[4] for row in _history(root)] == ["1", "2"]


def test_same_local_minute_is_idempotent_across_restart(tmp_path: Path) -> None:
    root, env = _runtime_tree(tmp_path)
    assert _run(env).returncode == 0

    result = _run(env)

    assert result.returncode == 0
    assert len(_history(root)) == 1
    assert "already-current" in (root / "literary-clock.log").read_text()


def test_new_asset_set_is_displayed_even_with_same_local_minute(tmp_path: Path) -> None:
    root, env = _runtime_tree(tmp_path)
    assert _run(env).returncode == 0
    state = root / "state/state.tsv"
    assert state.read_text().splitlines()[-1].endswith("\tv1")
    source = root / "bundles/v1"
    target = root / "bundles/v2"
    shutil.copytree(source, target)
    (root / "current").write_text("v2\n")

    assert _run(env).returncode == 0
    assert len(_history(root)) == 2
    assert state.read_text().splitlines()[-1].endswith("\tv2")


def test_orphaned_temp_file_does_not_replace_last_good_state(tmp_path: Path) -> None:
    root, env = _runtime_tree(tmp_path)
    assert _run(env).returncode == 0
    (root / "state/state.999.tmp").write_text("corrupt partial write")
    jumped = {
        **env,
        "LITCLOCK_TIMESTAMP_SNAPSHOT": "1788712500|2026-09-06|1235|0-09-06",
    }

    assert _run(jumped).returncode == 0
    assert len(_history(root)) == 2


def test_runtime_uses_one_snapshot_for_date_and_minute(tmp_path: Path) -> None:
    root, env = _runtime_tree(tmp_path)
    env["LITCLOCK_TIMESTAMP_SNAPSHOT"] = "1788753600|2026-09-06|1234|0-09-06"

    assert _run(env).returncode == 0

    history = _history(root)[0]
    assert history[2] == "2026-09-06"
    assert history[3] == "0754"


def test_device_shell_sources_are_syntax_valid() -> None:
    scripts = sorted((PROJECT / "kindle").rglob("*.sh"))
    for script in scripts:
        result = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, f"{script}: {result.stderr}"


def test_pilot_registers_job_before_scheduler_daemon_starts() -> None:
    source = (PROJECT / "kindle/runtime/literary-clock-pilot.sh").read_text()

    add = source.index("add -timeout 30s literary-clock-minute")
    daemon = source.index("-jobtimeout 30s daemon")
    assert add < daemon
