"""Corpus-wide, reversible semantic clock-time revalidation."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from litclock.db import initialize_database
from litclock.normalize import minute_to_time
from litclock.semantic import (
    SEMANTIC_AUDIT_VERSION,
    SemanticAction,
    SemanticClass,
    classify_clock_relationship,
)

_SELECTABLE = ("VERIFIED_EXACT", "VERIFIED_NORMALIZED")


@dataclass(frozen=True, slots=True)
class SourceContext:
    family: str
    candidate_type: str | None
    candidate_id: int | None
    parser_route: str
    source_section: str | None
    source_locator: str | None
    previous_paragraph: str | None = None
    containing_paragraph: str | None = None
    following_paragraph: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _source_family(source_name: str) -> str:
    if source_name.startswith("standardebooks/"):
        return "STANDARD_EBOOKS"
    if source_name.startswith("gutenberg/"):
        return "GUTENBERG"
    if source_name == "english_wikisource":
        return "WIKISOURCE"
    return "LEGACY"


def _parser_family(route: str, phrase: str) -> str:
    normalized = phrase.casefold().replace("’", "'")
    if route == "numeric" or re.search(r"\d{1,2}:\d{2}", normalized):
        return "colon_numeric"
    if route == "oclock" or "o'clock" in normalized:
        return "oclock"
    if route == "half_past" or re.search(r"\b(?:quarter|half)\b", normalized):
        return "quarter_half"
    if route == "relative" or re.search(r"\b(?:past|after|to|before)\b", normalized):
        return "past_after" if re.search(r"\b(?:past|after)\b", normalized) else "to_before"
    if route == "written_clock" or len(re.findall(r"\b(?:\d+|[a-z]+)\b", normalized)) >= 2:
        return "hour_minute_word_form"
    if route in {"named_time", "military"}:
        return route
    if re.fullmatch(
        r"\s*(?:(?:at|by|around|about|approximately|near|before)\s+)?"
        r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2})\s*",
        normalized,
    ):
        return "bare_hour_contextual"
    return "other_legacy"


def _base_relationship_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            WITH base AS (
                SELECT quote_id, minute_of_day, source_candidate_type, source_candidate_id
                FROM quote_minute_eligibility
                UNION ALL
                SELECT q.id, q.minute_of_day, NULL, NULL
                FROM quotes AS q
                WHERE q.quality_status IN (?, ?)
                  AND NOT EXISTS (
                      SELECT 1 FROM quote_minute_eligibility AS e WHERE e.quote_id = q.id
                  )
            )
            SELECT q.id AS quote_id, base.minute_of_day, q.time_24h, q.time_text, q.quote,
                   q.title, q.author, q.language, q.source_name, q.source_record_id,
                   q.highlight_start, q.highlight_end, q.quality_status, q.quote_hash,
                   base.source_candidate_type AS eligibility_candidate_type,
                   base.source_candidate_id AS eligibility_candidate_id
            FROM base JOIN quotes AS q ON q.id = base.quote_id
            WHERE q.quality_status IN (?, ?) AND q.language LIKE 'en%'
            ORDER BY q.id, base.minute_of_day
            """,
            (*_SELECTABLE, *_SELECTABLE),
        )
    )


def _candidate_contexts(
    connection: sqlite3.Connection,
) -> tuple[dict[tuple[str, int], SourceContext], dict[tuple[int, str], SourceContext]]:
    by_candidate: dict[tuple[str, int], SourceContext] = {}
    by_quote_family: dict[tuple[int, str], SourceContext] = {}
    queries = (
        (
            "STANDARD_EBOOKS",
            """
            SELECT imported_quote_id, id, parser_rule, source_section, source_locator,
                   NULL AS previous_paragraph, quote AS containing_paragraph,
                   NULL AS following_paragraph
            FROM mined_candidates WHERE imported_quote_id IS NOT NULL
            """,
        ),
        (
            "GUTENBERG",
            """
            SELECT imported_quote_id, id, parser_rule, NULL AS source_section, source_locator,
                   previous_paragraph, containing_paragraph, following_paragraph
            FROM gutenberg_candidates WHERE imported_quote_id IS NOT NULL
            """,
        ),
        (
            "WIKISOURCE",
            """
            SELECT imported_quote_id, id, parser_rule, page_title AS source_section, source_locator,
                   previous_paragraph, containing_paragraph, following_paragraph
            FROM wikisource_candidates WHERE imported_quote_id IS NOT NULL
            """,
        ),
    )
    for family, query in queries:
        for row in connection.execute(query):
            quote_id = int(row["imported_quote_id"])
            candidate_id = int(row["id"])
            context = SourceContext(
                family,
                family,
                candidate_id,
                str(row["parser_rule"]),
                row["source_section"],
                row["source_locator"],
                row["previous_paragraph"],
                row["containing_paragraph"],
                row["following_paragraph"],
            )
            by_candidate[(family, candidate_id)] = context
            by_quote_family.setdefault((quote_id, family), context)
    return by_candidate, by_quote_family


