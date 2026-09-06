"""Coverage-driven Project Gutenberg mining, import, audit, and reporting."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sqlite3
import time
from collections import Counter, defaultdict
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from litclock.db import initialize_database
from litclock.gutenberg import (
    PUBLIC_DOMAIN_RIGHTS,
    acquire_catalog,
    acquire_texts,
    export_book_manifest,
    prune_processed_texts,
    read_text,
    select_books,
)
from litclock.gutenberg_text import extract_gutenberg_paragraphs, text_quality_rejection
from litclock.mining import (
    EXACT_CONFIDENCES,
    PassageDuplicateIndex,
    _coverage_state,
    target_priority,
)
from litclock.models import TimeConfidence
from litclock.normalize import minute_to_time, normalized_quote_hash, text_hash
from litclock.stats import calculate_stats, write_reports
from litclock.timeparse import detect_time_expressions
from litclock.xhtml import extract_quote_context

SOURCE_LICENSE = "Project Gutenberg; underlying work: Public domain in the USA."
DEFAULT_STAGE_LIMITS = {"pilot-a": 500, "pilot-b": 5000, "full": None}
_TOKEN_RE = re.compile(r"\b[^\W_]+(?:[’'][^\W_]+)?\b", re.UNICODE)
_EXACT_CONFIDENCE_ENUMS = {
    TimeConfidence.EXACT_24H,
    TimeConfidence.EXACT_AM,
    TimeConfidence.EXACT_PM,
    TimeConfidence.EXACT_CONTEXTUAL,
}
_EMPTY_MINUTES = {15 * 60 + 46, 16 * 60 + 19, 18 * 60 + 17}
_FALSE_POSITIVE_CATEGORIES = (
    "CHAPTER_VERSE",
    "LEGAL_REFERENCE",
    "SCORE_RATIO",
    "DIMENSION_RATIO",
    "PAGE_LINE_REFERENCE",
    "DATE_CATALOG_CODE",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _number_words(value: int) -> str:
    small = (
        "zero",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
    )
    if value < 20:
        return small[value]
    tens = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty"}
    base = value // 10 * 10
    return tens[base] if value == base else f"{tens[base]}-{small[value - base]}"


def target_expression_variants(minute: int) -> tuple[str, ...]:
    """Generate only forms supported by the shared parser for a target minute."""
    if not 0 <= minute < 1440:
        raise ValueError("minute must be between 0 and 1439")
    hour_24, minute_value = divmod(minute, 60)
    hour_12 = hour_24 % 12 or 12
    meridiem = "a.m." if hour_24 < 12 else "p.m."
    daypart = "morning" if hour_24 < 12 else "afternoon" if hour_24 < 18 else "evening"
    values = {
        f"{hour_24:02d}:{minute_value:02d}",
        f"{hour_12}:{minute_value:02d} {meridiem}",
        f"{hour_12}.{minute_value:02d} {meridiem}",
        f"{_number_words(hour_12)} {_number_words(minute_value)} {meridiem}",
    }
    if minute_value == 0:
        values.add(f"{_number_words(hour_12)} o'clock in the {daypart}")
        values.add(f"{_number_words(hour_12)} o’clock in the {daypart}")
    elif minute_value <= 30:
        amount = "half" if minute_value == 30 else _number_words(minute_value)
        unit = "" if minute_value == 30 else " minutes"
        values.add(f"{amount}{unit} past {_number_words(hour_12)} in the {daypart}")
    else:
        delta = 60 - minute_value
        next_hour = hour_12 % 12 + 1
        values.add(f"{_number_words(delta)} minutes to {_number_words(next_hour)} in the {daypart}")
    return tuple(sorted(values))


def build_target_expression_set(
    connection: sqlite3.Connection, *, target_per_minute: int = 7
) -> list[dict[str, object]]:
    """Build the deterministic sparse-minute search vocabulary from live coverage."""
    counts = _coverage_state(connection)[0]
    rows = [
        {
            "minute_of_day": minute,
            "time_24h": minute_to_time(minute),
            "selectable_count": counts.get(minute, 0),
            "deficit": target_per_minute - counts.get(minute, 0),
            "variants": list(target_expression_variants(minute)),
        }
        for minute in range(1440)
        if counts.get(minute, 0) < target_per_minute
    ]
    rows.sort(key=lambda row: (int(row["selectable_count"]), int(row["minute_of_day"])))
    return rows


def write_target_expression_set(connection: sqlite3.Connection, output: Path) -> Path:
    """Persist the dynamic targets for audit; the corpus itself is still scanned once."""
    payload = {
        "generated_at": _now(),
        "policy": "all supported exact variants for minutes with fewer than seven quotes",
        "targets": build_target_expression_set(connection),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    return output


def ambiguous_minute_options(detection_text: str, evidence: str | None) -> tuple[int, int] | None:
    """Read the auditable AM/PM pair emitted by the shared parser."""
    del detection_text
    match = re.search(r"\b(\d{2}):(\d{2})\|(\d{2}):(\d{2})\b", evidence or "")
    if not match:
        return None
    first = int(match.group(1)) * 60 + int(match.group(2))
    second = int(match.group(3)) * 60 + int(match.group(4))
    if not (0 <= first < 1440 and 0 <= second < 1440):
        return None
    return first, second


@dataclass(frozen=True, slots=True)
class ScanBook:
    ebook_id: int
    title: str
    author: str
    subjects: str
    bookshelves: str
    rights: str
    source_url: str
    text_path: str


@dataclass(frozen=True, slots=True)
class ScannedCandidate:
    minute_of_day: int | None
    possible_minute_am: int | None
    possible_minute_pm: int | None
    time_text: str
    quote: str
    source_locator: str
    source_paragraph_index: int
    source_quote_start: int
    source_quote_end: int
    source_expression_start: int
    source_expression_end: int
    previous_paragraph: str
    containing_paragraph: str
    following_paragraph: str
    highlight_start: int
    highlight_end: int
    parser_rule: str
    time_confidence: str
    ampm_evidence: str | None
    context_score: float
    literary_quality_score: float
    false_positive_category: str | None
    context_rejection: str | None
    candidate_hash: str


@dataclass(frozen=True, slots=True)
class ScanResult:
    ebook_id: int
    byte_count: int
    word_count: int
    character_count: int
    candidates: tuple[ScannedCandidate, ...]
    error: str | None = None


def _scan_book(book: ScanBook) -> ScanResult:
    path = Path(book.text_path)
    try:
        raw = read_text(path)
        paragraphs = extract_gutenberg_paragraphs(raw)
    except (OSError, ValueError) as error:
        return ScanResult(
            book.ebook_id, path.stat().st_size if path.exists() else 0, 0, 0, (), str(error)
        )
    candidates: list[ScannedCandidate] = []
    character_count = sum(len(paragraph.text) for paragraph in paragraphs)
    word_count = sum(len(_TOKEN_RE.findall(paragraph.text)) for paragraph in paragraphs)
    for index, paragraph in enumerate(paragraphs):
        previous = paragraphs[index - 1].text if index else ""
        following = paragraphs[index + 1].text if index + 1 < len(paragraphs) else ""
        for detection in detect_time_expressions(paragraph.text):
            context = extract_quote_context(paragraph.text, detection)
            context_rejection = context.rejection_reason or text_quality_rejection(
                paragraph.text, context.quote
            )
            if len(detect_time_expressions(context.quote)) >= 3 and context_rejection is None:
                context_rejection = "three or more time expressions in one context"
            false_positive = None
            if detection.rejection_reason and detection.rejection_reason.startswith(
                "false_positive:"
            ):
                false_positive = detection.rejection_reason.split(":", 1)[1]
            options = (
                ambiguous_minute_options(detection.text, detection.ampm_evidence)
                if detection.confidence == TimeConfidence.AMPM_AMBIGUOUS
                else None
            )
            quote_start = paragraph.source_offset(context.quote_start)
            quote_end = paragraph.source_offset(context.quote_end)
            expression_start = paragraph.source_offset(detection.start)
            expression_end = paragraph.source_offset(detection.end)
            locator = (
                f"pg{book.ebook_id}.txt#{paragraph.section or 'body'}:"
                f"p{paragraph.paragraph_index}:chars={quote_start}-{quote_end}"
            )
            fingerprint = "\0".join(
                (
                    str(book.ebook_id),
                    str(expression_start),
                    str(expression_end),
                    detection.parser_rule,
                    text_hash(context.quote),
                )
            )
            candidates.append(
                ScannedCandidate(
                    detection.minute_of_day,
                    options[0] if options else None,
                    options[1] if options else None,
                    detection.text,
                    context.quote,
                    locator,
                    paragraph.paragraph_index,
                    quote_start,
                    quote_end,
                    expression_start,
                    expression_end,
                    previous,
                    paragraph.text,
                    following,
                    context.highlight_start,
                    context.highlight_end,
                    detection.parser_rule,
                    detection.confidence.value,
                    detection.ampm_evidence,
                    context.context_score,
                    context.literary_quality_score,
                    false_positive,
                    context_rejection,
                    hashlib.sha256(fingerprint.encode()).hexdigest(),
                )
            )
    return ScanResult(
        book.ebook_id,
        path.stat().st_size,
        word_count,
        character_count,
        tuple(candidates),
    )


def _review_status(candidate: ScannedCandidate, duplicate_status: str) -> tuple[str, str | None]:
    if duplicate_status != "NEW":
        return "REJECTED_DUPLICATE", f"duplicate passage: {duplicate_status}"
    if candidate.false_positive_category:
        return "REJECTED_FALSE_POSITIVE", f"false positive: {candidate.false_positive_category}"
    if candidate.time_confidence == TimeConfidence.INVALID.value:
        return "REJECTED_INVALID", "invalid time expression"
    if candidate.context_rejection:
        return "REJECTED_QUALITY", candidate.context_rejection
    if candidate.time_confidence not in EXACT_CONFIDENCES:
        return "PENDING_REVIEW", candidate.time_confidence.casefold().replace("_", " ")
    if candidate.context_score < 70 or candidate.literary_quality_score < 60:
        return "REJECTED_QUALITY", "quality score below conservative auto-import threshold"
    return "HIGH_CONFIDENCE", None


def _insert_scan_result(
    connection: sqlite3.Connection,
    book: ScanBook,
    result: ScanResult,
    duplicate_index: PassageDuplicateIndex,
    coverage: tuple[dict[int, int], dict[int, set[str]], dict[int, set[str]]],
) -> Counter[str]:
    totals: Counter[str] = Counter()
    if result.error:
        connection.execute(
            """
            UPDATE gutenberg_books SET processing_status = 'PROCESSING_FAILED', error = ?,
                processing_timestamp = ?, updated_at = ? WHERE ebook_id = ?
            """,
            (result.error, _now(), _now(), book.ebook_id),
        )
        totals["books_skipped"] = 1
        return totals
    for candidate in result.candidates:
        duplicate = duplicate_index.check(candidate.quote, candidate.minute_of_day)
        review_status, rejection = _review_status(candidate, duplicate.status)
        priority_minute = candidate.minute_of_day
        if priority_minute is None:
            options = [
                value
                for value in (candidate.possible_minute_am, candidate.possible_minute_pm)
                if value is not None
            ]
            if options:
                priority_minute = min(options, key=lambda value: coverage[0].get(value, 0))
        priority = target_priority(priority_minute, book.author, book.title, coverage)
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO gutenberg_candidates (
                ebook_id, minute_of_day, possible_minute_am, possible_minute_pm, time_24h,
                time_text, quote, author, title, subjects, bookshelves, rights, source_url,
                source_file, source_locator, source_paragraph_index, source_quote_start,
                source_quote_end, source_expression_start, source_expression_end,
                previous_paragraph, containing_paragraph, following_paragraph,
                highlight_start, highlight_end, parser_rule, time_confidence, ampm_evidence,
                context_score, literary_quality_score, false_positive_category,
                duplicate_status, duplicate_of_quote_id, duplicate_of_candidate_id,
                duplicate_of_gutenberg_candidate_id, target_priority, review_status,
                rejection_reason, candidate_hash, normalized_quote_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pg'||?||'.txt', ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book.ebook_id,
                candidate.minute_of_day,
                candidate.possible_minute_am,
                candidate.possible_minute_pm,
                minute_to_time(candidate.minute_of_day)
                if candidate.minute_of_day is not None
                else None,
                candidate.time_text,
                candidate.quote,
                book.author,
                book.title,
                book.subjects,
                book.bookshelves,
                book.rights,
                book.source_url,
                book.ebook_id,
                candidate.source_locator,
                candidate.source_paragraph_index,
                candidate.source_quote_start,
                candidate.source_quote_end,
                candidate.source_expression_start,
                candidate.source_expression_end,
                candidate.previous_paragraph,
                candidate.containing_paragraph,
                candidate.following_paragraph,
                candidate.highlight_start,
                candidate.highlight_end,
                candidate.parser_rule,
                candidate.time_confidence,
                candidate.ampm_evidence,
                candidate.context_score,
                candidate.literary_quality_score,
                candidate.false_positive_category,
                duplicate.status,
                duplicate.quote_id,
                duplicate.candidate_id,
                duplicate.gutenberg_candidate_id,
                priority,
                review_status,
                rejection,
                candidate.candidate_hash,
                normalized_quote_hash(candidate.quote),
                _now(),
            ),
        )
        if cursor.rowcount == 0:
            continue
        candidate_id = int(cursor.lastrowid)
        totals["expressions_detected"] += 1
        totals[candidate.time_confidence] += 1
        totals[review_status] += 1
        if candidate.false_positive_category:
            totals[f"FALSE_{candidate.false_positive_category}"] += 1
        if duplicate.status != "NEW":
            totals["duplicates_detected"] += 1
        else:
            duplicate_index.add("GUTENBERG", candidate_id, candidate.quote, candidate.minute_of_day)
    now = _now()
    connection.execute(
        """
        UPDATE gutenberg_books SET processing_status = 'PROCESSED', processing_timestamp = ?,
            byte_count = ?, word_count = ?, character_count = ?, error = NULL, updated_at = ?
        WHERE ebook_id = ?
        """,
        (
            now,
            result.byte_count,
            result.word_count,
            result.character_count,
            now,
            book.ebook_id,
        ),
    )
    totals["books_scanned"] = 1
    totals["bytes_scanned"] = result.byte_count
    totals["words_scanned"] = result.word_count
    totals["characters_scanned"] = result.character_count
    return totals


def ensure_phase2b_baseline(connection: sqlite3.Connection, generated: Path) -> Path:
    path = generated / "phase2b_baseline.json"
    if path.exists():
        return path
    stats = calculate_stats(connection)
    payload = {
        "created_at": _now(),
        "selectable_quotes": stats["total_renderable_quotes"],
        "minute_coverage": stats["minute_coverage"],
    }
    generated.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def _scan_books(
    connection: sqlite3.Connection,
    project_root: Path,
    books: list[ScanBook],
    *,
    workers: int,
    duplicate_index: PassageDuplicateIndex | None = None,
    coverage: tuple[dict[int, int], dict[int, set[str]], dict[int, set[str]]] | None = None,
) -> Counter[str]:
    totals: Counter[str] = Counter()
    if not books:
        return totals
    duplicate_index = duplicate_index or PassageDuplicateIndex(connection)
    coverage = coverage or _coverage_state(connection)
    if workers == 1:
        results: Iterable[ScanResult] = map(_scan_book, books)
        for book, result in zip(books, results, strict=True):
            totals.update(_insert_scan_result(connection, book, result, duplicate_index, coverage))
    else:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            results = executor.map(_scan_book, books, chunksize=1)
            for book, result in zip(books, results, strict=True):
                totals.update(
                    _insert_scan_result(connection, book, result, duplicate_index, coverage)
                )
    connection.commit()
    del project_root
    return totals


def _selected_book_rows(
    connection: sqlite3.Connection, ids: list[int], data_directory: Path
) -> list[ScanBook]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = connection.execute(
        f"""
        SELECT ebook_id, title, authors, subjects, bookshelves, rights, source_url, text_path
        FROM gutenberg_books
        WHERE ebook_id IN ({placeholders}) AND processing_status = 'ACQUIRED'
        ORDER BY ebook_id
        """,
        ids,
    )
    return [
        ScanBook(
            ebook_id=int(row["ebook_id"]),
            title=row["title"],
            author=row["authors"] or "Unknown author",
            subjects=row["subjects"],
            bookshelves=row["bookshelves"],
            rights=row["rights"],
            source_url=row["source_url"],
            text_path=str(data_directory / row["text_path"]),
        )
        for row in rows
    ]


def validate_stage_precision(connection: sqlite3.Connection) -> dict[str, int]:
    """Fail scaling if a deterministic correctness invariant has leaked."""
    bad_rights = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM gutenberg_candidates AS c
            JOIN gutenberg_books AS b USING (ebook_id)
            WHERE c.review_status = 'HIGH_CONFIDENCE'
              AND (b.rights != ? OR b.eligibility_status != 'ELIGIBLE')
            """,
            (PUBLIC_DOMAIN_RIGHTS,),
        ).fetchone()[0]
    )
    false_positive_leaks = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM gutenberg_candidates
            WHERE review_status = 'HIGH_CONFIDENCE' AND false_positive_category IS NOT NULL
            """
        ).fetchone()[0]
    )
    bad_highlights = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM gutenberg_candidates
            WHERE substr(quote, highlight_start + 1, highlight_end - highlight_start) != time_text
            """
        ).fetchone()[0]
    )
    failures = bad_rights + false_positive_leaks + bad_highlights
    if failures:
        raise ValueError(
            "Gutenberg precision gate failed: "
            f"rights={bad_rights}, false-positive leaks={false_positive_leaks}, "
            f"bad highlights={bad_highlights}"
        )
    return {
        "rights_failures": bad_rights,
        "false_positive_leaks": false_positive_leaks,
        "bad_highlights": bad_highlights,
    }


