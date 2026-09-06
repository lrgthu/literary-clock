"""Safe USB deployment and rollback for versioned standalone Kindle bundles."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from litclock.runtime_bundle import read_manifest, validate_manifest


class DeploymentError(RuntimeError):
    pass


def _safe_version(value: str) -> str:
    if not value or value.startswith(".") or "/" in value or ".." in value:
        raise DeploymentError(f"unsafe bundle version: {value!r}")
    return value


def _runtime_root(mount: Path) -> Path:
    return mount / "literary-clock" / "runtime"


def _remove_appledouble(root: Path) -> None:
    """Remove rebuildable macOS metadata sidecars from the Kindle FAT volume."""
    for sidecar in sorted(root.rglob("._*"), reverse=True):
        if sidecar.is_file():
            sidecar.unlink()


def deploy_bundle(
    source: Path,
    mount: Path,
    project_root: Path,
    *,
    require_kindle: bool = True,
) -> str:
    """Copy, validate, and atomically activate a new bundle version."""
    source = source.resolve()
    mount = mount.resolve()
    if require_kindle and not (mount / "system").is_dir():
        raise DeploymentError(f"target does not look like USB-visible Kindle storage: {mount}")
    manifest = read_manifest(source)
    validate_manifest(
        manifest,
        root=source,
        require_all_minutes=manifest.metadata.get("complete") == "1",
        verify_checksums=True,
    )
    version = _safe_version(manifest.metadata.get("asset_set_version", source.name))
    runtime_root = _runtime_root(mount)
    bundles = runtime_root / "bundles"
    destination = bundles / version
    staging = bundles / f".staging-{version}"
    if destination.exists() or staging.exists():
        raise DeploymentError(f"bundle version already exists on device: {version}")
    source_bytes = sum(path.stat().st_size for path in source.rglob("*") if path.is_file())
    free_bytes = shutil.disk_usage(mount).free
    if free_bytes < source_bytes + 10 * 1024 * 1024:
        raise DeploymentError(
            f"insufficient Kindle space: need {source_bytes:,} bytes plus 10 MiB safety margin"
        )
    bundles.mkdir(parents=True, exist_ok=True)
    staging.mkdir()
    allowed = {
        "bundle.meta",
        "minutes.tsv",
        "quotes.tsv",
        "dates.tsv",
        "checksums.sha256",
        "build-summary.json",
        *(record.frame for record in manifest.quotes.values()),
        *(record.frame for record in manifest.dates.values()),
    }
    for relative in sorted(allowed):
        origin = source / relative
        if not origin.is_file():
            if relative == "build-summary.json":
                continue
            raise DeploymentError(f"required bundle file is absent: {relative}")
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)
    copied = read_manifest(staging)
    validate_manifest(
        copied,
        root=staging,
        require_all_minutes=copied.metadata.get("complete") == "1",
        verify_checksums=True,
    )
    _remove_appledouble(staging)
    os.replace(staging, destination)

    binary_dir = runtime_root / "bin"
    binary_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "literary-clock-display.sh",
        "literary-clock-runtime.sh",
        "literary-clock-pilot.sh",
        "literary-clock-time-jump-test.sh",
    ):
        source_script = project_root / "kindle" / "runtime" / name
        target = binary_dir / name
        shutil.copyfile(source_script, target)
        target.chmod(0o755)

    current = runtime_root / "current"
    previous = runtime_root / "previous"
    if current.is_file():
        old = current.read_text(encoding="utf-8").splitlines()[0]
        previous.write_text(old + "\n", encoding="utf-8")
    pending = runtime_root / "current.new"
    pending.write_text(version + "\n", encoding="utf-8")
    os.replace(pending, current)
    _remove_appledouble(runtime_root)
    if hasattr(os, "sync"):
        os.sync()
    return version


def rollback_bundle(mount: Path, *, require_kindle: bool = True) -> str:
    """Atomically reactivate the previous validated on-device bundle."""
    mount = mount.resolve()
    if require_kindle and not (mount / "system").is_dir():
        raise DeploymentError(f"target does not look like USB-visible Kindle storage: {mount}")
    runtime_root = _runtime_root(mount)
    previous = runtime_root / "previous"
    if not previous.is_file():
        raise DeploymentError("no previous bundle pointer is available")
    version = _safe_version(previous.read_text(encoding="utf-8").splitlines()[0])
    target = runtime_root / "bundles" / version
    manifest = read_manifest(target)
    validate_manifest(
        manifest,
        root=target,
        require_all_minutes=manifest.metadata.get("complete") == "1",
        verify_checksums=True,
    )
    current = runtime_root / "current"
    active = current.read_text(encoding="utf-8").splitlines()[0] if current.is_file() else ""
    pending = runtime_root / "current.new"
    pending.write_text(version + "\n", encoding="utf-8")
    os.replace(pending, current)
    if active:
        previous.write_text(active + "\n", encoding="utf-8")
    if hasattr(os, "sync"):
        os.sync()
    return version
