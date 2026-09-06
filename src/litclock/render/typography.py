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
    bold: Path | None
    italic: Path | None
    bold_italic: Path | None
    fallback_used: bool = False

    @property
    def has_bold(self) -> bool:
        return self.bold is not None

    @property
    def has_italic(self) -> bool:
        return self.italic is not None

    @property
    def has_bold_italic(self) -> bool:
        return self.bold_italic is not None

    @property
    def is_complete_family(self) -> bool:
        return self.has_bold and self.has_italic and self.has_bold_italic


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


def _loadable_font_path(path: Path | str, *, role: str) -> tuple[Path, str, str]:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FontNotFoundError(f"configured {role} font does not exist: {resolved}")
    try:
        font = ImageFont.truetype(str(resolved), 20)
        family, style = font.getname()
    except OSError as error:
        raise FontNotFoundError(f"configured {role} font cannot be loaded: {resolved}") from error
    return resolved, family, style


def _validate_face_style(role: str, style: str) -> None:
    normalized = style.casefold()
    bold = any(token in normalized for token in ("bold", "black", "heavy", "demi", "semi"))
    italic = any(token in normalized for token in ("italic", "oblique"))
    if role == "bold" and not bold:
        raise FontNotFoundError(f"configured bold face identifies itself as {style!r}, not bold")
    if role == "italic" and not italic:
        raise FontNotFoundError(
            f"configured italic face identifies itself as {style!r}, not italic"
        )
    if role == "bold italic" and not (bold and italic):
        raise FontNotFoundError(
            f"configured bold-italic face identifies itself as {style!r}, not bold italic"
        )


def discover_font(
    explicit_path: Path | str | None = None,
    *,
    regular_path: Path | str | None = None,
    bold_path: Path | str | None = None,
    italic_path: Path | str | None = None,
    bold_italic_path: Path | str | None = None,
) -> FontSelection:
    """Return an explicit or automatically discovered serif family.

    ``explicit_path`` is the backward-compatible single-face mode. Missing styles are
    represented honestly and loaded from the regular face only as a documented fallback.
    Structured explicit configuration requires all four paths.
    """
    structured = {
        "regular": regular_path,
        "bold": bold_path,
        "italic": italic_path,
        "bold italic": bold_italic_path,
    }
    if explicit_path is not None and any(path is not None for path in structured.values()):
        raise FontNotFoundError("--font cannot be combined with explicit family face paths")
    if any(path is not None for path in structured.values()):
        missing = [role for role, path in structured.items() if path is None]
        if missing:
            raise FontNotFoundError(
                "explicit font family requires regular, bold, italic, and bold-italic faces; "
                f"missing: {', '.join(missing)}"
            )
        loaded = {
            role: _loadable_font_path(path, role=role)
            for role, path in structured.items()
            if path is not None
        }
        for role in ("bold", "italic", "bold italic"):
            _validate_face_style(role, loaded[role][2])
        families = {family for _, family, _ in loaded.values()}
        family = loaded["regular"][1]
        if len(families) > 1:
            family = " / ".join(sorted(families))
        return FontSelection(
            family,
            loaded["regular"][0],
            loaded["bold"][0],
            loaded["italic"][0],
            loaded["bold italic"][0],
        )

    if explicit_path is not None:
        path, family, _ = _loadable_font_path(explicit_path, role="single-face")
        return FontSelection(family, path, None, None, None, fallback_used=True)

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
        "no supported serif family was found; configure all four explicit face paths or use "
        "the documented single-face --font fallback"
    )


def load_fonts(selection: FontSelection, body_size: int, attribution_size: int) -> LoadedFonts:
    def load(path: Path | None, size: int) -> ImageFont.FreeTypeFont:
        selected_path = path or selection.regular
        try:
            return ImageFont.truetype(str(selected_path), size)
        except OSError as error:
            raise FontNotFoundError(f"font could not be loaded: {selected_path}") from error

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
