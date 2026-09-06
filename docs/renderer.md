# Renderer

## Visual target

The primary profile is Kindle Paperwhite 4 / 10th Generation in native **1448 × 1072 landscape at
300 ppi**. Portrait is supported separately; landscape is not produced by rotating a portrait
bitmap.

The page contains a quiet date marginal note, one literary quotation, and attribution. Body text
is a classic serif, left aligned inside an optically centered wide block. In grayscale, ordinary
text is about 70–80% visual black, while the inline time phrase is bold/full black and may use a
second family. The book title is smaller and italic; the normalized author/editor line is smaller
regular serif. No standalone clock, weather, icons, border, Kindle status bar, or interface chrome
is drawn.

PW4 landscape shows a renderer-owned short date by default: `Sat, Sep 5`. Formatting uses fixed
English three-letter weekday/month names and an unpadded day, with no year or clock time. It sits
at 5% of frame width/height (72, 54 px on PW4), independent from the optically centered quote.
The physically selected PW4 scale produces a typical 29–32 px label. Sizing explicitly caps it at
one pixel below the selected attribution size, preserving the quote → attribution → date hierarchy
across Georgia on macOS and DejaVu Serif in Linux CI. Diagnostics record its exact text, font size,
bounding box, clipping, and collision state. Other device profiles default off;
the CLI offers `--show-date`, `--hide-date`, and deterministic `--date YYYY-MM-DD`.

## Layout policy

PW4 landscape uses 152 px horizontal and 96 px vertical safe margins. The normal text measure is
1,100 px and the compact fallback is at most 1,187 px. The nominal body size is 62 px with a hard
38 px minimum. Three to seven lines are preferred, eight is the soft maximum, and ten is the hard
maximum. Text never shrinks below the readable minimum merely to avoid clipping.

Wrapping uses the actual body and time-font metrics. A highlighted phrase stays together when it
fits cleanly; otherwise it can wrap without losing or altering any source character. The time
face's width, ascent, descent, scaled size, and raised baseline participate in line geometry before
drawing. PW4 non-final body lines use bounded book-style justification: spacing expands toward the
right edge only until each word gap reaches a strict 0.5 em maximum, after which the line remains
partially ragged. Final paragraph lines remain natural. Quote and attribution are composed as one
optically centered unit, with the landscape attribution kept as one left-aligned block anchored to
the right edge of the quote measure; the detached date does not push that composition downward.
Normal line spacing is 1.05 rather than the earlier 1.16.

Every body-size candidate is evaluated with the matching attribution font and its complete
vertical budget. If body plus gap plus attribution is too tall, the engine tries the next smaller
readable full-quote size before compact geometry or excerpting. The ten-line, minimum-size,
three-line attribution, and clipping limits remain hard constraints. The detached date is tested
against the proposed body using two-dimensional rectangles; vertical ranges may overlap when the
date and body are genuinely separated horizontally.

## Frozen PW4 V1 production contract

`PW4_V1_RENDER_CONFIG` and `uv run litclock render-pw4-v1` are the single production rendering
path for the later device runtime. The contract fixes `pw4_landscape`, date on, `picturesque`,
book/author attribution, 1-bit output, and threshold conversion. It also explicitly states that
the rendered page has no standalone clock or system UI.

The body family is resolved specifically as Georgia rather than using generic family preference.
The Apple Chancery accent is configured locally and never distributed:

```bash
export LITCLOCK_TIME_FONT='/local/path/to/Apple Chancery.ttf'
uv run litclock render-pw4-v1 16:37 --preview --output /tmp/litclock-frame.png
```

The preset checks the font's embedded family name and fails if the variable is absent, the file is
unloadable, or the configured face is not Apple Chancery. It never falls back while claiming the
frozen visual design. Generic renderer commands deliberately remain portable and keep their prior
subtle-lift/body-family defaults.

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

Title simplification recognizes only high-confidence catalog constructions such as `Being the
Narrative of`, `Being an Account of`, `An Account of`, repeated descriptive colons, and similar
structured tails. Bare `Being` is not a delimiter: titles such as *The Importance of Being Earnest*
and *On Being Human* remain intact.

