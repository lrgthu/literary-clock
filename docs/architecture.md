# Architecture

Literary Clock separates corpus truth, display eligibility, selection state, and rendering. A
rendering decision never changes quotation wording or time semantics.

```text
source acquisition
      ↓
source-specific extraction
      ↓
shared time parser + highlight validation
      ↓
canonical quote + provenance
      ↓
quote-minute eligibility relationships
      ↓
persistent shuffle-bag selector
      ↓
device-specific presentation and renderability gate
      ↓
metric-driven layout
      ↓
grayscale or 1-bit Pillow bitmap
```

## Corpus layer

`src/litclock/db.py` owns the SQLite schema. Importers preserve each source record in provenance
even when several sources merge into one canonical passage. Standard Ebooks, Project Gutenberg,
and Wikisource have independent acquisition/extraction modules but share `timeparse.py` for time
semantics. Approximate times, ranges, citations, ratios, and untrustworthy highlight spans do not
become selectable entries.

`quote_time_semantics` records what the text says. `quote_minute_eligibility` records the real-world
minutes at which that canonical quote may appear. This avoids cloning a literary passage merely
because an unresolved 12-hour expression can serve two clock moments.

## Selection layer

`selector.py` maintains one shuffle bag per minute and global display history in SQLite. Exact
quotes are preferably held out for 24 hours, books for 12 hours, and authors for 6 hours. These are
soft constraints that relax when a sparse bucket has no alternative. A quote shared by AM/PM pools
has one global ID, so displaying it in one pool affects the other.

## Rendering layer

`RenderQuote` explicitly carries canonical and display fields. Presentation-only title/author
cleanup and sentence-aligned excerpt construction never update the corpus. The PW4 renderability
gate rejects dirty serialization, corrupt excerpts, unreadable length, attribution overflow, and
layout failure before a bitmap is produced. A rejected candidate is not recorded as displayed;
selection continues within the same minute's display-safe pool.

`LayoutEngine` uses actual font metrics for highlight-aware wrapping and attribution ellipsis.
`PillowRenderer` consumes the resulting geometry and emits deterministic grayscale or 1-bit PNGs.

## Standalone Kindle runtime layer

Phase 4B keeps the frozen Pillow layout on the Mac build side and deploys one deduplicated 1-bit
bitmap per display-safe canonical quote. A compact TSV manifest maps each minute to quote IDs and
each quote ID to a bitmap plus hashed book/author identities. Separate tiny pre-rendered date
overlays avoid both Kindle-side typesetting and quote-by-date asset multiplication.

The Kindle runtime is POSIX shell plus BusyBox and FBInk. It captures local epoch, date, minute, and
date key in one `date` call, so Kindle local time is the only clock and timezone authority. It uses
per-minute shuffle bags and bounded global history, draws the bitmap, and commits state atomically
only after FBInk succeeds. The Mac and network are absent from this path.

The finite pilot used KindleCron's RTC-aware scheduler directly from user storage. No boot hook or
permanent daemon has been enabled. A deep-sleep-compatible fullscreen lifecycle remains a Phase 4B
deployment question; the bounded framework-suspension technique keeps this firmware active and is
not accepted as the final power architecture. See [`../kindle/README.md`](../kindle/README.md) and
the [Phase 4B report](reports/PHASE4B_STANDALONE_RUNTIME_REPORT.md).

## Repository and local data boundary

Source code, tests, documentation, pinned acquisition logic, and curated Markdown report snapshots
belong in Git. Third-party datasets, ebook caches, dumps, SQLite databases, candidate queues, and
preview bitmaps are rebuildable local state and are excluded. See [`../data/README.md`](../data/README.md).
