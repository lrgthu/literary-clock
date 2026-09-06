from __future__ import annotations

import bz2
import gzip
import io
import json
import tarfile
from pathlib import Path

import pytest
from conftest import empty_database, insert_quote

from litclock.gutenberg import (
    GutenbergMetadata,
    acquire_texts,
    iter_catalog_csv,
    iter_rdf_rights,
    language_is_english,
    literature_score,
    parse_rdf_rights,
    rights_are_eligible,
    sha256_file,
)
from litclock.gutenberg_mining import (
    build_target_expression_set,
    import_gutenberg,
    revalidate_gutenberg_candidates,
    target_expression_variants,
    validate_import_integrity,
    write_phase2b_report,
)
from litclock.gutenberg_text import (
    extract_gutenberg_paragraphs,
    strip_gutenberg_boilerplate,
    text_quality_rejection,
)
from litclock.mining import PassageDuplicateIndex
from litclock.models import TimeConfidence
from litclock.normalize import normalized_quote_hash
from litclock.timeparse import detect_time_expressions


def _metadata(**overrides) -> GutenbergMetadata:
    values = {
        "ebook_id": 123,
        "pg_type": "Text",
        "issued": "2000-01-01",
        "title": "A Test Novel",
        "language": "en",
        "authors": "Writer, Ada",
        "subjects": "Fiction; Adventure stories",
        "locc": "PR",
        "bookshelves": "Adventure",
        "rights": "Public domain in the USA.",
    }
    values.update(overrides)
    return GutenbergMetadata(**values)


def test_csv_catalog_parsing(tmp_path: Path) -> None:
    path = tmp_path / "catalog.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        handle.write(
            "Text#,Type,Issued,Title,Language,Authors,Subjects,LoCC,Bookshelves\n"
            '123,Text,2000-01-01,"A Tale, Complete",en,"Writer, Ada",Fiction,PR,Adventure\n'
        )
    rows = list(iter_catalog_csv(path))
    assert len(rows) == 1
    assert rows[0].ebook_id == 123
    assert rows[0].title == "A Tale, Complete"
    assert rows[0].authors == "Writer, Ada"


def _rdf(identifier: int, rights: str) -> bytes:
    return f"""<?xml version="1.0"?>
    <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
             xmlns:pgterms="http://www.gutenberg.org/2009/pgterms/"
             xmlns:dcterms="http://purl.org/dc/terms/">
      <pgterms:ebook rdf:about="ebooks/{identifier}">
        <dcterms:rights>{rights}</dcterms:rights>
      </pgterms:ebook>
    </rdf:RDF>""".encode()


def test_rdf_rights_parsing_and_streamed_archive(tmp_path: Path) -> None:
    assert parse_rdf_rights(_rdf(123, "Public domain in the USA.")) == (
        123,
        "Public domain in the USA.",
    )
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as archive:
        for identifier in (123, 456):
            payload = _rdf(identifier, "Public domain in the USA.")
            info = tarfile.TarInfo(f"cache/epub/{identifier}/pg{identifier}.rdf")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    archive_path = tmp_path / "rdf.tar.bz2"
    archive_path.write_bytes(bz2.compress(tar_buffer.getvalue()))
    assert list(iter_rdf_rights(archive_path)) == [
        (123, "Public domain in the USA."),
        (456, "Public domain in the USA."),
    ]


def test_rights_language_and_literature_filters_are_conservative() -> None:
    assert rights_are_eligible("Public domain in the USA.")[0]
    assert not rights_are_eligible("Copyrighted. Project Gutenberg has permission.")[0]
    assert not rights_are_eligible(None)[0]
    assert language_is_english("en")
    assert not language_is_english("en; fr")
    assert literature_score(_metadata())[0] >= 25
    rejected = _metadata(subjects="Theology; Bible commentaries", locc="BS", bookshelves="")
    score, reason = literature_score(rejected)
    assert score < 0
    assert "nonliterary" in reason
    weak_history = _metadata(
        title="A Division History",
        subjects="World War, 1914-1918 -- Regimental histories",
        locc="D501",
        bookshelves="Category: History - Warfare; Nobel Prizes in Literature",
    )
    assert literature_score(weak_history)[0] < 25
    instructional = _metadata(
        title="The Story of Eclipses",
        subjects="Eclipses",
        locc="QB",
        bookshelves="Children's Instructional Books",
    )
    assert literature_score(instructional)[0] < 25
    guidebook = _metadata(
        title="A Road Book",
        subjects="Railroad travel -- Guidebooks",
        locc="HE",
        bookshelves="Category: Travel Writing",
    )
    assert literature_score(guidebook)[0] < 0
    science_for_children = _metadata(
        title="Gilbert Weather Bureau (Meteorology) for Boys",
        subjects="Meteorology -- Juvenile literature; Weather -- Juvenile literature",
        locc="QC",
        bookshelves="Category: Children & Young Adult Reading",
    )
    assert literature_score(science_for_children)[0] < 0
    science_shelf_only = _metadata(
        title="The Romance of Comets",
        subjects="Comets; Meteors",
        locc="QB",
        bookshelves="Category: Mythology, Legends & Folklore; Category: Science - Physics",
    )
    assert literature_score(science_shelf_only)[0] < 0


