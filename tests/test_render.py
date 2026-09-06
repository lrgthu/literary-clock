from __future__ import annotations

import io
import random
from pathlib import Path

import pytest
from conftest import empty_database, insert_quote
from PIL import Image

from litclock.render.layout import LayoutEngine, mixed_style_width
from litclock.render.models import (
    AttributionStyle,
    DitherMode,
    RenderMode,
    RenderQuote,
    RenderValidationError,
)
from litclock.render.pillow_renderer import PillowRenderer
from litclock.render.profiles import BUILTIN_PROFILES, DeviceProfile, get_device_profile
from litclock.render.typography import FontNotFoundError, discover_font, load_fonts
from litclock.selector import QuoteSelector


def render_quote(
    text: str = "It was twenty-three minutes past four when the train arrived.",
    time_text: str = "twenty-three minutes past four",
    *,
    title: str = "The Example Book",
    author: str = "Élodie O’Connor",
) -> RenderQuote:
    start = text.index(time_text)
    return RenderQuote(
        quote_id=42,
        canonical_quote=text,
        display_quote=text,
        canonical_highlight_start=start,
        canonical_highlight_end=start + len(time_text),
        highlight_start=start,
        highlight_end=start + len(time_text),
        highlighted_time_text=time_text,
        canonical_title=title,
        display_title=title,
        canonical_author=author,
        display_author=author,
        display_minute=997,
        source_provenance_id="test:42",
        excerpt_end=len(text),
    )


@pytest.fixture(scope="module")
def renderer() -> PillowRenderer:
    return PillowRenderer(discover_font())


def test_render_quote_rejects_mismatched_highlight() -> None:
    with pytest.raises(RenderValidationError, match="do not match"):
        RenderQuote(
            quote_id=1,
            canonical_quote="At 4:37.",
            display_quote="At 4:37.",
            canonical_highlight_start=3,
            canonical_highlight_end=7,
            highlight_start=3,
            highlight_end=7,
            highlighted_time_text="5:37",
            canonical_title="Book",
            display_title="Book",
            canonical_author="Author",
            display_author="Author",
            display_minute=997,
        )


def test_exact_highlight_span_becomes_one_bold_segment(renderer: PillowRenderer) -> None:
    quote = render_quote()
    layout = LayoutEngine(renderer.font).layout(quote, BUILTIN_PROFILES["paperwhite-1-2"])
    highlighted = [
        segment for line in layout.body_lines for segment in line.segments if segment.highlighted
    ]
    assert "".join(segment.text for segment in highlighted) == quote.highlighted_time_text
    assert highlighted[0].source_start == quote.highlight_start
    assert highlighted[-1].source_end == quote.highlight_end


def test_mixed_style_width_uses_the_correct_font_for_each_span(
    renderer: PillowRenderer,
) -> None:
    fonts = load_fonts(renderer.font, 36, 16)
    text = "before four thirty-seven after"
    start = text.index("four")
    end = text.index(" after")
    actual = mixed_style_width(text, 0, len(text), start, end, fonts.regular, fonts.bold)
    expected = (
        fonts.regular.getlength(text[:start])
        + fonts.bold.getlength(text[start:end])
        + fonts.regular.getlength(text[end:])
    )
    assert actual == pytest.approx(expected)


def test_line_wrapping_preserves_all_non_whitespace_words(renderer: PillowRenderer) -> None:
    quote = render_quote(
        "Before the long journey it was twenty-three minutes past four and the station "
        "remained strangely quiet despite the crowd.",
        "twenty-three minutes past four",
    )
    profile = DeviceProfile("narrow", 360, 640)
    layout = LayoutEngine(renderer.font).layout(quote, profile)
    rendered_words = [
        word
        for line in layout.body_lines
        for word in quote.text[line.source_start : line.source_end].split()
    ]
    assert rendered_words == quote.text.split()
    assert len(layout.body_lines) > 1


def test_highlight_phrase_stays_on_one_line_when_it_fits(renderer: PillowRenderer) -> None:
    quote = render_quote(
        "A long preamble pushed the phrase toward the margin until twenty-three minutes past "
        "four brought everyone to attention.",
        "twenty-three minutes past four",
    )
    layout = LayoutEngine(renderer.font).layout(quote, DeviceProfile("test", 600, 800))
    assert not layout.diagnostics.highlight_wrapped
    assert not layout.diagnostics.pathological_highlight_wrap


