"""Conservative French literary time-expression parsing.

The parser never rewrites source text.  Every offset indexes the original Python
string, while ``possible_minutes`` carries unresolved clock-face alternatives.
"""

from __future__ import annotations

import re
import unicodedata

from litclock.models import CandidateConfidence, TimeConfidence, TimeDetection

_SEPARATOR_RE = re.compile(r"[\s\u00a0\-\u2010-\u2015]+")


def _number_forms() -> dict[str, int]:
    forms: dict[str, int] = {
        "zéro": 0,
        "zero": 0,
        "un": 1,
        "une": 1,
        "deux": 2,
        "trois": 3,
        "quatre": 4,
        "cinq": 5,
        "six": 6,
        "sept": 7,
        "huit": 8,
        "neuf": 9,
        "dix": 10,
        "onze": 11,
        "douze": 12,
        "treize": 13,
        "quatorze": 14,
        "quinze": 15,
        "seize": 16,
    }
    ones = {
        1: "un",
        2: "deux",
        3: "trois",
        4: "quatre",
        5: "cinq",
        6: "six",
        7: "sept",
        8: "huit",
        9: "neuf",
    }
    for value in range(17, 20):
        forms[f"dix {ones[value - 10]}"] = value
    for tens, word in ((20, "vingt"), (30, "trente"), (40, "quarante"), (50, "cinquante")):
        forms[word] = tens
        for unit, unit_word in ones.items():
            joiner = " et " if unit == 1 else " "
            forms[f"{word}{joiner}{unit_word}"] = tens + unit
    forms["soixante"] = 60
    for unit, unit_word in ones.items():
        joiner = " et " if unit == 1 else " "
        forms[f"soixante{joiner}{unit_word}"] = 60 + unit
    forms["soixante dix"] = 70
    return forms


_NUMBER_FORMS = _number_forms()


def _form_pattern(form: str) -> str:
    parts = form.split()
    return r"(?:[\s\u00a0\-\u2010-\u2015]+)".join(re.escape(part) for part in parts)


_NUMBER_PATTERN = (
    "(?:"
    + "|".join(
        [_form_pattern(form) for form in sorted(_NUMBER_FORMS, key=lambda item: (-len(item), item))]
        + [r"\d{1,2}"]
    )
    + ")"
)
_APOSTROPHE = "['\u2019]"
_DAYPART_PATTERN = (
    rf"(?P<daypart>du[\s\u00a0]+matin|du[\s\u00a0]+soir|"
    rf"de[\s\u00a0]+l{_APOSTROPHE}après-midi|de[\s\u00a0]+l{_APOSTROPHE}apres-midi)"
)

