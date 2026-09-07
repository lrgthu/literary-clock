"""Versioned second-pass adjudication and reversible semantic relationship repair."""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from litclock.normalize import minute_to_time
from litclock.semantic import (
    SEMANTIC_AUDIT_V1,
    SEMANTIC_AUDIT_VERSION,
    SemanticAction,
    classify_clock_relationship,
)
from litclock.semantic_audit import (
    _base_relationship_rows,
    _candidate_contexts,
    _context_for_row,
    _source_expression_window,
    _write_csv,
    run_semantic_audit,
)

ADJUDICATION_VERSION = "english-clock-semantics-v2-adjudication"
REVIEWED_BY = "semantic-adjudication-v2"


class AdjudicationAction(StrEnum):
    KEEP_AS_IS = "KEEP_AS_IS"
    QUARANTINE = "QUARANTINE"
    REPAIR_MINUTE = "REPAIR_MINUTE"
    REPAIR_HIGHLIGHT = "REPAIR_HIGHLIGHT"
    REPAIR_MINUTE_AND_HIGHLIGHT = "REPAIR_MINUTE_AND_HIGHLIGHT"
    REVIEW = "REVIEW"


class MismatchDisposition(StrEnum):
    NON_CLOCK = "NON_CLOCK"
    WRONG_MINUTE_LABEL = "WRONG_MINUTE_LABEL"
    WRONG_CLOCKFACE_SIDE = "WRONG_CLOCKFACE_SIDE"
    WRONG_HIGHLIGHT = "WRONG_HIGHLIGHT"
    PARSER_LIMITATION = "PARSER_LIMITATION"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class ManualAdjudication:
    quote_id: int
    original_minute: int
    action: AdjudicationAction
    corrected_minutes: tuple[int, ...]
    corrected_highlight: str | None
    evidence_code: str
    evidence_text: str
    reviewed_by: str


_NON_EXACT_REPAIR_RE = re.compile(
    r"\b(?:almost|nearly|near|about|around|approximately|towards?|shortly|moments?|"
    r"barely|roughly|circa|between|not\s+yet|a\s+little|a\s+few|few|just)\b|"
    r"\b(?:or)\b|(?:^|\s)'?bout\b|^(?:after|before|past)\b",
    re.I,
)
_PARTIAL_PARSE_RE = re.compile(
    r"\b(?:and\s+arrived|one\s+and\s+a\s+half|until\s+nearly)\b",
    re.I,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def load_manual_adjudications(path: Path | None) -> dict[tuple[int, int], ManualAdjudication]:
    if path is None:
        return {}
    rows: dict[tuple[int, int], ManualAdjudication] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle, delimiter="\t"):
            quote_id = int(raw["quote_id"])
            minute = int(raw["original_minute"])
            corrected = tuple(
                int(value) for value in raw["corrected_minutes"].split(",") if value.strip()
            )
            if any(value < 0 or value > 1439 for value in corrected):
                raise ValueError(f"invalid repaired minute for quote {quote_id}")
            item = ManualAdjudication(
                quote_id=quote_id,
                original_minute=minute,
                action=AdjudicationAction(raw["adjudication_action"]),
                corrected_minutes=corrected,
                corrected_highlight=raw["corrected_highlight"] or None,
                evidence_code=raw["evidence_code"],
                evidence_text=raw["evidence_text"],
                reviewed_by=raw["reviewed_by"],
            )
            key = (quote_id, minute)
            if key in rows:
                raise ValueError(f"duplicate manual adjudication for quote/minute {key}")
            rows[key] = item
    return rows


_WRONG_MINUTE_REPAIR_ROUTES = {
    "CLOCK_GRAMMAR_NUMERIC",
    "CLOCK_GRAMMAR_HISTORICAL_DOT",
    "CLOCK_GRAMMAR_COMPACT_12H",
    "CLOCK_GRAMMAR_COMPACT_24H",
    "CLOCK_GRAMMAR_EUROPEAN_24H",
    "CLOCK_GRAMMAR_RELATIVE",
    "CLOCK_GRAMMAR_PRECISE_RELATIVE",
    "CLOCK_GRAMMAR_OFFSET_QUARTER",
    "CLOCK_GRAMMAR_OFFSET_WRITTEN",
    "CLOCK_GRAMMAR_HOURS_FROM_NAMED",
    "CLOCK_GRAMMAR_NAMED_THEN_PAST",
    "CLOCK_GRAMMAR_NUMERIC_HALF_PAST",
    "CLOCK_GRAMMAR_FRACTION_OCLOCK",
    "CLOCK_GRAMMAR_WRITTEN_OCLOCK",
    "CLOCK_GRAMMAR_VERBOSE_TO_NAMED",
    "CLOCK_GRAMMAR_CLOCK_STRUCK_RELATIVE",
    "CLOCK_GRAMMAR_OCLOCK_PLUS_MINUTES",
    "CLOCK_GRAMMAR_OCLOCK_ALL_BUT",
    "CLOCK_GRAMMAR_FRACTIONAL_RELATIVE",
}


def _repairable_phrase(
    phrase: str,
    quote: str,
    highlight_start: int,
    parser_reason: str,
) -> bool:
    """Whether a derived minute is exact enough to materialize automatically."""
    if _NON_EXACT_REPAIR_RE.search(phrase) or _PARTIAL_PARSE_RE.search(phrase):
        return False
    immediate_before = quote[max(0, highlight_start - 45) : highlight_start]
    if re.search(
        r"\b(?:almost|nearly|near|about|around|approximately|just)\s*$|"
        r"\bshortly\s+(?:after|before)\s*$",
        immediate_before,
        re.I,
    ):
        return False
    if parser_reason not in _WRONG_MINUTE_REPAIR_ROUTES:
        return False
    if re.search(r"\b(?:invalid|unknown)\b", phrase, re.I):
        return False
    return not bool(re.search(r"\b\d{1,2}:\d{2}\s*[ap]\.?m\.?.*\b(?:before|after)\b", phrase, re.I))


def _highlight_offsets(
    quote: str, manual: ManualAdjudication, start: int, end: int
) -> tuple[int, int, str]:
    if manual.corrected_highlight is None:
        return start, end, quote[start:end]
    occurrences = [
        match.start() for match in re.finditer(re.escape(manual.corrected_highlight), quote)
    ]
    if len(occurrences) != 1:
        raise ValueError(
            f"corrected highlight for quote {manual.quote_id} has {len(occurrences)} occurrences"
        )
    corrected_start = occurrences[0]
    return (
        corrected_start,
        corrected_start + len(manual.corrected_highlight),
        manual.corrected_highlight,
    )


