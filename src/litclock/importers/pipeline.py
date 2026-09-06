"""Atomic, provenance-preserving corpus import and deduplication."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from litclock.db import connect_database, initialize_database
from litclock.importers.readers import read_source
from litclock.models import (
    DuplicateKind,
    ImportSummary,
    MalformedRecord,
    QualityStatus,
    RawQuote,
    SourceSpec,
)
from litclock.normalize import (
    clean_display_text,
    locate_time_text,
    normalized_quote_hash,
    parse_time_24h,
    quality_rank,
    text_hash,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _bool_db(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _insert_issue(
    connection: sqlite3.Connection,
    *,
    source_id: int,
    run_id: int,
    record_id: str | None,
    status: QualityStatus,
    error: str,
    payload: object,
) -> None:
    connection.execute(
        """
        INSERT INTO import_issues (
            source_id, import_run_id, source_record_id, quality_status, error, raw_payload
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (source_id, run_id, record_id, status.value, error, _json(payload)),
    )


def _merge_sfw(existing: int | None, incoming: bool | None) -> int | None:
    incoming_db = _bool_db(incoming)
    if existing == 0 or incoming_db == 0:
        return 0
    if existing == 1 or incoming_db == 1:
        return 1
    return None


def _import_quote(
    connection: sqlite3.Connection,
    *,
    raw: RawQuote,
    spec: SourceSpec,
    source_id: int,
    run_id: int,
    created_at: str,
) -> DuplicateKind | QualityStatus:
    try:
        minute_of_day, canonical_time = parse_time_24h(raw.time_24h)
    except ValueError as error:
        _insert_issue(
            connection,
            source_id=source_id,
            run_id=run_id,
            record_id=raw.source_record_id,
            status=QualityStatus.INVALID_TIME,
            error=str(error),
            payload=raw.raw_payload,
        )
        return QualityStatus.INVALID_TIME

    quote = clean_display_text(raw.quote)
    time_text = clean_display_text(raw.time_text)
    title = clean_display_text(raw.title)
    author = clean_display_text(raw.author)
    if not quote:
        _insert_issue(
            connection,
            source_id=source_id,
            run_id=run_id,
            record_id=raw.source_record_id,
            status=QualityStatus.MALFORMED,
            error="quote is empty after normalization",
            payload=raw.raw_payload,
        )
        return QualityStatus.MALFORMED

    highlight = locate_time_text(quote, time_text)
    exact_hash = text_hash(quote)
    normalized_hash = normalized_quote_hash(quote)
    existing = connection.execute(
        "SELECT * FROM quotes WHERE minute_of_day = ? AND normalized_quote_hash = ?",
        (minute_of_day, normalized_hash),
    ).fetchone()

    sfw_db = _bool_db(raw.sfw)
    if existing is None:
        cursor = connection.execute(
            """
            INSERT INTO quotes (
                minute_of_day, time_24h, time_text, quote, title, author, sfw, language,
                source_name, source_url, source_license, source_record_id, quote_hash,
                normalized_quote_hash, highlight_start, highlight_end, quality_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'en', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                minute_of_day,
                canonical_time,
                time_text,
                quote,
                title,
                author,
                sfw_db,
                spec.name,
                spec.url,
                spec.license,
                raw.source_record_id,
                exact_hash,
                normalized_hash,
                highlight.start,
                highlight.end,
                highlight.status.value,
                created_at,
            ),
        )
        quote_id = int(cursor.lastrowid)
        duplicate_kind = DuplicateKind.CANONICAL
    else:
        quote_id = int(existing["id"])
        exact_fields_match = (
            existing["quote"] == quote
            and existing["time_text"] == time_text
            and existing["title"] == title
            and existing["author"] == author
            and existing["sfw"] == sfw_db
        )
        duplicate_kind = (
            DuplicateKind.EXACT if exact_fields_match else DuplicateKind.TRIVIAL_VARIANT
        )
        merged_sfw = _merge_sfw(existing["sfw"], raw.sfw)
        if quality_rank(highlight.status) > quality_rank(existing["quality_status"]):
            connection.execute(
                """
                UPDATE quotes SET
                    time_24h = ?, time_text = ?, quote = ?, title = ?, author = ?, sfw = ?,
                    source_name = ?, source_url = ?, source_license = ?, source_record_id = ?,
                    quote_hash = ?, highlight_start = ?, highlight_end = ?, quality_status = ?
                WHERE id = ?
                """,
                (
                    canonical_time,
                    time_text,
                    quote,
                    title,
                    author,
                    merged_sfw,
                    spec.name,
                    spec.url,
                    spec.license,
                    raw.source_record_id,
                    exact_hash,
                    highlight.start,
                    highlight.end,
                    highlight.status.value,
                    quote_id,
                ),
            )
        elif merged_sfw != existing["sfw"]:
            connection.execute("UPDATE quotes SET sfw = ? WHERE id = ?", (merged_sfw, quote_id))

    connection.execute(
        """
        INSERT INTO quote_provenance (
            quote_id, source_id, import_run_id, source_record_id, raw_time_24h, raw_time_text,
            raw_quote, raw_title, raw_author, raw_sfw, raw_quote_hash, validation_status,
            highlight_start, highlight_end, duplicate_kind, raw_payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            quote_id,
            source_id,
            run_id,
            raw.source_record_id,
            raw.time_24h,
            raw.time_text,
            raw.quote,
            raw.title,
            raw.author,
            sfw_db,
            text_hash(raw.quote),
            highlight.status.value,
            highlight.start,
            highlight.end,
            duplicate_kind.value,
            _json(raw.raw_payload),
        ),
    )
    return duplicate_kind


