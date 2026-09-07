"""Deterministic semantic validation for highlighted English clock expressions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

SEMANTIC_AUDIT_VERSION = "english-clock-semantics-v1"


class SemanticClass(StrEnum):
    CLOCK_TIME_EXACT = "CLOCK_TIME_EXACT"
    CLOCK_TIME_AMBIGUOUS = "CLOCK_TIME_AMBIGUOUS"
    DURATION = "DURATION"
    RELATIVE_DURATION = "RELATIVE_DURATION"
    SECTION_OR_REFERENCE = "SECTION_OR_REFERENCE"
    HEADING_OR_TOC = "HEADING_OR_TOC"
    SCORE_OR_RESULT = "SCORE_OR_RESULT"
    RATIO_OR_MEASUREMENT = "RATIO_OR_MEASUREMENT"
    DATE_OR_NUMBER = "DATE_OR_NUMBER"
    NON_TEMPORAL_NUMBER = "NON_TEMPORAL_NUMBER"
    UNKNOWN = "UNKNOWN"


class SemanticAction(StrEnum):
    KEEP = "KEEP"
    QUARANTINE = "QUARANTINE"
    REVIEW = "REVIEW"


@dataclass(frozen=True, slots=True)
class SemanticDecision:
    semantic_class: SemanticClass
    action: SemanticAction
    reason_code: str
    parser_route: str
    confidence: str
    derived_minutes: tuple[int, ...] = ()


_NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
}
_NUMBER_TOKEN = (
    r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty(?:[ -]"
    r"(?:one|two|three|four|five|six|seven|eight|nine))?|thirty(?:[ -](?:one|two|"
    r"three|four|five|six|seven|eight|nine))?|forty(?:[ -](?:one|two|three|four|"
    r"five|six|seven|eight|nine))?|fifty(?:[ -](?:one|two|three|four|five|six|"
    r"seven|eight|nine))?|\d{1,2})"
)
_HOUR_TOKEN = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2})"
_HYPHENS = "-\u2010\u2011\u2012\u2013\u2014\u2015"
_MERIDIEM_RE = re.compile(r"(?<!\w)([ap])\s*\.?\s*m\s*\.?(?!\w)", re.I)
_COLON_RE = re.compile(r"(?<!\d)(?P<hour>\d{1,2}):(?P<minute>\d{2})(?!\d)", re.I)
_CLOCK_NUMERIC_RE = re.compile(
    r"(?<![\w.])(?P<hour>\d{1,2})(?P<separator>[:.])(?P<minute>\d{2})(?!\d)",
    re.I,
)
_HISTORICAL_DOT_RE = re.compile(
    r"(?<![\w.])(?P<hour>\d{1,2})\.(?P<minute>\d{1,2})"
    r"\s*(?P<meridiem>[ap])\s*\.?\s*m\s*\.?(?!\w)",
    re.I,
)
_MILITARY_RE = re.compile(r"(?<!\d)(?P<hour>\d{2})(?P<minute>\d{2})\s+hours?\b", re.I)
_COMPACT_24H_RE = re.compile(r"^(?P<hour>\d{2})(?P<minute>\d{2})(?:\s*(?:h|hrs?\.?))?$", re.I)
_COMPACT_12H_RE = re.compile(
    r"^(?P<hour>\d{1,2})(?P<minute>\d{2})\s*(?P<meridiem>[ap])\s*\.?\s*m\s*\.?$",
    re.I,
)
_EUROPEAN_24H_RE = re.compile(r"^(?P<hour>\d{1,2})h(?P<minute>\d{2})$", re.I)
_HOUR_MINUTE_UNITS_RE = re.compile(
    r"^(?P<hour>\d{1,2})\s*(?:hours?|hrs?|hr)\s*[,;]?\s*(?:and\s*)?"
    r"(?P<minute>\d{1,2})\s*(?:minutes?|mins?|m)"
    r"(?:\s*[,;]?\s*\d{1,2}\s*seconds?)?"
    r"(?:\s*(?P<meridiem>[ap])\s*\.?\s*m\s*\.?)?$",
    re.I,
)
_OCLOCK_RE = re.compile(rf"\b(?P<hour>{_HOUR_TOKEN})\s+o[’']clock\b", re.I)
_SPACED_OCLOCK_RE = re.compile(rf"\b(?P<hour>{_HOUR_TOKEN})[- ]+o[’']\s*clock\b", re.I)
_STRIKE_RE = re.compile(
    rf"\b(?:(?:clock|clocks|bells?)\s+(?:struck|strike|strikes|were striking)|"
    rf"struck)\s+(?P<hour>{_HOUR_TOKEN}|thirteen)\b",
    re.I,
)
_CLOCK_HOUR_RE = re.compile(rf"\b(?P<hour>{_HOUR_TOKEN})\s+(?:on|of)\s+the\s+clock\b", re.I)
_RELATIVE_RE = re.compile(
    rf"\b(?P<amount>a\s+quarter|quarter|half|{_NUMBER_TOKEN})"
    rf"(?:\s+minutes?)?[ {re.escape(_HYPHENS)}]+"
    rf"(?P<direction>past|after|to|before|till|of)\s+"
    rf"(?P<base>midnight|noon|{_HOUR_TOKEN})(?:\s+o[’']clock)?\b",
    re.I,
)
_WRITTEN_RE = re.compile(
    rf"\b(?P<hour>{_HOUR_TOKEN})[ {re.escape(_HYPHENS)}]+"
    rf"(?P<minute>(?:oh[ {re.escape(_HYPHENS)}]+)?{_NUMBER_TOKEN})\b",
    re.I,
)
_NAMED_RE = re.compile(r"\b(midnight|midday|noon)\b", re.I)
_BARE_HOUR_RE = re.compile(rf"\b(?P<hour>{_HOUR_TOKEN})\b", re.I)
_QUALIFIED_BARE_RE = re.compile(
    rf"^(?:(?:at|by|around|about|approximately|near|before|exactly|almost|almost at)\s+)?"
    rf"(?P<hour>{_HOUR_TOKEN})"
    r"(?:\s*(?:[ap]\s*\.?\s*m\s*\.?|in the morning|that morning|this morning|"
    r"in the afternoon|that afternoon|this afternoon|in the evening|that evening|"
    r"this evening|at night|that night|tonight|in the morn\.?|sharp))?$",
    re.I,
)
_DURATION_NUMBER = rf"(?:an?|half|{_NUMBER_TOKEN})"
_FULL_DURATION_RE = re.compile(
    rf"^(?:about\s+|approximately\s+|nearly\s+|almost\s+)?{_DURATION_NUMBER}\s+"
    rf"(?:hours?|minutes?)"
    rf"(?:\s+and\s+{_NUMBER_TOKEN}\s+minutes?)?"
    r"(?:\s+(?:later|earlier|elapsed|in duration))?$",
    re.I,
)

_SCRIPTURE = (
    r"genesis|exodus|leviticus|num|deuteronomy|joshua|judges|ruth|samuel|"
    r"kings|chronicles|ezra|nehemiah|esther|job|psalms?|proverbs|ecclesiastes|"
    r"canticles|isaiah|jeremiah|lamentations|ezekiel|daniel|hosea|joel|amos|"
    r"obadiah|jonah|micah|nahum|habakkuk|zephaniah|haggai|zechariah|malachi|"
    r"matthew|mark|luke|john|acts|romans|corinthians|galatians|ephesians|"
    r"philippians|colossians|thessalonians|timothy|titus|philemon|hebrews|"
    r"james|peter|jude|revelation|rev|matt|mt|mk|lk|jn|rom|cor|gal|eph|phil|"
    r"col|thess|tim|tit|heb|jas|pet|ps"
)
_STRUCTURE_RE = re.compile(
    r"^(?:table of contents|contents|index|bibliography|references|footnotes?|endnotes?|"
    r"list of (?:figures|tables|illustrations))\b",
    re.I,
)


def _normalize(value: str) -> str:
    value = value.casefold().replace("’", "'")
    value = re.sub(f"[{re.escape(_HYPHENS)}]", "-", value)
    return " ".join(value.split())


def _equivalent_display_text(left: str, right: str) -> bool:
    def comparable(value: str) -> str:
        value = _normalize(value)
        value = re.sub(r"\s+([.,;:!?])", r"\1", value)
        return value.rstrip(".")

    return comparable(left) == comparable(right)


def _number(value: str) -> int | None:
    value = _normalize(value).replace("-", " ")
    parts = value.split()
    if parts and parts[0] == "oh":
        parts = parts[1:]
    if len(parts) == 1:
        if parts[0].isdigit():
            return int(parts[0])
        return _NUMBER_WORDS.get(parts[0])
    if len(parts) == 2 and parts[0] in {"twenty", "thirty", "forty", "fifty"}:
        ones = _NUMBER_WORDS.get(parts[1])
        return _NUMBER_WORDS[parts[0]] + ones if ones is not None and ones < 10 else None
    return None


def _clockface_minutes(hour: int, minute: int) -> tuple[int, int]:
    am = (hour % 12) * 60 + minute
    return am, am + 720


def _explicit_or_context_minutes(
    hour: int,
    minute: int,
    phrase: str,
    before: str,
    after: str,
) -> tuple[int, ...]:
    meridian = _MERIDIEM_RE.search(phrase)
    if meridian:
        if not 1 <= hour <= 12:
            return ()
        base = (hour % 12) * 60 + minute
        return (base + (720 if meridian.group(1).casefold() == "p" else 0),)
    if hour == 0 or hour > 12:
        return (hour * 60 + minute,) if hour <= 23 else ()

    before_tail = before[-100:]
    after_head = after[:100]

    def adjacent_daypart(pattern: str) -> bool:
        return bool(
            re.search(rf"\b(?:{pattern})\b[^.!?]{{0,35}}$", before_tail, re.I)
            or re.search(
                rf"\b(?:{pattern})\b\s*[.!?]\s*(?:it\s+(?:was|is)\s*)?$",
                before_tail,
                re.I,
            )
            or re.match(rf"^[^.!?]{{0,40}}\b(?:{pattern})\b", after_head, re.I)
        )

    am = adjacent_daypart(r"morning|before dawn|after midnight|breakfast")
    pm = adjacent_daypart(r"afternoon|evening|after noon|lunch|dinner|sunset")
    night = adjacent_daypart(r"at night|that night|tonight|night")
    if night:
        if hour == 12 or 1 <= hour <= 4:
            am = True
        elif 6 <= hour <= 11:
            pm = True
    if am == pm:
        return _clockface_minutes(hour, minute)
    base = (hour % 12) * 60 + minute
    return (base + (720 if pm else 0),)


def _clock_context(before: str, after: str, phrase: str) -> bool:
    immediate = before[-100:]
    if re.search(
        r"\b(?:at|by|until|from|since|around|about|approximately|nearly|almost|"
        r"exactly|precisely|was|is|read|reads|said|says|showed|shows|displayed|"
        r"indicated|registered|struck|scheduled|timed)\s*[:=-]?\s*$",
        immediate,
        re.I,
    ):
        return True
    if re.search(
        r"\b(?:clock|watch|chronometer|timer|time|timestamp|display|alarm|dial|hands?)\b"
        r"[^.!?]{0,65}$",
        immediate,
        re.I,
    ):
        return True
    if re.match(
        r"[^.!?]{0,65}\b(?:clock|watch|chronometer|timer|time|timestamp|display|alarm|dial)\b",
        after,
        re.I,
    ):
        return True
    if re.match(
        r"\s*(?::\d{2})?\s*(?:[ap]\s*\.?\s*m\.?|hours?\b|gmt\b|cet\b|est\b|"
        r"local time\b|shiptime\b|in the morning\b|that morning\b|this morning\b|"
        r"in the afternoon\b|that afternoon\b|in the evening\b|that evening\b|at night\b)",
        after,
        re.I,
    ):
        return True
    if re.search(
        r"\b(?:train|bus|flight|departure|arrival|timetable|time-table)\b", before[-140:], re.I
    ):
        return True
    if re.match(r"\s+(?:train|bus|flight)\b", after, re.I):
        return True
    return bool(_MERIDIEM_RE.search(phrase))


def _negative_decision(
    quote: str,
    phrase: str,
    start: int,
    end: int,
    *,
    source_section: str | None,
    source_locator: str | None,
    parser_route: str,
) -> SemanticDecision | None:
    before = quote[max(0, start - 180) : start]
    after = quote[end : min(len(quote), end + 180)]
    structure = " ".join(part for part in (source_section, source_locator) if part)
    if structure and _STRUCTURE_RE.search(structure):
        return SemanticDecision(
            SemanticClass.HEADING_OR_TOC,
            SemanticAction.QUARANTINE,
            "STRUCTURE_TOC",
            parser_route,
            "HIGH",
        )

    if re.search(r"\b(?:minutes?|hours?)\s+(?:later|earlier)\b", phrase, re.I) or re.search(
        r"\b(?:minutes?|hours?)\s+(?:after|before)(?:\s+(?:that|then|the event))?\s*$",
        phrase,
        re.I,
    ):
        return SemanticDecision(
            SemanticClass.RELATIVE_DURATION,
            SemanticAction.QUARANTINE,
            "RELATIVE_LATER",
            parser_route,
            "HIGH",
        )
    full_duration = _FULL_DURATION_RE.fullmatch(_normalize(phrase))
    unit_clock = _HOUR_MINUTE_UNITS_RE.fullmatch(_normalize(phrase))
    if full_duration and not (unit_clock and _clock_context(before, after, phrase)):
        relative = bool(re.search(r"\b(?:later|earlier)\s*$", phrase, re.I))
        return SemanticDecision(
            SemanticClass.RELATIVE_DURATION if relative else SemanticClass.DURATION,
            SemanticAction.QUARANTINE,
            "RELATIVE_LATER" if relative else "DURATION_ELAPSED_UNITS",
            parser_route,
            "HIGH",
        )
    if re.search(r"^(?:in|for|during|within|after)\s+.*\b(?:minutes?|hours?)\b", phrase, re.I):
        return SemanticDecision(
            SemanticClass.RELATIVE_DURATION,
            SemanticAction.QUARANTINE,
            "RELATIVE_DURATION_PHRASE",
            parser_route,
            "HIGH",
        )
    if re.match(rf"\s*[{re.escape(_HYPHENS)}]\s*(?:minutes?|hours?)\b", after, re.I):
        return SemanticDecision(
            SemanticClass.DURATION,
            SemanticAction.QUARANTINE,
            "DURATION_HYPHENATED",
            parser_route,
            "HIGH",
        )
    numeric_hours = bool(
        re.fullmatch(r"\s*(?:\d{3,4}|\d{1,2}[.:]\d{1,2})\s*", phrase)
        and re.match(r"\s+hours?\b", after, re.I)
    )
    if (
        re.match(r"\s+(?:minutes?|hours?)\b", after, re.I)
        and not numeric_hours
        and not re.search(r"\b(?:past|after|to|before)\b|:|o['’]clock", phrase, re.I)
    ):
        if re.match(r"\s+(?:minutes?|hours?)\s+(?:later|earlier|after|before)\b", after, re.I):
            reason = "RELATIVE_LATER"
            semantic_class = SemanticClass.RELATIVE_DURATION
        elif re.search(
            r"\b(?:for|during|within|waited|lasted|spent|interval|pause|delay)\b", before, re.I
        ):
            reason = "DURATION_FOR_MINUTES"
            semantic_class = SemanticClass.DURATION
        else:
            reason = "DURATION_MINUTE_NOUN"
            semantic_class = SemanticClass.DURATION
        return SemanticDecision(
            semantic_class,
            SemanticAction.QUARANTINE,
            reason,
            parser_route,
            "HIGH",
        )

    colon = _COLON_RE.search(phrase)
    if not colon:
        return None
    if re.search(r"\b(?:score|scored|result)\s*(?:was|is|of|:)?\s*$", before, re.I):
        return SemanticDecision(
            SemanticClass.SCORE_OR_RESULT,
            SemanticAction.QUARANTINE,
            "SCORE_CONTEXT",
            parser_route,
            "HIGH",
        )
    if re.search(r"\b(?:ratio|odds|proportion|scale)\s*(?:was|is|of|:)?\s*$", before, re.I):
        return SemanticDecision(
            SemanticClass.RATIO_OR_MEASUREMENT,
            SemanticAction.QUARANTINE,
            "RATIO_CONTEXT",
            parser_route,
            "HIGH",
        )
    if re.match(r"\s+\d+\s*/\s*\d+", after):
        return SemanticDecision(
            SemanticClass.SCORE_OR_RESULT,
            SemanticAction.QUARANTINE,
            "SCORE_CONTEXT",
            parser_route,
            "HIGH",
        )
    if re.search(
        r"\b(?:timecode|catalog(?:ue)?|serial|model|item|code|reference|ref\.?|number|no\.?)\s*[:#=-]?\s*$",
        before,
        re.I,
    ):
        return SemanticDecision(
            SemanticClass.DATE_OR_NUMBER,
            SemanticAction.QUARANTINE,
            "NON_TEMPORAL_IDENTIFIER",
            parser_route,
            "HIGH",
        )
    if re.search(r"\b(?:chapter|chap\.?)\s*(?:[ivxlcdm\d]+\s*)?$", before, re.I):
        return SemanticDecision(
            SemanticClass.SECTION_OR_REFERENCE,
            SemanticAction.QUARANTINE,
            "REFERENCE_CHAPTER",
            parser_route,
            "HIGH",
        )
    if re.search(
        r"\b(?:section|article|subsection|clause|statute|§)\s*(?:[ivxlcdm\d]+\s*)?$", before, re.I
    ):
        return SemanticDecision(
            SemanticClass.SECTION_OR_REFERENCE,
            SemanticAction.QUARANTINE,
            "REFERENCE_SECTION",
            parser_route,
            "HIGH",
        )
    if re.search(r"\b(?:act|scene|book|verse)\s*(?:[ivxlcdm\d]+\s*)?$", before, re.I):
        return SemanticDecision(
            SemanticClass.SECTION_OR_REFERENCE,
            SemanticAction.QUARANTINE,
            "REFERENCE_CHAPTER",
            parser_route,
            "HIGH",
        )
    if re.search(rf"\b(?:[1-3]\s+)?(?:{_SCRIPTURE})\.?\s*$", before, re.I) or re.match(
        rf"\s*[,;)]?\s*(?:[1-3]\s+)?(?:{_SCRIPTURE})\.?\s+\d", after, re.I
    ):
        return SemanticDecision(
            SemanticClass.SECTION_OR_REFERENCE,
            SemanticAction.QUARANTINE,
            "REFERENCE_SCRIPTURE",
            parser_route,
            "HIGH",
        )
    if re.search(r"\b(?:pages?|pp\.?|lines?|ll\.?|figures?|fig\.?|tables?)\s*$", before, re.I):
        return SemanticDecision(
            SemanticClass.SECTION_OR_REFERENCE,
            SemanticAction.QUARANTINE,
            "REFERENCE_PAGE_LINE",
            parser_route,
            "HIGH",
        )
    if re.search(r"\brefers?\s+to\s+[\"“]?\s*$", before, re.I) or re.search(
        r"\brefers?\s+to\s+[\"“]?(?:book|chapter|hymn|section)\b", after, re.I
    ):
        return SemanticDecision(
            SemanticClass.SECTION_OR_REFERENCE,
            SemanticAction.QUARANTINE,
            "REFERENCE_EXPLICIT",
            parser_route,
            "HIGH",
        )
    if re.match(r"\s*[-–—]\s*\d+\b", after) and not _clock_context(before, after, phrase):
        return SemanticDecision(
            SemanticClass.SECTION_OR_REFERENCE,
            SemanticAction.QUARANTINE,
            "REFERENCE_RANGE",
            parser_route,
            "HIGH",
        )
    if re.search(
        r"\b(?:ratio|proportions?|scale)\s*(?:was|is|of|:|=)?\s*$", before, re.I
    ) or re.match(
        r"\s*(?:=|(?:inches?|feet|centimet(?:er|re)s?|millimet(?:er|re)s?|gait)\b)",
        after,
        re.I,
    ):
        return SemanticDecision(
            SemanticClass.RATIO_OR_MEASUREMENT,
            SemanticAction.QUARANTINE,
            "MEASUREMENT_CONTEXT",
            parser_route,
            "HIGH",
        )
    return None


def _finish_clock(
    *,
    minutes: tuple[int, ...],
    claimed_minute: int,
    parser_route: str,
    reason_code: str,
) -> SemanticDecision:
    if not minutes or claimed_minute not in minutes:
        return SemanticDecision(
            SemanticClass.CLOCK_TIME_EXACT
            if len(minutes) == 1
            else SemanticClass.CLOCK_TIME_AMBIGUOUS,
            SemanticAction.QUARANTINE,
            "HIGHLIGHT_SEMANTIC_MISMATCH",
            parser_route,
            "HIGH",
            minutes,
        )
    return SemanticDecision(
        SemanticClass.CLOCK_TIME_EXACT if len(minutes) == 1 else SemanticClass.CLOCK_TIME_AMBIGUOUS,
        SemanticAction.KEEP,
        reason_code,
        parser_route,
        "HIGH" if len(minutes) == 1 else "MEDIUM",
        minutes,
    )


def classify_clock_relationship(
    quote: str,
    highlight_start: int | None,
    highlight_end: int | None,
    claimed_minute: int,
    *,
    expected_text: str | None = None,
    parser_route: str = "unknown",
    source_section: str | None = None,
    source_locator: str | None = None,
) -> SemanticDecision:
    """Classify one quote/minute relationship without modifying source text or offsets."""
    if (
        highlight_start is None
        or highlight_end is None
        or highlight_start < 0
        or highlight_end <= highlight_start
        or highlight_end > len(quote)
    ):
        return SemanticDecision(
            SemanticClass.UNKNOWN,
            SemanticAction.QUARANTINE,
            "HIGHLIGHT_OFFSET_INVALID",
            parser_route,
            "HIGH",
        )
    phrase = quote[highlight_start:highlight_end]
    if expected_text is not None and not _equivalent_display_text(phrase, expected_text):
        return SemanticDecision(
            SemanticClass.UNKNOWN,
            SemanticAction.QUARANTINE,
            "HIGHLIGHT_TEXT_MISMATCH",
            parser_route,
            "HIGH",
        )
    before = quote[:highlight_start]
    after = quote[highlight_end:]
    negative = _negative_decision(
        quote,
        phrase,
        highlight_start,
        highlight_end,
        source_section=source_section,
        source_locator=source_locator,
        parser_route=parser_route,
    )
    if negative:
        return negative
    normalized_phrase = _normalize(phrase)

    compact = _COMPACT_24H_RE.fullmatch(normalized_phrase)
    if compact:
        hour, minute = int(compact.group("hour")), int(compact.group("minute"))
        minutes = (hour * 60 + minute,) if hour <= 23 and minute <= 59 else ()
        has_clock_suffix = bool(re.search(r"(?:h|hrs?\.?)$", normalized_phrase, re.I))
        if not has_clock_suffix and not _clock_context(before, after, phrase):
            return SemanticDecision(
                SemanticClass.UNKNOWN,
                SemanticAction.REVIEW,
                "COMPACT_NUMERIC_WITHOUT_TIME_EVIDENCE",
                parser_route,
                "LOW",
                minutes,
            )
        return _finish_clock(
            minutes=minutes,
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_COMPACT_24H",
        )

    compact_12h = _COMPACT_12H_RE.fullmatch(normalized_phrase)
    if compact_12h:
        hour, minute = int(compact_12h.group("hour")), int(compact_12h.group("minute"))
        base = (hour % 12) * 60 + minute
        minutes = (
            (base + (720 if compact_12h.group("meridiem").casefold() == "p" else 0),)
            if 1 <= hour <= 12 and minute <= 59
            else ()
        )
        return _finish_clock(
            minutes=minutes,
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_COMPACT_12H",
        )

    european = _EUROPEAN_24H_RE.fullmatch(normalized_phrase)
    if european:
        hour, minute = int(european.group("hour")), int(european.group("minute"))
        minutes = (hour * 60 + minute,) if hour <= 23 and minute <= 59 else ()
        return _finish_clock(
            minutes=minutes,
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_EUROPEAN_24H",
        )

    unit_clock = _HOUR_MINUTE_UNITS_RE.fullmatch(normalized_phrase)
    if unit_clock and (
        re.search(r"\b(?:clock time|time)\b[^.!?]{0,30}$", before, re.I)
        or _clock_context(before, after, phrase)
    ):
        hour, minute = int(unit_clock.group("hour")), int(unit_clock.group("minute"))
        meridiem = unit_clock.group("meridiem")
        if meridiem:
            base = (hour % 12) * 60 + minute
            minutes = (base + (720 if meridiem.casefold() == "p" else 0),)
        else:
            minutes = (hour * 60 + minute,) if hour <= 23 and minute <= 59 else ()
        return _finish_clock(
            minutes=minutes,
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_HOUR_MINUTE_UNITS",
        )

    historical_dot = _HISTORICAL_DOT_RE.search(phrase)
    if historical_dot:
        hour = int(historical_dot.group("hour"))
        minute = int(historical_dot.group("minute"))
        base = (hour % 12) * 60 + minute
        minutes = (
            (base + (720 if historical_dot.group("meridiem").casefold() == "p" else 0),)
            if 1 <= hour <= 12 and minute <= 59
            else ()
        )
        return _finish_clock(
            minutes=minutes,
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_HISTORICAL_DOT",
        )

    military = _MILITARY_RE.search(phrase)
    if military:
        hour, minute = int(military.group("hour")), int(military.group("minute"))
        minutes = (hour * 60 + minute,) if hour <= 23 and minute <= 59 else ()
        return _finish_clock(
            minutes=minutes,
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_MILITARY",
        )

    numeric = _CLOCK_NUMERIC_RE.search(phrase)
    if numeric:
        hour, minute = int(numeric.group("hour")), int(numeric.group("minute"))
        if hour > 23 or minute > 59:
            return SemanticDecision(
                SemanticClass.NON_TEMPORAL_NUMBER,
                SemanticAction.QUARANTINE,
                "NUMERIC_OUTSIDE_CLOCK_RANGE",
                parser_route,
                "HIGH",
            )
        minutes = _explicit_or_context_minutes(hour, minute, phrase, before, after)
        if not _clock_context(before, after, phrase):
            return SemanticDecision(
                SemanticClass.UNKNOWN,
                SemanticAction.REVIEW,
                "COLON_WITHOUT_TIME_EVIDENCE",
                parser_route,
                "LOW",
                minutes,
            )
        return _finish_clock(
            minutes=minutes,
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_NUMERIC",
        )

    relative = _RELATIVE_RE.search(normalized_phrase)
    if relative:
        amount_text = relative.group("amount")
        amount = (
            15
            if "quarter" in amount_text
            else 30
            if amount_text == "half"
            else _number(amount_text)
        )
        base_text = relative.group("base")
        direction = relative.group("direction")
        if amount is None or not 1 <= amount <= 59:
            return SemanticDecision(
                SemanticClass.UNKNOWN,
                SemanticAction.REVIEW,
                "UNKNOWN_CONTEXT",
                parser_route,
                "LOW",
            )
        delta = amount if direction in {"past", "after"} else -amount
        if base_text in {"midnight", "noon"}:
            base = 0 if base_text == "midnight" else 720
            minutes = ((base + delta) % 1440,)
        else:
            base_hour = _number(base_text)
            if base_hour is None or not 1 <= base_hour <= 23:
                minutes = ()
            elif base_hour > 12:
                minutes = (base_hour * 60,)
            else:
                base_candidates = _explicit_or_context_minutes(base_hour, 0, phrase, before, after)
                minutes = tuple((base + delta) % 1440 for base in base_candidates)
        return _finish_clock(
            minutes=minutes,
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_RELATIVE",
        )

    oclock = _OCLOCK_RE.search(normalized_phrase) or _SPACED_OCLOCK_RE.search(normalized_phrase)
    if oclock:
        hour = _number(oclock.group("hour"))
        if hour is not None and 13 <= hour <= 23:
            minutes = (hour * 60,)
        else:
            minutes = (
                _explicit_or_context_minutes(hour, 0, phrase, before, after)
                if hour is not None and 1 <= hour <= 12
                else ()
            )
        return _finish_clock(
            minutes=minutes,
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_OCLOCK",
        )

    clock_hour = _CLOCK_HOUR_RE.search(normalized_phrase)
    if clock_hour:
        hour = _number(clock_hour.group("hour"))
        minutes = (
            _explicit_or_context_minutes(hour, 0, phrase, before, after)
            if hour is not None and 1 <= hour <= 12
            else ()
        )
        return _finish_clock(
            minutes=minutes,
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_CLOCK_HOUR",
        )

    strike = _STRIKE_RE.search(normalized_phrase)
    if strike:
        hour_text = strike.group("hour")
        hour = 13 if hour_text == "thirteen" else _number(hour_text)
        minutes = (
            (13 * 60,)
            if hour == 13
            else _explicit_or_context_minutes(hour, 0, phrase, before, after)
            if hour is not None and 1 <= hour <= 12
            else ()
        )
        return _finish_clock(
            minutes=minutes,
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_STRIKE",
        )

    named = _NAMED_RE.search(normalized_phrase)
    if named:
        minutes = (0,) if named.group(1) == "midnight" else (720,)
        return _finish_clock(
            minutes=minutes,
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_NAMED",
        )

    written = _WRITTEN_RE.search(normalized_phrase)
    if written:
        hour, minute = _number(written.group("hour")), _number(written.group("minute"))
        if hour is not None and minute is not None and 1 <= hour <= 12 and 0 <= minute <= 59:
            minutes = _explicit_or_context_minutes(hour, minute, phrase, before, after)
            if not _clock_context(before, after, phrase):
                return SemanticDecision(
                    SemanticClass.UNKNOWN,
                    SemanticAction.REVIEW,
                    "WRITTEN_WITHOUT_TIME_EVIDENCE",
                    parser_route,
                    "LOW",
                    minutes,
                )
            return _finish_clock(
                minutes=minutes,
                claimed_minute=claimed_minute,
                parser_route=parser_route,
                reason_code="CLOCK_GRAMMAR_WRITTEN",
            )

    qualified_bare = _QUALIFIED_BARE_RE.fullmatch(normalized_phrase)
    bare = qualified_bare or _BARE_HOUR_RE.fullmatch(normalized_phrase)
    if bare:
        hour = _number(bare.group("hour"))
        if hour is not None and 1 <= hour <= 12:
            minutes = _explicit_or_context_minutes(hour, 0, phrase, before, after)
            if _clock_context(before, after, phrase) or re.match(
                r"^(?:at|by|around|about|approximately|near|before)\b", normalized_phrase
            ):
                return _finish_clock(
                    minutes=minutes,
                    claimed_minute=claimed_minute,
                    parser_route=parser_route,
                    reason_code="CLOCK_GRAMMAR_BARE_CONTEXTUAL",
                )
            return SemanticDecision(
                SemanticClass.UNKNOWN,
                SemanticAction.REVIEW,
                "BARE_HOUR_WITHOUT_TIME_EVIDENCE",
                parser_route,
                "LOW",
                minutes,
            )

    return SemanticDecision(
        SemanticClass.UNKNOWN,
        SemanticAction.REVIEW,
        "UNKNOWN_CONTEXT",
        parser_route,
        "LOW",
    )
