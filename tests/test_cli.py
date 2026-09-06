from __future__ import annotations

from datetime import datetime

from litclock.cli import current_hhmm, terminal_preview
from litclock.models import QualityStatus, Quote


def test_current_time_provider_is_injected() -> None:
    assert current_hhmm(lambda: datetime(2026, 1, 2, 3, 4, 5)) == "03:04"


def test_terminal_preview_uses_stored_offsets_and_stable_id() -> None:
    text = "It was 4:37 when it happened."
    start = text.index("4:37")
    quote = Quote(
        id=42,
        minute_of_day=997,
        time_24h="16:37",
        time_text="4:37",
        quote=text,
        title="Book",
        author="Author",
        sfw=True,
        language="en",
        source_name="source",
        source_url="https://example.test",
        source_license="TEST",
        source_record_id="1",
        quote_hash="hash",
        normalized_quote_hash="normalized",
        highlight_start=start,
        highlight_end=start + 4,
        quality_status=QualityStatus.VERIFIED_EXACT,
    )
    preview = terminal_preview(quote, ansi=False)
    assert "**4:37**" in preview
    assert "— Book, Author" in preview
    assert "quote 42" in preview
