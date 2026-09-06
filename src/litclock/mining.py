"""Standard Ebooks mining orchestration, deduplication, review, and import."""

from __future__ import annotations

import csv
import difflib
import hashlib
import json
import os
import re
import sqlite3
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from litclock.db import initialize_database
from litclock.models import DuplicateKind, TimeConfidence
from litclock.normalize import minute_to_time, normalized_quote_hash, text_hash
from litclock.standard_ebooks import (
    SOURCE_LICENSE,
    AcquiredBook,
    CatalogRepository,
    acquire_repository,
    fetch_catalog,
    select_diverse_repositories,
)
from litclock.stats import calculate_stats, write_reports
from litclock.timeparse import detect_time_expressions
from litclock.xhtml import extract_paragraphs, extract_quote_context

EXACT_CONFIDENCES = {
    TimeConfidence.EXACT_24H.value,
    TimeConfidence.EXACT_AM.value,
    TimeConfidence.EXACT_PM.value,
    TimeConfidence.EXACT_CONTEXTUAL.value,
}
_TOKEN_RE = re.compile(r"\b[^\W_]+(?:[’'][^\W_]+)?\b", re.UNICODE)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class DuplicateResult:
    status: str
    quote_id: int | None = None
    candidate_id: int | None = None
    gutenberg_candidate_id: int | None = None
    wikisource_candidate_id: int | None = None


@dataclass(frozen=True, slots=True)
class _Passage:
    kind: str
    identifier: int
    minute_of_day: int | None
    quote: str
    normalized: str
    tokens: tuple[str, ...]


