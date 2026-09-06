from __future__ import annotations

from datetime import date

import pytest
from test_render import render_quote

from litclock.render.date_label import (
    current_local_date,
    format_short_date,
    parse_date_override,
)
from litclock.render.layout import LayoutEngine, mixed_style_width
from litclock.render.models import RenderMode, TimeEmphasis
from litclock.render.pillow_renderer import PillowRenderer
from litclock.render.profiles import BUILTIN_PROFILES
from litclock.render.typography import (
    FontNotFoundError,
    discover_font,
    discover_time_font,
    load_fonts,
)


@pytest.fixture(scope="module")
def body_font():
    return discover_font()


@pytest.fixture(scope="module")
def sans_time_font():
    try:
        return discover_time_font(system_style="sans")
    except FontNotFoundError as error:
        pytest.skip(str(error))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (date(2026, 9, 5), "Sat, Sep 5"),
        (date(2027, 1, 3), "Sun, Jan 3"),
        (date(2028, 2, 29), "Tue, Feb 29"),
        (date(2026, 12, 31), "Thu, Dec 31"),
        (date(2027, 1, 1), "Fri, Jan 1"),
    ],
)
def test_short_date_format_is_deterministic(value: date, expected: str) -> None:
    assert format_short_date(value) == expected


def test_date_override_and_date_provider_are_isolated() -> None:
    assert parse_date_override("2026-09-05") == date(2026, 9, 5)
    assert current_local_date(lambda: date(2030, 4, 7)) == date(2030, 4, 7)
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        parse_date_override("Sep 5")


def test_pw4_landscape_date_defaults_on_and_can_be_disabled(body_font) -> None:
    renderer = PillowRenderer(body_font, date_provider=lambda: date(2026, 9, 5))
    profile = BUILTIN_PROFILES["pw4_landscape"]
    default_frame = renderer.render(render_quote(), profile)
    hidden_frame = renderer.render(render_quote(), profile, show_date=False)
    assert default_frame.diagnostics.date_visible
    assert default_frame.diagnostics.date_text == "Sat, Sep 5"
    assert not hidden_frame.diagnostics.date_visible
    assert hidden_frame.diagnostics.date_bbox is None


def test_landscape_date_is_quiet_and_does_not_collide(body_font) -> None:
    renderer = PillowRenderer(body_font)
    profile = BUILTIN_PROFILES["pw4_landscape"]
    frame = renderer.render(
        render_quote(),
        profile,
        display_date=date(2026, 9, 5),
        show_date=True,
    )
    diagnostics = frame.diagnostics
    assert diagnostics.date_bbox is not None
    assert 60 <= diagnostics.date_bbox.left <= 90
    assert 45 <= diagnostics.date_bbox.top <= 75
    assert diagnostics.date_bbox.bottom < diagnostics.body_bbox.top
    assert diagnostics.date_font_size < diagnostics.attribution_font_size
    assert not diagnostics.date_collision
    assert not diagnostics.clipping


def test_explicit_time_font_pair_loads_real_bold(body_font) -> None:
    accent = discover_time_font(regular_path=body_font.regular, bold_path=body_font.bold)
    assert accent.has_bold
    assert accent.bold == body_font.bold
    with pytest.raises(FontNotFoundError, match="missing: bold"):
        discover_time_font(regular_path=body_font.regular)


def test_invalid_time_font_fails_without_substitution(tmp_path) -> None:
    with pytest.raises(FontNotFoundError, match="does not exist"):
        discover_time_font(tmp_path / "missing-accent.ttf")


def test_no_accent_font_falls_back_to_body_bold(body_font) -> None:
    renderer = PillowRenderer(body_font)
    frame = renderer.render(
        render_quote(),
        BUILTIN_PROFILES["pw4_landscape"],
        show_date=False,
    )
    assert frame.diagnostics.time_font_family == body_font.family
    assert frame.diagnostics.time_font_path == str(body_font.bold)
    assert frame.diagnostics.time_font_fallback_to_body


