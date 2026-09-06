from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import empty_database, insert_quote

from litclock.db import connect_database
from litclock.phase2c import (
    activate_phase2c,
    build_phase2c_counterfactual,
    semantic_display_minutes,
)


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("At 3:46 the traveler entered the quiet station.", (226, 946)),
        ("At 3:46 a.m. the traveler entered the quiet station.", (226,)),
        ("At 3:46 p.m. the traveler entered the quiet station.", (946,)),
        ("At three forty-six in the morning, the traveler entered.", (226,)),
        ("At three forty-six that evening, the traveler entered.", (946,)),
        ("At a quarter past three the traveler entered the station.", (195, 915)),
        ("At nineteen minutes past four the traveler entered the station.", (259, 979)),
        ("At ten minutes to six the traveler entered the station.", (350, 1070)),
        ("At half past seven the traveler entered the station.", (450, 1170)),
        ("At noon the traveler entered the station.", (720,)),
        ("At midnight the traveler entered the station.", (0,)),
    ],
)
def test_phase2c_semantic_display_minutes(text: str, minutes: tuple[int, ...]) -> None:
    assert semantic_display_minutes(text) == minutes


@pytest.mark.parametrize(
    "text",
    [
        "At about 3:46 the traveler entered the station.",
        "At around half past seven the traveler entered the station.",
        "From 3:46 to 4:10 the traveler waited at the station.",
    ],
)
def test_phase2c_rejects_approximate_and_range_expressions(text: str) -> None:
    assert semantic_display_minutes(text) == ()


def test_source_context_deterministic_resolution_overrides_dual_mapping() -> None:
    displayed = "At 3:46 the traveler entered the quiet station."
    paragraph = "At 3:46 that evening, the traveler entered the quiet station."
    start = paragraph.index("3:46")
    source_context = (paragraph, start, start + 4, "", "")
    assert semantic_display_minutes(displayed, source_context=source_context) == (946,)


def test_vague_neighbor_context_does_not_falsely_resolve_or_block_dual_mapping() -> None:
    displayed = "At 3:46 the traveler entered the quiet station."
    paragraph = displayed
    start = paragraph.index("3:46")
    source_context = (
        paragraph,
        start,
        start + 4,
        "Years before, he had often walked here in the morning.",
        "He remembered those distant journeys.",
    )
    assert semantic_display_minutes(displayed, source_context=source_context) == (226, 946)


def test_explicit_greeting_and_monotonic_local_times_resolve_source_context() -> None:
    displayed = "At 3:46 another traveler entered the classroom."
    paragraph = (
        "At 3:20 the teacher entered and said, ‘Buenas tardes.’ "
        "At 3:28 the first lesson began. "
        "At 3:46 another traveler entered the classroom."
    )
    start = paragraph.index("3:46")
    source_context = (paragraph, start, start + 4, "", "")
    assert semantic_display_minutes(displayed, source_context=source_context) == (946,)


def _add_test_provenance(connection, quote_id: int) -> None:
    now = datetime.now(UTC).isoformat()
    run_id = connection.execute(
        "INSERT INTO import_runs (started_at, finished_at, status) VALUES (?, ?, 'COMPLETE')",
        (now, now),
    ).lastrowid
    source_id = connection.execute(
        """
        INSERT INTO sources (
            name, slug, source_url, source_license, upstream_commit,
            corpus_path, corpus_sha256, imported_at
        ) VALUES ('test', 'test', 'https://example.test', 'TEST', 'test', 'test', 'test', ?)
        """,
        (now,),
    ).lastrowid
    connection.execute(
        """
        INSERT INTO quote_provenance (
            quote_id, source_id, import_run_id, source_record_id, raw_time_24h,
            raw_time_text, raw_quote, raw_title, raw_author, raw_quote_hash,
            validation_status, highlight_start, highlight_end, duplicate_kind, raw_payload
        )
        SELECT id, ?, ?, CAST(id AS TEXT), time_24h, time_text, quote, title, author,
               quote_hash, quality_status, highlight_start, highlight_end, 'CANONICAL', '{}'
        FROM quotes WHERE id = ?
        """,
        (source_id, run_id, quote_id),
    )
    connection.commit()


def test_counterfactual_then_activation_keeps_one_quote_with_two_rows_and_restarts(
    tmp_path: Path,
) -> None:
    database = tmp_path / "data" / "generated" / "corpus.sqlite3"
    connection = empty_database(database)
    quote_id = insert_quote(
        connection,
        minute=226,
        time_24h="03:46",
        time_text="3:46",
        quote="At 3:46 the traveler entered the quiet station and carefully closed the door.",
        title="Clock-Face Story",
        author="Ada Writer",
    )
    _add_test_provenance(connection, quote_id)

    counterfactual = build_phase2c_counterfactual(connection, tmp_path)
    assert counterfactual["A"]["quote_minute_eligibility_relationships"] == 1
    assert counterfactual["C"]["quote_minute_eligibility_relationships"] == 2
    assert counterfactual["C"]["remaining_deficit_to_7"] == (
        counterfactual["A"]["remaining_deficit_to_7"] - 1
    )

    activated = activate_phase2c(connection, tmp_path)
    assert activated["integrity"] == {
        "missing_semantics": 0,
        "missing_eligibility": 0,
        "import_failures": 0,
        "provenance_failures": 0,
        "counterfactual_mismatch": 0,
    }
    assert connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 1
    assert [
        row[0]
        for row in connection.execute(
            "SELECT minute_of_day FROM quote_minute_eligibility ORDER BY minute_of_day"
        )
    ] == [226, 946]
    connection.close()

    reopened = connect_database(database)
    restarted = activate_phase2c(reopened, tmp_path)
    assert restarted["stats"]["quote_minute_eligibility_relationships"] == 2
    assert reopened.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 1
    reopened.close()


def test_activation_accepts_prevalidated_normalized_legacy_highlight(tmp_path: Path) -> None:
    database = tmp_path / "data" / "generated" / "corpus.sqlite3"
    connection = empty_database(database)
    quote_id = insert_quote(
        connection,
        minute=661,
        time_24h="11:01",
        time_text="One minute past eleven",
        quote="One minute past eleven, the visitor quietly entered the old drawing room.",
        title="Case Story",
        author="Bea Writer",
    )
    connection.execute(
        """
        UPDATE quotes SET time_text = 'one minute past eleven',
            quality_status = 'VERIFIED_NORMALIZED' WHERE id = ?
        """,
        (quote_id,),
    )
    _add_test_provenance(connection, quote_id)

    counterfactual = build_phase2c_counterfactual(connection, tmp_path)
    assert counterfactual["C"]["quote_minute_eligibility_relationships"] == 2
    activated = activate_phase2c(connection, tmp_path)
    assert activated["integrity"]["import_failures"] == 0
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM quote_minute_eligibility WHERE quote_id = ?", (quote_id,)
        ).fetchone()[0]
        == 2
    )
    connection.close()
