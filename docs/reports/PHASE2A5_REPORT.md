# Phase 2A.5 — Standard Ebooks Audit and Recovery Report

Generated 2026-09-04T21:19:19.279049-05:00 from the existing Standard Ebooks cache and SQLite candidate corpus. No external corpus was added.

## 1. Exact explanation of 7,293 → 277

| Disposition | Count |
|---|---:|
| Imported | 277 |
| Minute already full before Phase 2A | 6,912 |
| Lower-priority excess after the minute reached 7 | 104 |
| Diversity hard rejection | 0 |
| Quality/other failure inside high-confidence set | 0 |
| Otherwise eligible and missed | 0 |
| **Total** | **7,293** |

There is no volume-suppressing importer bug. The cap was applied against the Phase 1 coverage that existed before Standard Ebooks, and 6,912 candidates targeted buckets already at seven or more. The remaining 381 candidates competed for 277 open slots; 277 won and 104 were correctly deferred.

## 2. Unused high-confidence distribution

- Preexisting-full buckets: 6,912 candidates.
- Excess candidates behind selected quotes in sub-seven buckets: 104.
- Diversity was an ordering preference only; it caused no unfilled slots.

## 3. Counterfactual maximum from the original high-confidence set

| Metric | A: current | B: all eligible, cap 7 | C: no diversity preference |
|---|---:|---:|---:|
| Selectable corpus | 5,093 | 5,093 | 5,093 |
| Minutes at 0 | 3 | 3 | 3 |
| Minutes <3 | 734 | 734 | 734 |
| Minutes <5 | 1,179 | 1,179 | 1,179 |
| Minutes <7 | 1,319 | 1,319 | 1,319 |
| Minutes >=7 | 121 | 121 | 121 |
| Deficit to 7 | 5,839 | 5,839 | 5,839 |

## 4–7. Ambiguous recovery

| Metric | Count |
|---|---:|
| AM/PM-ambiguous detections | 15,563 |
| Nonduplicate ambiguous candidates relevant to a bucket <3 | 472 |
| Citation-shaped false positives among candidates relevant to a bucket <3 | 181 |
| Contextually resolved with deterministic evidence | 44 |
| Resolved candidates passing quality gates | 41 |
| Unresolved candidates exported for priority review | 277 |
| Additional quotes imported in Phase 2A.5 | 31 |
| Total contextual-recovery quotes now present | 31 |

Only a daypart cue in the containing sentence or deterministic local elapsed-time arithmetic from an explicit absolute time qualified for automatic resolution. Nearby but narratively vague cues remained human-review items.

## 8–11. Corpus after recovery

| Metric | Value |
|---|---:|
| Canonical quotes | 5,262 |
| Selectable quotes | 5,124 |
| Minutes at 0 | 3 |
| Minutes below 3 | 729 |
| Minutes below 5 | 1,172 |
| Minutes below 7 | 1,314 |
| Minutes at least 7 | 126 |
| Median quotes/minute | 2.00 |
| P10 / P25 / P75 / P90 | 1.00 / 2.00 / 4.00 / 6.00 |
| Remaining deficit to seven everywhere | 5,808 |
| Unique authors / books | 1,455 / 2,253 |

### Empty-minute investigation

- **15:46:** ampm_ambiguous=1, duplicate=2, failed_quality_or_eligibility=1.
  - candidate 26706: `three forty-six` — The Footsteps at the Lock, Ronald A. Knox (duplicate; duplicate passage: EXACT_MINED; `src/epub/text/chapter-16.xhtml#chapter-16:p42:chars=12099-12371`)
  - candidate 29874: `forty-six minutes past three` — Arsène Lupin Versus Herlock Sholmes, Maurice Leblanc (failed_quality_or_eligibility; invalid time expression; `src/epub/text/chapter-1.xhtml#chapter-1:p310:chars=39639-39771`)
  - candidate 29875: `forty-six minutes past three` — Arsène Lupin Versus Herlock Sholmes, Maurice Leblanc (duplicate; duplicate passage: EXACT_MINED; `src/epub/text/chapter-1.xhtml#chapter-1:p310:chars=39639-39771`)
  - candidate 37388: `3:46` — Nordenholt’s Million, J. J. Connington (ampm_ambiguous; ampm ambiguous; `src/epub/text/chapter-7.xhtml#chapter-7:p38:chars=19224-19333`)
- **16:19:** citation_quality_failure=2.
  - candidate 17565: `16:19` — Leviathan, Thomas Hobbes (citation_quality_failure; citation-shaped expression is not a literary clock time; `src/epub/text/chapter-42.xhtml#chapter-42:p99:chars=122985-123088`)
  - candidate 25387: `4:19` — The Kingdom of God Is Within You, Leo Tolstoy (citation_quality_failure; citation-shaped expression is not a literary clock time; `src/epub/text/chapter-12.xhtml#chapter-12-6:p294:chars=174985-175433`)
- **18:17:** citation_quality_failure=5.
  - candidate 5839: `6:17` — The Way to God and How to Find It, D. L. Moody (citation_quality_failure; citation-shaped expression is not a literary clock time; `src/epub/text/chapter-4.xhtml#chapter-4:p21:chars=7378-7940`)
  - candidate 5849: `18:17` — The Way to God and How to Find It, D. L. Moody (citation_quality_failure; citation-shaped expression is not a literary clock time; `src/epub/text/chapter-4.xhtml#chapter-4:p44:chars=15735-15817`)
  - candidate 9102: `6:17` — Two Treatises of Government, John Locke (citation_quality_failure; citation-shaped expression is not a literary clock time; `src/epub/text/chapter-2-5.xhtml#chapter-2-5:p8:chars=6547-6633`)
  - candidate 17464: `18:17` — Leviathan, Thomas Hobbes (citation_quality_failure; citation-shaped expression is not a literary clock time; `src/epub/text/chapter-39.xhtml#chapter-39:p4:chars=2192-2326`)
  - candidate 17510: `18:17` — Leviathan, Thomas Hobbes (citation_quality_failure; citation-shaped expression is not a literary clock time; `src/epub/text/chapter-42.xhtml#chapter-42:p22:chars=28860-29296`)

