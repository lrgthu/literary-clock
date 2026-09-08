from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from PIL import Image

from litclock.runtime_bundle import (
    RELEASE_FORMAT_VERSION,
    RUNTIME_VERSION,
    BundleManifest,
    DateAssetRecord,
    QuoteAssetRecord,
    sha256_file,
    write_manifest,
)

PROJECT = Path(__file__).resolve().parents[1]
RUNTIME = PROJECT / "kindle/runtime/literary-clock-runtime.sh"
DISPLAY = PROJECT / "kindle/runtime/literary-clock-display.sh"
LAUNCHER = PROJECT / "kindle/runtime/literary-clock-launch-current.sh"
POWER_STUDY = PROJECT / "kindle/runtime/literary-clock-power-study-start.sh"
POWER_STUDY_STOP = PROJECT / "kindle/runtime/literary-clock-power-study-stop.sh"
SERVICE_START = PROJECT / "kindle/runtime/literary-clock-service-start.sh"
SERVICE_STOP = PROJECT / "kindle/runtime/literary-clock-service-stop.sh"
SERVICE_START_CURRENT = PROJECT / "kindle/runtime/literary-clock-service-start-current.sh"
SERVICE_STOP_CURRENT = PROJECT / "kindle/runtime/literary-clock-service-stop-current.sh"


def _png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("1", (8, 8), 1).save(path)


