from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from test_render import render_quote

from litclock.render.layout import LayoutEngine, time_emphasis_metrics
from litclock.render.models import RenderMode, TimeEmphasis
from litclock.render.pillow_renderer import PillowRenderer
from litclock.render.profiles import BUILTIN_PROFILES
from litclock.render.typography import discover_font


@pytest.fixture(scope="module")
def renderer() -> PillowRenderer:
    return PillowRenderer(discover_font())


def _highlighted_segments(layout):
    return [
        segment for line in layout.body_lines for segment in line.segments if segment.highlighted
    ]


def test_pw4_attribution_is_larger_and_gap_is_tighter(renderer: PillowRenderer) -> None:
    quote = render_quote("At four minutes past ten, she arrived.", "four minutes past ten")
    current = BUILTIN_PROFILES["pw4_landscape"]
    old = replace(
        current,
        name="pw4-landscape-before-phase4a2",
        attribution_scale=0.43,
        attribution_gap_scale=0.72,
        minimum_attribution_scale=0.016,
    )
    old_layout = LayoutEngine(renderer.font).layout(
        quote,
        old,
        time_emphasis=TimeEmphasis.CLASSIC,
    )
    new_layout = LayoutEngine(renderer.font).layout(
        quote,
        current,
        time_emphasis=TimeEmphasis.CLASSIC,
    )
    assert new_layout.diagnostics.attribution_font_size >= round(
        old_layout.diagnostics.attribution_font_size * 1.15
    )
    assert (
        new_layout.diagnostics.quote_attribution_gap < old_layout.diagnostics.quote_attribution_gap
    )
    assert new_layout.diagnostics.attribution_line_count <= 3
    assert not new_layout.diagnostics.clipping


@pytest.mark.parametrize(
    ("mode", "scale", "shift_range"),
    [
        (TimeEmphasis.CLASSIC, 1.0, range(0, 1)),
        (TimeEmphasis.SUBTLE_LIFT, 1.10, range(2, 5)),
        (TimeEmphasis.EXPRESSIVE, 1.16, range(4, 7)),
    ],
)
def test_time_emphasis_metrics(mode: TimeEmphasis, scale: float, shift_range: range) -> None:
    metrics = time_emphasis_metrics(mode, 65)
    assert metrics.scale == pytest.approx(scale)
    assert metrics.font_size == round(65 * scale)
    assert metrics.baseline_shift in shift_range


def test_subtle_lift_width_and_baseline_are_part_of_layout(renderer: PillowRenderer) -> None:
    quote = render_quote(
        "It was four minutes past ten when the door opened.",
        "four minutes past ten",
    )
    engine = LayoutEngine(renderer.font)
    profile = BUILTIN_PROFILES["pw4_landscape"]
    classic = engine.layout(quote, profile, time_emphasis=TimeEmphasis.CLASSIC)
    lifted = engine.layout(quote, profile, time_emphasis=TimeEmphasis.SUBTLE_LIFT)
    classic_segment = _highlighted_segments(classic)[0]
    lifted_segment = _highlighted_segments(lifted)[0]
    assert classic.diagnostics.body_font_size == lifted.diagnostics.body_font_size
    assert lifted_segment.width > classic_segment.width
    assert lifted_segment.font_size == lifted.diagnostics.highlight_font_size
    assert lifted_segment.baseline_shift == lifted.diagnostics.highlight_baseline_shift
    assert lifted_segment.baseline_shift > 0
    assert lifted.body_lines[0].height > classic.body_lines[0].height
    assert not lifted.diagnostics.attribution_collision
    assert not lifted.diagnostics.clipping


def test_expressive_mode_is_stronger_but_restrained(renderer: PillowRenderer) -> None:
    quote = render_quote("At a quarter to eleven, he stopped.", "a quarter to eleven")
    profile = BUILTIN_PROFILES["pw4_landscape"]
    subtle = LayoutEngine(renderer.font).layout(
        quote,
        profile,
        time_emphasis=TimeEmphasis.SUBTLE_LIFT,
    )
    expressive = LayoutEngine(renderer.font).layout(
        quote,
        profile,
        time_emphasis=TimeEmphasis.EXPRESSIVE,
    )
    assert expressive.diagnostics.highlight_scale <= 1.18
    assert expressive.diagnostics.highlight_font_size > subtle.diagnostics.highlight_font_size
    assert (
        expressive.diagnostics.highlight_baseline_shift
        > subtle.diagnostics.highlight_baseline_shift
    )
    assert not expressive.diagnostics.clipping


