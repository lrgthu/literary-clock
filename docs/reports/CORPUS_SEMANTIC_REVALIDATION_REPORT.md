# Corpus Semantic Time Revalidation

Audit version: `english-clock-semantics-v1`

Corpus fingerprint: `e3888b302bdad8b6604f80e4ff7e1cb25da4bdb4ca8357fdbf9745ddd6abe9cc`

Status in the copy-on-write audit database: **APPLIED**

## 1. Motivation / construct-validity problem

The prior corpus proved lexical offset validity, but legacy minute labels and some mined parser routes did not always prove that the highlighted phrase *meant a clock time*. This audit separates clock-time semantics from durations, references, results, identifiers, structural text, and unresolved context. Precision takes priority over coverage.

## 2. Current English corpus baseline

The audit evaluated **7,091 selectable English quotes** and **8,730 quote-minute relationships** over 1,440/1,440 minutes.
The operational database was copied before mutation; neither the original database nor the currently deployed Kindle bundle was changed.

## 3. Existing parser routes

Legacy CSV/YAML importers accepted upstream minute labels after offset validation. Standard Ebooks, Gutenberg, and Wikisource shared `timeparse`, but imported-candidate parser metadata was not consulted by selection. The new audit recovers candidate routes where available and infers a grammar family for legacy relationships. The principal entry points were trusted upstream labels, permissive written-number pairing, and lexical numeric recognition without a second semantic clock-time gate.
`quote_time_semantics` records clock-face/daypart interpretation, `quote_minute_eligibility` stores the resulting relationships, and `quote_minute_pool` is the selector-facing derived view. Existing source-specific candidate tables retain review status and rejection reasons, but before this pass there was no corpus-wide semantic decision required by that final view.
A clean Phase 1 rebuild from the pinned local snapshots processed 13,179 raw rows into 4,949 canonical records and 4,047 selectable quotes. Its subsequent audit classified all 4,047 selectable relationships KEEP, showing that the importer-side gate blocks newly recognized legacy false positives before selection. The multi-gigabyte later-stage public-domain corpus was revalidated from the production snapshot rather than reacquired.

| Parser family | Relationships | KEEP | QUARANTINE | REVIEW | Auto-FP rate | Flagged rate |
|---|---:|---:|---:|---:|---:|---:|
| colon_numeric | 2,578 | 1,993 | 94 | 491 | 3.6% | 22.7% |
| hour_minute_word_form | 1,903 | 1,549 | 76 | 278 | 4.0% | 18.6% |
| past_after | 1,607 | 1,531 | 35 | 41 | 2.2% | 4.7% |
| to_before | 1,387 | 1,325 | 37 | 25 | 2.7% | 4.5% |
| oclock | 667 | 587 | 78 | 2 | 11.7% | 12.0% |
| quarter_half | 264 | 213 | 47 | 4 | 17.8% | 19.3% |
| other_legacy | 195 | 175 | 1 | 19 | 0.5% | 10.3% |
| bare_hour_contextual | 129 | 106 | 0 | 23 | 0.0% | 17.8% |

## 4. Semantic class taxonomy

Only `CLOCK_TIME_EXACT` and `CLOCK_TIME_AMBIGUOUS` receive `KEEP`. High-confidence non-clock readings receive `QUARANTINE`; insufficient evidence receives `REVIEW`, never implicit acceptance.

| Semantic class | Relationships |
|---|---:|
| CLOCK_TIME_AMBIGUOUS | 4,979 |
| CLOCK_TIME_EXACT | 2,767 |
| DATE_OR_NUMBER | 1 |
| DURATION | 8 |
| RATIO_OR_MEASUREMENT | 5 |
| RELATIVE_DURATION | 18 |
| SCORE_OR_RESULT | 4 |
| SECTION_OR_REFERENCE | 65 |
| UNKNOWN | 883 |

## 5. Colon-numeric audit

- confirmed clock: 1,770
- reference: 65
- score result: 4
- ratio measurement: 5
- unknown bare: 491
- other: 20

High-confidence colon auto-quarantines: **94**.

Colon syntax alone is not evidence. Explicit meridiem/daypart, clock/watch/display language, temporal prepositions, or schedule context can validate it; bare cases remain in REVIEW.

## 6. Duration audit

Duration/relative-duration relationships: **26**. Hyphenated duration nouns, elapsed-time cues, and `for/during/within` constructions are not clock times.
All **19** selectable relationships whose source context contains a number plus a hyphenated minute/hour noun were inspected; quote 6486 was the synthesized `one five-minute` failure (at both shared clock-face minutes), while unrelated duration wording did not override separately valid clock phrases.

## 7. Chapter/reference audit