def _runtime_tree(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "runtime"
    release = root / "releases/v1"
    bundle = release / "bundle"
    frame1 = bundle / "frames/q1.png"
    frame2 = bundle / "frames/q2.png"
    frame3 = bundle / "frames/q3.png"
    date_frame = bundle / "dates/0-09-06.png"
    for path in (frame1, frame2, frame3, date_frame):
        _png(path)
    manifest = BundleManifest(
        {
            "format_version": "1",
            "release_format_version": str(RELEASE_FORMAT_VERSION),
            "runtime_version": str(RUNTIME_VERSION),
            "asset_set_version": "v1",
            "complete": "0",
            "renderer_preset": "pw4-v1",
            "corpus_fingerprint": "test-fingerprint",
        },
        {184: (1,), 270: (3,), 754: (1, 2), 755: (1, 2), 819: (1, 2)},
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
            3: QuoteAssetRecord(
                3,
                "frames/q3.png",
                "book3",
                "author3",
                sha256_file(frame3),
                frame3.stat().st_size,
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
    release.mkdir(parents=True, exist_ok=True)
    (release / "release.meta").write_text(
        "release_format_version\t1\n"
        "runtime_version\t2\n"
        "release_version\tv1\n"
        "renderer_preset\tpw4-v1\n"
        "corpus_fingerprint\ttest-fingerprint\n"
    )
    release_runtime = release / "bin/literary-clock-runtime.sh"
    release_runtime.parent.mkdir()
    shutil.copyfile(RUNTIME, release_runtime)
    release_runtime.chmod(0o755)
    root.mkdir(parents=True, exist_ok=True)
    (root / "current-release").write_text("v1\n")
    env = {
        **os.environ,
        "LITCLOCK_RUNTIME_ROOT": str(root),
        "LITCLOCK_RELEASE_ROOT": str(release),
        "LITCLOCK_STATE_ROOT": str(root / "state"),
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


def _service_tree(tmp_path: Path, *, display_status: int = 0) -> tuple[Path, dict[str, str]]:
    runtime_root = tmp_path / "runtime"
    release = runtime_root / "releases/v1"
    binary = release / "bin"
    binary.mkdir(parents=True)
    (release / "release.meta").write_text("release_version\tv1\n")
    shutil.copyfile(SERVICE_STOP, binary / "literary-clock-service-stop.sh")
    (binary / "literary-clock-service-stop.sh").chmod(0o755)
    validator = binary / "literary-clock-validate-release.sh"
    validator.write_text("#!/bin/sh\nexit 0\n")
    validator.chmod(0o755)
    launcher = runtime_root / "literary-clock-launch-current.sh"
    launcher.write_text(
        f"#!/bin/sh\necho display >> '{tmp_path / 'display.log'}'\nexit {display_status}\n"
    )
    launcher.chmod(0o755)
    (runtime_root / "service.conf").write_text(
        "cadence_minutes=1\nkeepawake=3m\nfull_refresh_interval=15\nauto_stop_minutes=0\n"
    )

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    kron = fake_bin / "kron"
    kron.write_text(
        "#!/bin/sh\n"
        'case " $* " in\n'
        '  *" daemon "*)\n'
        '    echo $$ > "$FAKE_KRON_PID_FILE"\n'
        "    trap 'exit 0' 1 2 15\n"
        "    while :; do sleep 1; done\n"
        "    ;;\n"
        '  *" stop "*)\n'
        '    if test -r "$FAKE_KRON_PID_FILE"; then\n'
        '      kill "$(cat "$FAKE_KRON_PID_FILE")" 2>/dev/null || true\n'
        "    fi\n"
        "    exit 0\n"
        "    ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    kron.chmod(0o755)
    (fake_bin / "lipc-get-prop").write_text("#!/bin/sh\necho 0\n")
    (fake_bin / "lipc-set-prop").write_text(
        f"#!/bin/sh\necho \"$*\" >> '{tmp_path / 'lipc.log'}'\n"
    )
    (fake_bin / "pidof").write_text("#!/bin/sh\nexit 1\n")
    (fake_bin / "sync").write_text("#!/bin/sh\nexit 0\n")
    for name in ("lipc-get-prop", "lipc-set-prop", "pidof", "sync"):
        (fake_bin / name).chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "LITCLOCK_RUNTIME_ROOT": str(runtime_root),
        "LITCLOCK_RELEASE_ROOT": str(release),
        "LITCLOCK_SERVICE_ROOT": str(tmp_path / "service"),
        "LITCLOCK_STATE_ROOT": str(tmp_path / "state"),
        "LITCLOCK_SERVICE_CONFIG": str(runtime_root / "service.conf"),
        "LITCLOCK_KRON": str(kron),
        "LITCLOCK_CURRENT_LAUNCHER": str(launcher),
        "FAKE_KRON_PID_FILE": str(tmp_path / "kron.pid"),
        "LITCLOCK_TEST_ASSUME_PROCESS": "1",
    }
    return release, env


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
    (root / "releases/v1/bundle/frames/q1.png").write_bytes(b"not a PNG")
    # Fix the bag so the corrupt asset is selected first.
    state_dir = root / "state"
    state_dir.mkdir()
    (state_dir / "state.tsv").write_text("VERSION\t2\nDISPLAY_COUNT\t0\nBAG\t0754\t1,2\n")

    result = _run(env)

    assert result.returncode == 3
    assert _history(root) == []


def test_missing_fbink_does_not_commit_history(tmp_path: Path) -> None:
    root, env = _runtime_tree(tmp_path)
    failed = {key: value for key, value in env.items() if key != "LITCLOCK_FAKE_DISPLAY_STATUS"}
    failed["LITCLOCK_FBINK"] = str(tmp_path / "missing-fbink")

    result = _run(failed)

    assert result.returncode == 5
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
    state.write_text("VERSION\t2\nDISPLAY_COUNT\t0\nBAG\t0754\t1,2\nBAG\t0755\t1,2\n")
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
    source = root / "releases/v1"
    target = root / "releases/v2"
    shutil.copytree(source, target)
    metadata = target / "release.meta"
    metadata.write_text(metadata.read_text().replace("release_version\tv1", "release_version\tv2"))
    switched = {**env, "LITCLOCK_RELEASE_ROOT": str(target)}

    assert _run(switched).returncode == 0
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


def test_zero_padded_minute_keys_are_compared_as_text(tmp_path: Path) -> None:
    """04:30 must not become octal 0270 and match decimal minute 184 (03:04)."""
    root, env = _runtime_tree(tmp_path)
    env["LITCLOCK_TIMESTAMP_SNAPSHOT"] = "1788688200|2026-09-06|0430|0-09-06"

    result = _run(env)

    assert result.returncode == 0, result.stderr
    assert _history(root)[0][3:5] == ["0270", "3"]
    source = RUNTIME.read_text()
    assert '"m" $1 == "m" minute' in source
    assert '"m" $2 == "m" minute' in source
    assert '"m" $2 != "m" minute' in source


def test_display_count_survives_history_pruning_and_drives_gc16(tmp_path: Path) -> None:
    root, env = _runtime_tree(tmp_path)
    state = root / "state/state.tsv"
    state.parent.mkdir()
    state.write_text(
        "VERSION\t2\n"
        "DISPLAY_COUNT\t14\n"
        "H\t1\t1970-01-01\t0000\t99\told-book\told-author\n"
        "BAG\t0754\t1,2\n"
    )

    assert _run(env).returncode == 0

    text = state.read_text()
    assert "DISPLAY_COUNT\t15" in text
    assert "\t99\told-book\told-author" not in text
    assert "refresh_mode=GC16" in (root / "literary-clock.log").read_text()


def test_gc16_counter_remains_monotonic_after_more_than_25_hours(tmp_path: Path) -> None:
    root, env = _runtime_tree(tmp_path)
    state = root / "state/state.tsv"
    state.parent.mkdir()
    state.write_text(
        "VERSION\t2\n"
        "DISPLAY_COUNT\t1499\n"
        "H\t1\t1970-01-01\t0000\t99\told-book\told-author\n"
        "BAG\t0754\t1,2\n"
    )

    assert _run(env).returncode == 0

    text = state.read_text()
    assert "DISPLAY_COUNT\t1500" in text
    assert "\t99\told-book\told-author" not in text
    assert "refresh_mode=GC16" in (root / "literary-clock.log").read_text()


def test_failed_display_does_not_increment_persistent_count(tmp_path: Path) -> None:
    root, env = _runtime_tree(tmp_path)
    state = root / "state/state.tsv"
    state.parent.mkdir()
    state.write_text("VERSION\t2\nDISPLAY_COUNT\t41\nBAG\t0754\t1,2\n")

    result = _run({**env, "LITCLOCK_FAKE_DISPLAY_STATUS": "9"})

    assert result.returncode == 9
    assert "DISPLAY_COUNT\t41" in state.read_text()


def test_v1_state_migrates_counter_once(tmp_path: Path) -> None:
    root, env = _runtime_tree(tmp_path)
    state = root / "state/state.tsv"
    state.parent.mkdir()
    state.write_text(
        "VERSION\t1\n"
        "H\t1788700000\t2026-09-06\t0700\t1\tbook1\tauthor1\n"
        "H\t1788700060\t2026-09-06\t0701\t2\tbook2\tauthor2\n"
        "BAG\t0754\t1,2\n"
    )

    assert _run(env).returncode == 0

    assert "VERSION\t2" in state.read_text()
    assert "DISPLAY_COUNT\t3" in state.read_text()


def test_incompatible_state_fails_without_display_or_mutation(tmp_path: Path) -> None:
    root, env = _runtime_tree(tmp_path)
    state = root / "state/state.tsv"
    state.parent.mkdir()
    original = "VERSION\t99\nDISPLAY_COUNT\t12\n"
    state.write_text(original)

    result = _run(env)

    assert result.returncode == 37
    assert state.read_text() == original
    assert _history(root) == []


def test_signal_exits_releases_lock_and_never_commits(tmp_path: Path) -> None:
    root, env = _runtime_tree(tmp_path)
    delayed = {**env, "LITCLOCK_TEST_DELAY_BEFORE_DISPLAY": "30"}
    process = subprocess.Popen(
        ["sh", str(RUNTIME)],
        env=delayed,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    lock = root / "state/run.lock"
    for _ in range(100):
        if lock.is_dir():
            break
        time.sleep(0.01)
    assert lock.is_dir()

    overlapping = _run(env)
    assert overlapping.returncode == 20
    os.killpg(process.pid, signal.SIGTERM)
    process.wait(timeout=5)

    assert process.returncode != 0
    assert not lock.exists()
    assert _history(root) == []
    assert _run(env).returncode == 0
    assert len(_history(root)) == 1


def test_signal_after_display_before_commit_never_records_false_history(
    tmp_path: Path,
) -> None:
    root, env = _runtime_tree(tmp_path)
    marker = tmp_path / "displayed"
    display = tmp_path / "display.sh"
    display.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 0\n")
    display.chmod(0o755)
    delayed = {
        **env,
        "LITCLOCK_DISPLAY_HELPER": str(display),
        "LITCLOCK_TEST_DELAY_BEFORE_COMMIT": "30",
    }
    process = subprocess.Popen(
        ["sh", str(RUNTIME)],
        env=delayed,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    for _ in range(100):
        if marker.exists():
            break
        time.sleep(0.01)
    assert marker.exists()

    os.killpg(process.pid, signal.SIGTERM)
    process.wait(timeout=5)

    assert process.returncode != 0
    assert _history(root) == []
    assert not (root / "state/run.lock").exists()


def test_runtime_rejects_incompatible_release_format(tmp_path: Path) -> None:
    root, env = _runtime_tree(tmp_path)
    metadata = root / "releases/v1/release.meta"
    metadata.write_text(
        metadata.read_text().replace("release_format_version\t1", "release_format_version\t9")
    )

    result = _run(env)

    assert result.returncode == 25
    assert _history(root) == []


def test_current_release_launcher_fails_closed_then_recovers(tmp_path: Path) -> None:
    root, env = _runtime_tree(tmp_path)
    pointer = root / "current-release"
    pointer.unlink()

    failed = subprocess.run(
        ["sh", str(LAUNCHER)], env=env, text=True, capture_output=True, check=False
    )
    assert failed.returncode == 40
    assert _history(root) == []

    pointer.write_text("v1\n")
    recovered = subprocess.run(
        ["sh", str(LAUNCHER)], env=env, text=True, capture_output=True, check=False
    )
    assert recovered.returncode == 0
    assert len(_history(root)) == 1


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


def test_pilot_cadence_is_configurable_without_polling_loop() -> None:
    source = (PROJECT / "kindle/runtime/literary-clock-pilot.sh").read_text()

    assert "LITCLOCK_UPDATE_MINUTES" in source
    assert '2 | 3 | 5) schedule="*/$update_minutes * * * *"' in source
    assert "sleep 1" not in source


def test_power_study_cleanup_follows_final_regular_boundary() -> None:
    source = POWER_STUDY.read_text()

    assert "first_boundary=" in source
    assert "last_boundary=" in source
    assert "stop_epoch=$((last_boundary + " in source
    assert "duration=$((cadence * cycles * 60 + 90))" not in source
    assert "keepawake=$(read_config keepawake off)" in source
    assert '-keepawake "$keepawake"' in source
    assert "keepawake must be 'off' or a duration" in source
    assert 'kill -0 "$kron_pid"' in source
    assert source.index('kill -0 "$kron_pid"') < source.index('"$launcher" >> "$status"')
    assert "reset_test_state=$(read_config reset_test_state 0)" in source
    assert '"$runtime_root/state" /var/local/literary-clock/state' in source
    assert "cycles must be 2..1440" in source
    assert "wireless_before" in source


def test_power_study_stop_never_signals_unverified_persisted_pids() -> None:
    source = POWER_STUDY_STOP.read_text()

    assert "process_is" in source
    assert "signal_if_matching" in source
    assert "kill -CONT $cvm_pids" not in source
    assert "kill -CONT $awesome_pids" not in source


def test_service_validates_before_pausing_ui_and_is_configurable() -> None:
    source = SERVICE_START.read_text()

    assert source.index('"$validator" "$release_root"') < source.index("kill -STOP")
    assert source.index('kill -0 "$kron_pid"') < source.index("kill -STOP")
    assert "cadence_minutes" in source
    assert "1 | 2 | 3 | 5" not in source  # each supported cadence is explicit
    assert "keepawake 3m" in source
    assert "auto_stop_minutes" in source
    assert "LITCLOCK_STATE_ROOT" in source
    assert "-jobtimeout 30s daemon >/dev/null 2>&1 &" in source


def test_service_stop_restores_current_named_processes_not_saved_pids() -> None:
    source = SERVICE_STOP.read_text()

    assert 'pidof "$name"' in source
    assert "kill -CONT" in source
    assert "awesome_pids" not in source
    assert "cvm_pids" not in source
    assert "preventScreenSaver" in source
    assert source.index('rm -f "$session" "$session.new"') < source.index(
        '"$kron" -dir "$runtime_root/kron" stop'
    )


def test_exact_service_starts_and_stops_transactionally(tmp_path: Path) -> None:
    release, env = _service_tree(tmp_path)

    started = subprocess.run(
        ["sh", str(SERVICE_START)], env=env, text=True, capture_output=True, check=False
    )
    assert started.returncode == 0, started.stderr
    session = tmp_path / "service/session.tsv"
    assert "active\tyes" in session.read_text()
    assert "display" in (tmp_path / "display.log").read_text()

    stopped = subprocess.run(
        ["sh", str(release / "bin/literary-clock-service-stop.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert stopped.returncode == 0, stopped.stderr
    assert not session.exists()
    assert "preventScreenSaver 0" in (tmp_path / "lipc.log").read_text()
    assert "app://com.lab126.KPPMainApp?view=KPP_HOME" in (tmp_path / "lipc.log").read_text()
    assert "home_restore=kpp" in (tmp_path / "service/status.txt").read_text()


def test_service_uses_installed_legacy_scheduler_path_when_tools_path_is_absent(
    tmp_path: Path,
) -> None:
    release, env = _service_tree(tmp_path)
    installed = tmp_path / "runtime/bin/kron"
    installed.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(env.pop("LITCLOCK_KRON")), installed)
    installed.chmod(0o755)

    started = subprocess.run(
        ["sh", str(SERVICE_START)], env=env, text=True, capture_output=True, check=False
    )
    assert started.returncode == 0, started.stderr

    stopped = subprocess.run(
        ["sh", str(release / "bin/literary-clock-service-stop.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert stopped.returncode == 0, stopped.stderr


def test_service_display_failure_restores_ui_and_leaves_no_active_session(
    tmp_path: Path,
) -> None:
    _, env = _service_tree(tmp_path, display_status=9)

    failed = subprocess.run(
        ["sh", str(SERVICE_START)], env=env, text=True, capture_output=True, check=False
    )

    assert failed.returncode == 85
    assert not (tmp_path / "service/session.tsv").exists()
    assert "preventScreenSaver 0" in (tmp_path / "lipc.log").read_text()


def test_service_missing_scheduler_fails_before_framework_mutation(tmp_path: Path) -> None:
    _, env = _service_tree(tmp_path)
    env["LITCLOCK_KRON"] = str(tmp_path / "missing-kron")

    failed = subprocess.run(
        ["sh", str(SERVICE_START)], env=env, text=True, capture_output=True, check=False
    )

    assert failed.returncode == 71
    assert not (tmp_path / "lipc.log").exists()
    assert not (tmp_path / "service/session.tsv").exists()


def test_service_current_wrappers_fail_closed_and_have_recovery_path() -> None:
    start = SERVICE_START_CURRENT.read_text()
    stop = SERVICE_STOP_CURRENT.read_text()

    assert "current release pointer is missing" in start
    assert "active release has no service start helper" in start
    assert "stock UI recovery attempted" in stop
    assert "kill -CONT" in stop


def test_boot_hook_is_one_shot_and_disarms_before_service_start() -> None:
    hook = (PROJECT / "kindle/runtime/literary-clock-boot-once.sh").read_text()

    assert hook.index('mv -f "$hook" "$used"') < hook.index('exec "$launcher"')
    assert "emergency.sh.used" in hook
    assert "literary-clock-service-start-current.sh" in hook


def test_boot_hook_self_disarms_before_launch(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    hook = tmp_path / "emergency.sh"
    shutil.copyfile(PROJECT / "kindle/runtime/literary-clock-boot-once.sh", hook)
    marker = tmp_path / "launched"
    launcher = runtime / "literary-clock-service-start-current.sh"
    launcher.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    launcher.chmod(0o755)

    result = subprocess.run(
        ["sh", str(hook)],
        env={
            **os.environ,
            "LITCLOCK_RUNTIME_ROOT": str(runtime),
            "LITCLOCK_BOOT_HOOK": str(hook),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert marker.is_file()
    assert not hook.exists()
    assert (runtime / "emergency.sh.used").is_file()


def test_runtime_introduces_no_mac_or_network_dependency() -> None:
    sources = "\n".join(
        path.read_text() for path in sorted((PROJECT / "kindle/runtime").glob("*.sh"))
    )

    assert "/Users/" not in sources
    assert all(token not in sources for token in ("ssh ", "curl ", "wget ", "http://", "https://"))
