"""Conservative, deterministic literary time-expression detection."""

from __future__ import annotations

import re
from dataclasses import replace

from litclock.models import TimeConfidence, TimeDetection

_ONES = {
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
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}
_HOUR_WORDS = tuple(
    sorted((word for word, value in _ONES.items() if 1 <= value <= 12), key=len, reverse=True)
)
_SMALL_WORDS = tuple(
    sorted((word for word, value in _ONES.items() if 1 <= value <= 19), key=len, reverse=True)
)
_WORD_SEPARATOR = r"[\s\-\u2010-\u2015]"
_HOUR_PATTERN = "(?:" + "|".join(_HOUR_WORDS) + r"|\d{1,2})"
_SMALL_PATTERN = "(?:" + "|".join(_SMALL_WORDS) + ")"
_MINUTE_PATTERN = (
    rf"(?:fifty(?:{_WORD_SEPARATOR}+" + _SMALL_PATTERN + r")?"
    rf"|forty(?:{_WORD_SEPARATOR}+" + _SMALL_PATTERN + r")?"
    rf"|thirty(?:{_WORD_SEPARATOR}+" + _SMALL_PATTERN + r")?"
    rf"|twenty(?:{_WORD_SEPARATOR}+" + _SMALL_PATTERN + r")?"
    r"|" + _SMALL_PATTERN + r"|\d{1,2})"
)
_MERIDIEM_PATTERN = r"(?P<meridian>[ap])\s*\.?\s*m\.?"
_OCLOCK_PATTERN = r"o[’']clock"

_NUMERIC_RANGE_RE = re.compile(
    r"(?<!\w)(?:(?:from|between)\s+)?"
    r"\d{1,2}[:.]\d{2}(?:\s*[ap]\s*\.?\s*m\.?)?\s*"
    r"(?:-|–|—|to|and)\s*"
    r"\d{1,2}[:.]\d{2}(?:\s*[ap]\s*\.?\s*m\.?)?(?!\w)",
    re.IGNORECASE,
)
_WRITTEN_RANGE_RE = re.compile(
    rf"(?<!\w)(?:from\s+{_HOUR_PATTERN}\s+(?:to|until)\s+{_HOUR_PATTERN}"
    rf"|between\s+{_HOUR_PATTERN}\s+and\s+{_HOUR_PATTERN})(?:\s+{_OCLOCK_PATTERN})?",
    re.IGNORECASE,
)
_BARE_APPROXIMATE_RE = re.compile(
    rf"(?<!\w)(?:(?:about|around|nearly|almost|roughly|approximately)\s+"
    rf"(?:{_HOUR_PATTERN}(?:\s+{_OCLOCK_PATTERN})?|half{_WORD_SEPARATOR}+past\s+{_HOUR_PATTERN})"
    rf"|(?:sometime|shortly|just)\s+(?:before|after)\s+"
    rf"(?:midnight|noon|{_HOUR_PATTERN})"
    rf"|(?:before|after)\s+(?:midnight|noon|{_HOUR_PATTERN}))(?!\w)",
    re.IGNORECASE,
)
_NAMED_APPROXIMATE_RE = re.compile(
    r"(?<!\w)(?:(?:about|around|nearly|almost|roughly|approximately|by|"
    r"before|after|near|toward|towards)\s+|(?:long\s+)?past\s+|"
    r"(?:sometime|shortly|just)\s+(?:before|after)\s+)"
    r"(?:midnight|midday|noon)\b",
    re.IGNORECASE,
)
_RELATIVE_RE = re.compile(
    rf"(?<!\w)(?P<amount>a\s+quarter|quarter|half|an?|{_MINUTE_PATTERN})"
    rf"(?:\s+minutes?)?{_WORD_SEPARATOR}+(?P<direction>past|after|to|before)\s+"
    rf"(?P<base>midnight|noon|{_HOUR_PATTERN})(?:\s+{_OCLOCK_PATTERN})?"
    rf"(?:\s*{_MERIDIEM_PATTERN})?(?!\w)",
    re.IGNORECASE,
)
_NUMERIC_RE = re.compile(
    rf"(?<![\w.])(?P<hour>\d{{1,2}})(?P<separator>[:.])(?P<minute>\d{{2}})"
    rf"(?:\s*{_MERIDIEM_PATTERN})?(?![\w.])",
    re.IGNORECASE,
)
_MILITARY_RE = re.compile(r"(?<!\w)(?P<hour>\d{2})(?P<minute>\d{2})\s+hours?\b", re.IGNORECASE)
_OCLOCK_RE = re.compile(
    rf"(?<!\w)(?P<hour>{_HOUR_PATTERN})\s+{_OCLOCK_PATTERN}"
    rf"(?:\s*{_MERIDIEM_PATTERN})?(?!\w)",
    re.IGNORECASE,
)
_WRITTEN_CLOCK_RE = re.compile(
    rf"(?<!\w)(?P<hour>{_HOUR_PATTERN}){_WORD_SEPARATOR}+"
    rf"(?P<minute>{_MINUTE_PATTERN})"
    rf"(?:\s*{_MERIDIEM_PATTERN})?(?!\w)",
    re.IGNORECASE,
)
_NAMED_RE = re.compile(r"\b(?P<name>midnight|midday|noon)\b", re.IGNORECASE)

