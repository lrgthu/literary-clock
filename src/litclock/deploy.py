"""Safe USB deployment and rollback for atomic Kindle runtime releases."""

from __future__ import annotations

import os
import shutil
from pathlib import Path, PurePosixPath

from litclock.runtime_bundle import (
    RELEASE_FORMAT_VERSION,
    RUNTIME_VERSION,
    read_manifest,
    sha256_file,
    validate_manifest,
)

RUNTIME_SOURCES = {
    "literary-clock-display.sh": Path("kindle/runtime/literary-clock-display.sh"),
    "literary-clock-runtime.sh": Path("kindle/runtime/literary-clock-runtime.sh"),
    "literary-clock-pilot.sh": Path("kindle/runtime/literary-clock-pilot.sh"),
    "literary-clock-time-jump-test.sh": Path("kindle/runtime/literary-clock-time-jump-test.sh"),
    "literary-clock-power.sh": Path("kindle/literary-clock-power.sh"),
    "literary-clock-power-study-start.sh": Path(
        "kindle/runtime/literary-clock-power-study-start.sh"
    ),
    "literary-clock-power-study-stop.sh": Path("kindle/runtime/literary-clock-power-study-stop.sh"),
    "literary-clock-validate-release.sh": Path("kindle/runtime/literary-clock-validate-release.sh"),
    "literary-clock-service-start.sh": Path("kindle/runtime/literary-clock-service-start.sh"),
    "literary-clock-service-stop.sh": Path("kindle/runtime/literary-clock-service-stop.sh"),
}
CORE_RUNTIME_SOURCES = {
    "literary-clock-display.sh",
    "literary-clock-runtime.sh",
    "literary-clock-pilot.sh",
    "literary-clock-time-jump-test.sh",
    "literary-clock-power.sh",
    "literary-clock-power-study-start.sh",
    "literary-clock-power-study-stop.sh",
}
STABLE_LAUNCHERS = {
    "literary-clock-launch-current.sh": Path("kindle/runtime/literary-clock-launch-current.sh"),
    "literary-clock-service-start-current.sh": Path(
        "kindle/runtime/literary-clock-service-start-current.sh"
    ),
    "literary-clock-service-stop-current.sh": Path(
        "kindle/runtime/literary-clock-service-stop-current.sh"
    ),
}
BOOT_HOOK_SOURCE = Path("kindle/runtime/literary-clock-boot-once.sh")
NATIVE_RUNTIME_NAME = "litclock-native"
NATIVE_RUNTIME_VERSION = "0.1.0-scaffold"
NATIVE_ARCHITECTURE = "armv7-eabi5-hard-float-static"


class DeploymentError(RuntimeError):
    pass


def _safe_version(value: str) -> str:
    if not value or value.startswith(".") or "/" in value or ".." in value:
        raise DeploymentError(f"unsafe release version: {value!r}")
    return value


