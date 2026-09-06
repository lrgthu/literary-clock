from __future__ import annotations

from dataclasses import replace

import pytest
from test_render import render_quote

from litclock.models import QualityStatus, Quote
from litclock.render.layout import LayoutEngine, ellipsize_to_width
from litclock.render.models import (
    DirtyRecordStatus,
    RenderabilityStatus,
    RenderMode,
)
from litclock.render.pillow_renderer import PillowRenderer
from litclock.render.presentation import (
    classify_dirty_record,
    excerpt_candidates,
    normalize_attribution,
    validate_excerpt_integrity,
)
from litclock.render.profiles import BUILTIN_PROFILES, get_device_profile
from litclock.render.suitability import is_renderable_for_device
from litclock.render.typography import discover_font, load_fonts, text_width


@pytest.fixture(scope="module")
def renderer() -> PillowRenderer:
    return PillowRenderer(discover_font())


def test_pw4_landscape_is_native_1448_by_1072() -> None:
    profile = get_device_profile("pw4", orientation="landscape")
    assert profile.name == "pw4_landscape"
    assert (profile.width, profile.height, profile.pixel_density_ppi) == (1448, 1072, 300)
    assert profile.orientation == "landscape"


def test_pw4_portrait_remains_supported() -> None:
    profile = get_device_profile("pw4", orientation="portrait")
    assert profile.name == "pw4_portrait"
    assert (profile.width, profile.height, profile.pixel_density_ppi) == (1072, 1448, 300)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "A sentence.|Honor Among Thieves|Jeffrey Archer|sfw 20:43|20.43|"
            "Another sentence.|Spin Control|Chris Moriarty|unknown 13:52|1.52|",
            DirtyRecordStatus.MULTI_RECORD_CONCATENATION,
        ),
        (
            "A passage|Book|Author|sfw 13:45|1.45|",
            DirtyRecordStatus.MULTI_RECORD_CONCATENATION,
        ),
        ("A|B|C|D|E|", DirtyRecordStatus.DIRTY_SERIALIZED_RECORD),
        (
            "You are viewing the webjournal of someone posting on: address. Posted at: 01:39 "
            "on Friday. Status: public Mood: awake.",
            DirtyRecordStatus.DIRTY_SERIALIZED_RECORD,
        ),
    ],
)
def test_raw_serialized_or_multi_record_text_is_rejected(
    text: str, expected: DirtyRecordStatus
) -> None:
    assert classify_dirty_record(text) == expected


def test_clean_literary_text_passes_dirty_gate() -> None:
    assert (
        classify_dirty_record("At four o’clock, the last train crossed the river.")
        == DirtyRecordStatus.CLEAN
    )


def test_dirty_corpus_quote_is_rejected_before_bitmap(renderer: PillowRenderer) -> None:
    text = (
        "At 4:37 the bell rang.|Honor Among Thieves|Jeffrey Archer|sfw 20:43|20.43|"
        "At 8:43 another passage began."
    )
    quote = Quote(
        id=99,
        minute_of_day=997,
        time_24h="16:37",
        time_text="4:37",
        quote=text,
        title="Wrong final title",
        author="Wrong final author",
        sfw=True,
        language="en",
        source_name="test",
        source_url="https://example.test",
        source_license="TEST",
        source_record_id="99",
        quote_hash="",
        normalized_quote_hash="",
        highlight_start=3,
        highlight_end=7,
        quality_status=QualityStatus.VERIFIED_EXACT,
    )
    result = is_renderable_for_device(quote, BUILTIN_PROFILES["pw4_landscape"], renderer.font)
    assert result.status == RenderabilityStatus.REJECT_DIRTY
    assert result.quote is None


def test_excerpt_integrity_detects_display_span_corruption() -> None:
    quote = render_quote(
        "The room was silent. At four o’clock, the bell sounded. Nobody moved.",
        "four o’clock",
    )
    excerpt = excerpt_candidates(quote)[-1]
    corrupted = replace(excerpt, display_quote=excerpt.display_quote.replace("bell", "call"))
    assert validate_excerpt_integrity(corrupted) == DirtyRecordStatus.EXCERPT_CORRUPTION


def test_excerpt_is_sentence_aligned_and_preserves_highlight() -> None:
    quote = render_quote(
        "The room was silent. At four o’clock, the bell sounded. Nobody moved.",
        "four o’clock",
    )
    excerpt = excerpt_candidates(quote)[-1]
    assert excerpt.display_quote == "… At four o’clock, the bell sounded. …"
    assert excerpt.leading_ellipsis and excerpt.trailing_ellipsis
    assert excerpt.sentence_aligned
    assert excerpt.display_quote[excerpt.highlight_start : excerpt.highlight_end] == "four o’clock"
    assert excerpt.canonical_quote == quote.canonical_quote


@pytest.mark.parametrize(
    ("canonical", "display"),
    [
        (
            "Vagabonding down the Andes Being the Narrative of a Journey, Chiefly Afoot, "
            "from Panama to Buenos Aires",
            "Vagabonding down the Andes",
        ),
        (
            "The Lock and Key Library: Classic Mystery and Detective Stories: Modern English",
            "The Lock and Key Library",
        ),
        (
            "The Youthful Wanderer: An Account of a Tour through England, France, Belgium",
            "The Youthful Wanderer",
        ),
    ],
)
def test_catalog_subtitles_are_intelligently_removed(canonical: str, display: str) -> None:
    assert normalize_attribution(canonical, "Author").title == display


