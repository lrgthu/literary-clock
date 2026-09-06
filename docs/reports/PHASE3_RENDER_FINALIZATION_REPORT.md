# Phase 3 Renderer Finalization Report

## Result

**READY FOR PHYSICAL KINDLE TEST**

The V1 bitmap renderer is now optimized for a Kindle Paperwhite 4 / 10th Generation in native landscape. Corpus expansion and semantic rules remained frozen. No Kindle was accessed or modified.

## 1. Multi-record root cause and exact fix

The defect originated in the Phase 1 pipe-delimited importer, not in Pillow or line wrapping. The `JohsEnevoldsen/literature-clock` source uses `|` delimiters but contains prose lines with unmatched ASCII opening quotation marks and typographic closing quotation marks. `csv.reader(..., delimiter="|")` therefore treated later physical lines as one quoted field and consumed 2–32 upstream records into a single `RawQuote`. Those embedded records then reached the canonical `quote` field, while the last swallowed row supplied misleading title/author columns.

Exactly five canonical rows contain this pipe signature; two are selectable (quote IDs 4950, 4953). The reproducible importer now uses `quoting=csv.QUOTE_NONE`, which is correct for this pipe-only source and prevents cross-line consumption. A regression test covers an unmatched ASCII/curly quotation pair followed by another physical record. The frozen production database was not rebuilt; its two selectable contaminated rows are caught by the new presentation gate and are never rendered.

The pre-layout gate classifies `CLEAN`, `DIRTY_SERIALIZED_RECORD`, `MULTI_RECORD_CONCATENATION`, and `EXCERPT_CORRUPTION`. It rejects pipe-field runs, `|title|author|`-like metadata, repeated `sfw/unknown HH:MM|` fields, and excerpt/source-span mismatches. Selector fallback tries another candidate and persists history only for the accepted frame.

## 2. PW4 landscape specification

- Native frame: **1448 × 1072**, 300 ppi; no portrait rotation.
- Safe margins: 123px horizontal and 96px vertical.
- Normal/compact maximum text measure: 1173px / 1202px.
- Body is left aligned inside an optically centered wide measure; the complete composition is centered at 46% of the available vertical remainder.
- Attribution begins 51px to the right of the quote block in landscape, while remaining visually attached.
- Georgia was discovered locally for QA; paths are recorded in each sidecar. No font binary is stored in the repository.
- Body gray is 72/255 (about 72% visual black), highlighted time is bold at 0/255, and attribution is 112/255 (about 56% visual black).

## 3. Typography, line, and excerpt policy

The nominal landscape body size is 62px. It may adapt down to a hard minimum of **38px**, never below. Corpus-wide final sizes are 38 / 62.0 / 81px (minimum / median / maximum).

Preferred, soft, and hard body limits are 7, 8, and **10** lines. The audited maximum is 10; 0 quotes exceed the hard limit.

Fallback order is full text at normal spacing, full text with modestly wider/tighter compact geometry, then sentence-aligned excerpts. Excerpts always contain the exact highlighted phrase; may include the preceding/following sentence; preserve canonical wording; and add `…` only when source text is omitted. Offsets, ellipsis flags, and sentence alignment are stored in `RenderQuote`. If no readable excerpt fits, the quote is rejected for this device without changing the corpus.

## 4. Display attribution policy

Canonical titles and creators remain unchanged. Renderer-only display titles remove high-confidence subtitle/catalog tails introduced by patterns such as `Being the Narrative of`, `An Account of`, repeated descriptive colons, `, or`, semicolons, and long em-dash/hyphen subtitles. Width fallback uses measured pixels and a typographic ellipsis.

Display authors remove life dates and bracketed catalog roles, and invert `Surname, Given` when the structure is reliable. Two creators may appear together. Three or more become `<First Author> et al.` unless an editor is identified. Collections/anthologies with an editor use `Edited by <Editor>`; the real Lock and Key Library case becomes `The Lock and Key Library` / `Edited by Julian Hawthorne`.

Title is normally one line and at most two; creator is at most one; the complete attribution is capped at three. Corpus-wide attribution lines have min / median / max 1 / 2.0 / 3; over-budget frames: 0.

## 5. Corpus-wide PW4 landscape classification

- Canonical literary quotes: 7,229
- Unique selectable quotes audited: 7,091
- Effective quote-minute relationships before display filtering: 8,730
- Display-safe relationships: 8,727
- `DISPLAY_SAFE_FULL`: 7,080
- `DISPLAY_SAFE_EXCERPT`: 8
- `REJECT_DIRTY`: 3
- `REJECT_TOO_LONG`: 0
- `REJECT_ATTRIBUTION`: 0
- `REJECT_LAYOUT`: 0

Minutes with 0 / 1 / 2 / >=3 display-safe candidates: **0 / 26 / 71 / 1343**. Zero-minute identities: none.

The 3 selectable dirty rows remain valid database records under the frozen corpus but cannot reach a bitmap. No minute loses all display-safe choices.

## 6. Excerpt and highlight statistics

- Sentence-excerpt quotes: 8
- Excerpt canonical length min / median / max: 690 / 713.5 / 760 characters
- Excerpt display length min / median / max: 47 / 184.5 / 673 characters
- Highlight wrapped / not wrapped: 3 / 7,085
- Pathological highlight wraps: 0

## 7. Visual QA and remaining failures

- Deterministic PW4 frames: 32 successful; 0 failed
- QA clipping: 0
- QA body below minimum: 0
- QA body over ten lines: 0
- QA attribution over three lines: 0
- Unsupported QA glyphs: none
- Remaining zero-safe minute pools: 0

The only corpus-wide failures are listed in `RENDER_QA.json`: the two importer-contaminated rows and one serialized webjournal/export record caught by the defensive gate. Dirty regression cases M/N deliberately have JSON/Markdown audit records but no PNG. Consequently, no final QA image contains raw corpus serialization or multiple records.

Thresholded 1-bit is the V1 recommendation. The time emphasis survives through font weight, and threshold output is crisper than Floyd–Steinberg text edges. Grayscale remains the workstation review format.

## 8. Representative PW4 landscape artifacts

- `data/generated/render_previews/pw4_landscape/contact-sheet-grayscale.png`
- `data/generated/render_previews/pw4_landscape/contact-sheet-1bit.png`
- `data/generated/render_previews/pw4_landscape/contact-sheet-dither.png`
- `data/generated/render_previews/pw4_landscape/excerpt/`
- `data/generated/render_previews/pw4_landscape/anthology/`
- `data/generated/render_previews/pw4_landscape/long-title/`
- `data/generated/render_previews/pw4_landscape/dirty-rejected/DIRTY_RECORD_AUDIT.md`

## 9. Validation and recommendation

The final validation commands are `pytest`, `ruff check`, `ruff format --check`, and SQLite `PRAGMA integrity_check`. Their verified results are recorded below after the final run:

<!-- FINAL_VALIDATION_RESULTS -->

- `pytest`: PASS — 300 tests
- `ruff check .`: PASS
- `ruff format --check .`: PASS — 69 files formatted
- SQLite `PRAGMA integrity_check`: `ok`
- SQLite `PRAGMA foreign_key_check`: 0 violations
- Final preview inventory: 53 PNGs, with no rejected quote ID or Paperwhite-5 artifact

**READY FOR PHYSICAL KINDLE TEST.** For the physical test, use a pre-rendered, crisp 1-bit PNG through FBInk/eips first. This preserves the workstation-validated mixed weights, wrapping, Unicode, and excerpt geometry. Direct FBInk text composition remains a later device-side comparison. This phase did not access, configure, mount, jailbreak, or modify the Kindle.
