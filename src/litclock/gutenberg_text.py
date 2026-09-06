"""Project Gutenberg plain-text cleanup with source-offset preservation."""

from __future__ import annotations

import re
from dataclasses import dataclass

_START_RE = re.compile(
    r"(?im)^[ \t]*\*{3}\s*START OF (?:THIS|THE) PROJECT GUTENBERG EBOOK\b.*?\*{3}[ \t\r]*$"
)
_END_RE = re.compile(
    r"(?im)^[ \t]*\*{3}\s*END OF (?:THIS|THE) PROJECT GUTENBERG EBOOK\b.*?\*{3}[ \t\r]*$"
)
_BLOCK_RE = re.compile(
    r"(?ms)(?:\A|(?:\r?\n)[ \t]*(?:\r?\n)+)(.*?)(?=(?:\r?\n)[ \t]*(?:\r?\n)+|\Z)"
)
_CHAPTER_RE = re.compile(
    r"^(?:CHAPTER|BOOK|PART|ACT|SCENE)\s+(?:[IVXLCDM]+|\d+|ONE|TWO|THREE|FOUR|FIVE|SIX|"
    r"SEVEN|EIGHT|NINE|TEN)\b",
    re.IGNORECASE,
)
_BACKMATTER_RE = re.compile(
    r"^(?:INDEX|BIBLIOGRAPHY|REFERENCES|GLOSSARY|CATALOGUE|CATALOG)\.?$", re.IGNORECASE
)
_TOC_RE = re.compile(r"^(?:TABLE OF )?CONTENTS\.?$", re.IGNORECASE)
_TRANSCRIBER_RE = re.compile(
    r"(?:TRANSCRIBER(?:’|')?S? NOTE|TRANSCRIPTION NOTE|PROOFREADING TEAM|"
    r"EBOOK (?:WAS )?PRODUCED BY|PREPARED BY)",
    re.IGNORECASE,
)
_PAGE_RE = re.compile(r"\[(?:Pg|Page)\.?\s*\d+\]", re.IGNORECASE)
_DOT_LEADER_RE = re.compile(r"\.{3,}\s*(?:[ivxlcdm]+|\d+)\s*$", re.IGNORECASE)
_TOKEN_RE = re.compile(r"\b[^\W_]+(?:[’'][^\W_]+)?\b", re.UNICODE)


@dataclass(frozen=True, slots=True)
class GutenbergParagraph:
    text: str
    paragraph_index: int
    section: str | None
    raw_start: int
    raw_end: int
    offset_map: tuple[int, ...]

    def source_offset(self, text_offset: int) -> int:
        if text_offset < 0 or text_offset > len(self.text):
            raise ValueError("paragraph offset outside text")
        if text_offset == len(self.text):
            return self.raw_end
        return self.offset_map[text_offset]


@dataclass(frozen=True, slots=True)
class CleanedGutenbergText:
    text: str
    content_start: int
    content_end: int


def strip_gutenberg_boilerplate(text: str) -> CleanedGutenbergText:
    """Return only text between official START/END markers."""
    start_match = _START_RE.search(text)
    if not start_match:
        raise ValueError("Project Gutenberg START marker not found")
    end_match = _END_RE.search(text, start_match.end())
    if not end_match:
        raise ValueError("Project Gutenberg END marker not found")
    start = start_match.end()
    while start < end_match.start() and text[start] in "\r\n":
        start += 1
    end = end_match.start()
    while end > start and text[end - 1] in "\r\n":
        end -= 1
    return CleanedGutenbergText(text[start:end], start, end)