def _safe_relative(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def _runtime_root(mount: Path) -> Path:
    return mount / "literary-clock" / "runtime"


def _remove_appledouble(root: Path) -> None:
    """Remove rebuildable macOS metadata sidecars from the Kindle FAT volume."""
    for sidecar in sorted(root.rglob("._*"), reverse=True):
        if sidecar.is_file():
            sidecar.unlink()


def _bundle_allowlist(source: Path) -> set[str]:
    manifest = read_manifest(source)
    return {
        "bundle.meta",
        "minutes.tsv",
        "quotes.tsv",
        "dates.tsv",
        "checksums.sha256",
        *(record.frame for record in manifest.quotes.values()),
        *(record.frame for record in manifest.dates.values()),
        *({"build-summary.json"} if (source / "build-summary.json").is_file() else set()),
    }


def _write_release_metadata(
    root: Path,
    version: str,
    corpus_fingerprint: str,
    native_binary: Path | None,
) -> None:
    native_metadata: tuple[str, ...] = ()
    if native_binary is not None:
        native_metadata = (
            "runtime_engine\tnative\n",
            f"native_runtime_version\t{NATIVE_RUNTIME_VERSION}\n",
            f"native_binary_sha256\t{sha256_file(native_binary)}\n",
            f"architecture\t{NATIVE_ARCHITECTURE}\n",
        )
    (root / "release.meta").write_text(
        "".join(
            (
                f"release_format_version\t{RELEASE_FORMAT_VERSION}\n",
                f"runtime_version\t{RUNTIME_VERSION}\n",
                f"release_version\t{version}\n",
                "renderer_preset\tpw4-v1\n",
                f"corpus_fingerprint\t{corpus_fingerprint}\n",
                *(native_metadata or ("runtime_engine\tshell\n",)),
            )
        ),
        encoding="utf-8",
    )


def _release_files(root: Path) -> dict[str, Path]:
    return {
        path.relative_to(root).as_posix(): path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.relative_to(root).as_posix() != "checksums.sha256"
        and not path.name.startswith("._")
    }


def _write_release_checksums(root: Path) -> None:
    rows = _release_files(root)
    (root / "checksums.sha256").write_text(
        "".join(f"{sha256_file(path)}  {relative}\n" for relative, path in rows.items()),
        encoding="utf-8",
    )


def _read_metadata(path: Path) -> dict[str, str]:
    try:
        return dict(
            line.split("\t", 1) for line in path.read_text(encoding="utf-8").splitlines() if line
        )
    except (OSError, ValueError) as error:
        raise DeploymentError(f"invalid release metadata: {path}") from error


def validate_release(root: Path, *, verify_checksums: bool = True) -> str:
    """Validate code, manifest, assets, compatibility metadata, and full inventory."""
    metadata = _read_metadata(root / "release.meta")
    if metadata.get("release_format_version") != str(RELEASE_FORMAT_VERSION):
        raise DeploymentError("incompatible release_format_version")
    if metadata.get("runtime_version") != str(RUNTIME_VERSION):
        raise DeploymentError("incompatible runtime_version")
    version = _safe_version(metadata.get("release_version", ""))
    bundle = root / "bundle"
    try:
        manifest = read_manifest(bundle)
        validate_manifest(
            manifest,
            root=bundle,
            require_all_minutes=manifest.metadata.get("complete") == "1",
            verify_checksums=True,
        )
    except (OSError, ValueError) as error:
        raise DeploymentError(f"invalid bundled manifest/assets: {error}") from error
    for key, expected in (
        ("release_format_version", str(RELEASE_FORMAT_VERSION)),
        ("runtime_version", str(RUNTIME_VERSION)),
        ("renderer_preset", "pw4-v1"),
        ("corpus_fingerprint", manifest.metadata.get("corpus_fingerprint", "")),
    ):
        if metadata.get(key) != expected or manifest.metadata.get(key) != expected:
            raise DeploymentError(f"release/bundle compatibility mismatch: {key}")
    # Keep Phase 4B.1 releases rollback-valid. Service helpers are required by
    # the boot-hook installer, while these core files define runtime v2.
    for name in CORE_RUNTIME_SOURCES:
        script = root / "bin" / name
        if not script.is_file() or script.stat().st_size <= 0:
            raise DeploymentError(f"release runtime is missing: {name}")
    engine = metadata.get("runtime_engine", "shell")
    if engine not in {"native", "shell"}:
        raise DeploymentError("invalid runtime_engine")
    if engine == "native":
        native = root / "bin" / NATIVE_RUNTIME_NAME
        if not native.is_file() or native.stat().st_size <= 0:
            raise DeploymentError("native release runtime is missing")
        if metadata.get("native_runtime_version") != NATIVE_RUNTIME_VERSION:
            raise DeploymentError("native runtime version mismatch")
        if metadata.get("architecture") != NATIVE_ARCHITECTURE:
            raise DeploymentError("native runtime architecture mismatch")
        if metadata.get("native_binary_sha256") != sha256_file(native):
            raise DeploymentError("native runtime digest mismatch")
    checksums = root / "checksums.sha256"
    if not checksums.is_file():
        raise DeploymentError("release checksum inventory is missing")
    inventory: dict[str, str] = {}
    for line in checksums.read_text(encoding="utf-8").splitlines():
        fields = line.split("  ", 1)
        if len(fields) != 2 or len(fields[0]) != 64 or not _safe_relative(fields[1]):
            raise DeploymentError("malformed release checksum inventory")
        if fields[1] in inventory:
            raise DeploymentError("duplicate release checksum path")
        inventory[fields[1]] = fields[0]
    actual = _release_files(root)
    if set(inventory) != set(actual):
        raise DeploymentError("release checksum inventory does not match release files")
    if verify_checksums:
        for relative, path in actual.items():
            if sha256_file(path) != inventory[relative]:
                raise DeploymentError(f"release checksum mismatch: {relative}")
    return version


def _copy_bundle(source: Path, destination: Path) -> None:
    for relative in sorted(_bundle_allowlist(source)):
        origin = source / relative
        if not origin.is_file():
            raise DeploymentError(f"required bundle file is absent: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)


def _install_launchers(runtime_root: Path, project_root: Path) -> None:
    for name, relative in STABLE_LAUNCHERS.items():
        source = project_root / relative
        target = runtime_root / name
        if target.is_file():
            same_script = (
                target.read_text(encoding="utf-8").rstrip()
                == source.read_text(encoding="utf-8").rstrip()
            )
            if not same_script:
                raise DeploymentError(f"installed stable launcher differs: {name}")
            continue
        pending = runtime_root / f"{name}.new"
        shutil.copyfile(source, pending)
        pending.chmod(0o755)
        os.replace(pending, target)


def deploy_bundle(
    source: Path,
    mount: Path,
    project_root: Path,
    *,
    require_kindle: bool = True,
    release_version: str | None = None,
    native_binary: Path | None = None,
) -> str:
    """Stage, fully validate, and atomically activate matching code plus assets."""
    source = source.resolve()
    mount = mount.resolve()
    if native_binary is not None:
        native_binary = native_binary.resolve()
        if not native_binary.is_file() or native_binary.stat().st_size <= 0:
            raise DeploymentError(f"native runtime binary is missing: {native_binary}")
    if require_kindle and not (mount / "system").is_dir():
        raise DeploymentError(f"target does not look like USB-visible Kindle storage: {mount}")
    manifest = read_manifest(source)
    validate_manifest(
        manifest,
        root=source,
        require_all_minutes=manifest.metadata.get("complete") == "1",
        verify_checksums=True,
    )
    version = _safe_version(
        release_version or manifest.metadata.get("asset_set_version", source.name)
    )
    runtime_root = _runtime_root(mount)
    releases = runtime_root / "releases"
    destination = releases / version
    staging = releases / f".staging-{version}"
    if destination.exists() or staging.exists():
        raise DeploymentError(f"release version already exists on device: {version}")
    source_bytes = sum(path.stat().st_size for path in source.rglob("*") if path.is_file())
    free_bytes = shutil.disk_usage(mount).free
    if free_bytes < source_bytes + 10 * 1024 * 1024:
        raise DeploymentError(
            f"insufficient Kindle space: need {source_bytes:,} bytes plus 10 MiB safety margin"
        )
    releases.mkdir(parents=True, exist_ok=True)
    staging.mkdir()
    _copy_bundle(source, staging / "bundle")
    binary_dir = staging / "bin"
    binary_dir.mkdir()
    for name, relative in RUNTIME_SOURCES.items():
        source_script = project_root / relative
        target = binary_dir / name
        shutil.copyfile(source_script, target)
        target.chmod(0o755)
    staged_native: Path | None = None
    if native_binary is not None:
        staged_native = binary_dir / NATIVE_RUNTIME_NAME
        shutil.copyfile(native_binary, staged_native)
        staged_native.chmod(0o755)
    boot_dir = staging / "boot"
    boot_dir.mkdir()
    shutil.copyfile(project_root / BOOT_HOOK_SOURCE, boot_dir / "emergency.sh")
    (boot_dir / "emergency.sh").chmod(0o755)
    _write_release_metadata(
        staging,
        version,
        manifest.metadata.get("corpus_fingerprint", ""),
        staged_native,
    )
    _remove_appledouble(staging)
    _write_release_checksums(staging)
    validate_release(staging)
    os.replace(staging, destination)

    _install_launchers(runtime_root, project_root)
    current = runtime_root / "current-release"
    previous = runtime_root / "previous-release"
    if current.is_file():
        old = _safe_version(current.read_text(encoding="utf-8").splitlines()[0])
        previous.write_text(old + "\n", encoding="utf-8")
    pending = runtime_root / "current-release.new"
    pending.write_text(version + "\n", encoding="utf-8")
    os.replace(pending, current)
    _remove_appledouble(runtime_root)
    if hasattr(os, "sync"):
        os.sync()
    return version


def rollback_bundle(mount: Path, *, require_kindle: bool = True) -> str:
    """Atomically reactivate the previous fully validated code+asset release."""
    mount = mount.resolve()
    if require_kindle and not (mount / "system").is_dir():
        raise DeploymentError(f"target does not look like USB-visible Kindle storage: {mount}")
    runtime_root = _runtime_root(mount)
    previous = runtime_root / "previous-release"
    if not previous.is_file():
        raise DeploymentError("no previous release pointer is available")
    version = _safe_version(previous.read_text(encoding="utf-8").splitlines()[0])
    validate_release(runtime_root / "releases" / version)
    current = runtime_root / "current-release"
    active = (
        _safe_version(current.read_text(encoding="utf-8").splitlines()[0])
        if current.is_file()
        else ""
    )
    pending = runtime_root / "current-release.new"
    pending.write_text(version + "\n", encoding="utf-8")
    os.replace(pending, current)
    if active:
        previous.write_text(active + "\n", encoding="utf-8")
    if hasattr(os, "sync"):
        os.sync()
    return version


def set_boot_hook(
    mount: Path,
    *,
    enabled: bool,
    require_kindle: bool = True,
) -> str:
    """Enable/disable the one-shot KMC framework_ready hook on user storage."""
    mount = mount.resolve()
    if require_kindle and not (mount / "system").is_dir():
        raise DeploymentError(f"target does not look like USB-visible Kindle storage: {mount}")
    runtime_root = _runtime_root(mount)
    target = mount / "emergency.sh"
    disabled = runtime_root / "emergency.sh.disabled"
    if enabled:
        current = runtime_root / "current-release"
        if not current.is_file():
            raise DeploymentError("current release pointer is missing")
        version = _safe_version(current.read_text(encoding="utf-8").splitlines()[0])
        release = runtime_root / "releases" / version
        validate_release(release)
        source = release / "boot" / "emergency.sh"
        if not source.is_file():
            raise DeploymentError("active release one-shot boot hook is missing")
        if target.exists():
            try:
                matches = target.is_file() and sha256_file(target) == sha256_file(source)
            except OSError as error:
                raise DeploymentError("cannot inspect existing emergency hook") from error
            if not matches:
                raise DeploymentError("an unrelated /mnt/us/emergency.sh already exists")
            return "enabled"
        staging = mount / ".literary-clock-emergency.sh.staging"
        if staging.exists():
            staging.unlink()
        shutil.copyfile(source, staging)
        staging.chmod(0o755)
        os.replace(staging, target)
        if hasattr(os, "sync"):
            os.sync()
        return "enabled"

    if not target.exists():
        return "disabled"
    disabled.parent.mkdir(parents=True, exist_ok=True)
    if disabled.exists():
        try:
            matches = disabled.is_file() and sha256_file(target) == sha256_file(disabled)
        except OSError as error:
            raise DeploymentError("cannot compare existing disabled boot hook") from error
        if not matches:
            raise DeploymentError(f"disabled boot-hook archive already exists: {disabled}")
        target.unlink()
    else:
        os.replace(target, disabled)
    if hasattr(os, "sync"):
        os.sync()
    return "disabled"
