# Corpus Semantic Adjudication Report

V1 audit: `english-clock-semantics-v1` (run 10)  
V2 audit: `english-clock-semantics-v2` (run 11)  
Adjudication: `english-clock-semantics-v2-adjudication`  
Corpus fingerprint: `e3888b302bdad8b6604f80e4ff7e1cb25da4bdb4ca8357fdbf9745ddd6abe9cc`

## 1. V1 starting point

V1 classified 8,730 existing English relationships as 7,479 KEEP, 368 QUARANTINE, and 883 REVIEW. Its fail-closed production view retained 6,080 selectable quotes over 1,427/1,440 minutes. V1 evidence remains stored and was not overwritten.

## 2. Adjudication methodology

Every V1 REVIEW and every V1 `HIGHLIGHT_SEMANTIC_MISMATCH` was reclassified. Recurring constructions became deterministic grammar; source-specific cases use committed TSV adjudication data with explicit evidence and the honest reviewer label `semantic-adjudication-v2`. Repairs are staged separately, retain original minute/highlight values, and enter the selector view only after the corrected relationship passes the same v2 validator.

## 3. 883 REVIEW disposition

- KEEP, including validated repairs: **640**
- QUARANTINE: **240**
- still REVIEW: **3**

| Parser family | Relationships | Keep as-is | Repaired | Quarantine | Review |
|---|---:|---:|---:|---:|---:|
| colon_numeric | 491 | 331 | 8 | 151 | 1 |
| hour_minute_word_form | 278 | 229 | 10 | 37 | 2 |
| past_after | 41 | 11 | 0 | 30 | 0 |
| to_before | 25 | 10 | 1 | 14 | 0 |
| bare_hour_contextual | 23 | 19 | 2 | 2 | 0 |
| other_legacy | 19 | 16 | 0 | 3 | 0 |
| quarter_half | 4 | 1 | 0 | 3 | 0 |
| oclock | 2 | 2 | 0 | 0 | 0 |

## 4. 267 mismatch disposition

All **267** cases are accounted for: **39** were relationship/highlight repairs, **62** became KEEP after grammar improvement, **166** were confirmed non-clock/bad relationships, and **0** remain unresolved.

| Disposition | Adjudication | Relationships |
|---|---|---:|
| NON_CLOCK | QUARANTINE | 166 |
| PARSER_LIMITATION | KEEP_AS_IS | 62 |
| PARSER_LIMITATION | REPAIR_MINUTE | 1 |
| WRONG_CLOCKFACE_SIDE | REPAIR_MINUTE | 23 |
| WRONG_HIGHLIGHT | REPAIR_MINUTE_AND_HIGHLIGHT | 1 |
| WRONG_MINUTE_LABEL | REPAIR_MINUTE | 14 |

## 5. Repair model

There are **51 semantic repair adjudications across 51 quotes**, materializing **33 corrected minute/highlight relationships**. A further **23 highlight-only metadata repairs** make stored `time_text` match source case/punctuation exactly. In total, **56 repair-backed relationships across 44 quotes** are active and **27 canonical records** receive source-backed offset/time-text corrections when v2 is active. Canonical literary prose is unchanged; activating another audit restores the prior spans before applying its own.

## 6. Generalized grammar improvements

V2 adds deterministic coverage for archaic compounds, written 24-hour forms, ellipsis-separated speech, compact/historical timestamps, fractional o'clock, quarter/half variants, exact offset constructions, transport-service idioms, and explicit dashed meridiem. It simultaneously adds adversarial rejection for clock ranges, dense references, approximate boundaries, hyphenated quantities, and partial highlights such as `half a minute after nine` highlighted only as `a minute after nine`.

## 7. Explicit reviewed decisions

The committed adjudication TSV contains **147 relationship decisions/overrides**. These are agent adjudications, not claimed independent human review. The three unresolved cases remain 12:21 (`Twelve twenty-one`), 18:45 (`Six forty-five` around a new-baby exchange), and 21:58 (a source-truncated fragment).

## 8. Colon-numeric findings

V2 distinguishes prose timestamps, train/flight/service designations, digital/readout contexts, diary/log entries, and explicit meridiem from scripture, chapter/section, ratio, score, timecode, range, and bare-number uses. The final colon REVIEW set is limited to the source-truncated 21:58 fragment.

