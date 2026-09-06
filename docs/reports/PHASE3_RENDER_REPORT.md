# Phase 3 Render Report

## Result

The final V1 visual renderer is implemented as a device-independent `RenderQuote` contract, a metric-driven layout engine, and a fully implemented Pillow bitmap backend. Corpus expansion remained frozen and no Kindle was accessed.

## Architecture

`QuoteSelector` → `RenderQuote` → `LayoutEngine` → `PillowRenderer` → grayscale or 1-bit PNG. Selection remains outside the renderer. Real render commands persist selector history; `--preview` and `render-id` do not. Invalid highlight offsets fail before any frame is drawn.

## Device profiles

- `kindle-1-4`: 600 × 800
- `paperwhite-1-3`: 758 × 1024
- `kindle-basic-11`: 1072 × 1448
- `paperwhite-5`: 1236 × 1648
- `oasis`: 1264 × 1680

Custom dimensions are supported with paired `--width` and `--height` options.

## Final visual decisions

- Quote: Georgia regular in dark gray, left-aligned in a centered measure.
- Time phrase: Georgia bold in pure black, inline with the author's text.
- Attribution: `— Book Title` in italic, then author in quiet regular type.
- Composition: optically centered slightly above the mathematical midpoint.
- No independent clock, date, weather, icons, borders, or decorative interface.

The font is discovered from the host and referenced by path; no font binary is stored in this repository. Local QA used **Georgia** (`/System/Library/Fonts/Supplemental/Georgia.ttf`).

## QA measurements

- Render attempts / successes / failures: 99 / 99 / 0
- Body font min / median / max: 10 / 65.0 / 88 px
- Highlight-wrapped frames: 2
- Pathological highlight wraps: 0
- Clipped frames: 0
- Frames below the profile minimum size: 7 (quote IDs: 4950)
- Attribution collisions: 0
- Unsupported glyphs: none
- Quotes that failed layout: none

The QA suite includes the 10 shortest and 10 longest selectable quotes, relevant length percentiles, highlight position/length extremes, dialogue and Unicode punctuation, accented attribution, long attribution, shared-clock-face eligibility, 15:46, midnight, noon, both attribution orders, every built-in profile, and both bitmap modes.

Quote 4950, the 3,619-character corpus maximum, fits without clipping but requires 10–22 px body text depending on profile and is therefore flagged below the readable minimum. The renderer does not hide this corpus outlier or silently truncate it.

## E-ink recommendation

Use crisp thresholded 1-bit output for the eventual device path. Font weight—not gray alone—carries the emphasis, so the highlighted phrase survives monochrome conversion. Keep grayscale PNGs for workstation visual review. Floyd–Steinberg is available for comparison, but is not the default because scattered edge pixels reduce text crispness.

The selected attribution order is **book then author**: it preserves the literary-source hierarchy and reads naturally beneath the excerpt.

## Frozen corpus

- Canonical literary quotes: 7,229
- Unique selectable quotes: 7,091
- Quote-minute relationships: 8,730

## Validation

Final local validation: pytest, `ruff check`, `ruff format --check`, and SQLite `PRAGMA integrity_check` all pass.

## Kindle backend recommendation

Begin Phase 4 with **pre-rendered 1-bit PNG displayed through FBInk/eips**. This preserves the tested mixed weights, exact wrapping, Unicode behavior, and page composition while keeping the Kindle runtime small. Direct FBInk text composition should remain an on-device comparison, not the baseline.

Before Phase 4, obtain the exact Kindle model/generation, firmware version, native screen resolution and orientation, effective viewport after any status bar, available local serif font paths/styles, jailbreak/launcher state if the owner later authorizes device work, and whether FBInk or only `eips` is present.

## Artifacts

- `data/generated/RENDER_QA.json`
- `data/generated/RENDER_QA.md`
- `data/generated/render_previews/contact-sheet-grayscale.png`
- `data/generated/render_previews/contact-sheet-1bit.png`
- `data/generated/render_previews/length-p50_q6895_paperwhite-5_grayscale_threshold_book-author.png`
- `data/generated/render_previews/length-p50_q6895_paperwhite-5_1bit_threshold_book-author.png`
- `data/generated/render_previews/minute-1546_q7348_paperwhite-5_grayscale_threshold_book-author.png`
- `data/generated/render_previews/minute-1546_q7348_paperwhite-5_1bit_threshold_book-author.png`
- `data/generated/render_previews/midnight_q1_paperwhite-5_grayscale_threshold_book-author.png`
- `data/generated/render_previews/midnight_q1_paperwhite-5_1bit_threshold_book-author.png`
- `docs/FBINK_RENDER_PLAN.md`
