from pathlib import Path

from litclock.final_recovery import load_recovery_rows
from litclock.normalize import locate_time_text
from litclock.semantic import SemanticAction, classify_clock_relationship

RECOVERY = Path("data/semantic/english_v1_final_recovery.tsv")


def test_final_recovery_inventory_is_semantic_v2_keep() -> None:
    rows = load_recovery_rows(RECOVERY)

    assert len(rows) == 47
    assert sum(len(row.minutes) for row in rows) == 50
    for row in rows:
        highlight = locate_time_text(row.quote, row.time_text)
        assert highlight.start is not None
        assert highlight.end is not None
        for minute in row.minutes:
            decision = classify_clock_relationship(
                row.quote,
                highlight.start,
                highlight.end,
                minute,
                expected_text=row.time_text,
                parser_route="final_recovery",
                source_locator=row.source_locator or None,
                source_context_before=row.source_context_before or None,
                source_context_after=row.source_context_after or None,
            )
            assert decision.action == SemanticAction.KEEP, (
                row.recovery_id,
                minute,
                decision.reason_code,
            )


def test_clock_transition_is_not_mistaken_for_a_nonclock_range() -> None:
    quote = "I catch the clock changing from 13:31 to 13:32."
    start = quote.index("13:31")
    decision = classify_clock_relationship(
        quote,
        start,
        start + len("13:31"),
        13 * 60 + 31,
        expected_text="13:31",
        parser_route="final_recovery",
    )

    assert decision.action == SemanticAction.KEEP