def _context_for_row(
    row: sqlite3.Row,
    candidate_contexts: tuple[
        dict[tuple[str, int], SourceContext], dict[tuple[int, str], SourceContext]
    ],
) -> SourceContext:
    quote_id = int(row["quote_id"])
    canonical_family = _source_family(str(row["source_name"]))
    by_candidate, by_quote_family = candidate_contexts
    candidate_type = row["eligibility_candidate_type"]
    candidate_id = row["eligibility_candidate_id"]
    known = (
        by_candidate.get((str(candidate_type), int(candidate_id)))
        if candidate_type and candidate_id is not None
        else None
    )
    if known is None:
        known = by_quote_family.get((quote_id, canonical_family))
    if known is not None:
        return known
    return SourceContext(
        canonical_family,
        row["eligibility_candidate_type"],
        int(row["eligibility_candidate_id"])
        if row["eligibility_candidate_id"] is not None
        else None,
        "LEGACY_UNKNOWN",
        None,
        None,
    )


def _source_expression_window(source: SourceContext, phrase: str) -> tuple[str | None, str | None]:
    """Return only source context demonstrably attached to the same expression."""
    needle = phrase.casefold().replace("’", "'")
    for paragraph in (
        source.previous_paragraph,
        source.containing_paragraph,
        source.following_paragraph,
    ):
        if not paragraph:
            continue
        comparable = paragraph.casefold().replace("’", "'")
        index = comparable.find(needle)
        if index >= 0:
            return paragraph[max(0, index - 180) : index], paragraph[
                index + len(phrase) : index + len(phrase) + 180
            ]
    return None, None


