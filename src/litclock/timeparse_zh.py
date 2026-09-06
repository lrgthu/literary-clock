"""Precision-first Simplified/Traditional Chinese literary time parsing."""

from __future__ import annotations

import re

from litclock.models import CandidateConfidence, TimeConfidence, TimeDetection

_DIGITS = {
    "〇": 0,
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "兩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_NUMERAL = r"(?:\d{1,2}|[〇零一二两兩三四五六七八九十]{1,4})"
_DAYPART_FORMS = (
    "半夜裡",
    "半夜里",
    "凌晨",
    "清晨",
    "早上",
    "上午",
    "中午",
    "午後",
    "下午",
    "傍晚",
    "晚上",
    "夜間",
    "夜间",
    "夜里",
    "夜裡",
    "半夜",
)
_DAYPART = "(?:" + "|".join(_DAYPART_FORMS) + ")"
_MARKER = r"(?:点|點|时|時)"
_NUMERAL_CHAR = r"〇零一二两兩三四五六七八九十\d"
_TIME_CHAR = r"〇零一二两兩三四五六七八九十\d点點时時分刻钟鐘"
_CLOCK_SUFFIX = (
    rf"(?:(?P<whole>整)|(?P<half>半)|(?P<quarter>{_NUMERAL})刻|(?P<minute>{_NUMERAL})分)?"
)

_DIFFERENCE_RE = re.compile(
    rf"(?<![{_NUMERAL_CHAR}])(?P<daypart>{_DAYPART})?"
    rf"差(?:(?P<quarter>{_NUMERAL})刻|(?P<minute>{_NUMERAL})分?)"
    rf"(?P<hour>{_NUMERAL}){_MARKER}(?P<bell>钟|鐘)?(?![{_TIME_CHAR}])"
)
_CLOCK_RE = re.compile(
    rf"(?<![{_NUMERAL_CHAR}])(?P<daypart>{_DAYPART})?"
    rf"(?P<hour>{_NUMERAL})(?P<marker>{_MARKER})(?P<bell>钟|鐘)?{_CLOCK_SUFFIX}"
    rf"(?![{_TIME_CHAR}])"
)
_TEMPORAL_PREFIX_RE = re.compile(
    r"(?:到了?|已(?:经|經)?(?:到|是)?|正好(?:是|到)?|恰好(?:是|到)?|"
    r"时钟敲了|時鐘敲了|钟(?:声|聲)?敲了|鐘(?:聲)?敲了|报时到|報時到|"
    r"时间是|時間是|约在|約在|在|至)\s*$"
)
_TEMPORAL_SUFFIX_RE = re.compile(
    r"^\s*(?:整|左右|前后|前後|的时候|的時候|时|時|才|已经|已經|到了|响了|響了)"
)
_FALSE_SUFFIX_RE = re.compile(
    r"^\s*(?:也不|儿?也不|辦法|办法|意見|意见|建議|建议|要求|說明|说明|"
    r"原因|好處|好处|優勢|优势|看法|疑問|疑问|補充|补充)"
)
_APPROXIMATE_PREFIX_RE = re.compile(
    r"(?:大約|大约|約|约|將近|将近|差不多|快到|一過|一过|過了|过了|未到|不到)\s*$"
)
_APPROXIMATE_SUFFIX_RE = re.compile(
    r"^\s*(?:多(?:钟|鐘|種)?|來(?:钟|鐘)|来(?:钟|鐘)|左右|光景|前後|前后|"
    r"以前|以後|之后|之後|之前|[前後后])"
)
_NEAR_DAYPART_RE = re.compile(
    rf"(?P<daypart>{_DAYPART})(?:的|裡|里|已(?:經|经)?(?:是|到)?|到了?|約|约|\s){{0,6}}$"
)


def parse_chinese_number(value: str) -> int | None:
    """Parse Arabic or common Chinese cardinal numerals through 99."""
    value = value.strip()
    if not value:
        return None
    if value.isascii() and value.isdigit():
        return int(value)
    if all(char in _DIGITS for char in value):
        if len(value) > 1 and value[0] not in {"〇", "零"}:
            return None
        digits = "".join(str(_DIGITS[char]) for char in value)
        return int(digits)
    if value.count("十") == 1:
        before, after = value.split("十")
        if len(before) > 1 or len(after) > 1:
            return None
        tens = 1 if not before else _DIGITS.get(before)
        ones = 0 if not after else _DIGITS.get(after)
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    return None


def _clockface_possibilities(hour: int, minute: int) -> tuple[int, ...]:
    if hour == 12:
        return (minute, 720 + minute)
    return (hour * 60 + minute, (hour + 12) * 60 + minute)


def _resolve_daypart(hour: int, minute: int, daypart: str) -> tuple[int | None, str]:
    if daypart in {"凌晨", "清晨"}:
        if hour == 12:
            return minute, "explicit 凌晨; 十二 interpreted as midnight"
        if 0 <= hour <= 5:
            return hour * 60 + minute, "explicit 凌晨"
    elif daypart in {"早上", "上午"}:
        if 1 <= hour <= 11:
            return hour * 60 + minute, f"explicit {daypart}"
        if hour == 12:
            return None, f"{daypart}十二点 is conservatively unresolved"
    elif daypart == "中午":
        if hour in {11, 12}:
            return hour * 60 + minute, "explicit 中午"
        if hour == 1:
            return 13 * 60 + minute, "explicit 中午"
    elif daypart in {"下午", "午後"}:
        if hour == 12:
            return 12 * 60 + minute, "explicit 下午"
        if 1 <= hour <= 11:
            return (hour + 12) * 60 + minute, "explicit 下午"
    elif daypart == "傍晚":
        if 5 <= hour <= 7:
            return (hour + 12) * 60 + minute, "explicit 傍晚"
    elif daypart in {"晚上", "夜間", "夜间", "夜里", "夜裡"}:
        if hour == 12:
            return minute, f"explicit {daypart}; 十二 interpreted as midnight"
        if 6 <= hour <= 11:
            return (hour + 12) * 60 + minute, f"explicit {daypart}"
        if 1 <= hour <= 4:
            return hour * 60 + minute, f"explicit {daypart}; post-midnight hour"
    elif daypart in {"半夜", "半夜裡", "半夜里"}:
        if hour == 12:
            return minute, "explicit 半夜; 十二 interpreted as midnight"
        if 1 <= hour <= 4:
            return hour * 60 + minute, "explicit 半夜"
    return None, f"daypart {daypart} does not conservatively resolve hour {hour}"


def _rejected(
    match: re.Match[str], parser_rule: str, reason: str, possible: tuple[int, ...] = ()
) -> TimeDetection:
    return TimeDetection(
        match.start(),
        match.end(),
        match.group(),
        None,
        TimeConfidence.INVALID,
        parser_rule,
        rejection_reason=reason,
        language="zh",
        possible_minutes=possible,
        semantic_type="INVALID",
        candidate_confidence=CandidateConfidence.REJECT,
    )


def _build_detection(
    text: str,
    match: re.Match[str],
    *,
    hour: int | None,
    minute: int | None,
    parser_rule: str,
    structurally_explicit: bool,
) -> TimeDetection:
    if hour is None or minute is None or not 0 <= hour <= 24 or not 0 <= minute <= 59:
        return _rejected(match, parser_rule, "invalid Chinese clock value")
    if hour == 24:
        if minute:
            return _rejected(match, parser_rule, "二十四点 only permits minute zero")
        hour = 0

    before = text[max(0, match.start() - 24) : match.start()]
    after = text[match.end() : min(len(text), match.end() + 24)]
    if match.group() in {"一时一刻", "一時一刻"} or (
        match.group() in {"一时半", "一時半"} and after.startswith("晌")
    ):
        return _rejected(match, parser_rule, "false_positive:IDIOMATIC_DURATION")
    if (match.start() and text[match.start() - 1] == "第") or _FALSE_SUFFIX_RE.match(after):
        return _rejected(match, parser_rule, "false_positive:NON_TEMPORAL_POINT")

    daypart = match.group("daypart")
    if not daypart and (near_daypart := _NEAR_DAYPART_RE.search(before)):
        daypart = near_daypart.group("daypart")
    if _APPROXIMATE_PREFIX_RE.search(before) or _APPROXIMATE_SUFFIX_RE.match(after):
        if hour > 12 or hour == 0:
            possibilities = (hour * 60 + minute,)
        elif daypart:
            resolved, _ = _resolve_daypart(hour, minute, daypart)
            possibilities = (
                (resolved,) if resolved is not None else _clockface_possibilities(hour, minute)
            )
        else:
            possibilities = _clockface_possibilities(hour, minute)
        return TimeDetection(
            match.start(),
            match.end(),
            match.group(),
            None,
            TimeConfidence.APPROXIMATE,
            parser_rule,
            rejection_reason="approximate Chinese modifier",
            language="zh",
            possible_minutes=possibilities,
            semantic_type="APPROXIMATE",
            candidate_confidence=CandidateConfidence.MEDIUM,
        )
    if hour > 12 or hour == 0:
        resolved = hour * 60 + minute
        if daypart:
            compatible, evidence = _resolve_daypart(hour, minute, daypart)
            if compatible != resolved:
                return _rejected(match, parser_rule, evidence, (resolved,))
        return TimeDetection(
            match.start(),
            match.end(),
            match.group(),
            resolved,
            TimeConfidence.EXACT_24H,
            parser_rule,
            "explicit Chinese 24-hour expression",
            language="zh",
            possible_minutes=(resolved,),
            semantic_type="RESOLVED_24H",
            candidate_confidence=(
                CandidateConfidence.HIGH if structurally_explicit else CandidateConfidence.MEDIUM
            ),
        )
    if daypart:
        resolved, evidence = _resolve_daypart(hour, minute, daypart)
        if resolved is not None:
            return TimeDetection(
                match.start(),
                match.end(),
                match.group(),
                resolved,
                TimeConfidence.EXACT_CONTEXTUAL,
                parser_rule,
                evidence,
                language="zh",
                possible_minutes=(resolved,),
                semantic_type="RESOLVED_24H",
                candidate_confidence=CandidateConfidence.HIGH,
            )
        possible = _clockface_possibilities(hour, minute)
        return TimeDetection(
            match.start(),
            match.end(),
            match.group(),
            None,
            TimeConfidence.AMPM_AMBIGUOUS,
            parser_rule,
            evidence,
            language="zh",
            possible_minutes=possible,
            semantic_type="CLOCKFACE_12H",
            candidate_confidence=CandidateConfidence.MEDIUM,
        )

    possible = _clockface_possibilities(hour, minute)
    contextual_gate = bool(_TEMPORAL_PREFIX_RE.search(before) or _TEMPORAL_SUFFIX_RE.match(after))
    if not structurally_explicit and not contextual_gate:
        return TimeDetection(
            match.start(),
            match.end(),
            match.group(),
            None,
            TimeConfidence.AMPM_AMBIGUOUS,
            parser_rule,
            "bare Chinese 点/時 form without temporal context",
            language="zh",
            possible_minutes=possible,
            semantic_type="CLOCKFACE_12H",
            candidate_confidence=CandidateConfidence.MEDIUM,
        )
    rendered = "|".join(f"{value // 60:02d}:{value % 60:02d}" for value in possible)
    return TimeDetection(
        match.start(),
        match.end(),
        match.group(),
        None,
        TimeConfidence.AMPM_AMBIGUOUS,
        parser_rule,
        f"valid Chinese time without daypart; {rendered}",
        language="zh",
        possible_minutes=possible,
        semantic_type="CLOCKFACE_12H",
        candidate_confidence=CandidateConfidence.AMBIGUOUS_CLOCKFACE,
    )


def _difference_detection(text: str, match: re.Match[str]) -> TimeDetection:
    hour = parse_chinese_number(match.group("hour"))
    if match.group("quarter"):
        quarters = parse_chinese_number(match.group("quarter"))
        amount = None if quarters is None else quarters * 15
        parser_rule = "zh_difference_quarter"
    else:
        amount = parse_chinese_number(match.group("minute"))
        parser_rule = "zh_difference_minutes"
    if hour is None or amount is None or not 1 <= hour <= 24 or not 1 <= amount <= 59:
        return _rejected(match, parser_rule, "invalid Chinese difference expression")
    base = (0 if hour == 24 else hour) * 60
    resolved_clock = (base - amount) % (24 * 60)
    resolved_hour, minute = divmod(resolved_clock, 60)
    if hour <= 12 and resolved_hour == 0:
        resolved_hour = 12
    return _build_detection(
        text,
        match,
        hour=resolved_hour,
        minute=minute,
        parser_rule=parser_rule,
        structurally_explicit=True,
    )


def _clock_detection(text: str, match: re.Match[str]) -> TimeDetection:
    hour = parse_chinese_number(match.group("hour"))
    minute = 0
    parser_rule = "zh_bare_hour"
    structurally_explicit = bool(match.group("bell") or match.group("whole"))
    if match.group("half"):
        minute, parser_rule, structurally_explicit = 30, "zh_half", True
    elif match.group("quarter"):
        quarters = parse_chinese_number(match.group("quarter"))
        minute = None if quarters is None or not 1 <= quarters <= 3 else quarters * 15
        parser_rule, structurally_explicit = "zh_quarter", True
    elif match.group("minute"):
        minute = parse_chinese_number(match.group("minute"))
        parser_rule, structurally_explicit = "zh_hour_minute", True
    elif match.group("bell"):
        parser_rule = "zh_bell_hour"
    return _build_detection(
        text,
        match,
        hour=hour,
        minute=minute,
        parser_rule=parser_rule,
        structurally_explicit=structurally_explicit,
    )


def detect_chinese_time_expressions(text: str) -> list[TimeDetection]:
    """Return Chinese candidates, including explicit review/reject classifications."""
    detections = [_difference_detection(text, match) for match in _DIFFERENCE_RE.finditer(text)]
    occupied = [(item.start, item.end) for item in detections]
    for match in _CLOCK_RE.finditer(text):
        if any(start < match.end() and match.start() < end for start, end in occupied):
            continue
        detections.append(_clock_detection(text, match))
    detections.sort(key=lambda item: (item.start, -(item.end - item.start)))
    return detections
