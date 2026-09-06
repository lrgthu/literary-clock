from __future__ import annotations

import bz2
import html
from datetime import UTC, datetime
from pathlib import Path

from conftest import empty_database, insert_quote

from litclock.phase2d import start_phase2d
from litclock.selector import QuoteSelector
from litclock.wikisource import (
    iter_wikimedia_pages,
    multistream_ranges,
    namespace_disposition,
    pages_from_multistream_member,
    parse_sha1sums,
)
from litclock.wikisource_mining import mine_wikisource_dump, raw_target_time_prefilter
from litclock.wikisource_text import (
    clean_wikitext,
    extract_license_evidence,
    extract_work_metadata,
    proofread_quality,
)


def _page(page_id: int, namespace: int, title: str, wikitext: str) -> str:
    return f"""
    <page>
      <title>{html.escape(title)}</title><ns>{namespace}</ns><id>{page_id}</id>
      <revision><id>{page_id + 1000}</id><timestamp>2026-09-01T00:00:00Z</timestamp>
      <text xml:space="preserve">{html.escape(wikitext)}</text></revision>
    </page>
    """


def _dump(path: Path, pages: list[str]) -> Path:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">'
        + "".join(pages)
        + "</mediawiki>"
    )
    path.write_bytes(bz2.compress(xml.encode()))
    return path


def _prepare_run(connection, tmp_path: Path, dump_path: Path) -> tuple[int, int]:
    phase = start_phase2d(connection, tmp_path)
    connection.execute(
        "UPDATE phase2d_runs SET status = 'RECOVERY_COMPLETE' WHERE id = ?",
        (phase["run_id"],),
    )
    cursor = connection.execute(
        """
        INSERT INTO wikisource_dumps (
            filename, dump_date, source_url, checksum_algorithm, expected_checksum,
            actual_checksum, byte_size, local_path, index_filename, index_source_url,
            index_expected_checksum, index_actual_checksum, index_local_path,
            acquisition_timestamp, status
        ) VALUES ('test.xml.bz2', '20260901', 'https://dumps.wikimedia.org/test',
                  'SHA1', 'abc', 'abc', ?, ?, 'test-index.bz2',
                  'https://dumps.wikimedia.org/test-index', 'def', 'def', ?, ?, 'ACQUIRED')
        """,
        (
            dump_path.stat().st_size,
            str(dump_path),
            str(tmp_path / "index.bz2"),
            datetime.now(UTC).isoformat(),
        ),
    )
    connection.commit()
    return int(phase["run_id"]), int(cursor.lastrowid)


def test_wikisource_xml_dump_streaming_and_namespace_filtering(tmp_path: Path) -> None:
    path = _dump(
        tmp_path / "sample.xml.bz2",
        [
            _page(1, 0, "A Novel", "Literary text"),
            _page(2, 1, "Talk:A Novel", "Discussion"),
            _page(3, 104, "Page:A Novel.djvu/1", "Transcription"),
        ],
    )
    pages = list(iter_wikimedia_pages(path))
    assert [(page.page_id, page.namespace, page.title) for page in pages] == [
        (1, 0, "A Novel"),
        (2, 1, "Talk:A Novel"),
        (3, 104, "Page:A Novel.djvu/1"),
    ]
    assert namespace_disposition(0, "A Novel") == "MAINSPACE"
    assert namespace_disposition(104, "Page:A Novel.djvu/1") == "PAGE_TRANSCRIPTION"
    assert namespace_disposition(1, "Talk:A Novel") == "EXCLUDED_NAMESPACE"


def test_wikitext_cleanup_preserves_highlightable_words() -> None:
    wikitext = (
        '<noinclude><pagequality level="4" user="proofreader"/>{{rh|12|CHAPTER}}</noinclude>'
        "At {{sc|three}} forty-six, she [[opened|opened]] the door.<ref>editor note</ref>"
        "\n\n{{PD-old}}"
    )
    paragraphs = clean_wikitext(wikitext)
    assert paragraphs == ["At three forty-six, she opened the door."]
    assert proofread_quality(wikitext) == 4
    start = paragraphs[0].index("three forty-six")
    assert paragraphs[0][start : start + len("three forty-six")] == "three forty-six"


def test_wikitext_cleanup_handles_abbreviated_text_templates() -> None:
    assert clean_wikitext("The {{corr|clock}} struck {{sic|three}} forty-six.") == [
        "The clock struck three forty-six."
    ]


