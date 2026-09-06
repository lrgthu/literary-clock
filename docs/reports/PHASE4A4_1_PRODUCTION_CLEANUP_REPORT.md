# Phase 4A.4.1 — PW4 Production Cleanup

Date: 2026-09-06

## Outcome

The physically approved renderer is now expressed as a named, fail-closed production contract.
The date hierarchy works with both macOS Georgia and Linux DejaVu metrics, candidate fitting uses
real two-dimensional collision geometry, and the complete production-font corpus audit preserves
all 1,440 display-safe minute pools. The visual design did not change.

**Renderer visual design is frozen. Further work belongs to Phase 4B device scheduling and power
lifecycle.**

## CI regression and fix

Commit `0c457a6` failed GitHub Actions in
`test_landscape_date_is_quiet_and_does_not_collide`: Linux selected a 29 px DejaVu Serif date and
a 29 px attribution. The PW4 profile combined `minimum_attribution_scale = 0.019` with the larger
`minimum_date_scale = 0.020`, so the configured floors contradicted the intended hierarchy.

The source sizing rule—not the assertion—was corrected. A visible date is now the smaller of its
desired size and `attribution_size - 1`, bounded to at least one pixel. The PW4 minimum date scale
was reduced from 0.020 to 0.018, keeping the minimum readable while making the profile internally
consistent. The invariant remains strict on every font platform:

```text
date_font_size < attribution_font_size
```

The immediate correction commit `6ca2199` passed GitHub Actions. This cleanup retains that fix and
adds coverage for the minimum readable size and geometry.

## Frozen production path

`PW4_V1_RENDER_CONFIG` defines:

- PW4 native landscape, 1448 × 1072 at 300 ppi;
- renderer-owned short date on;
- Georgia body and attribution;
- Apple Chancery time accent;
- `picturesque` time emphasis;
- book-title/author attribution;
- crisp 1-bit threshold output;
- no standalone clock and no system UI.

The authoritative host command is:

```bash
export LITCLOCK_TIME_FONT='/local/path/to/Apple Chancery.ttf'
uv run litclock render-pw4-v1 16:37 --preview --output /tmp/litclock-frame.png
```

Omitting the time uses the current local minute. `--preview` avoids changing selector history. The
JSON sidecar records `production_preset: pw4-v1`. Generic CLI defaults remain unchanged.

No font binary or absolute production-Mac path is committed. Production resolves Georgia by its
stable family name and reads the local Apple Chancery file from `LITCLOCK_TIME_FONT`. Missing,
unloadable, or wrong-family accents fail clearly; the preset never silently falls back while
claiming the frozen look. CI injects an installed test face into a test-only contract and does not
require Apple Chancery.

## Date collision correction

The former candidate gate rejected whenever the date's bottom edge reached the proposed body top.
That one-dimensional test could shrink or excerpt a quote even when date and body were horizontally
separate. Candidate evaluation now measures the rendered body width—including bounded
justification, accent metrics, and stroke—and rejects only when the date and body rectangles
intersect in both axes. Final diagnostics use the same rectangle predicate. The approved `(72,
54)` date position is unchanged.

## PW4 audit before and after

| Metric | Before | After |
|---|---:|---:|
| Unique selectable quotes | 7,091 | 7,091 |
| `DISPLAY_SAFE_FULL` | 7,074 | 7,074 |
| `DISPLAY_SAFE_EXCERPT` | 14 | 14 |
| `REJECT_DIRTY` | 3 | 3 |
| Other rejections | 0 | 0 |
| Display-safe relationships | 8,727 | 8,727 |
| Display-safe minutes | 1,440 | 1,440 |

No quote moved from excerpt to full or full to excerpt. Excerpt IDs remain `394`, `769`, `967`,
`1196`, `1225`, `1649`, `2916`, `2946`, `2969`, `3644`, `3722`, `4029`, `4136`, and `4624`.
Dirty IDs remain `327`, `4950`, and `4953` and cannot reach a bitmap.

After cleanup, body font size is 38 / 62 / 81 px minimum/median/maximum. Maximum body lines are 10;
attribution maximum is three lines. Highlight wraps are 2, with zero pathological wraps. There are
zero clipping failures, date collisions, under-minimum bodies, over-limit bodies, or oversized
attributions.

## Validation

- Focused Phase 4 renderer tests: PASS.
- Production contract smoke frame: 1448 × 1072, mode `1`, Georgia + Apple Chancery, 1-bit
  threshold, date on, picturesque, no clipping/collision.
- Full pytest: PASS — 352 tests.
- Ruff check/format: PASS.
- SQLite integrity: `ok`; foreign-key violations: 0.
- Deterministic PW4 QA: 32 / 32 frames; clipping/layout failures: 0 / 0; unsupported
  glyphs: none.
- GitHub Actions: pending final push.

The remote result will be updated after the final push.