## Fonts

Automatic discovery selects a complete four-face family from EB Garamond, Linux Libertine,
Georgia, DejaVu Serif, or Liberation Serif. An explicit production family uses
`--font-regular`, `--font-bold`, `--font-italic`, and `--font-bold-italic` together. Missing faces
fail clearly, and supplied style files are validated using their embedded style metadata.

The older `--font PATH` option remains as an explicit single-face compatibility fallback. It emits
a warning, records unavailable bold/italic faces in diagnostics, and uses the regular face only;
therefore it is unsuitable when the final visual hierarchy matters. `LITCLOCK_FONT` has the same
single-face behavior. No font binaries are distributed by this repository.

An optional accent family for the literary time phrase is configured with
`--time-font-regular PATH --time-font-bold PATH`. Both are required; the supplied bold face is
validated. `--time-font PATH` uses only that exact file and reports whether it is truly bold. A
missing or unloadable explicit face fails clearly. With no accent option, the body family's bold
face remains the time face, preserving prior rendering. The `classic`, `subtle-lift`, and
`expressive` treatments apply to whichever time family is selected. `picturesque` adds a
deterministic character-level vertical rhythm without changing text or character order. When its
accent is a single honest face with no bold file, it may add a measured ink stroke; that stroke
participates in width, line height, wrapping, clipping, and diagnostics.

Physical 1-bit QA compared Georgia body text with Arial Bold, Georgia Bold, Times New Roman Bold,
and Apple Chancery. The first three were rejected as insufficiently art-like on the panel. The
frozen V1 pairing is **Georgia body and attribution + Apple Chancery time phrase + `picturesque`**.
At the controlled 65 px body size the time face is 72 px, uses deterministic 1–10 px character
lifts, and receives a measured 2 px ink stroke. The date is 32 px at the physically approved
`(72, 54)` position. Apple Chancery is a local system font and is not redistributed or hardcoded
as a portable path.

Built-in Paperwhite profiles distinguish `paperwhite-1-2` (758 × 1024 at 212 ppi),
`paperwhite-3` (1072 × 1448 at 300 ppi), PW4 portrait/landscape, and `paperwhite-5`. The historical
CLI alias `paperwhite-1-3` resolves to `paperwhite-1-2` for compatibility; it no longer implies that
Paperwhite 3 has the earlier resolution.

## Dirty-record and renderability gates

Before layout, records are classified as `CLEAN`, `DIRTY_SERIALIZED_RECORD`,
`MULTI_RECORD_CONCATENATION`, or `EXCERPT_CORRUPTION`. Raw pipe-delimited fields, repeated source
metadata, serialized web exports, and excerpt/source mismatches cannot reach a bitmap.

Device suitability reports `DISPLAY_SAFE_FULL`, `DISPLAY_SAFE_EXCERPT`, `REJECT_DIRTY`,
`REJECT_TOO_LONG`, `REJECT_ATTRIBUTION`, or `REJECT_LAYOUT`. These statuses do not delete or alter
the canonical quote. When a selected candidate is rejected, the selector chooses another eligible
quote and writes display history only for the rendered result.

## Verified status

The frozen PW4 landscape audit classified 7,074 selectable quotes as safe in full, 14 as safe via
excerpt, and 3 as dirty. It found zero empty display-safe minute pools, zero clipping, zero bodies
over ten lines, zero attributions over three lines, and no unsupported glyph in the curated QA
set. The final result is **READY FOR PHYSICAL KINDLE TEST**. Exact measurements and artifacts are
in [`reports/PHASE3_RENDER_FINALIZATION_REPORT.md`](reports/PHASE3_RENDER_FINALIZATION_REPORT.md).
Physical typography results are in
[`reports/PHASE4A4_PHYSICAL_COMPARISON_REPORT.md`](reports/PHASE4A4_PHYSICAL_COMPARISON_REPORT.md).
