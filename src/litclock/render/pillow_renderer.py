"""Pillow bitmap backend for grayscale and crisp monochrome frames."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from PIL import Image, ImageDraw

from litclock.render.date_label import current_local_date, format_short_date
from litclock.render.layout import LayoutEngine
from litclock.render.models import (
    AttributionStyle,
    DitherMode,
    RenderabilityStatus,
    RenderedFrame,
    RenderMode,
    RenderQuote,
    TimeEmphasis,
)
from litclock.render.profiles import DeviceProfile
from litclock.render.typography import (
    FontSelection,
    discover_font,
    load_fonts,
    unsupported_glyphs,
)

QUOTE_GRAY = 72
HIGHLIGHT_BLACK = 0
ATTRIBUTION_GRAY = 112
DATE_GRAY = 144
BACKGROUND_WHITE = 255
ONE_BIT_THRESHOLD = 176


class PillowRenderer:
    def __init__(
        self,
        font: FontSelection | None = None,
        *,
        time_font: FontSelection | None = None,
        date_provider: Callable[[], date] = date.today,
    ) -> None:
        self.font = font or discover_font()
        self.time_font = time_font
        self.date_provider = date_provider

    def render(
        self,
        quote: RenderQuote,
        profile: DeviceProfile,
        *,
        mode: RenderMode = RenderMode.GRAYSCALE,
        dither: DitherMode = DitherMode.THRESHOLD,
        attribution_style: AttributionStyle = AttributionStyle.BOOK_AUTHOR,
        compact: bool | None = None,
        time_emphasis: TimeEmphasis = TimeEmphasis.SUBTLE_LIFT,
        show_date: bool | None = None,
        display_date: date | None = None,
    ) -> RenderedFrame:
        if compact is None:
            compact = quote.typography_metadata.get("layout") == "compact"
        date_visible = profile.show_date_by_default if show_date is None else show_date
        date_value = display_date or current_local_date(self.date_provider)
        date_text = format_short_date(date_value) if date_visible else None
        layout = LayoutEngine(self.font, self.time_font).layout(
            quote,
            profile,
            attribution_style=attribution_style,
            compact=compact,
            time_emphasis=time_emphasis,
            date_text=date_text,
        )
        diagnostics = layout.diagnostics
        diagnostics.render_mode = mode.value
        diagnostics.dither_mode = dither.value
        diagnostics.renderability_status = (
            RenderabilityStatus.DISPLAY_SAFE_EXCERPT.value
            if quote.is_excerpt
            else RenderabilityStatus.DISPLAY_SAFE_FULL.value
        )
        fonts = load_fonts(
            self.font,
            diagnostics.body_font_size,
            diagnostics.attribution_font_size,
            highlight_size=diagnostics.highlight_font_size,
            time_selection=self.time_font,
            date_size=diagnostics.date_font_size or diagnostics.attribution_font_size,
        )
        canvas = Image.new("L", (profile.width, profile.height), BACKGROUND_WHITE)
        draw = ImageDraw.Draw(canvas)
        for line in layout.body_lines:
            for segment in line.segments:
                font = fonts.bold if segment.highlighted else fonts.regular
                fill = HIGHLIGHT_BLACK if segment.highlighted else QUOTE_GRAY
                draw.text(
                    (segment.x, line.baseline - segment.baseline_shift),
                    segment.text,
                    font=font,
                    fill=fill,
                    anchor="ls",
                )
        for line in layout.attribution_lines:
            font = (
                fonts.attribution_italic
                if line.font_role == "italic"
                else fonts.attribution_regular
            )
            draw.text((line.x, line.y), line.text, font=font, fill=ATTRIBUTION_GRAY, anchor="lt")
        if layout.date_label:
            draw.text(
                (layout.date_label.x, layout.date_label.y),
                layout.date_label.text,
                font=fonts.date_regular,
                fill=DATE_GRAY,
                anchor="lt",
            )

        unsupported = set()
        unsupported.update(unsupported_glyphs(quote.text, fonts.regular))
        unsupported.update(unsupported_glyphs(quote.highlighted_time_text, fonts.bold))
        if layout.date_label:
            unsupported.update(unsupported_glyphs(layout.date_label.text, fonts.date_regular))
        for line in layout.attribution_lines:
            font = (
                fonts.attribution_italic
                if line.font_role == "italic"
                else fonts.attribution_regular
            )
            unsupported.update(unsupported_glyphs(line.text, font))
        diagnostics.unsupported_glyphs = sorted(unsupported, key=ord)

        if mode == RenderMode.GRAYSCALE:
            output = canvas
        elif dither == DitherMode.FLOYD_STEINBERG:
            output = canvas.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
        else:
            thresholded = canvas.point(
                lambda pixel: BACKGROUND_WHITE if pixel >= ONE_BIT_THRESHOLD else 0
            )
            output = thresholded.convert("1", dither=Image.Dither.NONE)
        return RenderedFrame(output, layout)