Section/reference relationships: **65**. The rules recognize chapter, scripture, legal/section, page/line, and explicit reference syntax.

## 8. Source-structure audit

Standard Ebooks XHTML is parsed semantically; Gutenberg strips marked boilerplate/TOCs; Wikisource parses XML namespaces and wikitext structure. Candidate locators and sections are retained. Explicit TOC/index/reference-region evidence now supports quarantine, but structure-free legacy rows must be decided from displayed text and provenance alone.

Relationships classified solely from retained structural exclusion evidence: **0**. Structural adapters already exclude many non-prose regions before candidates enter the corpus.

| Source family | Relationships | KEEP | QUARANTINE | REVIEW |
|---|---:|---:|---:|---:|
| LEGACY | 5,450 | 4,633 | 245 | 572 |
| GUTENBERG | 2,945 | 2,522 | 122 | 301 |
| STANDARD_EBOOKS | 308 | 297 | 1 | 10 |
| WIKISOURCE | 27 | 27 | 0 | 0 |

## 9. Known discovered false positives

- Quote 6486 at 01:05, 13:05: `DURATION_HYPHENATED` — “There was one five-minute interval of excitement when, far down the tunnel through the forest, we saw a light gleaming.”
- Quote 6545 at 00:16, 12:16: `REFERENCE_SCRIPTURE` — “Esau sold his birthright, and that for a mess of pottage, and that birthright was his greatest jewel; and if he, why might not Little-faith do so t…”
- Quote 6453 at 08:22, 20:22: `REFERENCE_SCRIPTURE` — “8:22, Joel 2:2. Sometimes sin or impurity, 1 John 1:5. The devil have all these; how great is their sin, how great must be their distress and angui…”
- Quote 6522 at 07:17, 19:17: `REFERENCE_SCRIPTURE` — “There shall be no more crying, nor Sorrow: for He that is owner of the place will wipe all tears from our eyes. [Isa. 25.6-8; Rev. 7:17, 21:4]”
- Quote 6524 at 07:29, 19:29: `REFERENCE_SCRIPTURE` — “Yes; but I am so laden with this burden that I cannot take that pleasure in them as formerly; methinks I am as if I had none. [1 Cor 7:29]”
- Quote 6526 at 00:31, 12:31: `REFERENCE_SCRIPTURE` — “12:31, Mark 3:28] "Be not faithless, but believing." [John 20:27] Then did Christian again a little revive, and stood up trembling, as at first, be…”
- Quote 6530 at 01:13, 13:13: `REFERENCE_SCRIPTURE` — “3:4]; the third also set a mark on his forehead, and gave him a roll with a seal upon it, which he bade him look on as he ran, and that he should g…”
- Quote 6532 at 02:14, 14:14: `REFERENCE_SCRIPTURE` — “And by what they said, I perceived that he had been a great warrior, and had fought with and slain "him that had the Power of death", but not witho…”
- Quote 6533 at 06:23, 18:23: `REFERENCE_SCRIPTURE` — “I was born, indeed, in your dominions, but your service was hard, and your wages such as a man could not live on, "for the wages of sin is death" […”
- Quote 6536 at 02:22, 14:22: `REFERENCE_SCRIPTURE` — “Well, at my first setting out, I had hopes of that man; but now I fear he will perish in the overthrow of the city; for it is happened to him accor…”
- Quote 6538 at 07:24, 19:24: `REFERENCE_SCRIPTURE` — “This made me cry, "O wretched man!" [Rom. 7:24] So I went on my way up the hill.”
- Quote 6541 at 07:24, 19:24: `REFERENCE_SCRIPTURE` — “7:24, John 16:9, Mark 16:16]). This sight and sense of things worketh in him sorrow and shame for sin; he findeth, moreover, revealed in him the Sa…”

## 10. Auto-quarantine rules

High-confidence rules cover invalid/mismatched highlights, hyphenated and explicit durations, relative elapsed intervals, chapter/scripture/section/page references, scores, ratios/measurements, identifiers/timecodes, and claimed-minute mismatches.

The **368** relationships comprise **101** direct non-clock classifications and **267** claimed-minute/phrase mismatches.

## 11. REVIEW policy

**883 relationships across 729 quotes** remain unresolved. Bare colon numbers, unsupported written forms, and context-poor bare hours are REVIEW and are excluded once this audit is activated.
Local ignored review artifacts include 150 stratified colon cases, 150 duration-risk cases, 100 reference/heading cases, 100 UNKNOWN/REVIEW cases, and every high-risk number-plus-hyphenated-duration occurrence.

## 12. Parser-family error rates