_NAMED_RE = re.compile(
    r"(?<!\w)(?P<name>midi|minuit)"
    rf"(?:"
    rf"[\s\u00a0]+et[\s\u00a0]+(?P<addition>quart|demi)"
    rf"|[\s\u00a0]+moins[\s\u00a0]+(?:(?P<named_minus_quarter>le[\s\u00a0]+quart)|"
    rf"(?P<named_minus>{_NUMBER_PATTERN}))"
    rf"|[\s\u00a0]+(?P<named_quarter>un[\s\u00a0]+quart)"
    rf"|[\s\u00a0]+(?P<named_minute>{_NUMBER_PATTERN})"
    rf")?(?!\w)",
    re.IGNORECASE,
)
_CLOCK_RE = re.compile(
    rf"(?<!\w)(?P<hour>{_NUMBER_PATTERN})[\s\u00a0]+heures?"
    rf"(?:"
    rf"[\s\u00a0]+et[\s\u00a0]+(?P<quarter>(?:un[\s\u00a0]+)?quart)"
    rf"|[\s\u00a0]+et[\s\u00a0]+(?P<half>demi(?:e)?)"
    rf"|[\s\u00a0]+moins[\s\u00a0]+(?:(?P<minus_quarter>le[\s\u00a0]+quart)|"
    rf"(?P<minus>{_NUMBER_PATTERN}))"
    rf"|[\s\u00a0]+(?P<minute>{_NUMBER_PATTERN})"
    rf")?"
    rf"(?:[\s\u00a0]+{_DAYPART_PATTERN})?(?!\w)",
    re.IGNORECASE,
)
_NUMERIC_H_RE = re.compile(
    rf"(?<![\w.])(?P<hour>\d{{1,2}})[\s\u00a0]*h(?:eures?)?"
    rf"(?:[\s\u00a0]*(?P<minute>\d{{1,2}}))?"
    rf"(?:[\s\u00a0]+{_DAYPART_PATTERN})?(?!\w)",
    re.IGNORECASE,
)
_APPROXIMATE_PREFIX_RE = re.compile(
    r"(?:\bvers(?:\s+(?:les?|la|l['’]))?\s+|"
    r"\b(?:environ|presque|approximativement|bientôt)\s+|"
    r"\b(?:près|plus)\s+de\s+|\bsur\s+le\s+|\b(?:avant|après)\s+|"
    r"\bil\s+(?:pouvait|devait)\s+être\s+)$",
    re.IGNORECASE,
)
_APPROXIMATE_SUFFIX_RE = re.compile(
    r"^\s*(?:environ|à\s+peu\s+près|passé(?:e)?s?|près)\b", re.IGNORECASE
)
_ALTERNATIVE_PREFIX_RE = re.compile(r"\bou\s+$", re.IGNORECASE)
_RANGE_PREFIX_RE = re.compile(r"(?:\bde\s+|\bentre\s+[^,;.!?]{0,30})$", re.IGNORECASE)
_RANGE_SUFFIX_RE = re.compile(r"^\s*(?:à|et|jusqu['’]à)\s+", re.IGNORECASE)


def parse_french_number(value: str) -> int | None:
    """Parse the cardinal forms used by the grammar without folding source text."""
    normalized = unicodedata.normalize("NFC", value).casefold().strip()
    if normalized.isdigit():
        return int(normalized)
    normalized = _SEPARATOR_RE.sub(" ", normalized)
    return _NUMBER_FORMS.get(normalized)


def _clockface_possibilities(hour: int, minute: int) -> tuple[int, ...]:
    if hour == 12:
        return (minute, 12 * 60 + minute)
    return (hour * 60 + minute, (hour + 12) * 60 + minute)


def _daypart_resolution(hour: int, minute: int, daypart: str) -> tuple[int | None, str]:
    normalized = unicodedata.normalize("NFC", daypart).casefold().replace("’", "'")
    if normalized == "du matin":
        if 1 <= hour <= 11:
            return hour * 60 + minute, "explicit morning"
        if hour == 12:
            return minute, "explicit morning; twelve interpreted as midnight"
    if normalized in {"du soir", "de l'après-midi", "de l'apres-midi"}:
        if 1 <= hour <= 11:
            return (hour + 12) * 60 + minute, f"explicit {normalized}"
        if hour == 12:
            return 12 * 60 + minute, f"explicit {normalized}"
    return None, f"daypart {daypart!r} conflicts with {hour}-hour expression"


