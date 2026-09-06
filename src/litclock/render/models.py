"""Render-layer contracts with no corpus-selection behavior."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from litclock.models import Quote


class RenderValidationError(ValueError):
    """Raised before drawing when a presentation contract is unsafe."""


class RenderMode(StrEnum):
    GRAYSCALE = "grayscale"
    ONE_BIT = "1bit"


class DitherMode(StrEnum):
    THRESHOLD = "threshold"
    FLOYD_STEINBERG = "floyd-steinberg"


class AttributionStyle(StrEnum):
    BOOK_AUTHOR = "book-author"
    AUTHOR_BOOK = "author-book"


class DirtyRecordStatus(StrEnum):
    CLEAN = "CLEAN"
    DIRTY_SERIALIZED_RECORD = "DIRTY_SERIALIZED_RECORD"
    MULTI_RECORD_CONCATENATION = "MULTI_RECORD_CONCATENATION"
    EXCERPT_CORRUPTION = "EXCERPT_CORRUPTION"


class RenderabilityStatus(StrEnum):
    DISPLAY_SAFE_FULL = "DISPLAY_SAFE_FULL"
    DISPLAY_SAFE_EXCERPT = "DISPLAY_SAFE_EXCERPT"
    REJECT_DIRTY = "REJECT_DIRTY"
    REJECT_TOO_LONG = "REJECT_TOO_LONG"
    REJECT_ATTRIBUTION = "REJECT_ATTRIBUTION"
    REJECT_LAYOUT = "REJECT_LAYOUT"


@dataclass(frozen=True, slots=True)
class RenderQuote:
    quote_id: int
    canonical_quote: str
    display_quote: str
    canonical_highlight_start: int
    canonical_highlight_end: int
    highlight_start: int
    highlight_end: int
    highlighted_time_text: str
    canonical_title: str
    display_title: str
    canonical_author: str
    display_author: str
    display_minute: int
    source_provenance_id: str | None = None
    excerpt_start: int = 0
    excerpt_end: int = 0
    leading_ellipsis: bool = False
    trailing_ellipsis: bool = False
    sentence_aligned: bool = True
    title_simplified: bool = False
    author_simplified: bool = False
    anthology_mode: bool = False
    dirty_record_status: DirtyRecordStatus = DirtyRecordStatus.CLEAN
    typography_metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0 <= self.display_minute <= 1439:
            raise RenderValidationError(f"display minute is outside 0..1439: {self.display_minute}")
        if not (
            0
            <= self.canonical_highlight_start
            < self.canonical_highlight_end
            <= len(self.canonical_quote)
        ):
            raise RenderValidationError("canonical highlight offsets are outside the quote")
        canonical_highlight = self.canonical_quote[
            self.canonical_highlight_start : self.canonical_highlight_end
        ]
        if canonical_highlight != self.highlighted_time_text:
            raise RenderValidationError("canonical highlight offsets do not match the time text")
        if not 0 <= self.highlight_start < self.highlight_end <= len(self.display_quote):
            raise RenderValidationError("display highlight offsets are outside the quote")
        actual = self.display_quote[self.highlight_start : self.highlight_end]
        if actual != self.highlighted_time_text:
            raise RenderValidationError(
                "highlight offsets do not match highlighted_time_text: "
                f"expected {self.highlighted_time_text!r}, found {actual!r}"
            )
        if self.excerpt_end == 0:
            object.__setattr__(self, "excerpt_end", len(self.canonical_quote))
        if not 0 <= self.excerpt_start < self.excerpt_end <= len(self.canonical_quote):
            raise RenderValidationError("excerpt offsets are outside the canonical quote")

    @classmethod
    def from_quote(cls, quote: Quote) -> RenderQuote:
        from litclock.render.presentation import prepare_full_quote

        return prepare_full_quote(quote)

    @property
    def text(self) -> str:
        return self.display_quote

    @property
    def title(self) -> str:
        return self.display_title

    @property
    def author(self) -> str:
        return self.display_author

    @property
    def is_excerpt(self) -> bool:
        return self.excerpt_start > 0 or self.excerpt_end < len(self.canonical_quote)

    @property
    def display_time(self) -> str:
        return f"{self.display_minute // 60:02d}:{self.display_minute % 60:02d}"


@dataclass(frozen=True, slots=True)
class Rectangle:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True, slots=True)
class StyledSegment:
    text: str
    x: float
    width: float
    highlighted: bool
    source_start: int
    source_end: int


@dataclass(frozen=True, slots=True)
class BodyLine:
    source_start: int
    source_end: int
    x: float
    y: float
    width: float
    height: int
    segments: tuple[StyledSegment, ...]
    paragraph_index: int


@dataclass(frozen=True, slots=True)
class AttributionLine:
    text: str
    x: float
    y: float
    width: float
    height: int
    font_role: str


@dataclass(slots=True)
class LayoutDiagnostics:
    font_family: str
    font_regular_path: str
    font_bold_path: str
    font_italic_path: str
    bold_face_available: bool
    italic_face_available: bool
    bold_italic_face_available: bool
    body_font_size: int
    attribution_font_size: int
    body_line_count: int
    body_bbox: Rectangle
    attribution_bbox: Rectangle
    total_vertical_occupancy: float
    highlight_wrapped: bool
    highlight_line_count: int
    render_mode: str = "grayscale"
    dither_mode: str = "threshold"
    clipping: bool = False
    body_below_minimum: bool = False
    attribution_collision: bool = False
    excessive_whitespace: bool = False
    pathological_highlight_wrap: bool = False
    fallback_font: bool = False
    unsupported_glyphs: list[str] = field(default_factory=list)
    orientation: str = "portrait"
    canonical_quote_length: int = 0
    display_quote_length: int = 0
    full_vs_excerpt: str = "full"
    excerpt_start: int = 0
    excerpt_end: int = 0
    leading_ellipsis: bool = False
    trailing_ellipsis: bool = False
    sentence_aligned: bool = True
    title_simplified: bool = False
    author_simplified: bool = False
    anthology_mode: bool = False
    attribution_line_count: int = 0
    dirty_record_status: str = DirtyRecordStatus.CLEAN.value
    renderability_status: str = RenderabilityStatus.DISPLAY_SAFE_FULL.value
    rendered_title: str = ""
    rendered_author: str = ""
    compact_layout: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LayoutResult:
    quote: RenderQuote
    body_lines: tuple[BodyLine, ...]
    attribution_lines: tuple[AttributionLine, ...]
    diagnostics: LayoutDiagnostics


@dataclass(frozen=True, slots=True)
class RenderedFrame:
    image: Any
    layout: LayoutResult

    @property
    def diagnostics(self) -> LayoutDiagnostics:
        return self.layout.diagnostics


@dataclass(frozen=True, slots=True)
class RenderabilityResult:
    status: RenderabilityStatus
    quote: RenderQuote | None
    reason: str | None = None
    compact_layout: bool = False
    diagnostics: LayoutDiagnostics | None = None