def test_overwidth_highlight_wraps_without_losing_source_text(
    renderer: PillowRenderer,
) -> None:
    phrase = "twenty-three minutes past four " * 5
    quote = render_quote(f"It was {phrase}when it ended.", phrase)
    layout = LayoutEngine(renderer.font).layout(quote, DeviceProfile("test", 360, 640))
    highlighted_segments = [
        segment for line in layout.body_lines for segment in line.segments if segment.highlighted
    ]
    covered = "".join(
        quote.text[segment.source_start : segment.source_end] for segment in highlighted_segments
    )
    assert layout.diagnostics.highlight_wrapped
    assert covered.replace(" ", "") == phrase.replace(" ", "")


def test_adaptive_font_sizing_shrinks_long_quotes(renderer: PillowRenderer) -> None:
    short = render_quote("At four o’clock, she smiled.", "four o’clock")
    long_text = "At four o’clock, " + "the long road continued without interruption. " * 8
    long = render_quote(long_text, "four o’clock")
    profile = BUILTIN_PROFILES["kindle-1-4"]
    short_layout = LayoutEngine(renderer.font).layout(short, profile)
    long_layout = LayoutEngine(renderer.font).layout(long, profile)
    assert long_layout.diagnostics.body_font_size < short_layout.diagnostics.body_font_size
    assert not long_layout.diagnostics.clipping


def test_attribution_order_and_italic_role(renderer: PillowRenderer) -> None:
    quote = render_quote()
    engine = LayoutEngine(renderer.font)
    book_first = engine.layout(quote, BUILTIN_PROFILES["paperwhite-1-2"])
    author_first = engine.layout(
        quote,
        BUILTIN_PROFILES["paperwhite-1-2"],
        attribution_style=AttributionStyle.AUTHOR_BOOK,
    )
    assert book_first.attribution_lines[0].text.startswith("— The Example Book")
    assert book_first.attribution_lines[0].font_role == "italic"
    assert author_first.attribution_lines[0].text.startswith("— Élodie O’Connor")
    assert author_first.attribution_lines[-1].font_role == "italic"
    assert not book_first.diagnostics.attribution_collision


def test_unicode_punctuation_and_accents_render(renderer: PillowRenderer) -> None:
    quote = render_quote(
        "“At four o’clock—don’t wait…” Élise said.",
        "four o’clock",
        title="À la recherche",
        author="Élise D’Arcy",
    )
    frame = renderer.render(quote, BUILTIN_PROFILES["paperwhite-1-2"])
    assert frame.image.mode == "L"
    assert not frame.diagnostics.unsupported_glyphs


def test_custom_dimensions_override_profile() -> None:
    profile = get_device_profile("paperwhite", width=640, height=900)
    assert (profile.width, profile.height) == (640, 900)
    with pytest.raises(ValueError, match="together"):
        get_device_profile("paperwhite", width=640)


@pytest.mark.parametrize("profile", BUILTIN_PROFILES.values(), ids=BUILTIN_PROFILES.keys())
def test_every_builtin_profile_has_no_clipping(
    renderer: PillowRenderer, profile: DeviceProfile
) -> None:
    frame = renderer.render(render_quote(), profile)
    assert frame.image.size == (profile.width, profile.height)
    assert not frame.diagnostics.clipping


def test_grayscale_and_both_one_bit_paths(renderer: PillowRenderer) -> None:
    quote = render_quote()
    profile = BUILTIN_PROFILES["kindle-1-4"]
    grayscale = renderer.render(quote, profile, mode=RenderMode.GRAYSCALE)
    threshold = renderer.render(
        quote, profile, mode=RenderMode.ONE_BIT, dither=DitherMode.THRESHOLD
    )
    floyd = renderer.render(
        quote, profile, mode=RenderMode.ONE_BIT, dither=DitherMode.FLOYD_STEINBERG
    )
    assert grayscale.image.mode == "L"
    assert threshold.image.mode == "1"
    assert floyd.image.mode == "1"


