"""Semantic Standard Ebooks XHTML prose and sentence-context extraction."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from litclock.models import ExtractedParagraph, QuoteContext, TimeDetection

_EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"
_XML_SPACE_RE = re.compile(r"[ \t\r\n\f\v]+")
_NON_PROSE_FILES = {
    "colophon",
    "copyright",
    "cover",
    "halftitlepage",
    "imprint",
    "loi",
    "titlepage",
    "toc",
    "uncopyright",
}
_NON_PROSE_FILE_PARTS = {
    "acknowledgement",
    "bibliography",
    "colophon",
    "copyright",
    "endnote",
    "footnote",
    "glossary",
    "imprint",
    "index",
    "titlepage",
    "transcriber",
    "uncopyright",
}
_BLOCKED_TAGS = {"aside", "dl", "figure", "nav", "ol", "table", "ul"}
_BLOCKED_TYPES = {
    "backmatter",
    "bibliography",
    "colophon",
    "copyright-page",
    "endnote",
    "endnotes",
    "footnote",
    "footnotes",
    "glossary",
    "halftitlepage",
    "imprint",
    "index",
    "landmarks",
    "loi",
    "lot",
    "note",
    "rearnote",
    "titlepage",
    "toc",
}
_ABBREVIATIONS = {
    "a.m",
    "capt",
    "col",
    "dr",
    "e.g",
    "etc",
    "ft",
    "gen",
    "i.e",
    "jr",
    "lat",
    "long",
    "mr",
    "mrs",
    "ms",
    "p.m",
    "prof",
    "rev",
    "sen",
    "sr",
    "st",
    "vol",
    "vs",
    "yd",
    "yds",
}
_CLOSERS = "”’\"')]}»"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def is_literary_text_file(path: Path) -> bool:
    stem = path.stem.casefold()
    if stem in _NON_PROSE_FILES:
        return False
    return not any(part in stem for part in _NON_PROSE_FILE_PARTS)


def _type_tokens(element: ET.Element) -> set[str]:
    values = element.attrib.get(_EPUB_TYPE, "").casefold().split()
    return {value.rsplit(":", 1)[-1] for value in values}


def _is_blocked(element: ET.Element, parents: dict[ET.Element, ET.Element]) -> bool:
    current: ET.Element | None = element
    while current is not None:
        if _local_name(current.tag) in _BLOCKED_TAGS:
            return True
        if _type_tokens(current) & _BLOCKED_TYPES:
            return True
        if current.attrib.get("hidden") is not None:
            return True
        current = parents.get(current)
    return False


def _semantic_text(element: ET.Element) -> str:
    rendered = "".join(element.itertext())
    return _XML_SPACE_RE.sub(" ", rendered).strip(" \t\r\n\f\v")


def _section_id(element: ET.Element, parents: dict[ET.Element, ET.Element]) -> str | None:
    current: ET.Element | None = element
    while current is not None:
        if current.attrib.get("id"):
            return current.attrib["id"]
        current = parents.get(current)
    return None


def extract_paragraphs(path: Path, source_file: str | None = None) -> list[ExtractedParagraph]:
    """Extract bodymatter prose paragraphs without applying regexes to raw markup."""
    if not is_literary_text_file(path):
        return []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as error:
        raise ValueError(f"invalid XHTML {path}: {error}") from error
    body = next((element for element in root.iter() if _local_name(element.tag) == "body"), None)
    if body is None:
        return []
    body_types = _type_tokens(body)
    if "bodymatter" not in body_types or body_types & {"frontmatter", "backmatter"}:
        return []

    parents = {child: parent for parent in root.iter() for child in parent}
    paragraphs: list[ExtractedParagraph] = []
    document_offset = 0
    paragraph_number = 0
    for element in body.iter():
        if _local_name(element.tag) != "p":
            continue
        paragraph_number += 1
        if _is_blocked(element, parents):
            continue
        text = _semantic_text(element)
        if not text:
            continue
        paragraphs.append(
            ExtractedParagraph(
                text=text,
                source_file=source_file or path.name,
                section=_section_id(element, parents),
                paragraph_index=paragraph_number,
                document_offset=document_offset,
            )
        )
        document_offset += len(text) + 1
    return paragraphs


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Split prose into conservative sentence spans while retaining exact offsets."""
    spans: list[tuple[int, int]] = []
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char not in ".?!":
            index += 1
            continue
        if char == ".":
            if (
                index > 0
                and index + 1 < len(text)
                and text[index - 1].isdigit()
                and text[index + 1].isdigit()
            ):
                index += 1
                continue
            sentence_prefix = text[start : index + 1]
            meridiem_at_end = bool(re.search(r"\b[ap]\.m\.$", sentence_prefix, re.IGNORECASE))
            if meridiem_at_end:
                next_index = index + 1
                while next_index < len(text) and text[next_index].isspace():
                    next_index += 1
                if next_index < len(text) and not (
                    text[next_index].isupper() or text[next_index] in "“‘\"'"
                ):
                    index += 1
                    continue
            token_match = re.search(r"([A-Za-z](?:[A-Za-z.]*)?)\.$", sentence_prefix)
            token = token_match.group(1).casefold() if token_match else ""
            next_index = index + 1
            while next_index < len(text) and text[next_index].isspace():
                next_index += 1
            direction_sentence_end = bool(
                re.search(r"(?:\d|°)\s*[NSEW]\.$", sentence_prefix, re.IGNORECASE)
                and next_index < len(text)
                and (text[next_index].isupper() or text[next_index] in "“‘\"'")
            )
            if not meridiem_at_end and (
                token in _ABBREVIATIONS
                or (len(token) == 1 and token.isalpha() and not direction_sentence_end)
            ):
                index += 1
                continue
            if index + 1 < len(text) and text[index + 1] == ".":
                while index + 1 < len(text) and text[index + 1] == ".":
                    index += 1
        end = index + 1
        while end < len(text) and text[end] in _CLOSERS:
            end += 1
        if end < len(text) and not text[end].isspace():
            index += 1
            continue
        trimmed_start = start
        while trimmed_start < end and text[trimmed_start].isspace():
            trimmed_start += 1
        if trimmed_start < end:
            spans.append((trimmed_start, end))
        start = end
        while start < len(text) and text[start].isspace():
            start += 1
        index = start
    if start < len(text):
        end = len(text)
        while end > start and text[end - 1].isspace():
            end -= 1
        if end > start:
            spans.append((start, end))
    return spans