The table in section 3 reports deterministic auto-quarantine rate separately from broader flagged rate. These are corpus error estimates under this audit, not universal precision estimates for English.

## 13. Before/after corpus counts

| Metric | Before | After |
|---|---:|---:|
| canonical_quotes | 7229 | 7229 |
| selectable_quotes | 7091 | 6080 |
| relationships | 8730 | 7479 |
| covered_minutes | 1440 | 1427 |
| empty_minutes | 0 | 13 |
| exactly_1 | 25 | 67 |
| exactly_2 | 72 | 146 |
| at_least_3 | 1343 | 1214 |
| at_least_5 | 990 | 816 |
| at_least_7 | 694 | 333 |
| min | 1 | 0 |
| p10 | 3.0 | 2.0 |
| p25 | 4.0 | 3.0 |
| median | 6.0 | 5.0 |
| p75 | 7.0 | 6.0 |
| p90 | 7.0 | 7.0 |
| max | 69 | 64 |

Distinct quotes with auto-quarantine decisions: **324**.
Relationships: KEEP **7,479**, QUARANTINE **368**, REVIEW **883**.

## 14. Before/after coverage

Coverage changed from **1,440** to **1427** minutes. No replacement quote was added.

## 15. Newly empty/sparse minutes

Newly empty minutes: **13**. Newly below three from a baseline of at least three: **129**.

Newly empty: 00:31, 06:36, 06:44, 06:52, 07:36, 09:32, 11:42, 12:31, 13:36, 13:38, 17:44, 18:44, 20:36

## 16. Largest affected minute pools

