"""Build deterministic, dependency-free PW4 runtime asset bundles."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import statistics
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from litclock.db import row_to_quote
from litclock.models import Quote
from litclock.render.date_label import format_short_date
from litclock.render.models import RenderabilityStatus
from litclock.render.pillow_renderer import BACKGROUND_WHITE, ONE_BIT_THRESHOLD, PillowRenderer
from litclock.render.production import PW4_V1_RENDER_CONFIG, resolve_production_fonts
from litclock.render.suitability import is_renderable_for_device
from litclock.runtime_bundle import (
    BUNDLE_FORMAT_VERSION,
    RELEASE_FORMAT_VERSION,
    RUNTIME_VERSION,
    BundleManifest,
    DateAssetRecord,
    QuoteAssetRecord,
    sha256_file,
    stable_identity,
    validate_manifest,
    write_checksums,
    write_manifest,
)

SAFE_STATUSES = {
    RenderabilityStatus.DISPLAY_SAFE_FULL,
    RenderabilityStatus.DISPLAY_SAFE_EXCERPT,
}
REFERENCE_DATE = date(2026, 9, 5)
DATE_FONT_SIZE = 32
_WEEKDAYS_SUNDAY_FIRST = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


@dataclass(frozen=True, slots=True)
class BundleBuildSummary:
    output: str
    complete: bool
    minute_count: int
    quote_assets: int
    relationships: int
    date_assets: int
    rendered_assets: int
    reused_assets: int
    median_sample_bytes: int
    p90_sample_bytes: int
    maximum_sample_bytes: int
    projected_frame_bytes: int
    projected_date_bytes: int
    projected_manifest_bytes: int
    projected_fat_overhead_bytes: int
    projected_total_bytes: int
    actual_bundle_bytes: int
    corpus_fingerprint: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def minute_window(start: int, count: int) -> tuple[int, ...]:
    if not 0 <= start <= 1439:
        raise ValueError("pilot start minute must be in 0..1439")
    if not 1 <= count <= 1440:
        raise ValueError("pilot minute count must be in 1..1440")
    return tuple((start + offset) % 1440 for offset in range(count))


def _load_pools(
    connection: sqlite3.Connection, minutes: tuple[int, ...]
) -> tuple[dict[int, list[int]], dict[int, Quote]]:
    minute_set = set(minutes)
    pools: dict[int, list[int]] = {minute: [] for minute in minutes}
    quotes: dict[int, Quote] = {}
    rows = connection.execute(
        """
        SELECT pool.minute_of_day AS display_minute_of_day,
               printf('%02d:%02d', pool.minute_of_day / 60,
                      pool.minute_of_day % 60) AS display_time_24h,
               q.*
        FROM quote_minute_pool AS pool
        JOIN quotes AS q ON q.id = pool.quote_id
        WHERE q.quality_status IN ('VERIFIED_EXACT', 'VERIFIED_NORMALIZED')
          AND q.highlight_start IS NOT NULL
          AND q.highlight_end IS NOT NULL
        ORDER BY pool.minute_of_day, q.id
        """
    )
    for row in rows:
        minute = int(row["display_minute_of_day"])
        if minute not in minute_set:
            continue
        quote_id = int(row["id"])
        pools[minute].append(quote_id)
        quotes.setdefault(quote_id, row_to_quote(row))
    return pools, quotes


def _corpus_fingerprint(pools: dict[int, list[int]], quotes: dict[int, Quote]) -> str:
    digest = hashlib.sha256()
    for minute, quote_ids in sorted(pools.items()):
        digest.update(f"m{minute:04d}:{','.join(map(str, quote_ids))}\n".encode())
    for quote_id, quote in sorted(quotes.items()):
        digest.update(f"q{quote_id}:{quote.normalized_quote_hash}\n".encode())
    return digest.hexdigest()


def _date_key(value: date) -> str:
    # strftime %w is Sunday=0, unlike date.weekday().
    weekday = (value.weekday() + 1) % 7
    return f"{weekday}-{value.month:02d}-{value.day:02d}"


def _possible_date_labels() -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    for weekday, weekday_text in enumerate(_WEEKDAYS_SUNDAY_FIRST):
        for month, month_text in enumerate(_MONTHS, 1):
            maximum = 29 if month == 2 else 30 if month in {4, 6, 9, 11} else 31
            for day in range(1, maximum + 1):
                rows.append(
                    (f"{weekday}-{month:02d}-{day:02d}", f"{weekday_text}, {month_text} {day}")
                )
    return tuple(rows)


def _generated_at() -> str:
    """Return a reproducible bundle timestamp when SOURCE_DATE_EPOCH is set."""
    source_epoch = os.environ.get("SOURCE_DATE_EPOCH")
    generated = (
        datetime.fromtimestamp(int(source_epoch), UTC) if source_epoch else datetime.now(UTC)
    )
    return generated.replace(microsecond=0).isoformat()


def _blank_date_region(
    image: Image.Image, font_path: Path, profile_width: int, profile_height: int
) -> None:
    font = ImageFont.truetype(str(font_path), DATE_FONT_SIZE)
    longest = max(
        (f"{day}, {month} 30" for day in _WEEKDAYS_SUNDAY_FIRST for month in _MONTHS), key=len
    )
    left = round(profile_width * PW4_V1_RENDER_CONFIG.profile.date_inset_x) - 5
    top = round(profile_height * PW4_V1_RENDER_CONFIG.profile.date_inset_y) - 5
    bbox = ImageDraw.Draw(image).textbbox((left, top), longest, font=font, anchor="lt")
    ImageDraw.Draw(image).rectangle((left, top, bbox[2] + 5, bbox[3] + 5), fill=BACKGROUND_WHITE)


def _render_date_overlay(root: Path, key: str, text: str, font_path: Path) -> DateAssetRecord:
    profile = PW4_V1_RENDER_CONFIG.profile
    font = ImageFont.truetype(str(font_path), DATE_FONT_SIZE)
    x = round(profile.width * profile.date_inset_x)
    y = round(profile.height * profile.date_inset_y)
    probe = Image.new("L", (1, 1), BACKGROUND_WHITE)
    bounds = ImageDraw.Draw(probe).textbbox((0, 0), text, font=font, anchor="lt")
    padding = 3
    native = Image.new(
        "L",
        (bounds[2] - bounds[0] + 2 * padding, bounds[3] - bounds[1] + 2 * padding),
        BACKGROUND_WHITE,
    )
    ImageDraw.Draw(native).text(
        (padding - bounds[0], padding - bounds[1]), text, font=font, fill=144
    )
    thresholded = native.point(lambda pixel: BACKGROUND_WHITE if pixel >= ONE_BIT_THRESHOLD else 0)
    transport = thresholded.convert("1").transpose(Image.Transpose.ROTATE_90)
    native_left = x + bounds[0] - padding
    native_top = y + bounds[1] - padding
    transport_x = native_top
    transport_y = profile.width - (native_left + native.width)
    relative = f"dates/{key}.png"
    output = root / relative
    output.parent.mkdir(parents=True, exist_ok=True)
    transport.save(output, format="PNG", optimize=True)
    return DateAssetRecord(
        key,
        relative,
        transport_x,
        transport_y,
        sha256_file(output),
        output.stat().st_size,
        transport.width,
        transport.height,
    )


def _render_quote_asset(
    root: Path,
    quote: Quote,
    prepared,
    renderer: PillowRenderer,
) -> QuoteAssetRecord:
    relative = f"frames/q{quote.id:07d}.png"
    output = root / relative
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.is_file():
        frame = renderer.render(
            prepared,
            PW4_V1_RENDER_CONFIG.profile,
            mode=PW4_V1_RENDER_CONFIG.mode,
            dither=PW4_V1_RENDER_CONFIG.dither,
            attribution_style=PW4_V1_RENDER_CONFIG.attribution_style,
            time_emphasis=PW4_V1_RENDER_CONFIG.time_emphasis,
            show_date=True,
            display_date=REFERENCE_DATE,
        )
        _blank_date_region(
            frame.image,
            renderer.font.regular,
            PW4_V1_RENDER_CONFIG.profile.width,
            PW4_V1_RENDER_CONFIG.profile.height,
        )
        transport = frame.image.transpose(Image.Transpose.ROTATE_90)
        transport.save(output, format="PNG", optimize=True)
    return QuoteAssetRecord(
        quote.id,
        relative,
        stable_identity(quote.title),
        stable_identity(quote.author),
        sha256_file(output),
        output.stat().st_size,
    )


def _percentile_90(values: list[int]) -> int:
    if not values:
        return 0
    return (
        math.ceil(statistics.quantiles(values, n=10, method="inclusive")[8])
        if len(values) > 1
        else values[0]
    )


def _even_sample(values: list[int], count: int) -> list[int]:
    if count >= len(values):
        return values.copy()
    if count <= 1:
        return values[:1]
    return [values[round(index * (len(values) - 1) / (count - 1))] for index in range(count)]


def build_pw4_bundle(
    connection: sqlite3.Connection,
    output: Path,
    *,
    minutes: tuple[int, ...] | None = None,
    estimate_only: bool = False,
    sample_size: int = 100,
    incremental: bool = True,
    pilot_date: date | None = None,
) -> BundleBuildSummary:
    """Build or estimate the frozen PW4 bundle without embedding font files."""
    selected_minutes = minutes or tuple(range(1440))
    if len(set(selected_minutes)) != len(selected_minutes):
        raise ValueError("minute selection contains duplicates")
    pools, quotes = _load_pools(connection, selected_minutes)
    if any(not pool for pool in pools.values()):
        empty = [minute for minute, pool in pools.items() if not pool]
        raise ValueError(f"source corpus contains empty minute pools: {empty[:10]}")
    body_font, time_font = resolve_production_fonts()
    renderer = PillowRenderer(body_font, time_font=time_font)
    safe: dict[int, object] = {}
    for quote_id, quote in quotes.items():
        result = is_renderable_for_device(
            quote,
            PW4_V1_RENDER_CONFIG.profile,
            body_font,
            time_emphasis=PW4_V1_RENDER_CONFIG.time_emphasis,
            time_font=time_font,
            show_date=True,
            display_date=REFERENCE_DATE,
        )
        if result.status in SAFE_STATUSES and result.quote is not None:
            safe[quote_id] = result.quote
    safe_pools = {
        minute: [quote_id for quote_id in quote_ids if quote_id in safe]
        for minute, quote_ids in pools.items()
    }
    if any(not pool for pool in safe_pools.values()):
        empty = [minute for minute, pool in safe_pools.items() if not pool]
        raise ValueError(f"renderability filtering empties minute pools: {empty[:10]}")
    referenced = sorted({quote_id for ids in safe_pools.values() for quote_id in ids})
    fingerprint = _corpus_fingerprint(safe_pools, quotes)

    sample_ids = _even_sample(referenced, max(1, min(sample_size, len(referenced))))
    sample_directory = tempfile.TemporaryDirectory(prefix="litclock-bundle-estimate-")
    sample_root = Path(sample_directory.name)
    sample_sizes: list[int] = []
    for quote_id in sample_ids:
        record = _render_quote_asset(
            sample_root,
            quotes[quote_id],
            safe[quote_id],
            renderer,
        )
        sample_sizes.append(record.byte_size)
    median_bytes = round(statistics.median(sample_sizes)) if sample_sizes else 0
    p90_bytes = _percentile_90(sample_sizes)
    max_bytes = max(sample_sizes, default=0)
    projected = median_bytes * len(referenced)
    possible_dates = _possible_date_labels()
    date_sample_root = sample_root / "date-projection"
    projected_date_records = [
        _render_date_overlay(date_sample_root, key, text, body_font.regular)
        for key, text in possible_dates
    ]
    projected_date_bytes = sum(record.byte_size for record in projected_date_records)
    projected_manifest_bytes = (
        sum(len(ids) * 8 + 8 for ids in safe_pools.values())
        + len(referenced) * 140
        + len(possible_dates) * 140
        + 2048
    )
    projected_file_count = len(referenced) + len(possible_dates) + 6
    projected_fat_overhead = projected_file_count * 32768
    projected_total = (
        projected + projected_date_bytes + projected_manifest_bytes + projected_fat_overhead
    )
    if estimate_only:
        summary = BundleBuildSummary(
            str(output),
            len(selected_minutes) == 1440,
            len(selected_minutes),
            len(referenced),
            sum(map(len, safe_pools.values())),
            len(possible_dates),
            len(sample_ids),
            0,
            median_bytes,
            p90_bytes,
            max_bytes,
            projected,
            projected_date_bytes,
            projected_manifest_bytes,
            projected_fat_overhead,
            projected_total,
            0,
            fingerprint,
        )
        output.mkdir(parents=True, exist_ok=True)
        (output / "estimate.json").write_text(
            json.dumps(summary.as_dict(), indent=2) + "\n", encoding="utf-8"
        )
        sample_directory.cleanup()
        return summary

    if not incremental and output.exists():
        raise ValueError("refusing to replace an existing bundle without a new output directory")
    quote_records: dict[int, QuoteAssetRecord] = {}
    rendered = 0
    reused = 0
    for quote_id in referenced:
        expected = output / f"frames/q{quote_id:07d}.png"
        existed = expected.is_file()
        record = _render_quote_asset(output, quotes[quote_id], safe[quote_id], renderer)
        quote_records[quote_id] = record
        reused += int(existed)
        rendered += int(not existed)

    date_rows = (
        ((_date_key(pilot_date), format_short_date(pilot_date)),)
        if pilot_date is not None
        else _possible_date_labels()
    )
    date_records = {
        key: _render_date_overlay(output, key, text, body_font.regular) for key, text in date_rows
    }
    metadata = {
        "format_version": str(BUNDLE_FORMAT_VERSION),
        "release_format_version": str(RELEASE_FORMAT_VERSION),
        "runtime_version": str(RUNTIME_VERSION),
        "asset_set_version": output.name,
        "complete": "1" if len(selected_minutes) == 1440 else "0",
        "corpus_fingerprint": fingerprint,
        "generated_at": _generated_at(),
        "renderer_preset": PW4_V1_RENDER_CONFIG.name,
        "native_dimensions": "1448x1072",
        "transport_dimensions": "1072x1448",
        "rotation": "ccw",
        "date_architecture": "pre-rendered-overlay",
        "date_font_family": body_font.family,
        "date_font_size": str(DATE_FONT_SIZE),
        "time_source": "kindle-local-single-snapshot",
        "source_date_epoch": os.environ.get("SOURCE_DATE_EPOCH", "unset"),
    }
    manifest = BundleManifest(
        metadata,
        {minute: tuple(ids) for minute, ids in safe_pools.items()},
        quote_records,
        date_records,
    )
    write_manifest(output, manifest)
    validate_manifest(
        manifest,
        root=output,
        require_all_minutes=len(selected_minutes) == 1440,
        verify_checksums=True,
    )
    write_checksums(output, manifest)
    total = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
    summary = BundleBuildSummary(
        str(output),
        len(selected_minutes) == 1440,
        len(selected_minutes),
        len(quote_records),
        sum(map(len, safe_pools.values())),
        len(date_records),
        rendered,
        reused,
        median_bytes,
        p90_bytes,
        max_bytes,
        projected,
        projected_date_bytes,
        projected_manifest_bytes,
        projected_fat_overhead,
        projected_total,
        total,
        fingerprint,
    )
    (output / "build-summary.json").write_text(
        json.dumps(summary.as_dict(), indent=2) + "\n", encoding="utf-8"
    )
    sample_directory.cleanup()
    return summary