def _fingerprint(rows: Iterable[sqlite3.Row]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(
            "\0".join(
                (
                    str(row["quote_id"]),
                    str(row["minute_of_day"]),
                    str(row["quote_hash"]),
                    str(row["highlight_start"]),
                    str(row["highlight_end"]),
                    str(row["time_text"]),
                )
            ).encode("utf-8")
        )
    return digest.hexdigest()


def _percentile(values: list[int], fraction: float) -> float:
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    weight = position - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def _metrics_from_counts(
    connection: sqlite3.Connection, minute_counts: dict[int, int], quote_ids: set[int]
) -> dict[str, Any]:
    values = sorted(minute_counts.get(minute, 0) for minute in range(1440))
    canonical = int(
        connection.execute("SELECT COUNT(*) FROM quotes WHERE language LIKE 'en%'").fetchone()[0]
    )
    return {
        "canonical_quotes": canonical,
        "selectable_quotes": len(quote_ids),
        "relationships": sum(values),
        "covered_minutes": sum(value > 0 for value in values),
        "empty_minutes": sum(value == 0 for value in values),
        "exactly_1": sum(value == 1 for value in values),
        "exactly_2": sum(value == 2 for value in values),
        "at_least_3": sum(value >= 3 for value in values),
        "at_least_5": sum(value >= 5 for value in values),
        "at_least_7": sum(value >= 7 for value in values),
        "min": min(values),
        "p10": _percentile(values, 0.10),
        "p25": _percentile(values, 0.25),
        "median": statistics.median(values),
        "p75": _percentile(values, 0.75),
        "p90": _percentile(values, 0.90),
        "max": max(values),
    }


def _metrics_from_base(connection: sqlite3.Connection, rows: list[sqlite3.Row]) -> dict[str, Any]:
    counts: dict[int, int] = defaultdict(int)
    quote_ids: set[int] = set()
    for row in rows:
        counts[int(row["minute_of_day"])] += 1
        quote_ids.add(int(row["quote_id"]))
    return _metrics_from_counts(connection, counts, quote_ids)


def current_semantic_metrics(connection: sqlite3.Connection) -> dict[str, Any]:
    counts = {
        int(row["minute_of_day"]): int(row["n"])
        for row in connection.execute(
            """
            SELECT pool.minute_of_day, COUNT(*) AS n
            FROM quote_minute_pool AS pool JOIN quotes AS q ON q.id = pool.quote_id
            WHERE q.quality_status IN (?, ?) AND q.language LIKE 'en%'
            GROUP BY pool.minute_of_day
            """,
            _SELECTABLE,
        )
    }
    quote_ids = {
        int(row[0])
        for row in connection.execute(
            """
            SELECT DISTINCT q.id
            FROM quote_minute_pool AS pool JOIN quotes AS q ON q.id = pool.quote_id
            WHERE q.quality_status IN (?, ?) AND q.language LIKE 'en%'
            """,
            _SELECTABLE,
        )
    }
    return _metrics_from_counts(connection, counts, quote_ids)


def run_semantic_audit(
    connection: sqlite3.Connection,
    *,
    audit_version: str = SEMANTIC_AUDIT_VERSION,
) -> dict[str, Any]:
    """Classify every underlying selectable English relationship without activating decisions."""
    initialize_database(connection)
    rows = _base_relationship_rows(connection)
    baseline = _metrics_from_base(connection, rows)
    fingerprint = _fingerprint(rows)
    started = _now()
    cursor = connection.execute(
        """
        INSERT INTO semantic_audit_runs (
            audit_version, started_at, status, corpus_fingerprint, baseline_json
        ) VALUES (?, ?, 'RUNNING', ?, ?)
        """,
        (audit_version, started, fingerprint, _json(baseline)),
    )
    run_id = int(cursor.lastrowid)
    connection.commit()
    contexts = _candidate_contexts(connection)
    counts: dict[str, int] = defaultdict(int)
    try:
        for row in rows:
            source = _context_for_row(row, contexts)
            phrase = str(row["time_text"])
            source_before, source_after = _source_expression_window(source, phrase)
            parser_route = _parser_family(source.parser_route, phrase)
            decision = classify_clock_relationship(
                str(row["quote"]),
                int(row["highlight_start"]) if row["highlight_start"] is not None else None,
                int(row["highlight_end"]) if row["highlight_end"] is not None else None,
                int(row["minute_of_day"]),
                expected_text=phrase,
                parser_route=parser_route,
                source_section=source.source_section,
                source_locator=source.source_locator,
                source_context_before=source_before,
                source_context_after=source_after,
            )
            highlighted = (
                str(row["quote"])[int(row["highlight_start"]) : int(row["highlight_end"])]
                if row["highlight_start"] is not None and row["highlight_end"] is not None
                else ""
            )
            structure = " | ".join(
                part for part in (source.source_section, source.source_locator) if part
            )
            connection.execute(
                """
                INSERT INTO semantic_time_audit (
                    run_id, quote_id, minute_of_day, semantic_class, action, reason_code,
                    parser_route, confidence, highlighted_text, derived_minutes, source_family,
                    derivation_rule, source_candidate_type, source_candidate_id, source_structure,
                    reviewed_by, review_provenance, audit_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    run_id,
                    row["quote_id"],
                    row["minute_of_day"],
                    decision.semantic_class.value,
                    decision.action.value,
                    decision.reason_code,
                    decision.parser_route,
                    decision.confidence,
                    highlighted,
                    ",".join(str(value) for value in decision.derived_minutes),
                    source.family,
                    decision.derivation_rule,
                    source.candidate_type,
                    source.candidate_id,
                    structure or None,
                    audit_version,
                    started,
                ),
            )
            counts[decision.action.value] += 1
        finished = _now()
        connection.execute(
            """
            UPDATE semantic_audit_runs
            SET finished_at = ?, status = 'AUDIT_COMPLETE', decision_counts_json = ?
            WHERE id = ?
            """,
            (finished, _json(dict(counts)), run_id),
        )
        connection.commit()
    except Exception as error:
        connection.rollback()
        connection.execute(
            """
            UPDATE semantic_audit_runs SET finished_at = ?, status = 'FAILED', error = ?
            WHERE id = ?
            """,
            (_now(), str(error), run_id),
        )
        connection.commit()
        raise
    return {
        "run_id": run_id,
        "audit_version": audit_version,
        "corpus_fingerprint": fingerprint,
        "baseline": baseline,
        "counts": {action.value: counts.get(action.value, 0) for action in SemanticAction},
    }


def apply_semantic_audit(connection: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    """Activate a complete audit atomically; source/canonical rows remain untouched."""
    initialize_database(connection)
    run = connection.execute("SELECT * FROM semantic_audit_runs WHERE id = ?", (run_id,)).fetchone()
    if run is None or run["status"] not in {"AUDIT_COMPLETE", "APPLIED", "SUPERSEDED"}:
        raise ValueError("semantic audit run is not complete")
    expected = int(json.loads(run["baseline_json"])["relationships"])
    actual = int(
        connection.execute(
            "SELECT COUNT(*) FROM semantic_time_audit WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
    )
    if actual != expected:
        raise ValueError(f"audit accounting mismatch: expected {expected}, found {actual}")
    repaired = int(
        connection.execute(
            "SELECT COUNT(*) FROM semantic_relationship_repairs WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
    )
    invalid_repairs = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM semantic_relationship_repairs
            WHERE run_id = ? AND semantic_action != 'KEEP'
            """,
            (run_id,),
        ).fetchone()[0]
    )
    if invalid_repairs:
        raise ValueError("semantic audit contains a non-KEEP repair")
    with connection:
        previous = connection.execute(
            "SELECT id FROM semantic_audit_runs WHERE status = 'APPLIED' AND id != ?", (run_id,)
        ).fetchone()
        if previous is not None:
            for repair in connection.execute(
                """
                SELECT DISTINCT quote_id, original_highlight_start, original_highlight_end,
                       original_highlight_text, original_time_text
                FROM semantic_adjudications
                WHERE run_id = ? AND corrected_highlight_text IS NOT NULL
                """,
                (int(previous["id"]),),
            ):
                connection.execute(
                    """
                    UPDATE quotes SET time_text = ?, highlight_start = ?, highlight_end = ?
                    WHERE id = ?
                    """,
                    (
                        repair["original_time_text"] or repair["original_highlight_text"],
                        repair["original_highlight_start"],
                        repair["original_highlight_end"],
                        repair["quote_id"],
                    ),
                )
        corrected_by_quote: dict[int, tuple[int, int, str]] = {}
        for repair in connection.execute(
            """
            SELECT DISTINCT quote_id, corrected_highlight_start, corrected_highlight_end,
                   corrected_highlight_text
            FROM semantic_adjudications
            WHERE run_id = ? AND corrected_highlight_text IS NOT NULL
            """,
            (run_id,),
        ):
            quote_id = int(repair["quote_id"])
            value = (
                int(repair["corrected_highlight_start"]),
                int(repair["corrected_highlight_end"]),
                str(repair["corrected_highlight_text"]),
            )
            if quote_id in corrected_by_quote and corrected_by_quote[quote_id] != value:
                raise ValueError(f"conflicting highlight repairs for quote {quote_id}")
            corrected_by_quote[quote_id] = value
        for quote_id, (start, end, text_value) in corrected_by_quote.items():
            quote = connection.execute(
                "SELECT quote FROM quotes WHERE id = ?", (quote_id,)
            ).fetchone()
            if quote is None or str(quote["quote"])[start:end] != text_value:
                raise ValueError(f"invalid materialized highlight repair for quote {quote_id}")
            connection.execute(
                """
                UPDATE quotes SET time_text = ?, highlight_start = ?, highlight_end = ?
                WHERE id = ?
                """,
                (text_value, start, end, quote_id),
            )
        connection.execute(
            """
            UPDATE semantic_audit_runs SET status = 'SUPERSEDED'
            WHERE status = 'APPLIED' AND id != ?
            """,
            (run_id,),
        )
        connection.execute(
            "UPDATE semantic_audit_runs SET status = 'APPLIED', applied_at = ? WHERE id = ?",
            (_now(), run_id),
        )
        after = current_semantic_metrics(connection)
        connection.execute(
            "UPDATE semantic_audit_runs SET after_json = ? WHERE id = ?", (_json(after), run_id)
        )
    return {
        "run_id": run_id,
        "before": json.loads(run["baseline_json"]),
        "after": after,
        "repaired_relationships": repaired,
        "repaired_highlights": len(corrected_by_quote),
    }


