from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import empty_database, insert_quote

from litclock.db import connect_database
from litclock.selector import QuoteSelector

NOW = datetime(2026, 9, 4, 16, 37, tzinfo=UTC)


def _three_quote_database(path: Path) -> tuple[list[int], object]:
    connection = empty_database(path)
    ids = [
        insert_quote(
            connection,
            quote=f"At 4:37, event {number} happened.",
            title=f"Book {number}",
            author=f"Author {number}",
        )
        for number in range(3)
    ]
    return ids, connection


def test_shuffle_bag_exhausts_pool_before_repeating(tmp_path: Path) -> None:
    ids, connection = _three_quote_database(tmp_path / "quotes.sqlite3")
    selector = QuoteSelector(connection, rng=random.Random(7), now_provider=lambda: NOW)
    first_cycle = [selector.select("16:37").id for _ in range(3)]
    fourth = selector.select("16:37").id
    assert set(first_cycle) == set(ids)
    assert len(set(first_cycle)) == 3
    assert fourth in ids
    state = connection.execute(
        "SELECT cycle FROM shuffle_state WHERE minute_of_day = 997"
    ).fetchone()
    assert state["cycle"] == 2
    connection.close()


def test_shuffle_state_survives_process_style_restart(tmp_path: Path) -> None:
    database = tmp_path / "quotes.sqlite3"
    ids, connection = _three_quote_database(database)
    first = QuoteSelector(connection, rng=random.Random(2), now_provider=lambda: NOW).select(
        "16:37"
    )
    connection.close()

    reopened = connect_database(database)
    second = QuoteSelector(
        reopened,
        rng=random.Random(999),
        now_provider=lambda: NOW + timedelta(minutes=1),
    ).select("16:37")
    remaining = json.loads(
        reopened.execute(
            "SELECT remaining_quote_ids FROM shuffle_state WHERE minute_of_day = 997"
        ).fetchone()[0]
    )
    assert first.id in ids and second.id in ids
    assert first.id != second.id
    assert len(remaining) == 1
    reopened.close()


def test_recent_book_is_skipped_when_an_alternative_exists(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "quotes.sqlite3")
    recent_id = insert_quote(
        connection, quote="At 4:37, A happened.", title="Recent Book", author="Recent Author"
    )
    fresh_id = insert_quote(
        connection, quote="At 4:37, B happened.", title="Fresh Book", author="Fresh Author"
    )
    connection.execute(
        "INSERT INTO display_history (quote_id, minute_of_day, displayed_at) VALUES (?, ?, ?)",
        (recent_id, 997, (NOW - timedelta(hours=1)).isoformat()),
    )
    connection.execute(
        "INSERT INTO shuffle_state VALUES (?, ?, ?, ?)",
        (997, 1, json.dumps([recent_id, fresh_id]), NOW.isoformat()),
    )
    connection.commit()
    selected = QuoteSelector(connection, now_provider=lambda: NOW).select("16:37")
    assert selected.id == fresh_id
    connection.close()


def test_book_cooldown_relaxes_but_fresh_author_is_still_preferred(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "quotes.sqlite3")
    used_author = insert_quote(
        connection, quote="At 4:37, A happened.", title="Only Book", author="Used Author"
    )
    fresh_author = insert_quote(
        connection, quote="At 4:37, B happened.", title="Only Book", author="Fresh Author"
    )
    connection.execute(
        "INSERT INTO display_history (quote_id, minute_of_day, displayed_at) VALUES (?, ?, ?)",
        (used_author, 997, (NOW - timedelta(hours=1)).isoformat()),
    )
    connection.execute(
        "INSERT INTO shuffle_state VALUES (?, ?, ?, ?)",
        (997, 1, json.dumps([used_author, fresh_author]), NOW.isoformat()),
    )
    connection.commit()
    selected = QuoteSelector(connection, now_provider=lambda: NOW).select("16:37")
    assert selected.id == fresh_author
    connection.close()