def validate_import_integrity(connection: sqlite3.Connection) -> dict[str, int]:
    """Check that every Gutenberg import is renderable, attributable, and below the cap."""
    field_mismatches = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM gutenberg_candidates AS c
            LEFT JOIN quotes AS q ON q.id = c.imported_quote_id
            JOIN gutenberg_books AS b USING (ebook_id)
            WHERE c.imported_quote_id IS NOT NULL
              AND (q.id IS NULL OR q.minute_of_day != c.minute_of_day
                   OR q.time_text != c.time_text OR q.quote != c.quote
                   OR q.highlight_start != c.highlight_start
                   OR q.highlight_end != c.highlight_end
                   OR q.quality_status != 'VERIFIED_EXACT'
                   OR b.rights != ? OR b.eligibility_status != 'ELIGIBLE')
            """,
            (PUBLIC_DOMAIN_RIGHTS,),
        ).fetchone()[0]
    )
    provenance_failures = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM gutenberg_candidates AS c
            WHERE c.imported_quote_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM quote_provenance AS p
                  WHERE p.quote_id = c.imported_quote_id
                    AND p.source_record_id = CAST(c.id AS TEXT)
              )
            """
        ).fetchone()[0]
    )
    imports_above_cap = int(
        connection.execute(
            """
            WITH coverage AS (
                SELECT pool.minute_of_day, COUNT(*) AS n
                FROM quote_minute_pool AS pool JOIN quotes AS q ON q.id = pool.quote_id
                WHERE q.quality_status IN ('VERIFIED_EXACT', 'VERIFIED_NORMALIZED')
                GROUP BY pool.minute_of_day
            )
            SELECT COUNT(*) FROM gutenberg_candidates AS c
            JOIN coverage USING (minute_of_day)
            WHERE c.imported_quote_id IS NOT NULL AND coverage.n > 7
            """
        ).fetchone()[0]
    )
    result = {
        "field_mismatches": field_mismatches,
        "provenance_failures": provenance_failures,
        "imports_above_cap": imports_above_cap,
    }
    if sum(result.values()):
        raise ValueError(f"Gutenberg import integrity failed: {result}")
    return result


