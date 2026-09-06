"""Phase 2C clock-face semantics, counterfactual planning, activation, and reporting."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from litclock.db import backfill_primary_eligibility, initialize_database
from litclock.gutenberg import PUBLIC_DOMAIN_RIGHTS
from litclock.gutenberg_text import text_quality_rejection
from litclock.models import EligibilityType, TimeConfidence, TimeSemantics
from litclock.normalize import minute_to_time, text_hash
from litclock.phase2a5 import (
    ContextResolution,
    _quality_acceptable,
    _SourceContextCache,
    parse_ambiguous_options,
    resolve_contextual_ampm,
)
from litclock.standard_ebooks import SOURCE_LICENSE as STANDARD_EBOOKS_LICENSE
from litclock.stats import calculate_stats, write_reports
from litclock.timeparse import detect_time_expressions
from litclock.xhtml import sentence_spans

_RENDERABLE = ("VERIFIED_EXACT", "VERIFIED_NORMALIZED")
_SOURCE_STANDARD_EBOOKS = "STANDARD_EBOOKS"
_SOURCE_GUTENBERG = "GUTENBERG"
_SOURCE_LEGACY = "LEGACY_CANONICAL"
_CLOCKFACE_DECISIONS = {"DUAL_ELIGIBLE", "CONTEXT_RESOLVED"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class AuditRecord:
    source_candidate_type: str
    source_candidate_id: int
    possible_minute_am: int | None
    possible_minute_pm: int | None
    parser_rule: str | None
    parser_result: str
    time_semantics: str
    non_ampm_gate_status: str
    gate_reason: str | None
    displayed_resolution: int | None
    displayed_evidence_type: str | None
    displayed_evidence_text: str | None
    source_resolution: int | None
    source_evidence_type: str | None
    source_evidence_text: str | None
    decision: str
    review_reason: str | None
    context_score: float
    literary_quality_score: float
    author: str
    title: str
    time_text: str
    source_locator: str | None
    duplicate_status: str
    canonical_quote_id: int | None


def _matching_detection(
    quote: str, start: int | None, end: int | None
) -> tuple[Any | None, str | None]:
    if start is None or end is None or not 0 <= start < end <= len(quote):
        return None, "missing or invalid exact highlight offsets"
    matches = [
        detection
        for detection in detect_time_expressions(quote)
        if detection.start == start and detection.end == end
    ]
    if len(matches) != 1:
        return None, "highlight does not identify exactly one current-parser detection"
    return matches[0], None


def _neutral_resolution() -> ContextResolution:
    return ContextResolution(None, None, None, None, "UNRESOLVED")


def _requires_context_review(resolution: ContextResolution) -> bool:
    """Distinguish contradictory evidence from non-authoritative narrative hints."""
    proposed = (resolution.proposed_evidence or "").casefold()
    return proposed.startswith("conflicting daypart cues")


_GREETING_DAYPART_RE = re.compile(
    r"\b(?P<cue>good\s+morning|good\s+afternoon|buenos\s+d[ií]as|buenas\s+tardes)\b",
    re.IGNORECASE,
)


def _clockface_value(options: tuple[int, int]) -> int:
    return options[0] % 720


def _explicit_greeting_timeline_resolution(
    paragraph: str,
    expression_start: int,
    options: tuple[int, int],
    locator: str,
) -> ContextResolution | None:
    """Propagate a daypart only through an auditable local clock-time sequence.

    A greeting by itself is narrative color, not a daypart resolver. It becomes an
    anchor only when its own sentence contains an exact unresolved clock time and the
    target is a later member of a monotonic sequence of at least three clock times in
    the same paragraph. This covers diary-like timestamp sequences without relying on
    narrative intuition.
    """
    target_value = _clockface_value(options)
    detections: list[tuple[Any, tuple[int, int]]] = []
    for detection in detect_time_expressions(paragraph):
        if detection.confidence != TimeConfidence.AMPM_AMBIGUOUS:
            continue
        detection_options = parse_ambiguous_options(detection.ampm_evidence)
        if detection_options is not None:
            detections.append((detection, detection_options))

    target = next(
        (
            detection
            for detection, detection_options in detections
            if detection.start == expression_start
            and _clockface_value(detection_options) == target_value
        ),
        None,
    )
    if target is None:
        return None

    spans = sentence_spans(paragraph)
    for cue in _GREETING_DAYPART_RE.finditer(paragraph):
        sentence_span = next(
            ((start, end) for start, end in spans if start <= cue.start() and cue.end() <= end),
            None,
        )
        if sentence_span is None:
            continue
        sentence_start, sentence_end = sentence_span
        anchors = [
            (detection, detection_options)
            for detection, detection_options in detections
            if sentence_start <= detection.start < cue.start() and detection.end <= sentence_end
        ]
        if not anchors:
            continue
        anchor, anchor_options = anchors[-1]
        if target.start <= anchor.start:
            continue
        anchor_value = _clockface_value(anchor_options)
        target_delta = (target_value - anchor_value) % 720
        if not 0 < target_delta <= 180:
            continue
        preceding = [
            (detection, detection_options)
            for detection, detection_options in detections
            if detection.start <= target.start
        ]
        sequence: list[tuple[Any, tuple[int, int]]] = []
        for index in range(len(preceding)):
            possible = preceding[index:]
            if len(possible) < 3 or all(item[0] is not anchor for item in possible):
                continue
            first_value = _clockface_value(possible[0][1])
            deltas = [
                (_clockface_value(detection_options) - first_value) % 720
                for _, detection_options in possible
            ]
            if deltas == sorted(deltas) and deltas[-1] <= 180:
                sequence = possible
                break
        if not sequence:
            continue
        cue_text = cue.group("cue")
        normalized_cue = cue_text.casefold()
        meridiem = (
            "AM"
            if "morning" in normalized_cue or "días" in normalized_cue or "dias" in normalized_cue
            else "PM"
        )
        resolved = options[0] if meridiem == "AM" else options[1]
        expressions = ", ".join(detection.text for detection, _ in sequence)
        return ContextResolution(
            resolved,
            "SOURCE_GREETING_MONOTONIC_TIME_SEQUENCE",
            f"{cue_text!r} anchors {anchor.text!r}; monotonic local sequence: {expressions}",
            locator,
            "DETERMINISTIC",
        )
    return None


def _resolve_in_context(
    paragraph: str,
    start: int,
    end: int,
    options: tuple[int, int],
    *,
    previous: str = "",
    following: str = "",
    locator: str = "",
) -> ContextResolution:
    """Use the shared parser first, then the stricter neighboring-context resolver."""
    direct = next(
        (
            detection
            for detection in detect_time_expressions(paragraph)
            if detection.start == start
            and detection.end >= end
            and detection.minute_of_day in options
            and detection.confidence
            in {
                TimeConfidence.EXACT_AM,
                TimeConfidence.EXACT_PM,
                TimeConfidence.EXACT_CONTEXTUAL,
            }
        ),
        None,
    )
    if direct is not None:
        return ContextResolution(
            int(direct.minute_of_day),
            "SOURCE_PARSER_CONTEXT",
            direct.ampm_evidence or direct.text,
            locator,
            "DETERMINISTIC",
        )
    timeline = _explicit_greeting_timeline_resolution(paragraph, start, options, locator)
    if timeline is not None:
        return timeline
    return resolve_contextual_ampm(
        paragraph,
        start,
        end,
        options,
        previous_paragraph=previous,
        following_paragraph=following,
        source_locator=locator,
    )


def semantic_display_minutes(
    text: str,
    *,
    source_context: tuple[str, int, int, str, str] | None = None,
) -> tuple[int, ...]:
    """Return exact display minutes under the Phase 2C semantic policy.

    An empty tuple means the expression is approximate, invalid, a range, conflicting,
    or otherwise not automatically displayable.
    """
    detections = detect_time_expressions(text)
    if len(detections) != 1:
        return ()
    detection = detections[0]
    if detection.minute_of_day is not None and detection.confidence in {
        TimeConfidence.EXACT_24H,
        TimeConfidence.EXACT_AM,
        TimeConfidence.EXACT_PM,
        TimeConfidence.EXACT_CONTEXTUAL,
    }:
        return (int(detection.minute_of_day),)
    if detection.confidence != TimeConfidence.AMPM_AMBIGUOUS:
        return ()
    options = parse_ambiguous_options(detection.ampm_evidence)
    if options is None:
        return ()
    displayed = _resolve_in_context(text, detection.start, detection.end, options)
    source = _neutral_resolution()
    if source_context is not None:
        paragraph, start, end, previous, following = source_context
        source = _resolve_in_context(
            paragraph,
            start,
            end,
            options,
            previous=previous,
            following=following,
        )
    if (
        displayed.minute_of_day is not None
        and source.minute_of_day is not None
        and displayed.minute_of_day != source.minute_of_day
    ):
        return ()
    resolved = source.minute_of_day
    if resolved is None:
        resolved = displayed.minute_of_day
    if resolved is not None:
        return (int(resolved),)
    if _requires_context_review(displayed) or _requires_context_review(source):
        return ()
    return options


def _assess_clockface(
    *,
    source_candidate_type: str,
    source_candidate_id: int,
    quote: str,
    time_text: str,
    highlight_start: int | None,
    highlight_end: int | None,
    author: str,
    title: str,
    source_locator: str | None,
    duplicate_status: str,
    canonical_quote_id: int | None,
    context_score: float,
    literary_quality_score: float,
    gate_reason: str | None,
    source_context: tuple[str, int, int, str, str] | None,
) -> AuditRecord:
    detection, detection_error = _matching_detection(quote, highlight_start, highlight_end)
    options = (
        parse_ambiguous_options(detection.ampm_evidence)
        if detection is not None and detection.confidence == TimeConfidence.AMPM_AMBIGUOUS
        else None
    )
    displayed = _neutral_resolution()
    source = _neutral_resolution()
    if detection is not None and options is not None:
        displayed = _resolve_in_context(
            quote,
            int(highlight_start),
            int(highlight_end),
            options,
            locator=source_locator or "",
        )
        if source_context is not None:
            paragraph, start, end, previous, following = source_context
            source = _resolve_in_context(
                paragraph,
                start,
                end,
                options,
                previous=previous,
                following=following,
                locator=source_locator or "",
            )

    failure = gate_reason or detection_error
    if detection is not None and detection.confidence != TimeConfidence.AMPM_AMBIGUOUS:
        failure = failure or (
            "current parser classifies expression as "
            f"{detection.confidence.value}, not an exact unresolved clock-face time"
        )
    if options is None:
        failure = failure or "current parser does not provide two exact AM/PM alternatives"
    if duplicate_status != "NEW" and canonical_quote_id is None:
        failure = failure or f"duplicate passage: {duplicate_status}"

    review_reason: str | None = None
    if failure:
        decision = "REJECTED"
        gate_status = "FAIL"
    elif source_context is None and source_candidate_type != _SOURCE_LEGACY:
        decision = "CONTEXT_REVIEW"
        gate_status = "PASS"
        review_reason = "trusted surrounding source context is unavailable"
    elif (
        displayed.minute_of_day is not None
        and source.minute_of_day is not None
        and displayed.minute_of_day != source.minute_of_day
    ):
        decision = "CONTEXT_REVIEW"
        gate_status = "PASS"
        review_reason = "displayed text and source context imply conflicting dayparts"
    else:
        resolved = source.minute_of_day
        if resolved is None:
            resolved = displayed.minute_of_day
        if resolved is not None:
            decision = "CONTEXT_RESOLVED"
            gate_status = "PASS"
        else:
            proposed = source.proposed_evidence or displayed.proposed_evidence
            if _requires_context_review(source) or _requires_context_review(displayed):
                decision = "CONTEXT_REVIEW"
                gate_status = "PASS"
                review_reason = proposed
            else:
                decision = "DUAL_ELIGIBLE"
                gate_status = "PASS"

    return AuditRecord(
        source_candidate_type=source_candidate_type,
        source_candidate_id=source_candidate_id,
        possible_minute_am=options[0] if options else None,
        possible_minute_pm=options[1] if options else None,
        parser_rule=detection.parser_rule if detection is not None else None,
        parser_result=detection.confidence.value if detection is not None else "NO_EXACT_DETECTION",
        time_semantics=(
            TimeSemantics.CLOCKFACE_12H.value if options else TimeSemantics.INVALID.value
        ),
        non_ampm_gate_status=gate_status,
        gate_reason=failure,
        displayed_resolution=displayed.minute_of_day,
        displayed_evidence_type=displayed.evidence_type,
        displayed_evidence_text=displayed.evidence_text or displayed.proposed_evidence,
        source_resolution=source.minute_of_day,
        source_evidence_type=source.evidence_type,
        source_evidence_text=source.evidence_text or source.proposed_evidence,
        decision=decision,
        review_reason=review_reason,
        context_score=context_score,
        literary_quality_score=literary_quality_score,
        author=author,
        title=title,
        time_text=time_text,
        source_locator=source_locator,
        duplicate_status=duplicate_status,
        canonical_quote_id=canonical_quote_id,
    )


def _standard_ebooks_audits(
    connection: sqlite3.Connection, project_root: Path
) -> list[AuditRecord]:
    cache = _SourceContextCache(project_root)
    records: list[AuditRecord] = []
    rows = connection.execute(
        """
        SELECT c.*, b.language, b.rights, b.content_checksum
        FROM mined_candidates AS c
        JOIN standard_ebooks_books AS b ON b.id = c.book_id
        WHERE c.time_confidence = 'AMPM_AMBIGUOUS'
        ORDER BY c.id
        """
    )
    for row in rows:
        gate_reason: str | None = None
        if row["duplicate_status"] != "NEW" and row["imported_quote_id"] is None:
            gate_reason = f"duplicate passage: {row['duplicate_status']}"
        elif str(row["language"] or "").casefold() != "en":
            gate_reason = "source language is not English"
        elif not all(
            str(row[field] or "").strip()
            for field in (
                "source_repository",
                "source_url",
                "source_commit",
                "source_file",
                "source_locator",
                "content_checksum",
            )
        ):
            gate_reason = "incomplete Standard Ebooks provenance"
        else:
            acceptable, reason = _quality_acceptable(row)
            if not acceptable:
                gate_reason = reason
        context = cache.get(row)
        source_context = (
            (
                context.paragraph,
                context.expression_start,
                context.expression_end,
                context.previous,
                context.following,
            )
            if context is not None
            else None
        )
        records.append(
            _assess_clockface(
                source_candidate_type=_SOURCE_STANDARD_EBOOKS,
                source_candidate_id=int(row["id"]),
                quote=str(row["quote"]),
                time_text=str(row["time_text"]),
                highlight_start=int(row["highlight_start"]),
                highlight_end=int(row["highlight_end"]),
                author=str(row["author"]),
                title=str(row["title"]),
                source_locator=str(row["source_locator"]),
                duplicate_status=str(row["duplicate_status"]),
                canonical_quote_id=(
                    int(row["imported_quote_id"]) if row["imported_quote_id"] is not None else None
                ),
                context_score=float(row["context_score"]),
                literary_quality_score=float(row["literary_quality_score"]),
                gate_reason=gate_reason,
                source_context=source_context,
            )
        )
    return records


def _gutenberg_audits(connection: sqlite3.Connection) -> list[AuditRecord]:
    records: list[AuditRecord] = []
    rows = connection.execute(
        """
        SELECT c.*, b.language, b.eligibility_status, b.text_sha256
        FROM gutenberg_candidates AS c JOIN gutenberg_books AS b USING (ebook_id)
        WHERE c.time_confidence = 'AMPM_AMBIGUOUS'
        ORDER BY c.id
        """
    )
    for row in rows:
        gate_reason: str | None = None
        if row["duplicate_status"] != "NEW" and row["imported_quote_id"] is None:
            gate_reason = f"duplicate passage: {row['duplicate_status']}"
        elif (
            row["eligibility_status"] != "ELIGIBLE"
            or row["rights"] != PUBLIC_DOMAIN_RIGHTS
            or row["language"] != "en"
        ):
            gate_reason = "Gutenberg rights, language, or literary metadata gate failed"
        elif not all(
            str(row[field] or "").strip()
            for field in ("source_url", "source_file", "source_locator", "text_sha256")
        ):
            gate_reason = "incomplete Gutenberg provenance"
        elif float(row["context_score"]) < 70 or float(row["literary_quality_score"]) < 60:
            gate_reason = "quality score below conservative threshold"
        else:
            gate_reason = text_quality_rejection(row["containing_paragraph"], row["quote"])
        quote = str(row["quote"])
        paragraph = str(row["containing_paragraph"])
        quote_start = paragraph.find(quote)
        source_context = None
        if quote_start >= 0:
            source_context = (
                paragraph,
                quote_start + int(row["highlight_start"]),
                quote_start + int(row["highlight_end"]),
                str(row["previous_paragraph"] or ""),
                str(row["following_paragraph"] or ""),
            )
        records.append(
            _assess_clockface(
                source_candidate_type=_SOURCE_GUTENBERG,
                source_candidate_id=int(row["id"]),
                quote=quote,
                time_text=str(row["time_text"]),
                highlight_start=int(row["highlight_start"]),
                highlight_end=int(row["highlight_end"]),
                author=str(row["author"]),
                title=str(row["title"]),
                source_locator=str(row["source_locator"]),
                duplicate_status=str(row["duplicate_status"]),
                canonical_quote_id=(
                    int(row["imported_quote_id"]) if row["imported_quote_id"] is not None else None
                ),
                context_score=float(row["context_score"]),
                literary_quality_score=float(row["literary_quality_score"]),
                gate_reason=gate_reason,
                source_context=source_context,
            )
        )
    return records


def _legacy_quality_reason(connection: sqlite3.Connection, row: sqlite3.Row) -> str | None:
    quote = str(row["quote"])
    start = row["highlight_start"]
    end = row["highlight_end"]
    if row["quality_status"] not in _RENDERABLE:
        return "legacy record lacks a previously verified exact highlight"
    if start is None or end is None or quote[int(start) : int(end)] == "":
        return "legacy record lacks exact highlight offsets"
    if not 40 <= len(quote) <= 600:
        return "context length outside 40–600 characters"
    first_alpha = next((char for char in quote if char.isalpha()), "")
    if first_alpha and first_alpha.islower():
        return "context begins mid-sentence"
    if not quote.endswith((".", "?", "!", ".”", "?”", "!”", ".’", "?’", "!’")):
        return "context has no complete sentence ending"
    if len(detect_time_expressions(quote)) >= 3:
        return "three or more time expressions in one context"
    if not connection.execute(
        "SELECT 1 FROM quote_provenance WHERE quote_id = ? LIMIT 1", (row["id"],)
    ).fetchone():
        return "legacy quote has no provenance row"
    return None


def _legacy_audits(connection: sqlite3.Connection) -> tuple[list[AuditRecord], int]:
    records: list[AuditRecord] = []
    ambiguous_without_offsets = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM quotes
            WHERE quality_status = 'AMBIGUOUS'
              AND source_name NOT LIKE 'standardebooks/%'
              AND source_name NOT LIKE 'gutenberg/%'
            """
        ).fetchone()[0]
    )
    rows = connection.execute(
        """
        SELECT * FROM quotes
        WHERE quality_status IN ('VERIFIED_EXACT', 'VERIFIED_NORMALIZED')
          AND source_name NOT LIKE 'standardebooks/%'
          AND source_name NOT LIKE 'gutenberg/%'
        ORDER BY id
        """
    )
    for row in rows:
        detection, _ = _matching_detection(
            str(row["quote"]), row["highlight_start"], row["highlight_end"]
        )
        if detection is None or detection.confidence != TimeConfidence.AMPM_AMBIGUOUS:
            continue
        quote = str(row["quote"])
        records.append(
            _assess_clockface(
                source_candidate_type=_SOURCE_LEGACY,
                source_candidate_id=int(row["id"]),
                quote=quote,
                time_text=str(row["time_text"]),
                highlight_start=int(row["highlight_start"]),
                highlight_end=int(row["highlight_end"]),
                author=str(row["author"]),
                title=str(row["title"]),
                source_locator=f"canonical-quote:{row['id']}",
                duplicate_status="NEW",
                canonical_quote_id=int(row["id"]),
                context_score=100.0,
                literary_quality_score=100.0,
                gate_reason=_legacy_quality_reason(connection, row),
                source_context=(
                    quote,
                    int(row["highlight_start"]),
                    int(row["highlight_end"]),
                    "",
                    "",
                ),
            )
        )
    return records, ambiguous_without_offsets