def test_shared_quote_history_crosses_minute_buckets_and_survives_restart(tmp_path: Path) -> None:
    database = tmp_path / "quotes.sqlite3"
    connection = empty_database(database)
    shared = insert_quote(
        connection,
        minute=226,
        time_24h="03:46",
        time_text="3:46",
        quote="At 3:46 the shared traveler entered the quiet station.",
        title="Shared Book",
        author="Shared Author",
    )
    alternative = insert_quote(
        connection,
        minute=946,
        time_24h="15:46",
        time_text="15:46",
        quote="At 15:46 a different traveler entered the quiet station.",
        title="Fresh Book",
        author="Fresh Author",
    )
    connection.execute(
        """
        INSERT INTO quote_minute_eligibility (
            quote_id, minute_of_day, eligibility_type, confidence,
            evidence_type, created_at
        ) VALUES (?, 946, 'CLOCKFACE_SHARED_PM', 'CLOCKFACE_EXACT',
                  'CLOCKFACE_NO_DAYPART', ?)
        """,
        (shared, NOW.isoformat()),
    )
    connection.execute(
        "INSERT INTO shuffle_state VALUES (?, ?, ?, ?)",
        (226, 1, json.dumps([shared]), NOW.isoformat()),
    )
    connection.execute(
        "INSERT INTO shuffle_state VALUES (?, ?, ?, ?)",
        (946, 1, json.dumps([shared, alternative]), NOW.isoformat()),
    )
    connection.commit()
    first = QuoteSelector(connection, now_provider=lambda: NOW).select("03:46")
    assert first.id == shared
    connection.close()

    reopened = connect_database(database)
    second = QuoteSelector(reopened, now_provider=lambda: NOW + timedelta(hours=12)).select("15:46")
    assert second.id == alternative
    assert second.time_24h == "15:46"
    reopened.close()


def test_global_quote_cooldown_relaxes_for_a_single_shared_quote(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "quotes.sqlite3")
    shared = insert_quote(
        connection,
        minute=226,
        time_24h="03:46",
        time_text="3:46",
        quote="At 3:46 the sole traveler entered the quiet station.",
        title="Only Book",
        author="Only Author",
    )
    connection.execute(
        """
        INSERT INTO quote_minute_eligibility (
            quote_id, minute_of_day, eligibility_type, confidence,
            evidence_type, created_at
        ) VALUES (?, 946, 'CLOCKFACE_SHARED_PM', 'CLOCKFACE_EXACT',
                  'CLOCKFACE_NO_DAYPART', ?)
        """,
        (shared, NOW.isoformat()),
    )
    connection.commit()
    selector = QuoteSelector(connection, now_provider=lambda: NOW)
    assert selector.select("03:46").id == shared
    selector.now_provider = lambda: NOW + timedelta(hours=12)
    assert selector.select("15:46").id == shared
    connection.close()


def test_render_rejection_reselects_without_recording_rejected_quote(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "quotes.sqlite3")
    rejected = insert_quote(
        connection,
        quote="At 4:37, this record must be rejected.",
        title="Rejected Book",
        author="Rejected Author",
    )
    accepted = insert_quote(
        connection,
        quote="At 4:37, this clean record is displayable.",
        title="Accepted Book",
        author="Accepted Author",
    )
    connection.execute(
        "INSERT INTO shuffle_state VALUES (?, ?, ?, ?)",
        (997, 1, json.dumps([rejected, accepted]), NOW.isoformat()),
    )
    connection.commit()
    selector = QuoteSelector(connection, now_provider=lambda: NOW)
    selected = selector.select_compatible("16:37", lambda quote: quote.id == accepted)
    assert selected.id == accepted
    assert [
        int(row[0])
        for row in connection.execute("SELECT quote_id FROM display_history ORDER BY id")
    ] == [accepted]
    remaining = json.loads(
        connection.execute(
            "SELECT remaining_quote_ids FROM shuffle_state WHERE minute_of_day = 997"
        ).fetchone()[0]
    )
    assert remaining == []
    selector.now_provider = lambda: NOW + timedelta(hours=25)
    assert selector.select_compatible("16:37", lambda quote: quote.id == accepted).id == accepted
    assert (
        connection.execute("SELECT cycle FROM shuffle_state WHERE minute_of_day = 997").fetchone()[
            0
        ]
        == 2
    )
    connection.close()


def test_all_rejected_candidates_leave_history_and_shuffle_state_untouched(
    tmp_path: Path,
) -> None:
    connection = empty_database(tmp_path / "quotes.sqlite3")
    quote_id = insert_quote(
        connection,
        quote="At 4:37, this record is unsuitable.",
        title="Book",
        author="Author",
    )
    selector = QuoteSelector(connection, now_provider=lambda: NOW)
    from litclock.selector import NoQuoteAvailable

    with pytest.raises(NoQuoteAvailable, match="display-safe"):
        selector.select_compatible("16:37", lambda quote: quote.id != quote_id)
    assert connection.execute("SELECT COUNT(*) FROM display_history").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM shuffle_state").fetchone()[0] == 0
    connection.close()
