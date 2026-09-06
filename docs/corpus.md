# Corpus Architecture

## Canonical quotes and display relationships

A canonical quote is one distinct literary passage with its original text, title, author,
highlight offsets, validation status, and source provenance. Deduplication merges exact and
punctuation/whitespace-equivalent copies while retaining every contributing source record.
Language is part of quote identity. Canonical rows use stable `en`, `fr`, or `zh` codes (existing
`en-GB`/`en-US` rows remain in the English family), and Chinese provenance may additionally retain
`zh-Hans` or `zh-Hant`. No ingest path translates text, strips French diacritics, expands `œ`, or
converts Chinese script. Comparison-only hashes are stored separately from the original quote.

A quote-minute eligibility relationship answers a different question: at which clock minute may
that quote be displayed honestly? One canonical quote can have more than one relationship, so
coverage counts must distinguish:

- canonical literary quotes;
- unique selectable quotes;
- quote-minute eligibility relationships;
- effective candidates in each minute pool.

## Shared 12-hour clock-face semantics

An exact neutral phrase such as “three forty-six” specifies 03:46/15:46 on an ordinary 12-hour
clock face but does not claim morning or afternoon. When neither the displayed text nor trusted
surrounding context establishes a daypart, the same canonical quote may therefore have
`CLOCKFACE_SHARED_AM` and `CLOCKFACE_SHARED_PM` relationships.

Explicit `a.m.`/`p.m.` or deterministic wording such as “in the morning” or “that evening” keeps
only the corresponding side. Strong source context is authoritative even if it is omitted from a
short display excerpt; vague narrative intuition is not.

No second canonical row is created for the other clock moment. Global quote history consequently
prevents the shared passage from behaving like two independent quotations.

## Correctness gates

Automatically selectable passages require an exact minute, trustworthy source provenance, valid
highlight offsets, and clean literary context. The shared parser conservatively rejects or
quarantines:

- approximate expressions such as “about five” or “shortly after midnight”;
- time ranges;
- unresolved expressions with conflicting daypart evidence;
- chapter/verse, legal, ratio, score, dimension, page, and line references;
- metadata, timetables, broken OCR, and nonliterary fragments.

Problems remain auditable rather than being silently discarded.

French and Chinese mining add four explicit review classes: `HIGH`, `MEDIUM`,
`AMBIGUOUS_CLOCKFACE`, and `REJECT`. Only novel `HIGH` candidates with one resolved minute are
eligible for the conservative multilingual import. In particular, an otherwise valid French
`trois heures` or Chinese `三點鐘` records both clock-face possibilities but does not create two
minute-eligibility rows automatically.

## Multilingual acquisition and staged mining

[`../corpus/multilingual_sources.json`](../corpus/multilingual_sources.json) pins bounded Project
Gutenberg pilots by URL and SHA-256. [`../corpus/wikisource_dump_pins.json`](../corpus/wikisource_dump_pins.json)
records the audited 2026-09-01 French and Chinese Wikisource dump identities and Wikimedia SHA-1
values; those multi-gigabyte inputs are intentionally deferred until the parser pilots pass
manual precision review. Downloaded text, acquisition receipts, candidate context, review CSVs,
and databases remain ignored.

The reproducible stage sequence is:

```bash
# FR-0 / ZH-0: acquire the checksum-pinned bounded source set
uv run litclock multilingual-acquire --language fr
uv run litclock multilingual-acquire --language zh

# FR-1 / ZH-1: bounded parser pilot
uv run litclock multilingual-mine --language fr --stage pilot
uv run litclock multilingual-mine --language zh --stage pilot

# FR-2 / ZH-2: deterministic, expression-stratified local review
uv run litclock multilingual-review-export --language fr
uv run litclock multilingual-review-export --language zh

# FR-3 / ZH-3 uses --stage large only after the precision gate passes.
# FR-4 / ZH-4 imports resolved HIGH rows and reports each language independently.
uv run litclock multilingual-import --language fr
uv run litclock multilingual-import --language zh
uv run litclock stats --language fr
uv run litclock stats --language zh
```

The no-flag stats and selector behavior remains English. Passing a language to the internal
selector is corpus-layer plumbing for future work; no device or mixed-language product policy is
activated here.

## Sources and licensing

The local corpus was built from legacy Literary Clock datasets, Standard Ebooks source
repositories, Project Gutenberg bulk resources, and an official English Wikisource dump. These
sources have different licenses and underlying-work restrictions. The project records source URL,
commit/revision, license evidence, source locator, and candidate lineage.

The multilingual pilots add original-language title/creator fields, language and script tags,
translator/editor (when present), publication metadata, acquisition time, rights evidence, and
the downloaded document checksum. Project Gutenberg's catalog wording is retained as evidence;
it is not generalized into a worldwide legal conclusion. A public-domain original never implies
that an unreviewed translation is public domain.

The MIT license covers this project's original software and documentation only. It does not
relicense upstream quote data, the normalized database, or the quoted works. Consequently, the
public Git repository contains acquisition/import code and provenance documentation, but not the
normalized database or bulk corpora. See [`../NOTICE.md`](../NOTICE.md).
