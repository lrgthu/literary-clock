from __future__ import annotations

import pytest

from litclock.models import TimeConfidence
from litclock.timeparse import detect_time_expressions


def _one(text: str):
    detections = detect_time_expressions(text)
    assert len(detections) == 1, detections
    detection = detections[0]
    assert text[detection.start : detection.end] == detection.text
    return detection


@pytest.mark.parametrize(
    ("text", "minute", "confidence"),
    [
        ("At 04:37 he arrived.", 4 * 60 + 37, TimeConfidence.EXACT_24H),
        ("At 16:37 he arrived.", 16 * 60 + 37, TimeConfidence.EXACT_24H),
        ("At 1637 hours he arrived.", 16 * 60 + 37, TimeConfidence.EXACT_24H),
        ("At 4:37 a.m. he arrived.", 4 * 60 + 37, TimeConfidence.EXACT_AM),
        ("At 4.37 p.m. he arrived.", 16 * 60 + 37, TimeConfidence.EXACT_PM),
        ("At four o’clock in the afternoon he arrived.", 16 * 60, TimeConfidence.EXACT_CONTEXTUAL),
        ("It was five past four in the morning.", 4 * 60 + 5, TimeConfidence.EXACT_CONTEXTUAL),
        (
            "It was ten minutes past six that evening.",
            18 * 60 + 10,
            TimeConfidence.EXACT_CONTEXTUAL,
        ),
        ("It was a quarter past three p.m.", 15 * 60 + 15, TimeConfidence.EXACT_PM),
        ("It was twenty after seven at night.", 19 * 60 + 20, TimeConfidence.EXACT_CONTEXTUAL),
        ("It was five to twelve in the morning.", 11 * 60 + 55, TimeConfidence.EXACT_CONTEXTUAL),
        ("It was ten minutes to six that evening.", 17 * 60 + 50, TimeConfidence.EXACT_CONTEXTUAL),
        ("It was a quarter to nine in the morning.", 8 * 60 + 45, TimeConfidence.EXACT_CONTEXTUAL),
        ("It was twenty before four p.m.", 15 * 60 + 40, TimeConfidence.EXACT_PM),
        ("It was five minutes to midnight.", 23 * 60 + 55, TimeConfidence.EXACT_24H),
        ("It was half-past four in the afternoon.", 16 * 60 + 30, TimeConfidence.EXACT_CONTEXTUAL),
        ("At noon the bell rang.", 12 * 60, TimeConfidence.EXACT_24H),
        ("At midday the bell rang.", 12 * 60, TimeConfidence.EXACT_24H),
        ("At midnight the bell rang.", 0, TimeConfidence.EXACT_24H),
        (
            "At twenty-three minutes past four in the afternoon, he left.",
            16 * 60 + 23,
            TimeConfidence.EXACT_CONTEXTUAL,
        ),
        (
            "At seventeen minutes to six in the morning, she woke.",
            5 * 60 + 43,
            TimeConfidence.EXACT_CONTEXTUAL,
        ),
        (
            "At forty‐one minutes past ten a.m., she arrived.",
            10 * 60 + 41,
            TimeConfidence.EXACT_AM,
        ),
        (
            "At forty-six minutes past three p.m., she arrived.",
            15 * 60 + 46,
            TimeConfidence.EXACT_PM,
        ),
        (
            "At forty-one minutes to five p.m., she arrived.",
            16 * 60 + 19,
            TimeConfidence.EXACT_PM,
        ),
        (
            "At forty-three minutes to seven p.m., she arrived.",
            18 * 60 + 17,
            TimeConfidence.EXACT_PM,
        ),
        (
            "At an early hour,—twenty minutes past two o’clock in the morning, we left.",
            2 * 60 + 20,
            TimeConfidence.EXACT_CONTEXTUAL,
        ),
        ("At four thirty-seven p.m. he left.", 16 * 60 + 37, TimeConfidence.EXACT_PM),
    ],
)
def test_resolved_time_classes(text: str, minute: int, confidence: TimeConfidence) -> None:
    detection = _one(text)
    assert detection.minute_of_day == minute
    assert detection.confidence == confidence