def test_wikisource_license_evidence_is_not_assumed() -> None:
    assert extract_license_evidence("{{PD-US-expired}}") == (
        "Public domain in the United States",
        "English Wikisource template {{pd-us-expired}}",
        "PUBLIC_DOMAIN",
    )
    assert extract_license_evidence("{{header|title=Recent|author=A|year=2000}}") == (
        None,
        None,
        "UNKNOWN",
    )
    assert extract_license_evidence("{{header|title=Old|author=A|year=1900}}") == (
        "Public domain in the United States",
        "work/edition publication year 1900 is at or before 1930",
        "PUBLIC_DOMAIN",
    )


def test_relative_header_title_does_not_hide_inherited_work_classification() -> None:
    metadata = extract_work_metadata("{{header|title=../../|author=Thomas Aquinas|year=1900}}")
    assert metadata.title == ""


def test_sha1_manifest_parsing() -> None:
    assert parse_sha1sums("a" * 40 + "  file.xml.bz2\ninvalid\n") == {"file.xml.bz2": "a" * 40}


def test_multistream_ranges_and_member_parsing(tmp_path: Path) -> None:
    first = _page(1, 0, "First", "At 3:46 the door opened.").encode()
    second = _page(2, 0, "Second", "At 4:19 the bell rang.").encode()
    first_member = bz2.compress(first)
    second_member = bz2.compress(second + b"</mediawiki>")
    archive = tmp_path / "multi.xml.bz2"
    archive.write_bytes(first_member + second_member)
    index = tmp_path / "multi-index.txt.bz2"
    index.write_bytes(bz2.compress(f"0:1:First\n{len(first_member)}:2:Second\n".encode()))
    ranges = multistream_ranges(index, archive.stat().st_size)
    assert ranges == [(0, len(first_member)), (len(first_member), archive.stat().st_size)]
    assert pages_from_multistream_member(bz2.decompress(first_member))[0].title == "First"


def test_raw_target_prefilter_uses_shared_clockface_semantics() -> None:
    targets = frozenset({3 * 60 + 46, 15 * 60 + 46})
    assert raw_target_time_prefilter("At 3:46 the door opened.", targets)
    assert raw_target_time_prefilter("At three forty-six the door opened.", targets)
    assert not raw_target_time_prefilter("At 4:19 the door opened.", targets)


def test_mainspace_preferred_and_page_transcription_duplicate_rejected(tmp_path: Path) -> None:
    sentence = (
        "At 3:46 the traveler entered the silent station and carefully closed the heavy door."
    )
    root = "{{header|title=A Novel|author=Jane Writer|year=1900}}{{PD-old}}"
    page_text = f'<noinclude><pagequality level="4"/></noinclude>{sentence}'
    dump_path = _dump(
        tmp_path / "sample.xml.bz2",
        [
            _page(1, 0, "A Novel", root),
            _page(2, 0, "A Novel/Chapter 1", sentence),
            _page(
                3, 106, "Index:A Novel.djvu", "{{Index|Title=A Novel|Author=Jane Writer|Year=1900}}"
            ),
            _page(4, 104, "Page:A Novel.djvu/1", page_text),
        ],
    )
    connection = empty_database(tmp_path / "data" / "generated" / "corpus.sqlite3")
    phase_id, dump_id = _prepare_run(connection, tmp_path, dump_path)
    result = mine_wikisource_dump(connection, tmp_path, phase_id, dump_id)
    assert result["imported_quotes"] == 1
    assert result["relationships_added"] == 2
    assert connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM quote_minute_eligibility").fetchone()[0] == 2
    statuses = dict(
        connection.execute(
            "SELECT namespace, review_status FROM wikisource_candidates ORDER BY namespace"
        ).fetchall()
    )
    assert statuses == {0: "IMPORTED", 104: "REJECTED_DUPLICATE"}

    quote_id = connection.execute("SELECT id FROM quotes").fetchone()[0]
    selector = QuoteSelector(
        connection,
        now_provider=lambda: datetime(2026, 9, 1, 3, 46, tzinfo=UTC),
    )
    assert selector.select("03:46").id == quote_id
    selector.now_provider = lambda: datetime(2026, 9, 1, 15, 46, tzinfo=UTC)
    assert selector.select("15:46").id == quote_id
    restarted = mine_wikisource_dump(connection, tmp_path, phase_id, dump_id)
    assert restarted["imported_quotes"] == 1
    assert connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 1
    connection.close()