def _scores(quote: str, detection: TimeDetection) -> tuple[float, float, str | None]:
    length = len(quote)
    context_score = 40.0
    if 80 <= length <= 350:
        context_score += 35
    elif 40 <= length <= 600:
        context_score += 22
    else:
        context_score -= 30
    if quote and (quote[0].isupper() or quote[0] in "“‘\"'"):
        context_score += 10
    if quote.endswith((".", "?", "!", ".”", "?”", "!”", ".’", "?’", "!’")):
        context_score += 10
    repeated = quote.casefold().count(detection.text.casefold()) > 1
    if repeated:
        context_score -= 30

    words = re.findall(r"\b[^\W\d_]+(?:[’'][^\W\d_]+)?\b", quote, re.UNICODE)
    letters = sum(char.isalpha() for char in quote)
    literary_score = 25.0
    if len(words) >= 8:
        literary_score += 30
    if letters / max(1, len(quote)) >= 0.55:
        literary_score += 20
    if any(mark in quote for mark in (",", ";", ":", "“", '"')):
        literary_score += 15
    if len(set(word.casefold() for word in words)) >= min(10, len(words)):
        literary_score += 10
    metadata_like = bool(
        re.search(
            r"^(?:chapter\s+[\divxlc]+|contents|copyright|isbn|table\s+of|http\S+|"
            r"attendance\s*:|hours\s*:|time\s*[,.:])",
            quote,
            re.IGNORECASE,
        )
    )
    if metadata_like:
        literary_score -= 60

    reason: str | None = None
    if length < 40:
        reason = "context shorter than 40 characters"
    elif length > 600:
        reason = "containing sentence exceeds 600 characters"
    elif repeated:
        reason = "time expression repeats within context"
    elif metadata_like:
        reason = "metadata-like context"
    elif (
        first_alpha := next((char for char in quote if char.isalpha()), "")
    ) and first_alpha.islower():
        reason = "sentence context begins mid-sentence"
    elif not quote.endswith((".", "?", "!", ".”", "?”", "!”", ".’", "?’", "!’")):
        reason = "incomplete sentence boundary"
    return max(0.0, min(100.0, context_score)), max(0.0, min(100.0, literary_score)), reason


def extract_quote_context(paragraph: str, detection: TimeDetection) -> QuoteContext:
    """Extract one or more complete sentences containing the exact detected expression."""
    spans = sentence_spans(paragraph)
    containing = next(
        (
            index
            for index, (start, end) in enumerate(spans)
            if start <= detection.start and detection.end <= end
        ),
        None,
    )
    if containing is None:
        start, end = 0, len(paragraph)
    else:
        start, end = spans[containing]
        previous = containing - 1
        following = containing + 1
        while end - start < 80:
            choices: list[tuple[str, int, int]] = []
            if following < len(spans):
                choices.append(("following", start, spans[following][1]))
            if previous >= 0:
                choices.append(("previous", spans[previous][0], end))
            choice = next((item for item in choices if item[2] - item[1] <= 600), None)
            if choice is None:
                break
            direction, start, end = choice
            if direction == "following":
                following += 1
            else:
                previous -= 1
    while start < end and paragraph[start].isspace():
        start += 1
    while end > start and paragraph[end - 1].isspace():
        end -= 1
    quote = paragraph[start:end]
    highlight_start = detection.start - start
    highlight_end = detection.end - start
    if quote[highlight_start:highlight_end] != detection.text:
        raise AssertionError("highlight offsets did not round-trip through context extraction")
    context_score, literary_score, reason = _scores(quote, detection)
    return QuoteContext(
        quote=quote,
        quote_start=start,
        quote_end=end,
        highlight_start=highlight_start,
        highlight_end=highlight_end,
        context_score=context_score,
        literary_quality_score=literary_score,
        rejection_reason=reason,
    )
