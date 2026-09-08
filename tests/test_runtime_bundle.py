from __future__ import annotations

import os
import subprocess
from datetime import date
from pathlib import Path

import pytest
from PIL import Image

import litclock.deploy as deploy_module
from litclock.bundle import (
    _date_key,
    _generated_at,
    _possible_date_labels,
    _valid_cached_quote_ids,
    minute_window,
)
from litclock.deploy import (
    DeploymentError,
    deploy_bundle,
    rollback_bundle,
    set_boot_hook,
    set_production_boot_hook,
    validate_release,
)
from litclock.runtime_bundle import (
    RELEASE_FORMAT_VERSION,
    RUNTIME_VERSION,
    BundleManifest,
    BundleValidationError,
    DateAssetRecord,
    QuoteAssetRecord,
    read_manifest,
    sha256_file,
    validate_manifest,
    write_checksums,
    write_manifest,
)


def _png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("1", (8, 8), 1).save(path)


def _bundle(root: Path, *, version: str = "test-v1", all_minutes: bool = True) -> BundleManifest:
    frame = root / "frames/q1.png"
    date = root / "dates/0-09-06.png"
    _png(frame)
    _png(date)
    minutes = {minute: (1,) for minute in range(1440)} if all_minutes else {0: (1,)}
    manifest = BundleManifest(
        {
            "format_version": "1",
            "release_format_version": str(RELEASE_FORMAT_VERSION),
            "runtime_version": str(RUNTIME_VERSION),
            "asset_set_version": version,
            "complete": "1" if all_minutes else "0",
            "renderer_preset": "pw4-v1",
            "corpus_fingerprint": "test-fingerprint",
        },
        minutes,
        {
            1: QuoteAssetRecord(
                1,
                "frames/q1.png",
                "book",
                "author",
                sha256_file(frame),
                frame.stat().st_size,
                8,
                8,
            )
        },
        {
            "0-09-06": DateAssetRecord(
                "0-09-06",
                "dates/0-09-06.png",
                10,
                20,
                sha256_file(date),
                date.stat().st_size,
                8,
                8,
            )
        },
    )
    write_manifest(root, manifest)
    write_checksums(root, manifest)
    return manifest


def test_manifest_round_trip_requires_every_minute_and_preserves_shared_asset(
    tmp_path: Path,
) -> None:
    manifest = _bundle(tmp_path)

    loaded = read_manifest(tmp_path)
    validate_manifest(loaded, root=tmp_path, verify_checksums=True)

    assert loaded == manifest
    assert len(loaded.minutes) == 1440
    assert len(loaded.quotes) == 1
    assert all(ids == (1,) for ids in loaded.minutes.values())


def test_incremental_bundle_cache_reuses_only_sha256_verified_frames(tmp_path: Path) -> None:
    manifest = _bundle(tmp_path)

    assert _valid_cached_quote_ids(tmp_path, manifest) == {1}

    frame = tmp_path / manifest.quotes[1].frame
    frame.write_bytes(b"x" * frame.stat().st_size)
    assert _valid_cached_quote_ids(tmp_path, manifest) == set()


def test_minute_window_and_date_assets_cross_midnight_without_timezone_rules() -> None:
    labels = dict(_possible_date_labels())

    assert minute_window(1438, 4) == (1438, 1439, 0, 1)
    assert _date_key(date(2026, 9, 6)) == "0-09-06"
    assert labels["0-09-06"] == "Sun, Sep 6"
    assert labels["1-02-29"] == "Mon, Feb 29"


def test_source_date_epoch_makes_bundle_timestamp_reproducible(monkeypatch) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")

    assert _generated_at() == "1970-01-01T00:00:00+00:00"


def test_manifest_rejects_missing_asset_and_unreferenced_duplicate(tmp_path: Path) -> None:
    manifest = _bundle(tmp_path, all_minutes=False)
    extra = QuoteAssetRecord(2, "frames/q2.png", "b", "a", "0" * 64, 1)
    broken = BundleManifest(
        manifest.metadata,
        manifest.minutes,
        {**manifest.quotes, 2: extra},
        manifest.dates,
    )

    with pytest.raises(BundleValidationError, match="unreferenced"):
        validate_manifest(broken, root=tmp_path, require_all_minutes=False)


def test_manifest_rejects_wrong_image_dimensions(tmp_path: Path) -> None:
    manifest = _bundle(tmp_path, all_minutes=False)
    record = manifest.quotes[1]
    wrong = QuoteAssetRecord(
        record.quote_id,
        record.frame,
        record.book_id,
        record.author_id,
        record.sha256,
        record.byte_size,
        1072,
        1448,
    )

    with pytest.raises(BundleValidationError, match="wrong asset dimensions"):
        validate_manifest(
            BundleManifest(manifest.metadata, manifest.minutes, {1: wrong}, manifest.dates),
            root=tmp_path,
            require_all_minutes=False,
        )