def _revoke_gutenberg_imports(
    connection: sqlite3.Connection, rejected: dict[int, tuple[str, str]]
) -> int:
    """Remove rejected automatic imports while retaining their candidate audit rows."""
    if not rejected:
        return 0
    candidate_ids = sorted(rejected)
    placeholders = ",".join("?" for _ in candidate_ids)
    imported = list(
        connection.execute(
            f"""
            SELECT id, imported_quote_id, minute_of_day FROM gutenberg_candidates
            WHERE id IN ({placeholders}) AND imported_quote_id IS NOT NULL
            """,
            candidate_ids,
        )
    )
    quote_ids = [int(row["imported_quote_id"]) for row in imported]
    import_run_ids: set[int] = set()
    if quote_ids:
        quote_placeholders = ",".join("?" for _ in quote_ids)
        import_run_ids.update(
            int(row[0])
            for row in connection.execute(
                f"""
                SELECT DISTINCT import_run_id FROM quote_provenance
                WHERE quote_id IN ({quote_placeholders})
                """,
                quote_ids,
            )
        )
        connection.execute(
            f"""
            UPDATE gutenberg_candidates
            SET duplicate_of_quote_id = NULL,
                duplicate_status = 'REMOVED_REJECTED_PASSAGE',
                review_status = 'REJECTED_DUPLICATE',
                rejection_reason = 'duplicate referenced an import revoked by precision audit'
            WHERE duplicate_of_quote_id IN ({quote_placeholders})
            """,
            quote_ids,
        )
        connection.execute(
            f"""
            UPDATE mined_candidates
            SET duplicate_of_quote_id = NULL,
                duplicate_status = 'REMOVED_REJECTED_PASSAGE',
                review_status = 'PENDING_REVIEW',
                rejection_reason = 'duplicate referenced an import revoked by precision audit'
            WHERE duplicate_of_quote_id IN ({quote_placeholders})
            """,
            quote_ids,
        )
        connection.execute(
            f"DELETE FROM shuffle_state WHERE minute_of_day IN ({','.join('?' for _ in imported)})",
            [int(row["minute_of_day"]) for row in imported],
        )
    for candidate_id, (status, reason) in rejected.items():
        connection.execute(
            """
            UPDATE gutenberg_candidates
            SET review_status = ?, rejection_reason = ?, imported_quote_id = NULL,
                imported_at = NULL
            WHERE id = ?
            """,
            (status, reason, candidate_id),
        )
    if quote_ids:
        quote_placeholders = ",".join("?" for _ in quote_ids)
        connection.execute(f"DELETE FROM quotes WHERE id IN ({quote_placeholders})", quote_ids)
        connection.execute(
            """
            UPDATE sources SET record_count = (
                SELECT COUNT(*) FROM quote_provenance WHERE source_id = sources.id
            ) WHERE name LIKE 'gutenberg/%'
            """
        )
        for import_run_id in import_run_ids:
            connection.execute(
                """
                UPDATE import_runs SET canonical_inserted = (
                    SELECT COUNT(*) FROM quote_provenance WHERE import_run_id = import_runs.id
                ) WHERE id = ?
                """,
                (import_run_id,),
            )
    connection.commit()
    return len(imported)


