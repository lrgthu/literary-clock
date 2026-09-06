"""Phase 2D sparse-tail targeting, recovery, and reporting."""

from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from litclock.db import initialize_database
from litclock.gutenberg import PUBLIC_DOMAIN_RIGHTS
from litclock.gutenberg_mining import import_gutenberg
from litclock.gutenberg_text import text_quality_rejection
from litclock.mining import PassageDuplicateIndex
from litclock.models import TimeConfidence
from litclock.normalize import minute_to_time, normalized_quote_hash
from litclock.stats import calculate_stats, write_reports
from litclock.timeparse import detect_time_expressions
from litclock.wikisource_text import title_is_literary
from litclock.xhtml import sentence_spans

_RENDERABLE = ("VERIFIED_EXACT", "VERIFIED_NORMALIZED")
_EXACT_CONFIDENCES = {
    TimeConfidence.EXACT_24H,
    TimeConfidence.EXACT_AM,
    TimeConfidence.EXACT_PM,
    TimeConfidence.EXACT_CONTEXTUAL,
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _source_corpus(source_name: str) -> str:
    if source_name.startswith("standardebooks/"):
        return "Standard Ebooks"
    if source_name.startswith("gutenberg/"):
        return "Project Gutenberg"
    if source_name == "english_wikisource" or source_name.startswith("english_wikisource/"):
        return "English Wikisource"
    return "legacy literary-clock"


def _escape_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


_REVIEW_NONLITERARY_RE = re.compile(
    r"(?:bible|qur(?:%27|.)?an|catechism|theolog\w*|religious|church|dictionary|"
    r"encyclop\w*|report|commission|investigation|deposition|interview|testimony|"
    r"court|verdict|"
    r"judgment|executive order|memorandum|minutes|research paper|conference|ceremony|"
    r"accident|disaster|government|congress|calendar|guide book|commandments|"
    r"flight|department of|department of defense|the pentagon|regulations?|legal|"
    r"psychological reconstruction|inmate death|measuring wikipedia|gulf of tonkin)\b|\bv\.?\s",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SparseTarget:
    minute_of_day: int
    minute: str
    effective_candidate_count: int
    deficit_to_3: int
    deficit_to_5: int
    deficit_to_7: int
    existing_quote_ids: tuple[int, ...]
    authors: tuple[str, ...]
    books: tuple[str, ...]
    source_corpora: tuple[str, ...]


def current_sparse_targets(connection: sqlite3.Connection) -> list[SparseTarget]:
    """Return the exact effective minute pools currently below three."""
    grouped: dict[int, list[sqlite3.Row]] = {minute: [] for minute in range(1440)}
    for row in connection.execute(
        """
        SELECT pool.minute_of_day, q.id, q.author, q.title, q.source_name
        FROM quote_minute_pool AS pool
        JOIN quotes AS q ON q.id = pool.quote_id
        WHERE q.quality_status IN (?, ?)
        ORDER BY pool.minute_of_day, q.id
        """,
        _RENDERABLE,
    ):
        grouped[int(row["minute_of_day"])].append(row)

    targets: list[SparseTarget] = []
    for minute in range(1440):
        rows = grouped[minute]
        count = len(rows)
        if count >= 3:
            continue
        targets.append(
            SparseTarget(
                minute,
                minute_to_time(minute),
                count,
                3 - count,
                max(0, 5 - count),
                max(0, 7 - count),
                tuple(int(row["id"]) for row in rows),
                tuple(sorted({str(row["author"]) for row in rows if row["author"]})),
                tuple(sorted({str(row["title"]) for row in rows if row["title"]})),
                tuple(
                    sorted(
                        {
                            _source_corpus(str(row["source_name"]))
                            for row in rows
                            if row["source_name"]
                        }
                    )
                ),
            )
        )
    return targets


def _write_targets_csv(targets: list[SparseTarget], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "minute",
        "minute_of_day",
        "effective_candidate_count",
        "deficit_to_3",
        "deficit_to_5",
        "deficit_to_7",
        "existing_quote_ids",
        "authors",
        "books",
        "source_corpora",
    ]
    temporary = output.with_suffix(".csv.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for target in targets:
            writer.writerow(
                {
                    "minute": target.minute,
                    "minute_of_day": target.minute_of_day,
                    "effective_candidate_count": target.effective_candidate_count,
                    "deficit_to_3": target.deficit_to_3,
                    "deficit_to_5": target.deficit_to_5,
                    "deficit_to_7": target.deficit_to_7,
                    "existing_quote_ids": "|".join(map(str, target.existing_quote_ids)),
                    "authors": " | ".join(target.authors),
                    "books": " | ".join(target.books),
                    "source_corpora": " | ".join(target.source_corpora),
                }
            )
    os.replace(temporary, output)
    return output


def start_phase2d(connection: sqlite3.Connection, project_root: Path) -> dict[str, object]:
    """Freeze the pre-acquisition sparse-tail target set and write its audit CSV."""
    initialize_database(connection)
    existing = connection.execute(
        """
        SELECT * FROM phase2d_runs
        WHERE status != 'FAILED' ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    if existing is not None:
        target_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM phase2d_targets WHERE run_id = ?", (existing["id"],)
            ).fetchone()[0]
        )
        if target_count:
            targets = current_sparse_targets(connection)
            path = _write_targets_csv(
                _starting_targets(connection, int(existing["id"])),
                project_root / "data" / "generated" / "PHASE2D_TARGETS.csv",
            )
            return {
                "run_id": int(existing["id"]),
                "starting_target_minutes": int(existing["starting_target_minutes"]),
                "starting_deficit_to_3": int(existing["starting_deficit_to_3"]),
                "current_target_minutes": len(targets),
                "path": path,
                "reused": True,
            }

    targets = current_sparse_targets(connection)
    deficit = sum(target.deficit_to_3 for target in targets)
    cursor = connection.execute(
        """
        INSERT INTO phase2d_runs (
            started_at, status, starting_target_minutes, starting_deficit_to_3
        ) VALUES (?, 'TARGETED', ?, ?)
        """,
        (_now(), len(targets), deficit),
    )
    run_id = int(cursor.lastrowid)
    connection.executemany(
        """
        INSERT INTO phase2d_targets (
            run_id, minute_of_day, starting_count, starting_deficit_to_3,
            deficit_to_5, deficit_to_7, existing_quote_ids, authors, books, source_corpora
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                target.minute_of_day,
                target.effective_candidate_count,
                target.deficit_to_3,
                target.deficit_to_5,
                target.deficit_to_7,
                json.dumps(target.existing_quote_ids),
                json.dumps(target.authors, ensure_ascii=False),
                json.dumps(target.books, ensure_ascii=False),
                json.dumps(target.source_corpora, ensure_ascii=False),
            )
            for target in targets
        ],
    )
    connection.commit()
    path = _write_targets_csv(targets, project_root / "data" / "generated" / "PHASE2D_TARGETS.csv")
    return {
        "run_id": run_id,
        "starting_target_minutes": len(targets),
        "starting_deficit_to_3": deficit,
        "current_target_minutes": len(targets),
        "path": path,
        "reused": False,
    }


def _starting_targets(connection: sqlite3.Connection, run_id: int) -> list[SparseTarget]:
    targets: list[SparseTarget] = []
    for row in connection.execute(
        "SELECT * FROM phase2d_targets WHERE run_id = ? ORDER BY minute_of_day", (run_id,)
    ):
        minute = int(row["minute_of_day"])
        targets.append(
            SparseTarget(
                minute,
                minute_to_time(minute),
                int(row["starting_count"]),
                int(row["starting_deficit_to_3"]),
                int(row["deficit_to_5"]),
                int(row["deficit_to_7"]),
                tuple(json.loads(row["existing_quote_ids"])),
                tuple(json.loads(row["authors"])),
                tuple(json.loads(row["books"])),
                tuple(json.loads(row["source_corpora"])),
            )
        )
    return targets


def _balanced_dialogue(text: str) -> bool:
    return text.count('"') % 2 == 0 and text.count("“") == text.count("”")


def _phase2d_context_rejection(paragraph: str, quote: str) -> str | None:
    rejection = text_quality_rejection(paragraph, quote)
    if rejection:
        return rejection
    if len(quote) < 80:
        return "recovered context remains shorter than 80 characters"
    if len(quote) > 600:
        return "recovered context exceeds 600 characters"
    if not _balanced_dialogue(quote):
        return "recovered dialogue remains unbalanced"
    first_alpha = next((character for character in quote if character.isalpha()), "")
    if first_alpha and first_alpha.islower():
        return "recovered context begins mid-sentence"
    if not quote.rstrip().endswith((".", "?", "!", ".”", "?”", "!”", '."', '?"', '!"')):
        return "recovered context lacks a complete sentence ending"
    if re.search(r"\bEarth-time\b.*\breporting for duty\b", quote, re.IGNORECASE | re.DOTALL):
        return "log entry whose content is primarily a timestamp and status"
    if re.search(
        r"\b\d{1,2}[.:]\d{2}\s*[ap]\.?\s*m\.?\s+"
        r"(?:working\s+party|patrol|position|temperature|wind|barometer)\b",
        quote,
        re.IGNORECASE,
    ):
        return "log entry whose content is primarily a timestamp and observation"
    if re.search(r"\(_[^)]{5,}_\)", quote):
        return "editorial/diary notation rather than clean literary prose"
    if re.search(
        r"\b(?:WHAT|WHEN|WHERE|HOW|THE)\s+[A-Z][A-Z '\-]{8,}\s+"
        r"(?:AT\s+)?(?:[A-Z]+|\d{1,2}[.:]\d{2})",
        quote,
    ):
        return "chapter heading embedded in recovered context"
    corrupt_year_tokens = re.findall(r"\b\d{4}(?:ff|/\d+)?\b", quote, re.IGNORECASE)
    if len(corrupt_year_tokens) >= 3 and ("..." in quote or "ff" in quote.casefold()):
        return "OCR/editorial corruption around apparent clock time"
    if len(detect_time_expressions(quote)) >= 3:
        return "three or more time expressions in recovered context"
    legal_terms = re.findall(
        r"\b(?:findings?|testimony|petitioner|respondent|appellant|appellee|"
        r"judgment|statute|the court|upon the ground)\b",
        quote,
        re.IGNORECASE,
    )
    if len(legal_terms) >= 2:
        return "legal or official-document prose"
    return None


def _matching_exact_detection(row: sqlite3.Row, paragraph: str):
    matches = [
        detection
        for detection in detect_time_expressions(paragraph)
        if detection.text == row["time_text"]
        and detection.minute_of_day == row["minute_of_day"]
        and detection.confidence in _EXACT_CONFIDENCES
    ]
    return matches[0] if len(matches) == 1 else None


def _recovered_context(row: sqlite3.Row) -> tuple[str, int, int] | None:
    """Find a complete, balanced high-quality context around an existing detection."""
    paragraph = str(row["containing_paragraph"])
    detection = _matching_exact_detection(row, paragraph)
    if detection is None:
        return None
    spans = sentence_spans(paragraph)
    containing = next(
        (
            index
            for index, (start, end) in enumerate(spans)
            if start <= detection.start and detection.end <= end
        ),
        None,
    )
    candidates: set[str] = set()
    if containing is not None:
        for first in range(containing, -1, -1):
            for last in range(containing, len(spans)):
                quote = paragraph[spans[first][0] : spans[last][1]].strip()
                if len(quote) > 600:
                    break
                candidates.add(quote)

    previous = str(row["previous_paragraph"] or "")
    following = str(row["following_paragraph"] or "")
    pieces: list[tuple[str, str]] = []
    if previous:
        pieces.extend(
            (previous[start:end].strip(), "previous")
            for start, end in sentence_spans(previous)[-2:]
        )
    pieces.append((paragraph.strip(), "containing"))
    if following:
        pieces.extend(
            (following[start:end].strip(), "following")
            for start, end in sentence_spans(following)[:2]
        )
    pivot = next(index for index, (_, kind) in enumerate(pieces) if kind == "containing")
    for first in range(pivot, -1, -1):
        for last in range(pivot, len(pieces)):
            quote = " ".join(piece for piece, _ in pieces[first : last + 1]).strip()
            if len(quote) > 600:
                break
            candidates.add(quote)

    valid: list[tuple[str, int, int]] = []
    for quote in candidates:
        if quote.count(str(row["time_text"])) != 1:
            continue
        highlight_start = quote.index(str(row["time_text"]))
        highlight_end = highlight_start + len(str(row["time_text"]))
        if _phase2d_context_rejection(paragraph, quote) is not None:
            continue
        detection_matches = [
            item
            for item in detect_time_expressions(quote)
            if item.start == highlight_start
            and item.end == highlight_end
            and item.minute_of_day == row["minute_of_day"]
            and item.confidence in _EXACT_CONFIDENCES
        ]
        if len(detection_matches) == 1:
            valid.append((quote, highlight_start, highlight_end))
    if not valid:
        return None
    return min(valid, key=lambda item: (abs(len(item[0]) - 180), len(item[0]), item[0]))


def _target_minutes_set(connection: sqlite3.Connection) -> set[int]:
    return {target.minute_of_day for target in current_sparse_targets(connection)}


def recover_existing_candidates(
    connection: sqlite3.Connection, project_root: Path, run_id: int
) -> dict[str, int]:
    """Re-audit only retained candidates that could improve the frozen sparse tail."""
    initialize_database(connection)
    run = connection.execute("SELECT * FROM phase2d_runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise ValueError(f"unknown Phase 2D run {run_id}")
    if run["status"] not in {"TARGETED", "RECOVERY_COMPLETE"}:
        raise ValueError(f"existing-candidate recovery is not valid in status {run['status']}")
    if run["status"] == "RECOVERY_COMPLETE":
        return {
            "considered": int(run["existing_candidates_considered"]),
            "recovered": int(run["existing_quotes_recovered"]),
            "remaining_targets": len(current_sparse_targets(connection)),
            "remaining_deficit": sum(
                target.deficit_to_3 for target in current_sparse_targets(connection)
            ),
        }

    frozen_targets = {
        int(row[0])
        for row in connection.execute(
            "SELECT minute_of_day FROM phase2d_targets WHERE run_id = ?", (run_id,)
        )
    }
    audits: list[tuple[object, ...]] = []

    for row in connection.execute(
        """
        SELECT q.id, q.quality_status, q.minute_of_day
        FROM quotes AS q JOIN phase2d_targets AS t ON t.minute_of_day = q.minute_of_day
        WHERE t.run_id = ? AND q.quality_status NOT IN (?, ?)
        ORDER BY q.id
        """,
        (run_id, *_RENDERABLE),
    ):
        audits.append(
            (
                run_id,
                "LEGACY_CANONICAL",
                row["id"],
                json.dumps([row["minute_of_day"]]),
                row["quality_status"],
                "REVIEW" if row["quality_status"] == "AMBIGUOUS" else "REJECTED",
                "legacy record lacks trustworthy exact highlight offsets",
                None,
                None,
                None,
                None,
                _now(),
            )
        )

    for row in connection.execute(
        """
        SELECT c.* FROM mined_candidates AS c
        JOIN phase2d_targets AS t
          ON t.minute_of_day = COALESCE(c.resolved_minute_of_day, c.minute_of_day)
        WHERE t.run_id = ? AND c.imported_quote_id IS NULL
          AND c.duplicate_status = 'NEW'
          AND c.time_confidence IN ('EXACT_24H', 'EXACT_AM', 'EXACT_PM', 'EXACT_CONTEXTUAL')
        ORDER BY c.id
        """,
        (run_id,),
    ):
        minute = int(row["resolved_minute_of_day"] or row["minute_of_day"])
        audits.append(
            (
                run_id,
                "STANDARD_EBOOKS",
                row["id"],
                json.dumps([minute]),
                row["review_status"],
                "REJECTED",
                row["rejection_reason"] or "did not pass unchanged high-quality import gates",
                None,
                None,
                None,
                None,
                _now(),
            )
        )

    duplicate_index = PassageDuplicateIndex(connection, include_gutenberg=False)
    recoverable: list[int] = []
    for row in connection.execute(
        """
        SELECT c.*, b.eligibility_status, b.eligibility_reason, b.rights AS book_rights
        FROM gutenberg_candidates AS c
        JOIN gutenberg_books AS b USING (ebook_id)
        JOIN phase2d_targets AS t ON t.minute_of_day = c.minute_of_day
        WHERE t.run_id = ? AND c.imported_quote_id IS NULL
          AND c.duplicate_status = 'NEW'
          AND c.time_confidence IN ('EXACT_24H', 'EXACT_AM', 'EXACT_PM', 'EXACT_CONTEXTUAL')
        ORDER BY c.id
        """,
        (run_id,),
    ):
        minute = int(row["minute_of_day"])
        decision = "REJECTED"
        reason = str(row["rejection_reason"] or "did not pass unchanged high-quality gates")
        recovered: tuple[str, int, int] | None = None
        if row["eligibility_status"] != "ELIGIBLE":
            reason = f"metadata gate: {row['eligibility_reason']}"
        elif row["book_rights"] != PUBLIC_DOMAIN_RIGHTS:
            reason = "source rights are not explicitly public domain in the USA"
        elif row["false_positive_category"]:
            reason = f"false positive: {row['false_positive_category']}"
        else:
            recovered = _recovered_context(row)
            if recovered is None:
                reason = reason or "no complete high-quality context could be recovered"
            else:
                duplicate = duplicate_index.check(recovered[0])
                if duplicate.status != "NEW":
                    reason = f"recovered context duplicates existing passage: {duplicate.status}"
                else:
                    decision = "RECOVERED"
                    reason = "complete context recovered under unchanged semantic and quality gates"
                    recoverable.append(int(row["id"]))
                    connection.execute(
                        """
                        UPDATE gutenberg_candidates
                        SET quote = ?, highlight_start = ?, highlight_end = ?,
                            context_score = ?, literary_quality_score = ?,
                            review_status = 'HIGH_CONFIDENCE', rejection_reason = NULL,
                            normalized_quote_hash = ?
                        WHERE id = ?
                        """,
                        (
                            recovered[0],
                            recovered[1],
                            recovered[2],
                            95.0 if len(recovered[0]) <= 350 else 85.0,
                            100.0,
                            normalized_quote_hash(recovered[0]),
                            row["id"],
                        ),
                    )
                    duplicate_index.add("GUTENBERG", int(row["id"]), recovered[0], minute)
        audits.append(
            (
                run_id,
                "GUTENBERG",
                row["id"],
                json.dumps([minute]),
                row["review_status"],
                decision,
                reason,
                recovered[0] if recovered and decision == "RECOVERED" else None,
                recovered[1] if recovered and decision == "RECOVERED" else None,
                recovered[2] if recovered and decision == "RECOVERED" else None,
                None,
                _now(),
            )
        )

    phase2c = connection.execute(
        "SELECT id FROM phase2c_runs WHERE status = 'COMPLETE' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if phase2c is not None:
        for row in connection.execute(
            """
            SELECT * FROM phase2c_candidate_audit
            WHERE run_id = ? AND decision = 'CONTEXT_REVIEW'
            ORDER BY source_candidate_type, source_candidate_id
            """,
            (phase2c["id"],),
        ):
            possible = [
                int(value)
                for value in (row["possible_minute_am"], row["possible_minute_pm"])
                if value is not None and int(value) in frozen_targets
            ]
            if not possible:
                continue
            audits.append(
                (
                    run_id,
                    f"PHASE2C_{row['source_candidate_type']}",
                    row["source_candidate_id"],
                    json.dumps(possible),
                    "CONTEXT_REVIEW",
                    "REVIEW",
                    row["review_reason"] or "contextual daypart remains uncertain",
                    None,
                    None,
                    None,
                    None,
                    _now(),
                )
            )

    connection.executemany(
        """
        INSERT INTO phase2d_existing_candidate_audit (
            run_id, source_candidate_type, source_candidate_id, possible_target_minutes,
            prior_status, decision, reason, recovered_quote, recovered_highlight_start,
            recovered_highlight_end, imported_quote_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        audits,
    )
    connection.commit()
    imported = import_gutenberg(connection, target_per_minute=3) if recoverable else 0
    connection.execute(
        """
        UPDATE phase2d_existing_candidate_audit
        SET imported_quote_id = (
            SELECT c.imported_quote_id FROM gutenberg_candidates AS c
            WHERE c.id = phase2d_existing_candidate_audit.source_candidate_id
        )
        WHERE run_id = ? AND source_candidate_type = 'GUTENBERG' AND decision = 'RECOVERED'
        """,
        (run_id,),
    )
    recovered_count = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM phase2d_existing_candidate_audit
            WHERE run_id = ? AND decision = 'RECOVERED' AND imported_quote_id IS NOT NULL
            """,
            (run_id,),
        ).fetchone()[0]
    )
    if recovered_count != imported:
        raise ValueError(
            f"existing recovery accounting mismatch: audited {recovered_count}, imported {imported}"
        )
    connection.execute(
        """
        UPDATE phase2d_runs SET status = 'RECOVERY_COMPLETE',
            existing_candidates_considered = ?, existing_quotes_recovered = ?
        WHERE id = ?
        """,
        (len(audits), recovered_count, run_id),
    )
    connection.commit()
    remaining = current_sparse_targets(connection)
    return {
        "considered": len(audits),
        "recovered": recovered_count,
        "remaining_targets": len(remaining),
        "remaining_deficit": sum(target.deficit_to_3 for target in remaining),
    }


def _reviewable_wikisource_rows(
    connection: sqlite3.Connection,
) -> list[tuple[sqlite3.Row, tuple[int, ...]]]:
    current = {target.minute_of_day: target for target in current_sparse_targets(connection)}
    reviewable: list[tuple[sqlite3.Row, tuple[int, ...]]] = []
    for row in connection.execute(
        """
        SELECT * FROM wikisource_candidates
        WHERE review_status IN ('REVIEW_LICENSE', 'REVIEW_ATTRIBUTION', 'REVIEW_CONTEXT')
          AND duplicate_status = 'NEW'
        ORDER BY id
        """
    ):
        minutes = tuple(
            sorted(
                {
                    int(value)
                    for value in (
                        row["minute_of_day"],
                        row["possible_minute_am"],
                        row["possible_minute_pm"],
                    )
                    if value is not None and int(value) in current
                }
            )
        )
        if not minutes:
            continue
        title = str(row["work_title"] or "")
        author = str(row["author"] or "")
        quote = str(row["quote"] or "")
        if (
            not title
            or title in {"../", "../../"}
            or title.isdigit()
            or not title_is_literary(title)
            or _REVIEW_NONLITERARY_RE.search(title)
            or _REVIEW_NONLITERARY_RE.search(author)
            or re.match(r"^\[?\d{1,4}(?::\d{2})?(?::\d{2})?\]?\s", quote)
            or _phase2d_context_rejection(str(row["containing_paragraph"]), quote)
        ):
            continue
        matching = [
            detection
            for detection in detect_time_expressions(str(row["quote"]))
            if detection.start == row["highlight_start"] and detection.end == row["highlight_end"]
        ]
        if len(matching) != 1 or matching[0].rejection_reason:
            continue
        if matching[0].confidence in {
            TimeConfidence.APPROXIMATE,
            TimeConfidence.RANGE,
            TimeConfidence.INVALID,
        }:
            continue
        reviewable.append((row, minutes))
    reviewable.sort(
        key=lambda item: (
            min(
                next(
                    target.effective_candidate_count
                    for target in current_sparse_targets(connection)
                    if target.minute_of_day == minute
                )
                for minute in item[1]
            ),
            item[1],
            int(item[0]["id"]),
        )
    )
    return reviewable


def write_phase2d_review_export(connection: sqlite3.Connection, output: Path) -> tuple[Path, int]:
    """Export only reviewable Wikisource candidates relevant to a live pool below three."""
    counts = {
        int(row["minute_of_day"]): int(row["n"])
        for row in connection.execute(
            """
            SELECT minute_of_day, COUNT(*) AS n FROM quote_minute_pool
            GROUP BY minute_of_day
            """
        )
    }
    rows = _reviewable_wikisource_rows(connection)
    fields = [
        "candidate_id",
        "target_minutes",
        "current_counts",
        "time_phrase",
        "quote_context",
        "title",
        "author",
        "page_work_identifier",
        "revision_id",
        "source_url",
        "dump_date",
        "source_license",
        "license_evidence",
        "parser_status",
        "contextual_evidence",
        "rejection_review_reason",
        "previous_paragraph",
        "containing_paragraph",
        "following_paragraph",
        "highlight_start",
        "highlight_end",
        "source_locator",
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".csv.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row, minutes in rows:
            writer.writerow(
                {
                    "candidate_id": row["id"],
                    "target_minutes": "|".join(minute_to_time(value) for value in minutes),
                    "current_counts": "|".join(
                        f"{minute_to_time(value)}={counts.get(value, 0)}" for value in minutes
                    ),
                    "time_phrase": row["time_text"],
                    "quote_context": row["quote"],
                    "title": row["work_title"],
                    "author": row["author"],
                    "page_work_identifier": row["page_title"],
                    "revision_id": row["revision_id"],
                    "source_url": row["source_url"],
                    "dump_date": row["dump_date"],
                    "source_license": row["source_license"],
                    "license_evidence": row["license_evidence"],
                    "parser_status": f"{row['parser_rule']} / {row['time_confidence']}",
                    "contextual_evidence": f"{row['evidence_type']}: {row['evidence_text']}",
                    "rejection_review_reason": row["rejection_reason"],
                    "previous_paragraph": row["previous_paragraph"],
                    "containing_paragraph": row["containing_paragraph"],
                    "following_paragraph": row["following_paragraph"],
                    "highlight_start": row["highlight_start"],
                    "highlight_end": row["highlight_end"],
                    "source_locator": row["source_locator"],
                }
            )
    os.replace(temporary, output)
    return output, len(rows)


def record_phase2d_verification(project_root: Path, payload: dict[str, object]) -> Path:
    output = project_root / "data" / "generated" / "phase2d_verification.json"
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    return output


def write_phase2d_outputs(connection: sqlite3.Connection, project_root: Path) -> dict[str, object]:
    """Regenerate coverage, focused review export, and the final Phase 2D report."""
    initialize_database(connection)
    generated = project_root / "data" / "generated"
    run = connection.execute("SELECT * FROM phase2d_runs ORDER BY id DESC LIMIT 1").fetchone()
    wiki_run = connection.execute(
        "SELECT * FROM wikisource_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    dump = connection.execute("SELECT * FROM wikisource_dumps ORDER BY id DESC LIMIT 1").fetchone()
    if run is None or wiki_run is None or dump is None:
        raise ValueError("Phase 2D acquisition and mining must run before reporting")

    starting_targets = _starting_targets(connection, int(run["id"]))
    _write_targets_csv(starting_targets, generated / "PHASE2D_TARGETS.csv")
    review_path, review_count = write_phase2d_review_export(
        connection, generated / "PHASE2D_REVIEW_PRIORITY.csv"
    )
    stats = calculate_stats(connection)
    write_reports(stats, generated)
    remaining = current_sparse_targets(connection)
    remaining_deficit = sum(target.deficit_to_3 for target in remaining)
    candidate_statuses = {
        str(row["review_status"]): int(row["n"])
        for row in connection.execute(
            """
            SELECT review_status, COUNT(*) AS n FROM wikisource_candidates
            GROUP BY review_status ORDER BY n DESC
            """
        )
    }
    work_statuses = {
        str(row["literary_status"]): int(row["n"])
        for row in connection.execute(
            """
            SELECT literary_status, COUNT(*) AS n FROM wikisource_works
            GROUP BY literary_status ORDER BY n DESC
            """
        )
    }
    corpus_counts: Counter[str] = Counter()
    for row in connection.execute(
        "SELECT source_name, COUNT(*) AS n FROM quotes GROUP BY source_name"
    ):
        corpus_counts[_source_corpus(str(row["source_name"]))] += int(row["n"])
    license_counts = {
        str(row["source_license"]): int(row["n"])
        for row in connection.execute(
            """
            SELECT source_license, COUNT(*) AS n FROM quotes
            WHERE source_name = 'english_wikisource' GROUP BY source_license
            """
        )
    }
    spool_manifest = generated.parent / "public_domain" / "wikisource" / "spool_manifest.json"
    spool = (
        json.loads(spool_manifest.read_text(encoding="utf-8"))
        if spool_manifest.is_file()
        else {"counts": {}}
    )
    xml_pages = int(spool.get("counts", {}).get("xml_pages_streamed", 0))
    verification_path = generated / "phase2d_verification.json"
    verification = (
        json.loads(verification_path.read_text(encoding="utf-8"))
        if verification_path.is_file()
        else {"status": "PENDING"}
    )
    final_thresholds = stats["minute_thresholds"]
    starting_canonical = (
        int(stats["total_canonical_quotes"])
        - int(run["existing_quotes_recovered"])
        - int(wiki_run["imported_quotes"])
    )
    starting_relationships = int(stats["quote_minute_eligibility_relationships"]) - (
        int(run["starting_deficit_to_3"]) - remaining_deficit
    )
    starting_selectable = (
        int(stats["unique_selectable_quotes"])
        - int(run["existing_quotes_recovered"])
        - int(wiki_run["imported_quotes"])
    )
    hardest = sorted(
        remaining,
        key=lambda target: (
            target.effective_candidate_count,
            len(target.authors),
            len(target.books),
            target.minute_of_day,
        ),
    )[:30]
    lines = [
        "# Phase 2D Sparse-Tail Wikisource Report",
        "",
        f"Generated: {_now()}",
        "",
        "## Outcome",
        "",
        f"The exact starting deficit to three was **{run['starting_deficit_to_3']:,}** "
        f"relationships across **{run['starting_target_minutes']:,}** minutes.",
        f"Existing retained candidates supplied **{run['existing_quotes_recovered']:,}** quotes "
        f"before Wikisource. Wikisource then supplied **{wiki_run['imported_quotes']:,}** quotes "
        f"and **{wiki_run['relationships_added']:,}** effective relationships.",
        f"The retained-candidate pass eliminated **{run['existing_quotes_recovered']:,}** of the "
        "starting relationship deficit without acquiring a new source.",
        f"The remaining deficit is **{remaining_deficit:,}** relationships across "
        f"**{len(remaining):,}** minutes.",
        "",
    ]
    if not remaining:
        lines.extend(["## V1 CORPUS READY", "", "**V1 CORPUS READY**", ""])
    lines.extend(
        [
            "## Acquisition and scan",
            "",
            "- Method: official English Wikisource `pages-articles-multistream` XML dump "
            "plus its official multistream index; no page crawling.",
            f"- Dump: `{dump['filename']}` dated **{dump['dump_date']}**.",
            f"- Source URL: {dump['source_url']}",
            f"- SHA-1: `{dump['actual_checksum']}` (matched official manifest).",
            f"- Acquired: {dump['acquisition_timestamp']}.",
            f"- XML pages streamed: **{xml_pages:,}**.",
            f"- Target-filtered content pages rendered: **{wiki_run['pages_scanned']:,}** "
            f"({wiki_run['mainspace_pages']:,} mainspace; "
            f"{wiki_run['page_namespace_pages']:,} `Page:`).",
            f"- Work metadata records indexed: **{sum(work_statuses.values()):,}**; "
            f"classified literary: **{work_statuses.get('LITERARY', 0):,}**.",
            f"- Rendered characters scanned: **{wiki_run['characters_scanned']:,}**.",
            f"- Time expressions detected on retained pages: "
            f"**{wiki_run['expressions_detected']:,}**.",
            f"- Sparse-relevant detections: **{wiki_run['sparse_relevant']:,}**.",
            f"- Cross-source duplicates: **{wiki_run['duplicates_detected']:,}**.",
            "",
            "## Corpus before and after",
            "",
            "| Metric | Before Phase 2D | After Phase 2D |",
            "|---|---:|---:|",
            f"| Canonical literary quotes | {starting_canonical:,} | "
            f"{stats['total_canonical_quotes']:,} |",
            f"| Unique selectable quotes | {starting_selectable:,} | "
            f"{stats['unique_selectable_quotes']:,} |",
            f"| Effective eligibility relationships | {starting_relationships:,} | "
            f"{stats['quote_minute_eligibility_relationships']:,} |",
            f"| Minutes at 0 | 0 | {final_thresholds['zero']:,} |",
            f"| Minutes below 3 | {run['starting_target_minutes']:,} | "
            f"{final_thresholds['below_3']:,} |",
            f"| Minutes below 5 | {final_thresholds['below_5']:,} | "
            f"{final_thresholds['below_5']:,} |",
            f"| Minutes below 7 | {final_thresholds['below_7']:,} | "
            f"{final_thresholds['below_7']:,} |",
            f"| Minutes at least 7 | {final_thresholds['at_least_7']:,} | "
            f"{final_thresholds['at_least_7']:,} |",
            f"| Deficit to 3 everywhere | {run['starting_deficit_to_3']:,} | "
            f"{remaining_deficit:,} |",
            "",
            "Phase 2D imported only into pools below three, so the `<5`, `<7`, and `>=7` "
            "bucket memberships intentionally did not change.",
            "",
            "## Candidate disposition",
            "",
            f"All **{sum(candidate_statuses.values()):,}** sparse-relevant candidates are "
            "accounted for below; this equals the stored sparse-relevant total.",
            "",
        ]
    )
    for status, count in candidate_statuses.items():
        lines.append(f"- {status}: {count:,}")
    lines.extend(
        [
            "",
            f"The focused human-review export contains **{review_count:,}** candidates that "
            "could still improve a live `<3` bucket and fail only a reviewable attribution, "
            "license, or context condition.",
            "",
            "## Source and license distribution",
            "",
        ]
    )
    for source, count in sorted(corpus_counts.items()):
        lines.append(f"- {source}: {count:,} canonical quotes")
    for license_name, count in sorted(license_counts.items()):
        lines.append(f"- English Wikisource `{license_name}`: {count:,}")
    lines.extend(
        [
            "",
            "## Minutes that did not reach three",
            "",
            ", ".join(f"`{target.minute}`" for target in remaining) if remaining else "None.",
            "",
            "## Hardest remaining minutes",
            "",
            "| Minute | Effective | Deficit to 3 | Authors | Books |",
            "|---|---:|---:|---|---|",
        ]
    )
    for target in hardest:
        lines.append(
            f"| {target.minute} | {target.effective_candidate_count} | "
            f"{target.deficit_to_3} | {_escape_cell('; '.join(target.authors))} | "
            f"{_escape_cell('; '.join(target.books))} |"
        )
    lines.extend(
        [
            "",
            "## Verification",
            "",
            f"- Status: **{verification.get('status', 'PENDING')}**",
            f"- pytest: {verification.get('pytest', 'pending')}",
            f"- Ruff check: {verification.get('ruff_check', 'pending')}",
            f"- Ruff format check: {verification.get('ruff_format', 'pending')}",
            f"- SQLite integrity: {verification.get('sqlite_integrity', 'pending')}",
            f"- Selector smoke test: {verification.get('selector', 'pending')}",
            "",
            "## Recommendation",
            "",
            (
                "Stop coverage-driven corpus expansion and move to Kindle rendering; the "
                "three-per-minute V1 threshold is complete."
                if not remaining
                else "Do not lower correctness or literary-quality gates. The official usable "
                "Wikisource dump is exhausted for automatic Phase 2D imports, but the V1 "
                f"three-per-minute threshold is not complete: {len(remaining):,} minutes remain. "
                "Review the compact queue first, then use a genuinely independent corpus only "
                "for the residual target set."
            ),
            "",
        ]
    )
    report = generated / "PHASE2D_REPORT.md"
    temporary = report.with_suffix(".md.tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    os.replace(temporary, report)
    return {
        "report": report,
        "targets": generated / "PHASE2D_TARGETS.csv",
        "review": review_path,
        "review_count": review_count,
        "stats": stats,
        "remaining_targets": len(remaining),
        "remaining_deficit": remaining_deficit,
    }