_APPROX_PREFIX_RE = re.compile(
    r"(?P<prefix>\b(?:about|around|nearly|almost|roughly|approximately)\s+"
    r"|\b(?:sometime|shortly|just)\s+(?:before|after)\s+"
    r"|\b(?:before|after|toward|towards|by)\s+"
    r"|\bsomething\s+like\s+"
    r"|\bclose\s+upon\s+|\bas\s+early\s+as\s+)$",
    re.IGNORECASE,
)

_SCRIPTURE_NAMES = (
    r"genesis|exodus|leviticus|numbers|deuteronomy|joshua|judges|ruth|samuel|kings|"
    r"chronicles|ezra|nehemiah|esther|job|psalms?|proverbs|ecclesiastes|isaiah|"
    r"jeremiah|lamentations|ezekiel|daniel|hosea|joel|amos|obadiah|jonah|micah|"
    r"nahum|habakkuk|zephaniah|haggai|zechariah|malachi|matthew|mark|luke|john|"
    r"acts|romans|corinthians|galatians|ephesians|philippians|colossians|"
    r"thessalonians|timothy|titus|philemon|hebrews|james|peter|jude|revelation|rev"
)
_SCRIPTURE_ABBREVIATIONS = (
    r"gen|gn|exod?|lev|num|deut|josh|judg|sam|kgs?|chr|neh|esth|ps|prov|eccl|eccles|"
    r"isa|jer|lam|ezek|dan|hos|obad|jon|mic|nah|hab|zeph|hag|zech|mal|"
    r"matt|mat|mt|mk|lk|jn|rom|cor|gal|eph|phil|col|thess|tim|tit|philem|heb|jas|"
    r"pet|jude|rev"
)


def _number(value: str) -> int | None:
    normalized = re.sub(r"[\s\-\u2010-\u2015]+", " ", value.casefold().strip())
    if normalized.isdigit():
        return int(normalized)
    if normalized in {"a", "an"}:
        return 1
    if normalized in _ONES:
        return _ONES[normalized]
    parts = normalized.split()
    if len(parts) == 2 and parts[0] in _TENS and parts[1] in _ONES:
        return _TENS[parts[0]] + _ONES[parts[1]]
    if normalized in _TENS:
        return _TENS[normalized]
    return None


def _clock_minute(hour: int, minute: int, meridian: str) -> int:
    hour %= 12
    if meridian == "p":
        hour += 12
    return hour * 60 + minute


def _context_resolution(
    text: str, start: int, end: int, hour: int, minute: int, meridian: str | None
) -> tuple[int | None, TimeConfidence, str]:
    if meridian:
        if not 1 <= hour <= 12:
            return None, TimeConfidence.INVALID, f"invalid {hour}-hour value with meridiem"
        normalized = meridian.casefold()
        confidence = TimeConfidence.EXACT_AM if normalized == "a" else TimeConfidence.EXACT_PM
        return _clock_minute(hour, minute, normalized), confidence, f"explicit {normalized}.m."

    before = text[max(0, start - 90) : start].casefold()
    after = text[end : min(len(text), end + 90)].casefold()
    contextual: str | None = None
    if re.match(r"\s+(?:(?:in|during)\s+the|that|this)\s+morning\b", after) or re.search(
        r"\b(?:that|this)\s+morning[^.!?]{0,35}$", before
    ):
        contextual = "morning"
    elif re.match(r"\s+(?:(?:in|during)\s+the|that|this)\s+afternoon\b", after) or re.search(
        r"\b(?:that|this)\s+afternoon[^.!?]{0,35}$", before
    ):
        contextual = "afternoon"
    elif re.match(r"\s+(?:(?:in|during)\s+the|that|this)\s+evening\b", after) or re.search(
        r"\b(?:that|this)\s+evening[^.!?]{0,35}$", before
    ):
        contextual = "evening"
    elif re.match(r"\s+(?:at\s+night|that\s+night|tonight)\b", after):
        contextual = "night"
    elif re.search(r"\b(?:after\s+midnight|before\s+dawn|at\s+breakfast)[^.!?]{0,40}$", before):
        contextual = "morning-context"

    if contextual in {"morning", "morning-context"}:
        return _clock_minute(hour, minute, "a"), TimeConfidence.EXACT_CONTEXTUAL, contextual
    if contextual in {"afternoon", "evening"}:
        return _clock_minute(hour, minute, "p"), TimeConfidence.EXACT_CONTEXTUAL, contextual
    if contextual == "night":
        if hour == 12 or 1 <= hour <= 4:
            return _clock_minute(hour, minute, "a"), TimeConfidence.EXACT_CONTEXTUAL, "night"
        if 6 <= hour <= 11:
            return _clock_minute(hour, minute, "p"), TimeConfidence.EXACT_CONTEXTUAL, "night"

    possibilities = f"{_clock_minute(hour, minute, 'a') // 60:02d}:{minute:02d}|"
    pm_minute = _clock_minute(hour, minute, "p")
    possibilities += f"{pm_minute // 60:02d}:{minute:02d}"
    return None, TimeConfidence.AMPM_AMBIGUOUS, f"no strong AM/PM evidence; {possibilities}"