def revalidate_gutenberg_candidates(connection: sqlite3.Connection) -> dict[str, int]:
    """Apply current metadata, parser, and prose gates to previously scanned candidates."""
    initialize_database(connection)
    rejected: dict[int, tuple[str, str]] = {}
    metadata_rows = list(
        connection.execute(
            """
            SELECT c.id, c.imported_quote_id, b.eligibility_reason
            FROM gutenberg_candidates AS c JOIN gutenberg_books AS b USING (ebook_id)
            WHERE b.eligibility_status != 'ELIGIBLE'
            """
        )
    )
    for row in metadata_rows:
        rejected[int(row["id"])] = (
            "REJECTED_METADATA",
            f"book excluded by current metadata gate: {row['eligibility_reason']}",
        )

    quality_count = semantic_count = 0
    semantic_confidence: dict[int, str] = {}
    semantic_false_positive: dict[int, str] = {}
    for row in connection.execute(
        """
        SELECT c.* FROM gutenberg_candidates AS c
        JOIN gutenberg_books AS b USING (ebook_id)
        WHERE b.eligibility_status = 'ELIGIBLE'
          AND (
              c.review_status IN ('HIGH_CONFIDENCE', 'DEFERRED_DENSE', 'IMPORTED')
              OR (c.review_status = 'PENDING_REVIEW'
                  AND c.rejection_reason =
                      'current semantic parser no longer validates one exact minute')
          )
        """
    ):
        reason = text_quality_rejection(row["containing_paragraph"], row["quote"])
        if reason:
            rejected[int(row["id"])] = (
                "REJECTED_QUALITY",
                f"current precision audit: {reason}",
            )
            quality_count += 1
            continue
        detections = detect_time_expressions(row["quote"])
        exact_match = any(
            detection.start == row["highlight_start"]
            and detection.end == row["highlight_end"]
            and detection.text == row["time_text"]
            and detection.minute_of_day == row["minute_of_day"]
            and detection.confidence in _EXACT_CONFIDENCE_ENUMS
            for detection in detections
        )
        if not exact_match:
            rejected[int(row["id"])] = (
                "PENDING_REVIEW",
                "current semantic parser no longer validates one exact minute",
            )
            covering = next(
                (
                    detection
                    for detection in detections
                    if detection.start <= row["highlight_start"]
                    and detection.end >= row["highlight_end"]
                ),
                None,
            )
            if covering is not None:
                semantic_confidence[int(row["id"])] = covering.confidence.value
                if (
                    covering.confidence == TimeConfidence.INVALID
                    and covering.rejection_reason
                    and covering.rejection_reason.startswith("false_positive:")
                ):
                    category = covering.rejection_reason.partition(":")[2]
                    semantic_false_positive[int(row["id"])] = category
                    rejected[int(row["id"])] = (
                        "REJECTED_FALSE_POSITIVE",
                        f"false positive: {category}",
                    )
            semantic_count += 1

    # Apply strengthened numeric-reference rules to existing nonduplicate rows.
    # This repairs a resumable scan in place without re-reading every source book.
    pending_false_positive_updates: list[tuple[str, str, str, int]] = []
    pending_rows = list(
        connection.execute(
            """
            SELECT id, quote, time_text, highlight_start, highlight_end
            FROM gutenberg_candidates
            WHERE false_positive_category IS NULL
              AND time_confidence != 'INVALID'
              AND parser_rule IN ('numeric', 'numeric_range')
              AND duplicate_status = 'NEW'
              AND review_status != 'REJECTED_METADATA'
            ORDER BY id
            """
        )
    )
    for row in pending_rows:
        detection = next(
            (
                item
                for item in detect_time_expressions(row["quote"])
                if item.start == row["highlight_start"]
                and item.end == row["highlight_end"]
                and item.text == row["time_text"]
            ),
            None,
        )
        if (
            detection is None
            or detection.confidence != TimeConfidence.INVALID
            or not detection.rejection_reason
            or not detection.rejection_reason.startswith("false_positive:")
        ):
            continue
        category = detection.rejection_reason.partition(":")[2]
        pending_false_positive_updates.append(
            ("INVALID", category, f"false positive: {category}", int(row["id"]))
        )
    connection.executemany(
        """
        UPDATE gutenberg_candidates
        SET time_confidence = ?, false_positive_category = ?,
            review_status = 'REJECTED_FALSE_POSITIVE', rejection_reason = ?
        WHERE id = ?
        """,
        pending_false_positive_updates,
    )

    revoked = _revoke_gutenberg_imports(connection, rejected)
    for candidate_id, confidence in semantic_confidence.items():
        connection.execute(
            "UPDATE gutenberg_candidates SET time_confidence = ? WHERE id = ?",
            (confidence, candidate_id),
        )
    for candidate_id, category in semantic_false_positive.items():
        connection.execute(
            "UPDATE gutenberg_candidates SET false_positive_category = ? WHERE id = ?",
            (category, candidate_id),
        )
    connection.execute(
        """
        UPDATE gutenberg_candidates AS c
        SET review_status = 'HIGH_CONFIDENCE', rejection_reason = NULL
        WHERE c.review_status = 'DEFERRED_DENSE'
          AND c.duplicate_status = 'NEW'
          AND EXISTS (
              SELECT 1 FROM gutenberg_books AS b
              WHERE b.ebook_id = c.ebook_id AND b.eligibility_status = 'ELIGIBLE'
          )
        """
    )
    connection.commit()
    imported = import_gutenberg(connection, target_per_minute=7)
    return {
        "metadata_rejected": len(metadata_rows),
        "quality_reclassified": quality_count,
        "semantic_reclassified": semantic_count,
        "false_positive_reclassified": len(semantic_false_positive)
        + len(pending_false_positive_updates),
        "imports_revoked": revoked,
        "replacement_imports": imported,
    }


def import_gutenberg(connection: sqlite3.Connection, *, target_per_minute: int = 7) -> int:
    """Import only exact, high-confidence, distinct candidates into sparse buckets."""
    if not 1 <= target_per_minute <= 7:
        raise ValueError("target_per_minute must be between 1 and the Phase 2B ceiling of 7")
    initialize_database(connection)
    counts, authors_by_minute, books_by_minute = _coverage_state(connection)
    rows = list(
        connection.execute(
            """
            SELECT c.*, b.text_sha256, b.text_path, b.catalog_sha256
            FROM gutenberg_candidates AS c JOIN gutenberg_books AS b USING (ebook_id)
            WHERE c.review_status = 'HIGH_CONFIDENCE'
              AND c.duplicate_status = 'NEW'
              AND c.minute_of_day IS NOT NULL
              AND c.imported_quote_id IS NULL
              AND b.eligibility_status = 'ELIGIBLE'
              AND b.rights = ?
            ORDER BY c.minute_of_day, c.context_score DESC,
                     c.literary_quality_score DESC, c.id
            """,
            (PUBLIC_DOMAIN_RIGHTS,),
        )
    )
    grouped: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[int(row["minute_of_day"])].append(row)
    ordered: list[sqlite3.Row] = []
    for minute in sorted(grouped, key=lambda value: (counts.get(value, 0), value)):
        remaining = grouped[minute]
        seen_authors = set(authors_by_minute.get(minute, set()))
        seen_books = set(books_by_minute.get(minute, set()))
        while remaining:
            remaining.sort(
                key=lambda candidate: (
                    candidate["author"].casefold() not in seen_authors,
                    candidate["title"].casefold() not in seen_books,
                    float(candidate["context_score"]),
                    float(candidate["literary_quality_score"]),
                    -int(candidate["id"]),
                ),
                reverse=True,
            )
            candidate = remaining.pop(0)
            ordered.append(candidate)
            seen_authors.add(candidate["author"].casefold())
            seen_books.add(candidate["title"].casefold())

    run_cursor = connection.execute(
        "INSERT INTO import_runs (started_at, status) VALUES (?, 'RUNNING')", (_now(),)
    )
    run_id = int(run_cursor.lastrowid)
    duplicate_index = PassageDuplicateIndex(connection, include_gutenberg=False)
    accepted = duplicates = deferred = 0
    for candidate in ordered:
        minute = int(candidate["minute_of_day"])
        if counts.get(minute, 0) >= target_per_minute:
            connection.execute(
                "UPDATE gutenberg_candidates SET review_status = 'DEFERRED_DENSE' WHERE id = ?",
                (candidate["id"],),
            )
            deferred += 1
            continue
        duplicate = duplicate_index.check(candidate["quote"], minute)
        if duplicate.status != "NEW":
            connection.execute(
                """
                UPDATE gutenberg_candidates SET review_status = 'REJECTED_DUPLICATE',
                    duplicate_status = ?, duplicate_of_quote_id = ?,
                    duplicate_of_candidate_id = ?, rejection_reason = ? WHERE id = ?
                """,
                (
                    duplicate.status,
                    duplicate.quote_id,
                    duplicate.candidate_id,
                    f"duplicate at import: {duplicate.status}",
                    candidate["id"],
                ),
            )
            duplicates += 1
            continue
        source_name = f"gutenberg/{candidate['ebook_id']}"
        source_slug = f"gutenberg-{candidate['ebook_id']}"
        imported_at = _now()
        connection.execute(
            """
            INSERT INTO sources (
                name, slug, source_url, source_license, upstream_commit, corpus_path,
                corpus_sha256, record_count, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(name) DO UPDATE SET imported_at = excluded.imported_at
            """,
            (
                source_name,
                source_slug,
                candidate["source_url"],
                SOURCE_LICENSE,
                f"catalog:{candidate['catalog_sha256']}",
                candidate["text_path"],
                candidate["text_sha256"],
                imported_at,
            ),
        )
        source_id = int(
            connection.execute("SELECT id FROM sources WHERE name = ?", (source_name,)).fetchone()[
                0
            ]
        )
        try:
            quote_cursor = connection.execute(
                """
                INSERT INTO quotes (
                    minute_of_day, time_24h, time_text, quote, title, author, sfw, language,
                    source_name, source_url, source_license, source_record_id, quote_hash,
                    normalized_quote_hash, highlight_start, highlight_end,
                    quality_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'en', ?, ?, ?, ?, ?, ?, ?, ?,
                          'VERIFIED_EXACT', ?)
                """,
                (
                    minute,
                    candidate["time_24h"],
                    candidate["time_text"],
                    candidate["quote"],
                    candidate["title"],
                    candidate["author"],
                    source_name,
                    candidate["source_url"],
                    SOURCE_LICENSE,
                    str(candidate["id"]),
                    text_hash(candidate["quote"]),
                    candidate["normalized_quote_hash"],
                    candidate["highlight_start"],
                    candidate["highlight_end"],
                    imported_at,
                ),
            )
        except sqlite3.IntegrityError:
            connection.execute(
                """
                UPDATE gutenberg_candidates SET review_status = 'REJECTED_DUPLICATE',
                    duplicate_status = 'CANONICAL_CONFLICT',
                    rejection_reason = 'canonical uniqueness conflict at import' WHERE id = ?
                """,
                (candidate["id"],),
            )
            duplicates += 1
            continue
        quote_id = int(quote_cursor.lastrowid)
        raw_payload = json.dumps(
            {
                "ebook_id": candidate["ebook_id"],
                "source_locator": candidate["source_locator"],
                "subjects": candidate["subjects"],
                "bookshelves": candidate["bookshelves"],
                "rights": candidate["rights"],
                "parser_rule": candidate["parser_rule"],
                "time_confidence": candidate["time_confidence"],
                "ampm_evidence": candidate["ampm_evidence"],
            },
            ensure_ascii=False,
        )
        connection.execute(
            """
            INSERT INTO quote_provenance (
                quote_id, source_id, import_run_id, source_record_id, raw_time_24h,
                raw_time_text, raw_quote, raw_title, raw_author, raw_sfw, raw_quote_hash,
                validation_status, highlight_start, highlight_end, duplicate_kind, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 'VERIFIED_EXACT', ?, ?,
                      'CANONICAL', ?)
            """,
            (
                quote_id,
                source_id,
                run_id,
                str(candidate["id"]),
                candidate["time_24h"],
                candidate["time_text"],
                candidate["quote"],
                candidate["title"],
                candidate["author"],
                text_hash(candidate["quote"]),
                candidate["highlight_start"],
                candidate["highlight_end"],
                raw_payload,
            ),
        )
        connection.execute(
            """
            UPDATE gutenberg_candidates SET review_status = 'IMPORTED', imported_quote_id = ?,
                imported_at = ? WHERE id = ?
            """,
            (quote_id, imported_at, candidate["id"]),
        )
        connection.execute(
            "UPDATE sources SET record_count = record_count + 1 WHERE id = ?", (source_id,)
        )
        counts[minute] = counts.get(minute, 0) + 1
        duplicate_index.add("LEGACY", quote_id, candidate["quote"], minute)
        accepted += 1
    connection.execute(
        """
        UPDATE import_runs SET finished_at = ?, status = 'COMPLETE', raw_record_count = ?,
            canonical_inserted = ?, exact_duplicates = ? WHERE id = ?
        """,
        (_now(), accepted + duplicates + deferred, accepted, duplicates, run_id),
    )
    connection.commit()
    return accepted