| Minute | Before | After | Loss | Removed quote IDs | Reasons |
|---|---:|---:|---:|---|---|
| 03:00 | 54 | 48 | 6 | 550,561,566,574,575,577 | BARE_HOUR_WITHOUT_TIME_EVIDENCE,COMPACT_NUMERIC_WITHOUT_TIME_EVIDENCE |
| 05:00 | 30 | 24 | 6 | 857,864,870,872,877,880 | BARE_HOUR_WITHOUT_TIME_EVIDENCE |
| 00:00 | 69 | 64 | 5 | 22,28,29,54,56 | BARE_HOUR_WITHOUT_TIME_EVIDENCE,HIGHLIGHT_SEMANTIC_MISMATCH,UNKNOWN_CONTEXT |
| 00:59 | 7 | 2 | 5 | 207,208,209,210,211 | BARE_HOUR_WITHOUT_TIME_EVIDENCE,HIGHLIGHT_SEMANTIC_MISMATCH |
| 04:00 | 34 | 29 | 5 | 709,710,720,723,736 | BARE_HOUR_WITHOUT_TIME_EVIDENCE,UNKNOWN_CONTEXT |
| 08:00 | 48 | 43 | 5 | 1392,1400,1406,1408,1412 | BARE_HOUR_WITHOUT_TIME_EVIDENCE,COLON_WITHOUT_TIME_EVIDENCE,HIGHLIGHT_SEMANTIC_MISMATCH,UNKNOWN_CONTEXT |
| 09:00 | 44 | 39 | 5 | 1625,1632,1657,1658,1662 | BARE_HOUR_WITHOUT_TIME_EVIDENCE,UNKNOWN_CONTEXT |
| 10:00 | 45 | 40 | 5 | 1887,1889,1910,1911,1922 | BARE_HOUR_WITHOUT_TIME_EVIDENCE,HIGHLIGHT_SEMANTIC_MISMATCH,UNKNOWN_CONTEXT,WRITTEN_WITHOUT_TIME_EVIDENCE |
| 13:18 | 7 | 2 | 5 | 281,2615,6552,7276,7355 | COLON_WITHOUT_TIME_EVIDENCE,HIGHLIGHT_SEMANTIC_MISMATCH,REFERENCE_SCRIPTURE,WRITTEN_WITHOUT_TIME_EVIDENCE |
| 14:00 | 34 | 29 | 5 | 2710,2714,2715,2736,2739 | BARE_HOUR_WITHOUT_TIME_EVIDENCE,HIGHLIGHT_SEMANTIC_MISMATCH |
| 14:16 | 7 | 2 | 5 | 6531,6550,7336,7382,7450 | COLON_WITHOUT_TIME_EVIDENCE |
| 15:14 | 7 | 2 | 5 | 2980,6930,7146,7365,7445 | COLON_WITHOUT_TIME_EVIDENCE,REFERENCE_SCRIPTURE |
| 17:16 | 6 | 1 | 5 | 914,3370,6595,7275,7362 | COLON_WITHOUT_TIME_EVIDENCE,HIGHLIGHT_SEMANTIC_MISMATCH,REFERENCE_SCRIPTURE |
| 19:24 | 6 | 1 | 5 | 3761,3762,6538,6541,6553 | COLON_WITHOUT_TIME_EVIDENCE,HIGHLIGHT_SEMANTIC_MISMATCH,REFERENCE_SCRIPTURE |
| 19:59 | 7 | 2 | 5 | 3873,3874,3875,3876,4897 | HIGHLIGHT_SEMANTIC_MISMATCH,RELATIVE_LATER,UNKNOWN_CONTEXT |
| 21:01 | 7 | 2 | 5 | 4099,4101,4102,4103,6405 | HIGHLIGHT_SEMANTIC_MISMATCH,UNKNOWN_CONTEXT |
| 03:01 | 7 | 3 | 4 | 586,587,588,589 | HIGHLIGHT_SEMANTIC_MISMATCH |
| 03:14 | 7 | 3 | 4 | 6930,7146,7365,7445 | COLON_WITHOUT_TIME_EVIDENCE,REFERENCE_SCRIPTURE |
| 04:30 | 11 | 7 | 4 | 796,797,798,799 | COMPACT_NUMERIC_WITHOUT_TIME_EVIDENCE,WRITTEN_WITHOUT_TIME_EVIDENCE |
| 05:16 | 6 | 2 | 4 | 914,6595,7275,7362 | COLON_WITHOUT_TIME_EVIDENCE,REFERENCE_SCRIPTURE |
| 07:29 | 7 | 3 | 4 | 1284,1285,4737,6524 | COLON_WITHOUT_TIME_EVIDENCE,HIGHLIGHT_SEMANTIC_MISMATCH,REFERENCE_SCRIPTURE,WRITTEN_WITHOUT_TIME_EVIDENCE |
| 08:02 | 7 | 3 | 4 | 1423,1424,1425,6227 | COLON_WITHOUT_TIME_EVIDENCE,WRITTEN_WITHOUT_TIME_EVIDENCE |
| 11:58 | 7 | 3 | 4 | 2331,2333,2335,4819 | COLON_WITHOUT_TIME_EVIDENCE,HIGHLIGHT_SEMANTIC_MISMATCH |
| 11:59 | 7 | 3 | 4 | 2337,2338,5317,5846 | HIGHLIGHT_SEMANTIC_MISMATCH,UNKNOWN_CONTEXT |
| 13:01 | 7 | 3 | 4 | 2574,2576,2577,6644 | HIGHLIGHT_SEMANTIC_MISMATCH,UNKNOWN_CONTEXT |
| 13:15 | 7 | 3 | 4 | 2606,2608,2610,4832 | UNKNOWN_CONTEXT,WRITTEN_WITHOUT_TIME_EVIDENCE |
| 13:25 | 7 | 3 | 4 | 2632,2633,2634,4836 | WRITTEN_WITHOUT_TIME_EVIDENCE |
| 14:01 | 7 | 3 | 4 | 2743,2747,6124,6209 | HIGHLIGHT_SEMANTIC_MISMATCH,SCORE_CONTEXT,UNKNOWN_CONTEXT |
| 15:15 | 8 | 4 | 4 | 2985,2986,2987,2989 | COLON_WITHOUT_TIME_EVIDENCE,WRITTEN_WITHOUT_TIME_EVIDENCE |
| 16:00 | 48 | 44 | 4 | 3097,3105,3130,3138 | BARE_HOUR_WITHOUT_TIME_EVIDENCE,UNKNOWN_CONTEXT |

## 17. Remaining unresolved REVIEW cases

There are **883 relationship decisions** requiring evidence or deterministic rule refinement. Therefore this report does **not** claim that every remaining corpus relationship is semantically clean.
The activated view is a precision-first validated core: both QUARANTINE and REVIEW remain preserved in audit/source tables but are excluded from production selection.

## 18. Recommended targeted recovery phase

First adjudicate the prioritized REVIEW packet and encode only evidence-backed rules or review provenance. After that, target genuine replacements for newly empty and newly sub-three buckets. This branch intentionally performs no recovery mining.

## 19. Implications for Phase 4C

Phase 4C should consume a bundle built only after semantic audit activation and review. The current physical Kindle bundle remains unchanged; renderer, runtime, deployment, and bundle formats were not modified in this branch.
The unchanged frozen PW4 renderer audited 6,080 selectable quotes: 6,063 full, 14 sentence-excerpted, and 3 pre-existing dirty-record rejections. It retained 7,476 display-safe relationships, with 14 zero-safe minutes (the 13 semantic gaps plus 01:39, whose sole retained quote is rejected by the existing dirty-record gate). There was no clipping, undersized body text, line-budget violation, attribution overflow, or pathological highlight wrap.
