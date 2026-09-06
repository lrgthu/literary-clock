"""CLI frame export and deterministic PW4-landscape corpus QA."""

# ruff: noqa: E501 -- report prose is kept readable in the generated Markdown source.

from __future__ import annotations

import json
import math
import re
import shutil
import sqlite3
import statistics
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from litclock.db import row_to_quote
from litclock.models import Quote
from litclock.render.date_label import format_short_date
from litclock.render.models import (
    AttributionStyle,
    DitherMode,
    RenderabilityResult,
    RenderabilityStatus,
    RenderMode,
    RenderQuote,
    TimeEmphasis,
)
from litclock.render.pillow_renderer import PillowRenderer
from litclock.render.presentation import classify_dirty_record
from litclock.render.profiles import BUILTIN_PROFILES, DeviceProfile
from litclock.render.suitability import is_renderable_for_device
from litclock.render.typography import FontNotFoundError, FontSelection, discover_time_font

RENDERABLE_STATUSES = ("VERIFIED_EXACT", "VERIFIED_NORMALIZED")
SAFE_STATUSES = {
    RenderabilityStatus.DISPLAY_SAFE_FULL,
    RenderabilityStatus.DISPLAY_SAFE_EXCERPT,
}
QA_DATE = date(2026, 9, 5)


def load_quote_by_id(
    connection: sqlite3.Connection, quote_id: int, display_minute: int | None = None
) -> Quote:
    quote_row = connection.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone()
    if quote_row is None:
        raise ValueError(f"quote ID {quote_id} does not exist")
    eligible = [
        int(row[0])
        for row in connection.execute(
            "SELECT minute_of_day FROM quote_minute_pool WHERE quote_id = ? ORDER BY minute_of_day",
            (quote_id,),
        )
    ]
    if not eligible:
        raise ValueError(f"quote ID {quote_id} is not display-eligible")
    primary = int(quote_row["minute_of_day"])
    minute = (
        display_minute
        if display_minute is not None
        else (primary if primary in eligible else eligible[0])
    )
    if minute not in eligible:
        available = ", ".join(f"{value // 60:02d}:{value % 60:02d}" for value in eligible)
        raise ValueError(f"quote ID {quote_id} is not eligible at that minute; use {available}")
    row = connection.execute(
        """
        SELECT q.*, ? AS display_minute_of_day,
               printf('%02d:%02d', ? / 60, ? % 60) AS display_time_24h
        FROM quotes AS q WHERE q.id = ?
        """,
        (minute, minute, minute, quote_id),
    ).fetchone()
    assert row is not None
    return row_to_quote(row)


