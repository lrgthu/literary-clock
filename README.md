# Literary Clock

[![CI](https://github.com/lrgthu/literary-clock/actions/workflows/ci.yml/badge.svg)](https://github.com/lrgthu/literary-clock/actions/workflows/ci.yml)

`literary-clock` is a Kindle-oriented literary clock that tells time through quotations from
literature. Each frame displays one literary quotation containing the current time expression,
with that phrase emphasized inside the author's original wording and quiet book/author attribution
below it.

The primary device is a **Kindle Paperwhite 4 / 10th Generation**, rendered natively at
**1448 × 1072 landscape, 300 ppi**. Portrait remains supported. PW4 landscape includes an optional
quiet renderer-owned short date in the upper-left; there is no standalone digital clock, weather,
dashboard, iconography, border, Kindle status bar, or decorative interface.

## Current V1 status

- All 1,440 minutes have at least one effective display candidate.
- The local operational corpus contains 7,091 unique selectable literary quotes and 8,730
  quote-minute eligibility relationships.
- Neutral exact 12-hour expressions can serve both matching AM and PM clock moments.
- Selection uses persistent per-minute shuffle bags, 24-hour global quote cooldown, 12-hour book
  cooldown, and 6-hour author cooldown.
- The PW4 landscape audit leaves 8,727 display-safe relationships and zero empty display pools.
- Corpus expansion is frozen for V1; 97 accepted sparse-tail exceptions remain below three
  candidates.

Exact phase snapshots and renderer measurements are in [`docs/reports/`](docs/reports/).
Those V1 figures are the pre-audit baseline. The English semantic revalidation documented in
[`CORPUS_SEMANTIC_REVALIDATION_REPORT.md`](docs/reports/CORPUS_SEMANTIC_REVALIDATION_REPORT.md)
reduces the validated selectable view rather than preserving coverage with questionable records.

The frozen renderer produces reusable quote frames on the build Mac rather than typesetting on the
Kindle. A bounded Phase 4B pilot and two-hour power study have proved a tiny, offline Kindle-native
selector: it reads the Kindle's local clock, chooses a pre-rendered frame, displays it with FBInk,
and owns persistent history without a Mac or network. No indefinite scheduler or boot hook is
enabled by deployment. Weather,
dashboards, clock icons, and other ambient-display features remain out of scope: the screen is a
date marginal note, one quotation, its inline time phrase, and discreet literary attribution.

## Architecture

```text
legacy corpora + Standard Ebooks + Project Gutenberg + English Wikisource
                              ↓
                  normalization + provenance
                              ↓
                       time semantics
                              ↓
                 quote-minute eligibility
                              ↓
              selector + persistent anti-repeat state
                              ↓
                    device renderability gate
                              ↓
              Pillow bundle builder (Mac, offline)
                              ↓
             TSV manifest + deduplicated 1-bit assets
                              ↓
       Kindle clock + shell selector + transactional history
                              ↓
                 FBInk display + RTC-aware scheduling
```

The project is deliberately small and uses Python 3.11+, SQLite, PyYAML for the one authoritative
YAML source, `mwparserfromhell` for structural Wikisource cleanup, pytest, ruff, and the standard
library everywhere else.

- `src/litclock/importers/` pins, fetches, parses, and merges upstream corpora.
- `src/litclock/normalize.py` parses clock times, normalizes text, and recovers highlight offsets.
- `src/litclock/db.py` owns the SQLite schema.
- `src/litclock/stats.py` calculates full-day coverage and writes JSON, CSV, and Markdown reports.
- `src/litclock/selector.py` implements persistent per-minute shuffle bags and soft attribution
  cooldowns.
- `src/litclock/render/models.py` defines the renderer-only `RenderQuote` contract, explicitly
  separates canonical fields from display fields, and rejects unsafe highlight offsets.
- `src/litclock/render/presentation.py` detects serialized/multi-record contamination, normalizes
  catalog attribution for display only, and constructs exact sentence-aligned excerpts.
- `src/litclock/render/layout.py` performs adaptive, highlight-aware wrapping with real font
  metrics and proportional optical composition.
- `src/litclock/render/pillow_renderer.py` produces deterministic grayscale and 1-bit PNG frames;
  selection logic is deliberately absent from the renderer.
- `src/litclock/render/production.py` defines the fail-closed `pw4-v1` production contract used by
  the bundle builder.
- `src/litclock/bundle.py` pre-renders deduplicated quote frames and tiny reusable date overlays,
  calculates storage projections, and writes the compact Kindle manifest.
- `src/litclock/runtime_bundle.py` owns the line-oriented manifest and deployment-integrity rules.
- `src/litclock/deploy.py` stages and validates a versioned bundle on USB-visible Kindle storage,
  atomically activates it, and retains the previous version for rollback.
- `src/litclock/render/profiles.py` contains proportional profiles for early Kindle, Paperwhite,
  Basic 11, Paperwhite 5/11, Oasis, explicit PW4 portrait/landscape, and custom dimensions.
- `src/litclock/standard_ebooks.py` indexes Standard Ebooks on GitHub, performs resumable sparse
  acquisition, and records OPF metadata, commit IDs, and content checksums.
- `src/litclock/xhtml.py` extracts semantic prose and exact source offsets from XHTML.
- `src/litclock/timeparse.py` detects and conservatively resolves explicit literary time phrases.
- `src/litclock/semantic.py` deterministically distinguishes clock-time grammar from durations,
  references, scores, ratios, identifiers, and context-poor lexical matches.
- `src/litclock/semantic_audit.py` revalidates every existing English quote-minute relationship,
  exports review evidence, and atomically activates a reversible audited selection view.
- `src/litclock/mining.py` scores candidates, deduplicates passages, prioritizes sparse minutes,
  manages the review queue, and performs capped high-confidence imports.
- `src/litclock/phase2a5.py` audits candidate disposition, runs the evidence-recording contextual
  AM/PM pass, exports sparse-minute review packets, and performs controlled recovery imports.
- `src/litclock/gutenberg.py` indexes the official Project Gutenberg bulk catalogs and performs
  resumable, checksum-tracked plain-text acquisition from official rsync mirrors.
- `src/litclock/gutenberg_text.py` strips Gutenberg boilerplate and extracts prose paragraphs while
  retaining source offsets.
- `src/litclock/gutenberg_mining.py` streams books through the shared time parser, rejects
  source-specific false positives, deduplicates across all corpora, imports only into deficient
  buckets, and produces the Phase 2B report and review queue.
- `src/litclock/phase2c.py` separates textual clock-face semantics from display eligibility,
  audits ambiguous candidates, plans capped counterfactuals, and activates shared AM/PM minute
  relationships only after the counterfactual is saved.
- `src/litclock/wikisource.py`, `wikisource_text.py`, and `wikisource_mining.py` acquire an official
  English Wikisource multistream dump, parse XML and wiki markup structurally, quarantine uncertain
  licenses, and target only minute pools below three.
- `src/litclock/phase2d.py` freezes the exact sparse-tail target set, recovers retained candidates,
  exports the focused review queue, and produces the Phase 2D report.
- `data/third_party/` holds checksum-verified upstream snapshots and their provenance files.
- `data/public_domain/standard_ebooks/` holds the source index and manifest. Its ignored `books/`
  cache contains sparse working copies and is not part of the main Git history.
- `data/public_domain/gutenberg/` holds the ignored bulk catalogs, resumable text cache, and source
  manifests. Full-run texts may be pruned after checksums and candidates are committed.
- `data/public_domain/wikisource/` is an ignored local cache for the official compressed XML dump,
  multistream index, checksum manifest, and disposable target-page spools. The dump is never
  expanded permanently or added to normal Git history.
- `data/generated/` holds the rebuildable SQLite database and coverage outputs.
- `data/local/` is ignored scratch space for future locally mined material.

See [architecture](docs/architecture.md), [corpus design](docs/corpus.md),
[renderer behavior](docs/renderer.md), [Kindle runtime helpers](kindle/README.md), and the
[Phase 4B standalone runtime report](docs/reports/PHASE4B_STANDALONE_RUNTIME_REPORT.md) for focused
design documentation.

The normalized `quotes` table has one canonical row per distinct literary passage. Textual meaning
is recorded separately in `quote_time_semantics`, while `quote_minute_eligibility` maps that one
quote to one or more legitimate display minutes. An unresolved exact 12-hour clock-face expression
can therefore have AM and PM relationships without duplicating the canonical quote. Explicit
meridiem or deterministic source context keeps only the resolved side. Every upstream row that
merged into a canonical quote remains in `quote_provenance`; invalid-time and malformed rows remain
in `import_issues`. `import_runs` and `sources` make each build auditable, while `shuffle_state` and
`display_history` provide selection persistence.

Highlight validation has five corpus statuses:

- `VERIFIED_EXACT`: one literal occurrence of `time_text` in the display quote.
- `VERIFIED_NORMALIZED`: one occurrence after Unicode/case/punctuation/spacing normalization,
  with offsets mapped back into the display quote.
- `AMBIGUOUS`: more than one plausible occurrence; no offsets are trusted.
- `TIME_TEXT_NOT_FOUND`: no plausible occurrence; no offsets are trusted.
- `INVALID_TIME`: the source time is not a valid 24-hour `HH:MM` value and the row is quarantined.

Only the two verified statuses are automatically selectable.

Highlight verification is necessary but not sufficient. An activated English semantic audit also
requires the highlighted phrase to derive the claimed minute under accepted clock grammar and its
context not to force a duration, reference, score, ratio, measurement, identifier, or structural
reading. Unknown cases are REVIEW and are excluded from the precision-first selectable view; no
canonical quote or provenance record is deleted.

## Upstream corpora and licensing

The initial corpus imports pinned snapshots from:

1. [kapoorankush/litclock](https://github.com/kapoorankush/litclock), whose assembled quote
   database is distributed upstream under CC BY-NC-SA 4.0. Its MIT license applies to its software,
   not its quote database.
2. [zenbuffy/LiteraryClock](https://github.com/zenbuffy/LiteraryClock). The repository has no
   explicit root corpus license and credits substantial data derived from the JohsEnevoldsen
   corpus. This project records the license as `NOASSERTION`; no permission is implied here.
3. [JohsEnevoldsen/literature-clock](https://github.com/JohsEnevoldsen/literature-clock), licensed
   upstream under CC BY-NC-SA 2.5 Generic.

The Zenbuffy YAML is used instead of its derived CSV because the YAML is current and structurally
preserves multiline records; the current CSV has unescaped physical newlines and cannot be parsed
without conflating quote fragments with records.

Phase 2A mines the source repositories published by
[Standard Ebooks](https://github.com/standardebooks). The pipeline reads only
`src/epub/text/*.xhtml`, `src/epub/content.opf`, and repository license files. Standard Ebooks'
contributors dedicate their contributions through CC0; each repository states that its source
text and artwork are believed to be in the United States public domain and warns that other
jurisdictions may differ. Commit, repository, rights, and checksum metadata are retained per book.
Project Gutenberg is intentionally not used in Phase 2A. Phase 2B independently mines only works
whose RDF rights field is exactly `Public domain in the USA.`; it does not treat permission-only
works as public domain. Catalog and literary-filter metadata, source identifiers, checksums, and
acquisition timestamps remain attached to each book and candidate.

Phase 2D uses the official English Wikisource database dumps, not individual web pages. Wikisource
is a mixed-rights collection: automatic imports require work-level public-domain evidence, while
unknown or freely licensed material is quarantined for review. Each accepted quote records its
page title, revision, dump date, source URL, work metadata, license evidence, and source locator.

Our source code is MIT-licensed. Third-party corpus files, the normalized database, and
corpus-derived reports are **not thereby relicensed as MIT**. The normalized database and bulk
source datasets are intentionally not published in this Git repository. See [NOTICE.md](NOTICE.md)
for exact commits, source paths, and license cautions. No font binaries are stored here.

## Install

Install [`uv`](https://docs.astral.sh/uv/) and then create the locked project environment:

```bash
uv sync
```

`uv` reads `.python-version` and can provision a compatible Python automatically.

## Fetch and import

Download and checksum-verify the pinned source files:

```bash
uv run litclock fetch
```

Build a fresh database atomically at `data/generated/litclock.sqlite3`:

```bash
uv run litclock import
```

An existing database is replaced only after the complete new import succeeds. Use `--db PATH` on
`import`, `stats`, or selection commands to work with another SQLite file.

## Coverage analysis

```bash
uv run litclock stats
```

This prints the core coverage metrics and writes:

- `data/generated/coverage.json`
- `data/generated/minute_coverage.csv`
- `data/generated/COVERAGE_REPORT.md`

Coverage metrics and thresholds count effective quote-minute eligibility relationships backed by
selectable quotes with verified highlight offsets. Reports distinguish canonical literary quotes,
unique selectable quotes, and quote-minute relationships; a shared quote counts once in the first
two totals but once in each legitimate minute pool. The canonical total also includes retained
ambiguous and unmatched records. The report ranks mining targets by effective candidate count,
canonical candidate count, author diversity, book diversity, and time. The machine-readable minute
report includes all 1,440 rows.

## Revalidate English clock-time semantics

Run the audit before mutation, inspect the ignored review artifacts, then explicitly activate the
reviewed run:

```bash
uv run litclock semantic-audit
uv run litclock semantic-report --run-id RUN_ID
uv run litclock semantic-apply RUN_ID
uv run litclock semantic-report --run-id RUN_ID
```

The first command classifies all underlying selectable English relationships without changing the
active minute pool. Detailed CSVs under `data/generated/semantic-audit/` include quotation context
and are deliberately ignored. Activation filters QUARANTINE and unresolved REVIEW decisions from
`quote_minute_pool` while preserving the canonical quote, source provenance, and full versioned
decision record. It does not mine or manufacture replacements.

## Mine Standard Ebooks

Start with a bounded sample. Completed books and cached downloads are skipped on later runs:

```bash
uv run litclock mine-standard-ebooks --limit-books 20
uv run litclock mine-standard-ebooks --limit-books 100 --workers 8
```

The catalog is discovered from the Standard Ebooks GitHub organization rather than a hardcoded
title list. Acquisition uses shallow, blob-filtered sparse clones with retry/backoff. The manifest
at `data/public_domain/standard_ebooks/manifest.jsonl` records repository URL, commit, metadata,
rights, timestamps, processing state, counts, and checksums. Use `--refresh-catalog` to update the
cached repository index and `--reprocess` to re-run indexed books after parser changes.

The miner parses XHTML structurally, excludes identifiable front/back matter and navigation, and
preserves file, section, paragraph, and character locators. Exact 24-hour or strongly resolved
AM/PM candidates with clean context and exact highlights can be imported; ambiguous, approximate,
ranged, malformed, and lower-quality passages remain reviewable.

```bash
uv run litclock mining-stats
uv run litclock review-export
uv run litclock import-mined --confidence high
uv run litclock stats
```

High-confidence import dynamically favors sparse buckets and author/book diversity, and stops
adding to a minute once it has seven selectable quotes. The generated Phase 2A comparison is
`data/generated/PHASE2A_REPORT.md`. The CSV review export is intentionally ignored because it is a
large rebuildable artifact.

## Audit and recover existing Standard Ebooks candidates

Phase 2A.5 does not fetch another corpus. It accounts for every original high-confidence candidate,
simulates capped counterfactual imports, and revisits only AM/PM-ambiguous candidates whose two
possible buckets are not already full:

```bash
uv run litclock phase2a5
```

The contextual resolver automatically accepts only a daypart directly linked to the time phrase in
its containing sentence or deterministic elapsed-time arithmetic from a nearby explicit absolute
time. It stores the evidence and source locator for every automatic decision. Weak narrative
continuity remains unresolved. Use `--audit-only` to generate the analysis without importing newly
resolved candidates.

The command regenerates coverage files and writes `PHASE2A5_CANDIDATE_AUDIT.csv`,
`PHASE2A5_AUDIT.md`, `PHASE2A5_REVIEW_PRIORITY.csv`, and `PHASE2A5_REPORT.md` under
`data/generated/`. By default, it also copies the final report to
`~/Desktop/LITERARY_CLOCK_PHASE2A5_REPORT.md`; override that path with `--desktop-report PATH`.

## Mine Project Gutenberg

The Gutenberg pipeline follows the project's bulk-access guidance: it downloads the compressed
CSV and RDF catalogs and selectively retrieves generated UTF-8 plain text from official rsync
mirrors. It never crawls normal ebook pages. Acquisition and processing are restartable; the
SQLite book table is the live manifest and `data/public_domain/gutenberg/books_manifest.jsonl` is
an exported snapshot. `target_expressions.json` records the exact parser-supported variants and
live deficit for every target minute. Catalog feeds, working texts, and the large database remain
outside normal Git history.

Run the deterministic staged rollout separately:

```bash
uv run litclock mine-gutenberg --stage pilot-a --workers 4
uv run litclock mine-gutenberg --stage pilot-b --workers 8
uv run litclock mine-gutenberg --stage full --workers 10
```

Or run all three stages without a manual gate:

```bash
uv run litclock mine-gutenberg --stage all
```

Pilot A is cumulative to about 500 eligible books, Pilot B to about 5,000, and the full stage scans
all eligible indexed books. The full stage prunes only successfully processed raw texts by default
to bound disk usage; pass `--keep-text-cache` when sufficient storage is available. A resumed run
does not download or process books already marked complete.

The semantic parser is shared with Standard Ebooks. Gutenberg-specific extraction removes the
header/footer license and rejects catalog, citation, ratio, timetable, legal-reference, and other
nonliterary patterns. Automatic import requires explicit U.S. public-domain rights metadata,
English literary metadata, an exact unambiguous minute, exact highlight offsets, clean context,
cross-source novelty, and a target bucket below seven.

```bash
uv run litclock gutenberg-stats
uv run litclock gutenberg-review-export
uv run litclock import-gutenberg
uv run litclock gutenberg-revalidate
```

`gutenberg-review-export` includes unresolved AM/PM candidates only when either possible bucket is
below three. Reports are written to `data/generated/PHASE2B_REPORT.md`,
`PHASE2B_REVIEW_PRIORITY.csv`, and `PHASE2B_EMPTY_MINUTE_AUDIT.csv`; the ordinary coverage JSON,
CSV, and Markdown files are regenerated after import. `gutenberg-revalidate` reapplies the current
rights, metadata, parser, and prose gates to a resumable scan, safely revokes any obsolete imports,
and refills sparse buckets only with candidates that still pass every gate.

## Activate shared 12-hour clock-face eligibility

Phase 2C changes only the AM/PM policy for an exact, unresolved 12-hour expression. A phrase such
as `3:46`, `a quarter past three`, or `nineteen minutes past four` may serve both corresponding
clock moments when neither the displayed excerpt nor trusted source context establishes a
daypart. Explicit AM/PM, directly linked daypart wording, and deterministic local temporal evidence
remain authoritative. Approximate times, ranges, false positives, duplicates, bad highlights,
invalid provenance, and low-quality context remain excluded.

The counterfactual must be created before activation:

```bash
uv run litclock phase2c-counterfactual
uv run litclock phase2c-activate
uv run litclock phase2c-report
```

Scenario B fills deficient minute pools with quality-ranked relationships; Scenario C also prefers
new authors and books, then relaxes diversity preferences when needed. Both stop at seven effective
candidates per minute. Outputs include `PHASE2C_COUNTERFACTUAL.md`, `PHASE2C_REPORT.md`,
`PHASE2C_1546_AUDIT.csv`, and a sparse-bucket-only `PHASE2C_CONTEXT_REVIEW.csv`.

## Target the Phase 2D sparse tail with Wikisource

Phase 2D freezes the exact pools below three, reviews retained candidates first, then scans one
official English Wikisource `pages-articles-multistream` dump. The multistream index permits
deterministic parallel decompression. Each page is scanned once; only pages whose raw expression
maps through the shared parser to a live target are rendered with a wiki-markup AST. Mainspace is
processed before `Page:` transcriptions, preventing the same passage from being counted twice.

```bash
uv run litclock phase2d-targets
uv run litclock phase2d-recover-existing
uv run litclock acquire-wikisource --dump-date YYYYMMDD
uv run litclock mine-wikisource --dump-date YYYYMMDD
uv run litclock phase2d-revalidate
uv run litclock phase2d-review-export
uv run litclock phase2d-report
```

Automatic import requires a verified U.S. public-domain work, clean literary attribution, an exact
time and highlight, cross-source novelty, and a still-active pool below three. Unknown licensing,
missing attribution, and genuinely uncertain context remain in the compact review export; citations,
ratios, logs, official documents, broken prose, and OCR corruption do not. Outputs are
`PHASE2D_TARGETS.csv`, `PHASE2D_REVIEW_PRIORITY.csv`, and `PHASE2D_REPORT.md`.

## Select and preview a quote

```bash
uv run litclock show 16:37
uv run litclock show-now
```

The terminal preview marks the stored highlight span in ANSI bold when the output is a compatible
terminal and uses Markdown `**bold**` markers otherwise. `--seed INTEGER` injects a deterministic
RNG for repeatable experiments. `--sfw-only` restricts candidates to records explicitly marked
safe; it excludes both unsafe and unknown records.

Selection uses a persistent shuffle bag for each minute. A quote does not repeat until every
currently available quote for that minute has been used. Shared relationships retain one global
quote ID, so displaying a quote from its AM pool updates the same persistent history consulted by
its PM pool. The selector first prefers not to repeat an exact quote within 24 hours, then prefers
books not shown in the past 12 hours and authors not shown in the past 6 hours, progressively
relaxing these preferences when a minute has no alternative.

## Render a literary page

Pillow is the bitmap backend. It discovers a supported local serif family (EB Garamond, Linux
Libertine, Georgia, DejaVu Serif, or Liberation Serif, in preference order) and records the exact
paths and face availability in each JSON metadata sidecar. Font files are never copied into this
repository.

The physically approved PW4 V1 path is one authoritative command. It fixes the landscape profile,
renderer-owned date, picturesque emphasis, book/author attribution, and crisp 1-bit threshold
output. Georgia is resolved by family name. Apple Chancery remains a local proprietary font and
must be supplied through the environment; a missing file or wrong embedded family fails closed.

```bash
export LITCLOCK_TIME_FONT='/local/path/to/Apple Chancery.ttf'
uv run litclock render-pw4-v1 16:37 --preview --output /tmp/litclock-frame.png
# Omit 16:37 to use the current local minute.
```

No absolute font path or font binary is stored in the repository. `--preview` makes selection
non-mutating for QA; without it, the normal persistent anti-repeat history applies. The generated
JSON sidecar records `production_preset: pw4-v1`. Generic `render`, `render-now`, and `render-id`
remain configurable and retain their existing portable defaults.

For a production-quality generic explicit family, pass all four faces:

```bash
uv run litclock render 15:46 --device pw4 --orientation landscape --preview \
  --font-regular /path/Family-Regular.ttf \
  --font-bold /path/Family-Bold.ttf \
  --font-italic /path/Family-Italic.ttf \
  --font-bold-italic /path/Family-BoldItalic.ttf
```

The four structured options are all-or-nothing, and supplied bold/italic files are checked against
their embedded style names. The backward-compatible `--font PATH` and `LITCLOCK_FONT` forms are
explicit single-face fallbacks: the CLI warns that bold time emphasis and italic attribution are
unavailable, and metadata reports those missing faces instead of pretending the regular file is a
complete family. Automatic discovery still requires and returns a complete family.

The highlighted phrase may use an independent accent family. Pass a real pair with
`--time-font-regular` and `--time-font-bold`; both paths are required and the bold face is checked
using its embedded style metadata. `--time-font PATH` is an honest single-face option: that exact
face is used, and the CLI warns when it is not actually bold. The `picturesque` treatment may add
a measured ink stroke to a single-face accent; diagnostics report both the absent bold face and
the stroke width rather than pretending a bold file exists. With no time-font option, wrapping and
drawing use the body family's bold face exactly as before. No configured font failure silently
substitutes a different family.

```bash
uv run litclock render 15:46 --device pw4 --orientation landscape --preview \
  --date 2026-09-05 --time-emphasis subtle-lift \
  --time-font-regular /path/Accent-Regular.ttf --time-font-bold /path/Accent-Bold.ttf
uv run litclock render-id 6730 --time 10:04 --device pw4 --orientation landscape \
  --mode 1bit --dither threshold --date 2026-09-05 --time-emphasis picturesque \
  --time-font '/path/to/Apple Chancery.ttf'
uv run litclock render-now --device pw4 --orientation landscape --mode 1bit --show-date
uv run litclock render-id 42 --device pw4 --orientation landscape
uv run litclock render 16:37 --device custom --width 800 --height 1200 --preview
```

The V1 primary profile is Kindle Paperwhite 4 landscape: **1448 × 1072 at 300 ppi**. Its layout is
computed natively rather than rotating a 1072 × 1448 portrait frame. `pw4_portrait` remains a
built-in profile. Earlier generations are represented accurately as `paperwhite-1-2` (758 × 1024,
212 ppi) and `paperwhite-3` (1072 × 1448, 300 ppi); the legacy `paperwhite-1-3` CLI name remains an
alias for the early 758 × 1024 profile. The landscape body uses a 38 px hard minimum, prefers 3–7
lines, soft-limits at 8, and never allows more than 10. Long passages become exact,
sentence-aligned excerpts around the highlighted time phrase instead of shrinking indefinitely.

`render` and `render-now` use the existing selector and normally persist shuffle/history state.
Add `--preview` for non-mutating visual work. `render-id` is always non-mutating; for a shared
clock-face quote, `--time HH:MM` chooses one of its eligible display relationships. Available
modes are `grayscale` and `1bit`; crisp threshold conversion is the 1-bit default, while
`--dither floyd-steinberg` exists for deliberate comparison. The clock face never includes a
separate digital time.

PW4 landscape shows the date by default as locale-independent English text such as
`Sat, Sep 5`. `--date YYYY-MM-DD` makes preview output reproducible; `--show-date` and
`--hide-date` explicitly control it. Other profiles keep the label off unless requested. The date
source is independent from quote selection and contains no clock time or Kindle system UI. Its
size is computed from the selected attribution size and is always at least one pixel smaller. The
layout candidate gate uses real two-dimensional date/body rectangles, so vertical overlap alone
does not unnecessarily shrink or excerpt a horizontally separate quote.

Physical PW4 comparison selected Georgia for body and attribution, a locally installed Apple
Chancery face for the time phrase, and `picturesque` emphasis. The highlighted characters use a
deterministic uneven baseline while preserving their order and original wording. PW4 landscape
uses bounded book-style justification: non-final lines expand only until a word gap reaches 0.5 em
and remain partially ragged rather than forming wide rivers. The font file is neither copied nor
redistributed; systems without it must provide another local accent or use the portable body-family
fallback.

Before layout, the presentation gate rejects raw serialized corpus rows, concatenated records,
and corrupt excerpt/source mappings. A rejected selection is not written to display history; the
selector tries another quote from the same minute without disturbing the rejected item in its
shuffle bag. Canonical quote/title/author values are never changed. Display-only attribution
removes life dates and catalog roles, shortens clear subtitle tails, compresses three or more
creators, and renders collections with a named editor as `Edited by …`. Title occupies at most two
lines, creator at most one, and the entire attribution at most three.

Generate the deterministic corpus-extreme visual suite and its diagnostic reports with:

```bash
uv run litclock render-qa
```

This audits every selectable quote and all 1,440 effective minute pools for PW4 landscape, then
writes `data/generated/RENDER_QA.json`, `RENDER_QA.md`,
`PHASE3_RENDER_FINALIZATION_REPORT.md`, and ignored PNG/contact-sheet artifacts under
`data/generated/render_previews/pw4_landscape/`. Every frame records canonical/display length,
excerpt offsets, font families/sizes, line counts, attribution transformations,
body/attribution/date bounds, occupancy, highlight wrapping, clipping, fallback-font use, output
mode, and unsupported glyphs. It also emits a curated Phase 4A.3 date/time-font contact sheet.

## Build the standalone PW4 asset bundle

Phase 4B preserves the validated typography by pre-rendering it on the Mac. Each display-safe
canonical quote has one 1072 × 1448 CCW transport PNG regardless of how many minute pools reference
it. A compact TSV manifest maps all 1,440 local minutes to those quote IDs. Dynamic dates are tiny
separate 1-bit overlays, so the runtime does not need Pillow and the bundle does not multiply every
quote by every calendar date.

The builder uses the frozen `pw4-v1` contract and fails if the locally configured Apple Chancery
accent is unavailable:

```bash
export LITCLOCK_TIME_FONT='/local/path/to/Apple Chancery.ttf'

# Render a bounded sample and estimate full deployment size.
uv run litclock build-pw4-bundle \
  --output data/generated/pw4-bundle-estimate \
  --estimate-only --sample-size 200

# Build every display-safe frame and all reusable date overlays.
SOURCE_DATE_EPOCH=1788712440 uv run litclock build-pw4-bundle \
  --output data/generated/pw4-v1-001
```

`SOURCE_DATE_EPOCH` makes the manifest timestamp reproducible. The bundle contains no font file,
SQLite database, Python dependency, proprietary path, or duplicate image for shared AM/PM
relationships. Generated assets are ignored because quotations retain their separate source
licenses.

Deploying is a separate, non-scheduling operation:

```bash
uv run python scripts/deploy_pw4_bundle.py \
  --mount /Volumes/Kindle \
  --bundle data/generated/pw4-v1-001 \
  --release-version literary-clock-v1 \
  --production-release

uv run python scripts/deploy_pw4_bundle.py --mount /Volumes/Kindle --rollback
```

The deployer verifies source and copied assets, available storage, dimensions, crisp 1-bit mode,
and SHA-256 values before atomically switching the `current-release` pointer. The Kindle runtime
uses a static ARMv7 C one-shot engine, POSIX-shell service/recovery helpers, FBInk, and an optional
official KindleCron binary. It captures epoch, calendar date, and minute from one Kindle-local
timestamp and contains no timezone database. History is committed only after FBInk returns success.
The shell engine remains a state-compatible fallback. Code, assets, release metadata, and the
controlled one-shot reboot helper share one checksummed release, so rollback cannot combine new
runtime code with old manifest data.

The bounded pilot changed frames autonomously across eight consecutive local minutes and followed
a user-initiated one-hour Kindle timezone change without a Mac, network, configuration change, or
replay of missed minutes. A later two-hour exact-mode run completed 121 displays, used no Wi-Fi,
and moved the coarse battery reading from 100% to 97%, but necessarily prevented suspend to remain
minute-accurate. Deep-sleep tests at two, three, and five minutes were not reliably recurring on the
tested PW4/firmware; a long five-minute target became stale until USB wake. See
[`kindle/README.md`](kindle/README.md) for recovery commands and
[`PHASE4B2_POWER_LIFECYCLE_REPORT.md`](docs/reports/PHASE4B2_POWER_LIFECYCLE_REPORT.md) for the
measured cadence matrix and current deployment boundary.

The final appliance uses a persistent, checksummed KMC user-storage hook. It resolves the active
release, validates it before the framework is paused, and calls the idempotent exact-mode service:

```bash
uv run python scripts/deploy_pw4_bundle.py \
  --mount /Volumes/Kindle --enable-production-autostart
uv run python scripts/deploy_pw4_bundle.py \
  --mount /Volumes/Kindle --disable-production-autostart
```

The reversible reboot-test hook remains available for bounded qualification work and self-disarms
before launch:

```bash
uv run python scripts/deploy_pw4_bundle.py --mount /Volumes/Kindle --enable-boot-hook
uv run python scripts/deploy_pw4_bundle.py --mount /Volumes/Kindle --disable-boot-hook
```

## Development

```bash
uv run pytest
uv run ruff check .
```

The third-party inputs and reports are small enough to inspect. The rebuildable SQLite database,
virtual environment, caches, and local scratch corpus are ignored rather than committed.

## Roadmap

- Phase 1 — corpus core: reproducible import, validation, deduplication, coverage, and selection.
- Phase 2A — deterministic Standard Ebooks mining and conservative automatic acceptance.
- Phase 2A.5 — high-confidence accounting and evidence-backed recovery from ambiguous detections.
- Phase 2B — coverage-driven Project Gutenberg mining with conservative cross-source import.
- Phase 2C — shared 12-hour clock-face semantics, multi-minute eligibility, and global quote
  cooldowns.
- Phase 2D — targeted retained-candidate recovery and official English Wikisource dump mining for
  the below-three sparse tail, stopping automatic imports as each pool reaches three.
- Phase 3 — device-independent literary page layout, bitmap rendering, and visual QA.
- Phase 4A — physical PW4 renderer validation and frozen production typography.
- Phase 4B.1 — bounded offline Kindle runtime pilot, transactional history, and local-time test.
- Phase 4B.2 — measured exact/eco power behavior, release integrity, reversible startup, and bounded
  reboot recovery; indefinite 24/7 activation remains an explicit post-test decision.
- Phase 4C — static ARMv7 native one-shot runtime, checksummed engine selection, and preserved shell
  fallback on the frozen English V1 bundle.
- Phase 4D — 25-hour offline appliance qualification across midnight and the history-retention
  boundary. Literary Clock V1 is complete.
