from __future__ import annotations

import os
import random
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from PIL import Image

from litclock.runtime_bundle import (
    BundleManifest,
    DateAssetRecord,
    QuoteAssetRecord,
    sha256_file,
    write_manifest,
)

PROJECT = Path(__file__).resolve().parents[1]
NATIVE_ROOT = PROJECT / "kindle/native"
NATIVE = Path(os.environ.get("LITCLOCK_NATIVE_BIN", NATIVE_ROOT / "build/host/litclock-native"))
SHELL = PROJECT / "kindle/runtime/literary-clock-runtime.sh"
DISPLAY = PROJECT / "kindle/runtime/literary-clock-display.sh"


@dataclass(frozen=True)
class PairResult:
    shell: subprocess.CompletedProcess[str]
    native: subprocess.CompletedProcess[str]
    shell_state: str
    native_state: str
    shell_event: dict[str, str]
    native_event: dict[str, str]


@pytest.fixture(scope="session", autouse=True)
def build_native_runtime() -> None:
    result = subprocess.run(
        ["make", "host"], cwd=NATIVE_ROOT, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("1", (8, 8), 1).save(path)


def _release(
    root: Path,
    *,
    version: str = "v1",
    minutes: dict[int, tuple[int, ...]] | None = None,
    quote_identities: dict[int, tuple[str, str]] | None = None,
) -> Path:
    release = root / version
    bundle = release / "bundle"
    minutes = minutes or {100: (1, 2, 3), 101: (1, 2, 3), 820: (1, 2, 3)}
    quote_ids = sorted({quote_id for pool in minutes.values() for quote_id in pool})
    quote_identities = quote_identities or {
        quote_id: (f"book{quote_id}", f"author{quote_id}") for quote_id in quote_ids
    }
    quotes: dict[int, QuoteAssetRecord] = {}
    for quote_id in quote_ids:
        frame = bundle / f"frames/q{quote_id}.png"
        _png(frame)
        book, author = quote_identities[quote_id]
        quotes[quote_id] = QuoteAssetRecord(
            quote_id,
            f"frames/q{quote_id}.png",
            book,
            author,
            sha256_file(frame),
            frame.stat().st_size,
            8,
            8,
        )
    dates: dict[str, DateAssetRecord] = {}
    for key in ("0-09-06", "1-09-07"):
        frame = bundle / f"dates/{key}.png"
        _png(frame)
        dates[key] = DateAssetRecord(
            key,
            f"dates/{key}.png",
            10,
            20,
            sha256_file(frame),
            frame.stat().st_size,
            8,
            8,
        )
    manifest = BundleManifest(
        {
            "format_version": "1",
            "release_format_version": "1",
            "runtime_version": "2",
            "asset_set_version": version,
            "complete": "0",
            "renderer_preset": "pw4-v1",
            "corpus_fingerprint": "synthetic-only",
        },
        minutes,
        quotes,
        dates,
    )
    write_manifest(bundle, manifest)
    release.mkdir(parents=True, exist_ok=True)
    (release / "release.meta").write_text(
        "release_format_version\t1\n"
        "runtime_version\t2\n"
        f"release_version\t{version}\n"
        "renderer_preset\tpw4-v1\n"
        "corpus_fingerprint\tsynthetic-only\n"
    )
    return release


def _event(path: Path) -> dict[str, str]:
    if not path.exists() or not path.read_text().splitlines():
        return {}
    fields: dict[str, str] = {}
    for token in path.read_text().splitlines()[-1].split():
        if "=" in token:
            key, value = token.split("=", 1)
            fields[key] = value
    return fields


def _environment(
    release: Path,
    state_dir: Path,
    log: Path,
    snapshot: str,
    *,
    display_status: int = 0,
    delay_before_display: int = 0,
    delay_before_commit: int = 0,
) -> dict[str, str]:
    return {
        **os.environ,
        "LITCLOCK_RELEASE_ROOT": str(release),
        "LITCLOCK_STATE_ROOT": str(state_dir),
        "LITCLOCK_LOG_FILE": str(log),
        "LITCLOCK_DISPLAY_HELPER": str(DISPLAY),
        "LITCLOCK_FAKE_DISPLAY_STATUS": str(display_status),
        "LITCLOCK_TIMESTAMP_SNAPSHOT": snapshot,
        "LITCLOCK_FULL_REFRESH_INTERVAL": "15",
        "LITCLOCK_TEST_DELAY_BEFORE_DISPLAY": str(delay_before_display),
        "LITCLOCK_TEST_DELAY_BEFORE_COMMIT": str(delay_before_commit),
    }


def _run_one(
    command: list[str],
    release: Path,
    state_dir: Path,
    log: Path,
    snapshot: str,
    **environment: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        env=_environment(release, state_dir, log, snapshot, **environment),
        text=True,
        capture_output=True,
        check=False,
    )


def _prepare_pair(tmp_path: Path, state_text: str = "") -> tuple[Path, Path, Path, Path]:
    shell_state = tmp_path / "shell-state"
    native_state = tmp_path / "native-state"
    shell_state.mkdir(parents=True)
    native_state.mkdir(parents=True)
    if state_text:
        (shell_state / "state.tsv").write_text(state_text)
        (native_state / "state.tsv").write_text(state_text)
    return shell_state, native_state, tmp_path / "shell.log", tmp_path / "native.log"


def _run_pair(
    tmp_path: Path,
    release: Path,
    snapshot: str,
    *,
    state_text: str = "",
    compare_event: bool = True,
    **environment: int,
) -> PairResult:
    shell_state, native_state, shell_log, native_log = _prepare_pair(tmp_path, state_text)
    shell = _run_one(
        ["sh", str(SHELL)],
        state_dir=shell_state,
        release=release,
        log=shell_log,
        snapshot=snapshot,
        **environment,
    )
    native = _run_one(
        [str(NATIVE)],
        state_dir=native_state,
        release=release,
        log=native_log,
        snapshot=snapshot,
        **environment,
    )
    shell_text = (
        (shell_state / "state.tsv").read_text() if (shell_state / "state.tsv").exists() else ""
    )
    native_text = (
        (native_state / "state.tsv").read_text() if (native_state / "state.tsv").exists() else ""
    )
    result = PairResult(
        shell,
        native,
        shell_text,
        native_text,
        _event(shell_log),
        _event(native_log),
    )
    assert native.returncode == shell.returncode, (shell.stderr, native.stderr)
    assert native_text == shell_text
    if compare_event:
        for field in (
            "quote_id",
            "selection_reason",
            "refresh_mode",
            "display_result",
            "history_commit",
            "display_count",
        ):
            assert result.native_event.get(field) == result.shell_event.get(field), field
    return result


def _snapshot(epoch: int, minute: int = 100, *, day: int = 6) -> str:
    hour, minute_part = divmod(minute, 60)
    weekday = 0 if day == 6 else 1
    return f"{epoch}|2026-09-{day:02d}|{hour:02d}{minute_part:02d}|{weekday}-09-{day:02d}"


def _history(
    epoch: int,
    quote_id: int,
    book: str,
    author: str,
    *,
    minute: int = 99,
) -> str:
    return f"H\t{epoch}\t2026-09-06\t{minute:04d}\t{quote_id}\t{book}\t{author}\n"


def test_native_shell_parity_fresh_state(tmp_path: Path) -> None:
    release = _release(tmp_path / "releases")
    result = _run_pair(tmp_path / "case", release, _snapshot(200_000))
    assert result.shell_event["selection_reason"] == "0"
    assert result.shell_event["refresh_mode"] == "GC16"


def test_native_shell_parity_same_minute_idempotence(tmp_path: Path) -> None:
    release = _release(tmp_path / "releases")
    shell_state, native_state, shell_log, native_log = _prepare_pair(tmp_path / "case")
    snapshot = _snapshot(200_000)
    for command, state, log in (
        (["sh", str(SHELL)], shell_state, shell_log),
        ([str(NATIVE)], native_state, native_log),
    ):
        assert _run_one(command, release, state, log, snapshot).returncode == 0
        assert _run_one(command, release, state, log, snapshot).returncode == 0
    assert (native_state / "state.tsv").read_text() == (shell_state / "state.tsv").read_text()
    assert _event(native_log)["selection_reason"] == _event(shell_log)["selection_reason"]
    assert _event(native_log)["selection_reason"] == "already-current"


def test_native_shell_parity_quote_cooldown(tmp_path: Path) -> None:
    release = _release(tmp_path / "releases")
    state = (
        "VERSION\t2\nDISPLAY_COUNT\t8\n"
        + _history(199_990, 1, "other-book", "other-author")
        + "BAG\t0100\t1,2\n"
    )
    result = _run_pair(tmp_path / "case", release, _snapshot(200_000), state_text=state)
    assert result.shell_event["quote_id"] == "2"


def test_native_shell_parity_book_cooldown(tmp_path: Path) -> None:
    release = _release(tmp_path / "releases")
    state = (
        "VERSION\t2\nDISPLAY_COUNT\t8\n"
        + _history(199_990, 99, "book1", "other-author")
        + "BAG\t0100\t1,2\n"
    )
    result = _run_pair(tmp_path / "case", release, _snapshot(200_000), state_text=state)
    assert result.shell_event["quote_id"] == "2"


def test_native_shell_parity_author_cooldown(tmp_path: Path) -> None:
    release = _release(tmp_path / "releases")
    state = (
        "VERSION\t2\nDISPLAY_COUNT\t8\n"
        + _history(199_990, 99, "other-book", "author1")
        + "BAG\t0100\t1,2\n"
    )
    result = _run_pair(tmp_path / "case", release, _snapshot(200_000), state_text=state)
    assert result.shell_event["quote_id"] == "2"


@pytest.mark.parametrize(
    ("rows", "expected_level"),
    [
        ("", 0),
        (_history(199_990, 99, "other-book", "author1"), 1),
        (_history(199_990, 99, "book1", "other-author"), 2),
        (
            _history(199_990, 98, "book1", "other-author")
            + _history(199_990, 99, "other-book", "author1"),
            3,
        ),
        (_history(199_990, 1, "other-book", "other-author"), 4),
        (
            _history(199_990, 1, "other-book", "other-author")
            + _history(199_990, 99, "unrelated", "author1"),
            5,
        ),
        (
            _history(199_990, 1, "other-book", "other-author")
            + _history(199_990, 99, "book1", "unrelated"),
            6,
        ),
        (_history(199_990, 1, "book1", "author1"), 7),
    ],
)
def test_native_shell_parity_all_progressive_relaxation_levels(
    tmp_path: Path, rows: str, expected_level: int
) -> None:
    release = _release(tmp_path / "releases", minutes={100: (1,)})
    state = "VERSION\t2\nDISPLAY_COUNT\t8\n" + rows + "BAG\t0100\t1\n"
    result = _run_pair(tmp_path / "case", release, _snapshot(200_000), state_text=state)
    assert result.shell_event["selection_reason"] == str(expected_level)


def test_native_shell_parity_shared_am_pm_pool(tmp_path: Path) -> None:
    release = _release(tmp_path / "releases", minutes={100: (1, 2), 820: (1, 2)})
    shell_state, native_state, shell_log, native_log = _prepare_pair(
        tmp_path / "case", "VERSION\t2\nDISPLAY_COUNT\t0\nBAG\t0100\t1,2\nBAG\t0820\t1,2\n"
    )
    for command, state, log in (
        (["sh", str(SHELL)], shell_state, shell_log),
        ([str(NATIVE)], native_state, native_log),
    ):
        assert _run_one(command, release, state, log, _snapshot(200_000, 100)).returncode == 0
        assert _run_one(command, release, state, log, _snapshot(200_060, 820)).returncode == 0
    assert (native_state / "state.tsv").read_text() == (shell_state / "state.tsv").read_text()
    assert _event(shell_log)["quote_id"] == _event(native_log)["quote_id"] == "2"


def test_native_shell_parity_release_switch(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    first = _release(releases, version="v1")
    second = _release(releases, version="v2")
    shell_state, native_state, shell_log, native_log = _prepare_pair(tmp_path / "case")
    snapshot = _snapshot(200_000)
    for command, state, log in (
        (["sh", str(SHELL)], shell_state, shell_log),
        ([str(NATIVE)], native_state, native_log),
    ):
        assert _run_one(command, first, state, log, snapshot).returncode == 0
        assert _run_one(command, second, state, log, snapshot).returncode == 0
    assert (native_state / "state.tsv").read_text() == (shell_state / "state.tsv").read_text()
    assert (shell_state / "state.tsv").read_text().splitlines()[-1].endswith("\tv2")


def test_native_shell_parity_date_change(tmp_path: Path) -> None:
    release = _release(tmp_path / "releases")
    state = "VERSION\t2\nDISPLAY_COUNT\t4\nLAST\t2026-09-06\t0099\t199940\t3\tv1\n"
    result = _run_pair(tmp_path / "case", release, _snapshot(200_000, 101, day=7), state_text=state)
    assert result.shell_event["refresh_mode"] == "GC16"


def test_native_shell_parity_periodic_gc16(tmp_path: Path) -> None:
    release = _release(tmp_path / "releases")
    state = "VERSION\t2\nDISPLAY_COUNT\t14\nBAG\t0100\t1,2\nLAST\t2026-09-06\t0099\t199940\t3\tv1\n"
    result = _run_pair(tmp_path / "case", release, _snapshot(200_000), state_text=state)
    assert result.shell_event["display_count"] == "15"
    assert result.shell_event["refresh_mode"] == "GC16"


def test_native_shell_parity_history_pruning_after_25_hours(tmp_path: Path) -> None:
    release = _release(tmp_path / "releases")
    state = (
        "VERSION\t2\nDISPLAY_COUNT\t1500\n"
        + _history(109_999, 99, "old-book", "old-author")
        + _history(110_000, 98, "edge-book", "edge-author")
        + "BAG\t0100\t1,2\n"
    )
    result = _run_pair(tmp_path / "case", release, _snapshot(200_000), state_text=state)
    assert "old-book" not in result.shell_state
    assert "edge-book" in result.shell_state


@pytest.mark.parametrize(("first_minute", "second_minute"), [(100, 101), (101, 100)])
def test_native_shell_parity_time_jump(
    tmp_path: Path, first_minute: int, second_minute: int
) -> None:
    release = _release(tmp_path / "releases")
    shell_state, native_state, shell_log, native_log = _prepare_pair(tmp_path / "case")
    for command, state, log in (
        (["sh", str(SHELL)], shell_state, shell_log),
        ([str(NATIVE)], native_state, native_log),
    ):
        assert (
            _run_one(command, release, state, log, _snapshot(200_000, first_minute)).returncode == 0
        )
        assert (
            _run_one(command, release, state, log, _snapshot(200_060, second_minute)).returncode
            == 0
        )
    assert (native_state / "state.tsv").read_text() == (shell_state / "state.tsv").read_text()
    assert _event(native_log)["quote_id"] == _event(shell_log)["quote_id"]


def test_native_shell_parity_missing_frame(tmp_path: Path) -> None:
    release = _release(tmp_path / "releases")
    (release / "bundle/frames/q1.png").unlink()
    state = "VERSION\t2\nDISPLAY_COUNT\t4\nBAG\t0100\t1,2\n"
    result = _run_pair(tmp_path / "case", release, _snapshot(200_000), state_text=state)
    assert result.shell.returncode == 3
    assert result.shell_event["history_commit"] == "no"


def test_native_shell_parity_recoverable_corrupt_state(tmp_path: Path) -> None:
    release = _release(tmp_path / "releases")
    state = "garbage\nVERSION\t2\nDISPLAY_COUNT\t4\nH\t0\nBAG\t0100\t1,2\n"
    _run_pair(tmp_path / "case", release, _snapshot(200_000), state_text=state)


def test_native_shell_parity_display_failure(tmp_path: Path) -> None:
    release = _release(tmp_path / "releases")
    state = "VERSION\t2\nDISPLAY_COUNT\t41\nBAG\t0100\t1,2\n"
    result = _run_pair(
        tmp_path / "case", release, _snapshot(200_000), state_text=state, display_status=9
    )
    assert result.shell.returncode == 9
    assert "DISPLAY_COUNT\t41" in result.shell_state


@pytest.mark.parametrize(("delay_before_display", "delay_before_commit"), [(30, 0), (0, 30)])
def test_native_shell_parity_signal_interruption_and_lock_recovery(
    tmp_path: Path, delay_before_display: int, delay_before_commit: int
) -> None:
    release = _release(tmp_path / "releases")
    shell_state, native_state, shell_log, native_log = _prepare_pair(tmp_path / "case")
    snapshot = _snapshot(200_000)
    processes: list[tuple[subprocess.Popen[str], Path, list[str], Path]] = []
    for command, state, log in (
        (["sh", str(SHELL)], shell_state, shell_log),
        ([str(NATIVE)], native_state, native_log),
    ):
        process = subprocess.Popen(
            command,
            env=_environment(
                release,
                state,
                log,
                snapshot,
                delay_before_display=delay_before_display,
                delay_before_commit=delay_before_commit,
            ),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        processes.append((process, state, command, log))
    time.sleep(0.2)
    for _, state, command, log in processes:
        overlap = _run_one(command, release, state, log, snapshot)
        assert overlap.returncode == 20
    for process, _, _, _ in processes:
        os.killpg(process.pid, signal.SIGTERM)
    for process, state, command, log in processes:
        process.wait(timeout=5)
        assert process.returncode == 143
        assert not (state / "state.tsv").exists()
        assert _run_one(command, release, state, log, snapshot).returncode == 0
    assert (native_state / "state.tsv").read_text() == (shell_state / "state.tsv").read_text()


def test_native_shell_parity_stale_lock(tmp_path: Path) -> None:
    release = _release(tmp_path / "releases")
    shell_state, native_state, shell_log, native_log = _prepare_pair(tmp_path / "case")
    shell_lock = shell_state / "run.lock"
    shell_lock.mkdir()
    (shell_lock / "pid").write_text("99999999\n")
    (native_state / ".litclock-native.lock").write_text("99999999\n")
    snapshot = _snapshot(200_000)
    shell = _run_one(["sh", str(SHELL)], release, shell_state, shell_log, snapshot)
    native = _run_one([str(NATIVE)], release, native_state, native_log, snapshot)
    assert shell.returncode == native.returncode == 0
    assert (native_state / "state.tsv").read_text() == (shell_state / "state.tsv").read_text()


def test_native_shell_parity_empty_active_minute_row(tmp_path: Path) -> None:
    release = _release(tmp_path / "releases")
    minutes = release / "bundle/minutes.tsv"
    minutes.write_text(minutes.read_text().replace("0100\t1,2,3", "0100\t"))
    result = _run_pair(
        tmp_path / "case",
        release,
        _snapshot(200_000),
        compare_event=False,
    )
    assert result.shell.returncode == 39


def test_native_shell_parity_incompatible_release_version(tmp_path: Path) -> None:
    release = _release(tmp_path / "releases")
    metadata = release / "release.meta"
    metadata.write_text(
        metadata.read_text().replace("release_format_version\t1", "release_format_version\t9")
    )
    result = _run_pair(tmp_path / "case", release, _snapshot(200_000), compare_event=False)
    assert result.shell.returncode == 25


def test_native_shell_parity_incompatible_bundle_runtime(tmp_path: Path) -> None:
    release = _release(tmp_path / "releases")
    metadata = release / "bundle/bundle.meta"
    metadata.write_text(metadata.read_text().replace("runtime_version\t2", "runtime_version\t9"))
    result = _run_pair(tmp_path / "case", release, _snapshot(200_000), compare_event=False)
    assert result.shell.returncode == 31


def test_native_shell_parity_v1_state_migration(tmp_path: Path) -> None:
    release = _release(tmp_path / "releases")
    state = (
        "VERSION\t1\n"
        + _history(199_800, 1, "book1", "author1")
        + _history(199_860, 2, "book2", "author2")
        + "BAG\t0100\t3,1,2\n"
    )
    result = _run_pair(tmp_path / "case", release, _snapshot(200_000), state_text=state)
    assert "VERSION\t2\nDISPLAY_COUNT\t3\n" in result.shell_state


def test_native_rejects_traversal_asset_path(tmp_path: Path) -> None:
    release = _release(tmp_path / "releases", minutes={100: (1,)})
    quotes = release / "bundle/quotes.tsv"
    quotes.write_text(quotes.read_text().replace("frames/q1.png", "../q1.png"))
    state = tmp_path / "state"
    log = tmp_path / "native.log"
    result = _run_one([str(NATIVE)], release, state, log, _snapshot(200_000))
    assert result.returncode == 41
    assert not (state / "state.tsv").exists()


def test_native_shell_randomized_differential_sequences(tmp_path: Path) -> None:
    release = _release(
        tmp_path / "releases", minutes={100: (1, 2, 3, 4, 5, 6), 101: (1, 2, 3, 4, 5, 6)}
    )
    for seed in range(25):
        generator = random.Random(seed)
        order = list(range(1, 7))
        generator.shuffle(order)
        now = 300_000 + seed * 120
        rows = ["VERSION\t2", f"DISPLAY_COUNT\t{generator.randrange(0, 100)}"]
        for history_index in range(generator.randrange(0, 12)):
            quote_id = generator.randrange(1, 7)
            age = generator.randrange(-3_600, 100_000)
            rows.append(
                _history(
                    now - age,
                    quote_id,
                    f"book{quote_id}",
                    f"author{quote_id}",
                    minute=80 + history_index,
                ).rstrip("\n")
            )
        rows.append("BAG\t0100\t" + ",".join(str(value) for value in order))
        _run_pair(
            tmp_path / f"random-{seed}",
            release,
            _snapshot(now),
            state_text="\n".join(rows) + "\n",
        )
