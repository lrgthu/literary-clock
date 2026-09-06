# Phase 4A.3 — Landscape Date and Time-Font Report

Date: 2026-09-05  
Primary device: Kindle Paperwhite 4 landscape, 1448 × 1072 at 300 ppi

## Outcome

PW4 landscape now supports a renderer-owned short date and an independently configured font for
the highlighted literary time phrase. Corpus semantics, quote text, eligibility, selection, the
fullscreen path, and power behavior are unchanged. The recommended physical-test pairing is
**Georgia body + Arial Bold time phrase + `subtle-lift` + date on**.

## 1. Date

- Format: fixed English `EEE, MMM D`, for example `Sat, Sep 5`; there is no leading zero, year, or
  clock time.
- Source: explicit `--date YYYY-MM-DD` for reproducible previews, otherwise an isolated local-date
  provider.
- Visibility: on by default for `pw4_landscape`; explicitly controlled by `--show-date` and
  `--hide-date`. Other profiles default off.
- Placement: proportional `(0.05 × width, 0.05 × height)`, exactly `(72, 54)` on PW4 landscape.
- Typography: body-family regular at 72% of attribution size, subject to a 20 px PW4 minimum.
  The comparison quote used 22 px; the physical candidate quote used 24 px.
- Ink: 144/255 in grayscale. In thresholded 1-bit output it remains readable through the regular
  face rather than artificial bold.

The date has its own measured bounding box and collision flag. It remains detached from the quote
and does not participate in optical centering. The fixed QA label did not clip or collide with any
required frame.

## 2. Independent time font

`PillowRenderer` and `LayoutEngine` now accept a separate `FontSelection` for the highlighted
phrase. Mixed-style wrapping uses the actual accent face, scaled size, ascent/descent, and baseline
shift before choosing line breaks. Drawing therefore cannot substitute a wider or taller face
after layout.

The explicit CLI API is:

```text
--time-font-regular PATH --time-font-bold PATH
```

Both faces are required and the bold file's embedded style is validated. `--time-font PATH` uses
that one file exactly and reports when it is not a verified bold face. Missing/unloadable paths
fail; there is no unrelated silent substitution. With no accent configuration, the body family's
bold face is used exactly as before.

All three emphasis modes remain available:

- `classic`: 1.00×, same baseline
- `subtle-lift`: 1.10×, baseline raised by 5% of body size
- `expressive`: 1.16×, baseline raised by 7.5% of body size

## 3. Local 1-bit font comparison

The fixed comparison used quote 1859 and `Sat, Sep 5`:

| Pairing | Local families | Result |
|---|---|---|
| Same family | Georgia / Georgia Bold | Most traditional; least additional distinction |
| Sans accent | Georgia / Arial Bold | Clearest tasteful contrast; recommended |
| Second serif | Georgia / Times New Roman Bold | Restrained, but visually close to Georgia |

Ten curated 1-bit threshold variants covered date on/off, all three emphasis modes, and the three
font directions. Every variant had zero clipping, zero date collision, a one-line highlight, and
valid attribution. On the comparison quote, body/highlight sizes were 60/60 px in `classic`,
60/66 px in `subtle-lift`, and 60/70 px in `expressive`; the date was 22 px.

The final recommendation is **Arial Bold with `subtle-lift`**. The font contrast already provides
energy, so `expressive` adds more size than is necessary for V1. Arial is used only from the local
system and is not committed or redistributed.

## 4. Corpus and renderer QA

- Selectable classification: 7,079 `DISPLAY_SAFE_FULL`, 9 `DISPLAY_SAFE_EXCERPT`, 3
  `REJECT_DIRTY`, and 0 other rejection classes.
- Display-safe minute pools: 1,440 / 1,440.
- Minutes with 0 / 1 / 2 / ≥3 safe candidates: 0 / 26 / 71 / 1,343.
- Deterministic PW4 renders: 32 / 32.
- Clipping / under-minimum body / over-10-line body / over-3-line attribution: 0 / 0 / 0 / 0.
- Unsupported glyphs in the curated suite: none.
- Comparison wrapping regressions or date collisions: none.

## 5. Physical-test candidates

1. Recommended: date on, Georgia body, Arial Bold time, `subtle-lift`.
2. Conservative: date on, Georgia body/time, `subtle-lift`.
3. Alternate serif: date on, Georgia body, Times New Roman Bold time, `subtle-lift`.

All three physical candidates use the same known-good quote 6730, are exact 1448 × 1072 1-bit
threshold PNGs, keep the highlight on one line, and report no clipping or date collision.

Artifacts are generated locally (and intentionally ignored by Git):

- `data/generated/render_previews/pw4_landscape/phase4a3-date-time-font/PHASE4A3_CONTACT_SHEET.png`
- `data/generated/render_previews/pw4_landscape/phase4a3-date-time-font/date-on_sans-accent_subtle-lift_q1859.png`
- `data/generated/render_previews/pw4_landscape/phase4a3-date-time-font/date-on_same-family_subtle-lift_q1859.png`
- `data/generated/render_previews/pw4_landscape/phase4a3-date-time-font/date-on_second-serif-accent_subtle-lift_q1859.png`
- `data/generated/phase4a3-cli-smoke.png` (quote 6730, recommended pairing)
- `data/generated/phase4a3-physical-candidates/01-same-family.png`
- `data/generated/phase4a3-physical-candidates/02-arial-sans.png` (recommended)
- `data/generated/phase4a3-physical-candidates/03-times-serif.png`

## 6. Validation

Final validation results are recorded here after the repository-wide run:

<!-- FINAL_VALIDATION_RESULTS -->

- `uv sync`: PASS (11 locked packages resolved; 10 checked)
- `uv run pytest`: PASS — 339 tests
- `uv run ruff check .`: PASS
- `uv run ruff format --check .`: PASS — 75 files formatted
- SQLite `PRAGMA integrity_check`: `ok`
- SQLite `PRAGMA foreign_key_check`: 0 violations