class PassageDuplicateIndex:
    """Detect exact, normalized, and extended/shortened versions of known passages."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        include_gutenberg: bool = True,
        include_wikisource: bool = True,
    ) -> None:
        self.passages: dict[tuple[str, int], _Passage] = {}
        self.exact: dict[tuple[str, int | None], _Passage] = {}
        self.normalized: dict[tuple[str, int | None], _Passage] = {}
        self.shingles: dict[tuple[str, ...], set[tuple[str, int]]] = defaultdict(set)
        for row in connection.execute("SELECT id, minute_of_day, quote FROM quotes"):
            self.add("LEGACY", int(row["id"]), row["quote"], int(row["minute_of_day"]))
        for row in connection.execute(
            """
            SELECT id, minute_of_day, quote FROM mined_candidates
            WHERE duplicate_status = 'NEW'
            """
        ):
            self.add(
                "MINED",
                int(row["id"]),
                row["quote"],
                int(row["minute_of_day"]) if row["minute_of_day"] is not None else None,
            )
        gutenberg_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'gutenberg_candidates'"
        ).fetchone()
        if gutenberg_table and include_gutenberg:
            for row in connection.execute(
                """
                SELECT id, minute_of_day, quote FROM gutenberg_candidates
                WHERE duplicate_status = 'NEW'
                """
            ):
                self.add(
                    "GUTENBERG",
                    int(row["id"]),
                    row["quote"],
                    int(row["minute_of_day"]) if row["minute_of_day"] is not None else None,
                )
        wikisource_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'wikisource_candidates'"
        ).fetchone()
        if wikisource_table and include_wikisource:
            for row in connection.execute(
                """
                SELECT id, minute_of_day, quote FROM wikisource_candidates
                WHERE duplicate_status = 'NEW'
                """
            ):
                self.add(
                    "WIKISOURCE",
                    int(row["id"]),
                    row["quote"],
                    int(row["minute_of_day"]) if row["minute_of_day"] is not None else None,
                )

    @staticmethod
    def _tokens(quote: str) -> tuple[str, ...]:
        return tuple(token.casefold() for token in _TOKEN_RE.findall(quote))

    @staticmethod
    def _shingles(tokens: tuple[str, ...]) -> set[tuple[str, ...]]:
        if len(tokens) < 8:
            return set()
        return {tokens[index : index + 8] for index in range(len(tokens) - 7)}

    def add(self, kind: str, identifier: int, quote: str, minute_of_day: int | None = None) -> None:
        normalized = normalized_quote_hash(quote)
        passage = _Passage(kind, identifier, minute_of_day, quote, normalized, self._tokens(quote))
        key = (kind, identifier)
        self.passages[key] = passage
        self.exact.setdefault((quote, minute_of_day), passage)
        self.normalized.setdefault((normalized, minute_of_day), passage)
        for shingle in self._shingles(passage.tokens):
            self.shingles[shingle].add(key)

    @staticmethod
    def _result(label: str, passage: _Passage) -> DuplicateResult:
        status = f"{label}_{passage.kind}"
        if passage.kind == "LEGACY":
            return DuplicateResult(status, quote_id=passage.identifier)
        if passage.kind == "MINED":
            return DuplicateResult(status, candidate_id=passage.identifier)
        if passage.kind == "GUTENBERG":
            return DuplicateResult(status, gutenberg_candidate_id=passage.identifier)
        return DuplicateResult(status, wikisource_candidate_id=passage.identifier)

    @staticmethod
    def _lookup(
        values: dict[tuple[str, int | None], _Passage], key: str, minute_of_day: int | None
    ) -> _Passage | None:
        if minute_of_day is not None:
            return values.get((key, minute_of_day))
        return next((passage for (value, _), passage in values.items() if value == key), None)

    def check(self, quote: str, minute_of_day: int | None = None) -> DuplicateResult:
        exact = self._lookup(self.exact, quote, minute_of_day)
        if exact:
            return self._result("EXACT", exact)
        normalized = normalized_quote_hash(quote)
        normalized_match = self._lookup(self.normalized, normalized, minute_of_day)
        if normalized_match:
            return self._result("NORMALIZED", normalized_match)
        tokens = self._tokens(quote)
        possible: set[tuple[str, int]] = set()
        shingles = list(self._shingles(tokens))
        rare = sorted(
            shingles,
            key=lambda value: (len(self.shingles.get(value, set())), value),
        )[:8]
        if len(shingles) <= 8:
            spaced = shingles
        else:
            ordered = [tokens[index : index + 8] for index in range(len(tokens) - 7)]
            spaced = [ordered[index * (len(ordered) - 1) // 7] for index in range(8)]
        for shingle in set(rare + spaced):
            possible.update(self.shingles.get(shingle, set()))
        candidate_string = "\0".join(tokens)
        for key in sorted(possible):
            passage = self.passages[key]
            if minute_of_day is not None and passage.minute_of_day != minute_of_day:
                continue
            shorter = min(len(tokens), len(passage.tokens))
            if shorter < 8:
                continue
            passage_string = "\0".join(passage.tokens)
            if candidate_string in passage_string or passage_string in candidate_string:
                return self._result("CONTEXT", passage)
            block = difflib.SequenceMatcher(
                None, tokens, passage.tokens, autojunk=False
            ).find_longest_match()
            if block.size / shorter >= 0.8:
                return self._result("CONTEXT", passage)
        return DuplicateResult("NEW")


def _coverage_state(
    connection: sqlite3.Connection,
) -> tuple[dict[int, int], dict[int, set[str]], dict[int, set[str]]]:
    counts = {
        int(row["minute_of_day"]): int(row["n"])
        for row in connection.execute(
            """
            SELECT pool.minute_of_day, COUNT(*) AS n
            FROM quote_minute_pool AS pool JOIN quotes AS q ON q.id = pool.quote_id
            WHERE q.quality_status IN ('VERIFIED_EXACT', 'VERIFIED_NORMALIZED')
            GROUP BY pool.minute_of_day
            """
        )
    }
    authors: dict[int, set[str]] = defaultdict(set)
    titles: dict[int, set[str]] = defaultdict(set)
    for row in connection.execute(
        """
        SELECT pool.minute_of_day, q.author, q.title
        FROM quote_minute_pool AS pool JOIN quotes AS q ON q.id = pool.quote_id
        WHERE q.quality_status IN ('VERIFIED_EXACT', 'VERIFIED_NORMALIZED')
        """
    ):
        if row["author"]:
            authors[int(row["minute_of_day"])].add(row["author"].casefold())
        if row["title"]:
            titles[int(row["minute_of_day"])].add(row["title"].casefold())
    return counts, authors, titles


def target_priority(
    minute: int | None,
    author: str,
    title: str,
    coverage: tuple[dict[int, int], dict[int, set[str]], dict[int, set[str]]],
) -> float:
    if minute is None:
        return 0.0
    counts, authors, titles = coverage
    count = counts.get(minute, 0)
    if count == 0:
        base = 1000
    elif count == 1:
        base = 900
    elif count == 2:
        base = 800
    elif count <= 4:
        base = 600
    elif count <= 6:
        base = 400
    else:
        base = 25
    diversity = 0
    if author and author.casefold() not in authors.get(minute, set()):
        diversity += 20
    if title and title.casefold() not in titles.get(minute, set()):
        diversity += 10
    return float(base + max(0, 7 - count) * 10 + diversity)


def ensure_phase2a_baseline(connection: sqlite3.Connection, generated: Path) -> Path:
    path = generated / "phase2a_baseline.json"
    if path.exists():
        return path
    stats = calculate_stats(connection)
    payload = {key: value for key, value in stats.items() if key != "minute_coverage"}
    generated.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def _record_acquired(connection: sqlite3.Connection, book: AcquiredBook) -> int:
    now = _now()
    connection.execute(
        """
        INSERT INTO standard_ebooks_books (
            repository, source_url, default_branch, commit_sha, author, title, language,
            rights, source_license, acquisition_timestamp, processing_status, content_checksum,
            text_file_count, github_updated_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACQUIRED', ?, ?, ?, ?, ?)
        ON CONFLICT(repository) DO UPDATE SET
            source_url = excluded.source_url,
            default_branch = excluded.default_branch,
            commit_sha = excluded.commit_sha,
            author = excluded.author,
            title = excluded.title,
            language = excluded.language,
            rights = excluded.rights,
            source_license = excluded.source_license,
            acquisition_timestamp = excluded.acquisition_timestamp,
            processing_status = CASE
                WHEN standard_ebooks_books.processing_status = 'PROCESSED' THEN 'PROCESSED'
                ELSE 'ACQUIRED'
            END,
            content_checksum = excluded.content_checksum,
            text_file_count = excluded.text_file_count,
            github_updated_at = excluded.github_updated_at,
            error = NULL,
            updated_at = excluded.updated_at
        """,
        (
            book.repository.name,
            book.repository.source_url,
            book.repository.default_branch,
            book.commit_sha,
            book.metadata.author,
            book.metadata.title,
            book.metadata.language,
            book.metadata.rights,
            SOURCE_LICENSE,
            book.acquisition_timestamp,
            book.content_checksum,
            len(book.text_files),
            book.repository.github_updated_at,
            now,
            now,
        ),
    )
    connection.commit()
    return int(
        connection.execute(
            "SELECT id FROM standard_ebooks_books WHERE repository = ?",
            (book.repository.name,),
        ).fetchone()[0]
    )


def _record_acquisition_failure(
    connection: sqlite3.Connection, repository: CatalogRepository, error: Exception
) -> None:
    now = _now()
    connection.execute(
        """
        INSERT INTO standard_ebooks_books (
            repository, source_url, default_branch, source_license, processing_status,
            github_updated_at, error, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'ACQUISITION_FAILED', ?, ?, ?, ?)
        ON CONFLICT(repository) DO UPDATE SET
            processing_status = 'ACQUISITION_FAILED', error = excluded.error,
            updated_at = excluded.updated_at
        """,
        (
            repository.name,
            repository.source_url,
            repository.default_branch,
            SOURCE_LICENSE,
            repository.github_updated_at,
            str(error),
            now,
            now,
        ),
    )
    connection.commit()


def _candidate_review_status(
    confidence: TimeConfidence,
    duplicate: DuplicateResult,
    context_reason: str | None,
    context_score: float,
    literary_score: float,
) -> tuple[str, str | None]:
    if duplicate.status != "NEW":
        return "REJECTED_DUPLICATE", f"duplicate passage: {duplicate.status}"
    if confidence == TimeConfidence.INVALID:
        return "REJECTED", "invalid time expression"
    if confidence not in {
        TimeConfidence.EXACT_24H,
        TimeConfidence.EXACT_AM,
        TimeConfidence.EXACT_PM,
        TimeConfidence.EXACT_CONTEXTUAL,
    }:
        return "PENDING_REVIEW", confidence.value.casefold().replace("_", " ")
    if context_reason:
        return "PENDING_REVIEW", context_reason
    if context_score < 70 or literary_score < 60:
        return "PENDING_REVIEW", "quality score below conservative auto-import threshold"
    return "HIGH_CONFIDENCE", None


def _process_book(
    connection: sqlite3.Connection,
    book: AcquiredBook,
    book_id: int,
    duplicate_index: PassageDuplicateIndex,
    coverage: tuple[dict[int, int], dict[int, set[str]], dict[int, set[str]]],
    *,
    reprocess: bool,
) -> dict[str, int]:
    if not book.metadata.language.casefold().startswith("en"):
        connection.execute(
            """
            UPDATE standard_ebooks_books SET processing_status = 'SKIPPED_LANGUAGE',
                processing_timestamp = ?, updated_at = ? WHERE id = ?
            """,
            (_now(), _now(), book_id),
        )
        connection.commit()
        return {"skipped": 1}
    genres = {genre.casefold() for genre in book.metadata.genres}
    if genres & {"poetry", "drama"}:
        connection.execute(
            """
            UPDATE standard_ebooks_books SET processing_status = 'SKIPPED_NON_PROSE',
                processing_timestamp = ?, updated_at = ? WHERE id = ?
            """,
            (_now(), _now(), book_id),
        )
        connection.commit()
        return {"skipped": 1}
    if reprocess:
        connection.execute("DELETE FROM mined_candidates WHERE book_id = ?", (book_id,))
    connection.execute(
        """
        UPDATE standard_ebooks_books
        SET processing_status = 'PROCESSING', updated_at = ? WHERE id = ?
        """,
        (_now(), book_id),
    )
    connection.commit()

    totals: dict[str, int] = defaultdict(int)
    literary_file_count = 0
    for path in book.text_files:
        source_file = path.relative_to(book.root).as_posix()
        paragraphs = extract_paragraphs(path, source_file)
        if not paragraphs:
            continue
        literary_file_count += 1
        for paragraph in paragraphs:
            totals["characters"] += len(paragraph.text)
            totals["words"] += len(_TOKEN_RE.findall(paragraph.text))
            detections = detect_time_expressions(paragraph.text)
            totals["expressions"] += len(detections)
            for detection in detections:
                context = extract_quote_context(paragraph.text, detection)
                context_reason = context.rejection_reason
                if len(detect_time_expressions(context.quote)) >= 3 and not context_reason:
                    context_reason = "three or more time expressions in one context"
                duplicate = duplicate_index.check(context.quote, detection.minute_of_day)
                review_status, rejection = _candidate_review_status(
                    detection.confidence,
                    duplicate,
                    context_reason,
                    context.context_score,
                    context.literary_quality_score,
                )
                if detection.rejection_reason and not rejection:
                    rejection = detection.rejection_reason
                priority = target_priority(
                    detection.minute_of_day,
                    book.metadata.author,
                    book.metadata.title,
                    coverage,
                )
                expression_source_start = paragraph.document_offset + detection.start
                expression_source_end = paragraph.document_offset + detection.end
                quote_source_start = paragraph.document_offset + context.quote_start
                quote_source_end = paragraph.document_offset + context.quote_end
                locator = (
                    f"{source_file}#{paragraph.section or 'body'}:p{paragraph.paragraph_index}:"
                    f"chars={quote_source_start}-{quote_source_end}"
                )
                fingerprint = "\0".join(
                    (
                        book.repository.name,
                        book.commit_sha,
                        source_file,
                        str(paragraph.paragraph_index),
                        str(expression_source_start),
                        str(expression_source_end),
                        detection.parser_rule,
                    )
                )
                cursor = connection.execute(
                    """
                    INSERT INTO mined_candidates (
                        book_id, minute_of_day, time_24h, time_text, quote, author, title,
                        source_repository, source_url, source_commit, source_file, source_section,
                        source_locator, source_paragraph_index, source_quote_start,
                        source_quote_end, source_expression_start, source_expression_end,
                        highlight_start, highlight_end,
                        parser_rule, time_confidence, ampm_evidence, context_score,
                        literary_quality_score, duplicate_status, duplicate_of_quote_id,
                        duplicate_of_candidate_id, target_priority, review_status, rejection_reason,
                        candidate_hash, normalized_quote_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        book_id,
                        detection.minute_of_day,
                        minute_to_time(detection.minute_of_day)
                        if detection.minute_of_day is not None
                        else None,
                        detection.text,
                        context.quote,
                        book.metadata.author,
                        book.metadata.title,
                        book.repository.name,
                        book.repository.source_url,
                        book.commit_sha,
                        source_file,
                        paragraph.section,
                        locator,
                        paragraph.paragraph_index,
                        quote_source_start,
                        quote_source_end,
                        expression_source_start,
                        expression_source_end,
                        context.highlight_start,
                        context.highlight_end,
                        detection.parser_rule,
                        detection.confidence.value,
                        detection.ampm_evidence,
                        context.context_score,
                        context.literary_quality_score,
                        duplicate.status,
                        duplicate.quote_id,
                        duplicate.candidate_id,
                        priority,
                        review_status,
                        rejection,
                        hashlib.sha256(fingerprint.encode()).hexdigest(),
                        normalized_quote_hash(context.quote),
                        _now(),
                    ),
                )
                candidate_id = int(cursor.lastrowid)
                if duplicate.status == "NEW":
                    duplicate_index.add(
                        "MINED", candidate_id, context.quote, detection.minute_of_day
                    )
                totals[detection.confidence.value] += 1
                totals[review_status] += 1
                if duplicate.status != "NEW":
                    totals["duplicates"] += 1

    processed_at = _now()
    connection.execute(
        """
        UPDATE standard_ebooks_books SET
            processing_status = 'PROCESSED', processing_timestamp = ?, text_file_count = ?,
            word_count = ?, character_count = ?, error = NULL, updated_at = ?
        WHERE id = ?
        """,
        (
            processed_at,
            literary_file_count,
            totals["words"],
            totals["characters"],
            processed_at,
            book_id,
        ),
    )
    connection.commit()
    totals["scanned"] = 1
    return totals


