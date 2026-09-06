# Renderer

## Visual target

The primary profile is Kindle Paperwhite 4 / 10th Generation in native **1448 × 1072 landscape at
300 ppi**. Portrait is supported separately; landscape is not produced by rotating a portrait
bitmap.

The page contains only a literary quotation and quiet attribution. Body text is a classic serif,
left aligned inside an optically centered wide block. In grayscale, ordinary text is about 70–80%
visual black, while the inline time phrase uses the same face in bold/full black. The book title is
smaller and italic; the normalized author/editor line is smaller regular serif. No standalone
clock, date, weather, icons, border, or interface chrome is drawn.

## Layout policy

PW4 landscape uses 123 px horizontal and 96 px vertical safe margins. The normal text measure is
1,173 px and the compact fallback is at most 1,202 px. The nominal body size is 62 px with a hard
38 px minimum. Three to seven lines are preferred, eight is the soft maximum, and ten is the hard
maximum. Text never shrinks below the readable minimum merely to avoid clipping.

Wrapping uses actual regular/bold font metrics. A highlighted phrase stays together when it fits
cleanly; otherwise it can wrap without losing or altering any source character. Quote and
attribution are composed as one optically centered unit, with the landscape attribution shifted
slightly right while remaining attached to the quote.

## Full text and excerpt fallback

The fallback order is:

1. complete quote with normal geometry;
2. complete quote down to the minimum readable size;
3. complete quote with modestly tighter spacing/wider measure;
4. sentence-aligned excerpt containing the highlighted phrase;
5. reject the quote for this device and select another.

Excerpts preserve original wording and the complete highlight. They prefer the containing sentence
and may include one neighboring sentence. A leading or trailing `…` marks omitted source text.
`excerpt_start`, `excerpt_end`, ellipsis flags, and exact display offsets are validated against the
canonical passage before layout.

## Display-only attribution

Canonical metadata remains untouched. Presentation rules remove clear catalog subtitles, life
dates, bracketed creator roles, and reliable inverted-name syntax. Titles normally occupy one line
and at most two, using measured-width ellipsis only after semantic simplification. One creator is
shown normally, two may be joined, and three or more become `<First Author> et al.` unless a clear
editor exists. Collections then use `Edited by <Editor>`. The entire attribution is capped at three
lines and never forces a dramatic reduction in quote size.

## Dirty-record and renderability gates

Before layout, records are classified as `CLEAN`, `DIRTY_SERIALIZED_RECORD`,
`MULTI_RECORD_CONCATENATION`, or `EXCERPT_CORRUPTION`. Raw pipe-delimited fields, repeated source
metadata, serialized web exports, and excerpt/source mismatches cannot reach a bitmap.

Device suitability reports `DISPLAY_SAFE_FULL`, `DISPLAY_SAFE_EXCERPT`, `REJECT_DIRTY`,
`REJECT_TOO_LONG`, `REJECT_ATTRIBUTION`, or `REJECT_LAYOUT`. These statuses do not delete or alter
the canonical quote. When a selected candidate is rejected, the selector chooses another eligible
quote and writes display history only for the rendered result.

## Verified status

The final PW4 landscape audit classified 7,080 selectable quotes as safe in full, 8 as safe via
excerpt, and 3 as dirty. It found zero empty display-safe minute pools, zero clipping, zero bodies
over ten lines, zero attributions over three lines, and no unsupported glyph in the curated QA
set. The final result is **READY FOR PHYSICAL KINDLE TEST**. Exact measurements and artifacts are
in [`reports/PHASE3_RENDER_FINALIZATION_REPORT.md`](reports/PHASE3_RENDER_FINALIZATION_REPORT.md).