def _render_block(block: str, raw_start: int) -> tuple[str, tuple[int, ...]]:
    """Join hard-wrapped lines and retain a map to raw character offsets."""
    output: list[str] = []
    offsets: list[int] = []
    position = 0
    for line_number, raw_line in enumerate(block.splitlines(keepends=True)):
        line = raw_line.rstrip("\r\n")
        line_start = position
        position += len(raw_line)
        page_matches = list(_PAGE_RE.finditer(line))
        blocked = [range(match.start(), match.end()) for match in page_matches]
        kept = [index for index in range(len(line)) if not any(index in span for span in blocked)]
        first = next((index for index in kept if not line[index].isspace()), None)
        last = next((index for index in reversed(kept) if not line[index].isspace()), None)
        if first is None or last is None:
            continue
        if output and line_number:
            output.append(" ")
            offsets.append(raw_start + line_start)
        for index in kept:
            if index < first or index > last:
                continue
            output.append(line[index])
            offsets.append(raw_start + line_start + index)
    rendered = "".join(output)
    rendered = re.sub(r"[ \t]+", " ", rendered).strip()
    if len(rendered) != len(offsets):
        # Whitespace compaction above is uncommon after line joining; rebuild the map exactly.
        compact: list[str] = []
        compact_offsets: list[int] = []
        previous_space = False
        for char, source in zip(output, offsets, strict=True):
            if char.isspace():
                if not compact or previous_space:
                    continue
                char = " "
                previous_space = True
            else:
                previous_space = False
            compact.append(char)
            compact_offsets.append(source)
        while compact and compact[-1] == " ":
            compact.pop()
            compact_offsets.pop()
        rendered = "".join(compact)
        offsets = compact_offsets
    return rendered, tuple(offsets)


def extract_gutenberg_paragraphs(text: str) -> list[GutenbergParagraph]:
    """Extract literary-looking paragraphs, excluding marked boilerplate and back matter."""
    cleaned = strip_gutenberg_boilerplate(text)
    paragraphs: list[GutenbergParagraph] = []
    in_contents = False
    section: str | None = None
    paragraph_index = 0
    for match in _BLOCK_RE.finditer(cleaned.text):
        block = match.group(1)
        if not block.strip():
            continue
        raw_start = cleaned.content_start + match.start(1)
        rendered, offset_map = _render_block(block, raw_start)
        if not rendered:
            continue
        compact_heading = " ".join(rendered.split())
        if _BACKMATTER_RE.fullmatch(compact_heading):
            break
        if _TOC_RE.fullmatch(compact_heading):
            in_contents = True
            continue
        if in_contents:
            if _CHAPTER_RE.match(compact_heading) and not _DOT_LEADER_RE.search(compact_heading):
                in_contents = False
                section = compact_heading[:160]
            else:
                continue
        if _TRANSCRIBER_RE.search(compact_heading):
            continue
        if _DOT_LEADER_RE.search(compact_heading):
            continue
        if _CHAPTER_RE.match(compact_heading):
            section = compact_heading[:160]
            continue
        paragraph_index += 1
        paragraphs.append(
            GutenbergParagraph(
                text=rendered,
                paragraph_index=paragraph_index,
                section=section,
                raw_start=offset_map[0],
                raw_end=(offset_map[-1] + 1),
                offset_map=offset_map,
            )
        )
    return paragraphs


