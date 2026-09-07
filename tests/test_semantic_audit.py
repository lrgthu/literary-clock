from __future__ import annotations

from pathlib import Path

import pytest
from conftest import empty_database, insert_quote

from litclock.semantic import SemanticAction, SemanticClass, classify_clock_relationship
from litclock.semantic_audit import (
    apply_semantic_audit,
    current_semantic_metrics,
    run_semantic_audit,
)


def _classify(
    quote: str,
    phrase: str,
    minute: int,
    *,
    source_section: str | None = None,
):
    start = quote.index(phrase)
    decision = classify_clock_relationship(
        quote,
        start,
        start + len(phrase),
        minute,
        expected_text=phrase,
        parser_route="test",
        source_section=source_section,
    )
    assert quote[start : start + len(phrase)] == phrase
    return decision


@pytest.mark.parametrize(
    ("quote", "phrase", "minute", "semantic_class"),
    [
        ("There was one five-minute interval.", "one five", 65, SemanticClass.DURATION),
        ("He waited for five minutes.", "five", 5 * 60, SemanticClass.DURATION),
        ("Five minutes later he returned.", "Five", 5 * 60, SemanticClass.RELATIVE_DURATION),
        ("A ten-minute pause followed.", "ten", 10 * 60, SemanticClass.DURATION),
        ("The journey lasted three hours.", "three", 3 * 60, SemanticClass.DURATION),
        (
            "The journey took one hour and five minutes.",
            "one hour and five minutes",
            65,
            SemanticClass.DURATION,
        ),
        (
            "They waited an hour and five minutes.",
            "an hour and five minutes",
            65,
            SemanticClass.DURATION,
        ),
        (
            "The crossing lasted two hours and ten minutes.",
            "two hours and ten minutes",
            130,
            SemanticClass.DURATION,
        ),
        (
            "Half an hour later, she returned.",
            "Half an hour later",
            30,
            SemanticClass.RELATIVE_DURATION,
        ),
        ("A one-hour delay followed.", "one", 60, SemanticClass.DURATION),
        (
            "Within ten minutes, the room was empty.",
            "Within ten minutes",
            10,
            SemanticClass.RELATIVE_DURATION,
        ),
        (
            "After five minutes, he returned.",
            "After five minutes",
            5,
            SemanticClass.RELATIVE_DURATION,
        ),
        (
            "Five minutes earlier, the bell had rung.",
            "Five minutes earlier",
            5,
            SemanticClass.RELATIVE_DURATION,
        ),
        ("Chapter 12:16", "12:16", 12 * 60 + 16, SemanticClass.SECTION_OR_REFERENCE),
        ("Chap. 12:16", "12:16", 12 * 60 + 16, SemanticClass.SECTION_OR_REFERENCE),
        ("John 12:16", "12:16", 12 * 60 + 16, SemanticClass.SECTION_OR_REFERENCE),
        ("Matthew 5:12", "5:12", 5 * 60 + 12, SemanticClass.SECTION_OR_REFERENCE),
        ("Section 3:45", "3:45", 3 * 60 + 45, SemanticClass.SECTION_OR_REFERENCE),
        ("Act 2:15", "2:15", 2 * 60 + 15, SemanticClass.SECTION_OR_REFERENCE),
        ("Scene 3:20", "3:20", 3 * 60 + 20, SemanticClass.SECTION_OR_REFERENCE),
        ("The score was 3:15.", "3:15", 3 * 60 + 15, SemanticClass.SCORE_OR_RESULT),
        ("The ratio was 1:20.", "1:20", 80, SemanticClass.RATIO_OR_MEASUREMENT),
    ],
)
def test_required_non_clock_constructions_are_quarantined(
    quote: str,
    phrase: str,
    minute: int,
    semantic_class: SemanticClass,
) -> None:
    decision = _classify(quote, phrase, minute)
    assert decision.action == SemanticAction.QUARANTINE
    assert decision.semantic_class == semantic_class