def test_rendering_is_pixel_deterministic(renderer: PillowRenderer) -> None:
    quote = render_quote()
    profile = BUILTIN_PROFILES["kindle-1-4"]
    outputs = []
    for _ in range(2):
        frame = renderer.render(quote, profile, mode=RenderMode.ONE_BIT)
        buffer = io.BytesIO()
        frame.image.save(buffer, format="PNG")
        outputs.append(buffer.getvalue())
    assert outputs[0] == outputs[1]


def test_preview_does_not_mutate_selector_history(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "quotes.sqlite3")
    insert_quote(
        connection,
        quote="At 4:37 the train arrived.",
        title="Book",
        author="Author",
    )
    selector = QuoteSelector(connection, rng=random.Random(7))
    before_history = connection.execute("SELECT COUNT(*) FROM display_history").fetchone()[0]
    before_state = connection.execute("SELECT COUNT(*) FROM shuffle_state").fetchone()[0]
    quote = selector.preview("16:37")
    assert quote.id > 0
    history_after = connection.execute("SELECT COUNT(*) FROM display_history").fetchone()[0]
    assert history_after == before_history
    assert connection.execute("SELECT COUNT(*) FROM shuffle_state").fetchone()[0] == before_state
    connection.close()


def test_missing_font_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(FontNotFoundError, match="does not exist"):
        discover_font(tmp_path / "missing-serif.ttf")


def test_explicit_four_face_family(renderer: PillowRenderer) -> None:
    automatic = renderer.font
    explicit = discover_font(
        regular_path=automatic.regular,
        bold_path=automatic.bold,
        italic_path=automatic.italic,
        bold_italic_path=automatic.bold_italic,
    )
    assert explicit.is_complete_family
    assert explicit.regular == automatic.regular
    assert explicit.bold == automatic.bold
    assert explicit.italic == automatic.italic


def test_explicit_family_rejects_missing_bold(renderer: PillowRenderer) -> None:
    automatic = renderer.font
    with pytest.raises(FontNotFoundError, match="missing: bold"):
        discover_font(
            regular_path=automatic.regular,
            italic_path=automatic.italic,
            bold_italic_path=automatic.bold_italic,
        )


def test_explicit_family_rejects_missing_italic(renderer: PillowRenderer) -> None:
    automatic = renderer.font
    with pytest.raises(FontNotFoundError, match="missing: italic"):
        discover_font(
            regular_path=automatic.regular,
            bold_path=automatic.bold,
            bold_italic_path=automatic.bold_italic,
        )


def test_single_face_fallback_reports_unavailable_styles(renderer: PillowRenderer) -> None:
    selection = discover_font(renderer.font.regular)
    assert not selection.is_complete_family
    assert selection.bold is None
    assert selection.italic is None
    assert selection.bold_italic is None
    frame = PillowRenderer(selection).render(render_quote(), BUILTIN_PROFILES["pw4_landscape"])
    assert not frame.diagnostics.bold_face_available
    assert not frame.diagnostics.italic_face_available


def test_automatic_font_discovery_returns_complete_family() -> None:
    assert discover_font().is_complete_family


def test_paperwhite_generations_have_correct_separate_profiles() -> None:
    early = get_device_profile("paperwhite-1")
    pw2 = get_device_profile("paperwhite-2")
    pw3 = get_device_profile("paperwhite-3")
    legacy_alias = get_device_profile("paperwhite-1-3")
    assert early.name == pw2.name == "paperwhite-1-2"
    assert (early.width, early.height, early.pixel_density_ppi) == (758, 1024, 212)
    assert (pw3.width, pw3.height, pw3.pixel_density_ppi) == (1072, 1448, 300)
    assert legacy_alias == early


def test_missing_glyph_is_reported(renderer: PillowRenderer) -> None:
    quote = render_quote("At four o’clock, the symbol \U0010ffff appeared.", "four o’clock")
    frame = renderer.render(quote, BUILTIN_PROFILES["kindle-1-4"])
    assert "\U0010ffff" in frame.diagnostics.unsupported_glyphs


def test_rendered_frame_can_be_saved_as_png(renderer: PillowRenderer, tmp_path: Path) -> None:
    frame = renderer.render(render_quote(), BUILTIN_PROFILES["kindle-1-4"])
    output = tmp_path / "frame.png"
    frame.image.save(output)
    with Image.open(output) as reopened:
        assert reopened.size == (600, 800)