@pytest.mark.parametrize(
    "text",
    [
        "At 4:37 he arrived.",
        "At four o'clock he arrived.",
        "It was a quarter to nine when he left.",
        "At four thirty-seven he left.",
    ],
)
def test_unqualified_twelve_hour_expressions_remain_ambiguous(text: str) -> None:
    detection = _one(text)
    assert detection.minute_of_day is None
    assert detection.confidence == TimeConfidence.AMPM_AMBIGUOUS


@pytest.mark.parametrize(
    ("text", "evidence"),
    [
        ("At a quarter past three he arrived.", "03:15|15:15"),
        ("At nineteen minutes past four he arrived.", "04:19|16:19"),
        ("At ten minutes to six he arrived.", "05:50|17:50"),
        ("At half past seven he arrived.", "07:30|19:30"),
    ],
)
def test_ambiguous_relative_evidence_uses_the_expressed_minute(text: str, evidence: str) -> None:
    detection = _one(text)
    assert detection.minute_of_day is None
    assert detection.confidence == TimeConfidence.AMPM_AMBIGUOUS
    assert detection.ampm_evidence is not None
    assert detection.ampm_evidence.endswith(evidence)


@pytest.mark.parametrize(
    "text",
    [
        "It was about five.",
        "It was nearly six.",
        "It was around half past four.",
        "He came sometime after three.",
        "The office opens before seven.",
        "It was shortly after midnight.",
        "It was now past noon.",
        "They arrived before midnight.",
        "By noon the work was finished.",
        "Long past midnight, the lamp still burned.",
        "Noon had passed before they left.",
        "Towards five o'clock that evening, they returned.",
        "It was close upon one o'clock in the morning.",
        "As early as four o'clock in the afternoon, she began.",
        "The work would be complete by three o'clock that afternoon.",
    ],
)
def test_approximate_expressions_are_never_assigned_a_minute(text: str) -> None:
    detection = _one(text)
    assert detection.minute_of_day is None
    assert detection.confidence == TimeConfidence.APPROXIMATE


@pytest.mark.parametrize(
    "text",
    [
        "From 4:30 to 5:00 p.m. they waited.",
        "Between four and five o'clock they waited.",
    ],
)
def test_ranges_are_preserved_for_review_without_resolution(text: str) -> None:
    detection = _one(text)
    assert detection.minute_of_day is None
    assert detection.confidence == TimeConfidence.RANGE


def test_written_endpoint_of_mixed_time_range_is_not_auto_resolved() -> None:
    detection = _one(
        "The speech ran from two-thirty-seven P. M. (Greenwich) until "
        "three-forty-six P. M. (Greenwich)."
    )
    assert detection.minute_of_day is None
    assert detection.confidence == TimeConfidence.RANGE


def test_narrative_start_and_end_times_are_both_ranges() -> None:
    detections = detect_time_expressions(
        "The fourth act began at half-past four and ended at twelve minutes before five."
    )
    assert len(detections) == 2
    assert all(detection.minute_of_day is None for detection in detections)
    assert all(detection.confidence == TimeConfidence.RANGE for detection in detections)


def test_something_like_is_an_approximate_modifier() -> None:
    detection = _one("He was born at something like twenty minutes past three in the morning.")
    assert detection.minute_of_day is None
    assert detection.confidence == TimeConfidence.APPROXIMATE


@pytest.mark.parametrize(
    "text",
    [
        "They arrived between five and a quarter past five in the morning.",
        "The clock was fixed at three or four minutes past noon.",
    ],
)
def test_ranges_ending_in_relative_expressions_are_not_auto_resolved(text: str) -> None:
    detection = _one(text)
    assert detection.minute_of_day is None
    assert detection.confidence == TimeConfidence.RANGE


def test_compound_hour_and_minute_offset_is_preserved_for_review() -> None:
    detection = _one("It was one hour and one minute after midnight.")
    assert detection.minute_of_day is None
    assert detection.confidence == TimeConfidence.APPROXIMATE


def test_uncertain_numeric_suffix_is_not_auto_resolved() -> None:
    detections = detect_time_expressions(
        "I put it at 11:44 p.m. at the latest, or perhaps 11:43 p.m."
    )
    assert len(detections) == 2
    assert all(detection.minute_of_day is None for detection in detections)
    assert all(detection.confidence == TimeConfidence.RANGE for detection in detections)


