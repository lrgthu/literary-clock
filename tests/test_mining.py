from __future__ import annotations

from pathlib import Path

from conftest import empty_database, insert_quote

from litclock.db import connect_database
from litclock.mining import (
    PassageDuplicateIndex,
    _coverage_state,
    import_high_confidence,
    mine_standard_ebooks,
    target_priority,
)
from litclock.normalize import normalized_quote_hash, text_hash
from litclock.standard_ebooks import AcquiredBook, BookMetadata, CatalogRepository
from litclock.stats import calculate_stats


def test_duplicate_index_finds_exact_normalized_and_context_variants(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "quotes.sqlite3")
    quote_id = insert_quote(
        connection,
        time_24h="04:37",
        quote="At 4:37, he left the silent house without looking back.",
        title="Book",
        author="Author",
    )
    index = PassageDuplicateIndex(connection)
    assert (
        index.check("At 4:37, he left the silent house without looking back.", 997).status
        == "EXACT_LEGACY"
    )
    normalized = index.check("At 4:37 he left the silent house without looking back!", 997)
    assert normalized.status == "NORMALIZED_LEGACY"
    extended = index.check(
        "She watched from above. At 4:37, he left the silent house without looking back. "
        "The street was empty.",
        997,
    )
    assert extended.status == "CONTEXT_LEGACY"
    assert extended.quote_id == quote_id
    assert index.check("At 04:38, an unrelated train arrived at the station.", 998).status == "NEW"
    assert (
        index.check("At 4:37, he left the silent house without looking back.", 998).status == "NEW"
    )
    connection.close()


def test_sparse_priority_updates_and_rewards_diversity(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "quotes.sqlite3")
    empty_earlier = target_priority(100, "New Author", "New Book", _coverage_state(connection))
    insert_quote(
        connection,
        minute=100,
        time_24h="01:40",
        time_text="1:40",
        quote="At 1:40, the first event occurred in the hall.",
        title="Existing Book",
        author="Existing Author",
    )
    coverage = _coverage_state(connection)
    same = target_priority(100, "Existing Author", "Existing Book", coverage)
    diverse = target_priority(100, "New Author", "New Book", coverage)
    assert empty_earlier > diverse > same
    connection.close()