def _with_approximate_prefix(text: str, detection: TimeDetection) -> TimeDetection:
    prefix_window_start = max(0, detection.start - 60)
    prefix_window = text[prefix_window_start : detection.start]
    range_match = re.search(
        rf"(?P<prefix>\bbetween\s+[^,.;!?]{{1,35}}\s+and\s+|"
        rf"\bfrom\s+[^\r\n]{{1,70}}\s+(?:to|until)\s+|"
        rf"\b{_MINUTE_PATTERN}\s+or\s+|\bor\s+(?:perhaps|maybe)\s+)$",
        prefix_window,
        re.IGNORECASE,
    )
    if range_match:
        start = prefix_window_start + range_match.start("prefix")
        return replace(
            detection,
            start=start,
            text=text[start : detection.end],
            minute_of_day=None,
            confidence=TimeConfidence.RANGE,
            ampm_evidence=None,
            rejection_reason="time range or alternative",
        )
    compound_match = re.search(
        rf"(?P<prefix>\b{_MINUTE_PATTERN}\s+hours?\s+and\s+)$",
        prefix_window,
        re.IGNORECASE,
    )
    if compound_match:
        start = prefix_window_start + compound_match.start("prefix")
        return replace(
            detection,
            start=start,
            text=text[start : detection.end],
            minute_of_day=None,
            confidence=TimeConfidence.APPROXIMATE,
            ampm_evidence=None,
            rejection_reason="compound hour-and-minute offset is not resolved",
        )
    compound_approximate_match = re.search(
        r"(?P<prefix>\b(?:about|around|roughly|approximately)\s+"
        r"(?:half\s+an?|\w+(?:-\w+)?)\s+hours?\s+and\s+)$",
        prefix_window,
        re.IGNORECASE,
    )
    if compound_approximate_match:
        start = prefix_window_start + compound_approximate_match.start("prefix")
        return replace(
            detection,
            start=start,
            text=text[start : detection.end],
            minute_of_day=None,
            confidence=TimeConfidence.APPROXIMATE,
            ampm_evidence=None,
            rejection_reason="approximate compound offset",
        )
    suffix = text[detection.end : min(len(text), detection.end + 45)]
    if re.match(r"\s*[,;]?\s*(?:give\s+or\s+take|plus\s+or\s+minus|\+/-)\b", suffix, re.I):
        return replace(
            detection,
            minute_of_day=None,
            confidence=TimeConfidence.APPROXIMATE,
            ampm_evidence=None,
            rejection_reason="approximate tolerance",
        )
    if re.match(
        r"\s+(?:at\s+(?:the\s+)?(?:latest|earliest)|or\s+(?:perhaps|maybe))\b",
        suffix,
        re.I,
    ):
        return replace(
            detection,
            minute_of_day=None,
            confidence=TimeConfidence.RANGE,
            ampm_evidence=None,
            rejection_reason="uncertain time bound or alternative",
        )
    match = _APPROX_PREFIX_RE.search(prefix_window)
    if not match:
        return detection
    start = prefix_window_start + match.start("prefix")
    return replace(
        detection,
        start=start,
        text=text[start : detection.end],
        minute_of_day=None,
        confidence=TimeConfidence.APPROXIMATE,
        ampm_evidence=None,
        rejection_reason="approximate modifier",
    )


