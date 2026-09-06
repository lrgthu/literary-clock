# Phase 4A.4 — PW4 Physical Typography Comparison

Date: 2026-09-06

Device: Kindle Paperwhite 4, 1448 × 1072 native landscape at 300 ppi

## Outcome

Physical 1-bit comparison selected **Georgia for all non-time text, Apple Chancery for the literary
time phrase, `picturesque` emphasis, a 32 px renderer-owned date, bounded body justification, and
a right-side attribution block**. Quote 6730 and the fixed date `Sat, Sep 5` controlled every
comparison variable. The final user verdict was: “let's freeze this one.”

The accent font is loaded from the local production Mac only. No font binary or quotation preview
is committed or copied to the Kindle; the device receives only the rendered monochrome frame.

## Physical comparisons

All candidates used a 65 px Georgia body, 33 px Georgia attribution, one clean full quote, 1-bit
threshold conversion, and the same quote/date unless the named variable changed.

| Candidate | Controlled change | Physical result |
|---|---|---|
| A | Arial Bold time, `subtle-lift` | Rejected: too modern; 24 px date too small |
| B | Georgia Bold time | Rejected: insufficiently different from A |
| C | Times New Roman Bold time | Rejected: insufficiently art-like |
| D | Apple Chancery with uneven character horizons | Accepted direction: “looks better” |
| D2 | 2 px time stroke; date enlarged to 32 px | Fonts, date, and emphasis accepted: “all good” |
| D3 | Wider margins; roughly 10% tighter leading | Both changes accepted |
| D4 | Attribution moved to a coherent right-side block | Retained in the final design |
| D5 | Unbounded full justification | Rejected: word spacing too large |
| D6 | Strictly bounded book-style justification | Accepted and frozen |

## Frozen V1 specification

- Body/non-time quote text: Georgia regular.
- Time phrase: local Apple Chancery, 1.10× body size, full black.
- Time rhythm: deterministic per-character vertical lifts; source wording, order, and offsets are
  unchanged.
- Time weight: measured 2 px ink stroke at the controlled 65 px body size because the installed
  Apple Chancery file has no genuine bold face. Diagnostics report the absent bold face and actual
  stroke separately.
- Date: Georgia regular, 32 px in the controlled frame, at the approved `(72, 54)` position.
- Quote measure: 76% of frame width; horizontal safe margin 10.5% / 152 px.
- Leading: 1.05 normal, reduced 9.5% from 1.16; compact fallback 1.02.
- Body alignment: bounded justification on non-final paragraph lines. Word spaces may expand only
  to 0.50 em; a line remains partially ragged if full alignment would exceed that cap.
- Attribution: Georgia italic title plus regular creator, one coherent left-aligned block anchored
  to the quote measure's right edge.
- Output: native 1448 × 1072 layout, crisp 1-bit threshold PNG.

For the controlled D6 frame, the four natural-to-rendered line widths were 829→899.5, 940→1039,
1055→1100, and 965→965 px. Maximum gaps were 32.5, 32.5, 25, and natural final-line spacing. This
preserved alignment pressure without the 54 px gaps seen in rejected full justification.

## Implementation boundaries

`picturesque` segments only the highlighted phrase into measured characters and applies a fixed,
deterministic baseline rhythm. It does not rotate or reorder glyphs. Stroke extents participate in
width, ascent/descent, line height, wrapping, clipping, and JSON diagnostics.

Justification preserves token text and source offsets. Space expansion is measured explicitly;
only non-final lines participate, and the strict per-gap cap is part of the PW4 device profile.
Attribution positioning changes display layout only. No corpus semantics, canonical quote text, or
selection behavior changed.

Apple Chancery is a proprietary system font available on the production Mac. The repository does
not distribute it or hardcode its machine-specific path. Production rendering supplies it with
`--time-font PATH --time-emphasis picturesque`. Environments without that face must provide an
appropriate locally licensed accent or deliberately use the portable body-family fallback.

## Device method

Each candidate used the previously validated finite fullscreen FBInk flow. The helper temporarily
paused the Kindle framework, enabled reversible screen-saver prevention, issued one GC16 refresh,
held the frame for three minutes, restored the original power property, resumed the framework, and
returned Home. No scheduler, loop, new package, firmware change, or power redesign was introduced.

Temporary bitmaps and launchers remain ignored under `data/generated/` because the images contain
third-party literary text.

## Validation

- `uv sync`: PASS — 11 packages resolved, 10 checked.
- `uv run pytest`: PASS — 345 tests.
- `uv run ruff check .`: PASS.
- `uv run ruff format --check .`: PASS.
- SQLite integrity: `ok`; foreign-key violations: 0.
- Corpus audit: 7,091 selectable quotes; 7,074 full, 14 excerpted, 3 dirty; no other rejections.
- Display-safe relationships: 8,727 / 8,730.
- Display-safe minutes: 1,440 / 1,440 (0 empty pools).
- Body font min/median/max: 38 / 62 / 81 px; maximum lines: 10.
- Attribution over three lines: 0.
- Highlight wrapped/pathological: 2 / 0.
- Deterministic QA: 32 / 32 frames; clipping/layout failures: 0 / 0.

**The V1 visual layer is frozen and ready for Phase 4B.**