def text_quality_rejection(paragraph: str, quote: str) -> str | None:
    """Reject obvious tables, metadata, damaged text, and reference-like material."""
    words = _TOKEN_RE.findall(quote)
    if len(words) < 8:
        return "too few prose words"
    if "project gutenberg" in quote.casefold():
        return "Project Gutenberg boilerplate"
    if _TRANSCRIBER_RE.search(quote):
        return "transcriber/production material"
    if re.match(r"\s*(?:CHAPTER|BOOK|PART|ACT|SCENE)\b", quote, re.IGNORECASE):
        return "chapter/scene heading joined to prose"
    alphabetic = "".join(char for char in quote if char.isalpha())
    if alphabetic and alphabetic.isupper():
        return "all-caps time heading"
    if re.match(r"\s*[A-Z][A-Z .'-]{4,}\b", quote) and re.search(
        r"\bTime,?\s+\d{1,2}[.:]\d{2}", quote
    ):
        return "all-caps time heading"
    if re.match(r"\s*[A-Z][A-Z .'’,-]{3,40}\d{1,2}[.:]\d{2}\s*[AP]\.\s*M\.", quote):
        return "dateline/log heading"
    if re.match(r'\s*["“]?HEADQUARTERS\b', quote, re.IGNORECASE) and re.search(
        r"\d{1,2}[.:]\d{2}\s*[ap]\.?\s*m\.?", quote, re.IGNORECASE
    ):
        return "document/log heading"
    if quote.count("“") != quote.count("”") or (
        quote.lstrip().startswith('"') and quote.count('"') % 2
    ):
        return "broken dialogue fragment"
    if re.match(r"\s*\[\d+\]\s+", quote):
        return "footnote/endnote context"
    if re.match(
        r"\s*[A-Z][A-Z\s.'’-]{2,},\s*\d{1,2}[.:]\d{2}\s*[aApP]\.?\s*[mM]\.?,?",
        quote,
    ):
        return "dateline/log heading"
    if re.search(r"\b(?:No|Mr|Mrs|Ms|Dr|Prof|St)\.$", quote):
        return "truncated abbreviation context"
    if re.search(r"\bthe\s+\d{1,2}[.:]\d{2}\s*[ap]\.?\s*m\.?$", quote, re.IGNORECASE):
        return "truncated timetable expression"
    if re.match(r"\s*(?:\*\s*){3,}", quote):
        return "section ornament joined to prose"
    numeric_times = re.findall(r"\b\d{1,2}[.:]\d{2}(?:\s*[ap]\.?\s*m\.?)?", quote, re.I)
    if re.match(
        r"\s*(?:Started|Starting|Reached|Camped|Halted|Resumed)(?:\s+again)?\s+(?:at|by)\b",
        quote,
        re.IGNORECASE,
    ):
        return "telegraphic travel-log context"
    if re.match(
        r"\s*(?:Started|Start|Reached|Camped|Halted|Resumed|Arrived|Departed|"
        r"Left|Leaving|Steered|Steering|Struck|Changing|Following|Proceed(?:ed|ing)|"
        r"Then\s+bearing)\b",
        quote,
        re.IGNORECASE,
    ):
        return "telegraphic travel-log context"
    if re.match(
        r"\s*At\s+\d{1,2}[.:]\d{2}(?:\s*[ap]\.?\s*m\.?)?,?\s+"
        r"(?:made|steered|started|came|sat|went|left|arrived|reached|returned|"
        r"resumed|camped|halted|altered|changed|crossed|recrossed|followed|proceeded|found)\b",
        quote,
        re.IGNORECASE,
    ):
        return "navigation/travel-log context"
    navigation_terms = len(
        re.findall(
            r"\b(?:bearing|bivouac|camp|course|creek|degrees?|direction|miles?|"
            r"north|south|east|west|steered)\b",
            quote,
            re.IGNORECASE,
        )
    )
    if len(numeric_times) >= 2 and navigation_terms >= 2:
        return "navigation/travel-log context"
    technical_terms = len(
        re.findall(
            r"\b(?:altitude|latitude|longitude|degrees?|temperatures?|thermometer|barometer|"
            r"hygrometer|almicanteras?|horizon|measurements?|readings?|registered)\b",
            quote,
            re.IGNORECASE,
        )
    )
    numeric_values = len(re.findall(r"(?<!\w)\d+(?:[.·]\d+)?", quote))
    if technical_terms >= 2 and numeric_values >= 3:
        return "scientific measurement context"
    if len(numeric_times) >= 2 and re.search(r"\b(?:Lv|Ar)\.", quote):
        return "schedule/table context"
    if re.match(
        r"\s*(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+"
        r"[A-Z][A-Z\s.'’-]{3,},?\s+\d{1,2}[.:]\d{2}",
        quote,
    ):
        return "dated location/log heading"
    dated_log = re.match(
        r"\s*(?:"
        r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|"
        r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2}"
        r"|(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
        r"(?:,|\s+the)\s+[^.!?]{0,35}\d{1,2}(?:st|nd|rd|th)?"
        r")",
        quote,
        re.IGNORECASE,
    )
    if dated_log:
        return "dated journal/log context"
    if numeric_times and navigation_terms >= 4:
        return "navigation/travel-log context"
    if re.match(r'\s*["“]?HEADQUARTERS\b', quote, re.IGNORECASE) and re.search(
        r"\d{1,2}[.:]\d{2}\s*[ap]\.?\s*m\.?", quote, re.IGNORECASE
    ):
        return "document/log heading"
    if re.match(
        r"\s*(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+"
        r"\d{1,2}[.:]\d{2}\s*[ap]\.?\s*m\.?[^.!?]{0,20}\d{2,3}°",
        quote,
        re.IGNORECASE,
    ):
        return "dated weather-log context"
    if re.search(
        r"(?:^|\s)_?\d{1,2}(?:st|nd|rd|th)_?\.\s*(?:--|—|–)\s*"
        r"\d{1,2}[.:]\d{2}\s*[ap]\.?\s*m\.?",
        quote,
        re.IGNORECASE,
    ) and re.search(r"\b(?:temperature|water boiled|elevation)\b", quote, re.IGNORECASE):
        return "dated weather-log context"
    if (
        re.search(
            r"\b(?:arrival|departure|arrive|depart)\s*[,.:;-]?\s*\d{1,2}[.:]\d{2}",
            quote,
            re.IGNORECASE,
        )
        and len(re.findall(r"\b(?:arrival|departure|luggage|hotel|steamer|train)\b", quote, re.I))
        >= 2
    ):
        return "timetable/itinerary context"
    if len(numeric_times) >= 2 and re.search(
        r"\b(?:arrives?|depart(?:s|ure)?|leaves?|railway|timetable)\b", quote, re.I
    ):
        return "timetable/itinerary context"
    if re.match(
        r"\s*[\"'(]*\d{1,2}[.:]\d{2}\s*[aApP]\.?\s*[mM]\.?(?:\s|--|—|–)+[A-Z(]",
        quote,
    ):
        return "log/timeline context"
    if re.match(
        r"\s*At\s+\d{1,2}[.:]\d{2}\s*[ap]\.?\s*m\.?,?\s+"
        r"(?:left|arrived|entered|departed|returned|called)\b",
        quote,
        re.I,
    ):
        return "telegraphic log context"
    if re.match(r"\s*\(?Handed in at\s+\d{1,2}[.:]\d{2}", quote, re.IGNORECASE):
        return "telegram/log context"
    if re.match(r"\s*(?:Arrived|Departed|Left)\s+at\b", quote, re.IGNORECASE) and re.search(
        r"\d{1,2}[.:]\d{2}\s*[ap]\.?\s*m\.?\s*$", quote, re.IGNORECASE
    ):
        return "timetable/itinerary context"
    if sum(char == "|" for char in quote) >= 2 or sum(char == "\t" for char in quote) >= 2:
        return "table-like context"
    if len(re.findall(r"\b\d+\b", quote)) >= max(5, len(words) // 3):
        return "numeric list/table context"
    if re.search(r"(?:ISBN|CATALOG(?:UE)? NO\.|SERIAL NO\.|COPYRIGHT ©)", quote, re.I):
        return "catalog/metadata context"
    replacement_ratio = quote.count("�") / max(1, len(quote))
    if replacement_ratio > 0.005:
        return "decoding/OCR corruption"
    alpha = sum(char.isalpha() for char in quote)
    if alpha / max(1, len(quote)) < 0.5:
        return "insufficient prose content"
    if len(set(words)) < max(4, len(words) // 5):
        return "highly repetitive text"
    return None
