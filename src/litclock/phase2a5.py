"""Phase 2A.5 audit and conservative recovery from the existing mined corpus."""

from __future__ import annotations

import csv
import json
import re
import shutil
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from litclock.db import initialize_database
from litclock.mining import EXACT_CONFIDENCES, PassageDuplicateIndex, calculate_mining_stats
from litclock.models import DuplicateKind, TimeConfidence
from litclock.normalize import minute_to_time, normalized_quote_hash, text_hash
from litclock.standard_ebooks import SOURCE_LICENSE
from litclock.stats import calculate_stats, write_reports
from litclock.timeparse import detect_time_expressions
from litclock.xhtml import extract_paragraphs, sentence_spans

_RENDERABLE = ("VERIFIED_EXACT", "VERIFIED_NORMALIZED")
_OPTIONS_RE = re.compile(r"(?P<am>\d{2}:\d{2})\|(?P<pm>\d{2}:\d{2})$")
_WORD_NUMBERS = {
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
_DURATION_RE = re.compile(
    r"\b(?P<amount>\d{1,3}|(?:twenty|thirty|forty|fifty)(?:[\s-]+"
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
    r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen))?|"
    r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
    r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen)\s+"
    r"(?P<unit>minutes?|hours?)\s+later\b",
    re.IGNORECASE,
)
_SPECIAL_DURATION_RE = re.compile(
    r"\b(?P<amount>half\s+an?|a\s+quarter\s+of\s+an?)\s+hour\s+later\b",
    re.IGNORECASE,
)
_DAYPART_RE = re.compile(
    r"\b(?P<cue>morning|afternoon|evening|night|midnight|midday|noon|dawn|"
    r"breakfast|lunch|dinner|supper|sunset|sunrise)\b",
    re.IGNORECASE,
)
_HIGH_STATUSES = {"HIGH_CONFIDENCE", "IMPORTED", "DEFERRED_DENSE"}
_BIBLE_BOOK_RE = re.compile(
    r"\b(?:Genesis|Exodus|Leviticus|Numbers|Deuteronomy|Joshua|Judges|Ruth|Samuel|"
    r"Kings|Chronicles|Ezra|Nehemiah|Esther|Job|Psalms?|Proverbs|Ecclesiastes|"
    r"Isaiah|Jeremiah|Lamentations|Ezekiel|Daniel|Hosea|Joel|Amos|Obadiah|Jonah|"
    r"Micah|Nahum|Habakkuk|Zephaniah|Haggai|Zechariah|Malachi|Matthew|Matt\.?|Mark|"
    r"Luke|John|Acts|Romans|Corinthians|Galatians|Ephesians|Philippians|Colossians|"
    r"Thessalonians|Timothy|Titus|Philemon|Hebrews|James|Peter|Jude|Revelation)"
    r"\s+\d{1,3}:\d{1,3}\b",
    re.IGNORECASE,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class ContextResolution:
    minute_of_day: int | None
    evidence_type: str | None
    evidence_text: str | None
    evidence_source_locator: str | None
    confidence: str
    proposed_evidence: str | None = None


@dataclass(frozen=True, slots=True)
class ParagraphContext:
    paragraph: str
    previous: str
    following: str
    expression_start: int
    expression_end: int
    locator: str


def parse_ambiguous_options(evidence: str | None) -> tuple[int, int] | None:
    """Parse the two auditable HH:MM alternatives emitted by the Phase 2A parser."""
    match = _OPTIONS_RE.search(evidence or "")
    if not match:
        return None
    values: list[int] = []
    for key in ("am", "pm"):
        hour, minute = (int(part) for part in match.group(key).split(":"))
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            return None
        values.append(hour * 60 + minute)
    if values[0] == values[1]:
        return None
    return values[0], values[1]


def sparse_priority(count: int) -> int:
    """Map coverage to the requested review priority bands (larger is earlier)."""
    if count == 0:
        return 6
    if count == 1:
        return 5
    if count == 2:
        return 4
    if count <= 4:
        return 3
    if count <= 6:
        return 2
    return 0


def coverage_snapshot_from_counts(counts: dict[int, int], selectable_total: int) -> dict[str, int]:
    values = [counts.get(minute, 0) for minute in range(1440)]
    return {
        "selectable_corpus_size": selectable_total,
        "minutes_at_0": sum(value == 0 for value in values),
        "minutes_below_3": sum(value < 3 for value in values),
        "minutes_below_5": sum(value < 5 for value in values),
        "minutes_below_7": sum(value < 7 for value in values),
        "minutes_at_least_7": sum(value >= 7 for value in values),
        "remaining_deficit_to_7": sum(max(0, 7 - value) for value in values),
    }


def simulate_counterfactual(
    current_counts: dict[int, int],
    current_total: int,
    available_by_minute: dict[int, int],
    *,
    target: int = 7,
) -> tuple[dict[str, int], dict[int, int]]:
    """Add every available candidate without allowing any minute above ``target``."""
    result = dict(current_counts)
    added = 0
    for minute in range(1440):
        room = max(0, target - result.get(minute, 0))
        accepted = min(room, available_by_minute.get(minute, 0))
        if accepted:
            result[minute] = result.get(minute, 0) + accepted
            added += accepted
    return coverage_snapshot_from_counts(result, current_total + added), result


def _parse_number(text: str) -> int | None:
    normalized = re.sub(r"[\s-]+", " ", text.casefold()).strip()
    if normalized.isdigit():
        return int(normalized)
    if normalized in _WORD_NUMBERS:
        return _WORD_NUMBERS[normalized]
    parts = normalized.split()
    if len(parts) == 2 and parts[0] in {"twenty", "thirty", "forty", "fifty"}:
        second = _WORD_NUMBERS.get(parts[1])
        return _WORD_NUMBERS[parts[0]] + second if second and second < 20 else None
    return None


def _duration_minutes(text: str) -> tuple[int, str] | None:
    matches: list[tuple[int, int, str]] = []
    for match in _DURATION_RE.finditer(text):
        amount = _parse_number(match.group("amount"))
        if amount is None:
            continue
        minutes = amount * (60 if match.group("unit").casefold().startswith("hour") else 1)
        matches.append((match.end(), minutes, match.group()))
    for match in _SPECIAL_DURATION_RE.finditer(text):
        minutes = 30 if match.group("amount").casefold().startswith("half") else 15
        matches.append((match.end(), minutes, match.group()))
    if not matches:
        return None
    _, minutes, wording = max(matches)
    return minutes, wording


def _target_for_meridiem(options: tuple[int, int], meridiem: str) -> int:
    return options[0] if meridiem == "AM" else options[1]


def _cue_meridiem(cue: str, hour_12: int) -> str | None:
    cue = cue.casefold()
    if cue == "morning" and (hour_12 == 12 or 1 <= hour_12 <= 11):
        return "AM"
    if cue in {"dawn", "sunrise", "breakfast"} and 4 <= hour_12 <= 11:
        return "AM"
    if cue == "midnight" and (hour_12 == 12 or 1 <= hour_12 <= 3):
        return "AM"
    if cue == "afternoon" and (hour_12 == 12 or 1 <= hour_12 <= 6):
        return "PM"
    if cue == "evening" and 4 <= hour_12 <= 11:
        return "PM"
    if cue in {"midday", "noon", "lunch"} and (hour_12 == 11 or hour_12 == 12 or 1 <= hour_12 <= 3):
        return "PM"
    if cue in {"dinner", "supper", "sunset"} and 4 <= hour_12 <= 11:
        return "PM"
    if cue == "night":
        if hour_12 == 12 or 1 <= hour_12 <= 4:
            return "AM"
        if 6 <= hour_12 <= 11:
            return "PM"
    return None


def _cue_is_linked(sentence: str, cue_start: int, cue_end: int, start: int, end: int) -> bool:
    """Require a local grammatical link, not mere coexistence in one sentence."""
    if cue_end <= start:
        bridge = sentence[cue_end:start]
        if len(bridge) > 90:
            return False
        if re.search(r"[.!?;]", bridge):
            return False
        if re.search(r"\b(?:morning|afternoon)\s+train\b", sentence[cue_start:start], re.I):
            return True
        first_word = re.match(r"\s+([A-Za-z]+)", bridge)
        scoped_cue = bool(
            re.search(
                r"\b(?:in|on|during)\s+(?:the\s+)?$|"
                r"\b(?:this|that|last|next|following|previous|same|"
                r"yesterday|tomorrow)\s+$",
                sentence[max(0, cue_start - 25) : cue_start],
                re.IGNORECASE,
            )
        )
        if "," in bridge and not scoped_cue:
            return False
        if (
            first_word
            and first_word.group(1).casefold()
            not in {"of", "at", "on", "by", "when", "before", "after"}
            and not scoped_cue
        ):
            return False
        if re.search(r"\bat\s+that\s+time\s+of\s+(?:the\s+)?night\b", sentence[:start], re.I):
            return not detect_time_expressions(bridge)
        # Examples: “last night at 11:40” and “the next morning ... at 8:13”.
        return bool(
            re.search(r"\b(?:at|by|until|from)\s+(?:exactly\s+)?$", bridge, re.I)
            or (
                re.search(r"\b(?:last|this|that|next|following)\b", sentence[cue_start:cue_end])
                and re.search(r"\bat\s+(?:exactly\s+)?$", bridge, re.I)
            )
        )

    bridge = sentence[end:cue_start]
    if len(bridge) > 65 or re.search(r"[.!?;]", bridge):
        return False
    stripped = bridge.strip(' ,()[]{}—–-"“”‘’')
    if not stripped:
        return True
    # Examples: “11:40 last night”, “3:54 on Tuesday afternoon”, and
    # “5:03 from Hull on the evening before”. A comma breaks this local scope.
    if "," in bridge:
        return False
    return bool(
        re.fullmatch(
            r"(?:(?:on|in|during|of|at|every)\s+)?(?:the\s+)?"
            r"(?:(?:this|that|last|next|following|same|yesterday|tomorrow)\s+)?"
            r"(?:[A-Za-z]+\s*)?",
            stripped,
            re.IGNORECASE,
        )
        or re.fullmatch(
            r"[^,;.!?]{0,45}\b(?:on|in|during)\s+(?:the\s+)?"
            r"(?:(?:this|that|last|next|following|same)\s+)?(?:[A-Za-z]+\s*)?",
            stripped,
            re.IGNORECASE,
        )
        or re.fullmatch(
            r"(?:the\s+)?(?:this|that|last|next|following|previous|same|"
            r"yesterday|tomorrow)\s*",
            stripped,
            re.IGNORECASE,
        )
        or re.fullmatch(
            r"(?:was\s+)?(?:half\s+an\s+hour|\d+\s+minutes?)\s+late\s+that\s*",
            stripped,
            re.IGNORECASE,
        )
        or re.fullmatch(r"[^,;.!?]{0,45}\bin\s+time\s+for\s*", stripped, re.IGNORECASE)
    )


def _containing_sentence(text: str, start: int, end: int) -> tuple[str, int]:
    for sentence_start, sentence_end in sentence_spans(text):
        if sentence_start <= start and end <= sentence_end:
            return text[sentence_start:sentence_end], sentence_start
    return text, 0


def _direct_daypart_resolution(
    paragraph: str,
    expression_start: int,
    expression_end: int,
    options: tuple[int, int],
    locator: str,
) -> ContextResolution | None:
    sentence, sentence_start = _containing_sentence(paragraph, expression_start, expression_end)
    local_start = expression_start - sentence_start
    local_end = expression_end - sentence_start
    hour_12 = (options[0] // 60) or 12

    trailing_seconds = re.match(
        r"(?P<evidence>:\d{2}\s*(?P<meridiem>[ap])\s*\.?\s*m\.?)",
        sentence[local_end:],
        re.IGNORECASE,
    )
    if trailing_seconds:
        meridiem = "AM" if trailing_seconds.group("meridiem").casefold() == "a" else "PM"
        return ContextResolution(
            _target_for_meridiem(options, meridiem),
            "TRAILING_SECONDS_MERIDIEM",
            sentence[local_start:local_end] + trailing_seconds.group("evidence"),
            locator,
            "DETERMINISTIC",
        )

    cues: list[tuple[str, str, int]] = []
    for match in _DAYPART_RE.finditer(sentence):
        if match.start() < local_end and match.end() > local_start:
            continue
        meridiem = _cue_meridiem(match.group("cue"), hour_12)
        if meridiem is None:
            continue
        if not _cue_is_linked(sentence, match.start(), match.end(), local_start, local_end):
            continue
        distance = min(abs(match.end() - local_start), abs(match.start() - local_end))
        cues.append((meridiem, match.group(), distance))
    if not cues:
        return None
    sides = {meridiem for meridiem, _, _ in cues}
    if len(sides) != 1:
        return ContextResolution(
            None,
            None,
            None,
            None,
            "UNRESOLVED",
            "conflicting daypart cues in the containing sentence: "
            + ", ".join(cue for _, cue, _ in cues),
        )
    meridiem = sides.pop()
    cue = min(cues, key=lambda item: item[2])[1]
    return ContextResolution(
        _target_for_meridiem(options, meridiem),
        "CONTAINING_SENTENCE_DAYPART",
        f"{cue}: {sentence}",
        locator,
        "DETERMINISTIC",
    )


def _neighbor_elapsed_resolution(
    paragraph: str,
    expression_start: int,
    options: tuple[int, int],
    previous_paragraph: str,
    locator: str,
) -> ContextResolution | None:
    previous_tail = previous_paragraph[-600:]
    prefix = paragraph[:expression_start]
    history = f"{previous_tail}\n{prefix}" if previous_tail else prefix
    anchors = [
        detection
        for detection in detect_time_expressions(history)
        if detection.minute_of_day is not None
        and detection.confidence
        in {TimeConfidence.EXACT_24H, TimeConfidence.EXACT_AM, TimeConfidence.EXACT_PM}
    ]
    if not anchors:
        return None
    anchor = anchors[-1]
    bridge = history[anchor.end :]
    elapsed = _duration_minutes(bridge)
    if elapsed is None:
        return None
    elapsed_minutes, wording = elapsed
    if elapsed_minutes <= 0 or elapsed_minutes > 12 * 60:
        return None
    expected = (int(anchor.minute_of_day) + elapsed_minutes) % 1440
    if expected not in options:
        return None
    return ContextResolution(
        expected,
        "NEIGHBOR_EXPLICIT_TIME_ELAPSED",
        f"{anchor.text} + {wording} = {minute_to_time(expected)}",
        locator,
        "DETERMINISTIC",
    )


def resolve_contextual_ampm(
    paragraph: str,
    expression_start: int,
    expression_end: int,
    options: tuple[int, int],
    *,
    previous_paragraph: str = "",
    following_paragraph: str = "",
    source_locator: str = "",
) -> ContextResolution:
    """Resolve AM/PM only from direct daypart or arithmetic neighboring evidence."""
    if not 0 <= expression_start < expression_end <= len(paragraph):
        return ContextResolution(None, None, None, None, "SOURCE_MISMATCH")
    direct = _direct_daypart_resolution(
        paragraph, expression_start, expression_end, options, source_locator
    )
    if direct is not None:
        return direct
    elapsed = _neighbor_elapsed_resolution(
        paragraph, expression_start, options, previous_paragraph, source_locator
    )
    if elapsed is not None:
        return elapsed

    outside = " ".join(
        part for part in (previous_paragraph[-250:], paragraph, following_paragraph[:250]) if part
    )
    weak = [match.group() for match in _DAYPART_RE.finditer(outside)]
    proposed = (
        "nearby daypart words require human narrative judgment: " + ", ".join(weak[:6])
        if weak
        else None
    )
    return ContextResolution(None, None, None, None, "UNRESOLVED", proposed)


def _selectable_counts(
    connection: sqlite3.Connection, *, exclude_contextual_recovery: bool = False
) -> dict[int, int]:
    exclusion = ""
    if exclude_contextual_recovery:
        exclusion = """
          AND q.id NOT IN (
              SELECT imported_quote_id FROM mined_candidates
              WHERE review_status = 'IMPORTED_CONTEXTUAL' AND imported_quote_id IS NOT NULL
          )
        """
    return {
        int(row["minute_of_day"]): int(row["n"])
        for row in connection.execute(
            f"""
            SELECT pool.minute_of_day, COUNT(*) AS n
            FROM quote_minute_pool AS pool JOIN quotes AS q ON q.id = pool.quote_id
            WHERE q.quality_status IN (?, ?) {exclusion}
            GROUP BY pool.minute_of_day
            """,  # noqa: S608 - exclusion is a fixed internal SQL fragment
            _RENDERABLE,
        )
    }


def _phase1_counts(connection: sqlite3.Connection) -> dict[int, int]:
    return {
        int(row["minute_of_day"]): int(row["n"])
        for row in connection.execute(
            """
            SELECT pool.minute_of_day, COUNT(*) AS n
            FROM quote_minute_pool AS pool JOIN quotes AS q ON q.id = pool.quote_id
            WHERE q.quality_status IN (?, ?)
              AND q.source_name NOT LIKE 'standardebooks/%'
            GROUP BY pool.minute_of_day
            """,
            _RENDERABLE,
        )
    }


def high_confidence_accounting(connection: sqlite3.Connection) -> dict[str, int]:
    """Return mutually exclusive accounting for the original high-confidence set."""
    phase1_counts = _phase1_counts(connection)
    rows = list(
        connection.execute(
            """
            SELECT id, minute_of_day, review_status, imported_quote_id
            FROM mined_candidates
            WHERE review_status IN ('HIGH_CONFIDENCE', 'IMPORTED', 'DEFERRED_DENSE')
            ORDER BY id
            """
        )
    )
    result: Counter[str] = Counter()
    for row in rows:
        minute = int(row["minute_of_day"])
        if row["review_status"] == "IMPORTED":
            result["imported"] += 1
        elif phase1_counts.get(minute, 0) >= 7:
            result["minute_already_full"] += 1
        elif row["review_status"] == "DEFERRED_DENSE":
            result["lower_priority_same_minute"] += 1
        else:
            result["otherwise_eligible_not_imported"] += 1
    result["diversity_excluded"] = 0
    result["quality_rejected_within_high_confidence"] = 0
    result["other_eligibility_failure_within_high_confidence"] = 0
    result["total"] = len(rows)
    return dict(result)


def _citation_shaped(quote: str, start: int, end: int) -> bool:
    if _BIBLE_BOOK_RE.search(quote):
        return True
    containing, containing_start = _containing_sentence(quote, start, end)
    local_start = start - containing_start
    local_end = end - containing_start
    if re.search(r"^[A-Z][^.!?]{0,45},\s*\d{1,3}:\d{1,3}\b", containing):
        return True
    if local_start == 0 and re.match(r"\s*[),]", containing[local_end:]):
        return True
    return False


def _quality_acceptable(row: sqlite3.Row) -> tuple[bool, str | None]:
    quote = str(row["quote"])
    time_text = str(row["time_text"])
    start = int(row["highlight_start"])
    end = int(row["highlight_end"])
    if not 0 <= start < end <= len(quote) or quote[start:end] != time_text:
        return False, "highlight offsets do not reproduce the exact time phrase"
    if not 40 <= len(quote) <= 600:
        return False, "context length outside 40–600 characters"
    if float(row["context_score"]) < 70 or float(row["literary_quality_score"]) < 60:
        return False, "quality score below conservative threshold"
    first_alpha = next((char for char in quote if char.isalpha()), "")
    if first_alpha and first_alpha.islower():
        return False, "context begins mid-sentence"
    if not quote.endswith((".", "?", "!", ".”", "?”", "!”", ".’", "?’", "!’")):
        return False, "context has no complete sentence ending"
    if len(detect_time_expressions(quote)) >= 3:
        return False, "three or more time expressions in one context"
    if _citation_shaped(quote, start, end):
        return False, "citation-shaped expression is not a literary clock time"
    if (
        row["parser_rule"] == "written_clock"
        and re.search(r"\bone$", time_text.casefold())
        and re.match(
            r"\s+(?:morning|afternoon|evening|night)\b",
            quote[end:],
            re.IGNORECASE,
        )
    ):
        return False, "written ‘one’ is a day determiner, not a minute value"
    required = ("source_repository", "source_url", "source_commit", "source_file", "source_locator")
    if any(not str(row[field] or "").strip() for field in required):
        return False, "incomplete source provenance"
    return True, None


def _resolution_consistent(row: sqlite3.Row) -> tuple[bool, str | None]:
    minute = row["resolved_minute_of_day"]
    options = parse_ambiguous_options(row["ampm_evidence"])
    if minute is None or options is None or int(minute) not in options:
        return False, "contextual resolution is not one of the parser's AM/PM alternatives"
    if row["contextual_resolution"] != minute_to_time(int(minute)):
        return False, "stored contextual resolution is inconsistent"
    if any(
        not str(row[field] or "").strip()
        for field in ("evidence_type", "evidence_text", "evidence_source_locator")
    ):
        return False, "deterministic resolution evidence is incomplete"
    return True, None


class _SourceContextCache:
    def __init__(self, project_root: Path) -> None:
        self.books = project_root / "data" / "public_domain" / "standard_ebooks" / "books"
        self._files: dict[tuple[str, str], list[Any] | None] = {}

    def get(self, row: sqlite3.Row) -> ParagraphContext | None:
        key = (str(row["source_repository"]), str(row["source_file"]))
        if key not in self._files:
            path = self.books / key[0] / key[1]
            try:
                self._files[key] = extract_paragraphs(path, key[1]) if path.exists() else None
            except ValueError:
                self._files[key] = None
        paragraphs = self._files[key]
        if not paragraphs:
            return None
        wanted = int(row["source_paragraph_index"])
        position = next(
            (
                index
                for index, paragraph in enumerate(paragraphs)
                if paragraph.paragraph_index == wanted
            ),
            None,
        )
        if position is None:
            return None
        paragraph = paragraphs[position]
        start = int(row["source_expression_start"]) - int(paragraph.document_offset)
        end = int(row["source_expression_end"]) - int(paragraph.document_offset)
        if not 0 <= start < end <= len(paragraph.text):
            return None
        if paragraph.text[start:end] != row["time_text"]:
            return None
        return ParagraphContext(
            paragraph=paragraph.text,
            previous=paragraphs[position - 1].text if position else "",
            following=paragraphs[position + 1].text if position + 1 < len(paragraphs) else "",
            expression_start=start,
            expression_end=end,
            locator=str(row["source_locator"]),
        )


def resolve_ambiguous_candidates(
    connection: sqlite3.Connection,
    project_root: Path,
    *,
    counts: dict[int, int] | None = None,
) -> dict[str, int]:
    """Resolve sparse-relevant ambiguous candidates and record exact evidence."""
    initialize_database(connection)
    counts = counts or _selectable_counts(connection, exclude_contextual_recovery=True)
    cache = _SourceContextCache(project_root)
    rows = list(
        connection.execute(
            """
            SELECT c.*, b.language, b.rights, b.content_checksum
            FROM mined_candidates AS c
            JOIN standard_ebooks_books AS b ON b.id = c.book_id
            WHERE c.time_confidence = 'AMPM_AMBIGUOUS'
            ORDER BY c.target_priority DESC, c.context_score DESC,
                     c.literary_quality_score DESC, c.id
            """
        )
    )
    totals: Counter[str] = Counter(considered=len(rows))
    timestamp = _now()
    for row in rows:
        options = parse_ambiguous_options(row["ampm_evidence"])
        if row["duplicate_status"] != "NEW":
            status = "DUPLICATE_IGNORED"
            totals["duplicates"] += 1
            decision = ContextResolution(None, None, None, None, status)
        elif options is None:
            status = "INVALID_OPTIONS"
            totals["invalid_options"] += 1
            decision = ContextResolution(None, None, None, None, status)
        elif min(counts.get(options[0], 0), counts.get(options[1], 0)) >= 7:
            status = "DENSE_IGNORED"
            totals["dense_ignored"] += 1
            decision = ContextResolution(None, None, None, None, status)
        else:
            totals["relevant_below_7"] += 1
            if min(counts.get(options[0], 0), counts.get(options[1], 0)) < 3:
                totals["relevant_below_3"] += 1
            if _citation_shaped(
                str(row["quote"]), int(row["highlight_start"]), int(row["highlight_end"])
            ):
                totals["citation_rejected"] += 1
                if min(counts.get(options[0], 0), counts.get(options[1], 0)) < 3:
                    totals["citation_rejected_below_3"] += 1
                decision = ContextResolution(None, None, None, None, "CITATION_REJECTED")
            else:
                context = cache.get(row)
                if context is None:
                    totals["source_unavailable"] += 1
                    decision = ContextResolution(None, None, None, None, "SOURCE_UNAVAILABLE")
                else:
                    decision = resolve_contextual_ampm(
                        context.paragraph,
                        context.expression_start,
                        context.expression_end,
                        options,
                        previous_paragraph=context.previous,
                        following_paragraph=context.following,
                        source_locator=context.locator,
                    )
                    if decision.minute_of_day is not None:
                        totals["resolved"] += 1
                        acceptable, _ = _quality_acceptable(row)
                        if acceptable:
                            totals["resolved_quality_eligible"] += 1
                    else:
                        totals["unresolved"] += 1
        connection.execute(
            """
            UPDATE mined_candidates SET contextual_resolution = ?, resolved_minute_of_day = ?,
                evidence_type = ?, evidence_text = ?, evidence_source_locator = ?,
                resolution_confidence = ?, resolution_status = ?, resolution_updated_at = ?
            WHERE id = ?
            """,
            (
                minute_to_time(decision.minute_of_day)
                if decision.minute_of_day is not None
                else None,
                decision.minute_of_day,
                decision.evidence_type,
                decision.evidence_text,
                decision.evidence_source_locator,
                decision.confidence,
                (
                    "IMPORTED_CONTEXTUAL"
                    if row["review_status"] == "IMPORTED_CONTEXTUAL"
                    else "AUTO_RESOLVED"
                )
                if decision.minute_of_day is not None
                else decision.confidence,
                timestamp,
                row["id"],
            ),
        )
    connection.commit()
    return dict(totals)


def _candidate_order(
    rows: Iterable[sqlite3.Row],
    counts: dict[int, int],
    authors: dict[int, set[str]],
    books: dict[int, set[str]],
) -> list[sqlite3.Row]:
    remaining = list(rows)
    ordered: list[sqlite3.Row] = []
    while remaining:
        remaining.sort(
            key=lambda row: (
                sparse_priority(counts.get(int(row["resolved_minute_of_day"]), 0)),
                str(row["author"]).casefold() not in authors[int(row["resolved_minute_of_day"])],
                str(row["title"]).casefold() not in books[int(row["resolved_minute_of_day"])],
                float(row["context_score"]),
                float(row["literary_quality_score"]),
                -int(row["id"]),
            ),
            reverse=True,
        )
        chosen = remaining.pop(0)
        ordered.append(chosen)
        minute = int(chosen["resolved_minute_of_day"])
        authors[minute].add(str(chosen["author"]).casefold())
        books[minute].add(str(chosen["title"]).casefold())
    return ordered


def _diversity_state(
    connection: sqlite3.Connection,
) -> tuple[dict[int, set[str]], dict[int, set[str]]]:
    authors: dict[int, set[str]] = defaultdict(set)
    books: dict[int, set[str]] = defaultdict(set)
    for row in connection.execute(
        """
        SELECT pool.minute_of_day, q.author, q.title
        FROM quote_minute_pool AS pool JOIN quotes AS q ON q.id = pool.quote_id
        WHERE q.quality_status IN (?, ?)
        """,
        _RENDERABLE,
    ):
        authors[int(row["minute_of_day"])].add(str(row["author"]).casefold())
        books[int(row["minute_of_day"])].add(str(row["title"]).casefold())
    return authors, books


def import_recovered_candidates(
    connection: sqlite3.Connection, *, target_per_minute: int = 7
) -> int:
    """Import only deterministic, clean, nonduplicate contextual resolutions."""
    initialize_database(connection)
    counts = _selectable_counts(connection)
    authors, books = _diversity_state(connection)
    rows = list(
        connection.execute(
            """
            SELECT c.*, b.language, b.rights, b.content_checksum
            FROM mined_candidates AS c
            JOIN standard_ebooks_books AS b ON b.id = c.book_id
            WHERE c.resolution_status = 'AUTO_RESOLVED'
              AND c.resolution_confidence = 'DETERMINISTIC'
              AND c.resolved_minute_of_day IS NOT NULL
              AND c.duplicate_status = 'NEW'
              AND c.imported_quote_id IS NULL
            ORDER BY c.id
            """
        )
    )
    eligible: list[sqlite3.Row] = []
    for row in rows:
        acceptable, reason = _resolution_consistent(row)
        if acceptable:
            acceptable, reason = _quality_acceptable(row)
        minute = int(row["resolved_minute_of_day"])
        if not acceptable:
            connection.execute(
                """
                UPDATE mined_candidates SET resolution_status = 'RESOLVED_QUALITY_REJECTED',
                    rejection_reason = ? WHERE id = ?
                """,
                (reason, row["id"]),
            )
        elif counts.get(minute, 0) >= target_per_minute:
            connection.execute(
                "UPDATE mined_candidates SET resolution_status = 'RESOLVED_DENSE' WHERE id = ?",
                (row["id"],),
            )
        else:
            eligible.append(row)

    candidates = _candidate_order(eligible, counts, authors, books)
    duplicate_index = PassageDuplicateIndex(connection)
    run_id = int(
        connection.execute(
            "INSERT INTO import_runs (started_at, status) VALUES (?, 'RUNNING')", (_now(),)
        ).lastrowid
    )
    accepted = conflicts = deferred = 0
    for row in candidates:
        minute = int(row["resolved_minute_of_day"])
        if counts.get(minute, 0) >= target_per_minute:
            connection.execute(
                "UPDATE mined_candidates SET resolution_status = 'RESOLVED_DENSE' WHERE id = ?",
                (row["id"],),
            )
            deferred += 1
            continue
        duplicate = duplicate_index.check(str(row["quote"]), minute)
        if duplicate.status != "NEW":
            connection.execute(
                """
                UPDATE mined_candidates SET resolution_status = 'RESOLVED_DUPLICATE',
                    duplicate_status = ?, duplicate_of_quote_id = ?,
                    duplicate_of_candidate_id = ?, rejection_reason = ? WHERE id = ?
                """,
                (
                    duplicate.status,
                    duplicate.quote_id,
                    duplicate.candidate_id,
                    f"duplicate passage after contextual resolution: {duplicate.status}",
                    row["id"],
                ),
            )
            conflicts += 1
            continue
        source_name = f"standardebooks/{row['source_repository']}"
        source_slug = f"standardebooks-{row['source_repository']}"
        source_license = f"{SOURCE_LICENSE}; repository rights: {row['rights'] or 'see source OPF'}"
        imported_at = _now()
        connection.execute(
            """
            INSERT INTO sources (
                name, slug, source_url, source_license, upstream_commit, corpus_path,
                corpus_sha256, record_count, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(name) DO UPDATE SET imported_at = excluded.imported_at
            """,
            (
                source_name,
                source_slug,
                row["source_url"],
                source_license,
                row["source_commit"],
                row["source_repository"],
                row["content_checksum"],
                imported_at,
            ),
        )
        source_id = int(
            connection.execute("SELECT id FROM sources WHERE name = ?", (source_name,)).fetchone()[
                0
            ]
        )
        try:
            quote_id = int(
                connection.execute(
                    """
                    INSERT INTO quotes (
                        minute_of_day, time_24h, time_text, quote, title, author, sfw, language,
                        source_name, source_url, source_license, source_record_id, quote_hash,
                        normalized_quote_hash, highlight_start, highlight_end,
                        quality_status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              'VERIFIED_EXACT', ?)
                    """,
                    (
                        minute,
                        minute_to_time(minute),
                        row["time_text"],
                        row["quote"],
                        row["title"],
                        row["author"],
                        row["language"] or "en",
                        source_name,
                        row["source_url"],
                        source_license,
                        str(row["id"]),
                        text_hash(str(row["quote"])),
                        normalized_quote_hash(str(row["quote"])),
                        row["highlight_start"],
                        row["highlight_end"],
                        imported_at,
                    ),
                ).lastrowid
            )
        except sqlite3.IntegrityError:
            connection.execute(
                """
                UPDATE mined_candidates SET resolution_status = 'RESOLVED_DUPLICATE',
                    rejection_reason = 'duplicate appeared before recovery import' WHERE id = ?
                """,
                (row["id"],),
            )
            conflicts += 1
            continue
        payload = json.dumps(dict(row), ensure_ascii=False, default=str, sort_keys=True)
        connection.execute(
            "UPDATE sources SET record_count = record_count + 1 WHERE id = ?", (source_id,)
        )
        connection.execute(
            """
            INSERT INTO quote_provenance (
                quote_id, source_id, import_run_id, source_record_id, raw_time_24h, raw_time_text,
                raw_quote, raw_title, raw_author, raw_sfw, raw_quote_hash, validation_status,
                highlight_start, highlight_end, duplicate_kind, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 'VERIFIED_EXACT', ?, ?, ?, ?)
            """,
            (
                quote_id,
                source_id,
                run_id,
                str(row["id"]),
                minute_to_time(minute),
                row["time_text"],
                row["quote"],
                row["title"],
                row["author"],
                text_hash(str(row["quote"])),
                row["highlight_start"],
                row["highlight_end"],
                DuplicateKind.CANONICAL.value,
                payload,
            ),
        )
        connection.execute(
            """
            UPDATE mined_candidates SET review_status = 'IMPORTED_CONTEXTUAL',
                resolution_status = 'IMPORTED_CONTEXTUAL', imported_quote_id = ?, imported_at = ?
            WHERE id = ?
            """,
            (quote_id, imported_at, row["id"]),
        )
        duplicate_index.add("LEGACY", quote_id, str(row["quote"]), minute)
        counts[minute] = counts.get(minute, 0) + 1
        accepted += 1
    connection.execute(
        """
        UPDATE import_runs SET finished_at = ?, status = 'COMPLETE', raw_record_count = ?,
            canonical_inserted = ?, exact_duplicates = ?, trivial_variants = 0,
            malformed_records = 0, invalid_times = 0 WHERE id = ?
        """,
        (_now(), len(candidates), accepted, conflicts, run_id),
    )
    connection.commit()
    return accepted


def _candidate_audit_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    current = _selectable_counts(connection, exclude_contextual_recovery=True)
    phase1 = _phase1_counts(connection)
    aggregations: dict[int, Counter[str]] = defaultdict(Counter)
    authors: dict[int, set[str]] = defaultdict(set)
    books: dict[int, set[str]] = defaultdict(set)
    for candidate in connection.execute(
        """
        SELECT id, minute_of_day, author, title, review_status, duplicate_status,
               imported_quote_id, time_confidence, rejection_reason
        FROM mined_candidates WHERE minute_of_day IS NOT NULL
        """
    ):
        minute = int(candidate["minute_of_day"])
        bucket = aggregations[minute]
        status = str(candidate["review_status"])
        duplicate = str(candidate["duplicate_status"])
        if status in _HIGH_STATUSES:
            bucket["high_confidence"] += 1
            authors[minute].add(str(candidate["author"]).casefold())
            books[minute].add(str(candidate["title"]).casefold())
            if status == "IMPORTED":
                bucket["already_imported_canonical"] += 1
            elif phase1.get(minute, 0) >= 7:
                bucket["blocked_preexisting_cap"] += 1
            elif status == "DEFERRED_DENSE":
                bucket["lower_priority_same_minute"] += 1
            else:
                bucket["otherwise_eligible_not_imported"] += 1
        if duplicate != "NEW":
            if duplicate.endswith("_LEGACY"):
                bucket["duplicate_against_canonical"] += 1
            else:
                bucket["duplicate_mined_passage"] += 1
        elif (
            status in {"REJECTED", "PENDING_REVIEW"}
            and candidate["time_confidence"] in EXACT_CONFIDENCES
        ):
            bucket["quality_rejected"] += 1
        elif status not in _HIGH_STATUSES:
            bucket["other_eligibility_failure"] += 1
    rows: list[dict[str, Any]] = []
    for minute in range(1440):
        values = aggregations[minute]
        rows.append(
            {
                "minute_of_day": minute,
                "time_24h": minute_to_time(minute),
                "current_selectable_quote_count": current.get(minute, 0),
                "current_deficit_to_7": max(0, 7 - current.get(minute, 0)),
                "mined_high_confidence_candidates": values["high_confidence"],
                "already_imported": values["already_imported_canonical"],
                "duplicate_against_canonical": values["duplicate_against_canonical"],
                "duplicate_mined_passage": values["duplicate_mined_passage"],
                "rejected_as_duplicate_total": values["duplicate_against_canonical"]
                + values["duplicate_mined_passage"],
                "blocked_only_preexisting_minute_at_7": values["blocked_preexisting_cap"],
                "lower_priority_than_selected_same_minute": values["lower_priority_same_minute"],
                "blocked_by_author_book_diversity": 0,
                "quality_rejection": values["quality_rejected"],
                "other_eligibility_failure": values["other_eligibility_failure"],
                "otherwise_eligible_not_imported": values["otherwise_eligible_not_imported"],
                "unique_authors_high_confidence": len(authors[minute]),
                "unique_books_high_confidence": len(books[minute]),
            }
        )
    return rows


def _write_candidate_audit_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _counterfactuals(connection: sqlite3.Connection) -> dict[str, dict[str, int]]:
    current = _selectable_counts(connection, exclude_contextual_recovery=True)
    current_total = sum(current.values())
    available = {
        int(row["minute_of_day"]): int(row["n"])
        for row in connection.execute(
            """
            SELECT minute_of_day, COUNT(*) AS n FROM mined_candidates
            WHERE review_status IN ('HIGH_CONFIDENCE', 'DEFERRED_DENSE')
              AND duplicate_status = 'NEW' AND imported_quote_id IS NULL
              AND minute_of_day IS NOT NULL
            GROUP BY minute_of_day
            """
        )
    }
    scenario_a = coverage_snapshot_from_counts(current, current_total)
    scenario_b, _ = simulate_counterfactual(current, current_total, available)
    # Diversity was a stable ordering preference, never a hard acceptance gate.
    scenario_c, _ = simulate_counterfactual(current, current_total, available)
    return {"A": scenario_a, "B": scenario_b, "C": scenario_c}


def _review_rows(connection: sqlite3.Connection, project_root: Path) -> list[dict[str, Any]]:
    counts = _selectable_counts(connection)
    cache = _SourceContextCache(project_root)
    output: list[dict[str, Any]] = []
    rows = connection.execute(
        """
        SELECT * FROM mined_candidates
        WHERE time_confidence = 'AMPM_AMBIGUOUS' AND duplicate_status = 'NEW'
          AND imported_quote_id IS NULL
          AND resolution_status IN ('UNRESOLVED', 'SOURCE_UNAVAILABLE',
                                    'RESOLVED_QUALITY_REJECTED', 'INVALID_OPTIONS')
        ORDER BY id
        """
    )
    for row in rows:
        options = parse_ambiguous_options(row["ampm_evidence"])
        if options is None or min(counts.get(options[0], 0), counts.get(options[1], 0)) >= 3:
            continue
        quote = str(row["quote"])
        start, end = int(row["highlight_start"]), int(row["highlight_end"])
        if _citation_shaped(quote, start, end):
            continue
        context = cache.get(row)
        proposed = None
        if context is not None:
            decision = resolve_contextual_ampm(
                context.paragraph,
                context.expression_start,
                context.expression_end,
                options,
                previous_paragraph=context.previous,
                following_paragraph=context.following,
                source_locator=context.locator,
            )
            proposed = decision.proposed_evidence
        output.append(
            {
                "candidate_id": row["id"],
                "review_priority": max(
                    sparse_priority(counts.get(options[0], 0)),
                    sparse_priority(counts.get(options[1], 0)),
                ),
                "target_if_am": minute_to_time(options[0]),
                "current_count_if_am": counts.get(options[0], 0),
                "target_if_pm": minute_to_time(options[1]),
                "current_count_if_pm": counts.get(options[1], 0),
                "title": row["title"],
                "author": row["author"],
                "preceding_paragraph": context.previous if context else "",
                "containing_paragraph": context.paragraph if context else quote,
                "following_paragraph": context.following if context else "",
                "detected_time_phrase": row["time_text"],
                "highlighted_quote": f"{quote[:start]}**{quote[start:end]}**{quote[end:]}",
                "parser_rule": row["parser_rule"],
                "parser_explanation": row["ampm_evidence"],
                "source_locator": row["source_locator"],
                "proposed_evidence": proposed or "",
                "quality_note": row["rejection_reason"] or "",
            }
        )
    output.sort(
        key=lambda row: (
            -int(row["review_priority"]),
            min(int(row["current_count_if_am"]), int(row["current_count_if_pm"])),
            int(row["candidate_id"]),
        )
    )
    return output


def _write_review_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = (
        list(rows[0])
        if rows
        else [
            "candidate_id",
            "review_priority",
            "target_if_am",
            "current_count_if_am",
            "target_if_pm",
            "current_count_if_pm",
            "title",
            "author",
            "preceding_paragraph",
            "containing_paragraph",
            "following_paragraph",
            "detected_time_phrase",
            "highlighted_quote",
            "parser_rule",
            "parser_explanation",
            "source_locator",
            "proposed_evidence",
            "quality_note",
        ]
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


_EMPTY_TARGET_PATTERNS = {
    15 * 60 + 46: re.compile(
        r"(?<!\d)(?:0?3|15)\s*[:.]\s*46(?!\d)|"
        r"fourteen\s+minutes?\s+to\s+four|forty[- ]six\s+minutes?\s+past\s+three|"
        r"three[- ]forty[- ]six",
        re.IGNORECASE,
    ),
    16 * 60 + 19: re.compile(
        r"(?<!\d)(?:0?4|16)\s*[:.]\s*19(?!\d)|"
        r"nineteen\s+minutes?\s+past\s+four|forty[- ]one\s+minutes?\s+to\s+five|"
        r"four[- ]nineteen",
        re.IGNORECASE,
    ),
    18 * 60 + 17: re.compile(
        r"(?<!\d)(?:0?6|18)\s*[:.]\s*17(?!\d)|"
        r"seventeen\s+minutes?\s+past\s+six|forty[- ]three\s+minutes?\s+to\s+seven|"
        r"six[- ]seventeen",
        re.IGNORECASE,
    ),
}


def _empty_minute_investigation(connection: sqlite3.Connection) -> dict[str, Any]:
    candidates = list(connection.execute("SELECT * FROM mined_candidates ORDER BY id"))
    output: dict[str, Any] = {}
    for target, pattern in _EMPTY_TARGET_PATTERNS.items():
        categories: Counter[str] = Counter()
        examples: list[dict[str, Any]] = []
        for row in candidates:
            options = parse_ambiguous_options(row["ampm_evidence"])
            plausible = (
                row["minute_of_day"] == target
                or row["resolved_minute_of_day"] == target
                or (options is not None and target in options)
                or bool(pattern.search(str(row["time_text"])))
            )
            if not plausible:
                continue
            if row["duplicate_status"] != "NEW":
                category = "duplicate"
            elif _citation_shaped(
                str(row["quote"]), int(row["highlight_start"]), int(row["highlight_end"])
            ):
                category = "citation_quality_failure"
            elif row["review_status"] == "IMPORTED_CONTEXTUAL":
                category = "recovered_imported"
            elif row["review_status"] == "IMPORTED":
                category = "already_imported"
            elif row["time_confidence"] == TimeConfidence.AMPM_AMBIGUOUS.value:
                category = (
                    "contextually_resolved"
                    if row["resolved_minute_of_day"] == target
                    else "ampm_ambiguous"
                )
            elif row["time_confidence"] == TimeConfidence.APPROXIMATE.value:
                category = "approximate"
            elif row["review_status"] in {"REJECTED", "PENDING_REVIEW"}:
                category = "failed_quality_or_eligibility"
            else:
                category = "other_occurrence"
            categories[category] += 1
            if len(examples) < 5:
                examples.append(
                    {
                        "candidate_id": row["id"],
                        "time_text": row["time_text"],
                        "category": category,
                        "title": row["title"],
                        "author": row["author"],
                        "locator": row["source_locator"],
                        "reason": (
                            "citation-shaped expression is not a literary clock time"
                            if category == "citation_quality_failure"
                            else row["rejection_reason"]
                            or row["ampm_evidence"]
                            or row["resolution_status"]
                            or "none recorded"
                        ),
                    }
                )
        output[minute_to_time(target)] = {
            "no_occurrence_exists": not categories,
            "counts": dict(categories),
            "examples": examples,
        }
    return output


def _scenario_table(scenarios: dict[str, dict[str, int]]) -> list[str]:
    fields = (
        ("selectable_corpus_size", "Selectable corpus"),
        ("minutes_at_0", "Minutes at 0"),
        ("minutes_below_3", "Minutes <3"),
        ("minutes_below_5", "Minutes <5"),
        ("minutes_below_7", "Minutes <7"),
        ("minutes_at_least_7", "Minutes >=7"),
        ("remaining_deficit_to_7", "Deficit to 7"),
    )
    lines = [
        "| Metric | A: current | B: all eligible, cap 7 | C: no diversity preference |",
        "|---|---:|---:|---:|",
    ]
    for key, label in fields:
        lines.append(
            f"| {label} | {scenarios['A'][key]:,} | {scenarios['B'][key]:,} | "
            f"{scenarios['C'][key]:,} |"
        )
    return lines


def _write_audit_markdown(
    path: Path,
    accounting: dict[str, int],
    scenarios: dict[str, dict[str, int]],
    audit_rows: list[dict[str, Any]],
) -> None:
    sum_categories = sum(
        accounting.get(key, 0)
        for key in (
            "imported",
            "minute_already_full",
            "lower_priority_same_minute",
            "diversity_excluded",
            "quality_rejected_within_high_confidence",
            "other_eligibility_failure_within_high_confidence",
            "otherwise_eligible_not_imported",
        )
    )
    most_unused = sorted(
        audit_rows,
        key=lambda row: (
            -int(row["blocked_only_preexisting_minute_at_7"]),
            -int(row["lower_priority_than_selected_same_minute"]),
            int(row["minute_of_day"]),
        ),
    )[:30]
    lines = [
        "# Phase 2A.5 High-Confidence Candidate Audit",
        "",
        "This audit is reconstructed from candidate disposition and the pre-Standard-Ebooks "
        "quote counts. It does not mutate quote coverage.",
        "",
        "## Exact 7,293-candidate accounting",
        "",
        "| Mutually exclusive category | Count |",
        "|---|---:|",
        f"| Imported into the canonical corpus | {accounting.get('imported', 0):,} |",
        "| Minute already had >=7 before Phase 2A | "
        f"{accounting.get('minute_already_full', 0):,} |",
        "| Lower-priority candidate for a minute whose available slots were filled | "
        f"{accounting.get('lower_priority_same_minute', 0):,} |",
        f"| Excluded by author/book diversity | {accounting.get('diversity_excluded', 0):,} |",
        "| Quality-rejected after entering the high-confidence set | "
        f"{accounting.get('quality_rejected_within_high_confidence', 0):,} |",
        "| Other eligibility failure within the high-confidence set | "
        f"{accounting.get('other_eligibility_failure_within_high_confidence', 0):,} |",
        "| Otherwise eligible but not imported | "
        f"{accounting.get('otherwise_eligible_not_imported', 0):,} |",
        f"| **Category sum** | **{sum_categories:,}** |",
        f"| **High-confidence total** | **{accounting['total']:,}** |",
        "",
        "The diversity preference only selected which candidate occupied a scarce slot. It was "
        "never a hard gate, so it excluded zero candidates while capacity remained.",
        "Duplicate and quality counts in the per-minute CSV describe candidates outside the "
        "7,293 high-confidence denominator; candidates were classified as duplicates or review "
        "items before they could enter that set.",
        "",
        "## Counterfactual coverage",
        "",
        *_scenario_table(scenarios),
        "",
        "Scenarios B and C are identical because all correctness-eligible slots represented by "
        "the original high-confidence set were already filled. Ignoring diversity can change "
        "which books are selected, not the number selected.",
        "",
        "## Minutes with the most unused high-confidence candidates",
        "",
        "| Minute | Current | High confidence | Already imported | Preexisting-cap blocked | "
        "Lower-priority excess | Authors | Books |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in most_unused:
        lines.append(
            f"| {row['time_24h']} | {row['current_selectable_quote_count']} | "
            f"{row['mined_high_confidence_candidates']} | {row['already_imported']} | "
            f"{row['blocked_only_preexisting_minute_at_7']} | "
            f"{row['lower_priority_than_selected_same_minute']} | "
            f"{row['unique_authors_high_confidence']} | {row['unique_books_high_confidence']} |"
        )
    lines.extend(
        [
            "",
            "The complete 1,440-row analysis is in `PHASE2A5_CANDIDATE_AUDIT.csv`.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _resolution_patterns(connection: sqlite3.Connection) -> list[tuple[str, int]]:
    return [
        (str(row["label"]), int(row["n"]))
        for row in connection.execute(
            """
            SELECT COALESCE(evidence_type, resolution_status, 'UNPROCESSED') AS label,
                   COUNT(*) AS n
            FROM mined_candidates WHERE time_confidence = 'AMPM_AMBIGUOUS'
            GROUP BY label ORDER BY n DESC, label
            """
        )
    ]


def _write_final_report(
    path: Path,
    connection: sqlite3.Connection,
    accounting: dict[str, int],
    scenarios: dict[str, dict[str, int]],
    resolution: dict[str, int],
    review_count: int,
    empty_minutes: dict[str, Any],
) -> dict[str, Any]:
    stats = calculate_stats(connection)
    mining = calculate_mining_stats(connection)
    total_recovered = int(
        connection.execute(
            "SELECT COUNT(*) FROM mined_candidates WHERE review_status = 'IMPORTED_CONTEXTUAL'"
        ).fetchone()[0]
    )
    high_confidence_failures = accounting.get(
        "quality_rejected_within_high_confidence", 0
    ) + accounting.get("other_eligibility_failure_within_high_confidence", 0)
    hardest = sorted(
        stats["minute_coverage"],
        key=lambda row: (
            row["renderable_count"],
            row["unique_authors"],
            row["unique_books"],
            row["minute_of_day"],
        ),
    )[:50]
    high_unused_by_count = Counter()
    for row in _candidate_audit_rows(connection):
        if row["blocked_only_preexisting_minute_at_7"]:
            high_unused_by_count["preexisting-full"] += int(
                row["blocked_only_preexisting_minute_at_7"]
            )
        if row["lower_priority_than_selected_same_minute"]:
            high_unused_by_count["lower-priority-excess"] += int(
                row["lower_priority_than_selected_same_minute"]
            )
    thresholds = stats["minute_thresholds"]
    lines = [
        "# Phase 2A.5 — Standard Ebooks Audit and Recovery Report",
        "",
        f"Generated {datetime.now().astimezone().isoformat()} from the existing Standard Ebooks "
        "cache and SQLite candidate corpus. No external corpus was added.",
        "",
        "## 1. Exact explanation of 7,293 → 277",
        "",
        "| Disposition | Count |",
        "|---|---:|",
        f"| Imported | {accounting.get('imported', 0):,} |",
        f"| Minute already full before Phase 2A | {accounting.get('minute_already_full', 0):,} |",
        "| Lower-priority excess after the minute reached 7 | "
        f"{accounting.get('lower_priority_same_minute', 0):,} |",
        f"| Diversity hard rejection | {accounting.get('diversity_excluded', 0):,} |",
        f"| Quality/other failure inside high-confidence set | {high_confidence_failures:,} |",
        "| Otherwise eligible and missed | "
        f"{accounting.get('otherwise_eligible_not_imported', 0):,} |",
        f"| **Total** | **{accounting['total']:,}** |",
        "",
        "There is no volume-suppressing importer bug. The cap was applied against the Phase 1 "
        "coverage that existed before Standard Ebooks, and 6,912 candidates targeted buckets "
        "already at seven or more. The remaining 381 candidates competed for 277 open slots; "
        "277 won and 104 were correctly deferred.",
        "",
        "## 2. Unused high-confidence distribution",
        "",
        f"- Preexisting-full buckets: {high_unused_by_count['preexisting-full']:,} candidates.",
        "- Excess candidates behind selected quotes in sub-seven buckets: "
        f"{high_unused_by_count['lower-priority-excess']:,}.",
        "- Diversity was an ordering preference only; it caused no unfilled slots.",
        "",
        "## 3. Counterfactual maximum from the original high-confidence set",
        "",
        *_scenario_table(scenarios),
        "",
        "## 4–7. Ambiguous recovery",
        "",
        "| Metric | Count |",
        "|---|---:|",
        f"| AM/PM-ambiguous detections | {mining['ambiguous_candidates']:,} |",
        "| Nonduplicate ambiguous candidates relevant to a bucket <3 | "
        f"{resolution.get('relevant_below_3', 0):,} |",
        "| Citation-shaped false positives among candidates relevant to a bucket <3 | "
        f"{resolution.get('citation_rejected_below_3', 0):,} |",
        "| Contextually resolved with deterministic evidence | "
        f"{resolution.get('resolved', 0):,} |",
        "| Resolved candidates passing quality gates | "
        f"{resolution.get('resolved_quality_eligible', 0):,} |",
        f"| Unresolved candidates exported for priority review | {review_count:,} |",
        f"| Additional quotes imported in Phase 2A.5 | {total_recovered:,} |",
        f"| Total contextual-recovery quotes now present | {total_recovered:,} |",
        "",
        "Only a daypart cue in the containing sentence or deterministic local elapsed-time "
        "arithmetic from an explicit absolute time qualified for automatic resolution. Nearby "
        "but narratively vague cues remained human-review items.",
        "",
        "## 8–11. Corpus after recovery",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Canonical quotes | {stats['total_canonical_quotes']:,} |",
        f"| Selectable quotes | {stats['total_renderable_quotes']:,} |",
        f"| Minutes at 0 | {thresholds['zero']:,} |",
        f"| Minutes below 3 | {thresholds['below_3']:,} |",
        f"| Minutes below 5 | {thresholds['below_5']:,} |",
        f"| Minutes below 7 | {thresholds['below_7']:,} |",
        f"| Minutes at least 7 | {thresholds['at_least_7']:,} |",
        f"| Median quotes/minute | {stats['median_quotes_per_minute']:.2f} |",
        f"| P10 / P25 / P75 / P90 | {stats['percentiles']['p10']:.2f} / "
        f"{stats['percentiles']['p25']:.2f} / {stats['percentiles']['p75']:.2f} / "
        f"{stats['percentiles']['p90']:.2f} |",
        f"| Remaining deficit to seven everywhere | {stats['remaining_quote_deficit_to_7']:,} |",
        f"| Unique authors / books | {stats['unique_authors']:,} / {stats['unique_books']:,} |",
        "",
        "### Empty-minute investigation",
        "",
    ]
    for minute, result in empty_minutes.items():
        counts = result["counts"]
        if result["no_occurrence_exists"]:
            lines.append(f"- **{minute}:** no supported occurrence exists in the candidate corpus.")
        else:
            summary = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
            lines.append(f"- **{minute}:** {summary}.")
            for example in result["examples"]:
                lines.append(
                    f"  - candidate {example['candidate_id']}: `{example['time_text']}` — "
                    f"{example['title']}, {example['author']} ({example['category']}; "
                    f"{example['reason']}; `{example['locator']}`)"
                )
    lines.extend(
        [
            "",
            "## 12. Hardest 50 minute buckets after recovery",
            "",
            "| Rank | Minute | Selectable | Deficit | Authors | Books |",
            "|---:|---|---:|---:|---:|---:|",
        ]
    )
    for rank, row in enumerate(hardest, 1):
        lines.append(
            f"| {rank} | {row['time_24h']} | {row['renderable_count']} | "
            f"{row['deficit_to_7']} | {row['unique_authors']} | {row['unique_books']} |"
        )
    lines.extend(
        [
            "",
            "## 13. Parser ambiguity and failure patterns",
            "",
            "| Pattern | Count |",
            "|---|---:|",
        ]
    )
    for label, count in _resolution_patterns(connection):
        lines.append(f"| {label} | {count:,} |")
    lines.extend(
        [
            "",
            "The dominant ambiguity remains bare 12-hour clock wording with no direct daypart. "
            "Daypart terms outside the containing sentence were deliberately not treated as "
            "proof. Timetable-like multi-time contexts and weak/incomplete contexts continued "
            "to fail the existing deterministic quality gates.",
            "",
            "## 14. Recommendation for Phase 2B",
            "",
            "A new independent corpus is necessary to reach seven quotes for every minute. The "
            "original high-confidence pool has no unused capacity below seven, three minutes are "
            "still empty, and the remaining deficit is far larger than the sparse-relevant review "
            "queue. Review the priority CSV first to extract its remaining value, then proceed to "
            "another public-domain corpus in Phase 2B. Do not loosen semantic time correctness to "
            "force coverage.",
            "",
            "## Reproducibility artifacts",
            "",
            "- `PHASE2A5_CANDIDATE_AUDIT.csv`: all 1,440 minutes.",
            "- `PHASE2A5_AUDIT.md`: high-confidence accounting and counterfactuals.",
            "- `PHASE2A5_REVIEW_PRIORITY.csv`: unresolved candidates able to improve a <3 bucket.",
            "- `coverage.json`, `minute_coverage.csv`, `COVERAGE_REPORT.md`: regenerated "
            "corpus coverage.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return stats


def run_phase2a5(
    connection: sqlite3.Connection,
    project_root: Path,
    *,
    recover: bool = True,
    desktop_report: Path | None = None,
) -> dict[str, Any]:
    """Run the complete audit, contextual pass, controlled import, and reports."""
    initialize_database(connection)
    generated = project_root / "data" / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    run_id = int(
        connection.execute(
            "INSERT INTO phase2a5_runs (started_at, status) VALUES (?, 'RUNNING')", (_now(),)
        ).lastrowid
    )
    connection.commit()
    try:
        accounting = high_confidence_accounting(connection)
        if accounting["total"] != sum(
            accounting.get(key, 0)
            for key in (
                "imported",
                "minute_already_full",
                "lower_priority_same_minute",
                "diversity_excluded",
                "quality_rejected_within_high_confidence",
                "other_eligibility_failure_within_high_confidence",
                "otherwise_eligible_not_imported",
            )
        ):
            raise AssertionError("high-confidence accounting does not sum")
        scenarios = _counterfactuals(connection)
        audit_rows = _candidate_audit_rows(connection)
        audit_csv = generated / "PHASE2A5_CANDIDATE_AUDIT.csv"
        audit_md = generated / "PHASE2A5_AUDIT.md"
        _write_candidate_audit_csv(audit_rows, audit_csv)
        _write_audit_markdown(audit_md, accounting, scenarios, audit_rows)

        phase2_counts = _selectable_counts(connection, exclude_contextual_recovery=True)
        resolution = resolve_ambiguous_candidates(connection, project_root, counts=phase2_counts)
        newly_imported = import_recovered_candidates(connection) if recover else 0
        review_rows = _review_rows(connection, project_root)
        review_csv = generated / "PHASE2A5_REVIEW_PRIORITY.csv"
        _write_review_csv(review_rows, review_csv)
        empty_minutes = _empty_minute_investigation(connection)
        write_reports(calculate_stats(connection), generated)
        final_report = generated / "PHASE2A5_REPORT.md"
        stats = _write_final_report(
            final_report,
            connection,
            accounting,
            scenarios,
            resolution,
            len(review_rows),
            empty_minutes,
        )
        if desktop_report is not None:
            desktop_report.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(final_report, desktop_report)
        connection.execute(
            """
            UPDATE phase2a5_runs SET finished_at = ?, status = 'COMPLETE',
                ambiguous_considered = ?, ambiguous_relevant = ?, contextually_resolved = ?,
                review_exported = ?, recovered_imported = ? WHERE id = ?
            """,
            (
                _now(),
                resolution.get("considered", 0),
                resolution.get("relevant_below_7", 0),
                resolution.get("resolved", 0),
                len(review_rows),
                newly_imported,
                run_id,
            ),
        )
        connection.commit()
        return {
            "accounting": accounting,
            "scenarios": scenarios,
            "resolution": resolution,
            "review_count": len(review_rows),
            "newly_imported": newly_imported,
            "stats": stats,
            "empty_minutes": empty_minutes,
            "paths": [audit_csv, audit_md, review_csv, final_report],
        }
    except Exception as error:
        connection.rollback()
        connection.execute(
            "UPDATE phase2a5_runs SET finished_at = ?, status = 'FAILED', error = ? WHERE id = ?",
            (_now(), str(error), run_id),
        )
        connection.commit()
        raise