def _insert_audits(connection: sqlite3.Connection, run_id: int, records: list[AuditRecord]) -> None:
    timestamp = _now()
    connection.executemany(
        """
        INSERT INTO phase2c_candidate_audit (
            run_id, source_candidate_type, source_candidate_id, possible_minute_am,
            possible_minute_pm, parser_rule, parser_result, time_semantics,
            non_ampm_gate_status, gate_reason, displayed_resolution,
            displayed_evidence_type, displayed_evidence_text, source_resolution,
            source_evidence_type, source_evidence_text, decision, review_reason,
            context_score, literary_quality_score, author, title, time_text,
            source_locator, duplicate_status, canonical_quote_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?)
        """,
        [
            (
                run_id,
                record.source_candidate_type,
                record.source_candidate_id,
                record.possible_minute_am,
                record.possible_minute_pm,
                record.parser_rule,
                record.parser_result,
                record.time_semantics,
                record.non_ampm_gate_status,
                record.gate_reason,
                record.displayed_resolution,
                record.displayed_evidence_type,
                record.displayed_evidence_text,
                record.source_resolution,
                record.source_evidence_type,
                record.source_evidence_text,
                record.decision,
                record.review_reason,
                record.context_score,
                record.literary_quality_score,
                record.author,
                record.title,
                record.time_text,
                record.source_locator,
                record.duplicate_status,
                record.canonical_quote_id,
                timestamp,
            )
            for record in records
        ],
    )