def test_inverted_name_life_dates_and_roles_are_removed() -> None:
    display = normalize_attribution("Book", "Franck, Harry Alverson, 1881-1962 [Contributor]")
    assert display.creator == "Harry Alverson Franck"
    assert display.author_simplified


def test_anthology_prefers_editor_over_contributor_dump() -> None:
    display = normalize_attribution(
        "The Lock and Key Library: Classic Mystery and Detective Stories: Modern English",
        "Hawthorne, Julian, 1846-1934 [Editor]; Castle, Egerton, 1858-1920 [Contributor]; "
        "Collins, Wilkie, 1824-1889 [Contributor]; Doyle, Arthur Conan, 1859-1930 [Contributor]",
    )
    assert display.title == "The Lock and Key Library"
    assert display.creator == "Edited by Julian Hawthorne"
    assert display.anthology_mode


def test_three_authors_without_editor_are_compressed() -> None:
    display = normalize_attribution("Collected Tales", "Alpha, Anne; Beta, Bob; Gamma, Cora")
    assert display.creator == "Anne Alpha et al."
    assert display.anthology_mode


def test_measured_width_ellipsis_fits_pixel_budget(renderer: PillowRenderer) -> None:
    font = load_fonts(renderer.font, 42, 22).attribution_italic
    limit = text_width(font, "The Extremely Long Collected")
    rendered = ellipsize_to_width(
        "The Extremely Long Collected Works of a Fictional Writer in Several Volumes",
        font,
        limit,
    )
    assert rendered.endswith("…")
    assert text_width(font, rendered) <= limit


def test_attribution_never_exceeds_three_lines(renderer: PillowRenderer) -> None:
    quote = render_quote(
        "At four o’clock, the train arrived.",
        "four o’clock",
        title="The Very Long Complete History of an Extraordinary Expedition Across Three Seas "
        "and Several Continents Without Any Sensible Interruption",
        author="A Very Long Catalogued Author Name That Must Remain Subordinate",
    )
    layout = LayoutEngine(renderer.font).layout(quote, BUILTIN_PROFILES["pw4_landscape"])
    assert layout.diagnostics.attribution_line_count <= 3


def test_full_quote_respects_minimum_font_and_ten_line_limit(renderer: PillowRenderer) -> None:
    profile = BUILTIN_PROFILES["pw4_landscape"]
    text = "At four o’clock, " + "the road continued through the dark wood. " * 9
    quote = render_quote(text, "four o’clock")
    result = is_renderable_for_device(quote, profile, renderer.font)
    assert result.status in {
        RenderabilityStatus.DISPLAY_SAFE_FULL,
        RenderabilityStatus.DISPLAY_SAFE_EXCERPT,
    }
    assert result.quote is not None
    frame = renderer.render(result.quote, profile)
    assert frame.diagnostics.body_font_size >= profile.minimum_body_size
    assert frame.diagnostics.body_line_count <= profile.hard_body_lines
    assert not frame.diagnostics.clipping


def test_legitimate_long_quote_uses_sentence_excerpt(renderer: PillowRenderer) -> None:
    before = "Before the appointed hour, the travelers argued in the station. " * 8
    target = "At four o’clock, the guard finally opened the gate."
    after = "Afterward they departed without another word. " * 8
    quote = render_quote(before + target + after, "four o’clock")
    result = is_renderable_for_device(quote, BUILTIN_PROFILES["pw4_landscape"], renderer.font)
    assert result.status == RenderabilityStatus.DISPLAY_SAFE_EXCERPT
    assert result.quote is not None
    assert target in result.quote.display_quote
    assert result.quote.leading_ellipsis and result.quote.trailing_ellipsis
    assert (
        result.quote.display_quote[result.quote.highlight_start : result.quote.highlight_end]
        == "four o’clock"
    )


def test_rendered_frame_contains_only_one_canonical_quote(renderer: PillowRenderer) -> None:
    quote = render_quote("At four o’clock, the single bell sounded.", "four o’clock")
    frame = renderer.render(quote, BUILTIN_PROFILES["pw4_landscape"])
    reconstructed = " ".join(
        quote.text[line.source_start : line.source_end] for line in frame.layout.body_lines
    )
    assert "single bell" in reconstructed
    assert "|" not in reconstructed


def test_pw4_grayscale_and_one_bit_are_clean(renderer: PillowRenderer) -> None:
    quote = render_quote()
    profile = BUILTIN_PROFILES["pw4_landscape"]
    grayscale = renderer.render(quote, profile, mode=RenderMode.GRAYSCALE)
    monochrome = renderer.render(quote, profile, mode=RenderMode.ONE_BIT)
    assert grayscale.image.size == (1448, 1072) and grayscale.image.mode == "L"
    assert monochrome.image.size == (1448, 1072) and monochrome.image.mode == "1"
    assert not grayscale.diagnostics.clipping