def test_cached_text_acquisition_resumes_without_rsync(monkeypatch, tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "corpus.sqlite3")
    now = "2026-09-04T00:00:00+00:00"
    connection.execute(
        """
        INSERT INTO gutenberg_books (
            ebook_id, pg_type, title, language, authors, subjects, locc, bookshelves,
            rights, source_url, catalog_sha256, eligibility_status, created_at, updated_at
        ) VALUES (123, 'Text', 'Book', 'en', 'Author', 'Fiction', 'PR', 'Fiction',
                  'Public domain in the USA.', 'https://www.gutenberg.org/ebooks/123',
                  'catalog-sha', 'ELIGIBLE', ?, ?)
        """,
        (now, now),
    )
    path = tmp_path / "books" / "123" / "pg123.txt"
    path.parent.mkdir(parents=True)
    path.write_text("cached text", encoding="utf-8")
    connection.execute(
        """
        UPDATE gutenberg_books SET text_path = 'books/123/pg123.txt', text_sha256 = ?,
            text_cached = 1 WHERE ebook_id = 123
        """,
        (sha256_file(path),),
    )
    connection.commit()
    monkeypatch.setattr(
        "litclock.gutenberg.subprocess.run",
        lambda *args, **kwargs: pytest.fail("rsync should not run for cached files"),
    )
    first = acquire_texts(connection, tmp_path, [123])
    second = acquire_texts(connection, tmp_path, [123])
    assert first == second == {"requested": 1, "acquired": 1, "missing": 0}
    row = connection.execute(
        "SELECT processing_status, text_sha256 FROM gutenberg_books WHERE ebook_id = 123"
    ).fetchone()
    assert row["processing_status"] == "ACQUIRED"
    assert len(row["text_sha256"]) == 64
    connection.close()


def test_untracked_partial_text_is_replaced_on_resume(monkeypatch, tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "corpus.sqlite3")
    now = "2026-09-04T00:00:00+00:00"
    connection.execute(
        """
        INSERT INTO gutenberg_books (
            ebook_id, pg_type, title, language, authors, subjects, locc, bookshelves,
            rights, source_url, catalog_sha256, eligibility_status, created_at, updated_at
        ) VALUES (123, 'Text', 'Book', 'en', 'Author', 'Fiction', 'PR', 'Fiction',
                  'Public domain in the USA.', 'https://www.gutenberg.org/ebooks/123',
                  'catalog-sha', 'ELIGIBLE', ?, ?)
        """,
        (now, now),
    )
    path = tmp_path / "books" / "123" / "pg123.txt"
    path.parent.mkdir(parents=True)
    path.write_text("truncated", encoding="utf-8")

    def fake_rsync(*args, **kwargs):
        assert not path.exists()
        path.write_text("complete source text", encoding="utf-8")
        return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr("litclock.gutenberg.subprocess.run", fake_rsync)
    assert acquire_texts(connection, tmp_path, [123])["acquired"] == 1
    assert path.read_text(encoding="utf-8") == "complete source text"
    connection.close()


def test_gutenberg_boilerplate_cleanup_preserves_prose_and_offsets() -> None:
    raw = """Header and production credits
*** START OF THE PROJECT GUTENBERG EBOOK TEST ***

CONTENTS

CHAPTER I .... 1

CHAPTER I

At 4:19 p.m., the traveler entered the quiet room and closed the door.
The lamp was burning.

[Transcriber's Note: corrected a typo.]

CHAPTER II

At 6:17 p.m., another traveler arrived.

INDEX

4:19 reference

*** END OF THE PROJECT GUTENBERG EBOOK TEST ***
Trademark license footer
"""
    cleaned = strip_gutenberg_boilerplate(raw)
    assert "Header" not in cleaned.text
    assert "Trademark" not in cleaned.text
    paragraphs = extract_gutenberg_paragraphs(raw)
    assert [paragraph.section for paragraph in paragraphs] == ["CHAPTER I", "CHAPTER II"]
    assert len(paragraphs) == 2
    paragraph = paragraphs[0]
    start = paragraph.text.index("4:19 p.m.")
    end = start + len("4:19 p.m.")
    assert raw[paragraph.source_offset(start) : paragraph.source_offset(end)] == "4:19 p.m."
    assert "Transcriber" not in " ".join(value.text for value in paragraphs)
    assert "reference" not in " ".join(value.text for value in paragraphs)


