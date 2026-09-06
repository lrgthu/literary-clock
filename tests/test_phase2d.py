from __future__ import annotations

import csv
from pathlib import Path

from conftest import empty_database, insert_quote

from litclock.phase2d import (
    _phase2d_context_rejection,
    _recovered_context,
    current_sparse_targets,
    start_phase2d,
)


def test_sparse_targets_compute_exact_row_level_deficit(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "corpus.sqlite3")
    insert_quote(
        connection,
        minute=0,
        time_24h="00:00",
        time_text="midnight",
        quote="At midnight the first traveler quietly entered the empty station.",
        title="First Book",
        author="First Author",
    )
    insert_quote(
        connection,
        minute=0,
        time_24h="00:00",
        time_text="midnight",
        quote="At midnight the second traveler quietly entered the empty station.",
        title="Second Book",
        author="Second Author",
    )
    insert_quote(
        connection,
        minute=1,
        time_24h="00:01",
        time_text="12:01 a.m.",
        quote="At 12:01 a.m. the third traveler quietly entered the empty station.",
        title="Third Book",
        author="Third Author",
    )

    targets = current_sparse_targets(connection)
    assert len(targets) == 1440
    assert targets[0].effective_candidate_count == 2
    assert targets[0].deficit_to_3 == 1
    assert targets[1].effective_candidate_count == 1
    assert targets[1].deficit_to_3 == 2
    assert sum(target.deficit_to_3 for target in targets) == 1438 * 3 + 3
    connection.close()


def test_phase2d_target_snapshot_is_restart_reproducible(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "data" / "generated" / "corpus.sqlite3")
    first = start_phase2d(connection, tmp_path)
    second = start_phase2d(connection, tmp_path)
    assert first["run_id"] == second["run_id"]
    assert first["starting_deficit_to_3"] == 4320
    assert second["reused"] is True
    rows = list(csv.DictReader((tmp_path / "data" / "generated" / "PHASE2D_TARGETS.csv").open()))
    assert len(rows) == 1440
    connection.close()


def test_existing_candidate_context_recovery_balances_dialogue() -> None:
    paragraph = (
        "“You will hear more about that when I come to it. "
        "That was at twelve thirty-nine p.m. exactly—I looked at my watch immediately.”"
    )
    row = {
        "containing_paragraph": paragraph,
        "previous_paragraph": "",
        "following_paragraph": "",
        "time_text": "twelve thirty-nine p.m.",
        "minute_of_day": 12 * 60 + 39,
    }
    recovered = _recovered_context(row)  # type: ignore[arg-type]
    assert recovered is not None
    quote, start, end = recovered
    assert quote.startswith("“") and quote.endswith("”")
    assert quote[start:end] == "twelve thirty-nine p.m."


def test_sparse_tail_quality_rejects_logs_headings_and_ocr_fragments() -> None:
    examples = [
        'But these were dull days. "9.44 a.m. Working party seen and fired on."',
        "He waited. WHAT HAPPENED AT THREE FORTY-THREE P. M. The room was empty.",
        "1811ff Melissa waited until a late hour 1851/70 hour 1811ff ...",
        "We had plenty to do (_Bugle. Now at 3.24 P.M._) imaginary attacks.",
        "he remarked pleasantly. We arrive at three thirty-nine this afternoon.",
    ]
    for example in examples:
        assert _phase2d_context_rejection(example, example) is not None


def test_sparse_tail_quality_rejects_legal_official_prose() -> None:
    quote = (
        "These findings are attacked upon the ground that the testimony showed the "
        "steamship passed the fort at 4:26 in the afternoon."
    )
    assert _phase2d_context_rejection(quote, quote) == "legal or official-document prose"