| Action | Semantic class | Relationships |
|---|---|---:|
| KEEP | CLOCK_TIME_AMBIGUOUS | 1,049 |
| KEEP | CLOCK_TIME_EXACT | 997 |
| QUARANTINE | CLOCK_TIME_AMBIGUOUS | 9 |
| QUARANTINE | CLOCK_TIME_EXACT | 17 |
| QUARANTINE | RATIO_OR_MEASUREMENT | 39 |
| QUARANTINE | SCORE_OR_RESULT | 14 |
| QUARANTINE | SECTION_OR_REFERENCE | 202 |
| QUARANTINE | UNKNOWN | 27 |
| REVIEW | UNKNOWN | 1 |

## 9. Written-clock findings

Written number adjacency is accepted only through a clock grammar or explicit evidence. `one twenty` can be a clock reading, while `one twenty-minute interval`, page/quantity/measurement uses, and unrelated adjacent numbers remain excluded.

## 10. O'clock findings

Literal o'clock constructions are intrinsically clock-like and no longer require redundant surrounding tokens. Fractional and archaic spellings are supported; durations and incomplete highlights remain excluded.

## 11. Quarter/half findings

`half past` and `quarter past/to` are exact clock grammars. `half an hour`, clock ranges, approximate phrases, and spans omitting the base hour are not. Several legacy quarter labels were repaired rather than discarding their literary quotes.

## 12. Bare-hour findings

Bare numbers remain conservative. Temporal syntax such as `at`, `by`, `until`, `around`, and an actual clock-strike construction can validate an hour; arbitrary numbers, policy boundaries, and quantities cannot.

## 13. Source-family findings

Retained Standard Ebooks/Gutenberg/Wikisource paragraph and locator evidence resolved several dayparts and timestamps. Legacy rows are disproportionately responsible for mislabeled minutes and insufficient context because they often carry only an upstream label and display excerpt. Missing evidence stays REVIEW rather than being guessed.

| Source family | Relationships | KEEP | QUARANTINE | REVIEW |
|---|---:|---:|---:|---:|
| LEGACY | 5,450 | 5,078 | 369 | 3 |
| GUTENBERG | 2,945 | 2,656 | 289 | 0 |
| STANDARD_EBOOKS | 308 | 300 | 8 | 0 |
| WIKISOURCE | 27 | 26 | 1 | 0 |

Parser-family residual risk is concentrated in colon numeric and written-number forms; o'clock and quarter/half still contain bad legacy labels, but their intrinsic clock grammars are now separated from duration forms.

| Parser family | Relationships | KEEP | QUARANTINE | REVIEW | Flagged rate |
|---|---:|---:|---:|---:|---:|
| colon_numeric | 2,578 | 2,267 | 310 | 1 | 12.1% |
| hour_minute_word_form | 1,903 | 1,764 | 137 | 2 | 7.3% |
| past_after | 1,607 | 1,553 | 54 | 0 | 3.4% |
| to_before | 1,387 | 1,350 | 37 | 0 | 2.7% |
| oclock | 667 | 594 | 73 | 0 | 10.9% |
| quarter_half | 264 | 219 | 45 | 0 | 17.0% |
| other_legacy | 195 | 188 | 7 | 0 | 3.6% |
| bare_hour_contextual | 129 | 125 | 4 | 0 | 3.1% |

## 14. V1 → V2 transition matrix

Repairs count as final V2 KEEP dispositions even when the original minute relationship is removed.

| V1 action | V2 KEEP | V2 QUARANTINE | V2 REVIEW |
|---|---:|---:|---:|
| KEEP | 7,393 | 86 | 0 |
| QUARANTINE | 101 | 267 | 0 |
| REVIEW | 640 | 240 | 3 |

## 15. Repairs made

Semantic repair adjudications: **51**; corrected minute/highlight relationships: **33**; exact metadata-only highlight repairs: **23**; repaired highlights total: **27**. Exact rows and evidence are in the ignored `materialized_repairs.csv` artifact.

## 16. Before / V1 / V2 corpus metrics

| Metric | Baseline | V1 | V2 |
|---|---:|---:|---:|
| canonical quotes | 7,229 | 7,229 | 7,229 |
| selectable quotes | 7,091 | 6,080 | 6,621 |
| relationships | 8,730 | 7,479 | 8,116 |
| covered minutes | 1,440 | 1,427 | 1,435 |
| empty minutes | 0 | 13 | 5 |
| exactly 1 | 25 | 67 | 37 |
| exactly 2 | 72 | 146 | 122 |
| at least 3 | 1,343 | 1,214 | 1,276 |
| at least 5 | 990 | 816 | 894 |
| at least 7 | 694 | 333 | 467 |
| min | 1 | 0 | 0 |
| p10 | 3.0 | 2.0 | 2.0 |
| p25 | 4.0 | 3.0 | 4.0 |
| median | 6.0 | 5.0 | 5.0 |
| p75 | 7.0 | 6.0 | 7.0 |
| p90 | 7.0 | 7.0 | 7.0 |
| max | 69 | 64 | 66 |