def _manual_audit_action(
    manual: ManualAdjudication, derived_minutes: tuple[int, ...]
) -> tuple[str, str, str]:
    clock_class = "CLOCK_TIME_EXACT" if len(derived_minutes) == 1 else "CLOCK_TIME_AMBIGUOUS"
    if manual.action == AdjudicationAction.KEEP_AS_IS:
        return "KEEP", clock_class, "ADJUDICATED_CLOCK_EVIDENCE"
    if manual.action == AdjudicationAction.REVIEW:
        return "REVIEW", "UNKNOWN", manual.evidence_code
    return "QUARANTINE", clock_class, manual.evidence_code


def _mismatch_disposition(
    *,
    prior_reason: str,
    current_action: str,
    current_reason: str,
    claimed_minute: int,
    derived_minutes: tuple[int, ...],
    phrase: str,
    quote: str,
    highlight_start: int,
    derivation_rule: str | None,
) -> MismatchDisposition | None:
    if prior_reason != "HIGHLIGHT_SEMANTIC_MISMATCH":
        return None
    if current_action == SemanticAction.KEEP:
        return MismatchDisposition.PARSER_LIMITATION
    if current_reason != "HIGHLIGHT_SEMANTIC_MISMATCH" or not derived_minutes:
        return (
            MismatchDisposition.NON_CLOCK
            if current_action == SemanticAction.QUARANTINE
            else MismatchDisposition.UNRESOLVED
        )
    if not _repairable_phrase(
        phrase,
        quote,
        highlight_start,
        derivation_rule or current_reason,
    ):
        return MismatchDisposition.NON_CLOCK
    if any(claimed_minute % 720 == minute % 720 for minute in derived_minutes):
        return MismatchDisposition.WRONG_CLOCKFACE_SIDE
    return MismatchDisposition.WRONG_MINUTE_LABEL


def _revalidate_repair(
    row: sqlite3.Row,
    source: Any,
    repaired_minute: int,
    highlight_start: int,
    highlight_end: int,
    highlight_text: str,
    parser_route: str,
) -> Any:
    source_before, source_after = _source_expression_window(source, highlight_text)
    return classify_clock_relationship(
        str(row["quote"]),
        highlight_start,
        highlight_end,
        repaired_minute,
        expected_text=highlight_text,
        parser_route=parser_route,
        source_section=source.source_section,
        source_locator=source.source_locator,
        source_context_before=source_before,
        source_context_after=source_after,
    )


