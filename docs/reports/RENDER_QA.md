# PW4 Landscape Render QA

Primary target: Kindle Paperwhite 4, native landscape 1448 × 1072 at 300 ppi. No portrait bitmap is rotated for this layout.

## Corpus-wide display-safety audit

- Unique selectable quotes audited: 7,091
- Display-safe quote-minute relationships: 8,727 / 8,730
- DISPLAY_SAFE_FULL: 7,080
- DISPLAY_SAFE_EXCERPT: 8
- REJECT_DIRTY: 3
- REJECT_TOO_LONG: 0
- REJECT_ATTRIBUTION: 0
- REJECT_LAYOUT: 0
- Minutes with 0 / 1 / 2 / >=3 safe candidates: 0 / 26 / 71 / 1343
- Body font size min / median / max: 38 / 62.0 / 81 px
- Maximum body lines: 10 (hard limit: 10)
- Full / excerpt: 7,080 / 8
- Highlight wrapped / pathological: 3 / 0

## Deterministic visual cases

- Rendered frames: 32
- Render failures: 0
- Clipping: 0
- Below minimum body size: 0
- Attribution over three lines: 0
- Unsupported glyphs: none

Dirty M/N regression cases are represented as rejection metadata only. No bitmap was produced for either contaminated canonical row.

## Artifacts

- Grayscale sheet: `data/generated/render_previews/pw4_landscape/contact-sheet-grayscale.png`
- 1-bit threshold sheet: `data/generated/render_previews/pw4_landscape/contact-sheet-1bit.png`
- Threshold/dither comparison: `data/generated/render_previews/pw4_landscape/contact-sheet-dither.png`