def export_source_manifest(connection: sqlite3.Connection, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.jsonl"
    rows = connection.execute("SELECT * FROM standard_ebooks_books ORDER BY repository")
    temporary = path.with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
    os.replace(temporary, path)
    return path


def mine_standard_ebooks(
    connection: sqlite3.Connection,
    project_root: Path,
    *,
    limit_books: int,
    workers: int = 4,
    refresh_catalog: bool = False,
    reprocess: bool = False,
) -> dict[str, Any]:
    if not 1 <= limit_books:
        raise ValueError("--limit-books must be positive")
    if not 1 <= workers <= 8:
        raise ValueError("--workers must be between 1 and 8")
    initialize_database(connection)
    data_directory = project_root / "data" / "public_domain" / "standard_ebooks"
    generated = project_root / "data" / "generated"
    ensure_phase2a_baseline(connection, generated)
    catalog = fetch_catalog(data_directory, refresh=refresh_catalog)
    completed = set()
    if reprocess:
        indexed = {
            row[0]
            for row in connection.execute(
                "SELECT repository FROM standard_ebooks_books WHERE commit_sha IS NOT NULL"
            )
        }
        catalog = [repository for repository in catalog if repository.name in indexed]
    else:
        completed = {
            row[0]
            for row in connection.execute(
                """
                SELECT repository FROM standard_ebooks_books
                WHERE processing_status IN (
                    'PROCESSED', 'SKIPPED_LANGUAGE', 'SKIPPED_NON_PROSE'
                )
                """
            )
        }
    selected = select_diverse_repositories(catalog, completed, limit_books)
    run_cursor = connection.execute(
        """
        INSERT INTO mining_runs (started_at, status, requested_book_limit)
        VALUES (?, 'RUNNING', ?)
        """,
        (_now(), limit_books),
    )
    run_id = int(run_cursor.lastrowid)
    connection.commit()

    acquired: dict[str, AcquiredBook] = {}
    failures = 0
    books_directory = data_directory / "books"
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(acquire_repository, repository, books_directory): repository
            for repository in selected
        }
        for future in as_completed(futures):
            repository = futures[future]
            try:
                book = future.result()
                acquired[repository.name] = book
                _record_acquired(connection, book)
            except Exception as error:
                failures += 1
                _record_acquisition_failure(connection, repository, error)

    if reprocess and acquired:
        placeholders = ",".join("?" for _ in acquired)
        connection.execute(
            f"""
            DELETE FROM mined_candidates WHERE book_id IN (
                SELECT id FROM standard_ebooks_books WHERE repository IN ({placeholders})
            )
            """,  # noqa: S608 - placeholders are generated, values remain bound
            tuple(acquired),
        )
        connection.commit()
    duplicate_index = PassageDuplicateIndex(connection)
    coverage = _coverage_state(connection)
    totals: dict[str, int] = defaultdict(int)
    totals["acquired"] = len(acquired)
    totals["skipped"] = failures
    for repository in selected:
        book = acquired.get(repository.name)
        if book is None:
            continue
        book_id = int(
            connection.execute(
                "SELECT id FROM standard_ebooks_books WHERE repository = ?", (repository.name,)
            ).fetchone()[0]
        )
        existing_status = connection.execute(
            "SELECT processing_status FROM standard_ebooks_books WHERE id = ?", (book_id,)
        ).fetchone()[0]
        if existing_status == "PROCESSED" and not reprocess:
            continue
        try:
            result = _process_book(
                connection,
                book,
                book_id,
                duplicate_index,
                coverage,
                reprocess=False,
            )
            for key, value in result.items():
                totals[key] += value
        except Exception as error:
            connection.rollback()
            totals["skipped"] += 1
            connection.execute(
                """
                UPDATE standard_ebooks_books SET processing_status = 'PROCESSING_FAILED',
                    error = ?, updated_at = ? WHERE id = ?
                """,
                (str(error), _now(), book_id),
            )
            connection.commit()

    connection.execute(
        """
        UPDATE mining_runs SET finished_at = ?, status = 'COMPLETE', books_acquired = ?,
            books_scanned = ?, books_skipped = ?, words_scanned = ?, characters_scanned = ?,
            expressions_detected = ?, exact_resolved = ?, ambiguous = ?, approximate = ?,
            ranges = ?, candidates_rejected = ?, duplicates_detected = ?,
            high_confidence_candidates = ? WHERE id = ?
        """,
        (
            _now(),
            totals["acquired"],
            totals["scanned"],
            totals["skipped"],
            totals["words"],
            totals["characters"],
            totals["expressions"],
            sum(totals[confidence] for confidence in EXACT_CONFIDENCES),
            totals[TimeConfidence.AMPM_AMBIGUOUS.value],
            totals[TimeConfidence.APPROXIMATE.value],
            totals[TimeConfidence.RANGE.value],
            totals["REJECTED"] + totals["REJECTED_DUPLICATE"],
            totals["duplicates"],
            totals["HIGH_CONFIDENCE"],
            run_id,
        ),
    )
    connection.commit()
    export_source_manifest(connection, data_directory)
    return calculate_mining_stats(connection)