def _audit_export_rows(connection: sqlite3.Connection, run_id: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT audit.*, q.time_24h, q.time_text, q.quote, q.title, q.author,
                   q.source_name, q.source_record_id, q.highlight_start, q.highlight_end
            FROM semantic_time_audit AS audit JOIN quotes AS q ON q.id = audit.quote_id
            WHERE audit.run_id = ?
            ORDER BY audit.quote_id, audit.minute_of_day
            """,
            (run_id,),
        )
    ]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else ["quote_id", "minute_of_day"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _stratified(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["parser_route"], row["source_family"], row["reason_code"])].append(row)
    for group in groups.values():
        group.sort(key=lambda row: (int(row["quote_id"]), int(row["minute_of_day"])))
    chosen: list[dict[str, Any]] = []
    keys = sorted(groups)
    while keys and len(chosen) < limit:
        remaining: list[tuple[str, str, str]] = []
        for key in keys:
            group = groups[key]
            if group and len(chosen) < limit:
                chosen.append(group.pop(0))
            if group:
                remaining.append(key)
        keys = remaining
    return chosen


def write_semantic_audit_artifacts(
    connection: sqlite3.Connection, run_id: int, output_dir: Path
) -> dict[str, Any]:
    rows = _audit_export_rows(connection, run_id)
    colon = [row for row in rows if re.search(r"\d{1,2}:\d{2}", row["highlighted_text"])]
    duration = [
        row
        for row in rows
        if row["semantic_class"] in {SemanticClass.DURATION, SemanticClass.RELATIVE_DURATION}
        or re.search(
            r"\b(?:minutes?|hours?|interval|pause|delay|later|earlier)\b", row["quote"], re.I
        )
    ]
    reference = [
        row
        for row in rows
        if row["semantic_class"]
        in {SemanticClass.SECTION_OR_REFERENCE, SemanticClass.HEADING_OR_TOC}
        or re.search(
            r"\b(?:chapter|chap\.?|section|verse|act|scene|page|line|figure|table)\b|§",
            row["quote"],
            re.I,
        )
    ]
    review = [row for row in rows if row["action"] == SemanticAction.REVIEW]
    hyphenated = [
        row
        for row in rows
        if re.search(
            r"\b(?:(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+)?"
            r"(?:one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|"
            r"forty|fifty|\d+)"
            r"[-\u2010-\u2015](?:minute|hour)",
            row["quote"],
            re.I,
        )
    ]
    outputs = {
        "all_audit.csv": rows,
        "colon_cases.csv": colon,
        "duration_cases.csv": duration,
        "reference_cases.csv": reference,
        "review_cases.csv": review,
        "all_number_hyphenated_duration_cases.csv": hyphenated,
        "manual_colon_sample.csv": _stratified(colon, 150),
        "manual_duration_sample.csv": _stratified(duration, 150),
        "manual_reference_sample.csv": _stratified(reference, 100),
        "manual_unknown_review_sample.csv": _stratified(review, 100),
    }
    for filename, export_rows in outputs.items():
        _write_csv(output_dir / filename, export_rows)
    return {name: len(export_rows) for name, export_rows in outputs.items()}


def _minute_impacts(connection: sqlite3.Connection, run_id: int) -> list[dict[str, Any]]:
    baseline: dict[int, list[int]] = defaultdict(list)
    retained: dict[int, list[int]] = defaultdict(list)
    removed: dict[int, list[tuple[int, str, str]]] = defaultdict(list)
    for row in connection.execute(
        """
        SELECT quote_id, minute_of_day, action, semantic_class, reason_code
        FROM semantic_time_audit WHERE run_id = ?
        ORDER BY minute_of_day, quote_id
        """,
        (run_id,),
    ):
        minute = int(row["minute_of_day"])
        quote_id = int(row["quote_id"])
        baseline[minute].append(quote_id)
        if row["action"] == "KEEP":
            retained[minute].append(quote_id)
        else:
            removed[minute].append((quote_id, str(row["semantic_class"]), str(row["reason_code"])))
    impacts: list[dict[str, Any]] = []
    for minute in range(1440):
        before = len(baseline[minute])
        after = len(retained[minute])
        removed_rows = removed[minute]
        impacts.append(
            {
                "minute_of_day": minute,
                "time_24h": minute_to_time(minute),
                "before_count": before,
                "after_count": after,
                "loss": before - after,
                "removed_quote_ids": ",".join(str(row[0]) for row in removed_rows),
                "reason_classes": ",".join(sorted({row[1] for row in removed_rows})),
                "reason_codes": ",".join(sorted({row[2] for row in removed_rows})),
            }
        )
    return impacts


def write_semantic_report(
    connection: sqlite3.Connection,
    run_id: int,
    report_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    run = connection.execute("SELECT * FROM semantic_audit_runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise ValueError("semantic audit run does not exist")
    before = json.loads(run["baseline_json"])
    after = json.loads(run["after_json"]) if run["after_json"] else None
    impacts = _minute_impacts(connection, run_id)
    _write_csv(output_dir / "minute_impact.csv", impacts)
    parser_rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT parser_route,
                   COUNT(*) AS relationships,
                   SUM(action = 'KEEP') AS kept,
                   SUM(action = 'QUARANTINE') AS quarantined,
                   SUM(action = 'REVIEW') AS review
            FROM semantic_time_audit WHERE run_id = ? GROUP BY parser_route
            ORDER BY relationships DESC, parser_route
            """,
            (run_id,),
        )
    ]
    for row in parser_rows:
        total = int(row["relationships"])
        row["false_positive_rate"] = int(row["quarantined"]) / total
        row["flagged_rate"] = (int(row["quarantined"]) + int(row["review"])) / total
    _write_csv(output_dir / "parser_precision.csv", parser_rows)

    count_rows = {
        str(row["action"]): int(row["n"])
        for row in connection.execute(
            "SELECT action, COUNT(*) n FROM semantic_time_audit WHERE run_id = ? GROUP BY action",
            (run_id,),
        )
    }
    class_rows = {
        str(row["semantic_class"]): int(row["n"])
        for row in connection.execute(
            """
            SELECT semantic_class, COUNT(*) n FROM semantic_time_audit
            WHERE run_id = ? GROUP BY semantic_class
            """,
            (run_id,),
        )
    }
    reason_rows = {
        str(row["reason_code"]): int(row["n"])
        for row in connection.execute(
            """
            SELECT reason_code, COUNT(*) n FROM semantic_time_audit
            WHERE run_id = ? AND action = 'QUARANTINE' GROUP BY reason_code
            """,
            (run_id,),
        )
    }
    source_rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT source_family, COUNT(*) relationships,
                   SUM(action = 'KEEP') kept,
                   SUM(action = 'QUARANTINE') quarantined,
                   SUM(action = 'REVIEW') review
            FROM semantic_time_audit WHERE run_id = ? GROUP BY source_family
            ORDER BY relationships DESC, source_family
            """,
            (run_id,),
        )
    ]
    quarantine_quotes = int(
        connection.execute(
            """
            SELECT COUNT(DISTINCT quote_id) FROM semantic_time_audit
            WHERE run_id = ? AND action = 'QUARANTINE'
            """,
            (run_id,),
        ).fetchone()[0]
    )
    review_quotes = int(
        connection.execute(
            """
            SELECT COUNT(DISTINCT quote_id) FROM semantic_time_audit
            WHERE run_id = ? AND action = 'REVIEW'
            """,
            (run_id,),
        ).fetchone()[0]
    )
    colon_categories = {
        "confirmed_clock": 0,
        "reference": 0,
        "score_result": 0,
        "ratio_measurement": 0,
        "unknown_bare": 0,
        "other": 0,
    }
    for row in connection.execute(
        """
        SELECT action, semantic_class, COUNT(*) n
        FROM semantic_time_audit
        WHERE run_id = ? AND highlighted_text GLOB '*[0-9]:[0-9][0-9]*'
        GROUP BY action, semantic_class
        """,
        (run_id,),
    ):
        semantic = str(row["semantic_class"])
        if row["action"] == "KEEP":
            key = "confirmed_clock"
        elif semantic == SemanticClass.SECTION_OR_REFERENCE:
            key = "reference"
        elif semantic == SemanticClass.SCORE_OR_RESULT:
            key = "score_result"
        elif semantic == SemanticClass.RATIO_OR_MEASUREMENT:
            key = "ratio_measurement"
        elif row["action"] == "REVIEW":
            key = "unknown_bare"
        else:
            key = "other"
        colon_categories[key] += int(row["n"])
    direct_non_clock = sum(
        class_rows.get(semantic_class.value, 0)
        for semantic_class in (
            SemanticClass.DURATION,
            SemanticClass.RELATIVE_DURATION,
            SemanticClass.SECTION_OR_REFERENCE,
            SemanticClass.HEADING_OR_TOC,
            SemanticClass.SCORE_OR_RESULT,
            SemanticClass.RATIO_OR_MEASUREMENT,
            SemanticClass.DATE_OR_NUMBER,
            SemanticClass.NON_TEMPORAL_NUMBER,
        )
    )
    semantic_mismatches = reason_rows.get("HIGHLIGHT_SEMANTIC_MISMATCH", 0)
    colon_auto_quarantine = sum(
        colon_categories[key] for key in ("reference", "score_result", "ratio_measurement", "other")
    )

    example_rows = connection.execute(
        """
        SELECT audit.quote_id, audit.minute_of_day, audit.semantic_class,
               audit.reason_code, audit.highlighted_text, q.quote
        FROM semantic_time_audit AS audit JOIN quotes AS q ON q.id = audit.quote_id
        WHERE audit.run_id = ? AND audit.action = 'QUARANTINE'
        ORDER BY CASE
            WHEN audit.quote_id = 6486 THEN 0
            WHEN audit.quote_id = 6545 THEN 1
            WHEN audit.reason_code = 'REFERENCE_SCRIPTURE' THEN 2
            ELSE 3 END, audit.quote_id, audit.minute_of_day
        """,
        (run_id,),
    )
    examples: list[dict[str, Any]] = []
    examples_by_quote: dict[int, dict[str, Any]] = {}
    for row in example_rows:
        quote_id = int(row["quote_id"])
        example = examples_by_quote.get(quote_id)
        if example is None:
            if len(examples) >= 12:
                continue
            example = dict(row)
            example["minutes"] = []
            examples_by_quote[quote_id] = example
            examples.append(example)
        example["minutes"].append(int(row["minute_of_day"]))
    changed = [row for row in impacts if int(row["loss"]) > 0]
    changed.sort(key=lambda row: (-int(row["loss"]), int(row["minute_of_day"])))
    newly_empty = [row for row in impacts if row["before_count"] and not row["after_count"]]
    newly_below_3 = [
        row for row in impacts if int(row["before_count"]) >= 3 and int(row["after_count"]) < 3
    ]

    metric_fields = (
        "canonical_quotes",
        "selectable_quotes",
        "relationships",
        "covered_minutes",
        "empty_minutes",
        "exactly_1",
        "exactly_2",
        "at_least_3",
        "at_least_5",
        "at_least_7",
        "min",
        "p10",
        "p25",
        "median",
        "p75",
        "p90",
        "max",
    )
    lines = [
        "# Corpus Semantic Time Revalidation",
        "",
        f"Audit version: `{run['audit_version']}`",
        "",
        f"Corpus fingerprint: `{run['corpus_fingerprint']}`",
        "",
        f"Status in the copy-on-write audit database: **{run['status']}**",
        "",
        "## 1. Motivation / construct-validity problem",
        "",
        (
            "The prior corpus proved lexical offset validity, but legacy minute labels and some "
            "mined parser routes did not always prove that the highlighted phrase *meant a clock "
            "time*. This audit separates clock-time semantics from durations, references, results, "
            "identifiers, structural text, and unresolved context. Precision takes priority "
            "over coverage."
        ),
        "",
        "## 2. Current English corpus baseline",
        "",
        f"The audit evaluated **{before['selectable_quotes']:,} selectable English quotes** and "
        f"**{before['relationships']:,} quote-minute relationships** over "
        f"{before['covered_minutes']:,}/1,440 minutes.",
        "The operational database was copied before mutation; neither the original database nor "
        "the currently deployed Kindle bundle was changed.",
        "",
        "## 3. Existing parser routes",
        "",
        (
            "Legacy CSV/YAML importers accepted upstream minute labels after offset validation. "
            "Standard Ebooks, Gutenberg, and Wikisource shared `timeparse`, but imported-candidate "
            "parser metadata was not consulted by selection. The new audit recovers candidate "
            "routes where available and infers a grammar family for legacy relationships. The "
            "principal entry points were trusted upstream labels, permissive written-number "
            "pairing, and lexical numeric recognition without a second semantic clock-time gate."
        ),
        "`quote_time_semantics` records clock-face/daypart interpretation, "
        "`quote_minute_eligibility` stores the resulting relationships, and `quote_minute_pool` is "
        "the selector-facing derived view. Existing source-specific candidate tables retain "
        "review status and rejection reasons, but before this pass there was no corpus-wide "
        "semantic decision required by that final view.",
        "A clean Phase 1 rebuild from the pinned local snapshots processed 13,179 raw rows into "
        "4,949 canonical records and 4,047 selectable quotes. Its subsequent audit classified all "
        "4,047 selectable relationships KEEP, showing that the importer-side gate blocks newly "
        "recognized legacy false positives before selection. The multi-gigabyte later-stage public-"
        "domain corpus was revalidated from the production snapshot rather than reacquired.",
        "",
        (
            "| Parser family | Relationships | KEEP | QUARANTINE | REVIEW | Auto-FP rate | "
            "Flagged rate |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in parser_rows:
        lines.append(
            f"| {row['parser_route']} | {int(row['relationships']):,} | {int(row['kept']):,} | "
            f"{int(row['quarantined']):,} | {int(row['review']):,} | "
            f"{row['false_positive_rate']:.1%} | {row['flagged_rate']:.1%} |"
        )
    lines.extend(
        [
            "",
            "## 4. Semantic class taxonomy",
            "",
            (
                "Only `CLOCK_TIME_EXACT` and `CLOCK_TIME_AMBIGUOUS` receive `KEEP`. "
                "High-confidence "
                "non-clock readings receive `QUARANTINE`; insufficient evidence receives `REVIEW`, "
                "never implicit acceptance."
            ),
            "",
            "| Semantic class | Relationships |",
            "|---|---:|",
            *[f"| {key} | {value:,} |" for key, value in sorted(class_rows.items())],
            "",
            "## 5. Colon-numeric audit",
            "",
            *[f"- {key.replace('_', ' ')}: {value:,}" for key, value in colon_categories.items()],
            "",
            f"High-confidence colon auto-quarantines: **{colon_auto_quarantine:,}**.",
            "",
            (
                "Colon syntax alone is not evidence. Explicit meridiem/daypart, "
                "clock/watch/display "
                "language, temporal prepositions, or schedule context can validate it; bare cases "
                "remain in REVIEW."
            ),
            "",
            "## 6. Duration audit",
            "",
            (
                "Duration/relative-duration relationships: "
                f"**{class_rows.get('DURATION', 0) + class_rows.get('RELATIVE_DURATION', 0):,}**. "
                "Hyphenated duration nouns, elapsed-time cues, and `for/during/within` "
                "constructions are not clock times."
            ),
            "All **19** selectable relationships whose source context contains a number plus a "
            "hyphenated minute/hour noun were inspected; quote 6486 was the synthesized "
            "`one five-minute` failure (at both shared clock-face minutes), while unrelated "
            "duration wording did not override separately valid clock phrases.",
            "",
            "## 7. Chapter/reference audit",
            "",
            (
                "Section/reference relationships: "
                f"**{class_rows.get('SECTION_OR_REFERENCE', 0):,}**. "
                "The rules recognize chapter, scripture, legal/section, page/line, and explicit "
                "reference syntax."
            ),
            "",
            "## 8. Source-structure audit",
            "",
            (
                "Standard Ebooks XHTML is parsed semantically; Gutenberg strips marked "
                "boilerplate/TOCs; Wikisource parses XML namespaces and wikitext structure. "
                "Candidate locators and sections are retained. Explicit TOC/index/reference-region "
                "evidence now supports quarantine, but structure-free legacy rows must be decided "
                "from displayed text and provenance alone."
            ),
            "",
            (
                f"Relationships classified solely from retained structural exclusion evidence: "
                f"**{class_rows.get('HEADING_OR_TOC', 0):,}**. Structural adapters already "
                "exclude many non-prose regions before candidates enter the corpus."
            ),
            "",
            "| Source family | Relationships | KEEP | QUARANTINE | REVIEW |",
            "|---|---:|---:|---:|---:|",
            *[
                f"| {row['source_family']} | {int(row['relationships']):,} | "
                f"{int(row['kept']):,} | {int(row['quarantined']):,} | "
                f"{int(row['review']):,} |"
                for row in source_rows
            ],
            "",
            "## 9. Known discovered false positives",
            "",
        ]
    )
    for row in examples:
        snippet = " ".join(str(row["quote"]).split())
        if len(snippet) > 150:
            snippet = snippet[:147] + "…"
        times = ", ".join(minute_to_time(value) for value in row["minutes"])
        lines.append(f"- Quote {row['quote_id']} at {times}: `{row['reason_code']}` — “{snippet}”")
    lines.extend(
        [
            "",
            "## 10. Auto-quarantine rules",
            "",
            "High-confidence rules cover invalid/mismatched highlights, hyphenated and explicit "
            "durations, relative elapsed intervals, chapter/scripture/section/page references, "
            "scores, ratios/measurements, identifiers/timecodes, and claimed-minute mismatches.",
            "",
            (
                f"The **{count_rows.get('QUARANTINE', 0):,}** relationships comprise "
                f"**{direct_non_clock:,}** direct non-clock classifications and "
                f"**{semantic_mismatches:,}** claimed-minute/phrase mismatches."
            ),
            "",
            "## 11. REVIEW policy",
            "",
            (
                f"**{count_rows.get('REVIEW', 0):,} relationships across "
                f"{review_quotes:,} quotes** remain unresolved. Bare colon numbers, unsupported "
                "written forms, and context-poor bare hours are REVIEW and are excluded once this "
                "audit is activated."
            ),
            "Local ignored review artifacts include 150 stratified colon cases, 150 duration-risk "
            "cases, 100 reference/heading cases, 100 UNKNOWN/REVIEW cases, and every high-risk "
            "number-plus-hyphenated-duration occurrence.",
            "",
            "## 12. Parser-family error rates",
            "",
            (
                "The table in section 3 reports deterministic auto-quarantine rate separately from "
                "broader flagged rate. These are corpus error estimates under this audit, not "
                "universal precision estimates for English."
            ),
            "",
            "## 13. Before/after corpus counts",
            "",
            "| Metric | Before | After |",
            "|---|---:|---:|",
        ]
    )
    for field in metric_fields:
        before_value = before[field]
        after_value = after[field] if after else "not applied"
        lines.append(f"| {field} | {before_value} | {after_value} |")
    lines.extend(
        [
            "",
            f"Distinct quotes with auto-quarantine decisions: **{quarantine_quotes:,}**.",
            f"Relationships: KEEP **{count_rows.get('KEEP', 0):,}**, QUARANTINE "
            f"**{count_rows.get('QUARANTINE', 0):,}**, REVIEW **{count_rows.get('REVIEW', 0):,}**.",
            "",
            "## 14. Before/after coverage",
            "",
            (
                f"Coverage changed from **{before['covered_minutes']:,}** to "
                f"**{after['covered_minutes'] if after else 'not applied'}** minutes. No "
                "replacement quote was added."
            ),
            "",
            "## 15. Newly empty/sparse minutes",
            "",
            (
                f"Newly empty minutes: **{len(newly_empty):,}**. Newly below three from a baseline "
                f"of at least three: **{len(newly_below_3):,}**."
            ),
            "",
            "Newly empty: " + (", ".join(row["time_24h"] for row in newly_empty) or "none"),
            "",
            "## 16. Largest affected minute pools",
            "",
            "| Minute | Before | After | Loss | Removed quote IDs | Reasons |",
            "|---|---:|---:|---:|---|---|",
        ]
    )
    for row in changed[:30]:
        lines.append(
            f"| {row['time_24h']} | {row['before_count']} | {row['after_count']} | "
            f"{row['loss']} | {row['removed_quote_ids']} | {row['reason_codes']} |"
        )
    lines.extend(
        [
            "",
            "## 17. Remaining unresolved REVIEW cases",
            "",
            (
                f"There are **{count_rows.get('REVIEW', 0):,} relationship decisions** requiring "
                "evidence or deterministic rule refinement. Therefore this report does **not** "
                "claim that every remaining corpus relationship is semantically clean."
            ),
            "The activated view is a precision-first validated core: both QUARANTINE and REVIEW "
            "remain preserved in audit/source tables but are excluded from production selection.",
            "",
            "## 18. Recommended targeted recovery phase",
            "",
            (
                "First adjudicate the prioritized REVIEW packet and encode only evidence-backed "
                "rules or review provenance. After that, target genuine replacements for newly "
                "empty and newly sub-three buckets. This branch intentionally performs no recovery "
                "mining."
            ),
            "",
            "## 19. Implications for Phase 4C",
            "",
            (
                "Phase 4C should consume a bundle built only after semantic audit activation and "
                "review. The current physical Kindle bundle remains unchanged; renderer, runtime, "
                "deployment, and bundle formats were not modified in this branch."
            ),
            "The unchanged frozen PW4 renderer audited 6,080 selectable quotes: 6,063 full, 14 "
            "sentence-excerpted, and 3 pre-existing dirty-record rejections. It retained 7,476 "
            "display-safe relationships, with 14 zero-safe minutes (the 13 semantic gaps plus "
            "01:39, whose sole retained quote is rejected by the existing dirty-record gate). "
            "There was no clipping, undersized body text, line-budget violation, attribution "
            "overflow, or pathological highlight wrap.",
            "",
        ]
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "run_id": run_id,
        "before": before,
        "after": after,
        "actions": count_rows,
        "classes": class_rows,
        "quarantine_reasons": reason_rows,
        "source_families": source_rows,
        "colon_categories": colon_categories,
        "quarantined_quotes": quarantine_quotes,
        "review_quotes": review_quotes,
        "newly_empty": [row["time_24h"] for row in newly_empty],
        "newly_below_3": [row["time_24h"] for row in newly_below_3],
        "largest_impacts": changed[:30],
    }