def run_gutenberg_stage(
    connection: sqlite3.Connection,
    project_root: Path,
    *,
    stage: str,
    cumulative_limit: int | None = None,
    workers: int = 4,
    batch_size: int = 250,
    refresh_catalog: bool = False,
    prune_processed_cache: bool = False,
) -> dict[str, Any]:
    """Acquire, scan, validate, and coverage-import one resumable stage."""
    if stage not in DEFAULT_STAGE_LIMITS:
        raise ValueError("stage must be pilot-a, pilot-b, or full")
    if not 1 <= workers <= 12:
        raise ValueError("workers must be between 1 and 12")
    initialize_database(connection)
    generated = project_root / "data" / "generated"
    data_directory = project_root / "data" / "public_domain" / "gutenberg"
    ensure_phase2b_baseline(connection, generated)
    catalog = acquire_catalog(connection, data_directory, refresh=refresh_catalog)
    write_target_expression_set(connection, data_directory / "target_expressions.json")
    limit = DEFAULT_STAGE_LIMITS[stage] if cumulative_limit is None else cumulative_limit
    selected = select_books(connection, cumulative_limit=limit)
    pending = [
        ebook_id
        for ebook_id in selected
        if connection.execute(
            "SELECT processing_status FROM gutenberg_books WHERE ebook_id = ?", (ebook_id,)
        ).fetchone()[0]
        != "PROCESSED"
    ]
    run_cursor = connection.execute(
        """
        INSERT INTO gutenberg_runs (stage, started_at, status, requested_book_limit)
        VALUES (?, ?, 'RUNNING', ?)
        """,
        (stage, _now(), limit),
    )
    run_id = int(run_cursor.lastrowid)
    connection.commit()
    began = time.monotonic()
    totals: Counter[str] = Counter()
    try:
        duplicate_index = PassageDuplicateIndex(connection)
        coverage = _coverage_state(connection)
        for offset in range(0, len(pending), batch_size):
            ids = pending[offset : offset + batch_size]
            acquired = acquire_texts(connection, data_directory, ids)
            totals["books_acquired"] += acquired["acquired"]
            totals["books_skipped"] += acquired["missing"]
            books = _selected_book_rows(connection, ids, data_directory)
            totals.update(
                _scan_books(
                    connection,
                    project_root,
                    books,
                    workers=workers,
                    duplicate_index=duplicate_index,
                    coverage=coverage,
                )
            )
            if prune_processed_cache:
                totals["texts_pruned"] += prune_processed_texts(connection, data_directory, ids)
        precision = validate_stage_precision(connection)
        revalidation = revalidate_gutenberg_candidates(connection)
        imported = revalidation["replacement_imports"]
        validate_import_integrity(connection)
        totals["imported_quotes"] += imported
        runtime = time.monotonic() - began
        connection.execute(
            """
            UPDATE gutenberg_runs SET finished_at = ?, status = 'COMPLETE',
                books_acquired = ?, books_scanned = ?, books_skipped = ?,
                bytes_scanned = ?, words_scanned = ?, characters_scanned = ?,
                expressions_detected = ?, exact_resolved = ?, ambiguous = ?,
                approximate = ?, ranges = ?, invalid = ?, false_positives = ?,
                high_confidence_candidates = ?, duplicates_detected = ?,
                imported_quotes = ?, runtime_seconds = ? WHERE id = ?
            """,
            (
                _now(),
                totals["books_acquired"],
                totals["books_scanned"],
                totals["books_skipped"],
                totals["bytes_scanned"],
                totals["words_scanned"],
                totals["characters_scanned"],
                totals["expressions_detected"],
                sum(totals[value] for value in EXACT_CONFIDENCES),
                totals[TimeConfidence.AMPM_AMBIGUOUS.value],
                totals[TimeConfidence.APPROXIMATE.value],
                totals[TimeConfidence.RANGE.value],
                totals[TimeConfidence.INVALID.value],
                sum(value for key, value in totals.items() if key.startswith("FALSE_")),
                totals["HIGH_CONFIDENCE"],
                totals["duplicates_detected"],
                totals["imported_quotes"],
                runtime,
                run_id,
            ),
        )
        connection.commit()
        export_gutenberg_review(connection, generated / "PHASE2B_REVIEW_PRIORITY.csv")
        write_empty_minute_audit(connection, generated / "PHASE2B_EMPTY_MINUTE_AUDIT.csv")
        export_book_manifest(connection, data_directory)
        report = write_phase2b_report(connection, project_root)
        return {
            "stage": stage,
            "catalog": catalog,
            "selected_books": len(selected),
            "pending_books": len(pending),
            "run": dict(totals),
            "precision": precision,
            "report": str(report),
        }
    except Exception as error:
        connection.execute(
            """
            UPDATE gutenberg_runs SET finished_at = ?, status = 'FAILED', error = ?,
                runtime_seconds = ? WHERE id = ?
            """,
            (_now(), str(error), time.monotonic() - began, run_id),
        )
        connection.commit()
        raise


