from __future__ import annotations

import csv
import json
from pathlib import Path

from conftest import insert_quote, source_spec

from litclock.db import connect_database, initialize_database
from litclock.importers.pipeline import import_corpora
from litclock.stats import calculate_stats, write_reports


def test_coverage_metrics_include_all_1440_minutes_and_write_reports(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    rows = [
        ["00:00", "midnight", "At midnight one bell rang.", "One", "Author A"],
        ["00:00", "twelve", "At twelve another rang.", "Two", "Author B"],
        [
            "00:01",
            "one minute past midnight",
            "At one minute past midnight, silence.",
            "Three",
            "Author C",
        ],
        ["00:01", "00:01", "The display said 00:01.", "Four", "Author D"],
    ]
    with source.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle, delimiter="|", lineterminator="\n").writerows(rows)
    database = tmp_path / "corpus.sqlite3"
    import_corpora(database, [source_spec(source)])
    connection = connect_database(database)
    try:
        stats = calculate_stats(connection)
    finally:
        connection.close()

    assert stats["total_raw_records"] == 4
    assert stats["total_canonical_quotes"] == 4
    assert stats["minutes_covered"] == 2
    assert stats["missing_minutes"] == 1438
    assert stats["mean_quotes_per_minute"] == 4 / 1440
    assert stats["median_quotes_per_minute"] == 0
    assert stats["minute_thresholds"] == {
        "zero": 1438,
        "below_3": 1440,
        "below_5": 1440,
        "below_7": 1440,
        "at_least_7": 0,
        "at_least_14": 0,
    }
    assert stats["unique_books"] == 4
    assert stats["unique_authors"] == 4
    assert len(stats["minute_coverage"]) == 1440
    assert stats["urgent_minutes"][0]["time_24h"] == "00:02"

    json_path, csv_path, markdown_path = write_reports(stats, tmp_path / "reports")
    assert json.loads(json_path.read_text())["missing_minutes"] == 1438
    with csv_path.open(newline="", encoding="utf-8") as handle:
        report_rows = list(csv.DictReader(handle))
    assert len(report_rows) == 1440
    assert "Highest-priority minutes" in markdown_path.read_text()


def test_shared_eligibility_counts_one_quote_and_two_minute_relationships(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "corpus.sqlite3")
    initialize_database(connection)
    quote_id = insert_quote(
        connection,
        minute=226,
        time_24h="03:46",
        time_text="3:46",
        quote="At 3:46 the traveler entered the quiet station.",
        title="One Book",
        author="One Author",
    )
    connection.execute(
        """
        INSERT INTO quote_minute_eligibility (
            quote_id, minute_of_day, eligibility_type, confidence,
            evidence_type, created_at
        ) VALUES (?, 946, 'CLOCKFACE_SHARED_PM', 'CLOCKFACE_EXACT',
                  'CLOCKFACE_NO_DAYPART', '2026-01-01T00:00:00+00:00')
        """,
        (quote_id,),
    )
    stats = calculate_stats(connection)
    assert stats["total_canonical_quotes"] == 1
    assert stats["unique_selectable_quotes"] == 1
    assert stats["quote_minute_eligibility_relationships"] == 2
    assert stats["minute_coverage"][226]["renderable_count"] == 1
    assert stats["minute_coverage"][946]["renderable_count"] == 1
    connection.close()