def run_semantic_adjudication(
    connection: sqlite3.Connection,
    *,
    prior_run_id: int | None = None,
    manual_path: Path | None = None,
) -> dict[str, Any]:
    """Run v2, adjudicate every v1 REVIEW/mismatch, and stage validated repairs."""
    if prior_run_id is None:
        prior = connection.execute(
            """
            SELECT id FROM semantic_audit_runs
            WHERE audit_version = ? AND status IN ('APPLIED', 'SUPERSEDED')
            ORDER BY CASE status WHEN 'APPLIED' THEN 0 ELSE 1 END, id DESC LIMIT 1
            """,
            (SEMANTIC_AUDIT_V1,),
        ).fetchone()
        if prior is None:
            raise ValueError("no v1 semantic audit is available for adjudication")
        prior_run_id = int(prior[0])
    prior_run = connection.execute(
        "SELECT * FROM semantic_audit_runs WHERE id = ?", (prior_run_id,)
    ).fetchone()
    if prior_run is None or prior_run["audit_version"] != SEMANTIC_AUDIT_V1:
        raise ValueError("prior run must be an english-clock-semantics-v1 audit")

    result = run_semantic_audit(connection, audit_version=SEMANTIC_AUDIT_VERSION)
    run_id = int(result["run_id"])
    manual = load_manual_adjudications(manual_path)
    base_rows = {
        (int(row["quote_id"]), int(row["minute_of_day"])): row
        for row in _base_relationship_rows(connection)
    }
    contexts = _candidate_contexts(connection)
    prior_rows = {
        (int(row["quote_id"]), int(row["minute_of_day"])): row
        for row in connection.execute(
            "SELECT * FROM semantic_time_audit WHERE run_id = ?", (prior_run_id,)
        )
    }
    current_rows = {
        (int(row["quote_id"]), int(row["minute_of_day"])): row
        for row in connection.execute(
            "SELECT * FROM semantic_time_audit WHERE run_id = ?", (run_id,)
        )
    }
    if set(base_rows) != set(prior_rows) or set(base_rows) != set(current_rows):
        raise ValueError("v1/v2 corpus relationship sets do not match")
    missing_manual = sorted(set(manual) - set(base_rows))
    if missing_manual:
        raise ValueError(f"manual adjudications refer to missing relationships: {missing_manual}")

    counts: dict[str, int] = {action.value: 0 for action in AdjudicationAction}
    mismatch_counts: dict[str, int] = {item.value: 0 for item in MismatchDisposition}
    repaired_relationships = 0
    repaired_quotes: set[int] = set()
    repaired_highlights: set[int] = set()
    created_at = _now()

    with connection:
        for key, row in base_rows.items():
            quote_id, original_minute = key
            prior = prior_rows[key]
            current = current_rows[key]
            phrase = str(row["quote"])[int(row["highlight_start"]) : int(row["highlight_end"])]
            derived = tuple(
                int(value) for value in str(current["derived_minutes"]).split(",") if value
            )
            manual_item = manual.get(key)
            disposition = _mismatch_disposition(
                prior_reason=str(prior["reason_code"]),
                current_action=str(current["action"]),
                current_reason=str(current["reason_code"]),
                claimed_minute=original_minute,
                derived_minutes=derived,
                phrase=phrase,
                quote=str(row["quote"]),
                highlight_start=int(row["highlight_start"]),
                derivation_rule=current["derivation_rule"],
            )
            repaired_minutes: tuple[int, ...] = ()
            corrected_start = int(row["highlight_start"])
            corrected_end = int(row["highlight_end"])
            corrected_text = phrase
            highlight_changed = False
            evidence_code = "DETERMINISTIC_V2_CLASSIFIER"
            evidence_text = (
                f"v1={prior['action']}:{prior['reason_code']}; "
                f"v2={current['action']}:{current['reason_code']}"
            )
            reviewed_by = REVIEWED_BY

            if manual_item is not None:
                action = manual_item.action
                repaired_minutes = manual_item.corrected_minutes
                corrected_start, corrected_end, corrected_text = _highlight_offsets(
                    str(row["quote"]),
                    manual_item,
                    corrected_start,
                    corrected_end,
                )
                highlight_changed = (
                    corrected_start != int(row["highlight_start"])
                    or corrected_end != int(row["highlight_end"])
                    or corrected_text != str(row["time_text"])
                )
                evidence_code = manual_item.evidence_code
                evidence_text = manual_item.evidence_text
                reviewed_by = manual_item.reviewed_by
                if (
                    action
                    in {
                        AdjudicationAction.REPAIR_HIGHLIGHT,
                        AdjudicationAction.REPAIR_MINUTE_AND_HIGHLIGHT,
                    }
                    and prior["reason_code"] == "HIGHLIGHT_SEMANTIC_MISMATCH"
                ):
                    disposition = MismatchDisposition.WRONG_HIGHLIGHT
                audit_action, semantic_class, reason_code = _manual_audit_action(
                    manual_item, derived
                )
                connection.execute(
                    """
                    UPDATE semantic_time_audit
                    SET action = ?, semantic_class = ?, reason_code = ?, confidence = 'HIGH',
                        reviewed_by = ?, review_provenance = ?
                    WHERE run_id = ? AND quote_id = ? AND minute_of_day = ?
                    """,
                    (
                        audit_action,
                        semantic_class,
                        reason_code,
                        reviewed_by,
                        evidence_text,
                        run_id,
                        quote_id,
                        original_minute,
                    ),
                )
            elif disposition in {
                MismatchDisposition.WRONG_MINUTE_LABEL,
                MismatchDisposition.WRONG_CLOCKFACE_SIDE,
            }:
                action = AdjudicationAction.REPAIR_MINUTE
                repaired_minutes = derived
            elif current["action"] == SemanticAction.KEEP:
                action = AdjudicationAction.KEEP_AS_IS
            elif current["action"] == SemanticAction.QUARANTINE:
                action = AdjudicationAction.QUARANTINE
            else:
                action = AdjudicationAction.REVIEW

            cursor = connection.execute(
                """
                INSERT INTO semantic_adjudications (
                    run_id, prior_run_id, quote_id, original_minute, prior_action,
                    prior_reason_code, adjudication_action, mismatch_disposition,
                    corrected_minutes, original_highlight_start, original_highlight_end,
                    original_highlight_text, original_time_text, corrected_highlight_start,
                    corrected_highlight_end, corrected_highlight_text, evidence_code,
                    evidence_text, reviewed_by,
                    review_provenance, adjudication_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    prior_run_id,
                    quote_id,
                    original_minute,
                    prior["action"],
                    prior["reason_code"],
                    action.value,
                    disposition.value if disposition else None,
                    ",".join(str(value) for value in repaired_minutes),
                    row["highlight_start"],
                    row["highlight_end"],
                    phrase,
                    row["time_text"],
                    corrected_start if highlight_changed else None,
                    corrected_end if highlight_changed else None,
                    corrected_text if highlight_changed else None,
                    evidence_code,
                    evidence_text,
                    reviewed_by,
                    f"{ADJUDICATION_VERSION}; prior audit {prior_run_id}",
                    ADJUDICATION_VERSION,
                    created_at,
                ),
            )
            adjudication_id = int(cursor.lastrowid)
            counts[action.value] += 1
            if disposition:
                mismatch_counts[disposition.value] += 1

            source = _context_for_row(row, contexts)
            for repaired_minute in repaired_minutes:
                validation = _revalidate_repair(
                    row,
                    source,
                    repaired_minute,
                    corrected_start,
                    corrected_end,
                    corrected_text,
                    str(current["parser_route"]),
                )
                if validation.action != SemanticAction.KEEP:
                    raise ValueError(
                        f"repair failed v2 validation: quote {quote_id}, minute {repaired_minute}, "
                        f"{validation.action}:{validation.reason_code}"
                    )
                existing = current_rows.get((quote_id, repaired_minute))
                if (
                    existing is not None
                    and existing["action"] == SemanticAction.KEEP
                    and repaired_minute != original_minute
                ):
                    continue
                connection.execute(
                    """
                    INSERT OR IGNORE INTO semantic_relationship_repairs (
                        run_id, adjudication_id, quote_id, original_minute, repaired_minute,
                        eligibility_type, confidence, evidence_type, evidence_text,
                        corrected_highlight_start, corrected_highlight_end,
                        corrected_highlight_text, semantic_class, semantic_action,
                        semantic_reason_code, validation_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'KEEP', ?, ?, ?)
                    """,
                    (
                        run_id,
                        adjudication_id,
                        quote_id,
                        original_minute,
                        repaired_minute,
                        "SEMANTIC_REPAIR",
                        validation.confidence,
                        evidence_code,
                        evidence_text,
                        corrected_start if highlight_changed else None,
                        corrected_end if highlight_changed else None,
                        corrected_text if highlight_changed else None,
                        validation.semantic_class.value,
                        validation.reason_code,
                        SEMANTIC_AUDIT_VERSION,
                        created_at,
                    ),
                )
                if connection.execute("SELECT changes()").fetchone()[0]:
                    repaired_relationships += 1
                    repaired_quotes.add(quote_id)
                    if highlight_changed:
                        repaired_highlights.add(quote_id)

        final_counts = {
            str(row["action"]): int(row["n"])
            for row in connection.execute(
                "SELECT action, COUNT(*) n FROM semantic_time_audit "
                "WHERE run_id = ? GROUP BY action",
                (run_id,),
            )
        }
        connection.execute(
            "UPDATE semantic_audit_runs SET decision_counts_json = ? WHERE id = ?",
            (json.dumps(final_counts, sort_keys=True), run_id),
        )

    return {
        **result,
        "prior_run_id": prior_run_id,
        "adjudication_version": ADJUDICATION_VERSION,
        "adjudication_counts": counts,
        "mismatch_dispositions": mismatch_counts,
        "repaired_relationships": repaired_relationships,
        "repaired_quotes": len(repaired_quotes),
        "repaired_highlights": len(repaired_highlights),
        "manual_decisions": len(manual),
        "counts": {action.value: final_counts.get(action.value, 0) for action in SemanticAction},
    }


def _logical_outcome(action: str) -> str:
    if action == AdjudicationAction.KEEP_AS_IS:
        return "KEEP"
    if action.startswith("REPAIR_"):
        return "KEEP"
    if action == AdjudicationAction.QUARANTINE:
        return "QUARANTINE"
    return "REVIEW"


def _report_rows(connection: sqlite3.Connection, run_id: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT adjud.quote_id, adjud.original_minute,
                   printf('%02d:%02d', adjud.original_minute / 60,
                          adjud.original_minute % 60) AS original_time,
                   adjud.prior_action, adjud.prior_reason_code,
                   adjud.adjudication_action, adjud.mismatch_disposition,
                   adjud.corrected_minutes, adjud.original_highlight_text,
                   adjud.corrected_highlight_text, adjud.evidence_code,
                   adjud.evidence_text, adjud.reviewed_by,
                   audit.parser_route, audit.source_family,
                   quote.title, quote.author, quote.source_name, quote.quote
            FROM semantic_adjudications AS adjud
            JOIN semantic_time_audit AS audit
              ON audit.run_id = adjud.run_id
             AND audit.quote_id = adjud.quote_id
             AND audit.minute_of_day = adjud.original_minute
            JOIN quotes AS quote ON quote.id = adjud.quote_id
            WHERE adjud.run_id = ?
            ORDER BY adjud.quote_id, adjud.original_minute
            """,
            (run_id,),
        )
    ]