def _candidate(
    text: str,
    match: re.Match[str],
    *,
    hour: int | None,
    minute: int | None,
    parser_rule: str,
    daypart: str | None = None,
    named_minute: int | None = None,
) -> TimeDetection:
    matched = match.group()
    prefix = text[max(0, match.start() - 45) : match.start()]
    suffix = text[match.end() : min(len(text), match.end() + 45)]
    if named_minute is not None:
        if _APPROXIMATE_PREFIX_RE.search(prefix) or _APPROXIMATE_SUFFIX_RE.match(suffix):
            return TimeDetection(
                match.start(),
                match.end(),
                matched,
                None,
                TimeConfidence.APPROXIMATE,
                parser_rule,
                rejection_reason="approximate French modifier",
                language="fr",
                possible_minutes=(named_minute,),
                semantic_type="APPROXIMATE",
                candidate_confidence=CandidateConfidence.MEDIUM,
            )
        return TimeDetection(
            match.start(),
            match.end(),
            matched,
            named_minute,
            TimeConfidence.EXACT_24H,
            parser_rule,
            "explicit midi/minuit",
            language="fr",
            possible_minutes=(named_minute,),
            semantic_type=(
                "NOON"
                if matched.casefold() == "midi"
                else "MIDNIGHT"
                if matched.casefold() == "minuit"
                else "RESOLVED_24H"
            ),
            candidate_confidence=CandidateConfidence.HIGH,
        )
    if hour is None or minute is None or not 0 <= minute <= 59 or not 1 <= hour <= 24:
        return TimeDetection(
            match.start(),
            match.end(),
            matched,
            None,
            TimeConfidence.INVALID,
            parser_rule,
            rejection_reason="invalid French clock value",
            language="fr",
            semantic_type="INVALID",
            candidate_confidence=CandidateConfidence.REJECT,
        )
    was_twenty_four = hour == 24
    if was_twenty_four:
        if minute:
            return TimeDetection(
                match.start(),
                match.end(),
                matched,
                None,
                TimeConfidence.INVALID,
                parser_rule,
                rejection_reason="24 heures only permits minute zero",
                language="fr",
                semantic_type="INVALID",
                candidate_confidence=CandidateConfidence.REJECT,
            )
        hour = 0

    if _APPROXIMATE_PREFIX_RE.search(prefix) or _APPROXIMATE_SUFFIX_RE.match(suffix):
        if daypart:
            resolved, _ = _daypart_resolution(hour, minute, daypart)
            possible = (
                (resolved,) if resolved is not None else _clockface_possibilities(hour, minute)
            )
        else:
            possible = (
                (hour * 60 + minute,)
                if hour > 12 or was_twenty_four
                else _clockface_possibilities(hour, minute)
            )
        return TimeDetection(
            match.start(),
            match.end(),
            matched,
            None,
            TimeConfidence.APPROXIMATE,
            parser_rule,
            rejection_reason="approximate French modifier",
            language="fr",
            possible_minutes=possible,
            semantic_type="APPROXIMATE",
            candidate_confidence=CandidateConfidence.MEDIUM,
        )
    if _ALTERNATIVE_PREFIX_RE.search(prefix):
        if daypart:
            resolved, _ = _daypart_resolution(hour, minute, daypart)
            possible = (
                (resolved,) if resolved is not None else _clockface_possibilities(hour, minute)
            )
        else:
            possible = (
                (hour * 60 + minute,)
                if hour > 12 or was_twenty_four
                else _clockface_possibilities(hour, minute)
            )
        return TimeDetection(
            match.start(),
            match.end(),
            matched,
            None,
            TimeConfidence.RANGE,
            parser_rule,
            rejection_reason="French alternative/range time",
            language="fr",
            possible_minutes=possible,
            semantic_type="RANGE",
            candidate_confidence=CandidateConfidence.MEDIUM,
        )
    if _RANGE_PREFIX_RE.search(prefix) and _RANGE_SUFFIX_RE.match(suffix):
        return TimeDetection(
            match.start(),
            match.end(),
            matched,
            None,
            TimeConfidence.RANGE,
            parser_rule,
            rejection_reason="French time range",
            language="fr",
            semantic_type="RANGE",
            candidate_confidence=CandidateConfidence.MEDIUM,
        )

    if hour > 12 or hour == 0:
        resolved = hour * 60 + minute
        if daypart:
            return TimeDetection(
                match.start(),
                match.end(),
                matched,
                None,
                TimeConfidence.INVALID,
                parser_rule,
                rejection_reason="daypart attached to an explicit 24-hour value",
                language="fr",
                possible_minutes=(resolved,),
                semantic_type="INVALID",
                candidate_confidence=CandidateConfidence.REJECT,
            )
        return TimeDetection(
            match.start(),
            match.end(),
            matched,
            resolved,
            TimeConfidence.EXACT_24H,
            parser_rule,
            (
                "French vingt-quatre heures retained for review"
                if was_twenty_four
                else "explicit French 24-hour expression"
            ),
            language="fr",
            possible_minutes=(resolved,),
            semantic_type="RESOLVED_24H",
            candidate_confidence=(
                CandidateConfidence.MEDIUM if was_twenty_four else CandidateConfidence.HIGH
            ),
        )
    if daypart:
        resolved, evidence = _daypart_resolution(hour, minute, daypart)
        if resolved is not None:
            return TimeDetection(
                match.start(),
                match.end(),
                matched,
                resolved,
                TimeConfidence.EXACT_CONTEXTUAL,
                parser_rule,
                evidence,
                language="fr",
                possible_minutes=(resolved,),
                semantic_type="RESOLVED_24H",
                candidate_confidence=CandidateConfidence.HIGH,
            )
        return TimeDetection(
            match.start(),
            match.end(),
            matched,
            None,
            TimeConfidence.INVALID,
            parser_rule,
            rejection_reason=evidence,
            language="fr",
            semantic_type="INVALID",
            candidate_confidence=CandidateConfidence.REJECT,
        )
    possible = _clockface_possibilities(hour, minute)
    rendered = "|".join(f"{value // 60:02d}:{value % 60:02d}" for value in possible)
    return TimeDetection(
        match.start(),
        match.end(),
        matched,
        None,
        TimeConfidence.AMPM_AMBIGUOUS,
        parser_rule,
        f"French 12-hour expression without daypart; {rendered}",
        language="fr",
        possible_minutes=possible,
        semantic_type="CLOCKFACE_12H",
        candidate_confidence=CandidateConfidence.AMBIGUOUS_CLOCKFACE,
    )


