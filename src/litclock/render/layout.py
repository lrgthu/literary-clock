"""Metric-driven, highlight-aware page composition."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace

from PIL import ImageFont

from litclock.render.models import (
    AttributionLine,
    AttributionStyle,
    BodyLine,
    DateLabel,
    LayoutDiagnostics,
    LayoutResult,
    Rectangle,
    RenderQuote,
    StyledSegment,
    TimeEmphasis,
)
from litclock.render.profiles import DeviceProfile
from litclock.render.typography import (
    FontSelection,
    LoadedFonts,
    line_height,
    load_fonts,
    text_width,
)


class LayoutError(RuntimeError):
    def __init__(self, message: str, *, code: str = "layout") -> None:
        super().__init__(message)
        self.code = code


def rectangles_intersect(first: Rectangle, second: Rectangle) -> bool:
    """Return whether two non-empty layout rectangles overlap in both dimensions."""
    return not (
        first.right <= second.left
        or first.left >= second.right
        or first.bottom <= second.top
        or first.top >= second.bottom
    )


def date_font_size(profile: DeviceProfile, attribution_size: int) -> int:
    """Keep the renderer-owned date readable but strictly below attribution size."""
    desired = max(
        profile.minimum_date_size,
        round(attribution_size * profile.date_scale),
    )
    return max(1, min(attribution_size - 1, desired))


@dataclass(frozen=True, slots=True)
class _RawLine:
    start: int
    end: int
    width: float
    paragraph_index: int


@dataclass(frozen=True, slots=True)
class EmphasisMetrics:
    mode: TimeEmphasis
    scale: float
    font_size: int
    baseline_shift: int
    stroke_width: int


def time_emphasis_metrics(
    mode: TimeEmphasis,
    body_size: int,
    *,
    single_face_accent: bool = False,
) -> EmphasisMetrics:
    """Return deterministic mixed-style metrics for a body font size."""
    if mode == TimeEmphasis.CLASSIC:
        scale, shift_ratio = 1.0, 0.0
    elif mode == TimeEmphasis.SUBTLE_LIFT:
        scale, shift_ratio = 1.10, 0.05
    elif mode == TimeEmphasis.EXPRESSIVE:
        scale, shift_ratio = 1.16, 0.075
    else:
        scale, shift_ratio = 1.10, 0.16
    font_size = max(body_size, round(body_size * scale))
    baseline_shift = 0 if shift_ratio == 0 else max(1, round(body_size * shift_ratio))
    stroke_width = (
        max(1, round(body_size * 0.025))
        if mode == TimeEmphasis.PICTURESQUE and single_face_accent
        else 0
    )
    return EmphasisMetrics(mode, scale, font_size, baseline_shift, stroke_width)


def _styled_line_geometry(
    fonts: LoadedFonts,
    emphasis: EmphasisMetrics,
    *,
    spacing: float,
) -> tuple[int, int]:
    """Return a collision-safe line height and the normal-text baseline offset."""
    regular_ascent, regular_descent = fonts.regular.getmetrics()
    highlight_ascent, highlight_descent = fonts.bold.getmetrics()
    top_extent = max(
        regular_ascent,
        highlight_ascent + emphasis.baseline_shift + emphasis.stroke_width,
    )
    bottom_extent = max(
        regular_descent,
        highlight_descent - emphasis.baseline_shift + emphasis.stroke_width,
    )
    ink_height = top_extent + bottom_extent
    styled_height = max(1, round(ink_height * spacing))
    leading = max(0, styled_height - ink_height)
    baseline_offset = leading // 2 + top_extent
    return styled_height, baseline_offset


def mixed_style_width(
    text: str,
    start: int,
    end: int,
    highlight_start: int,
    highlight_end: int,
    regular_font: ImageFont.FreeTypeFont,
    highlight_font: ImageFont.FreeTypeFont,
    *,
    highlight_stroke_width: int = 0,
) -> float:
    """Measure a source range with the highlight rendered in its own font."""
    if start >= end:
        return 0.0
    width = 0.0
    before_end = min(end, max(start, highlight_start))
    if before_end > start:
        width += text_width(regular_font, text[start:before_end])
    styled_start = max(start, highlight_start)
    styled_end = min(end, highlight_end)
    if styled_end > styled_start:
        width += text_width(highlight_font, text[styled_start:styled_end])
        width += 2 * highlight_stroke_width
    after_start = max(start, min(end, highlight_end))
    if end > after_start:
        width += text_width(regular_font, text[after_start:end])
    return width


def _paragraph_ranges(text: str) -> list[tuple[int, int, int]]:
    ranges: list[tuple[int, int, int]] = []
    start = 0
    paragraph = 0
    for match in re.finditer(r"\n+", text):
        if match.start() > start:
            ranges.append((start, match.start(), paragraph))
        paragraph += max(1, len(match.group()))
        start = match.end()
    if start < len(text):
        ranges.append((start, len(text), paragraph))
    return ranges


def _protected_highlight_range(quote: RenderQuote) -> tuple[int, int]:
    """Include punctuation attached to the first/last highlighted words."""
    overlapping = [
        (match.start(), match.end())
        for match in re.finditer(r"\S+", quote.text)
        if match.end() > quote.highlight_start and match.start() < quote.highlight_end
    ]
    if not overlapping:
        return quote.highlight_start, quote.highlight_end
    return overlapping[0][0], overlapping[-1][1]


def _fit_character_end(
    text: str,
    start: int,
    end: int,
    max_width: float,
    measure: object,
) -> int:
    width_for = measure
    assert callable(width_for)
    low, high = start + 1, end
    fitted = start + 1
    while low <= high:
        middle = (low + high) // 2
        if width_for(start, middle) <= max_width:
            fitted = middle
            low = middle + 1
        else:
            high = middle - 1
    return fitted


def _wrap_body(
    quote: RenderQuote,
    fonts: LoadedFonts,
    max_width: float,
    emphasis: EmphasisMetrics,
) -> list[_RawLine]:
    def measure(start: int, end: int) -> float:
        return mixed_style_width(
            quote.text,
            start,
            end,
            quote.highlight_start,
            quote.highlight_end,
            fonts.regular,
            fonts.bold,
            highlight_stroke_width=emphasis.stroke_width,
        )

    lines: list[_RawLine] = []
    for paragraph_start, paragraph_end, paragraph_index in _paragraph_ranges(quote.text):
        words = [
            (match.start(), match.end())
            for match in re.finditer(r"\S+", quote.text[paragraph_start:paragraph_end])
        ]
        words = [(paragraph_start + start, paragraph_start + end) for start, end in words]
        if not words:
            continue

        units = words.copy()
        highlighted_word_indexes = [
            index
            for index, (start, end) in enumerate(words)
            if end > quote.highlight_start and start < quote.highlight_end
        ]
        if len(highlighted_word_indexes) > 1:
            first = highlighted_word_indexes[0]
            last = highlighted_word_indexes[-1]
            group = (words[first][0], words[last][1])
            if measure(*group) <= max_width:
                units = [*words[:first], group, *words[last + 1 :]]

        index = 0
        while index < len(units):
            line_start = units[index][0]
            best_index: int | None = None
            cursor = index
            while cursor < len(units):
                line_end = units[cursor][1]
                if measure(line_start, line_end) <= max_width:
                    best_index = cursor
                    cursor += 1
                else:
                    break
            if best_index is None:
                unit_end = units[index][1]
                split_end = _fit_character_end(quote.text, line_start, unit_end, max_width, measure)
                lines.append(
                    _RawLine(
                        line_start,
                        split_end,
                        measure(line_start, split_end),
                        paragraph_index,
                    )
                )
                units[index] = (split_end, unit_end)
                continue
            line_end = units[best_index][1]
            lines.append(
                _RawLine(
                    line_start,
                    line_end,
                    measure(line_start, line_end),
                    paragraph_index,
                )
            )
            index = best_index + 1
    return lines


def _segments_for_line(
    quote: RenderQuote,
    line: _RawLine,
    line_x: float,
    fonts: LoadedFonts,
    emphasis: EmphasisMetrics,
    justify_to_width: float | None = None,
    maximum_word_space: float | None = None,
) -> tuple[StyledSegment, ...]:
    def picturesque_shift(source_position: int) -> int:
        visible_index = sum(
            not character.isspace()
            for character in quote.text[quote.highlight_start : source_position]
        )
        rhythm = (0.86, 0.24, 0.62, 1.0, 0.38, 0.74, 0.14, 0.54, 0.92, 0.30, 0.68)
        return max(1, round(emphasis.baseline_shift * rhythm[visible_index % len(rhythm)]))

    def append_segment(
        segment_text: str,
        source_start: int,
        source_end: int,
        *,
        highlighted: bool,
        baseline_shift: int,
    ) -> None:
        nonlocal x
        font = fonts.bold if highlighted else fonts.regular
        width = text_width(font, segment_text)
        segments.append(
            StyledSegment(
                segment_text,
                x,
                width,
                highlighted,
                source_start,
                source_end,
                emphasis.font_size if highlighted else fonts.regular.size,
                baseline_shift,
                emphasis.stroke_width if highlighted else 0,
            )
        )
        x += width

    points = sorted(
        {
            line.start,
            line.end,
            max(line.start, min(line.end, quote.highlight_start)),
            max(line.start, min(line.end, quote.highlight_end)),
        }
    )
    segments: list[StyledSegment] = []
    x = line_x
    for start, end in zip(points, points[1:], strict=False):
        if start == end:
            continue
        highlighted = start >= quote.highlight_start and end <= quote.highlight_end
        segment_text = quote.text[start:end]
        if highlighted and emphasis.mode == TimeEmphasis.PICTURESQUE:
            segment_x = x + emphasis.stroke_width
            previous_advance = 0.0
            for offset, character in enumerate(segment_text):
                source_position = start + offset
                current_advance = text_width(fonts.bold, segment_text[: offset + 1])
                x = segment_x + previous_advance
                append_segment(
                    character,
                    source_position,
                    source_position + 1,
                    highlighted=True,
                    baseline_shift=(
                        0 if character.isspace() else picturesque_shift(source_position)
                    ),
                )
                x = segment_x + current_advance
                previous_advance = current_advance
            x += emphasis.stroke_width
        else:
            pieces = (
                tuple(re.finditer(r"\s+|\S+", segment_text)) if justify_to_width is not None else ()
            )
            if pieces:
                for piece in pieces:
                    append_segment(
                        piece.group(),
                        start + piece.start(),
                        start + piece.end(),
                        highlighted=highlighted,
                        baseline_shift=emphasis.baseline_shift if highlighted else 0,
                    )
            else:
                append_segment(
                    segment_text,
                    start,
                    end,
                    highlighted=highlighted,
                    baseline_shift=emphasis.baseline_shift if highlighted else 0,
                )

    if justify_to_width is None or maximum_word_space is None or justify_to_width <= line.width:
        return tuple(segments)
    gap_indexes = [
        index
        for index, segment in enumerate(segments)
        if segment.text.isspace() and 0 < index < len(segments) - 1
    ]
    if not gap_indexes:
        return tuple(segments)
    requested_extra = justify_to_width - line.width
    expansions = {index: 0.0 for index in gap_indexes}
    active = set(gap_indexes)
    remaining = requested_extra
    while active and remaining > 0.01:
        equal_share = remaining / len(active)
        saturated: list[int] = []
        for index in active:
            capacity = max(0.0, maximum_word_space - segments[index].width)
            if capacity <= equal_share:
                expansions[index] = capacity
                remaining -= capacity
                saturated.append(index)
        if saturated:
            active.difference_update(saturated)
            continue
        for index in active:
            expansions[index] = equal_share
        remaining = 0.0
    shifted: list[StyledSegment] = []
    accumulated = 0.0
    for index, segment in enumerate(segments):
        expansion = expansions.get(index, 0.0)
        expanded_width = segment.width + expansion
        shifted.append(replace(segment, x=segment.x + accumulated, width=expanded_width))
        accumulated += expansion
    return tuple(shifted)


def _wrap_single_font(text: str, font: ImageFont.FreeTypeFont, max_width: float) -> list[str]:
    words = list(re.finditer(r"\S+", text))
    if not words:
        return []
    result: list[str] = []
    index = 0
    while index < len(words):
        start = words[index].start()
        best: int | None = None
        cursor = index
        while cursor < len(words):
            end = words[cursor].end()
            if text_width(font, text[start:end]) <= max_width:
                best = cursor
                cursor += 1
            else:
                break
        if best is None:
            end = start + 1
            while end <= words[index].end() and text_width(font, text[start:end]) <= max_width:
                end += 1
            end = max(start + 1, end - 1)
            result.append(text[start:end])
            remainder = text[end : words[index].end()]
            if remainder:
                synthetic = re.match(r"\S+", remainder)
                assert synthetic is not None
                words[index] = _OffsetMatch(end, words[index].end())
            else:
                index += 1
            continue
        result.append(text[start : words[best].end()])
        index = best + 1
    return result


@dataclass(frozen=True, slots=True)
class _OffsetMatch:
    _start: int
    _end: int

    def start(self) -> int:
        return self._start

    def end(self) -> int:
        return self._end


def ellipsize_to_width(text: str, font: ImageFont.FreeTypeFont, max_width: float) -> str:
    """Ellipsize using rendered width, not a character-count estimate."""
    if text_width(font, text) <= max_width:
        return text
    ellipsis = "…"
    if text_width(font, ellipsis) > max_width:
        return ""
    low, high = 0, len(text)
    fitted = 0
    while low <= high:
        middle = (low + high) // 2
        candidate = text[:middle].rstrip() + ellipsis
        if text_width(font, candidate) <= max_width:
            fitted = middle
            low = middle + 1
        else:
            high = middle - 1
    head = text[:fitted].rstrip()
    if fitted < len(text) and fitted > 0 and not text[fitted - 1].isspace():
        word_boundary = head.rfind(" ")
        if word_boundary >= max(2, len(head) // 2):
            head = head[:word_boundary].rstrip()
    return head + ellipsis


def _wrap_limited(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: float,
    max_lines: int,
) -> list[str]:
    remaining = " ".join(text.split()).strip()
    lines: list[str] = []
    for line_index in range(max_lines):
        if not remaining:
            break
        if text_width(font, remaining) <= max_width:
            lines.append(remaining)
            break
        if line_index == max_lines - 1:
            lines.append(ellipsize_to_width(remaining, font, max_width))
            break
        words = remaining.split()
        fitted = ""
        consumed = 0
        for word in words:
            candidate = f"{fitted} {word}".strip()
            if text_width(font, candidate) > max_width:
                break
            fitted = candidate
            consumed += 1
        if not fitted:
            fitted = ellipsize_to_width(remaining, font, max_width)
            lines.append(fitted)
            break
        lines.append(fitted)
        remaining = " ".join(words[consumed:])
    return [line for line in lines if line]


def _fit_attribution(
    quote: RenderQuote,
    fonts: LoadedFonts,
    max_width: float,
    style: AttributionStyle,
) -> tuple[list[tuple[str, str, float]], str, str]:
    title = quote.display_title.strip()
    author = quote.display_author.strip()
    if not title and not author:
        raise LayoutError("quote has no displayable attribution", code="attribution")
    title_prefix = "— " if style == AttributionStyle.BOOK_AUTHOR or not author else ""
    author_prefix = "— " if style == AttributionStyle.AUTHOR_BOOK or not title else ""
    title_lines = (
        _wrap_limited(
            title_prefix + title,
            fonts.attribution_italic,
            max_width,
            2,
        )
        if title
        else []
    )
    author_lines = (
        _wrap_limited(
            author_prefix + author,
            fonts.attribution_regular,
            max_width,
            1,
        )
        if author
        else []
    )
    ordered = (
        [(line, "italic") for line in title_lines] + [(line, "regular") for line in author_lines]
        if style == AttributionStyle.BOOK_AUTHOR
        else [(line, "regular") for line in author_lines]
        + [(line, "italic") for line in title_lines]
    )
    if len(ordered) > 3:
        raise LayoutError("attribution exceeds three lines", code="attribution")
    specs = [
        (
            line,
            role,
            text_width(
                fonts.attribution_italic if role == "italic" else fonts.attribution_regular,
                line,
            ),
        )
        for line, role in ordered
    ]
    rendered_title = " ".join(title_lines).removeprefix("— ")
    rendered_author = " ".join(author_lines).removeprefix("— ")
    return specs, rendered_title, rendered_author


class LayoutEngine:
    """Fit a quote and attribution into a profile without clipping."""

    def __init__(self, font: FontSelection, time_font: FontSelection | None = None) -> None:
        self.font = font
        self.time_font = time_font

    def layout(
        self,
        quote: RenderQuote,
        profile: DeviceProfile,
        *,
        attribution_style: AttributionStyle = AttributionStyle.BOOK_AUTHOR,
        compact: bool = False,
        time_emphasis: TimeEmphasis = TimeEmphasis.SUBTLE_LIFT,
        date_text: str | None = None,
    ) -> LayoutResult:
        if quote.dirty_record_status.value != "CLEAN":
            raise LayoutError(
                f"quote {quote.quote_id} failed dirty-record validation: "
                f"{quote.dirty_record_status.value}",
                code="dirty",
            )
        usable_width = profile.width - 2 * profile.margin_x
        width_ratio = profile.compact_line_width if compact else profile.preferred_line_width
        max_width = min(usable_width, round(profile.width * width_ratio))
        quote_left = (profile.width - max_width) / 2
        available_height = profile.height - 2 * profile.margin_y
        attribution_shift = (
            round(profile.width * 0.035) if profile.orientation == "landscape" else 0
        )
        attribution_width = max_width - attribution_shift
        length_factor = max(0.78, min(1.38, (180 / max(40, len(quote.text))) ** 0.18))
        starting_size = max(7, round(profile.base_font_size * length_factor))
        single_face_accent = self.time_font is not None and not self.time_font.has_bold
        minimum_emphasis = time_emphasis_metrics(
            time_emphasis,
            profile.minimum_body_size,
            single_face_accent=single_face_accent,
        )
        minimum_probe = load_fonts(
            self.font,
            profile.minimum_body_size,
            profile.minimum_attribution_size,
            highlight_size=minimum_emphasis.font_size,
            time_selection=self.time_font,
            date_size=profile.minimum_date_size,
        )
        protected_highlight_start, protected_highlight_end = _protected_highlight_range(quote)
        highlight_can_fit_readably = (
            mixed_style_width(
                quote.text,
                protected_highlight_start,
                protected_highlight_end,
                quote.highlight_start,
                quote.highlight_end,
                minimum_probe.regular,
                minimum_probe.bold,
                highlight_stroke_width=minimum_emphasis.stroke_width,
            )
            <= max_width
        )

        chosen: (
            tuple[
                int,
                int,
                LoadedFonts,
                list[_RawLine],
                int,
                int,
                EmphasisMetrics,
            ]
            | None
        ) = None
        hard_candidate = None
        composition_too_tall = False
        date_collision_seen = False
        for body_size in range(starting_size, profile.minimum_body_size - 1, -1):
            attribution_size = max(
                profile.minimum_attribution_size,
                round(body_size * profile.attribution_scale),
            )
            emphasis = time_emphasis_metrics(
                time_emphasis,
                body_size,
                single_face_accent=single_face_accent,
            )
            fonts = load_fonts(
                self.font,
                body_size,
                attribution_size,
                highlight_size=emphasis.font_size,
                time_selection=self.time_font,
                date_size=date_font_size(profile, attribution_size),
            )
            raw_lines = _wrap_body(quote, fonts, max_width, emphasis)
            if not raw_lines:
                raise LayoutError("the quote contains no renderable text")
            spacing = profile.compact_line_spacing if compact else profile.normal_line_spacing
            body_line_height, baseline_offset = _styled_line_geometry(
                fonts,
                emphasis,
                spacing=spacing,
            )
            paragraph_transitions = sum(
                current.paragraph_index != previous.paragraph_index
                for previous, current in zip(raw_lines, raw_lines[1:], strict=False)
            )
            paragraph_gap = round(body_line_height * (0.24 if compact else 0.35))
            body_height = len(raw_lines) * body_line_height + paragraph_transitions * paragraph_gap
            if (
                body_height <= round(profile.height * profile.maximum_quote_region)
                and len(raw_lines) <= profile.hard_body_lines
            ):
                if (
                    highlight_can_fit_readably
                    and mixed_style_width(
                        quote.text,
                        protected_highlight_start,
                        protected_highlight_end,
                        quote.highlight_start,
                        quote.highlight_end,
                        fonts.regular,
                        fonts.bold,
                        highlight_stroke_width=emphasis.stroke_width,
                    )
                    > max_width
                ):
                    continue
                attr_specs, _, _ = _fit_attribution(
                    quote, fonts, attribution_width, attribution_style
                )
                attr_line_height = line_height(fonts.attribution_regular, spacing=1.12)
                attribution_height = len(attr_specs) * attr_line_height
                if len(attr_specs) > 1:
                    attribution_height += round(attr_line_height * 0.12)
                gap = max(
                    round(body_line_height * profile.attribution_gap_scale),
                    round(profile.height * 0.025),
                )
                if body_height + gap + attribution_height > available_height:
                    composition_too_tall = True
                    continue
                candidate_top = profile.margin_y + round(
                    (available_height - body_height - gap - attribution_height) * 0.46
                )
                if date_text:
                    candidate_right = quote_left
                    for line_index, raw_line in enumerate(raw_lines):
                        is_paragraph_end = (
                            line_index == len(raw_lines) - 1
                            or raw_lines[line_index + 1].paragraph_index != raw_line.paragraph_index
                        )
                        justify_to_width = (
                            max_width if profile.justify_body and not is_paragraph_end else None
                        )
                        segments = _segments_for_line(
                            quote,
                            raw_line,
                            quote_left,
                            fonts,
                            emphasis,
                            justify_to_width,
                            body_size * profile.maximum_word_space_scale,
                        )
                        candidate_right = max(
                            candidate_right,
                            *(
                                segment.x + segment.width + segment.stroke_width
                                for segment in segments
                            ),
                        )
                    candidate_body_bbox = Rectangle(
                        math.floor(quote_left),
                        math.floor(candidate_top),
                        math.ceil(candidate_right),
                        math.ceil(candidate_top + body_height),
                    )
                    date_x = round(profile.width * profile.date_inset_x)
                    date_y = round(profile.height * profile.date_inset_y)
                    candidate_date_bbox = Rectangle(
                        date_x,
                        date_y,
                        math.ceil(date_x + text_width(fonts.date_regular, date_text)),
                        date_y + line_height(fonts.date_regular, spacing=1.0),
                    )
                    if rectangles_intersect(candidate_date_bbox, candidate_body_bbox):
                        date_collision_seen = True
                        continue
                candidate = (
                    body_size,
                    attribution_size,
                    fonts,
                    raw_lines,
                    body_line_height,
                    baseline_offset,
                    emphasis,
                )
                if len(raw_lines) <= profile.soft_body_lines:
                    chosen = candidate
                    break
                hard_candidate = candidate
        if chosen is None:
            chosen = hard_candidate
        if chosen is None:
            if date_collision_seen:
                raise LayoutError(
                    f"quote {quote.quote_id} collides with the date at every readable size",
                    code="layout",
                )
            if composition_too_tall:
                raise LayoutError(
                    f"quote {quote.quote_id} and its attribution exceed the vertical safe "
                    f"region at the {profile.minimum_body_size}px minimum",
                    code="layout",
                )
            raise LayoutError(
                f"quote {quote.quote_id} exceeds {profile.hard_body_lines} lines at the "
                f"{profile.minimum_body_size}px minimum",
                code="too_long",
            )

        (
            body_size,
            attribution_size,
            fonts,
            raw_lines,
            body_line_height,
            baseline_offset,
            emphasis,
        ) = chosen
        attr_specs, rendered_title, rendered_author = _fit_attribution(
            quote, fonts, attribution_width, attribution_style
        )
        if profile.orientation == "landscape" and attr_specs:
            attribution_block_width = max(width for _, _, width in attr_specs)
            attribution_left = quote_left + max_width - attribution_shift - attribution_block_width
        else:
            attribution_left = quote_left
        attr_line_height = line_height(fonts.attribution_regular, spacing=1.12)
        gap = max(
            round(body_line_height * profile.attribution_gap_scale),
            round(profile.height * 0.025),
        )
        paragraph_gap = round(body_line_height * (0.24 if compact else 0.35))
        transitions = sum(
            current.paragraph_index != previous.paragraph_index
            for previous, current in zip(raw_lines, raw_lines[1:], strict=False)
        )
        body_height = len(raw_lines) * body_line_height + transitions * paragraph_gap
        attribution_height = len(attr_specs) * attr_line_height
        if len(attr_specs) > 1:
            attribution_height += round(attr_line_height * 0.12)
        total_height = body_height + gap + attribution_height
        if total_height > available_height:
            raise LayoutError("body and attribution exceed the vertical safe region")
        top = profile.margin_y + round((available_height - total_height) * 0.46)

        date_label = None
        if date_text:
            date_x = round(profile.width * profile.date_inset_x)
            date_y = round(profile.height * profile.date_inset_y)
            date_height = line_height(fonts.date_regular, spacing=1.0)
            date_label = DateLabel(
                text=date_text,
                x=float(date_x),
                y=float(date_y),
                width=text_width(fonts.date_regular, date_text),
                height=date_height,
                font_size=fonts.date_regular.size,
            )

        body_lines: list[BodyLine] = []
        y = float(top)
        previous_paragraph: int | None = None
        for line_index, raw_line in enumerate(raw_lines):
            if previous_paragraph is not None and raw_line.paragraph_index != previous_paragraph:
                y += paragraph_gap
            is_paragraph_end = (
                line_index == len(raw_lines) - 1
                or raw_lines[line_index + 1].paragraph_index != raw_line.paragraph_index
            )
            justify_to_width = max_width if profile.justify_body and not is_paragraph_end else None
            segments = _segments_for_line(
                quote,
                raw_line,
                quote_left,
                fonts,
                emphasis,
                justify_to_width,
                body_size * profile.maximum_word_space_scale,
            )
            rendered_width = (
                max(segment.x + segment.width + segment.stroke_width for segment in segments)
                - quote_left
            )
            body_lines.append(
                BodyLine(
                    raw_line.start,
                    raw_line.end,
                    quote_left,
                    y,
                    rendered_width,
                    body_line_height,
                    y + baseline_offset,
                    segments,
                    raw_line.paragraph_index,
                )
            )
            y += body_line_height
            previous_paragraph = raw_line.paragraph_index

        attribution_lines: list[AttributionLine] = []
        if attr_specs:
            y += gap
            for index, (line_text, role, width) in enumerate(attr_specs):
                if index == 1:
                    y += round(attr_line_height * 0.12)
                attribution_lines.append(
                    AttributionLine(
                        line_text,
                        attribution_left,
                        y,
                        width,
                        attr_line_height,
                        role,
                    )
                )
                y += attr_line_height

        body_right = math.ceil(max(line.x + line.width for line in body_lines))
        body_bottom = math.ceil(max(line.y + line.height for line in body_lines))
        body_bbox = Rectangle(math.floor(quote_left), top, body_right, body_bottom)
        if attribution_lines:
            attr_bbox = Rectangle(
                math.floor(attribution_left),
                math.floor(attribution_lines[0].y),
                math.ceil(max(line.x + line.width for line in attribution_lines)),
                math.ceil(attribution_lines[-1].y + attribution_lines[-1].height),
            )
        else:
            attr_bbox = Rectangle(
                math.floor(quote_left), body_bottom, math.floor(quote_left), body_bottom
            )

        highlight_lines = [
            line
            for line in body_lines
            if line.source_end > quote.highlight_start and line.source_start < quote.highlight_end
        ]
        highlight_width = mixed_style_width(
            quote.text,
            quote.highlight_start,
            quote.highlight_end,
            quote.highlight_start,
            quote.highlight_end,
            fonts.regular,
            fonts.bold,
            highlight_stroke_width=emphasis.stroke_width,
        )
        clipping = (
            body_bbox.left < profile.margin_x
            or body_bbox.right > profile.width - profile.margin_x
            or body_bbox.top < profile.margin_y
            or body_bbox.bottom > profile.height - profile.margin_y
            or attr_bbox.right > profile.width - profile.margin_x
            or attr_bbox.bottom > profile.height - profile.margin_y
        )
        collision = bool(attribution_lines and body_bbox.bottom > attribution_lines[0].y)
        date_bbox = (
            Rectangle(
                math.floor(date_label.x),
                math.floor(date_label.y),
                math.ceil(date_label.x + date_label.width),
                math.ceil(date_label.y + date_label.height),
            )
            if date_label
            else None
        )
        date_collision = bool(date_bbox and rectangles_intersect(date_bbox, body_bbox))
        if date_bbox:
            clipping = clipping or (
                date_bbox.left < 0
                or date_bbox.top < 0
                or date_bbox.right > profile.width
                or date_bbox.bottom > profile.height
            )
        if date_collision:
            raise LayoutError("date label collides with the quote body", code="layout")
        time_font = self.time_font or self.font
        diagnostics = LayoutDiagnostics(
            font_family=self.font.family,
            font_regular_path=str(self.font.regular),
            font_bold_path=str(self.font.bold or self.font.regular),
            font_italic_path=str(self.font.italic or self.font.regular),
            time_font_family=time_font.family,
            time_font_path=str(time_font.bold or time_font.regular),
            time_font_bold_face_available=time_font.has_bold,
            time_font_fallback_to_body=self.time_font is None,
            bold_face_available=self.font.has_bold,
            italic_face_available=self.font.has_italic,
            bold_italic_face_available=self.font.has_bold_italic,
            body_font_size=body_size,
            highlight_font_size=emphasis.font_size,
            highlight_scale=emphasis.scale,
            highlight_baseline_shift=emphasis.baseline_shift,
            highlight_stroke_width=emphasis.stroke_width,
            time_emphasis=time_emphasis.value,
            attribution_font_size=attribution_size,
            body_line_count=len(body_lines),
            body_bbox=body_bbox,
            attribution_bbox=attr_bbox,
            total_vertical_occupancy=total_height / profile.height,
            highlight_wrapped=len(highlight_lines) > 1,
            highlight_line_count=len(highlight_lines),
            clipping=clipping,
            body_below_minimum=body_size < profile.minimum_body_size,
            attribution_collision=collision,
            excessive_whitespace=total_height / profile.height < 0.16,
            pathological_highlight_wrap=len(highlight_lines) > 1 and highlight_width <= max_width,
            fallback_font=self.font.fallback_used,
            orientation=profile.orientation,
            canonical_quote_length=len(quote.canonical_quote),
            display_quote_length=len(quote.display_quote),
            full_vs_excerpt="excerpt" if quote.is_excerpt else "full",
            excerpt_start=quote.excerpt_start,
            excerpt_end=quote.excerpt_end,
            leading_ellipsis=quote.leading_ellipsis,
            trailing_ellipsis=quote.trailing_ellipsis,
            sentence_aligned=quote.sentence_aligned,
            title_simplified=quote.title_simplified or rendered_title != quote.canonical_title,
            author_simplified=quote.author_simplified or rendered_author != quote.canonical_author,
            anthology_mode=quote.anthology_mode,
            attribution_line_count=len(attribution_lines),
            dirty_record_status=quote.dirty_record_status.value,
            rendered_title=rendered_title,
            rendered_author=rendered_author,
            compact_layout=compact,
            quote_attribution_gap=gap,
            date_visible=date_label is not None,
            date_text=date_text or "",
            date_font_size=date_label.font_size if date_label else 0,
            date_bbox=date_bbox,
            date_collision=date_collision,
        )
        return LayoutResult(
            quote,
            tuple(body_lines),
            tuple(attribution_lines),
            diagnostics,
            date_label,
        )