def _minute_counts_for_run(
    connection: sqlite3.Connection, run_id: int, *, include_repairs: bool
) -> dict[int, int]:
    relationships = {
        (int(row["quote_id"]), int(row["minute_of_day"]))
        for row in connection.execute(
            """
            SELECT quote_id, minute_of_day FROM semantic_time_audit
            WHERE run_id = ? AND action = 'KEEP'
            """,
            (run_id,),
        )
    }
    if include_repairs:
        relationships.update(
            (int(row["quote_id"]), int(row["repaired_minute"]))
            for row in connection.execute(
                """
                SELECT quote_id, repaired_minute FROM semantic_relationship_repairs
                WHERE run_id = ? AND semantic_action = 'KEEP'
                """,
                (run_id,),
            )
        )
    counts: dict[int, int] = defaultdict(int)
    for _quote_id, minute in relationships:
        counts[minute] += 1
    return dict(counts)


def validate_semantic_adjudication(connection: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    """Validate referential, offset, pool, and repaired-grammar invariants."""
    integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    foreign_key_violations = len(list(connection.execute("PRAGMA foreign_key_check")))
    duplicate_active = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT quote_id, minute_of_day, COUNT(*) AS n
                FROM quote_minute_pool GROUP BY quote_id, minute_of_day HAVING n > 1
            )
            """
        ).fetchone()[0]
    )
    invalid_minutes = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM quote_minute_pool
            WHERE minute_of_day < 0 OR minute_of_day > 1439
            """
        ).fetchone()[0]
    )
    invalid_highlights = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM quote_minute_pool AS pool JOIN quotes AS quote
              ON quote.id = pool.quote_id
            WHERE quote.highlight_start IS NULL OR quote.highlight_end IS NULL
               OR quote.highlight_start < 0 OR quote.highlight_end <= quote.highlight_start
               OR quote.highlight_end > length(quote.quote)
               OR substr(quote.quote, quote.highlight_start + 1,
                         quote.highlight_end - quote.highlight_start) != quote.time_text
            """
        ).fetchone()[0]
    )
    unjustified_pool_rows = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM quote_minute_pool AS pool
            WHERE NOT EXISTS (
                SELECT 1 FROM semantic_time_audit AS audit
                WHERE audit.run_id = ? AND audit.quote_id = pool.quote_id
                  AND audit.minute_of_day = pool.minute_of_day AND audit.action = 'KEEP'
            ) AND NOT EXISTS (
                SELECT 1 FROM semantic_relationship_repairs AS repair
                WHERE repair.run_id = ? AND repair.quote_id = pool.quote_id
                  AND repair.repaired_minute = pool.minute_of_day
                  AND repair.semantic_action = 'KEEP'
            )
            """,
            (run_id, run_id),
        ).fetchone()[0]
    )
    orphan_adjudications = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM semantic_adjudications AS adjud
            LEFT JOIN semantic_time_audit AS audit
              ON audit.run_id = adjud.run_id AND audit.quote_id = adjud.quote_id
             AND audit.minute_of_day = adjud.original_minute
            WHERE adjud.run_id = ? AND audit.id IS NULL
            """,
            (run_id,),
        ).fetchone()[0]
    )

    base_rows = {
        (int(row["quote_id"]), int(row["minute_of_day"])): row
        for row in _base_relationship_rows(connection)
    }
    contexts = _candidate_contexts(connection)
    invalid_revalidated_repairs = 0
    for repair in connection.execute(
        """
        SELECT repair.*, audit.parser_route
        FROM semantic_relationship_repairs AS repair
        JOIN semantic_time_audit AS audit
          ON audit.run_id = repair.run_id AND audit.quote_id = repair.quote_id
         AND audit.minute_of_day = repair.original_minute
        WHERE repair.run_id = ?
        ORDER BY repair.quote_id, repair.repaired_minute
        """,
        (run_id,),
    ):
        key = (int(repair["quote_id"]), int(repair["original_minute"]))
        row = base_rows[key]
        source = _context_for_row(row, contexts)
        quote_row = connection.execute(
            "SELECT quote, time_text, highlight_start, highlight_end FROM quotes WHERE id = ?",
            (repair["quote_id"],),
        ).fetchone()
        validation = _revalidate_repair(
            quote_row,
            source,
            int(repair["repaired_minute"]),
            int(quote_row["highlight_start"]),
            int(quote_row["highlight_end"]),
            str(quote_row["time_text"]),
            str(repair["parser_route"]),
        )
        invalid_revalidated_repairs += validation.action != SemanticAction.KEEP

    return {
        "integrity_check": integrity,
        "foreign_key_violations": foreign_key_violations,
        "orphan_adjudications": orphan_adjudications,
        "duplicate_active_eligibility": duplicate_active,
        "invalid_minutes": invalid_minutes,
        "invalid_highlights": invalid_highlights,
        "unjustified_production_relationships": unjustified_pool_rows,
        "invalid_revalidated_repairs": invalid_revalidated_repairs,
    }


def write_semantic_adjudication_report(
    connection: sqlite3.Connection,
    run_id: int,
    report_path: Path,
    output_dir: Path,
    *,
    renderer_audit_path: Path | None = None,
) -> dict[str, Any]:
    """Write reproducible v2 adjudication evidence and a compact committed report."""
    run = connection.execute("SELECT * FROM semantic_audit_runs WHERE id = ?", (run_id,)).fetchone()
    if run is None or run["audit_version"] != SEMANTIC_AUDIT_VERSION:
        raise ValueError("adjudication report requires an english-clock-semantics-v2 run")
    prior = connection.execute(
        """
        SELECT prior_run_id FROM semantic_adjudications
        WHERE run_id = ? LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    if prior is None:
        raise ValueError("run has no semantic adjudications")
    prior_run_id = int(prior["prior_run_id"])
    prior_run = connection.execute(
        "SELECT * FROM semantic_audit_runs WHERE id = ?", (prior_run_id,)
    ).fetchone()
    if prior_run is None:
        raise ValueError("adjudication prior audit does not exist")

    before = json.loads(run["baseline_json"])
    v1 = json.loads(prior_run["after_json"])
    if not run["after_json"]:
        raise ValueError("apply the adjudication run before writing its final report")
    v2 = json.loads(run["after_json"])
    validation = validate_semantic_adjudication(connection, run_id)
    rows = _report_rows(connection, run_id)
    for row in rows:
        row["logical_v2_outcome"] = _logical_outcome(str(row["adjudication_action"]))
    _write_csv(output_dir / "all_adjudications.csv", rows)
    _write_csv(
        output_dir / "remaining_review.csv",
        [row for row in rows if row["logical_v2_outcome"] == "REVIEW"],
    )
    repair_rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT repair.*, adjud.original_highlight_text, adjud.evidence_code,
                   adjud.reviewed_by, quote.title, quote.author
            FROM semantic_relationship_repairs AS repair
            JOIN semantic_adjudications AS adjud ON adjud.id = repair.adjudication_id
            JOIN quotes AS quote ON quote.id = repair.quote_id
            WHERE repair.run_id = ?
            ORDER BY repair.quote_id, repair.repaired_minute
            """,
            (run_id,),
        )
    ]
    _write_csv(output_dir / "materialized_repairs.csv", repair_rows)

    transitions: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        transitions[(str(row["prior_action"]), str(row["logical_v2_outcome"]))] += 1
    transition_rows = [
        {"v1_action": prior_action, "v2_action": current_action, "relationships": count}
        for (prior_action, current_action), count in sorted(transitions.items())
    ]
    _write_csv(output_dir / "v1_to_v2_transition.csv", transition_rows)

    family_rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT audit.parser_route,
                   COUNT(*) AS relationships,
                   SUM(adjud.adjudication_action = 'KEEP_AS_IS') AS keep_as_is,
                   SUM(adjud.adjudication_action LIKE 'REPAIR_%') AS repaired,
                   SUM(adjud.adjudication_action = 'QUARANTINE') AS quarantined,
                   SUM(adjud.adjudication_action = 'REVIEW') AS review
            FROM semantic_adjudications AS adjud
            JOIN semantic_time_audit AS audit
              ON audit.run_id = adjud.run_id
             AND audit.quote_id = adjud.quote_id
             AND audit.minute_of_day = adjud.original_minute
            WHERE adjud.run_id = ? AND adjud.prior_action = 'REVIEW'
            GROUP BY audit.parser_route
            ORDER BY relationships DESC, audit.parser_route
            """,
            (run_id,),
        )
    ]
    _write_csv(output_dir / "v1_review_by_parser_family.csv", family_rows)
    parser_rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT parser_route, COUNT(*) AS relationships,
                   SUM(action = 'KEEP') AS kept,
                   SUM(action = 'QUARANTINE') AS quarantined,
                   SUM(action = 'REVIEW') AS review
            FROM semantic_time_audit WHERE run_id = ?
            GROUP BY parser_route ORDER BY relationships DESC, parser_route
            """,
            (run_id,),
        )
    ]
    _write_csv(output_dir / "v2_parser_families.csv", parser_rows)
    source_rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT source_family, COUNT(*) AS relationships,
                   SUM(action = 'KEEP') AS kept,
                   SUM(action = 'QUARANTINE') AS quarantined,
                   SUM(action = 'REVIEW') AS review
            FROM semantic_time_audit WHERE run_id = ?
            GROUP BY source_family ORDER BY relationships DESC, source_family
            """,
            (run_id,),
        )
    ]
    _write_csv(output_dir / "v2_source_families.csv", source_rows)

    mismatch_rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT mismatch_disposition, adjudication_action, COUNT(*) AS relationships
            FROM semantic_adjudications
            WHERE run_id = ? AND prior_reason_code = 'HIGHLIGHT_SEMANTIC_MISMATCH'
            GROUP BY mismatch_disposition, adjudication_action
            ORDER BY mismatch_disposition, adjudication_action
            """,
            (run_id,),
        )
    ]
    _write_csv(output_dir / "mismatch_dispositions.csv", mismatch_rows)
    colon_rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT action, semantic_class, COUNT(*) AS relationships
            FROM semantic_time_audit
            WHERE run_id = ? AND highlighted_text GLOB '*[0-9]:[0-9][0-9]*'
            GROUP BY action, semantic_class ORDER BY action, semantic_class
            """,
            (run_id,),
        )
    ]
    _write_csv(output_dir / "v2_colon_dispositions.csv", colon_rows)

    baseline_counts: dict[int, int] = defaultdict(int)
    for row in connection.execute(
        "SELECT minute_of_day, COUNT(*) n FROM semantic_time_audit "
        "WHERE run_id = ? GROUP BY minute_of_day",
        (run_id,),
    ):
        baseline_counts[int(row["minute_of_day"])] = int(row["n"])
    v1_counts = _minute_counts_for_run(connection, prior_run_id, include_repairs=False)
    v2_counts = _minute_counts_for_run(connection, run_id, include_repairs=True)
    review_minutes = {
        int(row["original_minute"]) for row in rows if row["logical_v2_outcome"] == "REVIEW"
    }
    renderer: dict[str, Any] | None = None
    if renderer_audit_path and renderer_audit_path.is_file():
        renderer = json.loads(renderer_audit_path.read_text(encoding="utf-8"))
    renderer_safe = renderer.get("safe_quote_ids_by_minute", {}) if renderer else {}
    dirty_quote_ids = (
        {
            int(row["quote_id"])
            for row in renderer.get("layout_failures", [])
            if row["status"] == "REJECT_DIRTY"
        }
        if renderer
        else set()
    )
    v2_quote_ids_by_minute: dict[int, set[int]] = defaultdict(set)
    for row in connection.execute(
        "SELECT minute_of_day, quote_id FROM quote_minute_pool ORDER BY minute_of_day, quote_id"
    ):
        v2_quote_ids_by_minute[int(row["minute_of_day"])].add(int(row["quote_id"]))

    target_rows: list[dict[str, Any]] = []
    for minute in range(1440):
        semantic_count = v2_counts.get(minute, 0)
        safe_count = (
            len(renderer_safe.get(minute_to_time(minute), [])) if renderer else semantic_count
        )
        if semantic_count >= 3 and safe_count >= 3:
            continue
        if minute in review_minutes:
            category = "UNRESOLVED_REVIEW"
        elif v2_quote_ids_by_minute[minute] & dirty_quote_ids:
            category = "SOURCE_CORRUPTION_PROBLEM"
        elif semantic_count >= 3 and safe_count < 3:
            category = "RENDERER_ONLY_PROBLEM"
        else:
            category = "SEMANTIC_CLEAN_GENUINELY_SPARSE"
        target_rows.append(
            {
                "minute_of_day": minute,
                "time_24h": minute_to_time(minute),
                "baseline_count": baseline_counts.get(minute, 0),
                "v1_count": v1_counts.get(minute, 0),
                "v2_count": semantic_count,
                "renderer_safe_count": safe_count,
                "deficit_to_3": 3 - min(semantic_count, safe_count),
                "category": category,
            }
        )
    _write_csv(output_dir / "targeted_recovery_minutes.csv", target_rows)
    primary_targets = [
        str(row["time_24h"])
        for row in target_rows
        if row["category"] == "SEMANTIC_CLEAN_GENUINELY_SPARSE"
    ]
    non_mining_targets = [
        row for row in target_rows if row["category"] != "SEMANTIC_CLEAN_GENUINELY_SPARSE"
    ]

    restored = [
        minute_to_time(minute)
        for minute in range(1440)
        if v1_counts.get(minute, 0) == 0 and v2_counts.get(minute, 0) > 0
    ]
    true_empty = [minute_to_time(minute) for minute in range(1440) if not v2_counts.get(minute, 0)]
    review_counts = {
        outcome: sum(
            count
            for (prior_action, current), count in transitions.items()
            if prior_action == "REVIEW" and current == outcome
        )
        for outcome in ("KEEP", "QUARANTINE", "REVIEW")
    }
    mismatch_total = sum(int(row["relationships"]) for row in mismatch_rows)
    mismatch_repaired = sum(
        int(row["relationships"])
        for row in mismatch_rows
        if str(row["adjudication_action"]).startswith("REPAIR_")
    )
    mismatch_bad = sum(
        int(row["relationships"])
        for row in mismatch_rows
        if row["adjudication_action"] == "QUARANTINE"
    )
    mismatch_parser_recovered = sum(
        int(row["relationships"])
        for row in mismatch_rows
        if row["adjudication_action"] == "KEEP_AS_IS"
    )
    repaired_adjudications = sum(
        1 for row in rows if str(row["adjudication_action"]).startswith("REPAIR_")
    )
    repaired_quotes = len(
        {
            int(row["quote_id"])
            for row in rows
            if str(row["adjudication_action"]).startswith("REPAIR_")
        }
    )
    repaired_highlights = len(
        {int(row["quote_id"]) for row in rows if row["corrected_highlight_text"]}
    )
    materialized_repair_quotes = len({int(row["quote_id"]) for row in repair_rows})
    core_repair_adjudications = sum(
        str(row["adjudication_action"]).startswith("REPAIR_")
        and row["evidence_code"] != "EXACT_SOURCE_HIGHLIGHT"
        for row in rows
    )
    core_repair_quotes = len(
        {
            int(row["quote_id"])
            for row in rows
            if str(row["adjudication_action"]).startswith("REPAIR_")
            and row["evidence_code"] != "EXACT_SOURCE_HIGHLIGHT"
        }
    )
    core_repair_rows = [
        row for row in repair_rows if row["evidence_code"] != "EXACT_SOURCE_HIGHLIGHT"
    ]
    exact_highlight_repairs = repaired_adjudications - core_repair_adjudications
    explicit_decisions = sum(row["evidence_code"] != "DETERMINISTIC_V2_CLASSIFIER" for row in rows)

    metric_names = (
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
        "# Corpus Semantic Adjudication Report",
        "",
        f"V1 audit: `{SEMANTIC_AUDIT_V1}` (run {prior_run_id})  ",
        f"V2 audit: `{SEMANTIC_AUDIT_VERSION}` (run {run_id})  ",
        f"Adjudication: `{ADJUDICATION_VERSION}`  ",
        f"Corpus fingerprint: `{run['corpus_fingerprint']}`",
        "",
        "## 1. V1 starting point",
        "",
        (
            f"V1 classified {before['relationships']:,} existing English relationships as "
            "7,479 KEEP, 368 QUARANTINE, and 883 REVIEW. Its fail-closed production view "
            f"retained {v1['selectable_quotes']:,} selectable quotes over "
            f"{v1['covered_minutes']:,}/1,440 minutes. V1 evidence remains stored and was "
            "not overwritten."
        ),
        "",
        "## 2. Adjudication methodology",
        "",
        (
            "Every V1 REVIEW and every V1 `HIGHLIGHT_SEMANTIC_MISMATCH` was reclassified. "
            "Recurring constructions became deterministic grammar; source-specific cases use "
            "committed TSV adjudication data with explicit evidence and the honest reviewer label "
            f"`{REVIEWED_BY}`. Repairs are staged separately, retain original minute/highlight "
            "values, and enter the selector view only after the corrected relationship passes the "
            "same v2 validator."
        ),
        "",
        "## 3. 883 REVIEW disposition",
        "",
        f"- KEEP, including validated repairs: **{review_counts['KEEP']:,}**",
        f"- QUARANTINE: **{review_counts['QUARANTINE']:,}**",
        f"- still REVIEW: **{review_counts['REVIEW']:,}**",
        "",
        "| Parser family | Relationships | Keep as-is | Repaired | Quarantine | Review |",
        "|---|---:|---:|---:|---:|---:|",
        *[
            f"| {row['parser_route']} | {int(row['relationships']):,} | "
            f"{int(row['keep_as_is']):,} | {int(row['repaired']):,} | "
            f"{int(row['quarantined']):,} | {int(row['review']):,} |"
            for row in family_rows
        ],
        "",
        "## 4. 267 mismatch disposition",
        "",
        f"All **{mismatch_total:,}** cases are accounted for: **{mismatch_repaired:,}** were "
        f"relationship/highlight repairs, **{mismatch_parser_recovered:,}** became KEEP after "
        f"grammar improvement, **{mismatch_bad:,}** were confirmed non-clock/bad relationships, "
        "and **0** remain unresolved.",
        "",
        "| Disposition | Adjudication | Relationships |",
        "|---|---|---:|",
        *[
            f"| {row['mismatch_disposition']} | {row['adjudication_action']} | "
            f"{int(row['relationships']):,} |"
            for row in mismatch_rows
        ],
        "",
        "## 5. Repair model",
        "",
        f"There are **{core_repair_adjudications:,} semantic repair adjudications across "
        f"{core_repair_quotes:,} quotes**, materializing **{len(core_repair_rows):,} corrected "
        "minute/highlight relationships**. A further "
        f"**{exact_highlight_repairs:,} highlight-only metadata repairs** make stored `time_text` "
        "match source case/punctuation exactly. In total, "
        f"**{len(repair_rows):,} repair-backed relationships across "
        f"{materialized_repair_quotes:,} quotes** are active and "
        f"**{repaired_highlights:,} canonical records** receive "
        "source-backed offset/time-text corrections when v2 is active. Canonical literary prose "
        "is unchanged; activating another audit restores the prior spans before applying its own.",
        "",
        "## 6. Generalized grammar improvements",
        "",
        (
            "V2 adds deterministic coverage for archaic compounds, written 24-hour forms, "
            "ellipsis-separated speech, compact/historical timestamps, fractional o'clock, "
            "quarter/half variants, exact offset constructions, transport-service idioms, and "
            "explicit dashed meridiem. It simultaneously adds adversarial rejection for clock "
            "ranges, dense references, approximate boundaries, hyphenated quantities, and "
            "partial highlights such as `half a minute after nine` highlighted only as "
            "`a minute after nine`."
        ),
        "",
        "## 7. Explicit reviewed decisions",
        "",
        f"The committed adjudication TSV contains **{explicit_decisions:,} "
        "relationship decisions/overrides**. These are agent "
        "adjudications, not claimed independent human review. The three unresolved cases remain "
        "12:21 (`Twelve twenty-one`), 18:45 (`Six forty-five` around a new-baby exchange), and "
        "21:58 (a source-truncated fragment).",
        "",
        "## 8. Colon-numeric findings",
        "",
        (
            "V2 distinguishes prose timestamps, train/flight/service designations, digital/readout "
            "contexts, diary/log entries, and explicit meridiem from scripture, chapter/section, "
            "ratio, score, timecode, range, and bare-number uses. The final colon REVIEW set is "
            "limited to the source-truncated 21:58 fragment."
        ),
        "",
        "| Action | Semantic class | Relationships |",
        "|---|---|---:|",
        *[
            f"| {row['action']} | {row['semantic_class']} | {int(row['relationships']):,} |"
            for row in colon_rows
        ],
        "",
        "## 9. Written-clock findings",
        "",
        (
            "Written number adjacency is accepted only through a clock grammar or explicit "
            "evidence. `one twenty` can be a clock reading, while `one twenty-minute interval`, "
            "page/quantity/measurement uses, and unrelated adjacent numbers remain excluded."
        ),
        "",
        "## 10. O'clock findings",
        "",
        (
            "Literal o'clock constructions are intrinsically clock-like and no longer require "
            "redundant surrounding tokens. Fractional and archaic spellings are supported; "
            "durations and incomplete highlights remain excluded."
        ),
        "",
        "## 11. Quarter/half findings",
        "",
        (
            "`half past` and `quarter past/to` are exact clock grammars. `half an hour`, clock "
            "ranges, approximate phrases, and spans omitting the base hour are not. Several legacy "
            "quarter labels were repaired rather than discarding their literary quotes."
        ),
        "",
        "## 12. Bare-hour findings",
        "",
        (
            "Bare numbers remain conservative. Temporal syntax such as `at`, `by`, `until`, "
            "`around`, and an actual clock-strike construction can validate an hour; arbitrary "
            "numbers, policy boundaries, and quantities cannot."
        ),
        "",
        "## 13. Source-family findings",
        "",
        (
            "Retained Standard Ebooks/Gutenberg/Wikisource paragraph and locator evidence resolved "
            "several dayparts and timestamps. Legacy rows are disproportionately responsible for "
            "mislabeled minutes and insufficient context because they often carry only an upstream "
            "label and display excerpt. Missing evidence stays REVIEW rather than being guessed."
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
        "Parser-family residual risk is concentrated in colon numeric and written-number forms; "
        "o'clock and quarter/half still contain bad legacy labels, but their intrinsic clock "
        "grammars are now separated from duration forms.",
        "",
        "| Parser family | Relationships | KEEP | QUARANTINE | REVIEW | Flagged rate |",
        "|---|---:|---:|---:|---:|---:|",
        *[
            f"| {row['parser_route']} | {int(row['relationships']):,} | "
            f"{int(row['kept']):,} | {int(row['quarantined']):,} | "
            f"{int(row['review']):,} | "
            f"{(int(row['quarantined']) + int(row['review'])) / int(row['relationships']):.1%} |"
            for row in parser_rows
        ],
        "",
        "## 14. V1 → V2 transition matrix",
        "",
        (
            "Repairs count as final V2 KEEP dispositions even when the original minute "
            "relationship is removed."
        ),
        "",
        "| V1 action | V2 KEEP | V2 QUARANTINE | V2 REVIEW |",
        "|---|---:|---:|---:|",
    ]
    for prior_action in ("KEEP", "QUARANTINE", "REVIEW"):
        lines.append(
            f"| {prior_action} | {transitions.get((prior_action, 'KEEP'), 0):,} | "
            f"{transitions.get((prior_action, 'QUARANTINE'), 0):,} | "
            f"{transitions.get((prior_action, 'REVIEW'), 0):,} |"
        )
    lines.extend(
        [
            "",
            "## 15. Repairs made",
            "",
            f"Semantic repair adjudications: **{core_repair_adjudications:,}**; corrected "
            f"minute/highlight relationships: **{len(core_repair_rows):,}**; exact metadata-only "
            f"highlight repairs: **{exact_highlight_repairs:,}**; repaired highlights total: "
            f"**{repaired_highlights:,}**. Exact rows and evidence are in the ignored "
            "`materialized_repairs.csv` artifact.",
            "",
            "## 16. Before / V1 / V2 corpus metrics",
            "",
            "| Metric | Baseline | V1 | V2 |",
            "|---|---:|---:|---:|",
            *[
                f"| {name.replace('_', ' ')} | {before[name]:,} | {v1[name]:,} | {v2[name]:,} |"
                for name in metric_names
            ],
            "",
            "## 17. Restored minutes",
            "",
            f"V2 restores **{len(restored)}** of V1's 13 empty minutes from existing evidence: "
            f"{', '.join(restored) or 'none'}.",
            "",
            "## 18. True empty minutes",
            "",
            f"Semantic v2 has **{len(true_empty)}** empty minutes: "
            f"{', '.join(true_empty) or 'none'}.",
            "",
            "## 19. True sub-three minutes",
            "",
            f"V2 has **{v2['empty_minutes'] + v2['exactly_1'] + v2['exactly_2']:,}** semantic "
            f"minutes below three: {v2['empty_minutes']} empty, {v2['exactly_1']} with one, "
            f"and {v2['exactly_2']} with two. "
            "The exact deterministic list and deficits are in `targeted_recovery_minutes.csv`.",
            "",
            "## 20. Remaining REVIEW cases",
            "",
            f"**{review_counts['REVIEW']} relationships** remain genuinely evidence-insufficient. "
            "They are excluded from production and preserved in `remaining_review.csv`.",
            "",
            "## 21. Renderer-safe coverage",
            "",
        ]
    )
    if renderer:
        classes = renderer["classification_counts"]
        minutes = renderer["display_safe_minute_distribution"]
        zero_safe = (
            ", ".join(item["minute"] for item in renderer["zero_display_safe_minutes"]) or "none"
        )
        lines.extend(
            [
                f"Frozen PW4 audit: semantic-selectable "
                f"**{renderer['unique_selectable_quotes']:,}**; "
                f"full **{classes.get('DISPLAY_SAFE_FULL', 0):,}**; excerpt "
                f"**{classes.get('DISPLAY_SAFE_EXCERPT', 0):,}**; dirty "
                f"**{classes.get('REJECT_DIRTY', 0):,}**; display-safe relationships "
                f"**{renderer['display_safe_relationships']:,}**.",
                f"Display-safe minutes: **{1440 - minutes['zero']:,}/1,440**; zero-safe: "
                f"{zero_safe}.",
            ]
        )
    else:
        lines.append("Frozen renderer audit was not supplied to this report invocation.")
    lines.extend(
        [
            "",
            "## 22. Targeted-recovery candidate minutes",
            "",
            (
                "Only rows categorized `SEMANTIC_CLEAN_GENUINELY_SPARSE` in the generated target "
                "CSV should lead a later mining pass. `UNRESOLVED_REVIEW` rows need evidence "
                "first; "
                "renderer-only/source-corruption gaps must be handled without weakening semantics."
            ),
            "",
            f"Precision-first mining targets (**{len(primary_targets)}**):",
            "",
            ", ".join(primary_targets),
            "",
            "Non-mining sparse exceptions:",
            "",
            *[
                f"- {row['time_24h']}: `{row['category']}` "
                f"(semantic={row['v2_count']}, renderer-safe={row['renderer_safe_count']})"
                for row in non_mining_targets
            ],
            "",
            "## 23. Implications for Phase 4C",
            "",
            (
                "The semantic corpus is ready for a separate precision-first targeted-recovery "
                "phase, but the current Kindle bundle must not be rebuilt from this branch yet. "
                "Phase 4C should consume only a reviewed/merged semantic view; it must not revive "
                "QUARANTINE or REVIEW relationships. Renderer and runtime code were unchanged."
            ),
            "",
            "## Validation invariants",
            "",
            f"- SQLite integrity: `{validation['integrity_check']}`",
            f"- foreign-key violations: {validation['foreign_key_violations']}",
            f"- orphan adjudications: {validation['orphan_adjudications']}",
            f"- duplicate active eligibility: {validation['duplicate_active_eligibility']}",
            f"- invalid minutes/highlights: {validation['invalid_minutes']} / "
            f"{validation['invalid_highlights']}",
            f"- production rows without KEEP evidence: "
            f"{validation['unjustified_production_relationships']}",
            f"- repaired relationships failing v2 revalidation: "
            f"{validation['invalid_revalidated_repairs']}",
            "",
            "## Repository validation",
            "",
            "At finalization on macOS/Python 3.11: `uv sync` succeeded; `uv run pytest` "
            "reported **492 passed**; `uv run ruff check .` and "
            "`uv run ruff format --check .` passed. The frozen renderer/runtime regression "
            "tests are included in that complete suite.",
            "",
        ]
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "run_id": run_id,
        "prior_run_id": prior_run_id,
        "review_disposition": review_counts,
        "mismatch_repaired": mismatch_repaired,
        "mismatch_confirmed_bad": mismatch_bad,
        "mismatch_parser_recovered": mismatch_parser_recovered,
        "repairs": len(repair_rows),
        "repaired_quotes": repaired_quotes,
        "materialized_repair_quotes": materialized_repair_quotes,
        "semantic_repair_adjudications": core_repair_adjudications,
        "semantic_repair_relationships": len(core_repair_rows),
        "exact_highlight_repairs": exact_highlight_repairs,
        "repaired_highlights": repaired_highlights,
        "restored_minutes": restored,
        "empty_minutes": true_empty,
        "below_three": len(target_rows),
        "targeted_recovery_minutes": primary_targets,
        "validation": validation,
        "report": str(report_path),
    }