def test_missing_boilerplate_markers_fail_closed() -> None:
    with pytest.raises(ValueError, match="START marker"):
        extract_gutenberg_paragraphs("At 4:19 p.m. the clock struck.")


@pytest.mark.parametrize(
    ("quote", "reason"),
    [
        (
            "CHAPTER FOUR SCENE OUTSIDE A CLUB At 11:30 p.m., the traveler arrived.",
            "chapter/scene heading joined to prose",
        ),
        (
            "Marseilles, arrival, 11.40 a.m. Grand Hotel, luggage. The steamer left soon after.",
            "timetable/itinerary context",
        ),
        (
            '" Jackson 10.54 p.m. " Arrives Detroit 1.00 a.m. Monday.',
            "timetable/itinerary context",
        ),
        (
            "At 3:02 p.m. left hurriedly for the company office. Entered without waiting.",
            "navigation/travel-log context",
        ),
        (
            "4:40 P.M. Geiger counter set up at bedside. Patient conscious but weak.",
            "log/timeline context",
        ),
        (
            "(Handed in at 12.15 P.M. Footover.) Still at station expect arrival Tip.",
            "telegram/log context",
        ),
        (
            "WHAT HAPPENED AT THREE FORTY-THREE P. M.",
            "all-caps time heading",
        ),
        (
            "Arrived at Turin, via Mont Cenis, Friday, 4th October, 6.35 a.m.",
            "telegraphic travel-log context",
        ),
        (
            "“Thursday, 1.35 a.m. Mr. Superintendent Miller, C.I.D.",
            "broken dialogue fragment",
        ),
        (
            "Started at 8.13 a.m. and steered south for three miles across the plain.",
            "telegraphic travel-log context",
        ),
        (
            "Left the encampment at 4.40 a.m., and steering south-west, made our old bivouac.",
            "telegraphic travel-log context",
        ),
        (
            "At 12.26 p.m. made one mile north-east up the creek before the next camp.",
            "navigation/travel-log context",
        ),
        (
            "We started at 8.48 a.m. and at 9.23 had made two miles south-west by south.",
            "navigation/travel-log context",
        ),
        (
            "Steered north 160 degrees east from 6.25 a.m. across the basaltic plain.",
            "telegraphic travel-log context",
        ),
        (
            "At 2.35 p.m. recrossed the creek, which turned to the north-east.",
            "navigation/travel-log context",
        ),
        (
            "Grand Canyon 4:55 P.M. CD 3rd Day Lv. Grand Canyon 8:20 A.M. Ar.",
            "schedule/table context",
        ),
        (
            "May 11: 1.50 a.m. south-south-east for twenty-five miles before camp.",
            "dated journal/log context",
        ),
        (
            '"HEADQUARTERS CAVALRY, April 8, 1865--9:40 p.m. Orders followed.',
            "document/log heading",
        ),
        (
            "Tuesday, THE CHATEAU OF SAINT ANNA, 11.53 A.M. Somewhere in Livadia.",
            "dated location/log heading",
        ),
        (
            "Saturday 4.40 p.m. 86° in a cabin with an electrical fan running.",
            "dated weather-log context",
        ),
        (
            "LONDON, 3.07 P.M., Wednesday. The following telegram was then delivered.",
            "dateline/log heading",
        ),
        (
            "[58] The child was born at 8.55 p.m. on Friday in Windsor.",
            "footnote/endnote context",
        ),
        (
            "At 1.13 P.M. sat down upon its western lip after a long ascent.",
            "navigation/travel-log context",
        ),
        (
            "“The signalman reported at 11:23 P.M. that the ship had proceeded to rendezvous.",
            "broken dialogue fragment",
        ),
        (
            "The lookout sighted destroyer No. 4 at 12.35 P.M. and then sighted cruiser No.",
            "truncated abbreviation context",
        ),
        (
            "Nothing could better the drawing in which he catches the 5.17 a.m.",
            "truncated timetable expression",
        ),
        (
            "The Shans wore sandals. _4th_.--5.25 A.M. Temperature 55.5. "
            "Water boiled at 210. Elevation as before.",
            "dated weather-log context",
        ),
        (
            "JUNIOR CHAPLAIN drifting uneasily through the house. Time, 3:40 A. M. "
            "Heat 94 degrees in veranda.",
            "all-caps time heading",
        ),
        (
            "BARKER'S, N. J., 7.40 A.M. Just arrived after a difficult night journey.",
            "dateline/log heading",
        ),
        (
            "DETERMINE THE DISTANCE FROM THE WARP SHUTTLE TO SIRIUS AT 13:53, "
            "BASING YOUR COMPUTATIONS ON THE EXPANDING-SHIP THEORY.",
            "all-caps time heading",
        ),
        (
            "At 2.55 P.M., the thermometer marked a temperature of 26.24 F., "
            "while the second reading at 4.07 P.M. was unchanged.",
            "scientific measurement context",
        ),
        (
            "The temperatures registered at 4 P.M. and 7.16 P.M. differed by 3.5 degrees.",
            "scientific measurement context",
        ),
        (
            "At 11.30 found fresh water in the river and halted till 1.50 p.m. "
            "to refresh the horses.",
            "navigation/travel-log context",
        ),
        (
            "We left camp at 8.35 a.m., steered north along the river, and changed "
            "course near the eastern creek.",
            "navigation/travel-log context",
        ),
        (
            "Starting at 6.25 a.m. our route was east over a plain of poor soil.",
            "telegraphic travel-log context",
        ),
        (
            "The air at 11.20 a.m. was 33 degrees, the hygrometer registering 0.5.",
            "scientific measurement context",
        ),
    ],
)
def test_text_level_quality_gate_rejects_nonliterary_layout(quote: str, reason: str) -> None:
    assert text_quality_rejection(quote, quote) == reason