def _relative_detection(text: str, match: re.Match[str]) -> TimeDetection:
    amount_text = match.group("amount")
    amount = (
        15
        if "quarter" in amount_text.casefold()
        else 30
        if amount_text.casefold() == "half"
        else _number(amount_text)
    )
    direction = match.group("direction").casefold()
    base_text = match.group("base").casefold()
    meridian = match.group("meridian")
    if (
        amount is None
        or not 1 <= amount <= 59
        or (amount_text.casefold() == "half" and direction != "past")
    ):
        return TimeDetection(
            match.start(),
            match.end(),
            match.group(),
            None,
            TimeConfidence.INVALID,
            "relative",
            rejection_reason="invalid relative minute amount",
        )
    delta = amount if direction in {"past", "after"} else -amount
    if (
        base_text in {"midnight", "noon"}
        and "minute" not in match.group().casefold()
        and "quarter" not in amount_text.casefold()
        and amount_text.casefold() != "half"
    ):
        # Phrases such as “eleven before noon” can mean eleven o'clock,
        # rather than eleven minutes before noon. Retain them for review.
        return TimeDetection(
            match.start(),
            match.end(),
            match.group(),
            None,
            TimeConfidence.AMPM_AMBIGUOUS,
            "relative",
            rejection_reason="named-base relative phrase lacks an explicit minute unit",
        )
    bare_relative = (
        "minute" not in match.group().casefold()
        and "quarter" not in amount_text.casefold()
        and amount_text.casefold() != "half"
        and base_text not in {"midnight", "noon"}
    )
    if bare_relative:
        before = text[max(0, match.start() - 110) : match.start()]
        after = text[match.end() : min(len(text), match.end() + 35)]
        range_cue = bool(
            re.search(
                r"\b(?:the\s+)?(?:hour\s+of|hours?\s+of\s+attendance|recommend)\s*$"
                r"|\bdog[\s-]?watch(?:es)?(?:\s+\w+){0,3}\s*[,;:]?\s*$",
                before,
                re.IGNORECASE,
            )
            or re.search(
                rf"\b{_HOUR_PATTERN}\s+to\s+{_HOUR_PATTERN}"
                rf"(?:\s+{_OCLOCK_PATTERN})?"
                r"(?:\s+in\s+the\s+(?:morning|forenoon|afternoon|evening))?"
                r"(?:,\s*|\s+and\s+)$",
                before,
                re.IGNORECASE,
            )
        )
        if range_cue:
            return TimeDetection(
                match.start(),
                match.end(),
                match.group(),
                None,
                TimeConfidence.RANGE,
                "relative",
                rejection_reason="bare written time range",
            )
        direct_time_cue = re.search(r"\b(?:at|was|is|struck|time)\s*$", before, re.IGNORECASE)
        if amount == 1 and direction in {"past", "after"} and not direct_time_cue:
            return TimeDetection(
                match.start(),
                match.end(),
                match.group(),
                None,
                TimeConfidence.APPROXIMATE,
                "relative",
                rejection_reason="bare one-after phrase lacks a direct clock cue",
            )
        has_time_cue = bool(
            direct_time_cue
            or re.match(
                r"\s+(?:(?:in|during)\s+the|that|this|at)\s+"
                r"(?:morning|afternoon|evening|night)\b",
                after,
                re.IGNORECASE,
            )
        )
        if not has_time_cue:
            return TimeDetection(
                match.start(),
                match.end(),
                match.group(),
                None,
                TimeConfidence.APPROXIMATE,
                "relative",
                rejection_reason="bare relative phrase lacks a strong temporal cue",
            )
    if base_text == "midnight":
        if meridian:
            return TimeDetection(
                match.start(),
                match.end(),
                match.group(),
                None,
                TimeConfidence.INVALID,
                "relative",
                rejection_reason="named time cannot take a meridiem",
            )
        base_minute = 0
        confidence = TimeConfidence.EXACT_24H
        evidence = "named midnight"
    elif base_text == "noon":
        if meridian:
            return TimeDetection(
                match.start(),
                match.end(),
                match.group(),
                None,
                TimeConfidence.INVALID,
                "relative",
                rejection_reason="named time cannot take a meridiem",
            )
        base_minute = 12 * 60
        confidence = TimeConfidence.EXACT_24H
        evidence = "named noon"
    else:
        base_hour = _number(base_text)
        if base_hour is None or not 1 <= base_hour <= 23:
            return TimeDetection(
                match.start(),
                match.end(),
                match.group(),
                None,
                TimeConfidence.INVALID,
                "relative",
                rejection_reason="invalid base hour",
            )
        if base_hour > 12:
            base_minute = base_hour * 60
            confidence = TimeConfidence.EXACT_24H
            evidence = "24-hour base"
        else:
            base_minute, confidence, evidence = _context_resolution(
                text, match.start(), match.end(), base_hour, 0, meridian
            )
            if base_minute is None:
                clockface_minute = ((base_hour % 12) * 60 + delta) % (12 * 60)
                morning = f"{clockface_minute // 60:02d}:{clockface_minute % 60:02d}"
                evening_minute = clockface_minute + 12 * 60
                evening = f"{evening_minute // 60:02d}:{evening_minute % 60:02d}"
                return TimeDetection(
                    match.start(),
                    match.end(),
                    match.group(),
                    None,
                    confidence,
                    "half_past" if amount_text.casefold() == "half" else "relative",
                    f"no strong AM/PM evidence; {morning}|{evening}",
                )
    if base_text not in {"midnight", "noon"} and base_hour <= 12:
        period = 720 if base_minute >= 720 else 0
        resolved_minute = (((base_hour % 12) * 60 + delta) % 720) + period
    else:
        resolved_minute = (base_minute + delta) % 1440
    return TimeDetection(
        match.start(),
        match.end(),
        match.group(),
        resolved_minute,
        confidence,
        "half_past" if amount_text.casefold() == "half" else "relative",
        evidence,
    )


