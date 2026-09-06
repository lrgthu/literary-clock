"""Pillow bitmap backend for grayscale and crisp monochrome frames."""

from __future__ import annotations

from PIL import Image, ImageDraw

from litclock.render.layout import LayoutEngine
from litclock.render.models import (
    AttributionStyle,
    DitherMode,
    RenderabilityStatus,
    RenderedFrame,
    RenderMode,
    RenderQuote,
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
BACKGROUND_WHITE = 255
ONE_BIT_THRESHOLD = 176


class PillowRenderer:
    def __init__(self, font: FontSelection | None = None) -> None:
        self.font = font or discover_font()

    def render(
        self,
        quote: RenderQuote,
        profile: DeviceProfile,
        *,
        mode: RenderMode = RenderMode.GRAYSCALE,
        dither: DitherMode = DitherMode.THRESHOLD,
        attribution_style: AttributionStyle = AttributionStyle.BOOK_AUTHOR,
        compact: bool | None = None,
    ) -> RenderedFrame:
        if compact is None:
            compact = quote.typography_metadata.get("layout") == "compact"
        layout = LayoutEngine(self.font).layout(
            quote,
            profile,
            attribution_style=attribution_style,
            compact=compact,
        )
        diagnostics = layout.diagnostics
        diagnostics.render_mode = mode.value
        diagnostics.dither_mode = dither.value
        diagnostics.renderability_status = (
            RenderabilityStatus.DISPLAY_SAFE_EXCERPT.value
            if quote.is_excerpt
            else RenderabilityStatus.DISPLAY_SAFE_FULL.value
        )
        fonts = load_fonts(self.font, diagnostics.body_font_size, diagnostics.attribution_font_size)
        canvas = Image.new("L", (profile.width, profile.height), BACKGROUND_WHITE)
        draw = ImageDraw.Draw(canvas)
        for line in layout.body_lines:
            for segment in line.segments:
                font = fonts.bold if segment.highlighted else fonts.regular
                fill = HIGHLIGHT_BLACK if segment.highlighted else QUOTE_GRAY
                draw.text((segment.x, line.y), segment.text, font=font, fill=fill, anchor="lt")
        for line in layout.attribution_lines:
            font = (
                fonts.attribution_italic
                if line.font_role == "italic"
                else fonts.attribution_regular
            )
            draw.text((line.x, line.y), line.text, font=font, fill=ATTRIBUTION_GRAY, anchor="lt")

        unsupported = set()
        unsupported.update(unsupported_glyphs(quote.text, fonts.regular))
        unsupported.update(unsupported_glyphs(quote.highlighted_time_text, fonts.bold))
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