def test_windows_line_endings_do_not_hide_boilerplate_markers() -> None:
    raw = (
        "Header\r\n*** START OF THE PROJECT GUTENBERG EBOOK TEST ***\r\n\r\n"
        "At 4:19 p.m., the traveler entered a quiet room and closed the door.\r\n\r\n"
        "At 6:17 p.m., another traveler entered and sat beside the fire.\r\n\r\n"
        "*** END OF THE PROJECT GUTENBERG EBOOK TEST ***\r\nLicense"
    )
    paragraphs = extract_gutenberg_paragraphs(raw)
    assert [paragraph.text for paragraph in paragraphs] == [
        "At 4:19 p.m., the traveler entered a quiet room and closed the door.",
        "At 6:17 p.m., another traveler entered and sat beside the fire.",
    ]


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("John 3:16 records the saying.", "CHAPTER_VERSE"),
        ("The words spoken in Acts, 4:19, were recalled.", "CHAPTER_VERSE"),
        ("See chapter 4:19 for the remainder.", "CHAPTER_VERSE"),
        ("I:3:46 ALAR. Fate has crossed our path.", "PAGE_LINE_REFERENCE"),
        ("The rule appears at § 6:17 in the code.", "LEGAL_REFERENCE"),
        ("Consult pages 16:19 for the discussion.", "PAGE_LINE_REFERENCE"),
        ("The frame used 4:19 proportions.", "DIMENSION_RATIO"),
        ("The final score was 6:10 after a poor defense.", "SCORE_RATIO"),
        ("The catalog number was 18:17 in the old list.", "DATE_CATALOG_CODE"),
    ],
)
def test_reference_false_positives_are_explicitly_invalid(text: str, category: str) -> None:
    detections = detect_time_expressions(text)
    assert len(detections) == 1
    assert detections[0].minute_of_day is None
    assert detections[0].confidence == TimeConfidence.INVALID
    assert detections[0].rejection_reason == f"false_positive:{category}"


def test_betting_odds_are_not_a_contextual_time() -> None:
    detection = detect_time_expressions(
        "Do you remember betting me ten to one this morning that he would break for home?"
    )[0]
    assert detection.confidence == TimeConfidence.INVALID
    assert detection.rejection_reason == "false_positive:SCORE_RATIO"


def test_winning_odds_remain_a_ratio_despite_afternoon_context() -> None:
    detection = detect_time_expressions(
        "Benedictine did win that afternoon at six to one: the odds were extraordinary."
    )[0]
    assert detection.confidence == TimeConfidence.INVALID
    assert detection.rejection_reason == "false_positive:SCORE_RATIO"