def _numeric_detection(text: str, match: re.Match[str]) -> TimeDetection | None:
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    separator = match.group("separator")
    meridian = match.group("meridian")
    if hour > 23 or minute > 59 or (meridian and not 1 <= hour <= 12):
        return TimeDetection(
            match.start(),
            match.end(),
            match.group(),
            None,
            TimeConfidence.INVALID,
            "numeric",
            rejection_reason="numeric time outside valid range",
        )
    if separator == "." and not meridian:
        return None
    if meridian:
        resolved, confidence, evidence = _context_resolution(
            text, match.start(), match.end(), hour, minute, meridian
        )
    elif hour >= 13 or hour == 0 or (len(match.group("hour")) == 2 and hour < 10):
        before = text[max(0, match.start() - 45) : match.start()]
        after = text[match.end() : min(len(text), match.end() + 20)]
        direct_time_cue = bool(
            re.search(
                r"\b(?:at|by|until|from|before|after|about|around|toward|towards|"
                r"time|timed|read|reads|showed|displayed)\s*$",
                before,
                re.IGNORECASE,
            )
        )
        starts_sentence = not text[: match.start()].strip()
        citation_like_suffix = bool(re.match(r"[⁠\s]*(?:[,;:)]|[–—-][⁠\s]*\d)", after))
        if not direct_time_cue and not (starts_sentence and not citation_like_suffix):
            return TimeDetection(
                match.start(),
                match.end(),
                match.group(),
                None,
                TimeConfidence.APPROXIMATE,
                "numeric",
                rejection_reason="24-hour numeric form lacks a strong temporal cue",
            )
        resolved, confidence, evidence = (
            hour * 60 + minute,
            TimeConfidence.EXACT_24H,
            ("24-hour form with temporal cue"),
        )
    else:
        resolved, confidence, evidence = _context_resolution(
            text, match.start(), match.end(), hour, minute, None
        )
    return TimeDetection(
        match.start(),
        match.end(),
        match.group(),
        resolved,
        confidence,
        "numeric",
        evidence,
    )


def _oclock_detection(text: str, match: re.Match[str]) -> TimeDetection:
    hour = _number(match.group("hour"))
    meridian = match.group("meridian")
    if hour is None or not 1 <= hour <= 12:
        return TimeDetection(
            match.start(),
            match.end(),
            match.group(),
            None,
            TimeConfidence.INVALID,
            "oclock",
            rejection_reason="invalid o'clock hour",
        )
    resolved, confidence, evidence = _context_resolution(
        text, match.start(), match.end(), hour, 0, meridian
    )
    return TimeDetection(
        match.start(), match.end(), match.group(), resolved, confidence, "oclock", evidence
    )


def _written_clock_detection(text: str, match: re.Match[str]) -> TimeDetection | None:
    before = text[max(0, match.start() - 18) : match.start()]
    if not re.search(r"\b(?:at|by|until|exactly|struck|was)\s*$", before, re.IGNORECASE):
        return None
    hour = _number(match.group("hour"))
    minute = _number(match.group("minute"))
    meridian = match.group("meridian")
    if hour is None or minute is None or not 1 <= hour <= 12 or not 0 <= minute <= 59:
        return None
    resolved, confidence, evidence = _context_resolution(
        text, match.start(), match.end(), hour, minute, meridian
    )
    return TimeDetection(
        match.start(),
        match.end(),
        match.group(),
        resolved,
        confidence,
        "written_clock",
        evidence,
    )


