# Corpus Architecture

## Canonical quotes and display relationships

A canonical quote is one distinct literary passage with its original text, title, author,
highlight offsets, validation status, and source provenance. Deduplication merges exact and
punctuation/whitespace-equivalent copies while retaining every contributing source record.

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

## Semantic clock-time revalidation

Lexical shape and exact offsets do not prove time-of-day meaning. Every existing selectable English
quote-minute relationship can therefore be passed through a second deterministic semantic gate.
The gate uses these classes:

- `CLOCK_TIME_EXACT` and `CLOCK_TIME_AMBIGUOUS`: the only production-selectable classes;
- `DURATION` and `RELATIVE_DURATION`;
- `SECTION_OR_REFERENCE` and `HEADING_OR_TOC`;
- `SCORE_OR_RESULT` and `RATIO_OR_MEASUREMENT`;
- `DATE_OR_NUMBER`, `NON_TEMPORAL_NUMBER`, and `UNKNOWN`.

For example, `one five-minute interval` is a duration rather than 01:05, and `John 12:16` is a
scripture reference rather than 12:16. Conversely, `At 12:16 he entered` has explicit clock-time
context. A context-free bare `12:16` remains REVIEW; uncertainty never silently becomes KEEP.

The claimed minute must be derivable from the highlighted phrase itself under an accepted grammar.
Unrelated surrounding numbers cannot supply an hour or minute. The audit does not alter source
text or highlight offsets.

Decisions are stored per quote-minute relationship with an audit version, semantic class, action,
reason code, parser family, confidence, and source/candidate context. Activation is reversible:
QUARANTINE and REVIEW disappear only from the derived selection view, while canonical quotes,
candidate rows, provenance, and the audit decision remain intact. This allows later human review
or deterministic rule refinement without reconstructing lost records.

## Sources and licensing

The local corpus was built from legacy Literary Clock datasets, Standard Ebooks source
repositories, Project Gutenberg bulk resources, and an official English Wikisource dump. These
sources have different licenses and underlying-work restrictions. The project records source URL,
commit/revision, license evidence, source locator, and candidate lineage.

The MIT license covers this project's original software and documentation only. It does not
relicense upstream quote data, the normalized database, or the quoted works. Consequently, the
public Git repository contains acquisition/import code and provenance documentation, but not the
normalized database or bulk corpora. See [`../NOTICE.md`](../NOTICE.md).
