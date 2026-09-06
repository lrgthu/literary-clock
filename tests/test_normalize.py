from __future__ import annotations

import pytest

from litclock.models import QualityStatus
from litclock.normalize import (
    clean_display_text,
    locate_time_text,
    minute_to_time,
    normalized_text,
    parse_time_24h,
)


@pytest.mark.parametrize(
    ("raw", "minute", "canonical"),
    [("00:00", 0, "00:00"), ("7:05", 425, "07:05"), ("23:59", 1439, "23:59")],
)
def test_time_parsing(raw: str, minute: int, canonical: str) -> None:
    assert parse_time_24h(raw) == (minute, canonical)
    assert minute_to_time(minute) == canonical


@pytest.mark.parametrize("raw", ["", "24:00", "12:60", "12:5", "noon", "-1:00"])
def test_invalid_times_are_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_time_24h(raw)


def test_display_cleanup_preserves_punctuation_but_removes_transport_markup() -> None:
    assert clean_display_text("  Six<br/>o’clock\n now.  ") == "Six o’clock now."
    assert normalized_text("Twenty-three  O’Clock!") == "twentythreeoclock"


def test_exact_highlight_offsets_round_trip() -> None:
    quote = "It was already twenty minutes past four when the door opened."
    result = locate_time_text(quote, "twenty minutes past four")
    assert result.status == QualityStatus.VERIFIED_EXACT
    assert quote[result.start : result.end] == "twenty minutes past four"


def test_normalized_highlight_maps_unicode_punctuation_to_original_offsets() -> None:
    quote = "At Twenty–Three minutes to five, we left at six o’clock."
    first = locate_time_text(quote, "twenty-three minutes to five")
    second = locate_time_text(quote, "six o'clock")
    assert first.status == QualityStatus.VERIFIED_NORMALIZED
    assert quote[first.start : first.end] == "Twenty–Three minutes to five"
    assert second.status == QualityStatus.VERIFIED_NORMALIZED
    assert quote[second.start : second.end] == "six o’clock"


def test_ambiguous_and_missing_phrases_never_receive_offsets() -> None:
    ambiguous = locate_time_text("At noon, noon came again.", "noon")
    missing = locate_time_text("The bell rang.", "midnight")
    assert ambiguous.status == QualityStatus.AMBIGUOUS
    assert (ambiguous.start, ambiguous.end) == (None, None)
    assert missing.status == QualityStatus.TIME_TEXT_NOT_FOUND
    assert (missing.start, missing.end) == (None, None)
