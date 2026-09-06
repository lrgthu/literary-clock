# Phase 3.1 Renderer Cleanup Report

Date: 2026-09-05
Primary device: Kindle Paperwhite 4 landscape, 1448 × 1072 at 300 ppi

## Outcome

Phase 3.1 fixed the reviewed presentation and configuration defects without changing corpus
semantics, literary wording, selection policy, or the established renderer design. The full
PW4-landscape corpus audit still has no empty display-safe minute pool. The recommendation remains
**READY FOR PHYSICAL KINDLE TEST**.

## 1. Title trimming

The old subtitle expression allowed the qualifier after `Being` to be empty, so any title with a
standalone `Being` after an initial phrase could be cut. `simplify_title()` now recognizes only the
high-confidence constructions `Being the Narrative of`, `Being an Account of`, `Being a Narrative
of`, `An Account of`, and `A Narrative of` in this rule.

Regression tests preserve *The Importance of Being Earnest*, *On Being Human*, and *The Art of
Being ...*. Existing catalog cleanup remains intact: *Vagabonding down the Andes Being the
Narrative of a Journey...* becomes *Vagabonding down the Andes*, and the repeated-colon *Lock and
Key Library* title becomes *The Lock and Key Library*.

## 2. Attribution-aware full-layout fitting

`LayoutEngine.layout()` now wraps and measures attribution at every candidate body size. A
candidate is accepted only when body height, quote/attribution gap, and attribution height fit the
safe vertical region together. If the complete block is too tall, search continues at smaller body
sizes down to the existing readability minimum before compact layout or sentence excerpting.

The hard ten-body-line limit, minimum body size, three-line attribution limit, and clipping rules
are unchanged. A targeted regression reproduces a 65 px body candidate whose complete block is too
tall; the engine selects a readable 58 px complete quote rather than an excerpt.

## 3. Corpus-wide full versus excerpt audit

The published pre-fix baseline and post-fix audit are identical:

- `DISPLAY_SAFE_FULL`: 7,080 before / 7,080 after
- `DISPLAY_SAFE_EXCERPT`: 8 before / 8 after
- `REJECT_DIRTY`: 3 before / 3 after
- Other rejection classes: 0

All eight existing excerpts genuinely still require sentence-aligned excerpting under the current
readability and line limits. None changed from excerpt to full. Their rendered excerpt body sizes
also remain unchanged:

| Quote ID | Before | After | Result |
| ---: | ---: | ---: | --- |
| 394 | 38 px | 38 px | excerpt remains required |
| 967 | 72 px | 72 px | excerpt remains required |
| 2946 | 38 px | 38 px | excerpt remains required |
| 2969 | 79 px | 79 px | excerpt remains required |
| 3644 | 64 px | 64 px | excerpt remains required |
| 3722 | 38 px | 38 px | excerpt remains required |
| 4029 | 71 px | 71 px | excerpt remains required |
| 4624 | 60 px | 60 px | excerpt remains required |

The defect was real but latent in the current production corpus: it affected valid fit-search
behavior without being the reason these particular eight quotes were excerpted.

## 4. Explicit font configuration

Production-quality explicit configuration now accepts a complete family through
`--font-regular`, `--font-bold`, `--font-italic`, and `--font-bold-italic`. All four are required
together. The files are load-checked, and bold/italic roles are checked against embedded font style
metadata.

Automatic discovery still returns the first complete available EB Garamond, Linux Libertine,
Georgia, DejaVu Serif, or Liberation Serif family. The legacy `--font PATH` and `LITCLOCK_FONT`
forms remain explicit single-face compatibility fallbacks: they emit a CLI warning, mark
bold/italic faces unavailable in diagnostics, and no longer represent one regular path as four
real styles.

## 5. Device profiles

Paperwhite generations are now represented separately:

- `paperwhite-1-2`: 758 × 1024, 212 ppi
- `paperwhite-3`: 1072 × 1448, 300 ppi
- `pw4_portrait`: 1072 × 1448, 300 ppi
- `pw4_landscape`: 1448 × 1072, 300 ppi
- `paperwhite-5`: 1236 × 1648, 300 ppi

The historical CLI name `paperwhite-1-3` remains an alias for `paperwhite-1-2`. PW4 landscape is
unchanged and remains the primary QA target.

## 6. Continuous integration

`.github/workflows/ci.yml` runs on pushes and pull requests with Python 3.11 and uv. It performs
`uv sync`, the complete tracked-fixture pytest suite, Ruff lint, and Ruff format checking. It has no
production database, local Georgia, or bulk-corpus dependency; renderer tests use automatic
cross-platform family discovery with DejaVu/Liberation fallbacks available on Linux.

## 7. Validation

- `uv sync`: PASS
- `uv run pytest`: PASS — 311 tests
- `uv run ruff check .`: PASS
- `uv run ruff format --check .`: PASS
- SQLite `PRAGMA integrity_check`: `ok`
- SQLite `PRAGMA foreign_key_check`: 0 violations

PW4 landscape QA:

- deterministic frames: 32 / 32
- display-safe minute pools: 1,440 / 1,440
- minutes with 0 / 1 / 2 / >=3 safe candidates: 0 / 26 / 71 / 1,343
- clipping: 0
- body below minimum: 0
- body over ten lines: 0
- attribution over three lines: 0
- raw serialized records rendered: 0
- body font minimum / median / maximum: 38 / 62 / 81 px
- highlight wraps / pathological wraps: 3 / 0

## Recommendation

**READY FOR PHYSICAL KINDLE TEST.** Phase 3.1 introduces no new deployment work and does not touch,
mount, configure, jailbreak, or modify a Kindle.
