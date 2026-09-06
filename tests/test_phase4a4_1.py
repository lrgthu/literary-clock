from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from test_render import render_quote

from litclock.cli import build_parser
from litclock.render.layout import LayoutEngine, rectangles_intersect
from litclock.render.models import DitherMode, RenderMode, TimeEmphasis
from litclock.render.production import (
    PW4_V1_RENDER_CONFIG,
    resolve_production_fonts,
)
from litclock.render.profiles import BUILTIN_PROFILES
from litclock.render.typography import FontNotFoundError, discover_font


def test_pw4_v1_contract_is_complete_and_frozen() -> None:
    config = PW4_V1_RENDER_CONFIG

    assert config.profile is BUILTIN_PROFILES["pw4_landscape"]
    assert (config.profile.width, config.profile.height) == (1448, 1072)
    assert config.profile.orientation == "landscape"
    assert config.show_date
    assert config.time_emphasis is TimeEmphasis.PICTURESQUE
    assert config.mode is RenderMode.ONE_BIT
    assert config.dither is DitherMode.THRESHOLD
    assert config.body_family == "Georgia"
    assert config.time_family == "Apple Chancery"
    assert config.time_font_environment == "LITCLOCK_TIME_FONT"
    assert not Path(config.time_font_environment).is_absolute()
    assert not config.standalone_clock
    assert not config.system_ui
    assert config.profile.minimum_date_size < config.profile.minimum_attribution_size


def test_no_font_binary_is_bundled_with_project_sources() -> None:
    project = Path(__file__).resolve().parents[1]
    searched_roots = (project / "src", project / "scripts", project / "kindle", project / "docs")
    bundled = [
        path
        for root in searched_roots
        for path in root.rglob("*")
        if path.suffix.casefold() in {".ttf", ".otf", ".ttc", ".woff", ".woff2"}
    ]

    assert bundled == []


def test_pw4_v1_missing_accent_fails_closed() -> None:
    body = discover_font()
    config = replace(
        PW4_V1_RENDER_CONFIG,
        body_family=body.family,
        time_family=body.family,
    )

    with pytest.raises(FontNotFoundError, match="LITCLOCK_TIME_FONT"):
        resolve_production_fonts(config, environment={}, body_font=body)


def test_pw4_v1_explicit_local_accent_is_verified() -> None:
    body = discover_font()
    config = replace(
        PW4_V1_RENDER_CONFIG,
        body_family=body.family,
        time_family=body.family,
    )

    resolved_body, accent = resolve_production_fonts(
        config,
        environment={"LITCLOCK_TIME_FONT": str(body.regular)},
        body_font=body,
    )

    assert resolved_body is body
    assert accent.regular == body.regular
    assert accent.family == body.family
    assert not accent.has_bold


def test_pw4_v1_rejects_wrong_accent_family() -> None:
    body = discover_font()
    config = replace(
        PW4_V1_RENDER_CONFIG,
        body_family=body.family,
        time_family="Definitely Not The Fixture Family",
    )

    with pytest.raises(FontNotFoundError, match="identifies as"):
        resolve_production_fonts(
            config,
            environment={"LITCLOCK_TIME_FONT": str(body.regular)},
            body_font=body,
        )


def test_production_command_enforces_contract_while_generic_defaults_remain() -> None:
    parser = build_parser()
    generic = parser.parse_args(["render", "16:37"])
    production = parser.parse_args(["render-pw4-v1", "16:37", "--preview"])

    assert generic.time_emphasis == TimeEmphasis.SUBTLE_LIFT.value
    assert generic.mode == RenderMode.GRAYSCALE.value
    assert generic.show_date is None
    assert production.device == "pw4"
    assert production.orientation == "landscape"
    assert production.show_date
    assert production.time_emphasis == TimeEmphasis.PICTURESQUE.value
    assert production.mode == RenderMode.ONE_BIT.value
    assert production.dither == DitherMode.THRESHOLD.value
    assert production.preview


def test_date_candidate_gate_uses_two_dimensional_collision() -> None:
    body = discover_font()
    quote = render_quote(
        "At four minutes past ten, the quiet room began to stir.",
        "four minutes past ten",
    )
    base = replace(
        BUILTIN_PROFILES["pw4_landscape"],
        preferred_line_width=0.50,
        compact_line_width=0.55,
        date_inset_x=0.01,
        show_date_by_default=False,
    )
    without_date = LayoutEngine(body).layout(quote, base)
    overlapping_y = (without_date.diagnostics.body_bbox.top + 1) / base.height
    profile = replace(base, date_inset_y=overlapping_y)

    with_date = LayoutEngine(body).layout(quote, profile, date_text="Sat, Sep 5")
    date_bbox = with_date.diagnostics.date_bbox
    body_bbox = with_date.diagnostics.body_bbox

    assert date_bbox is not None
    assert date_bbox.top < body_bbox.bottom and date_bbox.bottom > body_bbox.top
    assert date_bbox.right <= body_bbox.left
    assert not rectangles_intersect(date_bbox, body_bbox)
    assert not with_date.diagnostics.date_collision
    assert with_date.diagnostics.body_font_size == without_date.diagnostics.body_font_size
    assert with_date.diagnostics.date_font_size < with_date.diagnostics.attribution_font_size