def _named_detection(text: str, match: re.Match[str]) -> TimeDetection | None:
    name = match.group("name").casefold()
    after = text[match.end() : min(len(text), match.end() + 12)].casefold()
    before = text[max(0, match.start() - 18) : match.start()].casefold()
    if name == "midnight" and re.match(r"\s+(?:blue|sun|oil)\b", after):
        return None
    if re.search(
        r"\b(?:about|around|nearly|almost|roughly|approximately|by|before|after|"
        r"past|near|toward|towards)\s+$",
        before,
    ) or re.match(r"\s+(?:had|has)\s+passed\b", after):
        return TimeDetection(
            match.start(),
            match.end(),
            match.group(),
            None,
            TimeConfidence.APPROXIMATE,
            "named_time",
            rejection_reason="approximate named time",
        )
    if name == "midday" and not (
        re.search(r"\b(?:at|by|until|exactly|toward|towards)\s*$", before)
        or re.match(r"\s+(?:came|arrived|struck)\b", after)
    ):
        return None
    minute = 0 if name == "midnight" else 12 * 60
    clear_temporal_use = bool(
        re.search(
            r"\b(?:at|until|from|since|till|through|exactly|struck|was|is)\s+"
            r"(?:a\s+|the\s+)?(?:high\s+)?$",
            before,
        )
        or re.match(r"\s+(?:came|comes|arrived|struck)\b", after)
        or (name == "noon" and re.search(r"\bhigh\s+$", before))
    )
    if not clear_temporal_use:
        return TimeDetection(
            match.start(),
            match.end(),
            match.group(),
            None,
            TimeConfidence.APPROXIMATE,
            "named_time",
            rejection_reason="named time lacks exact temporal syntax",
        )
    return TimeDetection(
        match.start(),
        match.end(),
        match.group(),
        minute,
        TimeConfidence.EXACT_24H,
        "named_time",
        f"named {name}",
    )


def _remove_overlaps(
    detections: list[tuple[int, TimeDetection]],
) -> list[TimeDetection]:
    selected: list[TimeDetection] = []
    for _, candidate in sorted(
        detections,
        key=lambda item: (item[0], item[1].end - item[1].start),
        reverse=True,
    ):
        if any(candidate.start < other.end and other.start < candidate.end for other in selected):
            continue
        selected.append(candidate)
    return sorted(selected, key=lambda detection: (detection.start, detection.end))


