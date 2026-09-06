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
    date_regular: ImageFont.FreeTypeFont


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

_TIME_SANS_FAMILIES = (
    ("DejaVu Sans", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    ("Liberation Sans", "LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf"),
    ("Arial", "Arial.ttf", "Arial Bold.ttf"),
)

_TIME_SERIF_FAMILIES = (
    ("Times New Roman", "Times New Roman.ttf", "Times New Roman Bold.ttf"),
    ("DejaVu Serif", "DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf"),
    ("Liberation Serif", "LiberationSerif-Regular.ttf", "LiberationSerif-Bold.ttf"),
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


def discover_time_font(
    explicit_path: Path | str | None = None,
    *,
    regular_path: Path | str | None = None,
    bold_path: Path | str | None = None,
    system_style: str | None = None,
    exclude_family: str | None = None,
) -> FontSelection:
    """Load a real accent face/pair or discover a local sans/second-serif pair.

    A single explicit file remains a single honest face. A structured configuration
    requires both regular and bold so the renderer never invents a weight.
    """
    if explicit_path is not None and (regular_path is not None or bold_path is not None):
        raise FontNotFoundError(
            "--time-font cannot be combined with --time-font-regular/--time-font-bold"
        )
    if system_style is not None and (
        explicit_path is not None or regular_path is not None or bold_path is not None
    ):
        raise FontNotFoundError("system accent discovery cannot be combined with explicit paths")
    if explicit_path is not None:
        path, family, style = _loadable_font_path(explicit_path, role="time accent")
        normalized = style.casefold()
        is_bold = any(token in normalized for token in ("bold", "black", "heavy", "demi", "semi"))
        return FontSelection(
            family,
            path,
            path if is_bold else None,
            None,
            None,
            fallback_used=not is_bold,
        )
    if regular_path is not None or bold_path is not None:
        missing = [
            role for role, path in (("regular", regular_path), ("bold", bold_path)) if path is None
        ]
        if missing:
            raise FontNotFoundError(
                f"explicit time font requires regular and bold faces; missing: {', '.join(missing)}"
            )
        assert regular_path is not None and bold_path is not None
        regular = _loadable_font_path(regular_path, role="time regular")
        bold = _loadable_font_path(bold_path, role="time bold")
        _validate_face_style("bold", bold[2])
        family = regular[1] if regular[1] == bold[1] else f"{regular[1]} / {bold[1]}"
        return FontSelection(family, regular[0], bold[0], None, None)
    if system_style not in {"sans", "serif"}:
        raise FontNotFoundError("time font requires explicit paths or system_style sans|serif")
    families = _TIME_SANS_FAMILIES if system_style == "sans" else _TIME_SERIF_FAMILIES
    for name, regular_name, bold_name in families:
        if exclude_family and name.casefold() == exclude_family.casefold():
            continue
        regular = _find_filename(regular_name)
        bold = _find_filename(bold_name)
        if regular is not None and bold is not None:
            return FontSelection(name, regular, bold, None, None)
    raise FontNotFoundError(f"no supported local {system_style} time-font family was found")


def load_fonts(
    selection: FontSelection,
    body_size: int,
    attribution_size: int,
    *,
    highlight_size: int | None = None,
    time_selection: FontSelection | None = None,
    date_size: int | None = None,
) -> LoadedFonts:
    def load(path: Path | None, size: int) -> ImageFont.FreeTypeFont:
        selected_path = path or selection.regular
        try:
            return ImageFont.truetype(str(selected_path), size)
        except OSError as error:
            raise FontNotFoundError(f"font could not be loaded: {selected_path}") from error

    styled_size = highlight_size or body_size
    time_source = time_selection or selection
    return LoadedFonts(
        regular=load(selection.regular, body_size),
        bold=load(time_source.bold or time_source.regular, styled_size),
        italic=load(selection.italic, body_size),
        bold_italic=load(selection.bold_italic, body_size),
        attribution_regular=load(selection.regular, attribution_size),
        attribution_italic=load(selection.italic, attribution_size),
        date_regular=load(selection.regular, date_size or attribution_size),
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