def _strict_state(
    connection: sqlite3.Connection,
) -> tuple[dict[int, int], dict[int, set[str]], dict[int, set[str]], set[tuple[int, int]]]:
    counts: dict[int, int] = defaultdict(int)
    authors: dict[int, set[str]] = defaultdict(set)
    books: dict[int, set[str]] = defaultdict(set)
    relationships: set[tuple[int, int]] = set()
    for row in connection.execute(
        """
        SELECT id, minute_of_day, author, title FROM quotes
        WHERE quality_status IN ('VERIFIED_EXACT', 'VERIFIED_NORMALIZED')
        """
    ):
        minute = int(row["minute_of_day"])
        quote_id = int(row["id"])
        counts[minute] += 1
        relationships.add((quote_id, minute))
        if row["author"]:
            authors[minute].add(str(row["author"]).casefold())
        if row["title"]:
            books[minute].add(str(row["title"]).casefold())
    return dict(counts), authors, books, relationships


def _snapshot(
    counts: dict[int, int], *, canonical_quotes: int, unique_selectable: int, relationships: int
) -> dict[str, Any]:
    values = [counts.get(minute, 0) for minute in range(1440)]
    return {
        "canonical_quotes": canonical_quotes,
        "unique_selectable_quotes": unique_selectable,
        "quote_minute_eligibility_relationships": relationships,
        "minutes_at_0": sum(value == 0 for value in values),
        "minutes_below_3": sum(value < 3 for value in values),
        "minutes_below_5": sum(value < 5 for value in values),
        "minutes_below_7": sum(value < 7 for value in values),
        "minutes_at_least_7": sum(value >= 7 for value in values),
        "remaining_deficit_to_7": sum(max(0, 7 - value) for value in values),
        "effective_counts": values,
    }


def _audit_targets(row: sqlite3.Row) -> list[tuple[int, str]]:
    if row["decision"] == "DUAL_ELIGIBLE":
        return [
            (int(row["possible_minute_am"]), EligibilityType.CLOCKFACE_SHARED_AM.value),
            (int(row["possible_minute_pm"]), EligibilityType.CLOCKFACE_SHARED_PM.value),
        ]
    if row["decision"] == "CONTEXT_RESOLVED":
        minute = row["source_resolution"]
        if minute is None:
            minute = row["displayed_resolution"]
        if minute is None:
            return []
        return [
            (
                int(minute),
                (
                    EligibilityType.CONTEXT_RESOLVED_AM.value
                    if int(minute) < 720
                    else EligibilityType.CONTEXT_RESOLVED_PM.value
                ),
            )
        ]
    return []