@pytest.mark.parametrize(
    ("text", "phrase"),
    [
        ("Four minutes past ten came quietly at last.", "Four minutes past ten"),
        ("She arrived at four minutes past ten and waited.", "four minutes past ten"),
        ("The final bell sounded at four minutes past ten", "four minutes past ten"),
    ],
)
def test_lifted_phrase_at_line_start_middle_and_end(
    renderer: PillowRenderer,
    text: str,
    phrase: str,
) -> None:
    quote = render_quote(text, phrase)
    layout = LayoutEngine(renderer.font).layout(
        quote,
        BUILTIN_PROFILES["pw4_landscape"],
        time_emphasis=TimeEmphasis.SUBTLE_LIFT,
    )
    segments = _highlighted_segments(layout)
    assert "".join(segment.text for segment in segments) == phrase
    assert all(segment.baseline_shift > 0 for segment in segments)
    assert not layout.diagnostics.clipping


def test_long_lifted_phrase_stays_whole_when_it_fits(renderer: PillowRenderer) -> None:
    phrase = "twenty-three minutes past four"
    quote = render_quote(
        f"Beyond the gate, {phrase} arrived without warning.",
        phrase,
    )
    layout = LayoutEngine(renderer.font).layout(
        quote,
        BUILTIN_PROFILES["pw4_landscape"],
        time_emphasis=TimeEmphasis.SUBTLE_LIFT,
    )
    assert not layout.diagnostics.highlight_wrapped
    assert layout.diagnostics.highlight_line_count == 1
    assert not layout.diagnostics.pathological_highlight_wrap


def test_one_bit_subtle_lift_is_structurally_distinct(renderer: PillowRenderer) -> None:
    assert renderer.font.is_complete_family
    quote = render_quote("At four minutes past ten, she arrived.", "four minutes past ten")
    profile = BUILTIN_PROFILES["pw4_landscape"]
    classic = renderer.render(
        quote,
        profile,
        mode=RenderMode.ONE_BIT,
        time_emphasis=TimeEmphasis.CLASSIC,
    )
    lifted = renderer.render(
        quote,
        profile,
        mode=RenderMode.ONE_BIT,
        time_emphasis=TimeEmphasis.SUBTLE_LIFT,
    )
    assert classic.image.mode == lifted.image.mode == "1"
    assert classic.image.tobytes() != lifted.image.tobytes()
    assert lifted.diagnostics.bold_face_available
    assert lifted.diagnostics.highlight_font_size > lifted.diagnostics.body_font_size
    assert lifted.diagnostics.highlight_baseline_shift > 0


def test_pw4_png_reserves_a_clean_top_right_region(renderer: PillowRenderer) -> None:
    quote = render_quote("At four minutes past ten, she arrived.", "four minutes past ten")
    frame = renderer.render(
        quote,
        BUILTIN_PROFILES["pw4_landscape"],
        mode=RenderMode.ONE_BIT,
        time_emphasis=TimeEmphasis.SUBTLE_LIFT,
    )
    region = frame.image.crop((1448 - 300, 0, 1448, 120))
    assert region.getextrema() == (255, 255)
    assert not frame.diagnostics.clipping
    assert frame.diagnostics.attribution_line_count <= 3


def test_kindle_power_and_restore_helpers_are_explicit() -> None:
    project_root = Path(__file__).resolve().parents[1]
    power = project_root / "kindle" / "literary-clock-power.sh"
    display = project_root / "kindle" / "literary-clock-static-test.sh"
    subprocess.run(["sh", "-n", str(power)], check=True)
    subprocess.run(["sh", "-n", str(display)], check=True)
    power_text = power.read_text(encoding="utf-8")
    display_text = display.read_text(encoding="utf-8")
    assert "preventScreenSaver" in power_text
    assert 'lipc-set-prop "$service" "$property" "$original"' in power_text
    assert "disableScreenOff" not in power_text
    assert "deferSuspend" not in power_text
    assert "flIntensity" not in power_text
    assert "kill -CONT" in display_text
    assert '"$power_helper" off' in display_text
    assert 'sleep "$hold_seconds"' in display_text
