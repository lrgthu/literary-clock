"""Proportional e-reader device profiles."""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    name: str
    width: int
    height: int
    orientation: str = "portrait"
    safe_margin_x: float = 0.085
    safe_margin_y: float = 0.075
    base_font_scale: float = 0.053
    attribution_scale: float = 0.44
    minimum_body_scale: float = 0.025
    minimum_attribution_scale: float = 0.016
    maximum_quote_region: float = 0.68
    preferred_line_width: float = 0.80
    pixel_density_ppi: int | None = None
    preferred_body_lines: int = 7
    soft_body_lines: int = 8
    hard_body_lines: int = 10
    attribution_max_lines: int = 3
    compact_line_width: float = 0.86
    normal_line_spacing: float = 1.16
    compact_line_spacing: float = 1.08

    def __post_init__(self) -> None:
        if self.width < 200 or self.height < 200:
            raise ValueError("device dimensions must both be at least 200 pixels")
        if self.orientation not in {"portrait", "landscape"}:
            raise ValueError("orientation must be portrait or landscape")
        for value in (
            self.safe_margin_x,
            self.safe_margin_y,
            self.base_font_scale,
            self.attribution_scale,
            self.minimum_body_scale,
            self.minimum_attribution_scale,
            self.maximum_quote_region,
            self.preferred_line_width,
            self.compact_line_width,
            self.normal_line_spacing,
            self.compact_line_spacing,
        ):
            if not 0 < value < 2:
                raise ValueError("profile scales must be between zero and two")
        if not (1 <= self.preferred_body_lines <= self.soft_body_lines <= self.hard_body_lines):
            raise ValueError("body line limits must be monotonically increasing")

    @property
    def margin_x(self) -> int:
        return round(self.width * self.safe_margin_x)

    @property
    def margin_y(self) -> int:
        return round(self.height * self.safe_margin_y)

    @property
    def base_font_size(self) -> int:
        return max(8, round(self.width * self.base_font_scale))

    @property
    def minimum_body_size(self) -> int:
        return max(12, round(self.width * self.minimum_body_scale))

    @property
    def minimum_attribution_size(self) -> int:
        return max(10, round(self.width * self.minimum_attribution_scale))


BUILTIN_PROFILES: dict[str, DeviceProfile] = {
    "kindle-1-4": DeviceProfile("kindle-1-4", 600, 800, pixel_density_ppi=167),
    "paperwhite-1-3": DeviceProfile("paperwhite-1-3", 758, 1024, pixel_density_ppi=212),
    "kindle-basic-11": DeviceProfile("kindle-basic-11", 1072, 1448, pixel_density_ppi=300),
    "paperwhite-5": DeviceProfile("paperwhite-5", 1236, 1648, pixel_density_ppi=300),
    "oasis": DeviceProfile("oasis", 1264, 1680, pixel_density_ppi=300),
    "pw4_portrait": DeviceProfile(
        "pw4_portrait",
        1072,
        1448,
        orientation="portrait",
        pixel_density_ppi=300,
        minimum_body_scale=0.027,
    ),
    "pw4_landscape": DeviceProfile(
        "pw4_landscape",
        1448,
        1072,
        orientation="landscape",
        safe_margin_x=0.085,
        safe_margin_y=0.09,
        base_font_scale=0.043,
        attribution_scale=0.43,
        minimum_body_scale=0.026,
        minimum_attribution_scale=0.016,
        maximum_quote_region=0.69,
        preferred_line_width=0.81,
        compact_line_width=0.86,
        pixel_density_ppi=300,
    ),
}

PROFILE_ALIASES = {
    "kindle": "kindle-1-4",
    "kindle-1": "kindle-1-4",
    "kindle-2": "kindle-1-4",
    "kindle-3": "kindle-1-4",
    "kindle-4": "kindle-1-4",
    "paperwhite": "paperwhite-5",
    "paperwhite-11": "paperwhite-5",
    "paperwhite-1": "paperwhite-1-3",
    "paperwhite-2": "paperwhite-1-3",
    "paperwhite-3": "paperwhite-1-3",
    "basic-11": "kindle-basic-11",
    "pw4-portrait": "pw4_portrait",
    "pw4-landscape": "pw4_landscape",
}


def get_device_profile(
    name: str = "pw4",
    *,
    width: int | None = None,
    height: int | None = None,
    orientation: str | None = None,
) -> DeviceProfile:
    if (width is None) != (height is None):
        raise ValueError("--width and --height must be supplied together")
    if orientation is not None and orientation not in {"portrait", "landscape"}:
        raise ValueError("orientation must be portrait or landscape")
    if name == "pw4":
        canonical = f"pw4_{orientation or 'landscape'}"
    else:
        canonical = PROFILE_ALIASES.get(name, name)
    if canonical == "custom":
        if width is None or height is None:
            raise ValueError("the custom profile requires --width and --height")
        inferred_orientation = orientation or ("landscape" if width > height else "portrait")
        return DeviceProfile("custom", width, height, orientation=inferred_orientation)
    try:
        profile = BUILTIN_PROFILES[canonical]
    except KeyError as error:
        choices = ", ".join((*BUILTIN_PROFILES, "custom"))
        raise ValueError(f"unknown device profile {name!r}; choose one of: {choices}") from error
    if width is not None and height is not None:
        inferred_orientation = orientation or ("landscape" if width > height else "portrait")
        return replace(
            profile,
            name=f"custom-{width}x{height}",
            width=width,
            height=height,
            orientation=inferred_orientation,
        )
    return profile