## 12. Hardest 50 minute buckets after recovery

| Rank | Minute | Selectable | Deficit | Authors | Books |
|---:|---|---:|---:|---:|---:|
| 1 | 15:46 | 0 | 7 | 1 | 1 |
| 2 | 16:19 | 0 | 7 | 1 | 1 |
| 3 | 18:17 | 0 | 7 | 1 | 1 |
| 4 | 00:06 | 1 | 6 | 1 | 1 |
| 5 | 00:08 | 1 | 6 | 1 | 1 |
| 6 | 00:23 | 1 | 6 | 1 | 1 |
| 7 | 00:24 | 1 | 6 | 1 | 1 |
| 8 | 00:26 | 1 | 6 | 1 | 1 |
| 9 | 00:31 | 1 | 6 | 1 | 1 |
| 10 | 00:34 | 1 | 6 | 1 | 1 |
| 11 | 00:47 | 1 | 6 | 1 | 1 |
| 12 | 00:50 | 1 | 6 | 1 | 1 |
| 13 | 00:54 | 1 | 6 | 1 | 1 |
| 14 | 00:57 | 1 | 6 | 1 | 1 |
| 15 | 01:05 | 1 | 6 | 1 | 1 |
| 16 | 01:22 | 1 | 6 | 1 | 1 |
| 17 | 01:24 | 1 | 6 | 1 | 1 |
| 18 | 01:26 | 1 | 6 | 1 | 1 |
| 19 | 01:37 | 1 | 6 | 1 | 1 |
| 20 | 01:41 | 1 | 6 | 1 | 1 |
| 21 | 01:42 | 1 | 6 | 1 | 1 |
| 22 | 01:48 | 1 | 6 | 1 | 1 |
| 23 | 01:51 | 1 | 6 | 1 | 1 |
| 24 | 01:52 | 1 | 6 | 1 | 1 |
| 25 | 01:56 | 1 | 6 | 1 | 1 |
| 26 | 02:01 | 1 | 6 | 1 | 1 |
| 27 | 02:04 | 1 | 6 | 1 | 1 |
| 28 | 02:14 | 1 | 6 | 1 | 1 |
| 29 | 02:18 | 1 | 6 | 1 | 1 |
| 30 | 02:24 | 1 | 6 | 1 | 1 |
| 31 | 02:26 | 1 | 6 | 1 | 1 |
| 32 | 02:28 | 1 | 6 | 1 | 1 |
| 33 | 02:31 | 1 | 6 | 1 | 1 |
| 34 | 02:32 | 1 | 6 | 1 | 1 |
| 35 | 02:38 | 1 | 6 | 1 | 1 |
| 36 | 02:53 | 1 | 6 | 1 | 1 |
| 37 | 02:56 | 1 | 6 | 1 | 1 |
| 38 | 02:57 | 1 | 6 | 1 | 1 |
| 39 | 03:03 | 1 | 6 | 1 | 1 |
| 40 | 03:09 | 1 | 6 | 1 | 1 |
| 41 | 03:19 | 1 | 6 | 1 | 1 |
| 42 | 03:23 | 1 | 6 | 1 | 1 |
| 43 | 03:28 | 1 | 6 | 1 | 1 |
| 44 | 03:31 | 1 | 6 | 1 | 1 |
| 45 | 03:34 | 1 | 6 | 1 | 1 |
| 46 | 03:36 | 1 | 6 | 1 | 1 |
| 47 | 03:37 | 1 | 6 | 1 | 1 |
| 48 | 03:38 | 1 | 6 | 1 | 1 |
| 49 | 03:39 | 1 | 6 | 1 | 1 |
| 50 | 03:40 | 1 | 6 | 1 | 1 |

## 13. Parser ambiguity and failure patterns

| Pattern | Count |
|---|---:|
| DENSE_IGNORED | 13,432 |
| DUPLICATE_IGNORED | 1,265 |
| UNRESOLVED | 579 |
| CITATION_REJECTED | 237 |
| CONTAINING_SENTENCE_DAYPART | 41 |
| INVALID_OPTIONS | 6 |
| TRAILING_SECONDS_MERIDIEM | 3 |

The dominant ambiguity remains bare 12-hour clock wording with no direct daypart. Daypart terms outside the containing sentence were deliberately not treated as proof. Timetable-like multi-time contexts and weak/incomplete contexts continued to fail the existing deterministic quality gates.

## 14. Recommendation for Phase 2B

A new independent corpus is necessary to reach seven quotes for every minute. The original high-confidence pool has no unused capacity below seven, three minutes are still empty, and the remaining deficit is far larger than the sparse-relevant review queue. Review the priority CSV first to extract its remaining value, then proceed to another public-domain corpus in Phase 2B. Do not loosen semantic time correctness to force coverage.

## Reproducibility artifacts

- `PHASE2A5_CANDIDATE_AUDIT.csv`: all 1,440 minutes.
- `PHASE2A5_AUDIT.md`: high-confidence accounting and counterfactuals.
- `PHASE2A5_REVIEW_PRIORITY.csv`: unresolved candidates able to improve a <3 bucket.
- `coverage.json`, `minute_coverage.csv`, `COVERAGE_REPORT.md`: regenerated corpus coverage.
