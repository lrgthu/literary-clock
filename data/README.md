# Local Data Layout

The Git repository intentionally contains no normalized quote database or downloaded literary
corpus. A complete local working tree can exceed 20 GB, while the reviewable software repository
should remain small and normally cloneable.

## Tracked

- This data-boundary document.
- `local/.gitkeep`, which preserves the scratch directory shape.
- Curated aggregate report snapshots under [`../docs/reports/`](../docs/reports/).

## Downloaded and excluded

- `third_party/`: pinned legacy Literary Clock source files and their upstream notices.
- `public_domain/standard_ebooks/books/`: shallow/sparse book repositories.
- `public_domain/gutenberg/`: bulk catalogs, manifests, and downloaded plain texts.
- `public_domain/wikisource/`: compressed XML dump, multistream index, and processing spool.

The legacy fetcher pins upstream commits and SHA-256 checksums in source code. Public-domain
acquisition manifests record source URLs, revisions/dump dates, rights evidence, and checksums
locally. These downloaded files retain their own licenses and are not covered by the repository's
MIT license.

## Generated and excluded

- `generated/litclock.sqlite3`: operational normalized corpus and selector state.
- Coverage JSON/CSV, candidate audits, review queues, and mining statistics.
- `generated/render_previews/`: grayscale/1-bit frames and contact sheets.
- `generated/pw4-*` and `generated/phase4b-*`: storage samples, versioned Kindle asset bundles,
  downloaded local scheduler releases, and physical-pilot evidence.
- `local/`: database snapshots, failed-run recovery files, and experiments.

Human-readable phase reports are copied to `docs/reports/` as curated snapshots. Large
machine-readable reports can contain quotation text or candidate context and remain local.

## Rebuild workflow

Install dependencies, fetch the three pinned legacy sources, build the initial database, and
generate coverage reports:

```bash
uv sync
uv run litclock fetch
uv run litclock import
uv run litclock stats
```

Later corpus stages use resumable commands documented in the root README. They depend on official
bulk sources and can require substantial disk, network, and processing time. The currently frozen
V1 database incorporates completed Standard Ebooks, Project Gutenberg, and Wikisource phases; a
fresh `litclock import` rebuilds Phase 1, not the already-mined final V1 state.

Renderer QA requires an existing operational database:

```bash
uv run litclock render-qa
```

This regenerates the ignored local previews and reports. Do not add them to Git; update the
curated Markdown snapshot in `docs/reports/` deliberately after reviewing it.

Standalone Kindle bundles also require the frozen local production accent font:

```bash
export LITCLOCK_TIME_FONT='/local/path/to/Apple Chancery.ttf'
uv run litclock build-pw4-bundle \
  --output data/generated/pw4-v1-001
```

The resulting frame assets contain literary quotations and stay ignored. Only the builder,
manifest format, runtime, deployment tools, tests, and sanitized aggregate report are published.
