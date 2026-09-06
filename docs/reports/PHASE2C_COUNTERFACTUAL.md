# Phase 2C Counterfactual — Shared 12-Hour Clock-Face Eligibility

Generated before production eligibility mutation: 2026-09-05T18:01:06.801746+00:00

An unresolved exact 12-hour clock expression denotes a position on an ordinary clock
face. Representing it at both corresponding AM and PM display moments does not infer
narrative daypart. Deterministic displayed or source-context evidence remains authoritative
and produces one resolved eligibility relationship instead.

## Audit boundary

Displayed-text semantics were checked independently from full source paragraph and
neighboring-paragraph context. Direct daypart wording or deterministic local elapsed-time
arithmetic resolves one side. Mere nearby daypart vocabulary is not treated as evidence
and leaves the expression daypart-neutral; conflicting cues or unavailable trusted source
context are retained for review.

## Candidate accounting

- Standard Ebooks ambiguous detections: **15,563**
- Project Gutenberg ambiguous detections: **151,723**
- Applicable selectable legacy clock-face records: **1,669**
- Other legacy AMBIGUOUS records lacking exact offsets: **68**
- Passed as daypart-neutral dual candidates: **98,792**
- Deterministically context-resolved candidates: **12,300**
- Contextual review required: **90**
- Failed unchanged non-AM/PM gates: **57,773**

## Counterfactual coverage

| Scenario | Canonical | Unique selectable | Relationships | 0 | <3 | <5 | <7 | >=7 | Deficit |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A — current strict 24-hour model | 5,820 | 5,682 | 5,682 | 1 | 639 | 1,072 | 1,210 | 230 | 5,250 |
| B — shared clock-face model | 7,156 | 7,018 | 8,699 | 0 | 123 | 450 | 746 | 694 | 2,233 |
| C — shared model with diversity preference | 7,202 | 7,064 | 8,699 | 0 | 123 | 450 | 746 | 694 | 2,233 |

Scenario B ranks by deterministic quality. Scenario C first prefers new authors and books
within each minute, then relaxes those preferences so they never prevent filling a bucket.
No scenario adds an eighth effective candidate to a minute.

## 50 AM/PM pairs with the largest improvement

| Rank | Pair | Before | After | Added relationships |
|---:|---|---:|---:|---:|
| 1 | 02:04 / 14:04 | 1 + 1 | 7 + 7 | 12 |
| 2 | 02:57 / 14:57 | 1 + 1 | 7 + 7 | 12 |
| 3 | 03:03 / 15:03 | 1 + 1 | 7 + 7 | 12 |
| 4 | 04:02 / 16:02 | 1 + 1 | 7 + 7 | 12 |
| 5 | 00:54 / 12:54 | 1 + 2 | 7 + 7 | 11 |
| 6 | 00:57 / 12:57 | 1 + 2 | 7 + 7 | 11 |
| 7 | 01:56 / 13:56 | 1 + 2 | 7 + 7 | 11 |
| 8 | 05:02 / 17:02 | 1 + 2 | 7 + 7 | 11 |
| 9 | 05:04 / 17:04 | 2 + 1 | 7 + 7 | 11 |
| 10 | 05:14 / 17:14 | 1 + 2 | 7 + 7 | 11 |
| 11 | 06:07 / 18:07 | 1 + 2 | 7 + 7 | 11 |
| 12 | 08:48 / 20:48 | 2 + 1 | 7 + 7 | 11 |
| 13 | 08:52 / 20:52 | 1 + 2 | 7 + 7 | 11 |
| 14 | 00:53 / 12:53 | 3 + 1 | 7 + 7 | 10 |
| 15 | 00:56 / 12:56 | 3 + 1 | 7 + 7 | 10 |
| 16 | 01:04 / 13:04 | 2 + 2 | 7 + 7 | 10 |
| 17 | 01:05 / 13:05 | 2 + 2 | 7 + 7 | 10 |
| 18 | 01:13 / 13:13 | 2 + 2 | 7 + 7 | 10 |
| 19 | 01:26 / 13:26 | 1 + 3 | 7 + 7 | 10 |
| 20 | 02:03 / 14:03 | 2 + 2 | 7 + 7 | 10 |
| 21 | 02:16 / 14:16 | 3 + 1 | 7 + 7 | 10 |
| 22 | 02:18 / 14:18 | 1 + 3 | 7 + 7 | 10 |
| 23 | 02:47 / 14:47 | 2 + 2 | 7 + 7 | 10 |
| 24 | 02:56 / 14:56 | 1 + 2 | 6 + 7 | 10 |
| 25 | 03:16 / 15:16 | 2 + 2 | 7 + 7 | 10 |
| 26 | 03:18 / 15:18 | 2 + 2 | 7 + 7 | 10 |
| 27 | 04:13 / 16:13 | 3 + 1 | 7 + 7 | 10 |
| 28 | 04:16 / 16:16 | 1 + 1 | 6 + 6 | 10 |
| 29 | 07:02 / 19:02 | 1 + 3 | 7 + 7 | 10 |
| 30 | 07:08 / 19:08 | 2 + 2 | 7 + 7 | 10 |
| 31 | 07:14 / 19:14 | 3 + 1 | 7 + 7 | 10 |
| 32 | 07:54 / 19:54 | 2 + 2 | 7 + 7 | 10 |
| 33 | 08:16 / 20:16 | 3 + 1 | 7 + 7 | 10 |
| 34 | 08:26 / 20:26 | 2 + 2 | 7 + 7 | 10 |
| 35 | 09:18 / 21:18 | 3 + 1 | 7 + 7 | 10 |
| 36 | 10:28 / 22:28 | 2 + 1 | 7 + 6 | 10 |
| 37 | 00:48 / 12:48 | 2 + 3 | 7 + 7 | 9 |
| 38 | 00:52 / 12:52 | 3 + 2 | 7 + 7 | 9 |
| 39 | 01:03 / 13:03 | 2 + 3 | 7 + 7 | 9 |
| 40 | 01:52 / 13:52 | 1 + 4 | 7 + 7 | 9 |
| 41 | 01:58 / 13:58 | 3 + 2 | 7 + 7 | 9 |
| 42 | 01:59 / 13:59 | 2 + 3 | 7 + 7 | 9 |
| 43 | 02:06 / 14:06 | 4 + 1 | 7 + 7 | 9 |
| 44 | 02:08 / 14:08 | 2 + 1 | 6 + 6 | 9 |
| 45 | 02:11 / 14:11 | 2 + 3 | 7 + 7 | 9 |
| 46 | 02:12 / 14:12 | 3 + 2 | 7 + 7 | 9 |
| 47 | 03:06 / 15:06 | 2 + 2 | 7 + 6 | 9 |
| 48 | 03:14 / 15:14 | 2 + 3 | 7 + 7 | 9 |
| 49 | 03:57 / 15:57 | 3 + 2 | 7 + 7 | 9 |
| 50 | 04:03 / 16:03 | 1 + 4 | 7 + 7 | 9 |
