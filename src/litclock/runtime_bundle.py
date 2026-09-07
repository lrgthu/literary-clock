"""Compact line-oriented bundle manifest shared by host tools and shell runtime."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

BUNDLE_FORMAT_VERSION = 1
RELEASE_FORMAT_VERSION = 1
RUNTIME_VERSION = 2
TRANSPORT_WIDTH = 1072
TRANSPORT_HEIGHT = 1448


class BundleValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class QuoteAssetRecord:
    quote_id: int
    frame: str
    book_id: str
    author_id: str
    sha256: str
    byte_size: int
    width: int = TRANSPORT_WIDTH
    height: int = TRANSPORT_HEIGHT


@dataclass(frozen=True, slots=True)
class DateAssetRecord:
    key: str
    frame: str
    x: int
    y: int
    sha256: str
    byte_size: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class BundleManifest:
    metadata: dict[str, str]
    minutes: dict[int, tuple[int, ...]]
    quotes: dict[int, QuoteAssetRecord]
    dates: dict[str, DateAssetRecord]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_identity(value: str) -> str:
    normalized = " ".join(value.casefold().split())
    if not normalized:
        return "-"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _safe_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value.startswith("/"):
        raise BundleValidationError(f"bundle path is not relative and contained: {value!r}")


def validate_manifest(
    manifest: BundleManifest,
    *,
    root: Path | None = None,
    require_all_minutes: bool = True,
    verify_checksums: bool = False,
) -> None:
    if manifest.metadata.get("format_version") != str(BUNDLE_FORMAT_VERSION):
        raise BundleValidationError("unsupported or missing bundle format_version")
    expected = set(range(1440))
    actual = set(manifest.minutes)
    if require_all_minutes and actual != expected:
        missing = sorted(expected - actual)
        raise BundleValidationError(f"manifest is missing {len(missing)} minute rows")
    if any(not 0 <= minute <= 1439 for minute in actual):
        raise BundleValidationError("manifest contains a minute outside 0..1439")
    referenced = {quote_id for ids in manifest.minutes.values() for quote_id in ids}
    missing_quotes = sorted(referenced - set(manifest.quotes))
    if missing_quotes:
        raise BundleValidationError(
            f"manifest minute rows reference {len(missing_quotes)} missing quote assets"
        )
    if any(not ids for ids in manifest.minutes.values()):
        raise BundleValidationError("manifest contains an empty minute pool")
    if set(manifest.quotes) != referenced:
        raise BundleValidationError("bundle contains unreferenced quote assets")
    frame_paths: set[str] = set()
    records: list[QuoteAssetRecord | DateAssetRecord] = [
        *manifest.quotes.values(),
        *manifest.dates.values(),
    ]
    for record in records:
        _safe_relative_path(record.frame)
        if record.frame in frame_paths:
            raise BundleValidationError(f"asset path is reused: {record.frame}")
        frame_paths.add(record.frame)
        if record.byte_size <= 0 or len(record.sha256) != 64:
            raise BundleValidationError(f"invalid integrity metadata for {record.frame}")
        if root is not None:
            asset = root / record.frame
            if not asset.is_file() or asset.stat().st_size != record.byte_size:
                raise BundleValidationError(f"missing or wrong-sized asset: {record.frame}")
            from PIL import Image

            try:
                with Image.open(asset) as image:
                    image.load()
                    if image.size != (record.width, record.height):
                        raise BundleValidationError(
                            f"wrong asset dimensions for {record.frame}: {image.size}"
                        )
                    if image.mode != "1":
                        raise BundleValidationError(
                            f"asset is not crisp 1-bit PNG data: {record.frame} ({image.mode})"
                        )
            except OSError as error:
                raise BundleValidationError(f"invalid image asset: {record.frame}") from error
            if verify_checksums and sha256_file(asset) != record.sha256:
                raise BundleValidationError(f"checksum mismatch: {record.frame}")

    if root is not None:
        for name in ("bundle.meta", "minutes.tsv", "quotes.tsv", "dates.tsv"):
            if not (root / name).is_file():
                raise BundleValidationError(f"missing manifest component: {name}")


def write_manifest(root: Path, manifest: BundleManifest) -> None:
    root.mkdir(parents=True, exist_ok=True)
    metadata = "".join(f"{key}\t{value}\n" for key, value in sorted(manifest.metadata.items()))
    minutes = "".join(
        f"{minute:04d}\t{','.join(str(value) for value in ids)}\n"
        for minute, ids in sorted(manifest.minutes.items())
    )
    quotes = "".join(
        "\t".join(
            (
                str(record.quote_id),
                record.frame,
                record.book_id,
                record.author_id,
                record.sha256,
                str(record.byte_size),
                str(record.width),
                str(record.height),
            )
        )
        + "\n"
        for record in sorted(manifest.quotes.values(), key=lambda item: item.quote_id)
    )
    dates = "".join(
        "\t".join(
            (
                record.key,
                record.frame,
                str(record.x),
                str(record.y),
                record.sha256,
                str(record.byte_size),
                str(record.width),
                str(record.height),
            )
        )
        + "\n"
        for record in sorted(manifest.dates.values(), key=lambda item: item.key)
    )
    for name, content in (
        ("bundle.meta", metadata),
        ("minutes.tsv", minutes),
        ("quotes.tsv", quotes),
        ("dates.tsv", dates),
    ):
        (root / name).write_text(content, encoding="utf-8")


def read_manifest(root: Path) -> BundleManifest:
    metadata = dict(
        line.rstrip("\n").split("\t", 1)
        for line in (root / "bundle.meta").read_text(encoding="utf-8").splitlines()
        if line
    )
    minutes: dict[int, tuple[int, ...]] = {}
    for line in (root / "minutes.tsv").read_text(encoding="utf-8").splitlines():
        minute, values = line.split("\t", 1)
        minutes[int(minute)] = tuple(int(value) for value in values.split(",") if value)
    quotes: dict[int, QuoteAssetRecord] = {}
    for line in (root / "quotes.tsv").read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        record = QuoteAssetRecord(
            int(fields[0]),
            fields[1],
            fields[2],
            fields[3],
            fields[4],
            int(fields[5]),
            int(fields[6]),
            int(fields[7]),
        )
        quotes[record.quote_id] = record
    dates: dict[str, DateAssetRecord] = {}
    for line in (root / "dates.tsv").read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        record = DateAssetRecord(
            fields[0],
            fields[1],
            int(fields[2]),
            int(fields[3]),
            fields[4],
            int(fields[5]),
            int(fields[6]),
            int(fields[7]),
        )
        dates[record.key] = record
    return BundleManifest(metadata, minutes, quotes, dates)


def write_checksums(root: Path, manifest: BundleManifest) -> Path:
    """Write a deployment-time checksum inventory for immutable assets and indexes."""
    asset_rows = [
        (record.sha256, record.frame)
        for record in (*manifest.quotes.values(), *manifest.dates.values())
    ]
    index_rows = [
        (sha256_file(root / name), name)
        for name in ("bundle.meta", "minutes.tsv", "quotes.tsv", "dates.tsv")
    ]
    output = root / "checksums.sha256"
    output.write_text(
        "".join(f"{digest}  {path}\n" for digest, path in sorted((*asset_rows, *index_rows))),
        encoding="utf-8",
    )
    return output
