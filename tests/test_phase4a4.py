from __future__ import annotations

from datetime import date

from test_render import render_quote

from litclock.render.layout import LayoutEngine, time_emphasis_metrics
from litclock.render.models import RenderMode, TimeEmphasis
from litclock.render.pillow_renderer import PillowRenderer
from litclock.render.profiles import BUILTIN_PROFILES
from litclock.render.typography import discover_font, discover_time_font


def test_pw4_final_composition_has_wider_margins_and_tighter_leading() -> None:
    profile = BUILTIN_PROFILES["pw4_landscape"]

    assert profile.margin_x == 152
    assert profile.preferred_line_width == 0.76
    assert profile.normal_line_spacing == 1.05
    assert profile.compact_line_spacing >= 1.0
    assert profile.justify_body
    assert profile.maximum_word_space_scale == 0.50


def test_pw4_landscape_attribution_is_a_right_side_coherent_block() -> None:
    body = discover_font()
    layout = LayoutEngine(body).layout(
        render_quote(),
        BUILTIN_PROFILES["pw4_landscape"],
        date_text="Sat, Sep 5",
    )

    line_starts = {line.x for line in layout.attribution_lines}
    assert len(line_starts) == 1
    assert layout.diagnostics.attribution_bbox.left > layout.diagnostics.body_bbox.left
    assert (
        layout.diagnostics.attribution_bbox.left + layout.diagnostics.attribution_bbox.width / 2
        > BUILTIN_PROFILES["pw4_landscape"].width / 2
    )
    assert layout.diagnostics.attribution_bbox.right <= (
        BUILTIN_PROFILES["pw4_landscape"].width - BUILTIN_PROFILES["pw4_landscape"].margin_x
    )


def test_pw4_uses_strictly_bounded_justification_on_nonfinal_lines() -> None:
    body = discover_font()
    phrase = "four minutes past ten"
    quote = render_quote(
        "It was four minutes past ten according to the clock, but as the latter was frequently "
        "wrong he didn’t accept the evidence as conclusive.",
        phrase,
    )
    profile = BUILTIN_PROFILES["pw4_landscape"]
    layout = LayoutEngine(body).layout(quote, profile, date_text="Sat, Sep 5")
    target_width = min(
        profile.width - 2 * profile.margin_x,
        round(profile.width * profile.preferred_line_width),
    )

    assert len(layout.body_lines) > 1
    maximum_word_space = layout.diagnostics.body_font_size * profile.maximum_word_space_scale
    expanded_spaces = [
        segment
        for line in layout.body_lines[:-1]
        for segment in line.segments
        if segment.text.isspace()
    ]
    assert all(line.width <= target_width for line in layout.body_lines[:-1])
    assert any(line.width < target_width for line in layout.body_lines[:-1])
    assert any(line.width == target_width for line in layout.body_lines[:-1])
    assert expanded_spaces
    assert all(segment.width <= maximum_word_space for segment in expanded_spaces)
    assert layout.body_lines[-1].width < target_width
    assert not layout.diagnostics.clipping


def test_picturesque_mode_uses_gentle_character_level_baseline_rhythm() -> None:
    body = discover_font()
    accent = discover_time_font(body.regular)
    phrase = "four minutes past ten"
    quote = render_quote(f"It was {phrase} when she arrived.", phrase)
    layout = LayoutEngine(body, accent).layout(
        quote,
        BUILTIN_PROFILES["pw4_landscape"],
        time_emphasis=TimeEmphasis.PICTURESQUE,
        date_text="Sat, Sep 5",
    )
    highlighted = [
        segment for line in layout.body_lines for segment in line.segments if segment.highlighted
    ]
    visible = [segment for segment in highlighted if not segment.text.isspace()]
    shifts = {segment.baseline_shift for segment in visible}

    assert "".join(segment.text for segment in highlighted) == phrase
    assert len(visible) == sum(not character.isspace() for character in phrase)
    assert len(shifts) >= 4
    assert min(shifts) >= 1
    assert max(shifts) == layout.diagnostics.highlight_baseline_shift
    assert layout.diagnostics.highlight_scale == 1.10
    assert layout.diagnostics.highlight_stroke_width == 2
    assert all(segment.stroke_width == 2 for segment in visible)
    assert not layout.diagnostics.clipping
    assert not layout.diagnostics.pathological_highlight_wrap


def test_picturesque_mode_and_larger_date_survive_one_bit() -> None:
    body = discover_font()
    accent = discover_time_font(body.regular)
    renderer = PillowRenderer(body, time_font=accent)
    frame = renderer.render(
        render_quote("At four minutes past ten, the door opened.", "four minutes past ten"),
        BUILTIN_PROFILES["pw4_landscape"],
        mode=RenderMode.ONE_BIT,
        time_emphasis=TimeEmphasis.PICTURESQUE,
        display_date=date(2026, 9, 5),
    )

    assert frame.image.mode == "1"
    assert frame.diagnostics.date_font_size >= 31
    assert frame.diagnostics.date_font_size < frame.diagnostics.attribution_font_size
    assert not frame.diagnostics.date_collision
    assert not frame.diagnostics.clipping


def test_picturesque_metrics_keep_existing_size_lift_but_allow_more_vertical_rhythm() -> None:
    subtle = time_emphasis_metrics(TimeEmphasis.SUBTLE_LIFT, 65)
    picturesque = time_emphasis_metrics(
        TimeEmphasis.PICTURESQUE,
        65,
        single_face_accent=True,
    )

    assert picturesque.font_size == subtle.font_size == 72
    assert picturesque.baseline_shift > subtle.baseline_shift
    assert picturesque.baseline_shift <= 11
    assert picturesque.stroke_width == 2