@pytest.mark.parametrize(
    ("quote", "phrase", "minute"),
    [
        ("At 12:16 he entered the room.", "12:16", 12 * 60 + 16),
        ("It was 12:16 when she arrived.", "12:16", 12 * 60 + 16),
        ("The clock showed 12:16.", "12:16", 12 * 60 + 16),
        ("At 1:05 a.m. the door opened.", "1:05 a.m.", 65),
        ("It was then 3.40 A.M.", "3.40 A.M.", 3 * 60 + 40),
        ("At one oh five, the bell rang.", "one oh five", 65),
        ("At one fifteen, the bell rang.", "one fifteen", 75),
        ("Ten after one, he left.", "Ten after one", 70),
        ("They arrived at five.", "at five", 5 * 60),
        ("They arrived around five.", "around five", 5 * 60),
        ("They arrived before five o'clock.", "before five o'clock", 5 * 60),
        ("They arrived by half past three.", "half past three", 3 * 60 + 30),
        ("At four in the morning he left.", "four in the morning", 4 * 60),
        ("At three p.m. she arrived.", "three p.m.", 15 * 60),
        ("The clock struck one.", "clock struck one", 60),
        ("The clocks were striking thirteen.", "clocks were striking thirteen", 13 * 60),
        ("At 0541 h he called.", "0541 h", 5 * 60 + 41),
        ("At 11h20 he called.", "11h20", 11 * 60 + 20),
        ("The telegram read 551 PM.", "551 PM", 17 * 60 + 51),
        ("It was 3.6 AM.", "3.6 AM", 3 * 60 + 6),
        ("It was five till nine.", "five till nine", 8 * 60 + 55),
        ("It was two minutes of nine.", "two minutes of nine", 8 * 60 + 58),
        ("It was ten on the clock.", "ten on the clock", 10 * 60),
        ("It was twelve of the clock.", "twelve of the clock", 12 * 60),
        ("Clock time is 0 Hours, 12 Minutes, 0 Seconds.", "0 Hours, 12 Minutes", 12),
        (
            "At precisely 13 hours and 6 minutes, confusion broke out.",
            "13 hours and 6 minutes",
            13 * 60 + 6,
        ),
        ("Five minutes past one, he left.", "Five minutes past one", 65),
        ("At quarter past three she woke.", "quarter past three", 3 * 60 + 15),
        ("At half past six...", "half past six", 6 * 60 + 30),
        ("Twenty minutes to eight...", "Twenty minutes to eight", 7 * 60 + 40),
    ],
)
def test_required_clock_constructions_are_kept(quote: str, phrase: str, minute: int) -> None:
    decision = _classify(quote, phrase, minute)
    assert decision.action == SemanticAction.KEEP
    assert decision.semantic_class in {
        SemanticClass.CLOCK_TIME_EXACT,
        SemanticClass.CLOCK_TIME_AMBIGUOUS,
    }
    assert minute in decision.derived_minutes


@pytest.mark.parametrize(
    ("quote", "phrase", "minute"),
    [
        ("There were two ten-minute pauses.", "two ten", 130),
        ("There were three twenty-minute delays.", "three twenty", 200),
    ],
)
def test_number_pairs_are_not_synthesized_across_duration_nouns(
    quote: str, phrase: str, minute: int
) -> None:
    decision = _classify(quote, phrase, minute)
    assert decision.action == SemanticAction.QUARANTINE
    assert decision.reason_code == "DURATION_HYPHENATED"


def test_bare_colon_expression_requires_review() -> None:
    decision = _classify("12:16", "12:16", 12 * 60 + 16)
    assert decision.action == SemanticAction.REVIEW
    assert decision.reason_code == "COLON_WITHOUT_TIME_EVIDENCE"


def test_unrelated_measurement_does_not_override_explicit_clock_context() -> None:
    quote = "The clock ticked on at 3:15. The room was seven feet wide."
    decision = _classify(quote, "3:15", 3 * 60 + 15)
    assert decision.action == SemanticAction.KEEP


def test_generic_word_numbers_is_not_mistaken_for_book_of_numbers() -> None:
    quote = "The red numbers 06:14 faded from the clock display."
    decision = _classify(quote, "06:14", 6 * 60 + 14)
    assert decision.action == SemanticAction.KEEP


def test_ordinary_verb_am_does_not_resolve_clockface_to_morning() -> None:
    quote = "I am in town, but I shall return by the twelve o'clock train."
    decision = _classify(quote, "twelve o'clock", 12 * 60)
    assert decision.action == SemanticAction.KEEP
    assert decision.semantic_class == SemanticClass.CLOCK_TIME_AMBIGUOUS
    assert decision.derived_minutes == (0, 12 * 60)


def test_nonadjacent_daypart_does_not_force_resolution() -> None:
    quote = (
        'The clock read "Eleven thirty-five." This was exactly the case I wanted; '
        "I was free until later this afternoon."
    )
    decision = _classify(quote, "Eleven thirty-five", 11 * 60 + 35)
    assert decision.action == SemanticAction.KEEP
    assert decision.semantic_class == SemanticClass.CLOCK_TIME_AMBIGUOUS