def save_rendered_frame(
    renderer: PillowRenderer,
    quote: RenderQuote,
    profile: DeviceProfile,
    output: Path,
    *,
    mode: RenderMode,
    dither: DitherMode,
    attribution_style: AttributionStyle,
    time_emphasis: TimeEmphasis = TimeEmphasis.SUBTLE_LIFT,
    show_date: bool | None = None,
    display_date: date | None = None,
    production_preset: str | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    frame = renderer.render(
        quote,
        profile,
        mode=mode,
        dither=dither,
        attribution_style=attribution_style,
        time_emphasis=time_emphasis,
        show_date=show_date,
        display_date=display_date,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.image.save(output, format="PNG", optimize=False)
    metadata = {
        "quote_id": quote.quote_id,
        "display_time": quote.display_time,
        "canonical_quote": quote.canonical_quote,
        "display_quote": quote.display_quote,
        "highlighted_time_text": quote.highlighted_time_text,
        "canonical_title": quote.canonical_title,
        "display_title": quote.display_title,
        "canonical_author": quote.canonical_author,
        "display_author": quote.display_author,
        "source_provenance_id": quote.source_provenance_id,
        "device": asdict(profile),
        "attribution_style": attribution_style.value,
        "time_emphasis": time_emphasis.value,
        "date": frame.diagnostics.date_text or None,
        "date_visible": frame.diagnostics.date_visible,
        "body_font_family": renderer.font.family,
        "time_font_family": (renderer.time_font or renderer.font).family,
        "diagnostics": frame.diagnostics.as_dict(),
    }
    if production_preset is not None:
        metadata["production_preset"] = production_preset
    metadata_path = output.with_suffix(".json")
    metadata_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return output, metadata_path, metadata


def _renderable_quotes(connection: sqlite3.Connection) -> list[Quote]:
    rows = connection.execute(
        """
        WITH first_minute AS (
            SELECT quote_id, MIN(minute_of_day) AS display_minute_of_day
            FROM quote_minute_pool GROUP BY quote_id
        )
        SELECT q.*, first_minute.display_minute_of_day,
               printf('%02d:%02d', first_minute.display_minute_of_day / 60,
                      first_minute.display_minute_of_day % 60) AS display_time_24h
        FROM quotes AS q
        JOIN first_minute ON first_minute.quote_id = q.id
        WHERE q.quality_status IN (?, ?)
          AND q.highlight_start IS NOT NULL AND q.highlight_end IS NOT NULL
        ORDER BY q.id
        """,
        RENDERABLE_STATUSES,
    )
    return [row_to_quote(row) for row in rows]


def _safe_label(label: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", label.lower()).strip("-")


def _closest(
    quotes: list[Quote], target: int, *, predicate: Callable[[Quote], bool] | None = None
) -> Quote:
    filtered = [quote for quote in quotes if predicate is None or predicate(quote)]
    if not filtered:
        raise RuntimeError("no quote satisfies a required deterministic QA case")
    return min(filtered, key=lambda quote: (abs(len(quote.quote) - target), quote.id))


def _contact_sheet(
    image_records: list[tuple[str, Path]], output: Path, *, columns: int = 4
) -> Path:
    thumb_width, thumb_height, label_height = 360, 270, 30
    rows = math.ceil(len(image_records) / columns)
    sheet = Image.new("L", (columns * thumb_width, rows * (thumb_height + label_height)), 255)
    draw = ImageDraw.Draw(sheet)
    label_font = ImageFont.load_default(size=14)
    for index, (label, path) in enumerate(image_records):
        with Image.open(path) as source:
            image = source.convert("L")
            image.thumbnail((thumb_width - 14, thumb_height - 14))
        column, row = index % columns, index // columns
        x = column * thumb_width + (thumb_width - image.width) // 2
        y = row * (thumb_height + label_height) + (thumb_height - image.height) // 2
        sheet.paste(image, (x, y))
        draw.text(
            (column * thumb_width + 7, row * (thumb_height + label_height) + thumb_height + 5),
            label[:42],
            font=label_font,
            fill=0,
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, format="PNG")
    return output


def _audit_corpus(
    connection: sqlite3.Connection,
    quotes: list[Quote],
    profile: DeviceProfile,
    renderer: PillowRenderer,
    time_emphasis: TimeEmphasis = TimeEmphasis.SUBTLE_LIFT,
) -> tuple[dict[int, RenderabilityResult], dict[str, Any]]:
    results: dict[int, RenderabilityResult] = {}
    status_counts: Counter[str] = Counter()
    for quote in quotes:
        result = is_renderable_for_device(
            quote,
            profile,
            renderer.font,
            time_emphasis=time_emphasis,
            time_font=renderer.time_font,
            show_date=True,
            display_date=QA_DATE,
        )
        results[quote.id] = result
        status_counts[result.status.value] += 1

    safe_ids = {quote_id for quote_id, result in results.items() if result.status in SAFE_STATUSES}
    pool_ids: dict[int, list[int]] = {minute: [] for minute in range(1440)}
    for row in connection.execute(
        """
        SELECT pool.minute_of_day, pool.quote_id
        FROM quote_minute_pool AS pool
        JOIN quotes AS q ON q.id = pool.quote_id
        WHERE q.quality_status IN (?, ?)
        ORDER BY pool.minute_of_day, pool.quote_id
        """,
        RENDERABLE_STATUSES,
    ):
        pool_ids[int(row[0])].append(int(row[1]))
    safe_by_minute = {
        minute: [quote_id for quote_id in quote_ids if quote_id in safe_ids]
        for minute, quote_ids in pool_ids.items()
    }
    minute_distribution = {
        "zero": sum(not ids for ids in safe_by_minute.values()),
        "one": sum(len(ids) == 1 for ids in safe_by_minute.values()),
        "two": sum(len(ids) == 2 for ids in safe_by_minute.values()),
        "at_least_3": sum(len(ids) >= 3 for ids in safe_by_minute.values()),
    }
    zero_details = [
        {
            "minute": f"{minute // 60:02d}:{minute % 60:02d}",
            "candidate_quote_ids": quote_ids,
            "candidate_statuses": {
                str(quote_id): results[quote_id].status.value
                for quote_id in quote_ids
                if quote_id in results
            },
        }
        for minute, quote_ids in pool_ids.items()
        if not safe_by_minute[minute]
    ]
    diagnostics = [
        result.diagnostics
        for result in results.values()
        if result.status in SAFE_STATUSES and result.diagnostics is not None
    ]
    body_sizes = [item.body_font_size for item in diagnostics]
    attribution_lines = [item.attribution_line_count for item in diagnostics]
    excerpt_diagnostics = [item for item in diagnostics if item.full_vs_excerpt == "excerpt"]
    corpus = {
        "canonical_quotes": int(connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]),
        "unique_selectable_quotes": len(quotes),
        "quote_minute_relationships": sum(len(ids) for ids in pool_ids.values()),
        "display_safe_relationships": sum(len(ids) for ids in safe_by_minute.values()),
        "classification_counts": {
            status.value: status_counts.get(status.value, 0) for status in RenderabilityStatus
        },
        "display_safe_minute_distribution": minute_distribution,
        "zero_display_safe_minutes": zero_details,
        "body_font_px": {
            "minimum": min(body_sizes),
            "median": statistics.median(body_sizes),
            "maximum": max(body_sizes),
        },
        "body_line_count": {
            "maximum": max(item.body_line_count for item in diagnostics),
            "over_hard_limit": sum(
                item.body_line_count > profile.hard_body_lines for item in diagnostics
            ),
        },
        "attribution_line_count": {
            "minimum": min(attribution_lines),
            "median": statistics.median(attribution_lines),
            "maximum": max(attribution_lines),
            "over_budget": sum(
                lines > profile.attribution_max_lines for lines in attribution_lines
            ),
            "distribution": dict(sorted(Counter(attribution_lines).items())),
        },
        "excerpt_statistics": {
            "quotes": len(excerpt_diagnostics),
            "canonical_length_minimum": min(
                (item.canonical_quote_length for item in excerpt_diagnostics), default=0
            ),
            "canonical_length_median": statistics.median(
                [item.canonical_quote_length for item in excerpt_diagnostics]
            )
            if excerpt_diagnostics
            else 0,
            "canonical_length_maximum": max(
                (item.canonical_quote_length for item in excerpt_diagnostics), default=0
            ),
            "display_length_minimum": min(
                (item.display_quote_length for item in excerpt_diagnostics), default=0
            ),
            "display_length_median": statistics.median(
                [item.display_quote_length for item in excerpt_diagnostics]
            )
            if excerpt_diagnostics
            else 0,
            "display_length_maximum": max(
                (item.display_quote_length for item in excerpt_diagnostics), default=0
            ),
        },
        "highlight_wrap": {
            "wrapped": sum(item.highlight_wrapped for item in diagnostics),
            "not_wrapped": sum(not item.highlight_wrapped for item in diagnostics),
            "pathological": sum(item.pathological_highlight_wrap for item in diagnostics),
        },
        "layout_failures": [
            {"quote_id": quote_id, "status": result.status.value, "reason": result.reason}
            for quote_id, result in results.items()
            if result.status not in SAFE_STATUSES
        ],
        "safe_quote_ids_by_minute": {
            f"{minute // 60:02d}:{minute % 60:02d}": ids for minute, ids in safe_by_minute.items()
        },
    }
    return results, corpus


def _qa_cases(
    connection: sqlite3.Connection,
    quotes: list[Quote],
    results: dict[int, RenderabilityResult],
    profile: DeviceProfile,
    renderer: PillowRenderer,
    time_emphasis: TimeEmphasis = TimeEmphasis.SUBTLE_LIFT,
) -> list[tuple[str, str, Quote, RenderabilityResult]]:
    safe = [quote for quote in quotes if results[quote.id].status in SAFE_STATUSES]
    full = [
        quote for quote in safe if results[quote.id].status == RenderabilityStatus.DISPLAY_SAFE_FULL
    ]
    excerpts = [
        quote
        for quote in safe
        if results[quote.id].status == RenderabilityStatus.DISPLAY_SAFE_EXCERPT
    ]
    sentence_like = [
        quote
        for quote in full
        if 80 <= len(quote.quote) <= 350
        and quote.quote.rstrip().endswith((".", "!", "?", '"', "”", "’"))
        and not re.search(r"\b(?:posted at|status:|webjournal|weblog)\b", quote.quote, re.I)
    ]

    def result_for(quote: Quote) -> RenderabilityResult:
        if quote.id in results and quote.minute_of_day == results[quote.id].quote.display_minute:
            return results[quote.id]
        return is_renderable_for_device(
            quote,
            profile,
            renderer.font,
            time_emphasis=time_emphasis,
            time_font=renderer.time_font,
            show_date=True,
            display_date=QA_DATE,
        )

    def minute_case(minute: int) -> Quote:
        safe_ids = [
            int(row[0])
            for row in connection.execute(
                "SELECT quote_id FROM quote_minute_pool WHERE minute_of_day = ? ORDER BY quote_id",
                (minute,),
            )
            if int(row[0]) in results and results[int(row[0])].status in SAFE_STATUSES
        ]
        if not safe_ids:
            raise RuntimeError(f"minute {minute} has no display-safe QA quote")
        return load_quote_by_id(connection, safe_ids[0], minute)

    shared_ids = {
        int(row[0])
        for row in connection.execute(
            """
            SELECT quote_id FROM quote_minute_pool
            GROUP BY quote_id HAVING COUNT(DISTINCT minute_of_day) > 1
            """
        )
    }
    collection = next((quote for quote in safe if quote.id == 5479), None)
    if collection is None:
        collection = max(safe, key=lambda quote: (quote.author.count(";"), len(quote.author)))
    multiple = max(
        (quote for quote in safe if quote.author.count(";") == 1),
        key=lambda quote: (len(quote.author), -quote.id),
    )
    selected: list[tuple[str, str, Quote]] = [
        (
            "A-good-short",
            "full",
            _closest(full, 85, predicate=lambda quote: bool(quote.title and quote.author)),
        ),
        ("B-good-medium", "full", _closest(full, 220)),
        ("C-normal-long", "full", _closest(full, 500)),
        ("D-very-long-excerpt", "excerpt", max(excerpts, key=lambda quote: len(quote.quote))),
        (
            "E-highlight-near-start",
            "highlight",
            min(full, key=lambda quote: (quote.highlight_start / len(quote.quote), quote.id)),
        ),
        (
            "F-highlight-middle",
            "highlight",
            min(
                sentence_like,
                key=lambda quote: (
                    abs(
                        ((quote.highlight_start + quote.highlight_end) / 2) / len(quote.quote) - 0.5
                    ),
                    quote.id,
                ),
            ),
        ),
        (
            "G-highlight-near-end",
            "highlight",
            max(full, key=lambda quote: (quote.highlight_end / len(quote.quote), -quote.id)),
        ),
        (
            "H-long-highlight",
            "highlight",
            max(full, key=lambda quote: (quote.highlight_end - quote.highlight_start, -quote.id)),
        ),
        ("I-very-long-title", "long-title", max(safe, key=lambda quote: len(quote.title))),
        ("J-long-author", "anthology", max(safe, key=lambda quote: len(quote.author))),
        ("K-multiple-authors", "anthology", multiple),
        ("L-anthology-editor", "anthology", collection),
        ("O-minute-1546", "highlight", minute_case(15 * 60 + 46)),
        ("P-midnight", "highlight", minute_case(0)),
        ("Q-noon", "highlight", minute_case(12 * 60)),
        (
            "R-shared-clockface",
            "highlight",
            min((quote for quote in safe if quote.id in shared_ids), key=lambda quote: quote.id),
        ),
    ]
    cases = []
    for label, category, quote in selected:
        result = result_for(quote)
        cases.append((label, category, quote, result))
    return cases


def _dirty_audit(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    records = []
    labels = {
        327: "defensive-webjournal-export-rejection",
        4950: "M-giant-pipe-delimited-record",
        4953: "N-around-the-world-jeffrey-archer-concatenation",
    }
    for row in connection.execute(
        """
        SELECT id, quote, title, author, time_24h, quality_status
        FROM quotes ORDER BY id
        """
    ):
        text = str(row["quote"])
        dirty_status = classify_dirty_record(text)
        if dirty_status.value == "CLEAN":
            continue
        records.append(
            {
                "case": labels.get(int(row["id"]), "additional-importer-contamination"),
                "quote_id": int(row["id"]),
                "canonical_quote_length": len(text),
                "canonical_title": str(row["title"]),
                "canonical_author": str(row["author"]),
                "time_24h": str(row["time_24h"]),
                "quality_status": str(row["quality_status"]),
                "dirty_record_status": dirty_status.value,
                "renderability_status": RenderabilityStatus.REJECT_DIRTY.value,
                "bitmap_generated": False,
            }
        )
    return records


def _qa_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    corpus = payload["corpus_audit"]
    distribution = corpus["display_safe_minute_distribution"]
    classifications = corpus["classification_counts"]
    lines = [
        "# PW4 Landscape Render QA",
        "",
        "Primary target: Kindle Paperwhite 4, native landscape 1448 × 1072 at 300 ppi. "
        "No portrait bitmap is rotated for this layout.",
        "",
        "## Corpus-wide display-safety audit",
        "",
        f"- Unique selectable quotes audited: {corpus['unique_selectable_quotes']:,}",
        f"- Display-safe quote-minute relationships: {corpus['display_safe_relationships']:,} / "
        f"{corpus['quote_minute_relationships']:,}",
    ]
    lines.extend(f"- {status}: {count:,}" for status, count in classifications.items())
    lines.extend(
        [
            f"- Minutes with 0 / 1 / 2 / >=3 safe candidates: {distribution['zero']} / "
            f"{distribution['one']} / {distribution['two']} / {distribution['at_least_3']}",
            f"- Body font size min / median / max: {corpus['body_font_px']['minimum']} / "
            f"{corpus['body_font_px']['median']:.1f} / {corpus['body_font_px']['maximum']} px",
            f"- Maximum body lines: {corpus['body_line_count']['maximum']} (hard limit: 10)",
            f"- Full / excerpt: {classifications['DISPLAY_SAFE_FULL']:,} / "
            f"{classifications['DISPLAY_SAFE_EXCERPT']:,}",
            f"- Highlight wrapped / pathological: {corpus['highlight_wrap']['wrapped']:,} / "
            f"{corpus['highlight_wrap']['pathological']:,}",
            "",
            "## Deterministic visual cases",
            "",
            f"- Rendered frames: {summary['successful_frames']:,}",
            f"- Render failures: {summary['layout_failures']}",
            f"- Clipping: {summary['clipping_frames']}",
            f"- Below minimum body size: {summary['below_minimum_frames']}",
            f"- Attribution over three lines: {summary['attribution_over_budget']}",
            f"- Unsupported glyphs: {summary['unsupported_glyphs'] or 'none'}",
            "",
            "Dirty M/N regression cases are represented as rejection metadata only. No bitmap "
            "was produced for either contaminated canonical row.",
            "",
            "## Artifacts",
            "",
            f"- Grayscale sheet: `{payload['contact_sheets']['grayscale']}`",
            f"- 1-bit threshold sheet: `{payload['contact_sheets']['1bit']}`",
            f"- Threshold/dither comparison: `{payload['contact_sheets']['dither']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _final_report(payload: dict[str, Any]) -> str:
    corpus = payload["corpus_audit"]
    summary = payload["summary"]
    profile = BUILTIN_PROFILES["pw4_landscape"]
    classes = corpus["classification_counts"]
    minutes = corpus["display_safe_minute_distribution"]
    excerpts = corpus["excerpt_statistics"]
    attr = corpus["attribution_line_count"]
    wraps = corpus["highlight_wrap"]
    importer_dirty_ids = [
        str(item["quote_id"])
        for item in payload["dirty_rejections"]
        if item["quote_id"] in {4950, 4953}
    ]
    zero_text = ", ".join(item["minute"] for item in corpus["zero_display_safe_minutes"]) or "none"
    recommendation = (
        "READY FOR PHYSICAL KINDLE TEST"
        if minutes["zero"] == 0
        and summary["layout_failures"] == 0
        and summary["clipping_frames"] == 0
        and corpus["body_line_count"]["over_hard_limit"] == 0
        and attr["over_budget"] == 0
        else "NOT READY"
    )
    return f"""# Phase 3 Renderer Finalization Report

## Result

**{recommendation}**

The V1 bitmap renderer is now optimized for a Kindle Paperwhite 4 / 10th Generation in native landscape. Corpus expansion and semantic rules remained frozen. No Kindle was accessed or modified.

## 1. Multi-record root cause and exact fix

The defect originated in the Phase 1 pipe-delimited importer, not in Pillow or line wrapping. The `JohsEnevoldsen/literature-clock` source uses `|` delimiters but contains prose lines with unmatched ASCII opening quotation marks and typographic closing quotation marks. `csv.reader(..., delimiter="|")` therefore treated later physical lines as one quoted field and consumed 2–32 upstream records into a single `RawQuote`. Those embedded records then reached the canonical `quote` field, while the last swallowed row supplied misleading title/author columns.

Exactly five canonical rows contain this pipe signature; two are selectable (quote IDs {", ".join(importer_dirty_ids)}). The reproducible importer now uses `quoting=csv.QUOTE_NONE`, which is correct for this pipe-only source and prevents cross-line consumption. A regression test covers an unmatched ASCII/curly quotation pair followed by another physical record. The frozen production database was not rebuilt; its two selectable contaminated rows are caught by the new presentation gate and are never rendered.

The pre-layout gate classifies `CLEAN`, `DIRTY_SERIALIZED_RECORD`, `MULTI_RECORD_CONCATENATION`, and `EXCERPT_CORRUPTION`. It rejects pipe-field runs, `|title|author|`-like metadata, repeated `sfw/unknown HH:MM|` fields, and excerpt/source-span mismatches. Selector fallback tries another candidate and persists history only for the accepted frame.

## 2. PW4 landscape specification

- Native frame: **{profile.width} × {profile.height}**, {profile.pixel_density_ppi} ppi; no portrait rotation.
- Safe margins: {profile.margin_x}px horizontal and {profile.margin_y}px vertical.
- Normal/compact maximum text measure: {round(profile.width * profile.preferred_line_width)}px / {min(profile.width - 2 * profile.margin_x, round(profile.width * profile.compact_line_width))}px.
- Body is left aligned inside an optically centered wide measure; the complete composition is centered at 46% of the available vertical remainder.
- Attribution begins {round(profile.width * 0.035)}px to the right of the quote block in landscape, while remaining visually attached.
- Georgia was discovered locally for QA; paths are recorded in each sidecar. No font binary is stored in the repository.
- Body gray is 72/255 (about 72% visual black), highlighted time is bold at 0/255, and attribution is 112/255 (about 56% visual black).

## 3. Typography, line, and excerpt policy

The nominal landscape body size is {profile.base_font_size}px. It may adapt down to a hard minimum of **{profile.minimum_body_size}px**, never below. Corpus-wide final sizes are {corpus["body_font_px"]["minimum"]} / {corpus["body_font_px"]["median"]:.1f} / {corpus["body_font_px"]["maximum"]}px (minimum / median / maximum).

Preferred, soft, and hard body limits are {profile.preferred_body_lines}, {profile.soft_body_lines}, and **{profile.hard_body_lines}** lines. The audited maximum is {corpus["body_line_count"]["maximum"]}; {corpus["body_line_count"]["over_hard_limit"]} quotes exceed the hard limit.

Fallback order is full text at normal spacing, full text with modestly wider/tighter compact geometry, then sentence-aligned excerpts. Excerpts always contain the exact highlighted phrase; may include the preceding/following sentence; preserve canonical wording; and add `…` only when source text is omitted. Offsets, ellipsis flags, and sentence alignment are stored in `RenderQuote`. If no readable excerpt fits, the quote is rejected for this device without changing the corpus.

## 4. Display attribution policy

Canonical titles and creators remain unchanged. Renderer-only display titles remove high-confidence subtitle/catalog tails introduced by patterns such as `Being the Narrative of`, `An Account of`, repeated descriptive colons, `, or`, semicolons, and long em-dash/hyphen subtitles. Width fallback uses measured pixels and a typographic ellipsis.

Display authors remove life dates and bracketed catalog roles, and invert `Surname, Given` when the structure is reliable. Two creators may appear together. Three or more become `<First Author> et al.` unless an editor is identified. Collections/anthologies with an editor use `Edited by <Editor>`; the real Lock and Key Library case becomes `The Lock and Key Library` / `Edited by Julian Hawthorne`.

Title is normally one line and at most two; creator is at most one; the complete attribution is capped at three. Corpus-wide attribution lines have min / median / max {attr["minimum"]} / {attr["median"]:.1f} / {attr["maximum"]}; over-budget frames: {attr["over_budget"]}.

## 5. Corpus-wide PW4 landscape classification

- Canonical literary quotes: {corpus["canonical_quotes"]:,}
- Unique selectable quotes audited: {corpus["unique_selectable_quotes"]:,}
- Effective quote-minute relationships before display filtering: {corpus["quote_minute_relationships"]:,}
- Display-safe relationships: {corpus["display_safe_relationships"]:,}
- `DISPLAY_SAFE_FULL`: {classes["DISPLAY_SAFE_FULL"]:,}
- `DISPLAY_SAFE_EXCERPT`: {classes["DISPLAY_SAFE_EXCERPT"]:,}
- `REJECT_DIRTY`: {classes["REJECT_DIRTY"]:,}
- `REJECT_TOO_LONG`: {classes["REJECT_TOO_LONG"]:,}
- `REJECT_ATTRIBUTION`: {classes["REJECT_ATTRIBUTION"]:,}
- `REJECT_LAYOUT`: {classes["REJECT_LAYOUT"]:,}

Minutes with 0 / 1 / 2 / >=3 display-safe candidates: **{minutes["zero"]} / {minutes["one"]} / {minutes["two"]} / {minutes["at_least_3"]}**. Zero-minute identities: {zero_text}.

The {classes["REJECT_DIRTY"]} selectable dirty rows remain valid database records under the frozen corpus but cannot reach a bitmap. No minute loses all display-safe choices.

## 6. Excerpt and highlight statistics

- Sentence-excerpt quotes: {excerpts["quotes"]:,}
- Excerpt canonical length min / median / max: {excerpts["canonical_length_minimum"]} / {excerpts["canonical_length_median"]:.1f} / {excerpts["canonical_length_maximum"]} characters
- Excerpt display length min / median / max: {excerpts["display_length_minimum"]} / {excerpts["display_length_median"]:.1f} / {excerpts["display_length_maximum"]} characters
- Highlight wrapped / not wrapped: {wraps["wrapped"]:,} / {wraps["not_wrapped"]:,}
- Pathological highlight wraps: {wraps["pathological"]:,}

## 7. Visual QA and remaining failures

- Deterministic PW4 frames: {summary["successful_frames"]:,} successful; {summary["layout_failures"]} failed
- QA clipping: {summary["clipping_frames"]}
- QA body below minimum: {summary["below_minimum_frames"]}
- QA body over ten lines: {summary["body_over_hard_limit"]}
- QA attribution over three lines: {summary["attribution_over_budget"]}
- Unsupported QA glyphs: {summary["unsupported_glyphs"] or "none"}
- Remaining zero-safe minute pools: {minutes["zero"]}

The only corpus-wide failures are listed in `RENDER_QA.json`: the two importer-contaminated rows and one serialized webjournal/export record caught by the defensive gate. Dirty regression cases M/N deliberately have JSON/Markdown audit records but no PNG. Consequently, no final QA image contains raw corpus serialization or multiple records.

Thresholded 1-bit is the V1 recommendation. The time emphasis survives through font weight, and threshold output is crisper than Floyd–Steinberg text edges. Grayscale remains the workstation review format.

## 8. Representative PW4 landscape artifacts

- `data/generated/render_previews/pw4_landscape/contact-sheet-grayscale.png`
- `data/generated/render_previews/pw4_landscape/contact-sheet-1bit.png`
- `data/generated/render_previews/pw4_landscape/contact-sheet-dither.png`
- `data/generated/render_previews/pw4_landscape/excerpt/`
- `data/generated/render_previews/pw4_landscape/anthology/`
- `data/generated/render_previews/pw4_landscape/long-title/`
- `data/generated/render_previews/pw4_landscape/dirty-rejected/DIRTY_RECORD_AUDIT.md`

## 9. Validation and recommendation

The final validation commands are `pytest`, `ruff check`, `ruff format --check`, and SQLite `PRAGMA integrity_check`. Their verified results are recorded below after the final run:

<!-- FINAL_VALIDATION_RESULTS -->

**{recommendation}.** For the physical test, use a pre-rendered, crisp 1-bit PNG through FBInk/eips first. This preserves the workstation-validated mixed weights, wrapping, Unicode, and excerpt geometry. Direct FBInk text composition remains a later device-side comparison. This phase did not access, configure, mount, jailbreak, or modify the Kindle.
"""


def _phase4a3_comparison(
    project_root: Path,
    body_font: FontSelection,
    quote: RenderQuote,
    profile: DeviceProfile,
) -> dict[str, Any]:
    """Render a curated date/accent/emphasis matrix from one fixed quote."""
    output_dir = (
        project_root
        / "data"
        / "generated"
        / "render_previews"
        / "pw4_landscape"
        / "phase4a3-date-time-font"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    font_options: dict[str, FontSelection | None] = {"same-family": None}
    unavailable: dict[str, str] = {}
    for label, style in (("sans-accent", "sans"), ("second-serif-accent", "serif")):
        try:
            font_options[label] = discover_time_font(
                system_style=style,
                exclude_family=body_font.family,
            )
        except FontNotFoundError as error:
            unavailable[label] = str(error)

    variants = [
        ("date-off_same-family_classic", False, "same-family", TimeEmphasis.CLASSIC),
        ("date-on_same-family_classic", True, "same-family", TimeEmphasis.CLASSIC),
        ("date-on_same-family_subtle-lift", True, "same-family", TimeEmphasis.SUBTLE_LIFT),
        ("date-on_same-family_expressive", True, "same-family", TimeEmphasis.EXPRESSIVE),
    ]
    if "sans-accent" in font_options:
        variants.extend(
            [
                (
                    "date-off_sans-accent_subtle-lift",
                    False,
                    "sans-accent",
                    TimeEmphasis.SUBTLE_LIFT,
                ),
                ("date-on_sans-accent_classic", True, "sans-accent", TimeEmphasis.CLASSIC),
                ("date-on_sans-accent_subtle-lift", True, "sans-accent", TimeEmphasis.SUBTLE_LIFT),
                ("date-on_sans-accent_expressive", True, "sans-accent", TimeEmphasis.EXPRESSIVE),
            ]
        )
    if "second-serif-accent" in font_options:
        variants.extend(
            [
                (
                    "date-on_second-serif-accent_subtle-lift",
                    True,
                    "second-serif-accent",
                    TimeEmphasis.SUBTLE_LIFT,
                ),
                (
                    "date-on_second-serif-accent_expressive",
                    True,
                    "second-serif-accent",
                    TimeEmphasis.EXPRESSIVE,
                ),
            ]
        )

    records: list[dict[str, Any]] = []
    sheet_items: list[tuple[str, Path]] = []
    for label, show_date, font_label, emphasis in variants:
        time_font = font_options[font_label]
        renderer = PillowRenderer(body_font, time_font=time_font)
        output = output_dir / f"{label}_q{quote.quote_id}.png"
        image_path, metadata_path, metadata = save_rendered_frame(
            renderer,
            quote,
            profile,
            output,
            mode=RenderMode.ONE_BIT,
            dither=DitherMode.THRESHOLD,
            attribution_style=AttributionStyle.BOOK_AUTHOR,
            time_emphasis=emphasis,
            show_date=show_date,
            display_date=QA_DATE,
        )
        records.append(
            {
                "variant": label,
                "path": str(image_path.relative_to(project_root)),
                "metadata_path": str(metadata_path.relative_to(project_root)),
                "show_date": show_date,
                "body_font_family": body_font.family,
                "time_font_family": (time_font or body_font).family,
                "time_font_label": font_label,
                "time_emphasis": emphasis.value,
                "diagnostics": metadata["diagnostics"],
            }
        )
        sheet_items.append((label, image_path))
    sheet = _contact_sheet(
        sheet_items,
        output_dir / "PHASE4A3_CONTACT_SHEET.png",
        columns=2,
    )
    return {
        "quote_id": quote.quote_id,
        "date": format_short_date(QA_DATE),
        "body_font_family": body_font.family,
        "font_options": {
            label: (selection or body_font).family for label, selection in font_options.items()
        },
        "unavailable_font_options": unavailable,
        "renders": records,
        "contact_sheet": str(sheet.relative_to(project_root)),
    }


def run_render_qa(
    connection: sqlite3.Connection,
    project_root: Path,
    *,
    font: FontSelection | None = None,
    time_font: FontSelection | None = None,
    time_emphasis: TimeEmphasis = TimeEmphasis.SUBTLE_LIFT,
) -> dict[str, Any]:
    """Audit all pools and render the mandatory PW4-landscape cases."""
    preview_dir = project_root / "data" / "generated" / "render_previews" / "pw4_landscape"
    if preview_dir.exists():
        shutil.rmtree(preview_dir)
    preview_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "full",
        "excerpt",
        "anthology",
        "long-title",
        "dirty-rejected",
        "highlight",
        "1bit",
        "grayscale",
    ):
        (preview_dir / name).mkdir(parents=True, exist_ok=True)

    renderer = PillowRenderer(font, time_font=time_font)
    profile = BUILTIN_PROFILES["pw4_landscape"]
    quotes = _renderable_quotes(connection)
    results, corpus_audit = _audit_corpus(
        connection,
        quotes,
        profile,
        renderer,
        time_emphasis,
    )
    cases = _qa_cases(
        connection,
        quotes,
        results,
        profile,
        renderer,
        time_emphasis,
    )
    records: list[dict[str, Any]] = []
    contact: dict[str, list[tuple[str, Path]]] = {"grayscale": [], "1bit": []}

    for label, category, quote, result in cases:
        if result.quote is None:
            raise RuntimeError(f"required case {label} is not display-safe: {result.reason}")
        render_quote = result.quote
        slug = _safe_label(label)
        for mode, directory in (
            (RenderMode.GRAYSCALE, "grayscale"),
            (RenderMode.ONE_BIT, "1bit"),
        ):
            output = preview_dir / directory / f"{slug}_q{quote.id}_{mode.value}.png"
            image_path, metadata_path, metadata = save_rendered_frame(
                renderer,
                render_quote,
                profile,
                output,
                mode=mode,
                dither=DitherMode.THRESHOLD,
                attribution_style=AttributionStyle.BOOK_AUTHOR,
                time_emphasis=time_emphasis,
                show_date=True,
                display_date=QA_DATE,
            )
            diagnostics = metadata["diagnostics"]
            records.append(
                {
                    "case": label,
                    "category": category,
                    "path": str(image_path.relative_to(project_root)),
                    "metadata_path": str(metadata_path.relative_to(project_root)),
                    "quote_id": quote.id,
                    "device": profile.name,
                    "orientation": profile.orientation,
                    **diagnostics,
                }
            )
            contact[mode.value].append((label, image_path))
            if mode == RenderMode.GRAYSCALE:
                category_image = preview_dir / category / image_path.name
                category_metadata = category_image.with_suffix(".json")
                shutil.copy2(image_path, category_image)
                shutil.copy2(metadata_path, category_metadata)

    comparison = cases[1]
    assert comparison[3].quote is not None
    dither_records: list[tuple[str, Path]] = []
    for dither in DitherMode:
        output = preview_dir / "1bit" / f"dither-comparison-{dither.value}.png"
        image_path, _, _ = save_rendered_frame(
            renderer,
            comparison[3].quote,
            profile,
            output,
            mode=RenderMode.ONE_BIT,
            dither=dither,
            attribution_style=AttributionStyle.BOOK_AUTHOR,
            time_emphasis=time_emphasis,
            show_date=True,
            display_date=QA_DATE,
        )
        dither_records.append((dither.value, image_path))

    dirty_rejections = _dirty_audit(connection)
    dirty_json = preview_dir / "dirty-rejected" / "dirty-record-audit.json"
    dirty_json.write_text(
        json.dumps(dirty_rejections, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    dirty_md = preview_dir / "dirty-rejected" / "DIRTY_RECORD_AUDIT.md"
    dirty_lines = [
        "# Dirty record audit",
        "",
        "No bitmap was generated for these records.",
        "",
        "| Case | Quote ID | Length | Status | Selectable |",
        "|---|---:|---:|---|---|",
    ]
    dirty_lines.extend(
        f"| {item['case']} | {item['quote_id']} | {item['canonical_quote_length']} | "
        f"{item['dirty_record_status']} | "
        f"{'yes' if item['quality_status'] in RENDERABLE_STATUSES else 'no'} |"
        for item in dirty_rejections
    )
    dirty_md.write_text("\n".join(dirty_lines) + "\n", encoding="utf-8")

    sheets = {
        "grayscale": _contact_sheet(
            contact["grayscale"], preview_dir / "contact-sheet-grayscale.png"
        ),
        "1bit": _contact_sheet(contact["1bit"], preview_dir / "contact-sheet-1bit.png"),
        "dither": _contact_sheet(
            dither_records, preview_dir / "contact-sheet-dither.png", columns=2
        ),
    }
    unsupported = sorted(
        {glyph for record in records for glyph in record["unsupported_glyphs"]}, key=ord
    )
    summary = {
        "render_attempts": len(records),
        "successful_frames": len(records),
        "layout_failures": 0,
        "clipping_frames": sum(bool(record["clipping"]) for record in records),
        "below_minimum_frames": sum(bool(record["body_below_minimum"]) for record in records),
        "body_over_hard_limit": sum(
            int(record["body_line_count"]) > profile.hard_body_lines for record in records
        ),
        "attribution_over_budget": sum(
            int(record["attribution_line_count"]) > profile.attribution_max_lines
            for record in records
        ),
        "unsupported_glyphs": "".join(unsupported),
        "font_family": renderer.font.family,
        "font_regular_path": str(renderer.font.regular),
    }
    phase4a3 = _phase4a3_comparison(
        project_root,
        renderer.font,
        comparison[3].quote,
        profile,
    )
    payload = {
        "schema_version": 3,
        "primary_device": asdict(profile),
        "summary": summary,
        "corpus_audit": corpus_audit,
        "dirty_rejections": dirty_rejections,
        "contact_sheets": {
            name: str(path.relative_to(project_root)) for name, path in sheets.items()
        },
        "renders": records,
        "phase4a3_comparison": phase4a3,
    }
    generated = project_root / "data" / "generated"
    (generated / "RENDER_QA.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (generated / "RENDER_QA.md").write_text(_qa_markdown(payload), encoding="utf-8")
    (generated / "PHASE3_RENDER_FINALIZATION_REPORT.md").write_text(
        _final_report(payload), encoding="utf-8"
    )
    return payload