## 17. Restored minutes

V2 restores **8** of V1's 13 empty minutes from existing evidence: 06:36, 06:44, 06:52, 07:36, 09:32, 11:42, 13:38, 20:36.

## 18. True empty minutes

Semantic v2 has **5** empty minutes: 00:31, 12:31, 13:36, 17:44, 18:44.

## 19. True sub-three minutes

V2 has **164** semantic minutes below three: 5 empty, 37 with one, and 122 with two. The exact deterministic list and deficits are in `targeted_recovery_minutes.csv`.

## 20. Remaining REVIEW cases

**3 relationships** remain genuinely evidence-insufficient. They are excluded from production and preserved in `remaining_review.csv`.

## 21. Renderer-safe coverage

Frozen PW4 audit: semantic-selectable **6,621**; full **6,605**; excerpt **14**; dirty **2**; display-safe relationships **8,114**.
Display-safe minutes: **1,435/1,440**; zero-safe: 00:31, 12:31, 13:36, 17:44, 18:44.

## 22. Targeted-recovery candidate minutes

Only rows categorized `SEMANTIC_CLEAN_GENUINELY_SPARSE` in the generated target CSV should lead a later mining pass. `UNRESOLVED_REVIEW` rows need evidence first; renderer-only/source-corruption gaps must be handled without weakening semantics.

Precision-first mining targets (**163**):

00:22, 00:24, 00:26, 00:28, 00:29, 00:31, 00:34, 00:44, 00:46, 00:59, 01:28, 01:31, 01:38, 01:41, 01:46, 01:48, 01:49, 01:51, 02:09, 02:14, 02:24, 02:26, 02:29, 02:31, 02:32, 02:38, 02:43, 02:49, 03:01, 03:09, 03:23, 03:31, 03:39, 03:43, 03:46, 04:14, 04:24, 04:26, 04:39, 04:43, 05:34, 05:44, 05:48, 06:19, 06:36, 06:44, 06:49, 06:52, 07:31, 07:36, 07:41, 07:46, 07:48, 08:09, 08:46, 08:56, 09:21, 09:32, 09:33, 09:41, 09:44, 09:46, 10:18, 10:19, 10:36, 10:41, 11:19, 11:26, 11:31, 11:33, 11:36, 11:38, 11:46, 12:14, 12:16, 12:18, 12:19, 12:28, 12:31, 12:34, 12:44, 13:19, 13:31, 13:33, 13:36, 13:38, 13:39, 13:43, 13:46, 13:47, 13:48, 13:51, 14:09, 14:14, 14:19, 14:26, 14:31, 14:46, 14:49, 14:54, 15:19, 15:21, 15:24, 15:31, 15:39, 15:43, 15:46, 16:09, 16:24, 16:26, 16:34, 16:36, 16:47, 17:06, 17:34, 17:44, 17:47, 18:09, 18:11, 18:13, 18:14, 18:16, 18:24, 18:26, 18:31, 18:36, 18:39, 18:44, 19:21, 19:24, 19:33, 19:36, 19:44, 19:46, 20:09, 20:11, 20:31, 20:36, 20:39, 21:01, 21:19, 21:21, 21:33, 21:34, 21:38, 21:39, 21:41, 21:44, 21:49, 22:16, 22:34, 22:36, 22:44, 22:49, 22:51, 22:54, 23:13, 23:14, 23:26, 23:31, 23:36, 23:38, 23:46

Non-mining sparse exceptions:

- 01:39: `SOURCE_CORRUPTION_PROBLEM` (semantic=2, renderer-safe=1)

## 23. Implications for Phase 4C

The semantic corpus is ready for a separate precision-first targeted-recovery phase, but the current Kindle bundle must not be rebuilt from this branch yet. Phase 4C should consume only a reviewed/merged semantic view; it must not revive QUARANTINE or REVIEW relationships. Renderer and runtime code were unchanged.

## Validation invariants

- SQLite integrity: `ok`
- foreign-key violations: 0
- orphan adjudications: 0
- duplicate active eligibility: 0
- invalid minutes/highlights: 0 / 0
- production rows without KEEP evidence: 0
- repaired relationships failing v2 revalidation: 0

## Repository validation

At finalization on macOS/Python 3.11: `uv sync` succeeded; `uv run pytest` reported **492 passed**; `uv run ruff check .` and `uv run ruff format --check .` passed. The frozen renderer/runtime regression tests are included in that complete suite.