def test_source_structure_can_quarantine_otherwise_clocklike_text() -> None:
    decision = _classify(
        "At 12:16 he entered the room.",
        "12:16",
        12 * 60 + 16,
        source_section="Table of Contents",
    )
    assert decision.action == SemanticAction.QUARANTINE
    assert decision.semantic_class == SemanticClass.HEADING_OR_TOC
    assert decision.reason_code == "STRUCTURE_TOC"


def test_claimed_minute_must_be_derivable_from_highlight() -> None:
    decision = _classify("At 1:05 a.m. the door opened.", "1:05 a.m.", 2 * 60 + 5)
    assert decision.action == SemanticAction.QUARANTINE
    assert decision.reason_code == "HIGHLIGHT_SEMANTIC_MISMATCH"
    assert decision.derived_minutes == (65,)


def test_unicode_offsets_remain_exact_and_source_text_is_unchanged() -> None:
    quote = "“Don’t wait”—at a quarter past three, she left."
    phrase = "a quarter past three"
    original = quote
    decision = _classify(quote, phrase, 3 * 60 + 15)
    assert decision.action == SemanticAction.KEEP
    assert quote == original
    start = quote.index(phrase)
    assert quote[start : start + len(phrase)] == phrase


def test_audit_is_reversible_and_review_is_excluded_only_after_apply(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "audit.sqlite3")
    kept_id = insert_quote(
        connection,
        minute=12 * 60 + 16,
        time_24h="12:16",
        time_text="12:16",
        quote="At 12:16 he entered the room.",
        title="Clock Book",
        author="A. Writer",
    )
    review_id = insert_quote(
        connection,
        minute=13 * 60 + 16,
        time_24h="13:16",
        time_text="12:16",
        quote="12:16",
        title="Bare Book",
        author="B. Writer",
    )
    bad_id = insert_quote(
        connection,
        minute=65,
        time_24h="01:05",
        time_text="one five",
        quote="There was one five-minute interval.",
        title="Duration Book",
        author="C. Writer",
    )

    before = current_semantic_metrics(connection)
    result = run_semantic_audit(connection)
    assert result["counts"] == {"KEEP": 1, "QUARANTINE": 1, "REVIEW": 1}
    assert current_semantic_metrics(connection) == before
    assert (
        connection.execute(
            "SELECT status FROM semantic_audit_runs WHERE id = ?", (result["run_id"],)
        ).fetchone()[0]
        == "AUDIT_COMPLETE"
    )

    applied = apply_semantic_audit(connection, result["run_id"])
    assert applied["after"]["selectable_quotes"] == 1
    assert [row[0] for row in connection.execute("SELECT quote_id FROM quote_minute_pool")] == [
        kept_id
    ]
    assert connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 3
    assert (
        connection.execute(
            "SELECT action FROM semantic_time_audit WHERE quote_id = ?", (review_id,)
        ).fetchone()[0]
        == "REVIEW"
    )
    assert (
        connection.execute(
            "SELECT action FROM semantic_time_audit WHERE quote_id = ?", (bad_id,)
        ).fetchone()[0]
        == "QUARANTINE"
    )

    unaudited_id = insert_quote(
        connection,
        minute=2 * 60,
        time_24h="02:00",
        time_text="two",
        quote="There were two birds in the sky.",
        title="Unaudited Book",
        author="D. Writer",
    )
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM quote_minute_pool WHERE quote_id = ?", (unaudited_id,)
        ).fetchone()[0]
        == 0
    )
    connection.close()


def test_repeated_audit_is_deterministic_and_fully_accounted(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "repeat.sqlite3")
    insert_quote(
        connection,
        minute=12 * 60 + 16,
        time_24h="12:16",
        time_text="12:16",
        quote="At 12:16 he entered the room.",
        title="Clock Book",
        author="A. Writer",
    )
    insert_quote(
        connection,
        minute=65,
        time_24h="01:05",
        time_text="one five",
        quote="There was one five-minute interval.",
        title="Duration Book",
        author="B. Writer",
    )

    first = run_semantic_audit(connection)
    second = run_semantic_audit(connection)
    assert first["corpus_fingerprint"] == second["corpus_fingerprint"]
    assert first["counts"] == second["counts"]
    assert sum(first["counts"].values()) == first["baseline"]["relationships"]
    connection.close()