@pytest.mark.parametrize("minute", [15 * 60 + 46, 16 * 60 + 19, 18 * 60 + 17])
def test_empty_minute_variants_include_parser_valid_exact_forms(minute: int) -> None:
    variants = target_expression_variants(minute)
    assert len(variants) >= 5
    resolved = {
        detection.minute_of_day
        for variant in variants
        for detection in detect_time_expressions(f"At {variant}, the traveler arrived.")
        if detection.confidence
        in {
            TimeConfidence.EXACT_24H,
            TimeConfidence.EXACT_AM,
            TimeConfidence.EXACT_PM,
            TimeConfidence.EXACT_CONTEXTUAL,
        }
    }
    assert minute in resolved


def test_dynamic_target_set_prioritizes_sparse_minutes_and_excludes_full_buckets(
    tmp_path: Path,
) -> None:
    connection = empty_database(tmp_path / "corpus.sqlite3")
    for index in range(7):
        insert_quote(
            connection,
            minute=0,
            time_24h="00:00",
            time_text="midnight",
            quote=f"At midnight, distinct event {index} unfolded in the moonlit courtyard.",
            title=f"Book {index}",
            author=f"Author {index}",
        )
    insert_quote(
        connection,
        minute=1,
        time_24h="00:01",
        time_text="12:01 a.m.",
        quote="At 12:01 a.m., one solitary traveler crossed the moonlit courtyard.",
        title="One Book",
        author="One Author",
    )
    targets = build_target_expression_set(connection)
    assert all(row["minute_of_day"] != 0 for row in targets)
    assert targets[0]["selectable_count"] == 0
    minute_one = next(row for row in targets if row["minute_of_day"] == 1)
    assert minute_one["deficit"] == 6
    connection.close()


def test_phase_report_refreshes_target_expression_snapshot(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "data" / "generated" / "corpus.sqlite3")
    for index in range(7):
        insert_quote(
            connection,
            minute=0,
            time_24h="00:00",
            time_text="midnight",
            quote=f"At midnight, distinct event {index} unfolded in the moonlit courtyard.",
            title=f"Book {index}",
            author=f"Author {index}",
        )

    write_phase2b_report(connection, tmp_path)

    target_path = tmp_path / "data" / "public_domain" / "gutenberg" / "target_expressions.json"
    targets = json.loads(target_path.read_text(encoding="utf-8"))["targets"]
    assert all(row["minute_of_day"] != 0 for row in targets)
    assert targets[0]["selectable_count"] == 0
    connection.close()


def test_cross_source_duplicate_index_rejects_existing_passage(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "corpus.sqlite3")
    quote = "At 4:19 p.m., the traveler entered the room and quietly closed the heavy door."
    quote_id = insert_quote(
        connection,
        minute=16 * 60 + 19,
        time_24h="16:19",
        time_text="4:19 p.m.",
        quote=quote,
        title="Existing Edition",
        author="Ada Writer",
    )
    result = PassageDuplicateIndex(connection).check(quote, 16 * 60 + 19)
    assert result.status == "EXACT_LEGACY"
    assert result.quote_id == quote_id
    connection.close()