def _plan_scenario(
    connection: sqlite3.Connection,
    run_id: int,
    scenario: str,
    baseline_counts: dict[int, int],
    baseline_authors: dict[int, set[str]],
    baseline_books: dict[int, set[str]],
    baseline_relationships: set[tuple[int, int]],
    *,
    diversity: bool,
) -> dict[str, Any]:
    audits = list(
        connection.execute(
            """
            SELECT * FROM phase2c_candidate_audit
            WHERE run_id = ? AND non_ampm_gate_status = 'PASS'
              AND decision IN ('DUAL_ELIGIBLE', 'CONTEXT_RESOLVED')
            ORDER BY source_candidate_type, source_candidate_id
            """,
            (run_id,),
        )
    )
    by_minute: dict[int, list[sqlite3.Row]] = defaultdict(list)
    target_type: dict[tuple[str, int, int], str] = {}
    for row in audits:
        for minute, eligibility_type in _audit_targets(row):
            if (
                row["canonical_quote_id"] is not None
                and (int(row["canonical_quote_id"]), minute) in baseline_relationships
            ):
                continue
            by_minute[minute].append(row)
            target_type[
                (str(row["source_candidate_type"]), int(row["source_candidate_id"]), minute)
            ] = eligibility_type

    planned: list[tuple[int, str, str, int, int, str]] = []
    for minute in range(1440):
        needed = max(0, 7 - baseline_counts.get(minute, 0))
        if not needed:
            continue
        candidates = by_minute.get(minute, [])
        if not candidates:
            continue
        selected: list[sqlite3.Row] = []
        seen_authors = set(baseline_authors.get(minute, set()))
        seen_books = set(baseline_books.get(minute, set()))
        remaining = candidates.copy()
        while remaining and len(selected) < needed:
            remaining.sort(
                key=lambda row: (
                    (str(row["author"]).casefold() not in seen_authors if diversity else False),
                    (str(row["title"]).casefold() not in seen_books if diversity else False),
                    float(row["context_score"]),
                    float(row["literary_quality_score"]),
                    row["source_candidate_type"] == _SOURCE_STANDARD_EBOOKS,
                    -int(row["source_candidate_id"]),
                ),
                reverse=True,
            )
            chosen = remaining.pop(0)
            selected.append(chosen)
            seen_authors.add(str(chosen["author"]).casefold())
            seen_books.add(str(chosen["title"]).casefold())
        for row in selected:
            source_type = str(row["source_candidate_type"])
            candidate_id = int(row["source_candidate_id"])
            planned.append(
                (
                    run_id,
                    scenario,
                    source_type,
                    candidate_id,
                    minute,
                    target_type[(source_type, candidate_id, minute)],
                )
            )
    connection.executemany(
        """
        INSERT INTO phase2c_plan_relationships (
            run_id, scenario, source_candidate_type, source_candidate_id,
            minute_of_day, eligibility_type
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        planned,
    )

    counts = dict(baseline_counts)
    new_relationships = 0
    new_candidates: set[tuple[str, int]] = set()
    existing_quote_ids = {
        (str(row["source_candidate_type"]), int(row["source_candidate_id"])): (
            int(row["canonical_quote_id"]) if row["canonical_quote_id"] is not None else None
        )
        for row in audits
    }
    for _, _, source_type, candidate_id, minute, _ in planned:
        quote_id = existing_quote_ids[(source_type, candidate_id)]
        if quote_id is not None and (quote_id, minute) in baseline_relationships:
            continue
        counts[minute] = counts.get(minute, 0) + 1
        new_relationships += 1
        if quote_id is None:
            new_candidates.add((source_type, candidate_id))
    canonical = int(connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0])
    selectable = int(
        connection.execute(
            "SELECT COUNT(*) FROM quotes WHERE quality_status IN (?, ?)", _RENDERABLE
        ).fetchone()[0]
    )
    snapshot = _snapshot(
        counts,
        canonical_quotes=canonical + len(new_candidates),
        unique_selectable=selectable + len(new_candidates),
        relationships=len(baseline_relationships) + new_relationships,
    )
    snapshot["new_canonical_quotes"] = len(new_candidates)
    snapshot["new_relationships"] = new_relationships
    snapshot["planned_rows"] = len(planned)
    snapshot["selected_authors"] = len(
        {
            str(row["author"]).casefold()
            for row in audits
            if (str(row["source_candidate_type"]), int(row["source_candidate_id"]))
            in new_candidates
        }
    )
    snapshot["selected_books"] = len(
        {
            str(row["title"]).casefold()
            for row in audits
            if (str(row["source_candidate_type"]), int(row["source_candidate_id"]))
            in new_candidates
        }
    )
    return snapshot


def _paired_improvements(
    before: dict[str, Any], after: dict[str, Any]
) -> list[dict[str, int | str]]:
    old = before["effective_counts"]
    new = after["effective_counts"]
    rows = []
    for minute in range(720):
        paired = minute + 720
        improvement = (new[minute] - old[minute]) + (new[paired] - old[paired])
        rows.append(
            {
                "pair": f"{minute_to_time(minute)} / {minute_to_time(paired)}",
                "before_am": old[minute],
                "before_pm": old[paired],
                "after_am": new[minute],
                "after_pm": new[paired],
                "improvement": improvement,
            }
        )
    return sorted(rows, key=lambda row: (-int(row["improvement"]), str(row["pair"])))


def _scenario_table(scenarios: dict[str, dict[str, Any]]) -> list[str]:
    lines = [
        "| Scenario | Canonical | Unique selectable | Relationships | 0 | <3 | <5 | <7 | "
        ">=7 | Deficit |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    labels = {
        "A": "A — current strict 24-hour model",
        "B": "B — shared clock-face model",
        "C": "C — shared model with diversity preference",
    }
    for key in ("A", "B", "C"):
        value = scenarios[key]
        lines.append(
            f"| {labels[key]} | {value['canonical_quotes']:,} | "
            f"{value['unique_selectable_quotes']:,} | "
            f"{value['quote_minute_eligibility_relationships']:,} | "
            f"{value['minutes_at_0']:,} | {value['minutes_below_3']:,} | "
            f"{value['minutes_below_5']:,} | {value['minutes_below_7']:,} | "
            f"{value['minutes_at_least_7']:,} | {value['remaining_deficit_to_7']:,} |"
        )
    return lines


def _write_counterfactual_report(
    connection: sqlite3.Connection,
    project_root: Path,
    run_id: int,
    scenarios: dict[str, dict[str, Any]],
    legacy_unhighlighted: int,
) -> Path:
    output = project_root / "data" / "generated" / "PHASE2C_COUNTERFACTUAL.md"
    decisions = dict(
        connection.execute(
            """
            SELECT decision, COUNT(*) FROM phase2c_candidate_audit
            WHERE run_id = ? GROUP BY decision
            """,
            (run_id,),
        )
    )
    sources = dict(
        connection.execute(
            """
            SELECT source_candidate_type, COUNT(*) FROM phase2c_candidate_audit
            WHERE run_id = ? GROUP BY source_candidate_type
            """,
            (run_id,),
        )
    )
    pairs = _paired_improvements(scenarios["A"], scenarios["C"])[:50]
    pair_lines = [
        "| Rank | Pair | Before | After | Added relationships |",
        "|---:|---|---:|---:|---:|",
    ]
    for rank, row in enumerate(pairs, 1):
        pair_lines.append(
            f"| {rank} | {row['pair']} | {row['before_am']} + {row['before_pm']} | "
            f"{row['after_am']} + {row['after_pm']} | {row['improvement']} |"
        )
    lines = [
        "# Phase 2C Counterfactual — Shared 12-Hour Clock-Face Eligibility",
        "",
        f"Generated before production eligibility mutation: {_now()}",
        "",
        "An unresolved exact 12-hour clock expression denotes a position on an ordinary clock",
        "face. Representing it at both corresponding AM and PM display moments does not infer",
        "narrative daypart. Deterministic displayed or source-context evidence remains "
        "authoritative",
        "and produces one resolved eligibility relationship instead.",
        "",
        "## Audit boundary",
        "",
        "Displayed-text semantics were checked independently from full source paragraph and",
        "neighboring-paragraph context. Direct daypart wording or deterministic local elapsed-time",
        "arithmetic resolves one side. Mere nearby daypart vocabulary is not treated as evidence",
        "and leaves the expression daypart-neutral; conflicting cues or unavailable trusted source",
        "context are retained for review.",
        "",
        "## Candidate accounting",
        "",
        f"- Standard Ebooks ambiguous detections: **{sources.get(_SOURCE_STANDARD_EBOOKS, 0):,}**",
        f"- Project Gutenberg ambiguous detections: **{sources.get(_SOURCE_GUTENBERG, 0):,}**",
        "- Applicable selectable legacy clock-face records: "
        f"**{sources.get(_SOURCE_LEGACY, 0):,}**",
        f"- Other legacy AMBIGUOUS records lacking exact offsets: **{legacy_unhighlighted:,}**",
        f"- Passed as daypart-neutral dual candidates: **{decisions.get('DUAL_ELIGIBLE', 0):,}**",
        "- Deterministically context-resolved candidates: "
        f"**{decisions.get('CONTEXT_RESOLVED', 0):,}**",
        f"- Contextual review required: **{decisions.get('CONTEXT_REVIEW', 0):,}**",
        f"- Failed unchanged non-AM/PM gates: **{decisions.get('REJECTED', 0):,}**",
        "",
        "## Counterfactual coverage",
        "",
        *_scenario_table(scenarios),
        "",
        "Scenario B ranks by deterministic quality. Scenario C first prefers new authors and books",
        "within each minute, then relaxes those preferences so they never prevent filling a "
        "bucket.",
        "No scenario adds an eighth effective candidate to a minute.",
        "",
        "## 50 AM/PM pairs with the largest improvement",
        "",
        *pair_lines,
        "",
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".md.tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    os.replace(temporary, output)
    return output


def build_phase2c_counterfactual(
    connection: sqlite3.Connection, project_root: Path, *, rebuild: bool = False
) -> dict[str, Any]:
    """Audit all ambiguous inputs and save counterfactuals before eligibility mutation."""
    initialize_database(connection)
    existing = connection.execute("SELECT * FROM phase2c_runs ORDER BY id DESC LIMIT 1").fetchone()
    if existing is not None and existing["status"] == "COMPLETE":
        if rebuild:
            raise ValueError("cannot rebuild a Phase 2C counterfactual after activation")
        return {
            "run_id": int(existing["id"]),
            "A": json.loads(existing["baseline_json"]),
            "B": json.loads(existing["scenario_b_json"]),
            "C": json.loads(existing["scenario_c_json"]),
            "reused": True,
        }
    if existing is not None and existing["status"] == "COUNTERFACTUAL_COMPLETE" and not rebuild:
        return {
            "run_id": int(existing["id"]),
            "A": json.loads(existing["baseline_json"]),
            "B": json.loads(existing["scenario_b_json"]),
            "C": json.loads(existing["scenario_c_json"]),
            "reused": True,
        }
    if existing is not None:
        connection.execute("DELETE FROM phase2c_runs WHERE id = ?", (existing["id"],))
    cursor = connection.execute(
        "INSERT INTO phase2c_runs (started_at, status) VALUES (?, 'RUNNING')", (_now(),)
    )
    run_id = int(cursor.lastrowid)
    connection.commit()
    try:
        baseline_counts, baseline_authors, baseline_books, baseline_relationships = _strict_state(
            connection
        )
        canonical = int(connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0])
        selectable = int(
            connection.execute(
                "SELECT COUNT(*) FROM quotes WHERE quality_status IN (?, ?)", _RENDERABLE
            ).fetchone()[0]
        )
        baseline = _snapshot(
            baseline_counts,
            canonical_quotes=canonical,
            unique_selectable=selectable,
            relationships=len(baseline_relationships),
        )
        standard = _standard_ebooks_audits(connection, project_root)
        gutenberg = _gutenberg_audits(connection)
        legacy, legacy_unhighlighted = _legacy_audits(connection)
        _insert_audits(connection, run_id, standard)
        _insert_audits(connection, run_id, gutenberg)
        _insert_audits(connection, run_id, legacy)
        scenario_b = _plan_scenario(
            connection,
            run_id,
            "B",
            baseline_counts,
            baseline_authors,
            baseline_books,
            baseline_relationships,
            diversity=False,
        )
        scenario_c = _plan_scenario(
            connection,
            run_id,
            "C",
            baseline_counts,
            baseline_authors,
            baseline_books,
            baseline_relationships,
            diversity=True,
        )
        scenarios = {"A": baseline, "B": scenario_b, "C": scenario_c}
        report = _write_counterfactual_report(
            connection, project_root, run_id, scenarios, legacy_unhighlighted
        )
        write_phase2c_1546_audit(connection, project_root, run_id)
        digest = hashlib.sha256(report.read_bytes()).hexdigest()
        connection.execute(
            """
            UPDATE phase2c_runs SET status = 'COUNTERFACTUAL_COMPLETE',
                counterfactual_saved_at = ?, counterfactual_sha256 = ?, baseline_json = ?,
                scenario_b_json = ?, scenario_c_json = ? WHERE id = ?
            """,
            (
                _now(),
                digest,
                json.dumps(baseline, sort_keys=True),
                json.dumps(scenario_b, sort_keys=True),
                json.dumps(scenario_c, sort_keys=True),
                run_id,
            ),
        )
        connection.commit()
        return {"run_id": run_id, **scenarios, "reused": False, "report": str(report)}
    except Exception as error:
        connection.rollback()
        connection.execute(
            "UPDATE phase2c_runs SET status = 'FAILED', finished_at = ?, error = ? WHERE id = ?",
            (_now(), str(error), run_id),
        )
        connection.commit()
        raise


def _candidate_content(
    connection: sqlite3.Connection, source_type: str, candidate_id: int
) -> dict[str, Any]:
    if source_type == _SOURCE_STANDARD_EBOOKS:
        row = connection.execute(
            """
            SELECT c.*, b.content_checksum, b.source_license
            FROM mined_candidates AS c JOIN standard_ebooks_books AS b ON b.id = c.book_id
            WHERE c.id = ?
            """,
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"missing Standard Ebooks candidate {candidate_id}")
        return {
            **dict(row),
            "previous_paragraph": "",
            "containing_paragraph": row["quote"],
            "following_paragraph": "",
        }
    if source_type == _SOURCE_GUTENBERG:
        row = connection.execute(
            """
            SELECT c.*, b.text_sha256, b.text_path, b.catalog_sha256
            FROM gutenberg_candidates AS c JOIN gutenberg_books AS b USING (ebook_id)
            WHERE c.id = ?
            """,
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"missing Gutenberg candidate {candidate_id}")
        return dict(row)
    row = connection.execute("SELECT * FROM quotes WHERE id = ?", (candidate_id,)).fetchone()
    if row is None:
        raise ValueError(f"missing canonical quote {candidate_id}")
    return {
        **dict(row),
        "previous_paragraph": "",
        "containing_paragraph": row["quote"],
        "following_paragraph": "",
        "source_locator": f"canonical-quote:{candidate_id}",
    }


def _ensure_source(
    connection: sqlite3.Connection, source_type: str, row: dict[str, Any], timestamp: str
) -> int:
    if source_type == _SOURCE_STANDARD_EBOOKS:
        name = f"standardebooks/{row['source_repository']}"
        slug = "standardebooks-" + str(row["source_repository"]).replace("/", "-")
        values = (
            name,
            slug,
            row["source_url"],
            row.get("source_license") or STANDARD_EBOOKS_LICENSE,
            row["source_commit"],
            f"data/public_domain/standard_ebooks/books/{row['source_repository']}",
            row["content_checksum"],
            timestamp,
        )
    elif source_type == _SOURCE_GUTENBERG:
        name = f"gutenberg/{row['ebook_id']}"
        slug = f"gutenberg-{row['ebook_id']}"
        values = (
            name,
            slug,
            row["source_url"],
            "Project Gutenberg; underlying work: Public domain in the USA.",
            f"catalog:{row['catalog_sha256']}",
            row.get("text_path") or row["source_file"],
            row["text_sha256"],
            timestamp,
        )
    else:
        raise ValueError(f"cannot create a source for {source_type}")
    connection.execute(
        """
        INSERT INTO sources (
            name, slug, source_url, source_license, upstream_commit,
            corpus_path, corpus_sha256, record_count, imported_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
        ON CONFLICT(name) DO UPDATE SET imported_at = excluded.imported_at
        """,
        values,
    )
    return int(connection.execute("SELECT id FROM sources WHERE name = ?", (name,)).fetchone()[0])


def _import_planned_candidate(
    connection: sqlite3.Connection,
    audit: sqlite3.Row,
    minutes: list[int],
    import_run_id: int,
) -> int:
    source_type = str(audit["source_candidate_type"])
    if source_type == _SOURCE_LEGACY:
        if audit["canonical_quote_id"] is None:
            raise ValueError("legacy audit lacks its canonical quote ID")
        return int(audit["canonical_quote_id"])
    row = _candidate_content(connection, source_type, int(audit["source_candidate_id"]))
    if row.get("imported_quote_id") is not None:
        return int(row["imported_quote_id"])
    timestamp = _now()
    source_id = _ensure_source(connection, source_type, row, timestamp)
    anchor = min(minutes)
    cursor = connection.execute(
        """
        INSERT INTO quotes (
            minute_of_day, time_24h, time_text, quote, title, author, sfw, language,
            source_name, source_url, source_license, source_record_id, quote_hash,
            normalized_quote_hash, highlight_start, highlight_end, quality_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'en', ?, ?, ?, ?, ?, ?, ?, ?,
                  'VERIFIED_EXACT', ?)
        """,
        (
            anchor,
            minute_to_time(anchor),
            row["time_text"],
            row["quote"],
            row["title"],
            row["author"],
            (
                f"standardebooks/{row['source_repository']}"
                if source_type == _SOURCE_STANDARD_EBOOKS
                else f"gutenberg/{row['ebook_id']}"
            ),
            row["source_url"],
            (
                row.get("source_license") or STANDARD_EBOOKS_LICENSE
                if source_type == _SOURCE_STANDARD_EBOOKS
                else "Project Gutenberg; underlying work: Public domain in the USA."
            ),
            str(audit["source_candidate_id"]),
            text_hash(row["quote"]),
            row["normalized_quote_hash"],
            row["highlight_start"],
            row["highlight_end"],
            timestamp,
        ),
    )
    quote_id = int(cursor.lastrowid)
    raw_payload = {
        "phase": "2C",
        "source_candidate_type": source_type,
        "source_candidate_id": int(audit["source_candidate_id"]),
        "source_locator": row["source_locator"],
        "parser_rule": audit["parser_rule"],
        "parser_result": audit["parser_result"],
        "time_semantics": audit["time_semantics"],
        "displayed_evidence": audit["displayed_evidence_text"],
        "source_evidence": audit["source_evidence_text"],
    }
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
            import_run_id,
            str(audit["source_candidate_id"]),
            minute_to_time(anchor),
            row["time_text"],
            row["quote"],
            row["title"],
            row["author"],
            text_hash(row["quote"]),
            row["highlight_start"],
            row["highlight_end"],
            json.dumps(raw_payload, ensure_ascii=False, sort_keys=True),
        ),
    )
    status = (
        "IMPORTED_CLOCKFACE_SHARED"
        if audit["decision"] == "DUAL_ELIGIBLE"
        else "IMPORTED_CONTEXTUAL_2C"
    )
    table = "mined_candidates" if source_type == _SOURCE_STANDARD_EBOOKS else "gutenberg_candidates"
    connection.execute(
        f"""
        UPDATE {table} SET imported_quote_id = ?, imported_at = ?, review_status = ?,
            rejection_reason = NULL
        WHERE id = ?
        """,  # noqa: S608 - table is selected from a fixed internal enum
        (quote_id, timestamp, status, audit["source_candidate_id"]),
    )
    connection.execute(
        "UPDATE sources SET record_count = record_count + 1 WHERE id = ?", (source_id,)
    )
    # The generic INSERT trigger is replaced by the exact Phase 2C semantics below.
    connection.execute("DELETE FROM quote_minute_eligibility WHERE quote_id = ?", (quote_id,))
    connection.execute("DELETE FROM quote_time_semantics WHERE quote_id = ?", (quote_id,))
    return quote_id


def _backfill_semantic_types(connection: sqlite3.Connection) -> None:
    timestamp = _now()
    rows = connection.execute(
        """
        SELECT q.*, e.minute_of_day AS eligible_minute
        FROM quotes AS q JOIN quote_minute_eligibility AS e ON e.quote_id = q.id
        WHERE q.quality_status IN ('VERIFIED_EXACT', 'VERIFIED_NORMALIZED')
        """
    )
    for row in rows:
        detection, _ = _matching_detection(
            str(row["quote"]), row["highlight_start"], row["highlight_end"]
        )
        semantics = TimeSemantics.RESOLVED_24H.value
        eligibility_type = EligibilityType.EXACT_24H.value
        narrative = "RESOLVED_24H"
        evidence_type = "CANONICAL_MINUTE"
        evidence_text = str(row["time_24h"])
        if detection is not None:
            evidence_text = detection.ampm_evidence or detection.text
            if detection.parser_rule == "named" and detection.text.casefold() == "noon":
                semantics = TimeSemantics.NOON.value
            elif detection.parser_rule == "named" and detection.text.casefold() == "midnight":
                semantics = TimeSemantics.MIDNIGHT.value
            elif detection.confidence == TimeConfidence.EXACT_AM:
                eligibility_type = EligibilityType.EXPLICIT_AM.value
                narrative = "AM"
                evidence_type = "EXPLICIT_AM"
            elif detection.confidence == TimeConfidence.EXACT_PM:
                eligibility_type = EligibilityType.EXPLICIT_PM.value
                narrative = "PM"
                evidence_type = "EXPLICIT_PM"
            elif detection.confidence == TimeConfidence.EXACT_CONTEXTUAL:
                narrative = "AM" if int(row["eligible_minute"]) < 720 else "PM"
                eligibility_type = (
                    EligibilityType.CONTEXT_RESOLVED_AM.value
                    if narrative == "AM"
                    else EligibilityType.CONTEXT_RESOLVED_PM.value
                )
                evidence_type = "DISPLAYED_CONTEXT"
        connection.execute(
            """
            INSERT INTO quote_time_semantics (
                quote_id, time_semantics, clockface_minute, narrative_resolution,
                evidence_type, evidence_text, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(quote_id) DO UPDATE SET
                time_semantics = excluded.time_semantics,
                clockface_minute = excluded.clockface_minute,
                narrative_resolution = excluded.narrative_resolution,
                evidence_type = excluded.evidence_type,
                evidence_text = excluded.evidence_text,
                updated_at = excluded.updated_at
            """,
            (
                row["id"],
                semantics,
                int(row["eligible_minute"]) % 720,
                narrative,
                evidence_type,
                evidence_text,
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            UPDATE quote_minute_eligibility
            SET eligibility_type = ?, confidence = 'VERIFIED', evidence_type = ?,
                evidence_text = ? WHERE quote_id = ? AND minute_of_day = ?
            """,
            (
                eligibility_type,
                evidence_type,
                evidence_text,
                row["id"],
                row["eligible_minute"],
            ),
        )


def _repair_candidate_options(connection: sqlite3.Connection, run_id: int) -> None:
    rows = list(
        connection.execute(
            """
            SELECT source_candidate_type, source_candidate_id, possible_minute_am,
                   possible_minute_pm
            FROM phase2c_candidate_audit
            WHERE run_id = ? AND possible_minute_am IS NOT NULL
              AND possible_minute_pm IS NOT NULL
            """,
            (run_id,),
        )
    )
    evidence = [
        (
            f"no strong AM/PM evidence; {minute_to_time(int(row['possible_minute_am']))}|"
            f"{minute_to_time(int(row['possible_minute_pm']))}",
            row["source_candidate_id"],
        )
        for row in rows
        if row["source_candidate_type"] == _SOURCE_STANDARD_EBOOKS
    ]
    connection.executemany("UPDATE mined_candidates SET ampm_evidence = ? WHERE id = ?", evidence)
    gutenberg = [
        (
            row["possible_minute_am"],
            row["possible_minute_pm"],
            f"no strong AM/PM evidence; {minute_to_time(int(row['possible_minute_am']))}|"
            f"{minute_to_time(int(row['possible_minute_pm']))}",
            row["source_candidate_id"],
        )
        for row in rows
        if row["source_candidate_type"] == _SOURCE_GUTENBERG
    ]
    connection.executemany(
        """
        UPDATE gutenberg_candidates SET possible_minute_am = ?, possible_minute_pm = ?,
            ampm_evidence = ? WHERE id = ?
        """,
        gutenberg,
    )


def activate_phase2c(connection: sqlite3.Connection, project_root: Path) -> dict[str, Any]:
    """Activate the saved diversity-aware plan in one restart-safe transaction."""
    initialize_database(connection)
    run = connection.execute("SELECT * FROM phase2c_runs ORDER BY id DESC LIMIT 1").fetchone()
    if run is None or run["status"] not in {"COUNTERFACTUAL_COMPLETE", "COMPLETE"}:
        raise ValueError("a saved Phase 2C counterfactual is required before activation")
    if run["status"] == "COMPLETE":
        return write_phase2c_outputs(connection, project_root, int(run["id"]))
    counterfactual = project_root / "data" / "generated" / "PHASE2C_COUNTERFACTUAL.md"
    if not counterfactual.is_file():
        raise ValueError("saved Phase 2C counterfactual report is missing")
    if hashlib.sha256(counterfactual.read_bytes()).hexdigest() != run["counterfactual_sha256"]:
        raise ValueError("saved Phase 2C counterfactual checksum mismatch")

    backfill_primary_eligibility(connection)
    _backfill_semantic_types(connection)
    connection.commit()
    run_id = int(run["id"])
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("UPDATE phase2c_runs SET status = 'ACTIVATING' WHERE id = ?", (run_id,))
        _repair_candidate_options(connection, run_id)
        import_cursor = connection.execute(
            "INSERT INTO import_runs (started_at, status) VALUES (?, 'RUNNING')", (_now(),)
        )
        import_run_id = int(import_cursor.lastrowid)
        plan_rows = list(
            connection.execute(
                """
                SELECT p.*, a.* FROM phase2c_plan_relationships AS p
                JOIN phase2c_candidate_audit AS a
                  ON a.run_id = p.run_id
                 AND a.source_candidate_type = p.source_candidate_type
                 AND a.source_candidate_id = p.source_candidate_id
                WHERE p.run_id = ? AND p.scenario = 'C'
                ORDER BY p.source_candidate_type, p.source_candidate_id, p.minute_of_day
                """,
                (run_id,),
            )
        )
        grouped: dict[tuple[str, int], list[sqlite3.Row]] = defaultdict(list)
        for row in plan_rows:
            grouped[(str(row["source_candidate_type"]), int(row["source_candidate_id"]))].append(
                row
            )
        counts = {
            int(row["minute_of_day"]): int(row["n"])
            for row in connection.execute(
                """
                SELECT minute_of_day, COUNT(*) AS n
                FROM quote_minute_eligibility GROUP BY minute_of_day
                """
            )
        }
        imported = 0
        relationships_added = 0
        for _, rows in grouped.items():
            audit = rows[0]
            minutes = [int(row["minute_of_day"]) for row in rows]
            quote_id = audit["canonical_quote_id"]
            if quote_id is None:
                quote_id = _import_planned_candidate(connection, audit, minutes, import_run_id)
                imported += 1
            else:
                quote_id = int(quote_id)
            decision = str(audit["decision"])
            resolved = audit["source_resolution"]
            if resolved is None:
                resolved = audit["displayed_resolution"]
            if decision == "DUAL_ELIGIBLE":
                narrative = "UNRESOLVED"
                evidence_type = "CLOCKFACE_NO_DAYPART"
                evidence_text = (
                    "displayed text and available source context contain no deterministic daypart"
                )
            else:
                narrative = "AM" if int(resolved) < 720 else "PM"
                evidence_type = audit["source_evidence_type"] or audit["displayed_evidence_type"]
                evidence_text = audit["source_evidence_text"] or audit["displayed_evidence_text"]
            timestamp = _now()
            connection.execute(
                """
                INSERT INTO quote_time_semantics (
                    quote_id, time_semantics, clockface_minute, narrative_resolution,
                    evidence_type, evidence_text, source_candidate_type,
                    source_candidate_id, created_at, updated_at
                ) VALUES (?, 'CLOCKFACE_12H', ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(quote_id) DO UPDATE SET
                    time_semantics = excluded.time_semantics,
                    clockface_minute = excluded.clockface_minute,
                    narrative_resolution = excluded.narrative_resolution,
                    evidence_type = excluded.evidence_type,
                    evidence_text = excluded.evidence_text,
                    source_candidate_type = excluded.source_candidate_type,
                    source_candidate_id = excluded.source_candidate_id,
                    updated_at = excluded.updated_at
                """,
                (
                    quote_id,
                    int(audit["possible_minute_am"]),
                    narrative,
                    evidence_type,
                    evidence_text,
                    audit["source_candidate_type"],
                    audit["source_candidate_id"],
                    timestamp,
                    timestamp,
                ),
            )
            if decision == "DUAL_ELIGIBLE":
                connection.execute(
                    """
                    UPDATE quote_minute_eligibility
                    SET eligibility_type = CASE WHEN minute_of_day < 720
                            THEN 'CLOCKFACE_SHARED_AM' ELSE 'CLOCKFACE_SHARED_PM' END,
                        confidence = 'CLOCKFACE_EXACT', evidence_type = ?, evidence_text = ?,
                        source_candidate_type = ?, source_candidate_id = ?
                    WHERE quote_id = ?
                    """,
                    (
                        evidence_type,
                        evidence_text,
                        audit["source_candidate_type"],
                        audit["source_candidate_id"],
                        quote_id,
                    ),
                )
            for plan in rows:
                minute = int(plan["minute_of_day"])
                exists = connection.execute(
                    """
                    SELECT 1 FROM quote_minute_eligibility
                    WHERE quote_id = ? AND minute_of_day = ?
                    """,
                    (quote_id, minute),
                ).fetchone()
                if exists:
                    connection.execute(
                        """
                        UPDATE quote_minute_eligibility SET eligibility_type = ?,
                            confidence = 'CLOCKFACE_EXACT', evidence_type = ?, evidence_text = ?,
                            source_candidate_type = ?, source_candidate_id = ?
                        WHERE quote_id = ? AND minute_of_day = ?
                        """,
                        (
                            plan["eligibility_type"],
                            evidence_type,
                            evidence_text,
                            audit["source_candidate_type"],
                            audit["source_candidate_id"],
                            quote_id,
                            minute,
                        ),
                    )
                    continue
                if counts.get(minute, 0) >= 7:
                    raise ValueError(
                        "Phase 2C plan would exceed the seven-quote cap at "
                        f"{minute_to_time(minute)}"
                    )
                connection.execute(
                    """
                    INSERT INTO quote_minute_eligibility (
                        quote_id, minute_of_day, eligibility_type, confidence,
                        evidence_type, evidence_text, source_candidate_type,
                        source_candidate_id, created_at
                    ) VALUES (?, ?, ?, 'CLOCKFACE_EXACT', ?, ?, ?, ?, ?)
                    """,
                    (
                        quote_id,
                        minute,
                        plan["eligibility_type"],
                        evidence_type,
                        evidence_text,
                        audit["source_candidate_type"],
                        audit["source_candidate_id"],
                        timestamp,
                    ),
                )
                counts[minute] = counts.get(minute, 0) + 1
                relationships_added += 1
            connection.execute(
                """
                UPDATE phase2c_candidate_audit SET canonical_quote_id = ?
                WHERE run_id = ? AND source_candidate_type = ? AND source_candidate_id = ?
                """,
                (
                    quote_id,
                    run_id,
                    audit["source_candidate_type"],
                    audit["source_candidate_id"],
                ),
            )
        connection.execute(
            """
            UPDATE import_runs SET finished_at = ?, status = 'COMPLETE',
                raw_record_count = ?, canonical_inserted = ? WHERE id = ?
            """,
            (_now(), len(grouped), imported, import_run_id),
        )
        connection.execute(
            """
            UPDATE phase2c_runs SET status = 'COMPLETE', activated_at = ?, finished_at = ?
            WHERE id = ?
            """,
            (_now(), _now(), run_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    result = write_phase2c_outputs(connection, project_root, run_id)
    result["new_canonical_quotes"] = imported
    result["relationships_added"] = relationships_added
    return result


def _effective_counts(connection: sqlite3.Connection) -> dict[int, int]:
    return {
        int(row["minute_of_day"]): int(row["n"])
        for row in connection.execute(
            """
            SELECT e.minute_of_day, COUNT(*) AS n
            FROM quote_minute_eligibility AS e JOIN quotes AS q ON q.id = e.quote_id
            WHERE q.quality_status IN ('VERIFIED_EXACT', 'VERIFIED_NORMALIZED')
            GROUP BY e.minute_of_day
            """
        )
    }


def _review_source_context(
    connection: sqlite3.Connection,
    project_root: Path,
    source_type: str,
    candidate_id: int,
    standard_cache: _SourceContextCache,
) -> tuple[dict[str, Any], str, str, str]:
    content = _candidate_content(connection, source_type, candidate_id)
    if source_type == _SOURCE_STANDARD_EBOOKS:
        context = standard_cache.get(content)
        if context is not None:
            return content, context.previous, context.paragraph, context.following
    return (
        content,
        str(content.get("previous_paragraph") or ""),
        str(content.get("containing_paragraph") or content["quote"]),
        str(content.get("following_paragraph") or ""),
    )


def export_phase2c_context_review(
    connection: sqlite3.Connection, project_root: Path, run_id: int
) -> int:
    """Export daypart-uncertain records only where a possible bucket remains below seven."""
    counts = _effective_counts(connection)
    standard_cache = _SourceContextCache(project_root)
    rows: list[dict[str, Any]] = []
    for audit in connection.execute(
        """
        SELECT * FROM phase2c_candidate_audit
        WHERE run_id = ? AND decision = 'CONTEXT_REVIEW'
          AND non_ampm_gate_status = 'PASS'
        ORDER BY source_candidate_type, source_candidate_id
        """,
        (run_id,),
    ):
        options = [
            int(value)
            for value in (audit["possible_minute_am"], audit["possible_minute_pm"])
            if value is not None
        ]
        sparse = [minute for minute in options if counts.get(minute, 0) < 7]
        if not sparse:
            continue
        content, previous, containing, following = _review_source_context(
            connection,
            project_root,
            str(audit["source_candidate_type"]),
            int(audit["source_candidate_id"]),
            standard_cache,
        )
        rows.append(
            {
                "possible_minutes": "|".join(minute_to_time(value) for value in options),
                "current_counts": "|".join(str(counts.get(value, 0)) for value in options),
                "minimum_bucket_count": min(counts.get(value, 0) for value in options),
                "time_phrase": audit["time_text"],
                "quote": content["quote"],
                "title": audit["title"],
                "author": audit["author"],
                "source_candidate_type": audit["source_candidate_type"],
                "source_candidate_id": audit["source_candidate_id"],
                "source_locator": audit["source_locator"],
                "displayed_text_evidence": audit["displayed_evidence_text"],
                "source_context_evidence": audit["source_evidence_text"],
                "review_reason": audit["review_reason"],
                "previous_context": previous,
                "containing_context": containing,
                "following_context": following,
            }
        )
    rows.sort(
        key=lambda row: (
            int(row["minimum_bucket_count"]),
            str(row["source_candidate_type"]),
            int(row["source_candidate_id"]),
        )
    )
    output = project_root / "data" / "generated" / "PHASE2C_CONTEXT_REVIEW.csv"
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
            "title",
            "author",
            "source_candidate_type",
            "source_candidate_id",
            "source_locator",
            "displayed_text_evidence",
            "source_context_evidence",
            "review_reason",
            "previous_context",
            "containing_context",
            "following_context",
        ]
    )
    temporary = output.with_suffix(".csv.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, output)
    return len(rows)


def _special_1546_audit_rows(
    connection: sqlite3.Connection, project_root: Path, run_id: int
) -> list[dict[str, Any]]:
    target_am = 3 * 60 + 46
    target_pm = 15 * 60 + 46
    standard_cache = _SourceContextCache(project_root)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    audits = list(
        connection.execute(
            """
            SELECT * FROM phase2c_candidate_audit
            WHERE run_id = ?
              AND (possible_minute_am = ? OR possible_minute_pm = ?)
            ORDER BY source_candidate_type, source_candidate_id
            """,
            (run_id, target_am, target_pm),
        )
    )
    for audit in audits:
        key = (str(audit["source_candidate_type"]), int(audit["source_candidate_id"]))
        seen.add(key)
        content, _, containing, _ = _review_source_context(
            connection, project_root, key[0], key[1], standard_cache
        )
        quote_id = audit["canonical_quote_id"]
        active = bool(
            quote_id is not None
            and connection.execute(
                """
                SELECT 1 FROM quote_minute_eligibility
                WHERE quote_id = ? AND minute_of_day = ?
                """,
                (quote_id, target_pm),
            ).fetchone()
        )
        planned = bool(
            connection.execute(
                """
                SELECT 1 FROM phase2c_plan_relationships
                WHERE run_id = ? AND scenario = 'C'
                  AND source_candidate_type = ? AND source_candidate_id = ?
                  AND minute_of_day = ?
                """,
                (run_id, key[0], key[1], target_pm),
            ).fetchone()
        )
        current_rejection = content.get("rejection_reason")
        rows.append(
            {
                "source_candidate_type": key[0],
                "source_candidate_id": key[1],
                "quote": content["quote"],
                "title": audit["title"],
                "author": audit["author"],
                "time_phrase": audit["time_text"],
                "parser_result": audit["parser_result"],
                "current_rejection_reason": current_rejection,
                "displayed_text_daypart_evidence": audit["displayed_evidence_text"],
                "source_context_daypart_evidence": audit["source_evidence_text"],
                "containing_source_context": containing,
                "duplicate_status": audit["duplicate_status"],
                "quality_status": audit["non_ampm_gate_status"],
                "phase2c_decision": audit["decision"],
                "eligible_for_15_46": "YES" if active or planned else "NO",
                "source_locator": audit["source_locator"],
            }
        )

    # Include already-resolved and rejected candidates at either 3:46 clock-face side.
    queries = (
        (
            _SOURCE_STANDARD_EBOOKS,
            """
            SELECT id FROM mined_candidates
            WHERE minute_of_day IN (?, ?) OR resolved_minute_of_day IN (?, ?)
            """,
        ),
        (
            _SOURCE_GUTENBERG,
            "SELECT id FROM gutenberg_candidates WHERE minute_of_day IN (?, ?)",
        ),
    )
    for source_type, query in queries:
        for item in connection.execute(
            query,
            (target_am, target_pm, target_am, target_pm)
            if source_type == _SOURCE_STANDARD_EBOOKS
            else (target_am, target_pm),
        ):
            key = (source_type, int(item["id"]))
            if key in seen:
                continue
            seen.add(key)
            content, _, containing, _ = _review_source_context(
                connection, project_root, source_type, key[1], standard_cache
            )
            detection, reason = _matching_detection(
                str(content["quote"]), content["highlight_start"], content["highlight_end"]
            )
            quote_id = content.get("imported_quote_id")
            active = bool(
                quote_id is not None
                and connection.execute(
                    """
                    SELECT 1 FROM quote_minute_eligibility
                    WHERE quote_id = ? AND minute_of_day = ?
                    """,
                    (quote_id, target_pm),
                ).fetchone()
            )
            rows.append(
                {
                    "source_candidate_type": source_type,
                    "source_candidate_id": key[1],
                    "quote": content["quote"],
                    "title": content["title"],
                    "author": content["author"],
                    "time_phrase": content["time_text"],
                    "parser_result": detection.confidence.value if detection else reason,
                    "current_rejection_reason": content.get("rejection_reason"),
                    "displayed_text_daypart_evidence": (
                        detection.ampm_evidence if detection is not None else None
                    ),
                    "source_context_daypart_evidence": None,
                    "containing_source_context": containing,
                    "duplicate_status": content.get("duplicate_status", "CANONICAL"),
                    "quality_status": content.get("review_status", content.get("quality_status")),
                    "phase2c_decision": "ALREADY_RESOLVED_OR_REJECTED",
                    "eligible_for_15_46": "YES" if active else "NO",
                    "source_locator": content.get("source_locator"),
                }
            )
    rows.sort(key=lambda row: (str(row["source_candidate_type"]), int(row["source_candidate_id"])))
    return rows


def write_phase2c_1546_audit(
    connection: sqlite3.Connection, project_root: Path, run_id: int
) -> int:
    rows = _special_1546_audit_rows(connection, project_root, run_id)
    output = project_root / "data" / "generated" / "PHASE2C_1546_AUDIT.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else ["source_candidate_type", "source_candidate_id"]
    temporary = output.with_suffix(".csv.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, output)
    return len(rows)


def validate_phase2c_integrity(connection: sqlite3.Connection, run_id: int) -> dict[str, int]:
    missing_semantics = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM quotes AS q
            WHERE q.quality_status IN ('VERIFIED_EXACT', 'VERIFIED_NORMALIZED')
              AND NOT EXISTS (SELECT 1 FROM quote_time_semantics AS s WHERE s.quote_id = q.id)
            """
        ).fetchone()[0]
    )
    missing_eligibility = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM quotes AS q
            WHERE q.quality_status IN ('VERIFIED_EXACT', 'VERIFIED_NORMALIZED')
              AND NOT EXISTS (SELECT 1 FROM quote_minute_eligibility AS e WHERE e.quote_id = q.id)
            """
        ).fetchone()[0]
    )
    import_failures = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM phase2c_candidate_audit AS a
            JOIN phase2c_plan_relationships AS p
              ON p.run_id = a.run_id
             AND p.source_candidate_type = a.source_candidate_type
             AND p.source_candidate_id = a.source_candidate_id
            LEFT JOIN quotes AS q ON q.id = a.canonical_quote_id
            WHERE a.run_id = ? AND p.scenario = 'C'
              AND (q.id IS NULL
                   OR q.quality_status NOT IN ('VERIFIED_EXACT', 'VERIFIED_NORMALIZED')
                   OR q.highlight_start IS NULL OR q.highlight_end IS NULL
                   OR q.highlight_start < 0 OR q.highlight_end <= q.highlight_start
                   OR q.highlight_end > length(q.quote))
            """,
            (run_id,),
        ).fetchone()[0]
    )
    provenance_failures = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM phase2c_candidate_audit AS a
            WHERE a.run_id = ? AND a.canonical_quote_id IS NOT NULL
              AND a.source_candidate_type != 'LEGACY_CANONICAL'
              AND EXISTS (
                  SELECT 1 FROM phase2c_plan_relationships AS p
                  WHERE p.run_id = a.run_id AND p.scenario = 'C'
                    AND p.source_candidate_type = a.source_candidate_type
                    AND p.source_candidate_id = a.source_candidate_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM quote_provenance AS provenance
                  WHERE provenance.quote_id = a.canonical_quote_id
              )
            """,
            (run_id,),
        ).fetchone()[0]
    )
    run = connection.execute(
        "SELECT scenario_c_json FROM phase2c_runs WHERE id = ?", (run_id,)
    ).fetchone()
    expected = json.loads(run["scenario_c_json"])
    stats = calculate_stats(connection)
    counterfactual_mismatch = int(
        stats["total_canonical_quotes"] != expected["canonical_quotes"]
        or stats["unique_selectable_quotes"] != expected["unique_selectable_quotes"]
        or stats["quote_minute_eligibility_relationships"]
        != expected["quote_minute_eligibility_relationships"]
        or stats["remaining_quote_deficit_to_7"] != expected["remaining_deficit_to_7"]
    )
    result = {
        "missing_semantics": missing_semantics,
        "missing_eligibility": missing_eligibility,
        "import_failures": import_failures,
        "provenance_failures": provenance_failures,
        "counterfactual_mismatch": counterfactual_mismatch,
    }
    if sum(result.values()):
        raise ValueError(f"Phase 2C integrity failure: {result}")
    return result