def _fake_book(root: Path, repository: CatalogRepository, text: str) -> AcquiredBook:
    book_root = root / repository.name
    text_dir = book_root / "src" / "epub" / "text"
    text_dir.mkdir(parents=True)
    xhtml = text_dir / "chapter-1.xhtml"
    xhtml.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
        <html xmlns="http://www.w3.org/1999/xhtml"
              xmlns:epub="http://www.idpf.org/2007/ops">
          <body epub:type="bodymatter z3998:fiction"><article id="chapter-1">
            <p>"""
        + text
        + """</p>
          </article></body>
        </html>""",
        encoding="utf-8",
    )
    return AcquiredBook(
        repository=repository,
        root=book_root,
        commit_sha=(repository.name.encode().hex() + "0" * 40)[:40],
        content_checksum=(repository.name.encode().hex() + "0" * 64)[:64],
        acquisition_timestamp="2026-09-04T00:00:00+00:00",
        metadata=BookMetadata(
            title=repository.name,
            author=repository.name.split("_", 1)[0],
            language="en-US",
            rights="Public domain test fixture",
            genres=("Fiction",),
            metadata_word_count=None,
        ),
        text_files=(xhtml,),
    )


def test_mining_resume_skips_completed_books(monkeypatch, tmp_path: Path) -> None:
    repositories = [
        CatalogRepository(
            name=f"author-{number}_book-{number}",
            source_url=f"https://example.test/book-{number}",
            clone_url=f"https://example.test/book-{number}.git",
            default_branch="main",
            description="Epub source for the Standard Ebooks edition of Test",
            github_updated_at="2026-09-04T00:00:00Z",
        )
        for number in range(2)
    ]
    books = {
        repository.name: _fake_book(
            tmp_path / "fixtures",
            repository,
            f"At 04:3{number}, the traveler entered a quiet room and closed "
            "the heavy door behind him.",
        )
        for number, repository in enumerate(repositories)
    }
    acquired: list[str] = []

    monkeypatch.setattr("litclock.mining.fetch_catalog", lambda *args, **kwargs: repositories)

    def acquire(repository, *_args, **_kwargs):
        acquired.append(repository.name)
        return books[repository.name]

    monkeypatch.setattr("litclock.mining.acquire_repository", acquire)
    database = tmp_path / "corpus.sqlite3"
    connection = connect_database(database)
    first = mine_standard_ebooks(connection, tmp_path, limit_books=1, workers=1)
    assert first["books_scanned"] == 1
    assert first["expressions_detected"] == 1
    second = mine_standard_ebooks(connection, tmp_path, limit_books=1, workers=1)
    assert second["books_scanned"] == 2
    assert second["expressions_detected"] == 2
    assert len(acquired) == 2
    assert len(set(acquired)) == 2
    connection.close()


def test_reprocess_uses_the_existing_local_index(monkeypatch, tmp_path: Path) -> None:
    repositories = [
        CatalogRepository(
            name=f"author-{number}_book-{number}",
            source_url=f"https://example.test/book-{number}",
            clone_url=f"https://example.test/book-{number}.git",
            default_branch="main",
            description="Epub source for the Standard Ebooks edition of Test",
            github_updated_at="2026-09-04T00:00:00Z",
        )
        for number in range(3)
    ]
    books = {
        repository.name: _fake_book(
            tmp_path / "fixtures",
            repository,
            f"At 04:3{number}, the traveler entered the room and closed the door quietly.",
        )
        for number, repository in enumerate(repositories)
    }
    monkeypatch.setattr("litclock.mining.fetch_catalog", lambda *args, **kwargs: repositories)
    monkeypatch.setattr(
        "litclock.mining.acquire_repository", lambda repository, *_args: books[repository.name]
    )
    connection = connect_database(tmp_path / "corpus.sqlite3")
    mine_standard_ebooks(connection, tmp_path, limit_books=1, workers=1)
    indexed = connection.execute("SELECT repository FROM standard_ebooks_books").fetchone()[0]

    mine_standard_ebooks(connection, tmp_path, limit_books=3, workers=1, reprocess=True)

    assert [
        row[0] for row in connection.execute("SELECT repository FROM standard_ebooks_books")
    ] == [indexed]
    assert connection.execute("SELECT COUNT(*) FROM mined_candidates").fetchone()[0] == 1
    connection.close()


def test_high_confidence_import_stops_at_target_and_preserves_provenance(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "corpus.sqlite3")
    now = "2026-09-04T00:00:00+00:00"
    book_id = int(
        connection.execute(
            """
            INSERT INTO standard_ebooks_books (
                repository, source_url, default_branch, commit_sha, author, title,
                language, rights, processing_status, content_checksum, created_at, updated_at
            ) VALUES ('author_book', 'https://example.test/book', 'main', 'abc123',
                      'Author', 'Book', 'en-US', 'Public domain', 'PROCESSED',
                      'checksum', ?, ?)
            """,
            (now, now),
        ).lastrowid
    )
    alternate_book_id = int(
        connection.execute(
            """
            INSERT INTO standard_ebooks_books (
                repository, source_url, default_branch, commit_sha, author, title,
                language, rights, processing_status, content_checksum, created_at, updated_at
            ) VALUES ('other_book', 'https://example.test/other', 'main', 'def456',
                      'Other Author', 'Other Book', 'en-US', 'Public domain', 'PROCESSED',
                      'other-checksum', ?, ?)
            """,
            (now, now),
        ).lastrowid
    )
    for number in range(8):
        quote = f"At 04:37, event number {number} occurred beside the quiet country station."
        alternate = number == 7
        connection.execute(
            """
            INSERT INTO mined_candidates (
                book_id, minute_of_day, time_24h, time_text, quote, author, title,
                source_repository, source_url, source_commit, source_file, source_section,
                source_locator, source_paragraph_index, source_quote_start, source_quote_end,
                source_expression_start, source_expression_end, highlight_start, highlight_end,
                parser_rule, time_confidence, ampm_evidence, context_score,
                literary_quality_score, duplicate_status, target_priority, review_status,
                candidate_hash, normalized_quote_hash, created_at
            ) VALUES (?, 277, '04:37', '04:37', ?, ?, ?, ?, ?, ?,
                      'chapter.xhtml', 'chapter', ?,
                      ?, 0, ?, 3, 8, 3, 8, 'numeric', 'EXACT_24H', '24-hour clock',
                      90, 90, 'NEW', 1000, 'HIGH_CONFIDENCE', ?, ?, ?)
            """,
            (
                alternate_book_id if alternate else book_id,
                quote,
                "Other Author" if alternate else "Author",
                "Other Book" if alternate else "Book",
                "other_book" if alternate else "author_book",
                "https://example.test/other" if alternate else "https://example.test/book",
                "def456" if alternate else "abc123",
                f"chapter.xhtml#p{number}:3-8",
                number,
                len(quote),
                text_hash(quote),
                normalized_quote_hash(quote),
                now,
            ),
        )
    connection.commit()

    assert import_high_confidence(connection, target_per_minute=7) == 7
    assert connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 7
    assert connection.execute("SELECT COUNT(*) FROM quote_provenance").fetchone()[0] == 7
    assert connection.execute("SELECT SUM(record_count) FROM sources").fetchone()[0] == 7
    assert connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 2
    assert (
        connection.execute(
            "SELECT review_status FROM mined_candidates WHERE source_repository = 'other_book'"
        ).fetchone()[0]
        == "IMPORTED"
    )
    statuses = dict(
        connection.execute(
            "SELECT review_status, COUNT(*) FROM mined_candidates GROUP BY review_status"
        )
    )
    assert statuses == {"DEFERRED_DENSE": 1, "IMPORTED": 7}
    assert calculate_stats(connection)["total_raw_records"] == 7
    connection.close()
