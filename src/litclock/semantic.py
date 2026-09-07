"""Deterministic semantic validation for highlighted English clock expressions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

SEMANTIC_AUDIT_V1 = "english-clock-semantics-v1"
SEMANTIC_AUDIT_VERSION = "english-clock-semantics-v2"


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
    derivation_rule: str | None = None


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
_COMPOUND_NUMBER_TOKEN = (
    rf"(?:(?:one|two|three|four|five|six|seven|eight|nine)[ -]and[ -]"
    rf"(?:twenty|thirty|forty|fifty)|{_NUMBER_TOKEN})"
)
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
_COMPACT_24H_RE = re.compile(r"^(?P<hour>\d{2})(?P<minute>\d{2})(?:\s*(?:h\.?|hrs?\.?))?\.?$", re.I)
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
_OCLOCK_RE = re.compile(rf"\b(?P<hour>{_NUMBER_TOKEN})\s+o[’']clock\b", re.I)
_SPACED_OCLOCK_RE = re.compile(rf"\b(?P<hour>{_HOUR_TOKEN})[- ]+o[’']\s*clock\b", re.I)
_ARCHAIC_OCLOCK_RE = re.compile(rf"\b(?P<hour>{_HOUR_TOKEN})\s+(?:a[- ]?clocke?|clocke?)\b", re.I)
_STRIKE_RE = re.compile(
    rf"\b(?:(?:clock|clocks|bells?)\s+(?:struck|strike|strikes|was striking|were striking)|"
    rf"struck)\s+(?P<hour>{_HOUR_TOKEN}|thirteen)\b",
    re.I,
)
_CLOCK_HOUR_RE = re.compile(rf"\b(?P<hour>{_HOUR_TOKEN})\s+(?:on|of)\s+the\s+clock\b", re.I)
_RELATIVE_RE = re.compile(
    rf"\b(?P<amount>three\s+quarters|third\s+quarter|a\s+quarter|quarter|half|an?|"
    rf"{_COMPOUND_NUMBER_TOKEN})"
    rf"(?:\s+minutes?)?[ {re.escape(_HYPHENS)}]+"
    rf"(?P<direction>past|after|to|before|till|until|of)\s+"
    rf"(?P<base>midnight|midday|noon|{_NUMBER_TOKEN})(?:\s+o[’']clock)?\b",
    re.I,
)
_WRITTEN_OCLOCK_RE = re.compile(
    rf"\b(?P<hour>{_HOUR_TOKEN})[ {re.escape(_HYPHENS)}]+"
    rf"(?P<minute>(?:oh[ {re.escape(_HYPHENS)}]+)?{_NUMBER_TOKEN})\s+o[’']clock\b",
    re.I,
)
_OFFSET_FROM_WRITTEN_RE = re.compile(
    rf"\b(?P<amount>{_COMPOUND_NUMBER_TOKEN}|a)\s+minutes?\s+"
    rf"(?P<direction>short\s+of|before|to|after|past)\s+"
    rf"(?P<hour>{_HOUR_TOKEN})[ {re.escape(_HYPHENS)}]+"
    rf"(?P<minute>(?:oh[ {re.escape(_HYPHENS)}]+)?{_NUMBER_TOKEN})\b",
    re.I,
)
_HOURS_FROM_NAMED_RE = re.compile(
    rf"\b(?P<amount>{_COMPOUND_NUMBER_TOKEN}|an?|half)\s+hours?\s+"
    r"(?P<direction>ere|before|to|after|past)\s+(?P<base>noon|midday|midnight)\b",
    re.I,
)
_HOUR_AND_FRACTION_RE = re.compile(
    rf"\b(?P<hour>{_HOUR_TOKEN})\s+(?:hour\s+)?and\s+"
    r"(?P<fraction>a\s+quarter|quarter|a\s+half|half)\b",
    re.I,
)
_FRACTION_OCLOCK_RE = re.compile(
    rf"\b(?P<hour>{_HOUR_TOKEN})\s+(?P<numerator>1)\s*/\s*(?P<denominator>2)"
    r"\s+o[’']clock\b",
    re.I,
)
_NUMERIC_HALF_PAST_RE = re.compile(
    rf"\b1\s*/\s*2\s+past\s+(?P<hour>{_HOUR_TOKEN})(?:\s+o[’']clock)?\b",
    re.I,
)
_MINUTES_SECONDS_RELATIVE_RE = re.compile(
    rf"\b(?P<amount>{_COMPOUND_NUMBER_TOKEN}|an?|half)\s+minutes?"
    rf"(?:\s+and\s+(?P<seconds>{_NUMBER_TOKEN})\s+seconds?)?\s+"
    r"(?P<direction>past|after|to|before)\s+"
    rf"(?P<base>midnight|midday|noon|{_HOUR_TOKEN})(?:\s+o[’']clock)?\b",
    re.I,
)
_FRACTIONAL_MINUTES_RELATIVE_RE = re.compile(
    rf"\b(?P<amount>{_COMPOUND_NUMBER_TOKEN})\s+and\s+a\s+half\s+minutes?\s+"
    r"(?P<direction>past|after|to|before)\s+"
    rf"(?P<base>midnight|midday|noon|{_HOUR_TOKEN})(?:\s+o[’']clock)?\b",
    re.I,
)
_OFFSET_QUARTER_RE = re.compile(
    rf"\b(?P<amount>{_COMPOUND_NUMBER_TOKEN}|an?)\s+minutes?\s+"
    r"(?P<outer>after|past|before)\s+(?:the\s+)?quarter\s+"
    rf"(?P<inner>to|past|after)\s+(?P<base>{_HOUR_TOKEN})\b",
    re.I,
)
_NAMED_THEN_PAST_RE = re.compile(
    rf"\b(?P<base>midnight|midday|noon)\s*[.!?;:\-–—]+\s*"
    rf"(?P<amount>{_COMPOUND_NUMBER_TOKEN})\s+(?P<direction>past|after)\b",
    re.I,
)
_VERBOSE_TO_NAMED_RE = re.compile(
    rf"\b(?P<amount>{_COMPOUND_NUMBER_TOKEN}|an?)\s+minutes?\s*[,;]?"
    r"(?:\s+(?:hardly|scarcely)\s+more\s*[,]?)?\s+to\s+"
    r"(?:the\s+)?(?:awful\s+)?(?P<base>midnight|midday|noon)(?:\s+hour)?\b",
    re.I,
)
_CLOCK_STRUCK_RELATIVE_RE = re.compile(
    rf"\b(?P<amount>{_COMPOUND_NUMBER_TOKEN}|an?)\s+minutes?\s+"
    r"(?P<direction>before|after)\s+(?:the\s+)?clock\s+(?:had\s+)?struck\s+"
    rf"(?P<base>midnight|midday|noon|{_HOUR_TOKEN})\b",
    re.I,
)
_OCLOCK_PLUS_MINUTES_RE = re.compile(
    rf"\b(?P<hour>{_HOUR_TOKEN})\s+o[’']clock\s+(?:and\s+)?"
    rf"(?P<amount>{_COMPOUND_NUMBER_TOKEN})\s+minutes?"
    r"(?:\s+and\s+(?:a\s+)?quarter)?\b",
    re.I,
)
_OCLOCK_ALL_BUT_RE = re.compile(
    rf"\b(?P<hour>{_HOUR_TOKEN})\s+o[’']clock\s*[,]?\s+all\s+but\s+"
    rf"(?P<amount>{_COMPOUND_NUMBER_TOKEN})\s+minutes?\b",
    re.I,
)
_WRITTEN_RE = re.compile(
    rf"\b(?P<hour>{_HOUR_TOKEN})[ {re.escape(_HYPHENS)}]+"
    rf"(?P<minute>(?:oh[ {re.escape(_HYPHENS)}]+)?{_NUMBER_TOKEN})\b",
    re.I,
)
_WRITTEN_AND_RE = re.compile(
    rf"\b(?P<hour>{_HOUR_TOKEN})\s+and\s+(?P<minute>{_NUMBER_TOKEN})\b", re.I
)
_WRITTEN_24H_RE = re.compile(
    rf"\b(?P<hour>{_NUMBER_TOKEN})[ {re.escape(_HYPHENS)}]+"
    rf"(?P<minute>(?:oh[ {re.escape(_HYPHENS)}]+)?{_NUMBER_TOKEN})\b",
    re.I,
)
_WRITTEN_MILITARY_RE = re.compile(
    rf"\boh\s+(?P<hour>{_HOUR_TOKEN})\s+oh\s+(?P<minute>oh|{_NUMBER_TOKEN})\s+hours?\b",
    re.I,
)
_MINUTE_SHORT_OF_NUMERIC_RE = re.compile(
    r"\b(?:(?P<amount>an?|one|\d{1,2})\s+)?minutes?\s+short\s+of\s+"
    r"(?P<hour>\d{1,2})[:.](?P<minute>\d{2})\b",
    re.I,
)
_NESTED_BEFORE_RE = re.compile(
    rf"\b(?P<outer>{_NUMBER_TOKEN})\s+minutes?\s+before\s+"
    rf"(?P<inner>{_NUMBER_TOKEN})\s+minutes?\s+to\s+(?P<base>{_HOUR_TOKEN})\b",
    re.I,
)
_MINUTE_TO_NEW_DAY_RE = re.compile(
    r"\b(?:the\s+)?new\s+day\s+was\s+still\s+(?:a|one)\s+minute\s+away\b", re.I
)
_NAMED_RE = re.compile(r"\b(midnight|midday|noon)\b", re.I)
_BARE_HOUR_RE = re.compile(rf"\b(?P<hour>{_HOUR_TOKEN})\b", re.I)
_BARE_24H_RE = re.compile(rf"\b(?P<hour>{_NUMBER_TOKEN})\b", re.I)
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
    r"genesis|gen|exodus|leviticus|num|deuteronomy|joshua|judges|ruth|samuel|"
    r"kings|chronicles|ezra|nehemiah|esther|job|psalms?|proverbs|ecclesiastes|"
    r"canticles|isaiah|jeremiah|lamentations|ezekiel|daniel|hosea|joel|amos|"
    r"obadiah|jonah|micah|nahum|habakkuk|zephaniah|haggai|zechariah|malachi|"
    r"matthew|mark|luke|john|acts|romans|corinthians|galatians|ephesians|"
    r"philippians|colossians|thessalonians|timothy|titus|philemon|hebrews|"
    r"james|peter|jude|revelation|rev|matt|mt|mk|lk|jn|rom|roman|romans|cor|"
    r"gal|eph|phil|col|thess|tim|tit|heb|jas|pet|ps|prov|josue|iona|nalii|nephi|mosiah"
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
    if len(parts) == 3 and parts[1] == "and":
        left = _NUMBER_WORDS.get(parts[0])
        right = _NUMBER_WORDS.get(parts[2])
        if left is not None and right is not None and left % 10 == 0 and right < 10:
            return left + right
        if left is not None and right is not None and left < 10 and right % 10 == 0:
            return left + right
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
    normalized_phrase = _normalize(phrase)
    meridian = _MERIDIEM_RE.search(phrase)
    if meridian:
        if not 1 <= hour <= 12:
            return ()
        base = (hour % 12) * 60 + minute
        return (base + (720 if meridian.group(1).casefold() == "p" else 0),)
    dashed_meridian = re.match(r"\s*[-–—]+\s*([ap])\s*[-–—]+\s*m\b", after, re.I)
    if dashed_meridian:
        if not 1 <= hour <= 12:
            return ()
        base = (hour % 12) * 60 + minute
        return (base + (720 if dashed_meridian.group(1).casefold() == "p" else 0),)
    if hour == 0 or hour > 12:
        return (hour * 60 + minute,) if hour <= 23 else ()

    before_tail = before[-120:]
    after_head = after[:120]

    phrase_am = bool(
        re.search(
            r"\b(?:morning|morn\.?|before dawn|after midnight|breakfast)\b", normalized_phrase
        )
    )
    phrase_pm = bool(
        re.search(r"\b(?:afternoon|evening|after noon|lunch|dinner|sunset)\b", normalized_phrase)
    )
    phrase_night = bool(re.search(r"\b(?:night|tonight)\b", normalized_phrase))
    if phrase_night:
        if hour == 12 or 1 <= hour <= 4:
            phrase_am = True
        elif 6 <= hour <= 11:
            phrase_pm = True
    if phrase_am != phrase_pm:
        base = (hour % 12) * 60 + minute
        return (base + (720 if phrase_pm else 0),)

    def adjacent_daypart(pattern: str) -> bool:
        direct = bool(
            re.search(
                rf"\b(?:{pattern})\b(?:\s+of\s+[^.!?]{{0,18}})?[,;:]?\s*$",
                before_tail,
                re.I,
            )
            or re.search(
                rf"\b(?:{pattern})\b\s*[.!?]\s*(?:it\s+(?:was|is)\s*)?$",
                before_tail,
                re.I,
            )
            or re.match(
                rf"^\s*(?:[,;:\-–—]|\b(?:on|in|of|that|this|the|a|an|to-?morrow|"
                rf"tomorrow|yesterday|following|next|friday|saturday|sunday|monday|"
                rf"tuesday|wednesday|thursday)\b\s*){{0,5}}\b(?:{pattern})\b",
                after_head,
                re.I,
            )
            or re.match(
                rf"^\s*[,;:\-–—]?\s*on\s+(?:that\s+|this\s+)?"
                rf"(?:[a-z]+\s+){{0,3}}(?:{pattern})\b",
                after_head,
                re.I,
            )
            or re.match(
                rf"^\s*(?:o[’']clock\s+)?(?:and\s+an?\s+hour\s+)?"
                rf"(?:this\s+|that\s+)?(?:{pattern})\b",
                after_head,
                re.I,
            )
        )
        if direct:
            return True
        clause = after_head.split(".", 1)[0]
        marker = re.search(
            rf"\b(?:that|this|the\s+(?:following|next)|on\s+(?:the\s+)?|"
            rf"to-?morrow|tomorrow|yesterday|(?:mon|tues|wednes|thurs|fri|satur|sun)day)"
            rf"\s+(?:{pattern})\b",
            clause,
            re.I,
        )
        if not marker:
            return False
        prefix = clause[: marker.start()]
        if len(prefix) > 60:
            return False
        other_clock = re.search(
            rf"\d|\b(?:at|by|until|before|after|nearly|about|past|to)\s+{_HOUR_TOKEN}\b",
            prefix,
            re.I,
        )
        return not other_clock

    am = adjacent_daypart(r"morning|before dawn|after midnight|breakfast|sunrise|dawn")
    pm = adjacent_daypart(r"afternoon|evening|after noon|lunch|dinner|sunset|sundown")
    night = adjacent_daypart(r"at night|that night|tonight|night")

    def preceding_daypart(pattern: str) -> bool:
        match = re.search(
            rf"\b(?:{pattern})\b[^.!?]{{0,35}}"
            r"\b(?:at|by|from|it\s+(?:was|will\s+be))\s*$",
            before_tail,
            re.I,
        )
        if not match:
            return False
        prefix = before_tail[: match.start()]
        return not bool(
            re.search(
                rf"\b{_HOUR_TOKEN}\s+(?:in\s+the|that|this)\s*$",
                prefix,
                re.I,
            )
        )

    am = am or preceding_daypart(r"morning|dawn|sunrise")
    pm = pm or preceding_daypart(r"afternoon|evening|sunset|sundown")
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
    if re.match(r"\s*[-–—]+\s*[ap]\s*[-–—]+\s*m\b", after, re.I):
        return True
    if re.search(r"\blog(?:\s+again)?[.!?]\s*[\"'“‘]?\s*$", immediate, re.I):
        return True
    if re.search(
        r"(?::\d{2}(?::\d{2})?)?\s*(?:gmt|cet|est|edt|pst|pdt|ost|lt|shiptime)\b|"
        r"\d{1,2}:\d{2}:\d{2}",
        phrase,
        re.I,
    ):
        return True
    if re.search(
        r"\b(?:at|by|until|from|since|around|about|approximately|nearly|almost|"
        r"exactly|precisely|already|was|is|read|reads|said|says|showed|shows|displayed|"
        r"indicated|registered|struck|scheduled|timed)\s+(?:the\s+)?[:=-]?\s*$",
        immediate,
        re.I,
    ):
        return True
    if re.search(
        r"\b(?:morning|afternoon|evening|night|midnight|noon|dawn|breakfast|lunch|"
        r"dinner)\b",
        phrase,
        re.I,
    ):
        return True
    if re.search(
        r"\b(?:what\s+time|time\s+(?:is|was)\s+it|woke|awoke|to\s+sleep|posted\s+at|"
        r"(?:time\s+)?log|due\s+at|delayed\s+to|made|caught|take|took|boarded|left)\b"
        r"[^.!?]{0,90}$",
        before[-180:],
        re.I,
    ):
        return True
    if re.search(
        r"\b(?:clock|clocks|watch|chronometer|timer|time|hours?|timestamp|notebook|display|alarm|"
        r"dial|hands?|readout|numbers?|digits?|chip|bells?|chimes?|strokes?)\b",
        before[-220:],
        re.I,
    ):
        return True
    if re.match(
        r"[^.!?]{0,100}\b(?:clock|watch|chronometer|timer|time|timestamp|display|alarm|"
        r"dial|readout|numbers?|digits?)\b",
        after,
        re.I,
    ):
        return True
    if re.match(r"\s*[,;:\-–—]?\s*(?:sunrise|sunset)\b", after, re.I):
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
        r"\b(?:train|express|local|bus|flight|departure|arrival|station|platform|"
        r"timetable|time-table|railway|carriage|depot|flyer|accommodation|boarding|"
        r"liftoff)\b",
        before[-260:],
        re.I,
    ):
        return True
    if re.search(
        r"\b(?:clock|clocks|watch|time|timestamp|notebook|display|readout|alarm|dial|chip|"
        r"seconds?|hours?)\b",
        after[:220],
        re.I,
    ):
        return True
    if re.match(
        r"\s+(?:train|bus|flight|express|flyer|class|accommodation|jet|departure)\b",
        after,
        re.I,
    ):
        return True
    if re.match(
        r"\s+(?:was\s+on\s+the\s+line|steamed|swung|bore|pulled|rushed|rushing|"
        r"goes?\s+through|back\s+to|for\s+[A-Z]|at\s+[A-Z])\b",
        after,
        re.I,
    ):
        return True
    if re.search(
        r"\b(?:morning|afternoon|evening|last night|that night|tonight)\b",
        before[-140:] + after[:140],
        re.I,
    ):
        return True
    if re.match(r"\s+(?:to|from)\s+[A-Z]", after):
        return True
    if re.search(
        r"(?:\b(?:it\s+is|it\s+was|it['’]s|was|is|must\s+be|only|now|at|by|until|"
        r"around|about|before|after|past|since|toward|towards|near|nearly)\s*)$",
        before[-55:],
        re.I,
    ):
        return True
    if re.match(r"\s*:\d{2}\b", after):
        return True
    stripped_before = before.rstrip()
    stripped_after = after.lstrip()
    timestamp_boundary = not stripped_before or stripped_before.endswith((".", "!", "?", ":", ";"))
    if (
        timestamp_boundary
        and len(stripped_after) >= 12
        and re.match(r"^[,.;:\-–—)]*\s*[\"'“‘(]?[A-Za-z]", stripped_after)
    ):
        return True
    if stripped_before.endswith((":", ";")) and len(before.strip()) >= 15:
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

    normalized_phrase = _normalize(phrase)
    approximate_bare_hour = bool(
        re.fullmatch(
            rf"(?:around|about|approximately|near)\s+{_HOUR_TOKEN}", normalized_phrase, re.I
        )
    )
    if not approximate_bare_hour and re.search(
        r"\b(?:about|around|approximately|nearly|almost|barely|roughly|circa|"
        r"shortly|few|close\s+upon|not\s+yet|far[- ]off)\b|\bjust\s+(?:after|before|past)\b",
        normalized_phrase,
        re.I,
    ):
        return SemanticDecision(
            SemanticClass.UNKNOWN,
            SemanticAction.QUARANTINE,
            "APPROXIMATE_CLOCK_EXPRESSION",
            parser_route,
            "HIGH",
        )
    if re.search(r"<\/?time\b|<time\s*/>", phrase, re.I):
        return SemanticDecision(
            SemanticClass.UNKNOWN,
            SemanticAction.QUARANTINE,
            "HIGHLIGHT_SOURCE_MARKUP",
            parser_route,
            "HIGH",
        )
    if re.search(
        r"\b(?:minutes?|hours?)\s+(?:after|before|past|to)\s+(?:one|two|three|four|"
        r"five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2})\s*$",
        normalized_phrase,
        re.I,
    ) and re.match(
        r"\s+(?:men|women|people|persons?|children|boys?|girls?|squatters?|horses?|"
        r"cars?|ships?|trains?|pages?|chapters?|items?|objects?)\b",
        after,
        re.I,
    ):
        return SemanticDecision(
            SemanticClass.RELATIVE_DURATION,
            SemanticAction.QUARANTINE,
            "RELATIVE_DURATION_BASE_IS_QUANTITY",
            parser_route,
            "HIGH",
        )
    if re.search(r"\bhalf\s*$", before, re.I) and re.match(
        r"\s*(?:a|one)\s+minute\b", normalized_phrase, re.I
    ):
        return SemanticDecision(
            SemanticClass.UNKNOWN,
            SemanticAction.QUARANTINE,
            "HIGHLIGHT_SEMANTIC_MISMATCH",
            parser_route,
            "HIGH",
        )
    if re.search(r"\bonly\s+a\s+few\s+hours?\s*[-–—]?\s*$", before, re.I) and re.search(
        r"\bminutes?\b", phrase, re.I
    ):
        return SemanticDecision(
            SemanticClass.DURATION,
            SemanticAction.QUARANTINE,
            "DURATION_ELAPSED_UNITS",
            parser_route,
            "HIGH",
        )

    colon = _CLOCK_NUMERIC_RE.search(phrase)
    if colon:
        immediate_before = before[-100:]
        immediate_after = after[:100]
        if (
            re.search(r"[\[{]\s*$", immediate_before)
            or re.match(r"\s*[\]}]", immediate_after)
            or re.match(r"\s*,\s*\d+(?:\s*[-–—;,.:\]})]|\s*$)", immediate_after)
            or (
                re.search(r"\(\s*$", immediate_before)
                and re.match(r"\s*\)\s*\[\d+\]", immediate_after)
            )
            or (
                re.search(r"\b(?:bible|trans\.)\b", immediate_after, re.I)
                and not _clock_context(before, after, phrase)
            )
        ):
            return SemanticDecision(
                SemanticClass.SECTION_OR_REFERENCE,
                SemanticAction.QUARANTINE,
                "REFERENCE_BRACKETED_ANNOTATION",
                parser_route,
                "HIGH",
            )
        if re.search(
            r"\b(?:observation\s+by|letters?\s+from|prophec(?:y|ies)\s+of|"
            r"fulfillment\s+of|see|compare|cf\.?)\s+[A-Z][\w. -]{0,50}$",
            immediate_before,
        ):
            return SemanticDecision(
                SemanticClass.SECTION_OR_REFERENCE,
                SemanticAction.QUARANTINE,
                "REFERENCE_EXPLICIT",
                parser_route,
                "HIGH",
            )
        if re.search(r"(?:£|\$)\s*$", immediate_before) or re.match(r"\s*:\d+", immediate_after):
            return SemanticDecision(
                SemanticClass.RATIO_OR_MEASUREMENT,
                SemanticAction.QUARANTINE,
                "MEASUREMENT_CONTEXT",
                parser_route,
                "HIGH",
            )
        if re.search(
            r"\b(?:right\s+angle|declension|record\s+of|went\s+in)\s*$",
            immediate_before,
            re.I,
        ) or re.match(r"\s+(?:speed|gait)\b", immediate_after, re.I):
            return SemanticDecision(
                SemanticClass.SCORE_OR_RESULT,
                SemanticAction.QUARANTINE,
                "SCORE_CONTEXT",
                parser_route,
                "HIGH",
            )
        if re.search(r"\bbetween\s+\d{1,2}(?::|\.)\d{2}.*\band\s*$", before, re.I) or re.search(
            r"\b\d{1,2}(?::|\.)\d{2}\s*(?:-|–|—|to)\s*$", before, re.I
        ):
            return SemanticDecision(
                SemanticClass.UNKNOWN,
                SemanticAction.QUARANTINE,
                "CLOCK_RANGE_EXPRESSION",
                parser_route,
                "HIGH",
            )
        if re.search(r"\bratio\b[^.!?]{0,100}$", before, re.I):
            return SemanticDecision(
                SemanticClass.RATIO_OR_MEASUREMENT,
                SemanticAction.QUARANTINE,
                "RATIO_CONTEXT",
                parser_route,
                "HIGH",
            )
        if len(re.findall(r"\d{1,2}:\d{1,3}", quote)) >= 2 and re.search(
            r"\b(?:see|gospel|christ|worship|sin|death|ordinances?|ministry)\b|[;\[\]]",
            quote,
            re.I,
        ):
            return SemanticDecision(
                SemanticClass.SECTION_OR_REFERENCE,
                SemanticAction.QUARANTINE,
                "REFERENCE_DENSE_COLON_LIST",
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
    if re.match(
        rf"\s*(?:[{re.escape(_HYPHENS)}]\s*)?(?:or\s*[{re.escape(_HYPHENS)}]\s*)?"
        r"\d+\b|\s+(?:days?|weeks?|months?|years?|pages?|dollars?|feet|foot)\b",
        after,
        re.I,
    ) and re.fullmatch(
        rf"\s*(?:{_HOUR_TOKEN})[ {re.escape(_HYPHENS)}]+(?:{_NUMBER_TOKEN})\s*",
        phrase,
        re.I,
    ):
        return SemanticDecision(
            SemanticClass.NON_TEMPORAL_NUMBER,
            SemanticAction.QUARANTINE,
            "NON_TEMPORAL_NUMBER_SEQUENCE",
            parser_route,
            "HIGH",
        )
    if re.fullmatch(
        rf"\s*(?:{_HOUR_TOKEN})[ {re.escape(_HYPHENS)}]+(?:{_NUMBER_TOKEN})\s*",
        phrase,
        re.I,
    ) and re.match(
        rf"\s*[{re.escape(_HYPHENS)}]?\s*"
        r"(?:pages?|dollars?|feet|foot|inches?|yards?|metres?|meters?)\b",
        after,
        re.I,
    ):
        return SemanticDecision(
            SemanticClass.NON_TEMPORAL_NUMBER,
            SemanticAction.QUARANTINE,
            "NON_TEMPORAL_QUANTITY",
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

    if not colon:
        return None
    if re.search(r"\b(?:between|from)\s*$", before, re.I) or re.match(
        r"\s*(?:-|–|—|to|through|and)\s*\d{1,2}(?::|\.)\d{2}", after, re.I
    ):
        return SemanticDecision(
            SemanticClass.UNKNOWN,
            SemanticAction.QUARANTINE,
            "CLOCK_RANGE_EXPRESSION",
            parser_route,
            "HIGH",
        )
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
    if re.search(rf"\b(?:[1-3]\s+)?(?:{_SCRIPTURE})\.?\s*[,;(]?\s*$", before, re.I) or re.match(
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
            reason_code,
        )
    return SemanticDecision(
        SemanticClass.CLOCK_TIME_EXACT if len(minutes) == 1 else SemanticClass.CLOCK_TIME_AMBIGUOUS,
        SemanticAction.KEEP,
        reason_code,
        parser_route,
        "HIGH" if len(minutes) == 1 else "MEDIUM",
        minutes,
        reason_code,
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
    source_context_before: str | None = None,
    source_context_after: str | None = None,
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
    if source_context_before:
        before = f"{source_context_before}\n{before}"
    if source_context_after:
        after = f"{after}\n{source_context_after}"
    normalized_phrase = _normalize(phrase)

    minute_to_new_day = _MINUTE_TO_NEW_DAY_RE.search(normalized_phrase)
    if minute_to_new_day:
        return _finish_clock(
            minutes=(1439,),
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_MINUTE_TO_NEW_DAY",
        )

    minute_short_numeric = _MINUTE_SHORT_OF_NUMERIC_RE.search(normalized_phrase)
    if minute_short_numeric:
        amount_text = minute_short_numeric.group("amount")
        amount = 1 if amount_text in {None, "a", "an", "one"} else int(amount_text)
        hour = int(minute_short_numeric.group("hour"))
        minute = int(minute_short_numeric.group("minute"))
        bases = _explicit_or_context_minutes(hour, minute, phrase, before, after)
        return _finish_clock(
            minutes=tuple((base - amount) % 1440 for base in bases),
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_SHORT_OF_NUMERIC",
        )

    nested_before = _NESTED_BEFORE_RE.search(normalized_phrase)
    if nested_before:
        outer = _number(nested_before.group("outer"))
        inner = _number(nested_before.group("inner"))
        hour = _number(nested_before.group("base"))
        if (
            outer is not None
            and inner is not None
            and hour is not None
            and 1 <= outer <= 180
            and 1 <= inner <= 59
            and 1 <= hour <= 12
        ):
            bases = _explicit_or_context_minutes(hour, 0, phrase, before, after)
            return _finish_clock(
                minutes=tuple((base - inner - outer) % 1440 for base in bases),
                claimed_minute=claimed_minute,
                parser_route=parser_route,
                reason_code="CLOCK_GRAMMAR_NESTED_BEFORE",
            )

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

    verbose_named = _VERBOSE_TO_NAMED_RE.search(normalized_phrase)
    if verbose_named:
        amount_text = verbose_named.group("amount")
        amount = 1 if amount_text in {"a", "an"} else _number(amount_text)
        base = 0 if verbose_named.group("base") == "midnight" else 720
        if amount is not None and 1 <= amount <= 59:
            return _finish_clock(
                minutes=((base - amount) % 1440,),
                claimed_minute=claimed_minute,
                parser_route=parser_route,
                reason_code="CLOCK_GRAMMAR_VERBOSE_TO_NAMED",
            )

    struck_relative = _CLOCK_STRUCK_RELATIVE_RE.search(normalized_phrase)
    if struck_relative:
        amount_text = struck_relative.group("amount")
        amount = 1 if amount_text in {"a", "an"} else _number(amount_text)
        base_text = struck_relative.group("base")
        if base_text in {"midnight", "midday", "noon"}:
            bases = (0,) if base_text == "midnight" else (720,)
        else:
            hour = _number(base_text)
            bases = (
                _explicit_or_context_minutes(hour, 0, phrase, before, after)
                if hour is not None and 1 <= hour <= 12
                else ()
            )
        if amount is not None:
            delta = amount if struck_relative.group("direction") == "after" else -amount
            return _finish_clock(
                minutes=tuple((base + delta) % 1440 for base in bases),
                claimed_minute=claimed_minute,
                parser_route=parser_route,
                reason_code="CLOCK_GRAMMAR_CLOCK_STRUCK_RELATIVE",
            )

    oclock_plus = _OCLOCK_PLUS_MINUTES_RE.search(normalized_phrase)
    if oclock_plus:
        hour = _number(oclock_plus.group("hour"))
        amount = _number(oclock_plus.group("amount"))
        minutes = (
            _explicit_or_context_minutes(hour, amount, phrase, before, after)
            if hour is not None and amount is not None and 1 <= hour <= 12 and amount <= 59
            else ()
        )
        return _finish_clock(
            minutes=minutes,
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_OCLOCK_PLUS_MINUTES",
        )

    oclock_all_but = _OCLOCK_ALL_BUT_RE.search(normalized_phrase)
    if oclock_all_but:
        hour = _number(oclock_all_but.group("hour"))
        amount = _number(oclock_all_but.group("amount"))
        bases = (
            _explicit_or_context_minutes(hour, 0, phrase, before, after)
            if hour is not None and 1 <= hour <= 12
            else ()
        )
        return _finish_clock(
            minutes=tuple((base - amount) % 1440 for base in bases) if amount else (),
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_OCLOCK_ALL_BUT",
        )

    offset_quarter = _OFFSET_QUARTER_RE.search(normalized_phrase)
    if offset_quarter:
        amount = (
            1
            if offset_quarter.group("amount") in {"a", "an"}
            else _number(offset_quarter.group("amount"))
        )
        hour = _number(offset_quarter.group("base"))
        if amount is not None and hour is not None and 1 <= amount <= 59 and 1 <= hour <= 12:
            bases = _explicit_or_context_minutes(hour, 0, phrase, before, after)
            inner = 15 if offset_quarter.group("inner") in {"past", "after"} else -15
            outer = amount if offset_quarter.group("outer") in {"past", "after"} else -amount
            minutes = tuple((base + inner + outer) % 1440 for base in bases)
            return _finish_clock(
                minutes=minutes,
                claimed_minute=claimed_minute,
                parser_route=parser_route,
                reason_code="CLOCK_GRAMMAR_OFFSET_QUARTER",
            )

    offset_written = _OFFSET_FROM_WRITTEN_RE.search(normalized_phrase)
    if offset_written:
        amount = (
            1 if offset_written.group("amount") == "a" else _number(offset_written.group("amount"))
        )
        hour = _number(offset_written.group("hour"))
        minute = _number(offset_written.group("minute"))
        if (
            amount is not None
            and hour is not None
            and minute is not None
            and 1 <= amount <= 59
            and 1 <= hour <= 12
            and 0 <= minute <= 59
        ):
            bases = _explicit_or_context_minutes(hour, minute, phrase, before, after)
            direction = offset_written.group("direction")
            delta = amount if direction in {"after", "past"} else -amount
            minutes = tuple((base + delta) % 1440 for base in bases)
            return _finish_clock(
                minutes=minutes,
                claimed_minute=claimed_minute,
                parser_route=parser_route,
                reason_code="CLOCK_GRAMMAR_OFFSET_WRITTEN",
            )

    hours_named = _HOURS_FROM_NAMED_RE.search(normalized_phrase)
    if hours_named:
        amount_text = hours_named.group("amount")
        amount = 1 if amount_text in {"a", "an"} else _number(amount_text)
        base = 0 if hours_named.group("base") == "midnight" else 720
        direction = hours_named.group("direction")
        if amount is not None:
            delta = amount * 60 if direction in {"after", "past"} else -amount * 60
            return _finish_clock(
                minutes=((base + delta) % 1440,),
                claimed_minute=claimed_minute,
                parser_route=parser_route,
                reason_code="CLOCK_GRAMMAR_HOURS_FROM_NAMED",
            )

    named_then_past = _NAMED_THEN_PAST_RE.search(normalized_phrase)
    if named_then_past:
        amount = _number(named_then_past.group("amount"))
        base = 0 if named_then_past.group("base") == "midnight" else 720
        if amount is not None and 1 <= amount <= 59:
            return _finish_clock(
                minutes=((base + amount) % 1440,),
                claimed_minute=claimed_minute,
                parser_route=parser_route,
                reason_code="CLOCK_GRAMMAR_NAMED_THEN_PAST",
            )

    fractional_relative = _FRACTIONAL_MINUTES_RELATIVE_RE.search(normalized_phrase)
    if fractional_relative:
        amount = _number(fractional_relative.group("amount"))
        base_text = fractional_relative.group("base")
        if base_text in {"midnight", "midday", "noon"}:
            bases = (0,) if base_text == "midnight" else (720,)
        else:
            base_hour = _number(base_text)
            bases = (
                _explicit_or_context_minutes(base_hour, 0, phrase, before, after)
                if base_hour is not None and 1 <= base_hour <= 12
                else ()
            )
        if amount is not None and bases:
            seconds = amount * 60 + 30
            signed = (
                seconds if fractional_relative.group("direction") in {"past", "after"} else -seconds
            )
            minutes = tuple(((base * 60 + signed) % 86400) // 60 for base in bases)
            return _finish_clock(
                minutes=minutes,
                claimed_minute=claimed_minute,
                parser_route=parser_route,
                reason_code="CLOCK_GRAMMAR_FRACTIONAL_RELATIVE",
            )

    precise_relative = _MINUTES_SECONDS_RELATIVE_RE.search(normalized_phrase)
    if precise_relative:
        amount_text = precise_relative.group("amount")
        amount = (
            1
            if amount_text in {"a", "an"}
            else 0.5
            if amount_text == "half"
            else _number(amount_text)
        )
        base_text = precise_relative.group("base")
        if base_text in {"midnight", "midday", "noon"}:
            bases = (0,) if base_text == "midnight" else (720,)
        else:
            base_hour = _number(base_text)
            bases = (
                _explicit_or_context_minutes(base_hour, 0, phrase, before, after)
                if base_hour is not None and 1 <= base_hour <= 12
                else ()
            )
        if amount is not None and bases:
            seconds = (
                _number(precise_relative.group("seconds"))
                if precise_relative.group("seconds")
                else 0
            )
            total_seconds = int(amount * 60) + (seconds or 0)
            direction = precise_relative.group("direction")
            signed = total_seconds if direction in {"past", "after"} else -total_seconds
            minutes = tuple(((base * 60 + signed) % 86400) // 60 for base in bases)
            return _finish_clock(
                minutes=minutes,
                claimed_minute=claimed_minute,
                parser_route=parser_route,
                reason_code="CLOCK_GRAMMAR_PRECISE_RELATIVE",
            )

    hour_fraction = _HOUR_AND_FRACTION_RE.search(normalized_phrase)
    if hour_fraction and _clock_context(before, after, phrase):
        hour = _number(hour_fraction.group("hour"))
        fraction = 30 if "half" in hour_fraction.group("fraction") else 15
        minutes = (
            _explicit_or_context_minutes(hour, fraction, phrase, before, after)
            if hour is not None and 1 <= hour <= 12
            else ()
        )
        return _finish_clock(
            minutes=minutes,
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_HOUR_FRACTION",
        )

    numeric_half_past = _NUMERIC_HALF_PAST_RE.search(normalized_phrase)
    if numeric_half_past:
        hour = _number(numeric_half_past.group("hour"))
        minutes = (
            _explicit_or_context_minutes(hour, 30, phrase, before, after)
            if hour is not None and 1 <= hour <= 12
            else ()
        )
        return _finish_clock(
            minutes=minutes,
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_NUMERIC_HALF_PAST",
        )

    relative_phrase = re.sub(
        rf"\b({_NUMBER_TOKEN})[{re.escape(_HYPHENS)}]+\s*(minutes?)\b",
        r"\1 \2",
        normalized_phrase,
        flags=re.I,
    )
    relative = _RELATIVE_RE.search(relative_phrase)
    if relative:
        amount_text = relative.group("amount")
        amount = (
            45
            if "three quarter" in amount_text or "third quarter" in amount_text
            else 15
            if "quarter" in amount_text
            else 30
            if amount_text == "half"
            else 1
            if amount_text in {"a", "an"}
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
        if base_text in {"midnight", "midday", "noon"}:
            base = 0 if base_text == "midnight" else 720
            minutes = ((base + delta) % 1440,)
        else:
            base_hour = _number(base_text)
            if base_hour is None or not 1 <= base_hour <= 23:
                minutes = ()
            elif base_hour > 12:
                minutes = ((base_hour * 60 + delta) % 1440,)
            else:
                base_candidates = _explicit_or_context_minutes(base_hour, 0, phrase, before, after)
                minutes = tuple((base + delta) % 1440 for base in base_candidates)
        return _finish_clock(
            minutes=minutes,
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_RELATIVE",
        )

    fraction_oclock = _FRACTION_OCLOCK_RE.search(normalized_phrase)
    if fraction_oclock:
        hour = _number(fraction_oclock.group("hour"))
        minutes = (
            _explicit_or_context_minutes(hour, 30, phrase, before, after)
            if hour is not None and 1 <= hour <= 12
            else ()
        )
        return _finish_clock(
            minutes=minutes,
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_FRACTION_OCLOCK",
        )

    written_oclock = _WRITTEN_OCLOCK_RE.search(normalized_phrase)
    if written_oclock:
        hour = _number(written_oclock.group("hour"))
        minute = _number(written_oclock.group("minute"))
        minutes = (
            _explicit_or_context_minutes(hour, minute, phrase, before, after)
            if hour is not None and minute is not None and 1 <= hour <= 12 and minute <= 59
            else ()
        )
        return _finish_clock(
            minutes=minutes,
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_WRITTEN_OCLOCK",
        )

    oclock = (
        _OCLOCK_RE.search(normalized_phrase)
        or _SPACED_OCLOCK_RE.search(normalized_phrase)
        or _ARCHAIC_OCLOCK_RE.search(normalized_phrase)
    )
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

    spoken_phrase = re.sub(r"\s*(?:(?:\.\s*){2,}|…+)\s*", " ", normalized_phrase)
    spoken_phrase = re.sub(r"\s*-\s*", "-", spoken_phrase)
    written_military = _WRITTEN_MILITARY_RE.search(spoken_phrase)
    if written_military:
        hour = _number(written_military.group("hour"))
        minute_text = written_military.group("minute")
        minute = 0 if minute_text == "oh" else _number(minute_text)
        minutes = (
            (hour * 60 + minute,)
            if hour is not None and minute is not None and 0 <= hour <= 23 and 0 <= minute <= 59
            else ()
        )
        return _finish_clock(
            minutes=minutes,
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_WRITTEN_MILITARY",
        )

    written_and = _WRITTEN_AND_RE.search(spoken_phrase)
    if written_and and _clock_context(before, after, phrase):
        hour = _number(written_and.group("hour"))
        minute = _number(written_and.group("minute"))
        minutes = (
            _explicit_or_context_minutes(hour, minute, phrase, before, after)
            if hour is not None and minute is not None and 1 <= hour <= 12 and minute <= 59
            else ()
        )
        return _finish_clock(
            minutes=minutes,
            claimed_minute=claimed_minute,
            parser_route=parser_route,
            reason_code="CLOCK_GRAMMAR_WRITTEN_AND",
        )

    written_24h = _WRITTEN_24H_RE.search(spoken_phrase)
    if written_24h:
        hour = _number(written_24h.group("hour"))
        minute = _number(written_24h.group("minute"))
        if hour is not None and minute is not None and 13 <= hour <= 23 and minute <= 59:
            return _finish_clock(
                minutes=(hour * 60 + minute,),
                claimed_minute=claimed_minute,
                parser_route=parser_route,
                reason_code="CLOCK_GRAMMAR_WRITTEN_24H",
            )

    written = _WRITTEN_RE.search(spoken_phrase)
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
            if (
                _clock_context(before, after, phrase)
                or re.match(
                    r"^(?:at|by|around|about|approximately|near|before)\b", normalized_phrase
                )
                or re.search(
                    r"\b(?:[ap]\.?\s*m\.?|morning|morn\.?|afternoon|evening|night)\b",
                    normalized_phrase,
                    re.I,
                )
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
        if hour is not None and 13 <= hour <= 23 and _clock_context(before, after, phrase):
            return _finish_clock(
                minutes=(hour * 60,),
                claimed_minute=claimed_minute,
                parser_route=parser_route,
                reason_code="CLOCK_GRAMMAR_BARE_24H_CONTEXTUAL",
            )

    bare_24h = _BARE_24H_RE.fullmatch(normalized_phrase)
    if bare_24h:
        hour = _number(bare_24h.group("hour"))
        if hour is not None and 13 <= hour <= 23 and _clock_context(before, after, phrase):
            return _finish_clock(
                minutes=(hour * 60,),
                claimed_minute=claimed_minute,
                parser_route=parser_route,
                reason_code="CLOCK_GRAMMAR_BARE_24H_CONTEXTUAL",
            )

    return SemanticDecision(
        SemanticClass.UNKNOWN,
        SemanticAction.REVIEW,
        "UNKNOWN_CONTEXT",
        parser_route,
        "LOW",
    )