def test_unknown_license_is_quarantined_and_not_imported(tmp_path: Path) -> None:
    sentence = (
        "At 4:19 the traveler entered the silent station, carefully closed the heavy door, "
        "and settled beside the fire."
    )
    dump_path = _dump(
        tmp_path / "sample.xml.bz2",
        [
            _page(
                1, 0, "Recent Novel", "{{header|title=Recent Novel|author=Jane Writer|year=2000}}"
            ),
            _page(2, 0, "Recent Novel/Chapter 1", sentence),
        ],
    )
    connection = empty_database(tmp_path / "data" / "generated" / "corpus.sqlite3")
    phase_id, dump_id = _prepare_run(connection, tmp_path, dump_path)
    result = mine_wikisource_dump(connection, tmp_path, phase_id, dump_id)
    assert result["imported_quotes"] == 0
    row = connection.execute("SELECT * FROM wikisource_candidates").fetchone()
    assert row["review_status"] == "REVIEW_LICENSE"
    assert row["imported_quote_id"] is None
    connection.close()


def test_wikisource_cross_source_duplicate_is_rejected(tmp_path: Path) -> None:
    sentence = (
        "At 3:46 the traveler entered the silent station and carefully closed the heavy door."
    )
    dump_path = _dump(
        tmp_path / "sample.xml.bz2",
        [
            _page(
                1,
                0,
                "A Novel",
                "{{header|title=A Novel|author=Jane Writer|year=1900}}{{PD-old}}",
            ),
            _page(2, 0, "A Novel/Chapter 1", sentence),
        ],
    )
    connection = empty_database(tmp_path / "data" / "generated" / "corpus.sqlite3")
    insert_quote(
        connection,
        minute=3 * 60 + 46,
        time_24h="03:46",
        time_text="3:46",
        quote=sentence,
        title="Existing Edition",
        author="Jane Writer",
    )
    phase_id, dump_id = _prepare_run(connection, tmp_path, dump_path)
    result = mine_wikisource_dump(connection, tmp_path, phase_id, dump_id)
    assert result["imported_quotes"] == 0
    candidate = connection.execute("SELECT * FROM wikisource_candidates").fetchone()
    assert candidate["review_status"] == "REJECTED_DUPLICATE"
    assert connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 1
    connection.close()


def test_target_deactivates_at_three_and_fourth_quote_is_not_imported(tmp_path: Path) -> None:
    root = "{{header|title=Clock Stories|author=Jane Writer|year=1900}}{{PD-old}}"
    chapters = [
        _page(
            number + 1,
            0,
            f"Clock Stories/Chapter {number}",
            f"At 4:19 traveler {number} entered the silent station, carefully closed the "
            f"heavy door, and began a distinct adventure number {number}.",
        )
        for number in range(1, 5)
    ]
    dump_path = _dump(tmp_path / "sample.xml.bz2", [_page(1, 0, "Clock Stories", root), *chapters])
    connection = empty_database(tmp_path / "data" / "generated" / "corpus.sqlite3")
    phase_id, dump_id = _prepare_run(connection, tmp_path, dump_path)
    result = mine_wikisource_dump(connection, tmp_path, phase_id, dump_id)
    assert result["imported_quotes"] == 3
    assert result["relationships_added"] == 6
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM quote_minute_eligibility WHERE minute_of_day = 259"
        ).fetchone()[0]
        == 3
    )
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM quote_minute_eligibility WHERE minute_of_day = 979"
        ).fetchone()[0]
        == 3
    )
    assert connection.execute("SELECT COUNT(*) FROM wikisource_candidates").fetchone()[0] == 3
    connection.close()


def test_theological_reference_is_not_imported_as_literary_time(tmp_path: Path) -> None:
    root = "{{header|title=Summa Theologica|author=Thomas Aquinas|year=1900}}{{PD-old}}"
    sentence = (
        'The question appears in Matt. 5:47): "Love your enemies," and the commentator '
        "continues at considerable length with a theological argument."
    )
    dump_path = _dump(
        tmp_path / "sample.xml.bz2",
        [_page(1, 0, "Summa Theologica", root), _page(2, 0, "Summa Theologica/Part 1", sentence)],
    )
    connection = empty_database(tmp_path / "data" / "generated" / "corpus.sqlite3")
    phase_id, dump_id = _prepare_run(connection, tmp_path, dump_path)
    result = mine_wikisource_dump(connection, tmp_path, phase_id, dump_id)
    assert result["imported_quotes"] == 0
    assert connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 0
    connection.close()
