"""Canonical-to-display transformations and sentence-aligned excerpting."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from litclock.models import Quote
from litclock.render.models import DirtyRecordStatus, RenderQuote, RenderValidationError

_SERIALIZED_METADATA = re.compile(
    r"\|[^|\n]{1,180}\|[^|\n]{1,120}\|(?:sfw|nsfw|unknown)\s+\d{1,2}:\d{2}\|",
    re.IGNORECASE,
)
_STATUS_TIME_FIELD = re.compile(r"\|(?:sfw|nsfw|unknown)\s+\d{1,2}:\d{2}\|", re.IGNORECASE)
_EXPORT_FIELD_RUN = re.compile(r"(?:\|[^|\n]{1,180}){4,}\|")
_WEB_EXPORT = re.compile(
    r"\b(?:webjournal|weblog|blog)\b.*\bposted\s+at:\s*\d{1,2}:\d{2}.*\bstatus:",
    re.IGNORECASE | re.DOTALL,
)
_ROLE = re.compile(r"\[\s*([^\]]+)\s*\]", re.IGNORECASE)
_LIFE_DATES = re.compile(
    r",?\s*(?:(?:ca\.\s*)?\d{3,4}\s*[-–—]\s*(?:\d{2,4})?|"
    r"active\s+\d{3,4}(?:\s*[-–—]\s*\d{2,4})?)",
    re.IGNORECASE,
)
_SINGLE_METADATA_DATE = re.compile(r",\s*\d{3,4}\??\s*$")
_SUBTITLE_PHRASE = re.compile(
    r"(?:\s*[,;:—-]\s*|\s+)"
    r"(?:being\s+(?:the\s+narrative\s+of|an?\s+(?:account|narrative)\s+of)|"
    r"an\s+account\s+of|a\s+narrative\s+of)",
    re.IGNORECASE,
)
_COLLECTION_SIGNAL = re.compile(
    r"\b(?:anthology|collection|library|collected|stories|tales)\b", re.IGNORECASE
)
_SUBTITLE_SIGNAL = re.compile(
    r"\b(?:account|adventures?|autobiography|classic|journey|memoir|narrative|stories|"
    r"tales|tour|travel)\b",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"[.!?]+[\"”’')\]]*(?=\s+|$)")
_ABBREVIATIONS = {
    "a.m.",
    "ca.",
    "dr.",
    "e.g.",
    "etc.",
    "i.e.",
    "jr.",
    "mr.",
    "mrs.",
    "ms.",
    "p.m.",
    "prof.",
    "sr.",
    "st.",
    "vs.",
}


@dataclass(frozen=True, slots=True)
class AttributionDisplay:
    title: str
    creator: str
    title_simplified: bool
    author_simplified: bool
    anthology_mode: bool


def classify_dirty_record(text: str) -> DirtyRecordStatus:
    """Reject recognizable source serialization before it reaches layout."""
    metadata_runs = len(_SERIALIZED_METADATA.findall(text))
    status_fields = len(_STATUS_TIME_FIELD.findall(text))
    if metadata_runs or status_fields >= 2:
        return DirtyRecordStatus.MULTI_RECORD_CONCATENATION
    if text.count("|") >= 4 or _EXPORT_FIELD_RUN.search(text) or _WEB_EXPORT.search(text):
        return DirtyRecordStatus.DIRTY_SERIALIZED_RECORD
    return DirtyRecordStatus.CLEAN


def simplify_title(title: str) -> str:
    """Remove clear catalog-style subtitle material without rewriting the title proper."""
    cleaned = " ".join(title.split()).strip()
    if not cleaned:
        return ""
    phrase = _SUBTITLE_PHRASE.search(cleaned)
    if phrase and phrase.start() >= 3:
        return cleaned[: phrase.start()].rstrip(" ,;:—-")
    comma_or = re.search(r",\s+or\b", cleaned, re.IGNORECASE)
    if comma_or and comma_or.start() >= 3:
        return cleaned[: comma_or.start()].rstrip()
    colon_positions = [match.start() for match in re.finditer(r":", cleaned)]
    if colon_positions:
        suffix = cleaned[colon_positions[0] + 1 :]
        if len(colon_positions) >= 2 or (len(cleaned) > 70 and _SUBTITLE_SIGNAL.search(suffix)):
            return cleaned[: colon_positions[0]].rstrip()
    if len(cleaned) > 85:
        for separator in (";", " — ", " - "):
            if separator in cleaned:
                head, tail = cleaned.split(separator, 1)
                if len(head) >= 8 and len(tail) >= 20:
                    return head.rstrip()
    return cleaned


def _normalize_person(segment: str) -> tuple[str, str | None]:
    role_match = _ROLE.search(segment)
    role_text = role_match.group(1).casefold() if role_match else ""
    if "editor" in role_text:
        role = "editor"
    elif "contributor" in role_text:
        role = "contributor"
    elif "illustrator" in role_text:
        role = "illustrator"
    elif "translator" in role_text:
        role = "translator"
    elif "compiler" in role_text:
        role = "compiler"
    elif "introduction" in role_text:
        role = "introduction"
    else:
        role = None
    name = _ROLE.sub("", segment)
    name = _LIFE_DATES.sub("", name)
    name = _SINGLE_METADATA_DATE.sub("", name)
    name = " ".join(name.replace("  ", " ").split()).strip(" ,;")
    parts = [part.strip() for part in name.split(",")]
    corporate = re.search(r"\b(?:association|company|committee|society|university)\b", name, re.I)
    if len(parts) == 2 and all(parts) and not corporate:
        name = f"{parts[1]} {parts[0]}"
    return name, role


def normalize_attribution(title: str, author: str) -> AttributionDisplay:
    display_title = simplify_title(title)
    raw_segments = [segment.strip() for segment in author.split(";") if segment.strip()]
    creators = [_normalize_person(segment) for segment in raw_segments]
    creators = [(name, role) for name, role in creators if name]
    editors = [name for name, role in creators if role == "editor"]
    anthology = bool(
        len(creators) >= 3
        or (editors and len(creators) >= 2)
        or (_COLLECTION_SIGNAL.search(title) and len(creators) >= 2)
    )
    if anthology and editors:
        display_author = f"Edited by {editors[0]}"
    elif len(creators) >= 3:
        display_author = f"{creators[0][0]} et al."
    elif len(creators) == 2:
        display_author = f"{creators[0][0]} and {creators[1][0]}"
    elif creators:
        display_author = creators[0][0]
    else:
        display_author = ""
    return AttributionDisplay(
        title=display_title,
        creator=display_author,
        title_simplified=display_title != title,
        author_simplified=display_author != author,
        anthology_mode=anthology,
    )


def prepare_full_quote(quote: Quote) -> RenderQuote:
    if quote.highlight_start is None or quote.highlight_end is None:
        raise RenderValidationError(f"quote {quote.id} has no trustworthy highlight offsets")
    highlighted = quote.quote[quote.highlight_start : quote.highlight_end]
    if not highlighted:
        raise RenderValidationError(f"quote {quote.id} has an empty highlight")
    provenance = ":".join(part for part in (quote.source_name, quote.source_record_id) if part)
    attribution = normalize_attribution(quote.title, quote.author)
    return RenderQuote(
        quote_id=quote.id,
        canonical_quote=quote.quote,
        display_quote=quote.quote,
        canonical_highlight_start=quote.highlight_start,
        canonical_highlight_end=quote.highlight_end,
        highlight_start=quote.highlight_start,
        highlight_end=quote.highlight_end,
        highlighted_time_text=highlighted,
        canonical_title=quote.title,
        display_title=attribution.title,
        canonical_author=quote.author,
        display_author=attribution.creator,
        display_minute=quote.minute_of_day,
        source_provenance_id=provenance or None,
        excerpt_start=0,
        excerpt_end=len(quote.quote),
        title_simplified=attribution.title_simplified,
        author_simplified=attribution.author_simplified,
        anthology_mode=attribution.anthology_mode,
        dirty_record_status=classify_dirty_record(quote.quote),
    )


def sentence_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        ending = match.end()
        prefix = text[start:ending]
        last_token = prefix.rstrip("\"”’')]").split()[-1].casefold() if prefix.split() else ""
        if last_token in _ABBREVIATIONS or re.fullmatch(r"[a-z]\.", last_token):
            continue
        sentence_start = start
        while sentence_start < ending and text[sentence_start].isspace():
            sentence_start += 1
        if sentence_start < ending:
            spans.append((sentence_start, ending))
        start = ending
    while start < len(text) and text[start].isspace():
        start += 1
    if start < len(text):
        spans.append((start, len(text)))
    return spans or [(0, len(text))]


def validate_excerpt_integrity(quote: RenderQuote) -> DirtyRecordStatus:
    prefix = "… " if quote.leading_ellipsis else ""
    suffix = " …" if quote.trailing_ellipsis else ""
    expected = prefix + quote.canonical_quote[quote.excerpt_start : quote.excerpt_end] + suffix
    if expected != quote.display_quote:
        return DirtyRecordStatus.EXCERPT_CORRUPTION
    expected_highlight = (
        prefix + quote.canonical_quote[quote.excerpt_start : quote.canonical_highlight_start]
    )
    if len(expected_highlight) != quote.highlight_start:
        return DirtyRecordStatus.EXCERPT_CORRUPTION
    return quote.dirty_record_status


def make_excerpt(quote: RenderQuote, start: int, end: int) -> RenderQuote:
    while start < end and quote.canonical_quote[start].isspace():
        start += 1
    while end > start and quote.canonical_quote[end - 1].isspace():
        end -= 1
    if not (start <= quote.canonical_highlight_start and quote.canonical_highlight_end <= end):
        raise RenderValidationError("excerpt does not contain the complete highlighted phrase")
    leading = start > 0
    trailing = end < len(quote.canonical_quote)
    prefix = "… " if leading else ""
    suffix = " …" if trailing else ""
    display = prefix + quote.canonical_quote[start:end] + suffix
    highlight_start = len(prefix) + quote.canonical_highlight_start - start
    excerpt = replace(
        quote,
        display_quote=display,
        highlight_start=highlight_start,
        highlight_end=highlight_start + len(quote.highlighted_time_text),
        excerpt_start=start,
        excerpt_end=end,
        leading_ellipsis=leading,
        trailing_ellipsis=trailing,
        sentence_aligned=True,
    )
    integrity = validate_excerpt_integrity(excerpt)
    if integrity == DirtyRecordStatus.EXCERPT_CORRUPTION:
        return replace(excerpt, dirty_record_status=integrity)
    return excerpt


def excerpt_candidates(quote: RenderQuote) -> list[RenderQuote]:
    spans = sentence_spans(quote.canonical_quote)
    target = next(
        (
            index
            for index, (start, end) in enumerate(spans)
            if start <= quote.canonical_highlight_start < end
        ),
        None,
    )
    if target is None:
        return []
    windows = (
        (max(0, target - 1), min(len(spans) - 1, target + 1)),
        (max(0, target - 1), target),
        (target, min(len(spans) - 1, target + 1)),
        (target, target),
    )
    candidates: list[RenderQuote] = []
    seen: set[tuple[int, int]] = set()
    for first, last in windows:
        bounds = (spans[first][0], spans[last][1])
        if bounds in seen or bounds == (0, len(quote.canonical_quote)):
            continue
        seen.add(bounds)
        candidates.append(make_excerpt(quote, *bounds))
    return candidates