def _build(connection: sqlite3.Connection, specs: list[SourceSpec]) -> ImportSummary:
    started_at = _now()
    cursor = connection.execute(
        "INSERT INTO import_runs (started_at, status) VALUES (?, 'RUNNING')", (started_at,)
    )
    run_id = int(cursor.lastrowid)
    raw_count = canonical = exact = trivial = malformed = invalid = 0

    for spec in specs:
        if not spec.corpus_path.is_file():
            raise FileNotFoundError(
                f"missing corpus file {spec.corpus_path}; run `uv run litclock fetch` first"
            )
        actual_sha256 = _file_sha256(spec.corpus_path)
        if spec.corpus_sha256 and actual_sha256 != spec.corpus_sha256:
            raise ValueError(
                f"checksum mismatch for {spec.corpus_path}: "
                f"expected {spec.corpus_sha256}, got {actual_sha256}"
            )
        imported_at = _now()
        source_cursor = connection.execute(
            """
            INSERT INTO sources (
                name, slug, source_url, source_license, upstream_commit, corpus_path,
                corpus_sha256, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                spec.name,
                spec.slug,
                spec.url,
                spec.license,
                spec.commit,
                str(spec.corpus_path),
                actual_sha256,
                imported_at,
            ),
        )
        source_id = int(source_cursor.lastrowid)
        source_records = 0
        for record in read_source(spec):
            raw_count += 1
            source_records += 1
            if isinstance(record, MalformedRecord):
                malformed += 1
                _insert_issue(
                    connection,
                    source_id=source_id,
                    run_id=run_id,
                    record_id=record.source_record_id,
                    status=QualityStatus.MALFORMED,
                    error=record.error,
                    payload=record.raw_payload,
                )
                continue
            outcome = _import_quote(
                connection,
                raw=record,
                spec=spec,
                source_id=source_id,
                run_id=run_id,
                created_at=imported_at,
            )
            if outcome == DuplicateKind.CANONICAL:
                canonical += 1
            elif outcome == DuplicateKind.EXACT:
                exact += 1
            elif outcome == DuplicateKind.TRIVIAL_VARIANT:
                trivial += 1
            elif outcome == QualityStatus.INVALID_TIME:
                invalid += 1
            elif outcome == QualityStatus.MALFORMED:
                malformed += 1
        connection.execute(
            "UPDATE sources SET record_count = ? WHERE id = ?", (source_records, source_id)
        )

    finished_at = _now()
    connection.execute(
        """
        UPDATE import_runs SET
            finished_at = ?, status = 'COMPLETE', raw_record_count = ?,
            canonical_inserted = ?, exact_duplicates = ?, trivial_variants = ?,
            malformed_records = ?, invalid_times = ?
        WHERE id = ?
        """,
        (finished_at, raw_count, canonical, exact, trivial, malformed, invalid, run_id),
    )
    connection.commit()
    return ImportSummary(raw_count, canonical, exact, trivial, malformed, invalid)


def import_corpora(database_path: Path, specs: list[SourceSpec]) -> ImportSummary:
    """Build a fresh database beside the destination and atomically replace it."""
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{database_path.name}.", suffix=".tmp", dir=database_path.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    connection: sqlite3.Connection | None = None
    try:
        connection = connect_database(temporary_path)
        initialize_database(connection)
        summary = _build(connection, specs)
        connection.close()
        connection = None
        os.replace(temporary_path, database_path)
        return summary
    except Exception:
        if connection is not None:
            connection.close()
        temporary_path.unlink(missing_ok=True)
        raise
