"""Persistent shuffle-bag selection with soft book and author cooldowns."""

from __future__ import annotations

import json
import random
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from litclock.db import row_to_quote
from litclock.models import Quote
from litclock.normalize import parse_time_24h

RENDERABLE_STATUSES = ("VERIFIED_EXACT", "VERIFIED_NORMALIZED")


class NoQuoteAvailable(LookupError):
    pass


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(UTC)


class QuoteSelector:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        rng: random.Random | None = None,
        now_provider: Callable[[], datetime] | None = None,
        book_cooldown: timedelta = timedelta(hours=12),
        author_cooldown: timedelta = timedelta(hours=6),
        exact_quote_cooldown: timedelta = timedelta(hours=24),
        language: str = "en",
    ) -> None:
        self.connection = connection
        self.rng = rng or random.Random()
        self.now_provider = now_provider or (lambda: datetime.now(UTC))
        self.book_cooldown = book_cooldown
        self.author_cooldown = author_cooldown
        self.exact_quote_cooldown = exact_quote_cooldown
        self.language = language

    def _candidate_rows(self, minute: int, *, sfw_only: bool) -> list[sqlite3.Row]:
        sfw_clause = "AND sfw = 1" if sfw_only else ""
        return list(
            self.connection.execute(
                f"""
                SELECT q.*,
                       pool.minute_of_day AS display_minute_of_day,
                       printf('%02d:%02d', pool.minute_of_day / 60,
                              pool.minute_of_day % 60) AS display_time_24h
                FROM quote_minute_pool AS pool
                JOIN quotes AS q ON q.id = pool.quote_id
                WHERE pool.minute_of_day = ?
                  AND q.quality_status IN (?, ?)
                  AND (q.language = ? OR q.language LIKE ?)
                  {sfw_clause}
                ORDER BY q.id
                """,  # noqa: S608 - sfw_clause is an internal constant
                (minute, *RENDERABLE_STATUSES, self.language, f"{self.language}-%"),
            )
        )

    def _load_bag(self, minute: int, candidate_ids: list[int]) -> tuple[int, list[int]]:
        state = self.connection.execute(
            "SELECT cycle, remaining_quote_ids FROM shuffle_state WHERE minute_of_day = ?",
            (minute,),
        ).fetchone()
        candidates = set(candidate_ids)
        if state is not None:
            try:
                remaining = [
                    int(quote_id)
                    for quote_id in json.loads(state["remaining_quote_ids"])
                    if int(quote_id) in candidates
                ]
            except (TypeError, ValueError, json.JSONDecodeError):
                remaining = []
            if remaining:
                return int(state["cycle"]), remaining
            cycle = int(state["cycle"]) + 1
        else:
            cycle = 1
        remaining = candidate_ids.copy()
        self.rng.shuffle(remaining)
        return cycle, remaining

    def _recent_values(
        self, now: datetime
    ) -> tuple[dict[int, datetime], dict[str, datetime], dict[str, datetime]]:
        cutoff = now - max(self.exact_quote_cooldown, self.book_cooldown, self.author_cooldown)
        rows = self.connection.execute(
            """
            SELECT q.id, q.title, q.author, h.displayed_at
            FROM display_history AS h
            JOIN quotes AS q ON q.id = h.quote_id
            WHERE h.displayed_at >= ?
            ORDER BY h.displayed_at DESC
            """,
            (cutoff.isoformat(),),
        )
        quotes: dict[int, datetime] = {}
        books: dict[str, datetime] = {}
        authors: dict[str, datetime] = {}
        for row in rows:
            displayed_at = datetime.fromisoformat(row["displayed_at"])
            quotes.setdefault(int(row["id"]), displayed_at)
            if row["title"]:
                books.setdefault(row["title"], displayed_at)
            if row["author"]:
                authors.setdefault(row["author"], displayed_at)
        return quotes, books, authors

    def _choose_with_cooldowns(
        self, ordered_ids: list[int], rows_by_id: dict[int, sqlite3.Row], now: datetime
    ) -> int:
        recent_quotes, recent_books, recent_authors = self._recent_values(now)

        def quote_ready(quote_id: int) -> bool:
            return (
                quote_id not in recent_quotes
                or now - recent_quotes[quote_id] >= self.exact_quote_cooldown
            )

        def book_ready(quote_id: int) -> bool:
            title = rows_by_id[quote_id]["title"]
            return (
                not title
                or title not in recent_books
                or now - recent_books[title] >= self.book_cooldown
            )

        def author_ready(quote_id: int) -> bool:
            author = rows_by_id[quote_id]["author"]
            return (
                not author
                or author not in recent_authors
                or now - recent_authors[author] >= self.author_cooldown
            )

        preference_groups = (
            lambda quote_id: (
                quote_ready(quote_id) and book_ready(quote_id) and author_ready(quote_id)
            ),
            lambda quote_id: quote_ready(quote_id) and book_ready(quote_id),
            lambda quote_id: quote_ready(quote_id) and author_ready(quote_id),
            quote_ready,
            lambda quote_id: book_ready(quote_id) and author_ready(quote_id),
            lambda quote_id: book_ready(quote_id),
            lambda quote_id: author_ready(quote_id),
            lambda quote_id: True,
        )
        for acceptable in preference_groups:
            for quote_id in ordered_ids:
                if acceptable(quote_id):
                    return quote_id
        raise AssertionError("non-empty shuffle bag produced no selection")

    def _select(
        self,
        time_24h: str,
        *,
        sfw_only: bool,
        persist: bool,
        accept: Callable[[Quote], bool] | None = None,
    ) -> Quote:
        minute, _ = parse_time_24h(time_24h)
        now = _aware_utc(self.now_provider())
        if persist:
            self.connection.execute("BEGIN IMMEDIATE")
        try:
            rows = self._candidate_rows(minute, sfw_only=sfw_only)
            if not rows:
                raise NoQuoteAvailable(f"no renderable quotes for {time_24h}")
            if accept is not None:
                rows = [row for row in rows if accept(row_to_quote(row))]
                if not rows:
                    raise NoQuoteAvailable(f"no display-safe quotes for {time_24h}")
            rows_by_id = {int(row["id"]): row for row in rows}
            cycle, remaining = self._load_bag(minute, list(rows_by_id))
            selected_id = self._choose_with_cooldowns(remaining, rows_by_id, now)
            if persist:
                remaining.remove(selected_id)
                self.connection.execute(
                    """
                    INSERT INTO shuffle_state (
                        minute_of_day, cycle, remaining_quote_ids, updated_at
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(minute_of_day) DO UPDATE SET
                        cycle = excluded.cycle,
                        remaining_quote_ids = excluded.remaining_quote_ids,
                        updated_at = excluded.updated_at
                    """,
                    (minute, cycle, json.dumps(remaining), now.isoformat()),
                )
                self.connection.execute(
                    """
                    INSERT INTO display_history (quote_id, minute_of_day, displayed_at)
                    VALUES (?, ?, ?)
                    """,
                    (selected_id, minute, now.isoformat()),
                )
                self.connection.commit()
            return row_to_quote(rows_by_id[selected_id])
        except Exception:
            if persist:
                self.connection.rollback()
            raise

    def select(self, time_24h: str, *, sfw_only: bool = False) -> Quote:
        """Select and persist shuffle/history state for a real display event."""
        return self._select(time_24h, sfw_only=sfw_only, persist=True)

    def preview(self, time_24h: str, *, sfw_only: bool = False) -> Quote:
        """Select from current bags/cooldowns without changing persistent state."""
        return self._select(time_24h, sfw_only=sfw_only, persist=False)

    def select_compatible(
        self,
        time_24h: str,
        accept: Callable[[Quote], bool],
        *,
        sfw_only: bool = False,
        preview: bool = False,
    ) -> Quote:
        """Skip rejected candidates and persist only the quote actually displayed."""
        return self._select(
            time_24h,
            sfw_only=sfw_only,
            persist=not preview,
            accept=accept,
        )
