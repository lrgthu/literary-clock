from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from litclock.db import connect_database, initialize_database
from litclock.models import SourceSpec
from litclock.normalize import normalized_quote_hash, text_hash


def source_spec(path: Path, *, name: str = "test/source", slug: str = "test-source") -> SourceSpec:
    return SourceSpec(
        name=name,
        slug=slug,
        url=f"https://example.test/{slug}",
        license="TEST ONLY",
        commit="test-commit",
        corpus_path=path,
        corpus_sha256="",
        format="pipe_csv",
    )


def empty_database(path: Path) -> sqlite3.Connection:
    connection = connect_database(path)
    initialize_database(connection)
    return connection


def insert_quote(
    connection: sqlite3.Connection,
    *,
    minute: int = 997,
    time_24h: str = "16:37",
    time_text: str = "4:37",
    quote: str,
    title: str,
    author: str,
    sfw: bool | None = True,
) -> int:
    start = quote.index(time_text)
    cursor = connection.execute(
        """
        INSERT INTO quotes (
            minute_of_day, time_24h, time_text, quote, title, author, sfw, language,
            source_name, source_url, source_license, source_record_id, quote_hash,
            normalized_quote_hash, highlight_start, highlight_end, quality_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'en', 'test', 'https://example.test', 'TEST', NULL,
                  ?, ?, ?, ?, 'VERIFIED_EXACT', ?)
        """,
        (
            minute,
            time_24h,
            time_text,
            quote,
            title,
            author,
            None if sfw is None else int(sfw),
            text_hash(quote),
            normalized_quote_hash(quote),
            start,
            start + len(time_text),
            datetime.now(UTC).isoformat(),
        ),
    )
    connection.commit()
    return int(cursor.lastrowid)