def record_phase2c_verification(project_root: Path, payload: dict[str, Any]) -> Path:
    output = project_root / "data" / "generated" / "phase2c_verification.json"
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    return output


def write_phase2c_outputs(
    connection: sqlite3.Connection, project_root: Path, run_id: int
) -> dict[str, Any]:
    stats = calculate_stats(connection)
    generated = project_root / "data" / "generated"
    write_reports(stats, generated)
    review_count = export_phase2c_context_review(connection, project_root, run_id)
    special_count = write_phase2c_1546_audit(connection, project_root, run_id)
    integrity = validate_phase2c_integrity(connection, run_id)
    run = connection.execute("SELECT * FROM phase2c_runs WHERE id = ?", (run_id,)).fetchone()
    scenarios = {
        "A": json.loads(run["baseline_json"]),
        "B": json.loads(run["scenario_b_json"]),
        "C": json.loads(run["scenario_c_json"]),
    }
    audit_total = int(
        connection.execute(
            "SELECT COUNT(*) FROM phase2c_candidate_audit WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
    )
    gates_passed = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM phase2c_candidate_audit
            WHERE run_id = ? AND non_ampm_gate_status = 'PASS'
            """,
            (run_id,),
        ).fetchone()[0]
    )
    dual_activated = int(
        connection.execute(
            """
            SELECT COUNT(DISTINCT a.source_candidate_type || ':' || a.source_candidate_id)
            FROM phase2c_candidate_audit AS a
            JOIN phase2c_plan_relationships AS p
              ON p.run_id = a.run_id
             AND p.source_candidate_type = a.source_candidate_type
             AND p.source_candidate_id = a.source_candidate_id
            WHERE a.run_id = ? AND p.scenario = 'C' AND a.decision = 'DUAL_ELIGIBLE'
            """,
            (run_id,),
        ).fetchone()[0]
    )
    dual_relationship_quotes = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT quote_id FROM quote_minute_eligibility
                WHERE eligibility_type IN ('CLOCKFACE_SHARED_AM', 'CLOCKFACE_SHARED_PM')
                GROUP BY quote_id HAVING COUNT(DISTINCT minute_of_day) = 2
            )
            """
        ).fetchone()[0]
    )
    source_counts = dict(
        connection.execute(
            """
            SELECT source_candidate_type, COUNT(*) FROM phase2c_candidate_audit
            WHERE run_id = ? GROUP BY source_candidate_type
            """,
            (run_id,),
        )
    )
    decision_counts = dict(
        connection.execute(
            """
            SELECT decision, COUNT(*) FROM phase2c_candidate_audit
            WHERE run_id = ? GROUP BY decision
            """,
            (run_id,),
        )
    )
    pairs = _paired_improvements(scenarios["A"], scenarios["C"])[:50]
    pair_lines = [
        "| Rank | Pair | Strict model | Shared model | Improvement |",
        "|---:|---|---:|---:|---:|",
    ]
    for rank, row in enumerate(pairs, 1):
        pair_lines.append(
            f"| {rank} | {row['pair']} | {row['before_am']} + {row['before_pm']} | "
            f"{row['after_am']} + {row['after_pm']} | {row['improvement']} |"
        )
    thresholds = stats["minute_thresholds"]
    count_1546 = next(
        int(row["renderable_count"])
        for row in stats["minute_coverage"]
        if row["time_24h"] == "15:46"
    )
    active_1546 = connection.execute(
        """
        SELECT q.title, q.author, q.time_text, e.eligibility_type,
               e.evidence_type, e.evidence_text
        FROM quote_minute_eligibility AS e
        JOIN quotes AS q ON q.id = e.quote_id
        WHERE e.minute_of_day = 946
          AND q.quality_status IN ('VERIFIED_EXACT', 'VERIFIED_NORMALIZED')
        ORDER BY q.id LIMIT 1
        """
    ).fetchone()
    active_1546_lines = []
    if active_1546 is not None:
        active_1546_lines = [
            "",
            f"The active phrase is **{active_1546['time_text']}** in "
            f"*{active_1546['title']}* by {active_1546['author']}. It is a "
            f"`{active_1546['eligibility_type']}` relationship supported by "
            f"`{active_1546['evidence_type']}`: {active_1546['evidence_text']}.",
        ]
    verification_path = generated / "phase2c_verification.json"
    verification = (
        json.loads(verification_path.read_text(encoding="utf-8"))
        if verification_path.exists()
        else {"status": "PENDING FINAL VERIFICATION"}
    )
    improvement = (
        scenarios["A"]["remaining_deficit_to_7"] - scenarios["C"]["remaining_deficit_to_7"]
    )
    imbalance_before = sum(
        abs(
            scenarios["A"]["effective_counts"][minute]
            - scenarios["A"]["effective_counts"][minute + 720]
        )
        for minute in range(720)
    )
    imbalance_after = sum(
        abs(
            scenarios["C"]["effective_counts"][minute]
            - scenarios["C"]["effective_counts"][minute + 720]
        )
        for minute in range(720)
    )
    recommendation = (
        "No additional corpus is needed for coverage."
        if thresholds["below_7"] == 0
        else "Another independent corpus is still necessary after prioritized contextual review; "
        "the shared-clock policy materially improves coverage but does not close every bucket."
    )
    lines = [
        "# Literary Clock Phase 2C Report",
        "",
        f"Generated: {_now()}",
        "",
        "## Rationale and semantic boundary",
        "",
        "A neutral exact 12-hour expression identifies one clock-face position, not a narrative",
        "AM/PM claim. One canonical quote may therefore have two display eligibility "
        "relationships.",
        "Explicit meridiem, deterministic displayed daypart, or deterministic trusted source "
        "context",
        "continues to select one side only. Approximate, range, duplicate, false-positive, "
        "malformed",
        "highlight, provenance, and literary-quality gates are unchanged.",
        "",
        "Displayed excerpt semantics and surrounding source-context semantics were audited "
        "separately.",
        "Vague nearby daypart words were not inferred and therefore remain daypart-neutral.",
        "Conflicting cues and cases without trusted source context remain in human review.",
        "",
        "## Re-evaluation",
        "",
        f"- Ambiguous/applicable records re-evaluated: **{audit_total:,}**",
        f"- Standard Ebooks: **{source_counts.get(_SOURCE_STANDARD_EBOOKS, 0):,}**",
        f"- Project Gutenberg: **{source_counts.get(_SOURCE_GUTENBERG, 0):,}**",
        f"- Applicable legacy canonical records: **{source_counts.get(_SOURCE_LEGACY, 0):,}**",
        f"- Passing every unchanged non-AM/PM gate: **{gates_passed:,}**",
        f"- Daypart-neutral dual candidates found: **{decision_counts.get('DUAL_ELIGIBLE', 0):,}**",
        f"- Deterministically context-resolved: **{decision_counts.get('CONTEXT_RESOLVED', 0):,}**",
        "- Contextual uncertainty retained for review: "
        f"**{decision_counts.get('CONTEXT_REVIEW', 0):,}**",
        f"- Rejected by unchanged gates: **{decision_counts.get('REJECTED', 0):,}**",
        f"- Dual candidates activated for at least one useful relationship: **{dual_activated:,}**",
        "- Canonical quotes receiving both AM and PM relationships: "
        f"**{dual_relationship_quotes:,}**",
        "",
        "## Counterfactual and activated coverage",
        "",
        *_scenario_table(scenarios),
        "",
        f"The diversity-aware policy removed **{improvement:,}** effective quote deficits. "
        "Pairwise",
        f"AM/PM absolute imbalance fell from **{imbalance_before:,}** to **{imbalance_after:,}**.",
        "This demonstrates that the old unique-AM/PM restriction was a substantial, but not sole,",
        "cause of sparsity.",
        "",
        "## Current corpus terminology",
        "",
        f"- Canonical literary quotes: **{stats['total_canonical_quotes']:,}**",
        f"- Unique selectable quotes: **{stats['unique_selectable_quotes']:,}**",
        "- Quote-minute eligibility relationships: "
        f"**{stats['quote_minute_eligibility_relationships']:,}**",
        "- Effective candidates summed across minute pools: "
        f"**{stats['total_effective_candidates']:,}**",
        f"- Minutes at 0: **{thresholds['zero']:,}**",
        f"- Minutes below 3: **{thresholds['below_3']:,}**",
        f"- Minutes below 5: **{thresholds['below_5']:,}**",
        f"- Minutes below 7: **{thresholds['below_7']:,}**",
        f"- Minutes at least 7: **{thresholds['at_least_7']:,}**",
        f"- Remaining effective deficit to seven: **{stats['remaining_quote_deficit_to_7']:,}**",
        "",
        "## 15:46 audit",
        "",
        f"15:46 now has **{count_1546}** effective candidate(s). The dedicated audit contains",
        f"**{special_count}** candidate records and preserves every rejection/evidence decision in",
        "`data/generated/PHASE2C_1546_AUDIT.csv`.",
        *active_1546_lines,
        "",
        "## Human review",
        "",
        f"**{review_count:,}** daypart-uncertain records can still improve a bucket below seven "
        "and",
        "are exported in `data/generated/PHASE2C_CONTEXT_REVIEW.csv`.",
        "",
        "## Selector and integrity verification",
        "",
        f"- Verification status: **{verification.get('status', 'PENDING')}**",
        f"- pytest: {verification.get('pytest', 'pending')}",
        f"- Ruff check: {verification.get('ruff_check', 'pending')}",
        f"- Ruff format: {verification.get('ruff_format', 'pending')}",
        f"- SQLite integrity: {verification.get('sqlite_integrity', 'pending')}",
        "- Selector/global cooldown tests: "
        f"{verification.get('selector_global_cooldown', 'pending')}",
        f"- Phase 2C semantic/provenance integrity: **{integrity}**",
        "",
        "The selector uses one global quote ID across both minute pools. A 24-hour exact-quote",
        "cooldown is evaluated from persistent display history before book and author cooldowns,",
        "with graceful relaxation only when no alternative exists.",
        "",
        "## 50 paired buckets with the largest improvement",
        "",
        *pair_lines,
        "",
        "## Recommendation",
        "",
        recommendation,
        "",
    ]
    report = generated / "PHASE2C_REPORT.md"
    temporary = report.with_suffix(".md.tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    os.replace(temporary, report)
    machine = {
        "generated_at": _now(),
        "run_id": run_id,
        "ambiguous_re_evaluated": audit_total,
        "non_ampm_gates_passed": gates_passed,
        "decision_counts": decision_counts,
        "dual_candidates_activated": dual_activated,
        "quotes_with_two_shared_relationships": dual_relationship_quotes,
        "context_review_records": review_count,
        "special_1546_records": special_count,
        "counterfactual": scenarios,
        "coverage": {key: value for key, value in stats.items() if key != "minute_coverage"},
        "integrity": integrity,
        "verification": verification,
    }
    (generated / "phase2c_stats.json").write_text(
        json.dumps(machine, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return {
        "report": str(report),
        "review_count": review_count,
        "special_1546_count": special_count,
        "stats": stats,
        "integrity": integrity,
    }
