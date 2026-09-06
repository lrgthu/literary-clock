"""Time parsing, text normalization, hashing, and highlight validation."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata

from litclock.models import HighlightResult, QualityStatus

_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")


def parse_time_24h(value: str) -> tuple[int, str]:
    """Return ``(minute_of_day, HH:MM)`` or raise ``ValueError``."""
    match = _TIME_RE.fullmatch(value)
    if not match:
        raise ValueError(f"invalid 24-hour time: {value!r}")
    hour, minute = (int(part) for part in match.groups())
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"invalid 24-hour time: {value!r}")
    return hour * 60 + minute, f"{hour:02d}:{minute:02d}"


def minute_to_time(minute_of_day: int) -> str:
    if not 0 <= minute_of_day < 1440:
        raise ValueError(f"minute outside 0..1439: {minute_of_day}")
    hour, minute = divmod(minute_of_day, 60)
    return f"{hour:02d}:{minute:02d}"


def clean_display_text(value: str) -> str:
    """Remove source transport artifacts while preserving literary punctuation."""
    value = html.unescape(_BR_RE.sub(" ", value))
    return _SPACE_RE.sub(" ", value).strip()


def normalized_text(value: str) -> str:
    """Canonical comparison form insensitive to case, punctuation, and spacing."""
    chars: list[str] = []
    for char in unicodedata.normalize("NFKD", value).casefold():
        if char.isalnum():
            chars.append(char)
    return "".join(chars)


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalized_quote_hash(value: str) -> str:
    return text_hash(normalized_text(value))


def _normalized_with_offsets(value: str) -> tuple[str, list[tuple[int, int]]]:
    chars: list[str] = []
    offsets: list[tuple[int, int]] = []
    for index, original in enumerate(value):
        expanded = unicodedata.normalize("NFKD", original).casefold()
        for char in expanded:
            if char.isalnum():
                chars.append(char)
                offsets.append((index, index + 1))
    return "".join(chars), offsets


def _all_occurrences(haystack: str, needle: str) -> list[int]:
    starts: list[int] = []
    start = 0
    while True:
        found = haystack.find(needle, start)
        if found < 0:
            return starts
        starts.append(found)
        start = found + 1


def locate_time_text(quote: str, time_text: str) -> HighlightResult:
    """Locate a unique time phrase and return trustworthy original-string offsets."""
    if not time_text:
        return HighlightResult(QualityStatus.TIME_TEXT_NOT_FOUND)

    exact_starts = _all_occurrences(quote, time_text)
    if len(exact_starts) == 1:
        start = exact_starts[0]
        return HighlightResult(QualityStatus.VERIFIED_EXACT, start, start + len(time_text))
    if len(exact_starts) > 1:
        return HighlightResult(QualityStatus.AMBIGUOUS)

    normalized_quote, offsets = _normalized_with_offsets(quote)
    normalized_time = normalized_text(time_text)
    if not normalized_time:
        return HighlightResult(QualityStatus.TIME_TEXT_NOT_FOUND)
    normalized_starts = _all_occurrences(normalized_quote, normalized_time)
    spans = {
        (offsets[start][0], offsets[start + len(normalized_time) - 1][1])
        for start in normalized_starts
    }
    if len(spans) == 1:
        start, end = spans.pop()
        return HighlightResult(QualityStatus.VERIFIED_NORMALIZED, start, end)
    if len(spans) > 1:
        return HighlightResult(QualityStatus.AMBIGUOUS)
    return HighlightResult(QualityStatus.TIME_TEXT_NOT_FOUND)


def quality_rank(status: QualityStatus | str) -> int:
    status = QualityStatus(status)
    return {
        QualityStatus.VERIFIED_EXACT: 4,
        QualityStatus.VERIFIED_NORMALIZED: 3,
        QualityStatus.AMBIGUOUS: 2,
        QualityStatus.TIME_TEXT_NOT_FOUND: 1,
        QualityStatus.INVALID_TIME: 0,
        QualityStatus.MALFORMED: 0,
    }[status]
