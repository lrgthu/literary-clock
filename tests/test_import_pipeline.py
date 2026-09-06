from __future__ import annotations

import csv
from pathlib import Path

from conftest import source_spec

from litclock.db import connect_database
from litclock.importers.pipeline import import_corpora
from litclock.models import SourceSpec


def _write_rows(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle, delimiter="|", lineterminator="\n").writerows(rows)


def test_dedup_merges_provenance_but_preserves_cross_minute_passages(tmp_path: Path) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    base_quote = "At six o'clock the train arrived."
    _write_rows(
        first,
        [
            ["06:00", "six o'clock", base_quote, "The Book", "A. Writer"],
            ["18:00", "six o'clock", base_quote, "The Book", "A. Writer"],
        ],
    )
    _write_rows(
        second,
        [
            ["06:00", "six o'clock", base_quote, "The Book", "A. Writer"],
            ["06:00", "six o’clock", "At six o’clock the train arrived!", "the book", "A Writer"],
        ],
    )
    specs = [
        source_spec(first, name="one", slug="one"),
        source_spec(second, name="two", slug="two"),
    ]
    database = tmp_path / "corpus.sqlite3"
    summary = import_corpora(database, specs)

    assert summary.raw_records == 4
    assert summary.canonical_quotes == 2
    assert summary.exact_duplicates == 1
    assert summary.trivial_variants == 1
    connection = connect_database(database)
    try:
        assert connection.execute("SELECT COUNT(*) FROM quote_provenance").fetchone()[0] == 4
        rows = list(connection.execute("SELECT minute_of_day FROM quotes ORDER BY minute_of_day"))
        assert [row[0] for row in rows] == [360, 1080]
        kinds = {
            row["duplicate_kind"]: row["n"]
            for row in connection.execute(
                "SELECT duplicate_kind, COUNT(*) AS n FROM quote_provenance GROUP BY duplicate_kind"
            )
        }
        assert kinds == {"CANONICAL": 2, "EXACT": 1, "TRIVIAL_VARIANT": 1}
    finally:
        connection.close()


def test_better_upstream_highlight_replaces_canonical_representation(tmp_path: Path) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    _write_rows(first, [["09:00", "nine", "At 9:00 we left.", "Book", "Author"]])
    _write_rows(second, [["09:00", "9:00", "At 9:00 we left!", "Book", "Author"]])
    database = tmp_path / "corpus.sqlite3"
    import_corpora(
        database,
        [source_spec(first, name="one", slug="one"), source_spec(second, name="two", slug="two")],
    )
    connection = connect_database(database)
    try:
        row = connection.execute("SELECT * FROM quotes").fetchone()
        assert row["quality_status"] == "VERIFIED_EXACT"
        assert row["source_name"] == "two"
        assert row["quote"][row["highlight_start"] : row["highlight_end"]] == "9:00"
    finally:
        connection.close()


def test_malformed_and_invalid_records_are_quarantined(tmp_path: Path) -> None:
    source = tmp_path / "bad.csv"
    _write_rows(
        source,
        [
            ["12:00", "noon", "too few fields"],
            ["24:00", "midnight", "At midnight.", "Book", "Author"],
            ["12:00", "noon", "", "Book", "Author"],
        ],
    )
    database = tmp_path / "corpus.sqlite3"
    summary = import_corpora(database, [source_spec(source)])
    assert summary.raw_records == 3
    assert summary.canonical_quotes == 0
    assert summary.invalid_times == 1
    assert summary.malformed_records == 2
    connection = connect_database(database)
    try:
        statuses = [
            row[0]
            for row in connection.execute("SELECT quality_status FROM import_issues ORDER BY id")
        ]
        assert statuses == ["MALFORMED", "INVALID_TIME", "MALFORMED"]
    finally:
        connection.close()


def test_unmatched_prose_quote_does_not_consume_following_pipe_record(tmp_path: Path) -> None:
    source = tmp_path / "unmatched.csv"
    source.write_text(
        '13:45|1:45|"He checked at 1:45.”|First Book|First Author|sfw\n'
        "13:46|1:46|She checked at 1:46.|Second Book|Second Author|sfw\n",
        encoding="utf-8",
    )
    database = tmp_path / "corpus.sqlite3"
    summary = import_corpora(database, [source_spec(source)])
    assert summary.raw_records == 2
    assert summary.canonical_quotes == 2
    connection = connect_database(database)
    try:
        rows = list(connection.execute("SELECT quote, title, author FROM quotes ORDER BY id"))
        assert [row["title"] for row in rows] == ["First Book", "Second Book"]
        assert all("|" not in row["quote"] for row in rows)
    finally:
        connection.close()


def test_checksum_mismatch_aborts_without_overwriting_existing_database(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    _write_rows(source, [["12:00", "noon", "At noon.", "Book", "Author"]])
    database = tmp_path / "corpus.sqlite3"
    database.write_bytes(b"existing database sentinel")
    base = source_spec(source)
    bad = SourceSpec(
        name=base.name,
        slug=base.slug,
        url=base.url,
        license=base.license,
        commit=base.commit,
        corpus_path=base.corpus_path,
        corpus_sha256="0" * 64,
        format=base.format,
    )
    try:
        import_corpora(database, [bad])
    except ValueError as error:
        assert "checksum mismatch" in str(error)
    else:
        raise AssertionError("checksum mismatch unexpectedly succeeded")
    assert database.read_bytes() == b"existing database sentinel"