def run_all_gutenberg_stages(
    connection: sqlite3.Connection,
    project_root: Path,
    *,
    workers: int = 4,
    batch_size: int = 250,
    refresh_catalog: bool = False,
    prune_full_cache: bool = True,
) -> list[dict[str, Any]]:
    """Run both pilots and then the full eligible catalog without a manual gate."""
    results = []
    for index, stage in enumerate(("pilot-a", "pilot-b", "full")):
        results.append(
            run_gutenberg_stage(
                connection,
                project_root,
                stage=stage,
                workers=workers,
                batch_size=batch_size,
                refresh_catalog=refresh_catalog and index == 0,
                prune_processed_cache=prune_full_cache and stage == "full",
            )
        )
    return results


def export_gutenberg_review(connection: sqlite3.Connection, output: Path) -> int:
    """Export unresolved candidates only when either possible bucket is below three."""
    counts = _coverage_state(connection)[0]
    rows = []
    for candidate in connection.execute(
        """
        SELECT * FROM gutenberg_candidates
        WHERE review_status = 'PENDING_REVIEW'
          AND time_confidence = 'AMPM_AMBIGUOUS'
          AND duplicate_status = 'NEW'
        """
    ):
        possibilities = [
            int(value)
            for value in (candidate["possible_minute_am"], candidate["possible_minute_pm"])
            if value is not None
        ]
        sparse = [value for value in possibilities if counts.get(value, 0) < 3]
        if not sparse:
            continue
        rows.append(
            {
                "possible_minutes": "|".join(minute_to_time(value) for value in possibilities),
                "current_counts": "|".join(str(counts.get(value, 0)) for value in possibilities),
                "minimum_bucket_count": min(counts.get(value, 0) for value in possibilities),
                "time_phrase": candidate["time_text"],
                "quote": candidate["quote"],
                "containing_paragraph": candidate["containing_paragraph"],
                "previous_paragraph": candidate["previous_paragraph"],
                "following_paragraph": candidate["following_paragraph"],
                "title": candidate["title"],
                "author": candidate["author"],
                "gutenberg_id": candidate["ebook_id"],
                "subjects": candidate["subjects"],
                "bookshelves": candidate["bookshelves"],
                "parser_status": candidate["time_confidence"],
                "ampm_evidence": candidate["ampm_evidence"],
                "source_locator": candidate["source_locator"],
                "rejection_reason": candidate["rejection_reason"],
                "target_priority": candidate["target_priority"],
            }
        )
    rows.sort(
        key=lambda row: (
            int(row["minimum_bucket_count"]),
            -float(row["target_priority"]),
            int(row["gutenberg_id"]),
            str(row["source_locator"]),
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = (
        list(rows[0])
        if rows
        else [
            "possible_minutes",
            "current_counts",
            "minimum_bucket_count",
            "time_phrase",
            "quote",
            "containing_paragraph",
            "previous_paragraph",
            "following_paragraph",
            "title",
            "author",
            "gutenberg_id",
            "subjects",
            "bookshelves",
            "parser_status",
            "ampm_evidence",
            "source_locator",
            "rejection_reason",
            "target_priority",
        ]
    )
    temporary = output.with_suffix(".csv.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, output)
    return len(rows)


def _empty_occurrence_category(row: sqlite3.Row) -> str:
    if row["imported_quote_id"] is not None:
        return "EXACT_IMPORTED"
    if row["false_positive_category"]:
        return "CITATION_OR_REFERENCE_FALSE_POSITIVE"
    if row["duplicate_status"] != "NEW":
        return "DUPLICATE"
    if row["time_confidence"] == TimeConfidence.AMPM_AMBIGUOUS.value:
        return "AMPM_AMBIGUOUS"
    if row["time_confidence"] == TimeConfidence.APPROXIMATE.value:
        return "APPROXIMATE"
    if row["time_confidence"] == TimeConfidence.RANGE.value:
        return "RANGE"
    if row["review_status"] == "REJECTED_QUALITY":
        return "CONTEXT_QUALITY_FAILURE"
    if row["minute_of_day"] is not None:
        return "EXACT_NOT_IMPORTED"
    return "OTHER_REJECTION"


def write_empty_minute_audit(connection: sqlite3.Connection, output: Path) -> int:
    rows: list[dict[str, object]] = []
    placeholders = ",".join("?" for _ in _EMPTY_MINUTES)
    parameters = tuple(sorted(_EMPTY_MINUTES)) * 3
    candidates = connection.execute(
        f"""
        SELECT * FROM gutenberg_candidates
        WHERE minute_of_day IN ({placeholders})
           OR possible_minute_am IN ({placeholders})
           OR possible_minute_pm IN ({placeholders})
        ORDER BY ebook_id, source_expression_start
        """,
        parameters,
    )
    for row in candidates:
        targets = sorted(
            _EMPTY_MINUTES
            & {
                value
                for value in (
                    row["minute_of_day"],
                    row["possible_minute_am"],
                    row["possible_minute_pm"],
                )
                if value is not None
            }
        )
        rows.append(
            {
                "target_minutes": "|".join(minute_to_time(value) for value in targets),
                "category": _empty_occurrence_category(row),
                "time_phrase": row["time_text"],
                "quote": row["quote"],
                "title": row["title"],
                "author": row["author"],
                "gutenberg_id": row["ebook_id"],
                "source_locator": row["source_locator"],
                "parser_status": row["time_confidence"],
                "rejection_reason": row["rejection_reason"],
            }
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "target_minutes",
        "category",
        "time_phrase",
        "quote",
        "title",
        "author",
        "gutenberg_id",
        "source_locator",
        "parser_status",
        "rejection_reason",
    ]
    temporary = output.with_suffix(".csv.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, output)
    return len(rows)


def calculate_gutenberg_stats(connection: sqlite3.Connection, project_root: Path) -> dict[str, Any]:
    initialize_database(connection)
    coverage = calculate_stats(connection)
    candidate_confidence = {
        str(row["time_confidence"]): int(row["n"])
        for row in connection.execute(
            """
            SELECT time_confidence, COUNT(*) AS n FROM gutenberg_candidates
            GROUP BY time_confidence
            """
        )
    }
    observed_false_positives = {
        str(row["false_positive_category"]): int(row["n"])
        for row in connection.execute(
            """
            SELECT false_positive_category, COUNT(*) AS n FROM gutenberg_candidates
            WHERE false_positive_category IS NOT NULL GROUP BY false_positive_category
            ORDER BY n DESC, false_positive_category
            """
        )
    }
    false_positives = {
        category: observed_false_positives.get(category, 0)
        for category in _FALSE_POSITIVE_CATEGORIES
    }
    false_positives.update(
        {
            category: count
            for category, count in observed_false_positives.items()
            if category not in false_positives
        }
    )
    total_candidates = int(
        connection.execute("SELECT COUNT(*) FROM gutenberg_candidates").fetchone()[0]
    )
    candidate_disposition = {
        str(row["review_status"]): int(row["n"])
        for row in connection.execute(
            """
            SELECT review_status, COUNT(*) AS n FROM gutenberg_candidates
            GROUP BY review_status ORDER BY review_status
            """
        )
    }
    exact = sum(candidate_confidence.get(value, 0) for value in EXACT_CONFIDENCES)
    high = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM gutenberg_candidates
            WHERE review_status IN ('HIGH_CONFIDENCE', 'IMPORTED', 'DEFERRED_DENSE')
            """
        ).fetchone()[0]
    )
    duplicates = int(
        connection.execute(
            "SELECT COUNT(*) FROM gutenberg_candidates WHERE duplicate_status != 'NEW'"
        ).fetchone()[0]
    )
    cross_source_duplicates = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM gutenberg_candidates
            WHERE duplicate_of_quote_id IS NOT NULL
               OR duplicate_of_candidate_id IS NOT NULL
               OR duplicate_status = 'CANONICAL_CONFLICT'
            """
        ).fetchone()[0]
    )
    within_gutenberg_duplicates = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM gutenberg_candidates
            WHERE duplicate_of_gutenberg_candidate_id IS NOT NULL
            """
        ).fetchone()[0]
    )
    imported = int(
        connection.execute(
            "SELECT COUNT(*) FROM gutenberg_candidates WHERE imported_quote_id IS NOT NULL"
        ).fetchone()[0]
    )
    book_totals = dict(
        connection.execute(
            """
            SELECT
                COUNT(*) AS catalog_size,
                SUM(eligibility_status = 'ELIGIBLE') AS eligible,
                SUM(processing_status = 'PROCESSED') AS scanned,
                SUM(eligibility_status = 'ELIGIBLE' AND processing_status = 'PROCESSED')
                    AS eligible_scanned,
                COALESCE(SUM(CASE WHEN processing_status = 'PROCESSED' THEN byte_count END), 0)
                    AS bytes,
                COALESCE(SUM(CASE WHEN processing_status = 'PROCESSED' THEN word_count END), 0)
                    AS words,
                COALESCE(SUM(CASE WHEN processing_status = 'PROCESSED' THEN character_count END), 0)
                    AS characters
            FROM gutenberg_books
            """
        ).fetchone()
    )
    eligible_processing_status = {
        str(row["processing_status"]): int(row["n"])
        for row in connection.execute(
            """
            SELECT processing_status, COUNT(*) AS n FROM gutenberg_books
            WHERE eligibility_status = 'ELIGIBLE'
            GROUP BY processing_status ORDER BY processing_status
            """
        )
    }
    baseline_path = project_root / "data" / "generated" / "phase2b_baseline.json"
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        before_coverage = {
            int(row["minute_of_day"]): int(row["renderable_count"])
            for row in baseline["minute_coverage"]
        }
        before_selectable = int(baseline["selectable_quotes"])
    else:
        before_coverage = {minute: 0 for minute in range(1440)}
        before_selectable = int(coverage["total_renderable_quotes"]) - imported
    after_coverage = {
        int(row["minute_of_day"]): int(row["renderable_count"])
        for row in coverage["minute_coverage"]
    }
    raised = {
        threshold: sum(
            before_coverage.get(minute, 0) < threshold <= after_coverage.get(minute, 0)
            for minute in range(1440)
        )
        for threshold in (1, 3, 5, 7)
    }
    source_contribution = {
        "project_gutenberg": int(
            connection.execute(
                "SELECT COUNT(*) FROM quotes WHERE source_name LIKE 'gutenberg/%'"
            ).fetchone()[0]
        ),
        "standard_ebooks": int(
            connection.execute(
                "SELECT COUNT(*) FROM quotes WHERE source_name LIKE 'standardebooks/%'"
            ).fetchone()[0]
        ),
    }
    source_contribution["legacy"] = (
        int(coverage["total_canonical_quotes"])
        - source_contribution["project_gutenberg"]
        - source_contribution["standard_ebooks"]
    )
    gutenberg_diversity = dict(
        connection.execute(
            """
            SELECT COUNT(DISTINCT author) AS authors, COUNT(DISTINCT title) AS books
            FROM quotes WHERE source_name LIKE 'gutenberg/%'
            """
        ).fetchone()
    )
    top_authors = [
        dict(row)
        for row in connection.execute(
            """
            SELECT author, COUNT(*) AS imported_quotes, COUNT(DISTINCT title) AS books
            FROM quotes WHERE source_name LIKE 'gutenberg/%'
            GROUP BY author ORDER BY imported_quotes DESC, author LIMIT 20
            """
        )
    ]
    top_books = [
        dict(row)
        for row in connection.execute(
            """
            SELECT title, author, COUNT(*) AS imported_quotes
            FROM quotes WHERE source_name LIKE 'gutenberg/%'
            GROUP BY title, author ORDER BY imported_quotes DESC, title LIMIT 20
            """
        )
    ]
    completed_runs = [
        dict(row)
        for row in connection.execute(
            """
            SELECT stage, requested_book_limit, books_scanned, expressions_detected,
                   high_confidence_candidates, duplicates_detected, imported_quotes,
                   runtime_seconds
            FROM gutenberg_runs
            WHERE status = 'COMPLETE'
            ORDER BY id
            """
        )
    ]
    processing_failures = [
        dict(row)
        for row in connection.execute(
            """
            SELECT ebook_id, title, authors, error FROM gutenberg_books
            WHERE eligibility_status = 'ELIGIBLE' AND processing_status = 'PROCESSING_FAILED'
            ORDER BY ebook_id
            """
        )
    ]
    hardest = sorted(
        (
            {
                "minute": minute_to_time(minute),
                "count": after_coverage.get(minute, 0),
                "deficit": max(0, 7 - after_coverage.get(minute, 0)),
            }
            for minute in range(1440)
        ),
        key=lambda row: (-int(row["deficit"]), str(row["minute"])),
    )[:50]
    empty_audit = {
        minute_to_time(minute): dict(
            Counter(
                _empty_occurrence_category(row)
                for row in connection.execute(
                    """
                    SELECT * FROM gutenberg_candidates
                    WHERE minute_of_day = ? OR possible_minute_am = ? OR possible_minute_pm = ?
                    """,
                    (minute, minute, minute),
                )
            )
        )
        for minute in sorted(_EMPTY_MINUTES)
    }
    return {
        "catalog_size": int(book_totals["catalog_size"] or 0),
        "eligible_books": int(book_totals["eligible"] or 0),
        "books_scanned": int(book_totals["scanned"] or 0),
        "eligible_books_scanned": int(book_totals["eligible_scanned"] or 0),
        "bytes_scanned": int(book_totals["bytes"] or 0),
        "words_scanned": int(book_totals["words"] or 0),
        "characters_scanned": int(book_totals["characters"] or 0),
        "eligible_processing_status": eligible_processing_status,
        "raw_time_expressions": total_candidates,
        "exact_resolved_candidates": exact,
        "high_confidence_candidates": high,
        "ambiguous_candidates": candidate_confidence.get(TimeConfidence.AMPM_AMBIGUOUS.value, 0),
        "approximate_candidates": candidate_confidence.get(TimeConfidence.APPROXIMATE.value, 0),
        "range_candidates": candidate_confidence.get(TimeConfidence.RANGE.value, 0),
        "invalid_candidates": candidate_confidence.get(TimeConfidence.INVALID.value, 0),
        "candidate_disposition": candidate_disposition,
        "false_positives": false_positives,
        "duplicate_candidates_total": duplicates,
        "duplicates_against_existing": cross_source_duplicates,
        "duplicates_within_gutenberg": within_gutenberg_duplicates,
        "imported_quotes": imported,
        "before_selectable_quotes": before_selectable,
        "newly_covered_minutes": raised[1],
        "buckets_raised_to_3": raised[3],
        "buckets_raised_to_5": raised[5],
        "buckets_raised_to_7": raised[7],
        "source_contribution": source_contribution,
        "gutenberg_unique_authors": int(gutenberg_diversity["authors"] or 0),
        "gutenberg_unique_books": int(gutenberg_diversity["books"] or 0),
        "coverage": coverage,
        "hardest_minutes": hardest,
        "empty_minute_audit": empty_audit,
        "top_authors": top_authors,
        "top_books": top_books,
        "completed_runs": completed_runs,
        "processing_failures": processing_failures,
    }


def write_phase2b_report(connection: sqlite3.Connection, project_root: Path) -> Path:
    generated = project_root / "data" / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    stats = calculate_gutenberg_stats(connection, project_root)
    write_target_expression_set(
        connection,
        project_root / "data" / "public_domain" / "gutenberg" / "target_expressions.json",
    )
    import_integrity = validate_import_integrity(connection)
    coverage = stats["coverage"]
    thresholds = coverage["minute_thresholds"]
    percentiles = coverage["percentiles"]
    remaining_deficit = sum(
        max(0, 7 - int(row["renderable_count"])) for row in coverage["minute_coverage"]
    )
    review_count = export_gutenberg_review(connection, generated / "PHASE2B_REVIEW_PRIORITY.csv")
    empty_count = write_empty_minute_audit(connection, generated / "PHASE2B_EMPTY_MINUTE_AUDIT.csv")
    false_lines = [f"- {key}: {value:,}" for key, value in stats["false_positives"].items()] or [
        "- None recorded"
    ]
    disposition_lines = [
        f"- {key}: {value:,}" for key, value in stats["candidate_disposition"].items()
    ]
    empty_lines = []
    for minute, categories in stats["empty_minute_audit"].items():
        current = next(
            int(row["renderable_count"])
            for row in coverage["minute_coverage"]
            if row["time_24h"] == minute
        )
        description = ", ".join(f"{key}={value}" for key, value in sorted(categories.items()))
        empty_lines.append(
            f"- {minute}: selectable={current}; {description or 'no occurrence found'}"
        )
    hardest_lines = [
        f"{index}. {row['minute']} — {row['count']} selectable, deficit {row['deficit']}"
        for index, row in enumerate(stats["hardest_minutes"], 1)
    ]
    author_lines = [
        f"- {row['author']}: {row['imported_quotes']} quotes across {row['books']} books"
        for row in stats["top_authors"]
    ] or ["- No Gutenberg imports"]
    book_lines = [
        f"- {row['title']} — {row['author']}: {row['imported_quotes']} quotes"
        for row in stats["top_books"]
    ] or ["- No Gutenberg imports"]
    run_lines = [
        "| "
        + " | ".join(
            (
                str(row["stage"]),
                f"{int(row['books_scanned']):,}",
                f"{int(row['expressions_detected']):,}",
                f"{int(row['high_confidence_candidates']):,}",
                f"{int(row['duplicates_detected']):,}",
                f"{int(row['imported_quotes']):,}",
                f"{float(row['runtime_seconds']) / 60:.1f} min",
            )
        )
        + " |"
        for row in stats["completed_runs"]
    ] or ["| None | 0 | 0 | 0 | 0 | 0 | 0.0 min |"]
    failure_lines = [
        f"- PG {row['ebook_id']}, {row['title']} — {row['authors']}: {row['error']}"
        for row in stats["processing_failures"]
    ] or ["- None"]
    recommendation = (
        "Coverage-driven Project Gutenberg importing has reached the seven-per-minute target; "
        "Phase 2C should focus on human quality review and diversity."
        if thresholds["below_7"] == 0
        else "Project Gutenberg alone is insufficient to reach seven verified quotes for every "
        "minute. Phase 2C should first review the exported AM/PM queue for the sparsest buckets, "
        "then add another independent public-domain corpus for the remaining deficits."
    )
    report = f"""# Literary Clock Phase 2B Report

Generated: {_now()}

## Acquisition and eligibility

- Method: official compressed Project Gutenberg CSV and RDF bulk catalogs, followed by batched
  selective retrieval of generated UTF-8 plain text from official rsync mirrors.
- Normal `www.gutenberg.org` ebook pages crawled: **0**
- Catalog records: **{stats["catalog_size"]:,}**
- Eligible English literary books: **{stats["eligible_books"]:,}**
- Texts scanned before the final metadata audit: **{stats["books_scanned"]:,}**
- Final eligible books scanned: **{stats["eligible_books_scanned"]:,}**
- Eligible books without an official generated UTF-8 text:
  **{stats["eligible_processing_status"].get("MISSING_TEXT", 0):,}**
- Eligible books that failed text processing:
  **{stats["eligible_processing_status"].get("PROCESSING_FAILED", 0):,}**
- Bytes / words / characters scanned: **{stats["bytes_scanned"]:,} / {stats["words_scanned"]:,} /
  {stats["characters_scanned"]:,}**
- Rights rule: exact Project Gutenberg metadata assertion “{PUBLIC_DOMAIN_RIGHTS}” only.

The monolithic text archive was not duplicated locally. Selective official rsync used at most one
compressed stream per configured official mirror. The pilot cache was retained; processed full-run
texts were pruned only after checksums and candidates were committed. The database is the restart
manifest, and a JSONL manifest is exported after each completed stage.

### Staged rollout

| Stage | New books | Expressions | High confidence | Duplicates | Initial imports | Runtime |
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(run_lines)}

### Processing failures

{chr(10).join(failure_lines)}

## Detection and correctness accounting

- Raw time expressions: **{stats["raw_time_expressions"]:,}**
- Exact resolved candidates: **{stats["exact_resolved_candidates"]:,}**
- High-confidence candidates: **{stats["high_confidence_candidates"]:,}**
- AM/PM ambiguous candidates: **{stats["ambiguous_candidates"]:,}**
- Approximate candidates: **{stats["approximate_candidates"]:,}**
- Range candidates: **{stats["range_candidates"]:,}**
- Invalid candidates: **{stats["invalid_candidates"]:,}**
- Duplicates against the pre-existing canonical/Standard Ebooks corpus:
  **{stats["duplicates_against_existing"]:,}**
- Duplicates of earlier Gutenberg candidates: **{stats["duplicates_within_gutenberg"]:,}**
- Duplicate candidates total: **{stats["duplicate_candidates_total"]:,}**
- Automatically imported Gutenberg quotes: **{stats["imported_quotes"]:,}**
- Prioritized human-review records: **{review_count:,}**
- Import-integrity failures (fields / provenance / cap):
  **{import_integrity["field_mismatches"]} / {import_integrity["provenance_failures"]} /
  {import_integrity["imports_above_cap"]}**

### Candidate disposition

{chr(10).join(disposition_lines)}

The disposition rows are mutually exclusive and sum to the raw candidate total. Duplicate and
false-positive diagnostics below are orthogonal flags, so their totals need not equal one primary
disposition row.

### False positives rejected by category

{chr(10).join(false_lines)}

## Coverage effect

- Selectable corpus before Phase 2B: **{stats["before_selectable_quotes"]:,}**
- Selectable corpus now: **{coverage["total_renderable_quotes"]:,}**
- Newly covered minutes: **{stats["newly_covered_minutes"]:,}**
- Buckets raised to 3 / 5 / 7: **{stats["buckets_raised_to_3"]:,} /
  {stats["buckets_raised_to_5"]:,} / {stats["buckets_raised_to_7"]:,}**
- Minutes at 0: **{thresholds["zero"]:,}**
- Minutes below 3: **{thresholds["below_3"]:,}**
- Minutes below 5: **{thresholds["below_5"]:,}**
- Minutes below 7: **{thresholds["below_7"]:,}**
- Minutes at least 7: **{thresholds["at_least_7"]:,}**
- Median selectable quotes/minute: **{coverage["median_quotes_per_minute"]:.2f}**
- P10 / P25 / P75 / P90: **{percentiles["p10"]:.2f} / {percentiles["p25"]:.2f} /
  {percentiles["p75"]:.2f} / {percentiles["p90"]:.2f}**
- Remaining deficit to seven everywhere: **{remaining_deficit:,}**

### Contribution by corpus

- Legacy literary-clock corpora: **{stats["source_contribution"]["legacy"]:,}**
- Standard Ebooks: **{stats["source_contribution"]["standard_ebooks"]:,}**
- Project Gutenberg: **{stats["source_contribution"]["project_gutenberg"]:,}**
- Gutenberg contribution diversity: **{stats["gutenberg_unique_authors"]:,} authors /
  {stats["gutenberg_unique_books"]:,} books**
- Whole selectable corpus diversity: **{coverage["unique_authors"]:,} authors /
  {coverage["unique_books"]:,} books**

## Empty-minute audit

The audit includes **{empty_count:,}** candidate occurrences and does not force AM/PM.

{chr(10).join(empty_lines)}

## 50 hardest remaining buckets

{chr(10).join(hardest_lines)}

## Gutenberg contribution by author

{chr(10).join(author_lines)}

## Gutenberg contribution by book

{chr(10).join(book_lines)}

## Recommendation for Phase 2C

{recommendation}
"""
    path = generated / "PHASE2B_REPORT.md"
    temporary = path.with_suffix(".md.tmp")
    temporary.write_text(report, encoding="utf-8")
    os.replace(temporary, path)
    machine_path = generated / "phase2b_stats.json"
    machine_payload = {key: value for key, value in stats.items() if key != "coverage"}
    machine_path.write_text(
        json.dumps(machine_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_reports(coverage, generated)
    return path