@pytest.mark.parametrize(
    "text",
    [
        "We read in John 15:13 that greater love hath no man.",
        "The words appear in Matthew 21:31.",
        "Compare 14:20⁠–⁠21 for the full passage.",
        "21:18⁠–⁠21. Cursed be he that setteth light by his father.",
        "According to the text (Rev. 20:13⁠–⁠14), all were judged.",
    ],
)
def test_scripture_references_are_not_interpreted_as_24_hour_times(text: str) -> None:
    detections = detect_time_expressions(text)
    assert all(detection.minute_of_day is None for detection in detections)


def test_24_hour_numeric_with_display_cue_is_exact() -> None:
    detection = _one("The panel showed 16:37 before going dark.")
    assert detection.minute_of_day == 16 * 60 + 37
    assert detection.confidence == TimeConfidence.EXACT_24H


def test_sentence_final_colon_time_is_detected() -> None:
    detection = _one("The clock showed 12:16.")
    assert detection.text == "12:16"
    assert detection.minute_of_day is None
    assert detection.confidence == TimeConfidence.AMPM_AMBIGUOUS


def test_written_clock_supports_oh_minutes() -> None:
    detection = _one("At one oh five, the bell rang.")
    assert detection.text == "one oh five"
    assert detection.minute_of_day is None
    assert detection.confidence == TimeConfidence.AMPM_AMBIGUOUS


@pytest.mark.parametrize(
    "text",
    [
        "There was one five-minute interval.",
        "There were two ten-minute pauses.",
        "There were three twenty-minute delays.",
    ],
)
def test_written_clock_does_not_cross_into_hyphenated_duration(text: str) -> None:
    detection = _one(text)
    assert detection.minute_of_day is None
    assert detection.confidence == TimeConfidence.INVALID
    assert detection.rejection_reason == "hyphenated duration is not a clock time"


def test_sports_score_is_not_a_relative_clock_time() -> None:
    detection = _one("That afternoon they had us three to one in the ninth inning.")
    assert detection.minute_of_day is None
    assert detection.confidence == TimeConfidence.INVALID
    assert detection.rejection_reason == "false_positive:SCORE_RATIO"


def test_approximate_military_time_is_not_exact() -> None:
    detection = _one("A date here starts around 2100 hours earliest.")
    assert detection.minute_of_day is None
    assert detection.confidence in {TimeConfidence.APPROXIMATE, TimeConfidence.RANGE}


def test_give_or_take_tolerance_is_approximate() -> None:
    detection = _one("At 22:10, give or take a couple of minutes, the guard heard shots.")
    assert detection.minute_of_day is None
    assert detection.confidence == TimeConfidence.APPROXIMATE


def test_invalid_numeric_time_is_classified() -> None:
    detection = _one("At 25:72 the broken clock stopped.")
    assert detection.minute_of_day is None
    assert detection.confidence == TimeConfidence.INVALID


@pytest.mark.parametrize(
    "text",
    [
        "Version 4.37 changed the ratio.",
        "Midnight blue suited her.",
        "He counted four thirty-seven coins.",
        "They met after seven generations had passed.",
        "She chose four oaks by the road.",
        "In some dialects, half four is used.",
    ],
)
def test_adversarial_non_times_are_not_detected(text: str) -> None:
    assert detect_time_expressions(text) == []


def test_numeric_measurement_range_is_not_a_relative_time() -> None:
    assert detect_time_expressions("The cliffs rose from 15 to 20 ft. above the path.") == []


@pytest.mark.parametrize(
    "text",
    [
        "The fast lasted from 12 hours 5 minutes to 16 hours 14 minutes.",
        "The maps varied from 23 degrees 6 minutes to 22 degrees 34 minutes.",
        "The river carried 28 million cubic feet a minute to 18 million elsewhere.",
    ],
)
def test_duration_and_measurement_phrases_are_not_clock_times(text: str) -> None:
    detection = _one(text)
    assert detection.minute_of_day is None
    assert detection.confidence == TimeConfidence.INVALID
    assert detection.rejection_reason == "false_positive:DIMENSION_RATIO"


