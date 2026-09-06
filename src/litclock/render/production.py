"""Fail-closed rendering contract for the physically approved PW4 V1 frame."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from litclock.render.models import AttributionStyle, DitherMode, RenderMode, TimeEmphasis
from litclock.render.profiles import DeviceProfile, get_device_profile
from litclock.render.typography import (
    FontNotFoundError,
    FontSelection,
    discover_font_family,
    discover_time_font,
)


@dataclass(frozen=True, slots=True)
class ProductionRenderConfig:
    """A complete, named production contract rather than a bag of CLI defaults."""

    name: str
    profile_name: str
    body_family: str
    time_family: str
    time_font_environment: str
    mode: RenderMode
    dither: DitherMode
    attribution_style: AttributionStyle
    time_emphasis: TimeEmphasis
    show_date: bool
    standalone_clock: bool
    system_ui: bool

    @property
    def profile(self) -> DeviceProfile:
        return get_device_profile(self.profile_name)


PW4_V1_RENDER_CONFIG = ProductionRenderConfig(
    name="pw4-v1",
    profile_name="pw4_landscape",
    body_family="Georgia",
    time_family="Apple Chancery",
    time_font_environment="LITCLOCK_TIME_FONT",
    mode=RenderMode.ONE_BIT,
    dither=DitherMode.THRESHOLD,
    attribution_style=AttributionStyle.BOOK_AUTHOR,
    time_emphasis=TimeEmphasis.PICTURESQUE,
    show_date=True,
    standalone_clock=False,
    system_ui=False,
)


def resolve_production_fonts(
    config: ProductionRenderConfig = PW4_V1_RENDER_CONFIG,
    *,
    environment: Mapping[str, str] | None = None,
    body_font: FontSelection | None = None,
) -> tuple[FontSelection, FontSelection]:
    """Resolve the exact local production faces, failing rather than downgrading.

    ``body_font`` is dependency injection for tests; the production CLI always uses
    stable family discovery and never embeds an absolute machine path.
    """
    body = body_font or discover_font_family(config.body_family)
    if body.family.casefold() != config.body_family.casefold():
        raise FontNotFoundError(
            f"{config.name} requires body family {config.body_family!r}, found {body.family!r}"
        )
    if not body.is_complete_family:
        raise FontNotFoundError(
            f"{config.name} requires regular, bold, italic, and bold-italic "
            f"{config.body_family} faces"
        )
    values = os.environ if environment is None else environment
    configured = values.get(config.time_font_environment)
    if not configured:
        raise FontNotFoundError(
            f"{config.name} requires local accent family {config.time_family!r}; set "
            f"{config.time_font_environment} to its font file"
        )
    accent = discover_time_font(configured)
    if accent.family.casefold() != config.time_family.casefold():
        raise FontNotFoundError(
            f"{config.name} requires accent family {config.time_family!r}, "
            f"but {config.time_font_environment} identifies as {accent.family!r}"
        )
    return body, accent