def _insert_gutenberg_book_and_candidates(connection, count: int = 8) -> None:
    now = "2026-09-04T00:00:00+00:00"
    connection.execute(
        """
        INSERT INTO gutenberg_books (
            ebook_id, pg_type, issued, title, language, authors, subjects, locc,
            bookshelves, rights, source_url, catalog_sha256, eligibility_status,
            literature_score, text_path, text_sha256, processing_status, created_at, updated_at
        ) VALUES (123, 'Text', '2000-01-01', 'Book', 'en', 'Author', 'Fiction', 'PR',
                  'Fiction', 'Public domain in the USA.', 'https://www.gutenberg.org/ebooks/123',
                  'catalog-sha', 'ELIGIBLE', 100, 'books/123/pg123.txt', 'text-sha',
                  'PROCESSED', ?, ?)
        """,
        (now, now),
    )
    for number in range(count):
        quote = f"At 4:19 p.m., event number {number} unfolded beside the quiet country station."
        start = quote.index("4:19 p.m.")
        connection.execute(
            """
            INSERT INTO gutenberg_candidates (
                ebook_id, minute_of_day, time_24h, time_text, quote, author, title,
                subjects, bookshelves, rights, source_url, source_file, source_locator,
                source_paragraph_index, source_quote_start, source_quote_end,
                source_expression_start, source_expression_end, containing_paragraph,
                highlight_start, highlight_end, parser_rule, time_confidence, ampm_evidence,
                context_score, literary_quality_score, duplicate_status, target_priority,
                review_status, candidate_hash, normalized_quote_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                123,
                979,
                "16:19",
                "4:19 p.m.",
                quote,
                "Author",
                "Book",
                "Fiction",
                "Fiction",
                "Public domain in the USA.",
                "https://www.gutenberg.org/ebooks/123",
                "pg123.txt",
                f"pg123.txt#p{number}",
                number,
                0,
                len(quote),
                start,
                start + len("4:19 p.m."),
                quote,
                start,
                start + len("4:19 p.m."),
                "numeric",
                "EXACT_PM",
                "explicit p.m.",
                90,
                90,
                "NEW",
                1000,
                "HIGH_CONFIDENCE",
                f"candidate-{number}",
                normalized_quote_hash(quote),
                now,
            ),
        )
    connection.commit()


def test_import_cap_and_restart_reproducibility(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "corpus.sqlite3")
    _insert_gutenberg_book_and_candidates(connection)
    assert import_gutenberg(connection) == 7
    assert (
        connection.execute("SELECT COUNT(*) FROM quotes WHERE minute_of_day = 979").fetchone()[0]
        == 7
    )
    assert import_gutenberg(connection) == 0
    statuses = dict(
        connection.execute(
            "SELECT review_status, COUNT(*) FROM gutenberg_candidates GROUP BY review_status"
        )
    )
    assert statuses == {"DEFERRED_DENSE": 1, "IMPORTED": 7}
    assert validate_import_integrity(connection) == {
        "field_mismatches": 0,
        "provenance_failures": 0,
        "imports_above_cap": 0,
    }
    connection.close()


def test_import_rejects_target_above_phase_ceiling(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "corpus.sqlite3")
    with pytest.raises(ValueError, match="ceiling of 7"):
        import_gutenberg(connection, target_per_minute=8)
    connection.close()


def test_revalidation_revokes_imports_from_newly_ineligible_book_and_is_idempotent(
    tmp_path: Path,
) -> None:
    connection = empty_database(tmp_path / "corpus.sqlite3")
    _insert_gutenberg_book_and_candidates(connection, count=2)
    assert import_gutenberg(connection) == 2
    connection.execute(
        """
        UPDATE gutenberg_books
        SET eligibility_status = 'INELIGIBLE_NONLITERARY',
            eligibility_reason = 'weak shelf label is not literary evidence'
        WHERE ebook_id = 123
        """
    )
    connection.commit()
    first = revalidate_gutenberg_candidates(connection)
    assert first["imports_revoked"] == 2
    assert first["replacement_imports"] == 0
    assert connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 0
    assert dict(
        connection.execute(
            "SELECT review_status, COUNT(*) FROM gutenberg_candidates GROUP BY review_status"
        )
    ) == {"REJECTED_METADATA": 2}
    second = revalidate_gutenberg_candidates(connection)
    assert second["imports_revoked"] == 0
    assert second["replacement_imports"] == 0
    connection.close()


def test_revalidation_reclassifies_new_false_positive_rule_in_review_queue(
    tmp_path: Path,
) -> None:
    connection = empty_database(tmp_path / "corpus.sqlite3")
    _insert_gutenberg_book_and_candidates(connection, count=1)
    quote = "The words spoken in Acts, 4:19, were recalled by everyone in the quiet room."
    start = quote.index("4:19")
    connection.execute(
        """
        UPDATE gutenberg_candidates
        SET minute_of_day = NULL, possible_minute_am = 259, possible_minute_pm = 979,
            time_24h = NULL, time_text = '4:19', quote = ?, containing_paragraph = ?,
            highlight_start = ?, highlight_end = ?, time_confidence = 'AMPM_AMBIGUOUS',
            review_status = 'PENDING_REVIEW'
        """,
        (quote, quote, start, start + len("4:19")),
    )
    connection.commit()

    first = revalidate_gutenberg_candidates(connection)
    assert first["false_positive_reclassified"] == 1
    row = connection.execute(
        """
        SELECT time_confidence, false_positive_category, review_status
        FROM gutenberg_candidates
        """
    ).fetchone()
    assert tuple(row) == ("INVALID", "CHAPTER_VERSE", "REJECTED_FALSE_POSITIVE")
    second = revalidate_gutenberg_candidates(connection)
    assert second["false_positive_reclassified"] == 0
    connection.close()