def test_measurement_prefix_is_not_a_clock_relative() -> None:
    detections = detect_time_expressions(
        'The translated expression was "two feet before noon, or a foot and a quarter after noon."'
    )
    detection = next(item for item in detections if "quarter" in item.text)
    assert detection.minute_of_day is None
    assert detection.confidence == TimeConfidence.INVALID
    assert detection.rejection_reason == "false_positive:DIMENSION_RATIO"


@pytest.mark.parametrize(
    "text",
    [
        "They pledged the hour of seven to eight in the evening.",
        "Let me recommend six to eight in the morning.",
        "The hours were from seven to ten in the forenoon, one to four in the afternoon.",
        "The dog watches ran four to six and six to eight in the evening.",
        "He worked from ten to twelve o’clock in the morning and two to four in the afternoon.",
        "They talked through the dog-watch, six to eight o'clock in the evening.",
    ],
)
def test_bare_written_intervals_are_ranges(text: str) -> None:
    detections = detect_time_expressions(text)
    assert detections
    assert all(detection.minute_of_day is None for detection in detections)
    assert all(detection.confidence == TimeConfidence.RANGE for detection in detections)


def test_bare_one_after_without_direct_clock_cue_stays_unresolved() -> None:
    detection = _one("The actor was a very busy one after eight at night.")
    assert detection.minute_of_day is None
    assert detection.confidence == TimeConfidence.APPROXIMATE


def test_distant_betting_context_rejects_written_odds() -> None:
    detection = _one(
        "They counted the bets that would be settled and got down a thousand "
        "that afternoon at the Iroquois at four to one."
    )
    assert detection.minute_of_day is None
    assert detection.confidence == TimeConfidence.INVALID
    assert detection.rejection_reason == "false_positive:SCORE_RATIO"


def test_compound_approximation_is_not_an_exact_relative_time() -> None:
    detection = _one(
        "About half an hour and fourteen minutes after three in the afternoon, they returned."
    )
    assert detection.minute_of_day is None
    assert detection.confidence == TimeConfidence.APPROXIMATE


def test_named_base_relative_without_minutes_is_not_auto_resolved() -> None:
    detection = _one("Dinner was served by eleven before noon.")
    assert detection.minute_of_day is None
    assert detection.confidence in {
        TimeConfidence.AMPM_AMBIGUOUS,
        TimeConfidence.APPROXIMATE,
    }


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("The question appears in Matt. 5:47).", "CHAPTER_VERSE"),
        ("The teaching is quoted at (2 Pet. 2:19).", "CHAPTER_VERSE"),
        ('12:26: "If Satan cast out Satan, his kingdom is divided."', "CHAPTER_VERSE"),
        ("(See Bolte and Polívka 1:39).", "PAGE_LINE_REFERENCE"),
        ("See Techniques, supra, at 6-9.", "PAGE_LINE_REFERENCE"),
        ("The best previous time for the distance was 1:51 by Stevens.", "SCORE_RATIO"),
        (
            'The argument continues according to Mat. 6:24, "No man can serve two masters."',
            "CHAPTER_VERSE",
        ),
        ("The story cites a fiery sword (3:24, Genesis) before continuing.", "CHAPTER_VERSE"),
        ("The rider turned the trick in 1:39 1/5, a new record.", "SCORE_RATIO"),
        ("The chart records Nouv. Journ. Phys. 1:38, fig. 10.", "PAGE_LINE_REFERENCE"),
        ("It is twenty-six to one that the satellite has this position.", "SCORE_RATIO"),
    ],
)
def test_real_wikisource_reference_false_positives(text: str, category: str) -> None:
    detection = _one(text)
    assert detection.minute_of_day is None
    assert detection.confidence == TimeConfidence.INVALID
    assert detection.rejection_reason == f"false_positive:{category}"


def test_vote_ratio_is_not_a_clock_time() -> None:
    detections = detect_time_expressions(
        "When the vote was taken, it was twelve to six, or two to one, in favor."
    )
    assert detections
    assert all(detection.minute_of_day is None for detection in detections)
    assert all(
        detection.confidence in {TimeConfidence.INVALID, TimeConfidence.APPROXIMATE}
        for detection in detections
    )