def _clock_detection(text: str, match: re.Match[str], *, numeric_h: bool = False) -> TimeDetection:
    hour = parse_french_number(match.group("hour"))
    minute = 0
    parser_rule = "fr_hour"
    if not numeric_h and match.group("quarter"):
        minute, parser_rule = 15, "fr_et_quart"
    elif not numeric_h and match.group("half"):
        minute, parser_rule = 30, "fr_et_demie"
    elif not numeric_h and match.group("minus_quarter"):
        if hour is not None:
            hour = 12 if hour == 1 else hour - 1
        minute, parser_rule = 45, "fr_moins_le_quart"
    elif not numeric_h and match.group("minus"):
        amount = parse_french_number(match.group("minus"))
        if hour is not None and amount is not None and 1 <= amount <= 59:
            hour = 12 if hour == 1 else hour - 1
            minute = 60 - amount
        else:
            minute = amount
        parser_rule = "fr_moins"
    elif match.group("minute"):
        minute = parse_french_number(match.group("minute"))
        parser_rule = "fr_numeric_h" if numeric_h else "fr_hour_minute"
    return _candidate(
        text,
        match,
        hour=hour,
        minute=minute,
        parser_rule=parser_rule,
        daypart=match.group("daypart"),
    )


def detect_french_time_expressions(text: str) -> list[TimeDetection]:
    """Return non-overlapping French time candidates in source order."""
    detections: list[TimeDetection] = []
    for match in _NAMED_RE.finditer(text):
        if text[max(0, match.start() - 6) : match.start()].casefold() in {"après-", "apres-"}:
            continue
        base = 12 * 60 if match.group("name").casefold() == "midi" else 0
        addition = match.group("addition")
        if match.group("named_minus_quarter"):
            minute = (base - 15) % (24 * 60)
        elif match.group("named_minus"):
            amount = parse_french_number(match.group("named_minus"))
            minute = (
                (base - amount) % (24 * 60) if amount is not None and 1 <= amount <= 59 else None
            )
        elif match.group("named_quarter"):
            minute = base + 15
        elif match.group("named_minute"):
            amount = parse_french_number(match.group("named_minute"))
            minute = base + amount if amount is not None and 0 <= amount <= 59 else None
        else:
            minute = base + (
                15 if addition and addition.casefold() == "quart" else 30 if addition else 0
            )
        detections.append(
            _candidate(
                text,
                match,
                hour=None,
                minute=None,
                parser_rule="fr_midi_minuit",
                named_minute=minute,
            )
        )
    for match in _CLOCK_RE.finditer(text):
        detections.append(_clock_detection(text, match))
    occupied = [(item.start, item.end) for item in detections]
    for match in _NUMERIC_H_RE.finditer(text):
        if any(start < match.end() and match.start() < end for start, end in occupied):
            continue
        detections.append(_clock_detection(text, match, numeric_h=True))
    detections.sort(key=lambda item: (item.start, -(item.end - item.start)))
    return detections