def calculate_mining_stats(connection: sqlite3.Connection) -> dict[str, Any]:
    confidence_counts = {
        row["time_confidence"]: int(row["n"])
        for row in connection.execute(
            "SELECT time_confidence, COUNT(*) AS n FROM mined_candidates GROUP BY time_confidence"
        )
    }
    review_counts = {
        row["review_status"]: int(row["n"])
        for row in connection.execute(
            "SELECT review_status, COUNT(*) AS n FROM mined_candidates GROUP BY review_status"
        )
    }
    duplicate_counts = {
        row["duplicate_status"]: int(row["n"])
        for row in connection.execute(
            "SELECT duplicate_status, COUNT(*) AS n FROM mined_candidates GROUP BY duplicate_status"
        )
    }
    parser_rules = {
        row["parser_rule"]: int(row["n"])
        for row in connection.execute(
            "SELECT parser_rule, COUNT(*) AS n FROM mined_candidates GROUP BY parser_rule"
        )
    }
    books = connection.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(processing_status = 'PROCESSED') AS processed,
               SUM(processing_status = 'SKIPPED_LANGUAGE') AS skipped_language,
               SUM(processing_status = 'SKIPPED_NON_PROSE') AS skipped_non_prose,
               SUM(processing_status = 'ACQUISITION_FAILED') AS acquisition_failed,
               COALESCE(SUM(word_count), 0) AS words,
               COALESCE(SUM(character_count), 0) AS characters
        FROM standard_ebooks_books
        """
    ).fetchone()
    detected = int(connection.execute("SELECT COUNT(*) FROM mined_candidates").fetchone()[0])
    exact = sum(confidence_counts.get(status, 0) for status in EXACT_CONFIDENCES)
    duplicates = sum(count for status, count in duplicate_counts.items() if status != "NEW")
    duplicates_legacy = sum(
        count for status, count in duplicate_counts.items() if status.endswith("_LEGACY")
    )
    duplicates_mined = sum(
        count for status, count in duplicate_counts.items() if status.endswith("_MINED")
    )
    return {
        "books_indexed": int(books["total"] or 0),
        "books_scanned": int(books["processed"] or 0),
        "books_skipped_language": int(books["skipped_language"] or 0),
        "books_skipped_non_prose": int(books["skipped_non_prose"] or 0),
        "books_acquisition_failed": int(books["acquisition_failed"] or 0),
        "words_scanned": int(books["words"] or 0),
        "characters_scanned": int(books["characters"] or 0),
        "expressions_detected": detected,
        "exact_resolved_candidates": exact,
        "ambiguous_candidates": confidence_counts.get(TimeConfidence.AMPM_AMBIGUOUS.value, 0),
        "approximate_candidates": confidence_counts.get(TimeConfidence.APPROXIMATE.value, 0),
        "range_candidates": confidence_counts.get(TimeConfidence.RANGE.value, 0),
        "invalid_candidates": confidence_counts.get(TimeConfidence.INVALID.value, 0),
        "duplicates_detected": duplicates,
        "duplicates_against_legacy": duplicates_legacy,
        "duplicates_within_mined": duplicates_mined,
        "high_confidence_candidates": sum(
            review_counts.get(status, 0)
            for status in ("HIGH_CONFIDENCE", "IMPORTED", "DEFERRED_DENSE")
        ),
        "high_confidence_imported": review_counts.get("IMPORTED", 0),
        "candidates_rejected": review_counts.get("REJECTED", 0)
        + review_counts.get("REJECTED_DUPLICATE", 0),
        "nonduplicate_candidates_rejected": review_counts.get("REJECTED", 0),
        "confidence_counts": confidence_counts,
        "review_status_counts": review_counts,
        "duplicate_status_counts": duplicate_counts,
        "parser_rule_counts": parser_rules,
    }


def export_review_queue(connection: sqlite3.Connection, output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "id",
        "target_priority",
        "review_status",
        "time_confidence",
        "time_24h",
        "time_text",
        "quote",
        "title",
        "author",
        "source_repository",
        "source_commit",
        "source_locator",
        "parser_rule",
        "ampm_evidence",
        "context_score",
        "literary_quality_score",
        "duplicate_status",
        "rejection_reason",
    ]
    rows = list(
        connection.execute(
            """
            SELECT id, target_priority, review_status, time_confidence, time_24h, time_text,
                   quote, title, author, source_repository, source_commit, source_locator,
                   parser_rule, ampm_evidence, context_score, literary_quality_score,
                   duplicate_status, rejection_reason
            FROM mined_candidates
            WHERE review_status NOT IN ('IMPORTED', 'REJECTED_DUPLICATE')
            ORDER BY target_priority DESC, context_score DESC, literary_quality_score DESC, id
            """
        )
    )
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(dict(row) for row in rows)
    return len(rows)


def import_high_confidence(connection: sqlite3.Connection, *, target_per_minute: int = 7) -> int:
    initialize_database(connection)
    if target_per_minute < 1:
        raise ValueError("target_per_minute must be positive")
    counts, authors_by_minute, books_by_minute = _coverage_state(connection)
    candidates = list(
        connection.execute(
            """
            SELECT c.*, b.language, b.rights, b.content_checksum
            FROM mined_candidates AS c
            JOIN standard_ebooks_books AS b ON b.id = c.book_id
            WHERE c.review_status = 'HIGH_CONFIDENCE'
              AND c.duplicate_status = 'NEW'
              AND c.minute_of_day IS NOT NULL
              AND c.imported_quote_id IS NULL
            ORDER BY c.target_priority DESC, c.context_score DESC,
                     c.literary_quality_score DESC, c.id
            """
        )
    )
    by_minute: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for candidate in candidates:
        by_minute[int(candidate["minute_of_day"])].append(candidate)
    candidates = []
    for minute in sorted(by_minute, key=lambda value: (counts.get(value, 0), value)):
        remaining = by_minute[minute]
        seen_authors = set(authors_by_minute.get(minute, set()))
        seen_books = set(books_by_minute.get(minute, set()))
        while remaining:
            remaining.sort(
                key=lambda candidate: (
                    candidate["title"] not in seen_books,
                    candidate["author"] not in seen_authors,
                    float(candidate["target_priority"]),
                    float(candidate["context_score"]),
                    float(candidate["literary_quality_score"]),
                    -int(candidate["id"]),
                ),
                reverse=True,
            )
            candidate = remaining.pop(0)
            candidates.append(candidate)
            seen_authors.add(candidate["author"])
            seen_books.add(candidate["title"])
    run_cursor = connection.execute(
        "INSERT INTO import_runs (started_at, status) VALUES (?, 'RUNNING')", (_now(),)
    )
    run_id = int(run_cursor.lastrowid)
    accepted = deferred = conflicts = 0
    for candidate in candidates:
        minute = int(candidate["minute_of_day"])
        if counts.get(minute, 0) >= target_per_minute:
            connection.execute(
                "UPDATE mined_candidates SET review_status = 'DEFERRED_DENSE' WHERE id = ?",
                (candidate["id"],),
            )
            deferred += 1
            continue
        source_name = f"standardebooks/{candidate['source_repository']}"
        source_slug = f"standardebooks-{candidate['source_repository']}"
        source_license = (
            f"{SOURCE_LICENSE}; repository rights: {candidate['rights'] or 'see source OPF'}"
        )
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
                source_license,
                candidate["source_commit"],
                candidate["source_repository"],
                candidate["content_checksum"],
                imported_at,
            ),
        )
        source_id = int(
            connection.execute("SELECT id FROM sources WHERE name = ?", (source_name,)).fetchone()[
                0
            ]
        )
        try:
            cursor = connection.execute(
                """
                INSERT INTO quotes (
                    minute_of_day, time_24h, time_text, quote, title, author, sfw, language,
                    source_name, source_url, source_license, source_record_id, quote_hash,
                    normalized_quote_hash, highlight_start, highlight_end,
                    quality_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'VERIFIED_EXACT', ?)
                """,
                (
                    minute,
                    candidate["time_24h"],
                    candidate["time_text"],
                    candidate["quote"],
                    candidate["title"],
                    candidate["author"],
                    candidate["language"] or "en",
                    source_name,
                    candidate["source_url"],
                    source_license,
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
                UPDATE mined_candidates SET review_status = 'REJECTED_DUPLICATE',
                    duplicate_status = 'NORMALIZED_LEGACY',
                    rejection_reason = 'duplicate appeared before import' WHERE id = ?
                """,
                (candidate["id"],),
            )
            conflicts += 1
            continue
        quote_id = int(cursor.lastrowid)
        connection.execute(
            "UPDATE sources SET record_count = record_count + 1 WHERE id = ?", (source_id,)
        )
        payload = json.dumps(dict(candidate), ensure_ascii=False, default=str, sort_keys=True)
        connection.execute(
            """
            INSERT INTO quote_provenance (
                quote_id, source_id, import_run_id, source_record_id, raw_time_24h, raw_time_text,
                raw_quote, raw_title, raw_author, raw_sfw, raw_quote_hash, validation_status,
                highlight_start, highlight_end, duplicate_kind, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 'VERIFIED_EXACT', ?, ?, ?, ?)
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
                DuplicateKind.CANONICAL.value,
                payload,
            ),
        )
        connection.execute(
            """
            UPDATE mined_candidates SET review_status = 'IMPORTED', imported_quote_id = ?,
                imported_at = ? WHERE id = ?
            """,
            (quote_id, imported_at, candidate["id"]),
        )
        counts[minute] = counts.get(minute, 0) + 1
        accepted += 1
    connection.execute(
        """
        UPDATE import_runs SET finished_at = ?, status = 'COMPLETE', raw_record_count = ?,
            canonical_inserted = ?, exact_duplicates = ?, trivial_variants = 0,
            malformed_records = 0, invalid_times = 0 WHERE id = ?
        """,
        (_now(), len(candidates), accepted, conflicts, run_id),
    )
    connection.commit()
    return accepted


def _metric_rows(before: dict[str, Any], after: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    return [
        ("Canonical quotes", before["total_canonical_quotes"], after["total_canonical_quotes"]),
        ("Selectable quotes", before["total_renderable_quotes"], after["total_renderable_quotes"]),
        ("Minutes covered", before["minutes_covered"], after["minutes_covered"]),
        (
            "Median quotes/minute",
            before["median_quotes_per_minute"],
            after["median_quotes_per_minute"],
        ),
        ("P10", before["percentiles"]["p10"], after["percentiles"]["p10"]),
        ("P25", before["percentiles"]["p25"], after["percentiles"]["p25"]),
        ("P75", before["percentiles"]["p75"], after["percentiles"]["p75"]),
        ("P90", before["percentiles"]["p90"], after["percentiles"]["p90"]),
        ("Minutes at 0", before["minute_thresholds"]["zero"], after["minute_thresholds"]["zero"]),
        (
            "Minutes below 3",
            before["minute_thresholds"]["below_3"],
            after["minute_thresholds"]["below_3"],
        ),
        (
            "Minutes below 5",
            before["minute_thresholds"]["below_5"],
            after["minute_thresholds"]["below_5"],
        ),
        (
            "Minutes below 7",
            before["minute_thresholds"]["below_7"],
            after["minute_thresholds"]["below_7"],
        ),
        (
            "Minutes at least 7",
            before["minute_thresholds"]["at_least_7"],
            after["minute_thresholds"]["at_least_7"],
        ),
        ("Unique authors", before["unique_authors"], after["unique_authors"]),
        ("Unique books", before["unique_books"], after["unique_books"]),
        (
            "Remaining deficit to 7 everywhere",
            before["remaining_quote_deficit_to_7"],
            after["remaining_quote_deficit_to_7"],
        ),
    ]


def write_phase2a_report(connection: sqlite3.Connection, project_root: Path) -> Path:
    generated = project_root / "data" / "generated"
    baseline_path = ensure_phase2a_baseline(connection, generated)
    before = json.loads(baseline_path.read_text(encoding="utf-8"))
    after = calculate_stats(connection)
    mining = calculate_mining_stats(connection)
    hardest = sorted(after["minute_coverage"], key=lambda row: row["target_rank"])[:50]
    zero_minutes = [
        row["time_24h"] for row in after["minute_coverage"] if row["renderable_count"] == 0
    ]
    lines = [
        "# Phase 2A — Standard Ebooks Mining Report",
        "",
        "Coverage counts only quotes with a resolved minute and exact trusted highlight offsets.",
        "Ambiguous, approximate, ranged, and lower-quality detections remain in the review queue.",
        "",
        "## Mining totals",
        "",
        "| Metric | Count |",
        "|---|---:|",
        f"| Repositories indexed | {mining['books_indexed']:,} |",
        f"| Books scanned | {mining['books_scanned']:,} |",
        f"| Non-prose books skipped | {mining['books_skipped_non_prose']:,} |",
        f"| Acquisition failures | {mining['books_acquisition_failed']:,} |",
        f"| Words scanned | {mining['words_scanned']:,} |",
        f"| Characters scanned | {mining['characters_scanned']:,} |",
        f"| Raw time expressions detected | {mining['expressions_detected']:,} |",
        f"| Exact resolved candidates | {mining['exact_resolved_candidates']:,} |",
        f"| AM/PM ambiguous candidates | {mining['ambiguous_candidates']:,} |",
        f"| Approximate candidates | {mining['approximate_candidates']:,} |",
        f"| Range candidates | {mining['range_candidates']:,} |",
        f"| Nonduplicate candidates rejected | {mining['nonduplicate_candidates_rejected']:,} |",
        f"| Duplicates against the legacy corpus | {mining['duplicates_against_legacy']:,} |",
        f"| Duplicates within mined candidates | {mining['duplicates_within_mined']:,} |",
        f"| Total duplicate candidates rejected | {mining['duplicates_detected']:,} |",
        f"| Total candidates rejected | {mining['candidates_rejected']:,} |",
        f"| High-confidence candidates | {mining['high_confidence_candidates']:,} |",
        f"| High-confidence quotes imported | {mining['high_confidence_imported']:,} |",
        "",
        "## Before Phase 2A vs. after Phase 2A",
        "",
        "| Metric | Before | After | Change |",
        "|---|---:|---:|---:|",
    ]
    for label, old, new in _metric_rows(before, after):
        lines.append(f"| {label} | {old:,} | {new:,} | {new - old:+,} |")
    lines.extend(
        [
            "",
            "## Hardest 50 minute buckets",
            "",
            "| Rank | Time | Selectable | Canonical | Authors | Books | Deficit to 7 |",
            "|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in hardest:
        lines.append(
            f"| {row['target_rank']} | {row['time_24h']} | {row['renderable_count']} | "
            f"{row['quote_count']} | {row['unique_authors']} | {row['unique_books']} | "
            f"{row['deficit_to_7']} |"
        )
    lines.extend(
        [
            "",
            "## Parser and review patterns",
            "",
            f"Confidence counts: `{json.dumps(mining['confidence_counts'], sort_keys=True)}`",
            "",
            f"Parser rule counts: `{json.dumps(mining['parser_rule_counts'], sort_keys=True)}`",
            "",
            f"Review statuses: `{json.dumps(mining['review_status_counts'], sort_keys=True)}`",
            "",
            "Duplicate statuses: `"
            f"{json.dumps(mining['duplicate_status_counts'], sort_keys=True)}`",
            "",
            "Observed precision traps routed away from automatic import:",
            "",
            "- bare 12-hour phrases without strong morning/afternoon/evening/night evidence;",
            "- approximate modifiers and deadlines such as ‘towards,’ ‘close upon,’ and ‘by’;",
            "- ranges, alternatives, compound offsets, and ‘give or take’ tolerances;",
            "- Bible chapter-and-verse citations that resemble 24-hour numeric times;",
            "- sports scores such as ‘three to one’ that resemble relative time phrases;",
            "- timetable-like text, malformed fragments, and XHTML abbreviation boundaries;",
            "- exact or normalized duplicates and expanded/contracted context duplicates.",
            "",
            "Minutes still at zero: " + (", ".join(zero_minutes) if zero_minutes else "none"),
            "",
            "## Phase 2B recommendation",
            "",
            "Review the highest-priority AM/PM-ambiguous candidates with their sentence context,",
            "then add an optional evidence-based second-stage scorer. Keep deterministic",
            "resolution, exact offsets, duplicate checks, and the seven-per-minute cap as",
            "non-negotiable gates.",
            "Do not use an LLM as the sole acceptance mechanism.",
            "",
        ]
    )
    path = generated / "PHASE2A_REPORT.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    write_reports(after, generated)
    stats_path = generated / "mining_stats.json"
    stats_path.write_text(json.dumps(mining, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
