"""Apply the bounded, source-backed English V1 recovery inventory.

The recovery file is deliberately data, not Python exceptions.  Every newly
materialized relationship is accepted only after the semantic-v2 classifier
returns KEEP.  Applying a recovery creates a new audit run containing the
previous production KEEP set plus the validated additions; older audit runs
remain inspectable.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from litclock.db import connect_database, initialize_database
from litclock.normalize import (
    clean_display_text,
    locate_time_text,
    minute_to_time,
    normalized_quote_hash,
    parse_time_24h,
    text_hash,
)
from litclock.semantic import SemanticAction, SemanticDecision, classify_clock_relationship
from litclock.semantic_audit import current_semantic_metrics

FINAL_RECOVERY_VERSION = "english-v1-corpus-freeze-v1"
_OPERATIONS = {"NEW_QUOTE", "EXISTING_QUOTE"}


@dataclass(frozen=True, slots=True)
class RecoveryRow:
    operation: str
    recovery_id: str
    quote_id: int | None
    minutes: tuple[int, ...]
    time_text: str
    quote: str
    title: str
    author: str
    source_url: str
    source_project: str
    source_license: str
    source_locator: str
    source_id: str
    source_checksum: str
    semantic_rationale: str
    source_context_before: str
    source_context_after: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _minutes(value: str) -> tuple[int, ...]:
    result: list[int] = []
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        minute = parse_time_24h(token)[0] if ":" in token else int(token)
        if not 0 <= minute < 1440:
            raise ValueError(f"minute outside 0..1439: {token}")
        if minute not in result:
            result.append(minute)
    if not result:
        raise ValueError("recovery row has no target minute")
    return tuple(result)


def load_recovery_rows(path: Path) -> list[RecoveryRow]:
    """Load and strictly validate the small committed recovery inventory."""
    with path.open(encoding="utf-8", newline="") as handle:
        records = list(csv.DictReader(handle, delimiter="\t"))
    rows: list[RecoveryRow] = []
    seen: set[str] = set()
    for number, record in enumerate(records, start=2):
        operation = record.get("operation", "").strip().upper()
        recovery_id = record.get("recovery_id", "").strip()
        if operation not in _OPERATIONS:
            raise ValueError(f"line {number}: invalid operation {operation!r}")
        if not recovery_id or recovery_id in seen:
            raise ValueError(f"line {number}: missing or duplicate recovery_id")
        seen.add(recovery_id)
        quote_id_text = record.get("quote_id", "").strip()
        row = RecoveryRow(
            operation=operation,
            recovery_id=recovery_id,
            quote_id=int(quote_id_text) if quote_id_text else None,
            minutes=_minutes(record.get("minutes", "")),
            time_text=clean_display_text(record.get("time_text", "")),
            quote=clean_display_text(record.get("quote", "")),
            title=clean_display_text(record.get("title", "")),
            author=clean_display_text(record.get("author", "")),
            source_url=record.get("source_url", "").strip(),
            source_project=record.get("source_project", "").strip(),
            source_license=record.get("source_license", "").strip(),
            source_locator=record.get("source_locator", "").strip(),
            source_id=record.get("source_id", "").strip(),
            source_checksum=record.get("source_checksum", "").strip(),
            semantic_rationale=record.get("semantic_rationale", "").strip(),
            source_context_before=clean_display_text(record.get("source_context_before") or ""),
            source_context_after=clean_display_text(record.get("source_context_after") or ""),
        )
        if operation == "EXISTING_QUOTE" and row.quote_id is None:
            raise ValueError(f"line {number}: EXISTING_QUOTE requires quote_id")
        if operation == "NEW_QUOTE" and not all(
            (
                row.time_text,
                row.quote,
                row.title,
                row.author,
                row.source_url,
                row.source_project,
                row.source_license,
                row.semantic_rationale,
            )
        ):
            raise ValueError(f"line {number}: NEW_QUOTE is missing required evidence")
        rows.append(row)
    return rows


def _decision(quote: sqlite3.Row, minute: int, row: RecoveryRow) -> SemanticDecision:
    decision = classify_clock_relationship(
        str(quote["quote"]),
        int(quote["highlight_start"]),
        int(quote["highlight_end"]),
        minute,
        expected_text=str(quote["time_text"]),
        parser_route="final_recovery",
        source_locator=row.source_locator or None,
        source_context_before=row.source_context_before or None,
        source_context_after=row.source_context_after or None,
    )
    if decision.action != SemanticAction.KEEP:
        raise ValueError(
            f"{row.recovery_id} {minute_to_time(minute)} failed semantic-v2: "
            f"{decision.action.value}/{decision.reason_code}"
        )
    return decision


def _eligibility_type(decision: SemanticDecision, minute: int, phrase: str) -> str:
    if len(decision.derived_minutes) == 2:
        return "CLOCKFACE_SHARED_AM" if minute < 720 else "CLOCKFACE_SHARED_PM"
    normalized = phrase.casefold().replace(" ", "")
    if re.search(r"a\.?m\.?", normalized):
        return "EXPLICIT_AM"
    if re.search(r"p\.?m\.?", normalized):
        return "EXPLICIT_PM"
    return "EXACT_24H"


def _source_family(source_name: str) -> str:
    if "gutenberg" in source_name.casefold():
        return "GUTENBERG"
    if "wikisource" in source_name.casefold():
        return "WIKISOURCE"
    if "standard" in source_name.casefold():
        return "STANDARD_EBOOKS"
    return "CURATED_RECOVERY"


def _fingerprint(connection: sqlite3.Connection, pairs: set[tuple[int, int]]) -> str:
    digest = hashlib.sha256()
    for quote_id, minute in sorted(pairs):
        row = connection.execute(
            "SELECT quote_hash, highlight_start, highlight_end, time_text FROM quotes WHERE id = ?",
            (quote_id,),
        ).fetchone()
        digest.update(
            "\0".join(
                (
                    str(quote_id),
                    str(minute),
                    str(row["quote_hash"]),
                    str(row["highlight_start"]),
                    str(row["highlight_end"]),
                    str(row["time_text"]),
                )
            ).encode()
        )
    return digest.hexdigest()


def apply_final_recovery(
    connection: sqlite3.Connection,
    recovery_path: Path,
    *,
    audit_version: str = FINAL_RECOVERY_VERSION,
) -> dict[str, object]:
    """Atomically add validated recoveries and activate a production-only KEEP audit."""
    initialize_database(connection)
    rows = load_recovery_rows(recovery_path)
    active = connection.execute(
        "SELECT id FROM semantic_audit_runs WHERE status = 'APPLIED'"
    ).fetchall()
    if len(active) != 1:
        raise ValueError("final recovery requires exactly one applied semantic-v2 audit")
    prior_run_id = int(active[0]["id"])
    before = current_semantic_metrics(connection)
    old_pool = list(
        connection.execute("SELECT * FROM quote_minute_pool ORDER BY quote_id, minute_of_day")
    )
    old_pairs = {(int(row["quote_id"]), int(row["minute_of_day"])) for row in old_pool}
    created_at = _now()
    additions: dict[tuple[int, int], tuple[SemanticDecision, RecoveryRow]] = {}
    inserted_quotes = 0

    with connection:
        source_sha = hashlib.sha256(recovery_path.read_bytes()).hexdigest()
        source = connection.execute(
            "SELECT id FROM sources WHERE slug = 'english-v1-final-recovery'"
        ).fetchone()
        if source is None:
            source_id = int(
                connection.execute(
                    """
                    INSERT INTO sources (
                        name, slug, source_url, source_license, upstream_commit, corpus_path,
                        corpus_sha256, record_count, imported_at
                    ) VALUES ('english_v1_final_recovery', 'english-v1-final-recovery', ?,
                              'Per-record evidence in recovery TSV', ?, ?, ?, ?, ?)
                    """,
                    (
                        recovery_path.as_posix(),
                        audit_version,
                        recovery_path.as_posix(),
                        source_sha,
                        len(rows),
                        created_at,
                    ),
                ).lastrowid
            )
        else:
            source_id = int(source["id"])
        import_run_id = int(
            connection.execute(
                """
                INSERT INTO import_runs (started_at, status, raw_record_count)
                VALUES (?, 'RUNNING', ?)
                """,
                (created_at, len(rows)),
            ).lastrowid
        )

        # A v2 repair is currently supplied by a run-scoped UNION.  Materialize
        # the active view before superseding that run so code and data remain
        # independently reproducible in the freeze audit.
        for pool in old_pool:
            connection.execute(
                """
                INSERT OR IGNORE INTO quote_minute_eligibility (
                    quote_id, minute_of_day, eligibility_type, confidence, evidence_type,
                    evidence_text, source_candidate_type, source_candidate_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*tuple(pool), created_at),
            )

        for recovery in rows:
            if recovery.operation == "EXISTING_QUOTE":
                quote_id = int(recovery.quote_id)
                quote = connection.execute(
                    "SELECT * FROM quotes WHERE id = ?", (quote_id,)
                ).fetchone()
                if quote is None:
                    raise ValueError(f"{recovery.recovery_id}: unknown quote_id {quote_id}")
            else:
                normalized_hash = normalized_quote_hash(recovery.quote)
                quote = connection.execute(
                    """
                    SELECT * FROM quotes
                    WHERE minute_of_day = ? AND normalized_quote_hash = ?
                    ORDER BY id LIMIT 1
                    """,
                    (recovery.minutes[0], normalized_hash),
                ).fetchone()
                if quote is None:
                    highlight = locate_time_text(recovery.quote, recovery.time_text)
                    if highlight.start is None or highlight.end is None:
                        raise ValueError(
                            f"{recovery.recovery_id}: exact highlighted phrase is not unique"
                        )
                    first_minute = recovery.minutes[0]
                    quote_id = int(
                        connection.execute(
                            """
                            INSERT INTO quotes (
                                minute_of_day, time_24h, time_text, quote, title, author, sfw,
                                language, source_name, source_url, source_license, source_record_id,
                                quote_hash, normalized_quote_hash, highlight_start, highlight_end,
                                quality_status, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, 1, 'en', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                first_minute,
                                minute_to_time(first_minute),
                                recovery.time_text,
                                recovery.quote,
                                recovery.title,
                                recovery.author,
                                f"final_recovery/{recovery.source_project}",
                                recovery.source_url,
                                recovery.source_license,
                                recovery.source_id or recovery.recovery_id,
                                text_hash(recovery.quote),
                                normalized_hash,
                                highlight.start,
                                highlight.end,
                                highlight.status.value,
                                created_at,
                            ),
                        ).lastrowid
                    )
                    inserted_quotes += 1
                    quote = connection.execute(
                        "SELECT * FROM quotes WHERE id = ?", (quote_id,)
                    ).fetchone()
                else:
                    quote_id = int(quote["id"])
                connection.execute(
                    """
                    INSERT OR IGNORE INTO quote_provenance (
                        quote_id, source_id, import_run_id, source_record_id, raw_time_24h,
                        raw_time_text, raw_quote, raw_title, raw_author, raw_sfw, raw_quote_hash,
                        validation_status, highlight_start, highlight_end, duplicate_kind,
                        semantic_class, semantic_action, semantic_reason_code,
                        semantic_audit_version, raw_payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        quote_id,
                        source_id,
                        import_run_id,
                        recovery.recovery_id,
                        minute_to_time(recovery.minutes[0]),
                        recovery.time_text,
                        recovery.quote,
                        recovery.title,
                        recovery.author,
                        text_hash(recovery.quote),
                        quote["quality_status"],
                        quote["highlight_start"],
                        quote["highlight_end"],
                        "CANONICAL"
                        if quote["source_name"].startswith("final_recovery/")
                        else "EXACT",
                        audit_version,
                        _json(
                            recovery.__dict__
                            if hasattr(recovery, "__dict__")
                            else {
                                field: getattr(recovery, field)
                                for field in recovery.__dataclass_fields__
                            }
                        ),
                    ),
                )

            for minute in recovery.minutes:
                decision = _decision(quote, minute, recovery)
                pair = (quote_id, minute)
                if pair in old_pairs:
                    continue
                additions[pair] = (decision, recovery)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO quote_minute_eligibility (
                        quote_id, minute_of_day, eligibility_type, confidence, evidence_type,
                        evidence_text, source_candidate_type, created_at
                    ) VALUES (?, ?, ?, 'VERIFIED', 'SEMANTIC_V2_FINAL_RECOVERY', ?,
                              'FINAL_RECOVERY', ?)
                    """,
                    (
                        quote_id,
                        minute,
                        _eligibility_type(decision, minute, str(quote["time_text"])),
                        recovery.semantic_rationale,
                        created_at,
                    ),
                )

        final_pairs = old_pairs | set(additions)
        baseline = dict(before)
        baseline["relationships"] = len(final_pairs)
        cursor = connection.execute(
            """
            INSERT INTO semantic_audit_runs (
                audit_version, started_at, finished_at, status, corpus_fingerprint,
                baseline_json, decision_counts_json
            ) VALUES (?, ?, ?, 'AUDIT_COMPLETE', ?, ?, ?)
            """,
            (
                audit_version,
                created_at,
                created_at,
                _fingerprint(connection, final_pairs),
                _json(baseline),
                _json({"KEEP": len(final_pairs), "QUARANTINE": 0, "REVIEW": 0}),
            ),
        )
        run_id = int(cursor.lastrowid)
        for quote_id, minute in sorted(final_pairs):
            quote = connection.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone()
            if (quote_id, minute) in additions:
                decision, recovery = additions[(quote_id, minute)]
                semantic_class = decision.semantic_class.value
                reason = decision.reason_code
                route = decision.parser_route
                confidence = decision.confidence
                derived = ",".join(str(value) for value in decision.derived_minutes)
                rule = decision.derivation_rule
                family = _source_family(recovery.source_project)
                provenance = recovery.semantic_rationale
            else:
                audit = connection.execute(
                    """
                    SELECT * FROM semantic_time_audit
                    WHERE run_id = ? AND quote_id = ? AND minute_of_day = ? AND action = 'KEEP'
                    """,
                    (prior_run_id, quote_id, minute),
                ).fetchone()
                repair = connection.execute(
                    """
                    SELECT * FROM semantic_relationship_repairs
                    WHERE run_id = ? AND quote_id = ? AND repaired_minute = ?
                    """,
                    (prior_run_id, quote_id, minute),
                ).fetchone()
                evidence = audit or repair
                if evidence is None:
                    raise ValueError(f"active relationship {quote_id}/{minute} lacks KEEP evidence")
                semantic_class = str(evidence["semantic_class"])
                reason = str(evidence["reason_code"] if audit else evidence["semantic_reason_code"])
                route = str(audit["parser_route"] if audit else "semantic_v2_repair")
                confidence = str(evidence["confidence"])
                derived = str(audit["derived_minutes"] if audit else minute)
                rule = audit["derivation_rule"] if audit else "semantic_v2_repair"
                family = str(
                    audit["source_family"] if audit else _source_family(quote["source_name"])
                )
                provenance = "Inherited from applied semantic-v2 production KEEP set."
            highlighted = str(quote["quote"])[
                int(quote["highlight_start"]) : int(quote["highlight_end"])
            ]
            connection.execute(
                """
                INSERT INTO semantic_time_audit (
                    run_id, quote_id, minute_of_day, semantic_class, action, reason_code,
                    parser_route, confidence, highlighted_text, derived_minutes, derivation_rule,
                    source_family, reviewed_by, review_provenance, audit_version, created_at
                ) VALUES (?, ?, ?, ?, 'KEEP', ?, ?, ?, ?, ?, ?, ?,
                          'english-v1-final-recovery', ?, ?, ?)
                """,
                (
                    run_id,
                    quote_id,
                    minute,
                    semantic_class,
                    reason,
                    route,
                    confidence,
                    highlighted,
                    derived,
                    rule,
                    family,
                    provenance,
                    audit_version,
                    created_at,
                ),
            )
        connection.execute(
            "UPDATE semantic_audit_runs SET status = 'SUPERSEDED' WHERE id = ?", (prior_run_id,)
        )
        connection.execute(
            "UPDATE semantic_audit_runs SET status = 'APPLIED', applied_at = ? WHERE id = ?",
            (created_at, run_id),
        )
        after = current_semantic_metrics(connection)
        connection.execute(
            "UPDATE semantic_audit_runs SET after_json = ? WHERE id = ?", (_json(after), run_id)
        )
        connection.execute(
            """
            UPDATE import_runs SET finished_at = ?, status = 'COMPLETE',
                canonical_inserted = ? WHERE id = ?
            """,
            (created_at, inserted_quotes, import_run_id),
        )

    return {
        "run_id": run_id,
        "audit_version": audit_version,
        "inserted_quotes": inserted_quotes,
        "relationships_added": len(additions),
        "before": before,
        "after": after,
        "corpus_fingerprint": _fingerprint(connection, final_pairs),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    connection = connect_database(args.db)
    try:
        result = apply_final_recovery(connection, args.input)
    finally:
        connection.close()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