def false_positive_category(text: str, detection: TimeDetection) -> str | None:
    """Classify numeric reference syntax that looks like a clock time."""
    if detection.parser_rule == "relative":
        before = text[max(0, detection.start - 180) : detection.start]
        after = text[detection.end : min(len(text), detection.end + 100)]
        if re.match(
            r"\s+(?:hours?|million|billion|degrees?|feet|foot|inches?|yards?|"
            r"miles?|kilomet(?:er|re)s?|centimet(?:er|re)s?|meters?|metres?)\b",
            after,
            re.IGNORECASE,
        ):
            return "DIMENSION_RATIO"
        if re.search(
            r"\b(?:feet|foot|inches?|yards?|met(?:er|re)s?|centimet(?:er|re)s?)\s+and\s+$",
            before,
            re.IGNORECASE,
        ):
            return "DIMENSION_RATIO"
        direct_time_cue = re.search(r"\b(?:at|by|until|from)\s+$", before, re.I)
        betting_context = re.search(
            r"\b(?:bet|bets|betting|betted|wager|wagered|wagering|odds|"
            r"win|wins|winning|won|odds-on|got\s+down)\b[^.!?]{0,170}$",
            before,
            re.I,
        )
        ratio_context = re.search(
            r"\b(?:score|scored|beat|lost|ratio)\b[^.!?]{0,45}$", before, re.I
        ) or re.match(r"[^.!?]{0,35}\b(?:odds|ratio|score|inning|game)\b", after, re.I)
        if betting_context or (ratio_context and not direct_time_cue):
            return "SCORE_RATIO"
        if re.search(r"\bto\s+one$", detection.text, re.I) and re.match(r"\s+that\b", after, re.I):
            return "SCORE_RATIO"
        if re.match(r"\s*,\s*or\s+\w+(?:[-\s]+\w+)*\s+to\s+one\b", after, re.I):
            return "SCORE_RATIO"
        return None
    if detection.parser_rule not in {"numeric", "numeric_range", "written_clock"}:
        return None
    if detection.parser_rule == "written_clock" and not re.search(
        r"\d\s*[-.:]\s*\d", detection.text
    ):
        return None
    before = text[max(0, detection.start - 100) : detection.start]
    after = text[detection.end : min(len(text), detection.end + 100)]
    if re.search(
        rf"(?:\b(?:chapter|chap\.?|verse|verses|{_SCRIPTURE_NAMES})[,;:]?\s+"
        rf"|\b(?:{_SCRIPTURE_ABBREVIATIONS})\.?\s+"
        r"|\b(?:i|ii|iii|iv|1|2|3)\s+(?:john|peter|samuel|kings|chronicles|"
        rf"corinthians|thessalonians|timothy|{_SCRIPTURE_ABBREVIATIONS})\.?\s+)$",
        before,
        re.IGNORECASE,
    ):
        return "CHAPTER_VERSE"
    if not before.strip() and re.match(r"\s*[,;:)]", after):
        return "CHAPTER_VERSE"
    if re.match(
        rf"\s*,?\s*(?:{_SCRIPTURE_NAMES}|{_SCRIPTURE_ABBREVIATIONS})\.?\b",
        after,
        re.I,
    ):
        return "CHAPTER_VERSE"
    if re.search(r"(?:\(|\[)\s*(?:see|cf\.?|compare)\b[^()[\]]{0,70}$", before, re.I):
        return "PAGE_LINE_REFERENCE"
    if re.match(r"\s*(?:,\s*\d+|[-–—]\s*\d+|\))", after) and not re.search(
        r"\b(?:at|by|until|from|was|is)\s*$", before, re.I
    ):
        return "PAGE_LINE_REFERENCE"
    if "-" in detection.text and re.search(r"\b(?:at|supra|infra|see)\s+$", before, re.I):
        return "PAGE_LINE_REFERENCE"
    if re.search(r"\b(?:time|record)\s+for\s+(?:the\s+)?distance\s+(?:was|is)\s+$", before, re.I):
        return "SCORE_RATIO"
    if re.match(r"\s+\d+\s*/\s*\d+", after):
        return "SCORE_RATIO"
    if re.search(
        r"\b(?:record|distance|sprint|race|lap|mile|paced|time)\b[^.!?]{0,70}$",
        before,
        re.I,
    ) and re.match(r"[^.!?]{0,80}\b(?:record|distance|sprint|race|lap|mile|clip)\b", after, re.I):
        return "SCORE_RATIO"
    if re.match(r"\s*,\s*(?:fig(?:ure)?\.?|table|col(?:umn)?\.?)\b", after, re.I):
        return "PAGE_LINE_REFERENCE"
    if re.search(r"\b[IVXLCDM]+:\s*$", before, re.IGNORECASE):
        return "PAGE_LINE_REFERENCE"
    if re.search(r"(?:§\s*|\b(?:article|section|statute|clause|subsection)\s+)$", before, re.I):
        return "LEGAL_REFERENCE"
    if re.search(r"\b(?:pages?|pp\.?|lines?|ll\.?|figures?|fig\.?|tables?)\s+$", before, re.I):
        return "PAGE_LINE_REFERENCE"
    if re.match(
        r"\s*(?:proportions?|ratio|scale|inches?|feet|ft\.?|centimet(?:er|re)s?|mm\b|cm\b)",
        after,
        re.I,
    ):
        return "DIMENSION_RATIO"
    if re.search(
        r"\b(?:score|scored|beat|won|lost|odds|ratio)\s*(?:of|was|is|:)??\s*$", before, re.I
    ):
        return "SCORE_RATIO"
    if re.search(
        r"\b(?:catalog(?:ue)?|serial|model|item|code|reference|ref\.?|number|no\.?)\s*"
        r"(?:is|was|:|#)?\s*$",
        before,
        re.I,
    ):
        return "DATE_CATALOG_CODE"
    if re.search(r"\b(?:dated?|born|died)\s+$", before, re.I) or re.match(
        r"\s*[/.-]\s*\d{2,4}\b", after
    ):
        return "DATE_CATALOG_CODE"
    return None


def _reject_false_positive(text: str, detection: TimeDetection) -> TimeDetection:
    category = false_positive_category(text, detection)
    if category is None:
        return detection
    return replace(
        detection,
        minute_of_day=None,
        confidence=TimeConfidence.INVALID,
        ampm_evidence=None,
        rejection_reason=f"false_positive:{category}",
    )


