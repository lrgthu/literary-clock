"""Deterministic, renderer-owned date labels."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def format_short_date(value: date) -> str:
    """Format an English short date without locale dependence or a leading zero."""
    return f"{_WEEKDAYS[value.weekday()]}, {_MONTHS[value.month - 1]} {value.day}"


def current_local_date(now_provider: Callable[[], date] = date.today) -> date:
    """Isolate the local calendar source for testable render commands."""
    return now_provider()


def parse_date_override(value: str) -> date:
    """Parse the CLI's reproducible ISO date override."""
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("--date must use ISO YYYY-MM-DD format") from error