def test_mixed_family_width_uses_actual_accent_face(body_font, sans_time_font) -> None:
    quote = render_quote(
        "It was nineteen minutes past four when she arrived.",
        "nineteen minutes past four",
    )
    engine = LayoutEngine(body_font, sans_time_font)
    layout = engine.layout(
        quote,
        BUILTIN_PROFILES["pw4_landscape"],
        time_emphasis=TimeEmphasis.SUBTLE_LIFT,
        date_text="Sat, Sep 5",
    )
    fonts = load_fonts(
        body_font,
        layout.diagnostics.body_font_size,
        layout.diagnostics.attribution_font_size,
        highlight_size=layout.diagnostics.highlight_font_size,
        time_selection=sans_time_font,
    )
    for line in layout.body_lines:
        natural_width = mixed_style_width(
            quote.text,
            line.source_start,
            line.source_end,
            quote.highlight_start,
            quote.highlight_end,
            fonts.regular,
            fonts.bold,
        )
        rendered_width = sum(segment.width for segment in line.segments)
        assert line.width == pytest.approx(rendered_width)
        assert line.width >= natural_width
    assert layout.diagnostics.time_font_family == sans_time_font.family
    assert layout.diagnostics.time_font_bold_face_available
    assert not layout.diagnostics.time_font_fallback_to_body


def test_mixed_family_baseline_and_long_phrase_wrap_safely(body_font, sans_time_font) -> None:
    phrase = "twenty-three minutes past four"
    quote = render_quote(
        f"After the unexpected silence, {phrase} arrived and the room stirred again.",
        phrase,
    )
    layout = LayoutEngine(body_font, sans_time_font).layout(
        quote,
        BUILTIN_PROFILES["pw4_landscape"],
        time_emphasis=TimeEmphasis.SUBTLE_LIFT,
        date_text="Sat, Sep 5",
    )
    highlighted = [
        segment for line in layout.body_lines for segment in line.segments if segment.highlighted
    ]
    assert "".join(segment.text for segment in highlighted) == phrase
    assert all(segment.baseline_shift > 0 for segment in highlighted)
    assert not layout.diagnostics.pathological_highlight_wrap
    assert not layout.diagnostics.clipping


def test_date_and_accent_survive_one_bit_output(body_font, sans_time_font) -> None:
    renderer = PillowRenderer(body_font, time_font=sans_time_font)
    frame = renderer.render(
        render_quote("At a quarter to eleven, the house grew still.", "a quarter to eleven"),
        BUILTIN_PROFILES["pw4_landscape"],
        mode=RenderMode.ONE_BIT,
        time_emphasis=TimeEmphasis.SUBTLE_LIFT,
        show_date=True,
        display_date=date(2026, 9, 5),
    )
    assert frame.image.mode == "1"
    assert frame.diagnostics.date_bbox is not None
    date_crop = frame.image.crop(
        (
            frame.diagnostics.date_bbox.left,
            frame.diagnostics.date_bbox.top,
            frame.diagnostics.date_bbox.right,
            frame.diagnostics.date_bbox.bottom,
        )
    )
    assert date_crop.getextrema() == (0, 255)
    assert not frame.diagnostics.clipping


def test_no_date_no_accent_retains_classic_contract(body_font) -> None:
    quote = render_quote()
    profile = BUILTIN_PROFILES["pw4_landscape"]
    direct = PillowRenderer(body_font).render(
        quote,
        profile,
        show_date=False,
        time_emphasis=TimeEmphasis.CLASSIC,
    )
    explicit = PillowRenderer(body_font, time_font=None).render(
        quote,
        profile,
        show_date=False,
        time_emphasis=TimeEmphasis.CLASSIC,
    )
    assert direct.image.tobytes() == explicit.image.tobytes()
    assert not direct.diagnostics.date_visible
    assert direct.diagnostics.time_font_fallback_to_body