def _mark_narrative_ranges(text: str, detections: list[TimeDetection]) -> list[TimeDetection]:
    """Mark paired start/end clock mentions in one clause as a range."""
    ranged: set[int] = set()
    excluded = {
        TimeConfidence.INVALID,
        TimeConfidence.APPROXIMATE,
        TimeConfidence.RANGE,
    }
    for first_index, first in enumerate(detections):
        if first.confidence in excluded:
            continue
        for second_index in range(first_index + 1, len(detections)):
            second = detections[second_index]
            if second.start - first.end > 100 or second.confidence in excluded:
                break
            prefix = text[max(0, first.start - 70) : first.start]
            between = text[first.end : second.start]
            if re.search(
                r"\b(?:began|started|commenced)\b[^.!?]{0,60}$", prefix, re.I
            ) and re.search(
                r"\b(?:ended|finished|stopped|concluded)\b[^.!?]{0,25}$", between, re.I
            ):
                ranged.update((first_index, second_index))
    return [
        replace(
            detection,
            minute_of_day=None,
            confidence=TimeConfidence.RANGE,
            ampm_evidence=None,
            rejection_reason="narrative start/end time range",
        )
        if index in ranged
        else detection
        for index, detection in enumerate(detections)
    ]


def detect_time_expressions(text: str) -> list[TimeDetection]:
    """Return conservative, non-overlapping time detections with exact source offsets."""
    found: list[tuple[int, TimeDetection]] = []
    for pattern, rule in (
        (_NUMERIC_RANGE_RE, "numeric_range"),
        (_WRITTEN_RANGE_RE, "written_range"),
    ):
        for match in pattern.finditer(text):
            if rule == "written_range" and not re.search(
                rf"{_OCLOCK_PATTERN}|\s+[ap]\s*\.?\s*m\.?\b|"
                r"\s+(?:in the )?(?:morning|forenoon|afternoon|evening|night)\b",
                text[match.start() : min(len(text), match.end() + 30)],
                re.IGNORECASE,
            ):
                continue
            found.append(
                (
                    120,
                    TimeDetection(
                        match.start(),
                        match.end(),
                        match.group(),
                        None,
                        TimeConfidence.RANGE,
                        rule,
                        rejection_reason="time range",
                    ),
                )
            )
    for match in _NAMED_APPROXIMATE_RE.finditer(text):
        found.append(
            (
                95,
                TimeDetection(
                    match.start(),
                    match.end(),
                    match.group(),
                    None,
                    TimeConfidence.APPROXIMATE,
                    "approximate",
                    rejection_reason="approximate named time",
                ),
            )
        )
    for match in _BARE_APPROXIMATE_RE.finditer(text):
        trailing_word = re.match(r"\s+([A-Za-z]+)", text[match.end() :])
        if trailing_word and trailing_word.group(1).casefold() not in {
            "at",
            "in",
            "that",
            "this",
            "tonight",
        }:
            continue
        found.append(
            (
                90,
                TimeDetection(
                    match.start(),
                    match.end(),
                    match.group(),
                    None,
                    TimeConfidence.APPROXIMATE,
                    "approximate",
                    rejection_reason="approximate expression",
                ),
            )
        )
    for match in _RELATIVE_RE.finditer(text):
        if match.group("amount").isdigit() and "minute" not in match.group().casefold():
            continue
        found.append((100, _with_approximate_prefix(text, _relative_detection(text, match))))
    for match in _MILITARY_RE.finditer(text):
        hour, minute = int(match.group("hour")), int(match.group("minute"))
        valid = 0 <= hour <= 23 and 0 <= minute <= 59
        found.append(
            (
                80,
                _with_approximate_prefix(
                    text,
                    TimeDetection(
                        match.start(),
                        match.end(),
                        match.group(),
                        hour * 60 + minute if valid else None,
                        TimeConfidence.EXACT_24H if valid else TimeConfidence.INVALID,
                        "military",
                        "military hours" if valid else None,
                        None if valid else "military time outside valid range",
                    ),
                ),
            )
        )
    for match in _NUMERIC_RE.finditer(text):
        detection = _numeric_detection(text, match)
        if detection:
            found.append((75, _with_approximate_prefix(text, detection)))
    for match in _WRITTEN_CLOCK_RE.finditer(text):
        detection = _written_clock_detection(text, match)
        if detection:
            found.append((70, _with_approximate_prefix(text, detection)))
    for match in _OCLOCK_RE.finditer(text):
        found.append((65, _with_approximate_prefix(text, _oclock_detection(text, match))))
    for match in _NAMED_RE.finditer(text):
        detection = _named_detection(text, match)
        if detection:
            found.append((55, _with_approximate_prefix(text, detection)))
    detections = [_reject_false_positive(text, detection) for detection in _remove_overlaps(found)]
    return _mark_narrative_ranges(text, detections)
