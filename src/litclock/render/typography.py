"""System font discovery, loading, metrics, and glyph diagnostics."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from PIL import ImageFont


class FontNotFoundError(FileNotFoundError):
    pass


@dataclass(frozen=True, slots=True)
class FontSelection:
    family: str
    regular: Path
    bold: Path
    italic: Path
    bold_italic: Path
    fallback_used: bool = False


@dataclass(frozen=True, slots=True)
class LoadedFonts:
    regular: ImageFont.FreeTypeFont
    bold: ImageFont.FreeTypeFont
    italic: ImageFont.FreeTypeFont
    bold_italic: ImageFont.FreeTypeFont
    attribution_regular: ImageFont.FreeTypeFont
    attribution_italic: ImageFont.FreeTypeFont


_SYSTEM_FAMILIES = (
    (
        "EB Garamond",
        "EBGaramond-Regular.ttf",
        "EBGaramond-Bold.ttf",
        "EBGaramond-Italic.ttf",
        "EBGaramond-BoldItalic.ttf",
    ),
    (
        "Linux Libertine",
        "LinLibertine_R.ttf",
        "LinLibertine_RB.ttf",
        "LinLibertine_RI.ttf",
        "LinLibertine_RBI.ttf",
    ),
    (
        "Georgia",
        "Georgia.ttf",
        "Georgia Bold.ttf",
        "Georgia Italic.ttf",
        "Georgia Bold Italic.ttf",
    ),
    (
        "DejaVu Serif",
        "DejaVuSerif.ttf",
        "DejaVuSerif-Bold.ttf",
        "DejaVuSerif-Italic.ttf",
        "DejaVuSerif-BoldItalic.ttf",
    ),
    (
        "Liberation Serif",
        "LiberationSerif-Regular.ttf",
        "LiberationSerif-Bold.ttf",
        "LiberationSerif-Italic.ttf",
        "LiberationSerif-BoldItalic.ttf",
    ),
)


def _font_roots() -> tuple[Path, ...]:
    home = Path.home()
    return (
        home / ".local" / "share" / "fonts",
        home / "Library" / "Fonts",
        Path("/Library/Fonts"),
        Path("/System/Library/Fonts"),
        Path("/System/Library/Fonts/Supplemental"),
        Path("/usr/local/share/fonts"),
        Path("/usr/share/fonts"),
    )


def _find_filename(filename: str) -> Path | None:
    for root in _font_roots():
        if not root.exists():
            continue
        direct = root / filename
        if direct.is_file():
            return direct
        matches = sorted(root.rglob(filename))
        if matches:
            return matches[0]
    return None


def discover_font(explicit_path: Path | str | None = None) -> FontSelection:
    """Return a complete serif family without copying it into the project."""
    if explicit_path is not None:
        path = Path(explicit_path).expanduser().resolve()
        if not path.is_file():
            raise FontNotFoundError(f"configured font does not exist: {path}")
        try:
            font = ImageFont.truetype(str(path), 20)
            family = font.getname()[0]
        except OSError as error:
            raise FontNotFoundError(f"configured font cannot be loaded: {path}") from error
        return FontSelection(family, path, path, path, path, fallback_used=True)

    env_path = os.environ.get("LITCLOCK_FONT")
    if env_path:
        return discover_font(env_path)

    for family in _SYSTEM_FAMILIES:
        name, *filenames = family
        paths = tuple(_find_filename(filename) for filename in filenames)
        if all(path is not None for path in paths):
            regular, bold, italic, bold_italic = paths
            assert regular and bold and italic and bold_italic
            return FontSelection(name, regular, bold, italic, bold_italic)
    raise FontNotFoundError(
        "no supported serif family was found; pass --font PATH or set LITCLOCK_FONT"
    )


def load_fonts(selection: FontSelection, body_size: int, attribution_size: int) -> LoadedFonts:
    def load(path: Path, size: int) -> ImageFont.FreeTypeFont:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError as error:
            raise FontNotFoundError(f"font could not be loaded: {path}") from error

    return LoadedFonts(
        regular=load(selection.regular, body_size),
        bold=load(selection.bold, body_size),
        italic=load(selection.italic, body_size),
        bold_italic=load(selection.bold_italic, body_size),
        attribution_regular=load(selection.regular, attribution_size),
        attribution_italic=load(selection.italic, attribution_size),
    )


def text_width(font: ImageFont.FreeTypeFont, text: str) -> float:
    return float(font.getlength(text))


def line_height(font: ImageFont.FreeTypeFont, spacing: float = 1.16) -> int:
    ascent, descent = font.getmetrics()
    return max(1, round((ascent + descent) * spacing))


def unsupported_glyphs(text: str, font: ImageFont.FreeTypeFont) -> set[str]:
    """Find code points rendered as the font's missing-glyph box."""

    def signature(character: str) -> tuple[tuple[int, int], bytes]:
        mask = font.getmask(character)
        return mask.size, bytes(mask)

    try:
        missing_signature = signature("\U0010ffff")
    except (OSError, ValueError):
        return set()
    unsupported: set[str] = set()
    for character in set(text):
        if character.isspace() or character in {"\u200b", "\ufeff"}:
            continue
        try:
            if signature(character) == missing_signature:
                unsupported.add(character)
        except (OSError, ValueError):
            unsupported.add(character)
    return unsupported