def test_bundle_has_no_font_or_absolute_host_path(tmp_path: Path) -> None:
    _bundle(tmp_path)
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "bundle.meta", tmp_path / "minutes.tsv", tmp_path / "quotes.tsv")
    )

    assert "/Users/" not in text
    assert not list(tmp_path.rglob("*.ttf"))
    assert not list(tmp_path.rglob("*.otf"))


def test_deploy_is_staged_and_rollback_swaps_version(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[1]
    mount = tmp_path / "mount"
    mount.mkdir()
    first = tmp_path / "first"
    second = tmp_path / "second"
    _bundle(first, version="v1")
    _bundle(second, version="v2")

    assert deploy_bundle(first, mount, project, require_kindle=False) == "v1"
    assert deploy_bundle(second, mount, project, require_kindle=False) == "v2"
    runtime = mount / "literary-clock/runtime"
    assert (runtime / "current-release").read_text().strip() == "v2"
    assert (runtime / "previous-release").read_text().strip() == "v1"
    assert (runtime / "releases/v2/bundle/frames/q1.png").is_file()
    assert (runtime / "releases/v2/bin/literary-clock-runtime.sh").is_file()
    inventory = (runtime / "releases/v2/checksums.sha256").read_text()
    assert "bundle/minutes.tsv" in inventory
    assert "bundle/checksums.sha256" in inventory
    assert "bundle/frames/q1.png" in inventory
    assert "bin/literary-clock-runtime.sh" in inventory
    assert "bin/literary-clock-power.sh" in inventory
    assert "bin/literary-clock-service-start.sh" in inventory
    assert "bin/literary-clock-service-stop.sh" in inventory
    assert "bin/literary-clock-validate-release.sh" in inventory
    assert "boot/emergency.sh" in inventory
    assert (runtime / "literary-clock-service-start-current.sh").is_file()
    assert (runtime / "literary-clock-service-stop-current.sh").is_file()
    assert rollback_bundle(mount, require_kindle=False) == "v1"
    assert (runtime / "current-release").read_text().strip() == "v1"
    assert (runtime / "previous-release").read_text().strip() == "v2"


def test_native_release_packages_binary_and_preserves_shell_fallback(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    mount = tmp_path / "mount"
    native = tmp_path / "litclock-native"
    mount.mkdir()
    native.write_bytes(b"native-test-binary")
    native.chmod(0o755)
    _bundle(source, version="native-v1")

    deploy_bundle(
        source,
        mount,
        project,
        require_kindle=False,
        native_binary=native,
    )
    release = mount / "literary-clock/runtime/releases/native-v1"
    metadata = dict(
        line.split("\t", 1) for line in (release / "release.meta").read_text().splitlines()
    )
    inventory = (release / "checksums.sha256").read_text()

    assert metadata["runtime_engine"] == "native"
    assert metadata["architecture"] == "armv7-eabi5-hard-float-static"
    assert metadata["native_binary_sha256"] == sha256_file(release / "bin/litclock-native")
    assert "bin/litclock-native" in inventory
    assert (release / "bin/literary-clock-runtime.sh").is_file()
    assert validate_release(release) == "native-v1"

    (release / "bin/litclock-native").write_bytes(b"corrupt-native")
    with pytest.raises(DeploymentError, match="native runtime digest mismatch"):
        validate_release(release)


def test_production_release_is_minimal_and_autostart_is_persistent(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    mount = tmp_path / "mount"
    native = tmp_path / "litclock-native"
    mount.mkdir()
    native.write_bytes(b"native-test-binary")
    native.chmod(0o755)
    _bundle(source, version="production-v1")

    deploy_bundle(
        source,
        mount,
        project,
        require_kindle=False,
        native_binary=native,
        production=True,
    )
    release = mount / "literary-clock/runtime/releases/production-v1"
    metadata = dict(
        line.split("\t", 1) for line in (release / "release.meta").read_text().splitlines()
    )

    assert metadata["deployment_profile"] == "production"
    assert (release / "boot/autostart.sh").is_file()
    assert not (release / "boot/emergency.sh").exists()
    assert not (release / "bin/literary-clock-pilot.sh").exists()
    assert not (release / "bin/literary-clock-time-jump-test.sh").exists()
    assert not (release / "bin/literary-clock-power-study-start.sh").exists()
    assert (release / "bin/litclock-native").is_file()
    assert (release / "bin/literary-clock-runtime.sh").is_file()

    assert set_production_boot_hook(mount, enabled=True, require_kindle=False) == "enabled"
    hook = mount / "emergency.sh"
    assert hook.read_bytes() == (release / "boot/autostart.sh").read_bytes()
    assert "emergency.sh.used" not in hook.read_text()
    assert set_production_boot_hook(mount, enabled=True, require_kindle=False) == "enabled"
    assert set_production_boot_hook(mount, enabled=False, require_kindle=False) == "disabled"
    assert not hook.exists()


def test_boot_hook_is_explicit_reversible_and_uses_active_release(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    mount = tmp_path / "mount"
    mount.mkdir()
    _bundle(source, version="v1")
    deploy_bundle(source, mount, project, require_kindle=False)

    assert set_boot_hook(mount, enabled=True, require_kindle=False) == "enabled"
    hook = mount / "emergency.sh"
    assert hook.is_file()
    script = hook.read_text()
    assert "emergency.sh.used" in script
    assert "literary-clock-service-start-current.sh" in script

    assert set_boot_hook(mount, enabled=False, require_kindle=False) == "disabled"
    assert not hook.exists()
    assert (mount / "literary-clock/runtime/emergency.sh.disabled").is_file()
    assert set_boot_hook(mount, enabled=True, require_kindle=False) == "enabled"
    assert set_boot_hook(mount, enabled=False, require_kindle=False) == "disabled"
    assert not hook.exists()


def test_boot_hook_refuses_release_without_one_shot_kmc_hook(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    mount = tmp_path / "mount"
    mount.mkdir()
    _bundle(source, version="v1")
    deploy_bundle(source, mount, project, require_kindle=False)
    release = mount / "literary-clock/runtime/releases/v1"
    (release / "boot/emergency.sh").unlink()

    with pytest.raises(DeploymentError):
        set_boot_hook(mount, enabled=True, require_kindle=False)

    assert not (mount / "emergency.sh").exists()


def test_boot_hook_never_overwrites_an_unrelated_emergency_script(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    mount = tmp_path / "mount"
    mount.mkdir()
    _bundle(source, version="v1")
    deploy_bundle(source, mount, project, require_kindle=False)
    emergency = mount / "emergency.sh"
    emergency.write_text("#!/bin/sh\necho unrelated\n")

    with pytest.raises(DeploymentError, match="unrelated"):
        set_boot_hook(mount, enabled=True, require_kindle=False)

    assert emergency.read_text() == "#!/bin/sh\necho unrelated\n"


def test_deploy_refuses_partial_collision(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    mount = tmp_path / "mount"
    mount.mkdir()
    _bundle(source, version="v1")
    deploy_bundle(source, mount, project, require_kindle=False)

    with pytest.raises(DeploymentError, match="already exists"):
        deploy_bundle(source, mount, project, require_kindle=False)


def test_release_inventory_rejects_corrupt_runtime_index_and_png(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    mount = tmp_path / "mount"
    mount.mkdir()
    _bundle(source, version="v1")
    deploy_bundle(source, mount, project, require_kindle=False)
    release = mount / "literary-clock/runtime/releases/v1"

    runtime = release / "bin/literary-clock-runtime.sh"
    original_runtime = runtime.read_bytes()
    runtime.write_bytes(original_runtime + b"\n# corrupt\n")
    with pytest.raises(DeploymentError, match="checksum mismatch"):
        validate_release(release)
    runtime.write_bytes(original_runtime)

    index = release / "bundle/minutes.tsv"
    original_index = index.read_bytes()
    index.write_bytes(original_index + b"corrupt\n")
    with pytest.raises(DeploymentError):
        validate_release(release)
    index.write_bytes(original_index)

    frame = release / "bundle/frames/q1.png"
    frame.write_bytes(b"not a png")
    with pytest.raises(DeploymentError):
        validate_release(release)


def test_corrupt_staged_runtime_cannot_replace_active_release(tmp_path: Path, monkeypatch) -> None:
    project = Path(__file__).resolve().parents[1]
    first = tmp_path / "first"
    second = tmp_path / "second"
    mount = tmp_path / "mount"
    mount.mkdir()
    _bundle(first, version="v1")
    _bundle(second, version="v2")
    deploy_bundle(first, mount, project, require_kindle=False)
    original = deploy_module._write_release_checksums

    def corrupt_after_inventory(root: Path) -> None:
        original(root)
        runtime = root / "bin/literary-clock-runtime.sh"
        runtime.write_bytes(runtime.read_bytes() + b"\n# interrupted copy\n")

    monkeypatch.setattr(deploy_module, "_write_release_checksums", corrupt_after_inventory)

    with pytest.raises(DeploymentError, match="checksum mismatch"):
        deploy_bundle(second, mount, project, require_kindle=False)

    runtime_root = mount / "literary-clock/runtime"
    assert (runtime_root / "current-release").read_text().strip() == "v1"
    assert not (runtime_root / "releases/v2").exists()
    assert validate_release(runtime_root / "releases/v1") == "v1"


def test_device_startup_validator_checks_entire_release_inventory(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    mount = tmp_path / "mount"
    mount.mkdir()
    _bundle(source, version="v1")
    deploy_bundle(source, mount, project, require_kindle=False)
    release = mount / "literary-clock/runtime/releases/v1"
    validator = release / "bin/literary-clock-validate-release.sh"
    env = {**os.environ, "LITCLOCK_ALLOW_TEST_RELEASE_PATH": "1"}

    valid = subprocess.run(
        ["sh", str(validator), str(release)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert valid.returncode == 0, valid.stderr

    (release / "bundle/minutes.tsv").write_text("corrupt\n")
    corrupt = subprocess.run(
        ["sh", str(validator), str(release)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert corrupt.returncode == 12
    assert "checksum" in corrupt.stderr
