# Literary Clock Phase 2C Report

Generated: 2026-09-05T18:06:16.810569+00:00

## Rationale and semantic boundary

A neutral exact 12-hour expression identifies one clock-face position, not a narrative
AM/PM claim. One canonical quote may therefore have two display eligibility relationships.
Explicit meridiem, deterministic displayed daypart, or deterministic trusted source context
continues to select one side only. Approximate, range, duplicate, false-positive, malformed
highlight, provenance, and literary-quality gates are unchanged.

Displayed excerpt semantics and surrounding source-context semantics were audited separately.
Vague nearby daypart words were not inferred and therefore remain daypart-neutral.
Conflicting cues and cases without trusted source context remain in human review.

## Re-evaluation

- Ambiguous/applicable records re-evaluated: **168,955**
- Standard Ebooks: **15,563**
- Project Gutenberg: **151,723**
- Applicable legacy canonical records: **1,669**
- Passing every unchanged non-AM/PM gate: **111,182**
- Daypart-neutral dual candidates found: **98,792**
- Deterministically context-resolved: **12,300**
- Contextual uncertainty retained for review: **90**
- Rejected by unchanged gates: **57,773**
- Dual candidates activated for at least one useful relationship: **1,959**
- Canonical quotes receiving both AM and PM relationships: **1,629**

## Counterfactual and activated coverage

| Scenario | Canonical | Unique selectable | Relationships | 0 | <3 | <5 | <7 | >=7 | Deficit |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A — current strict 24-hour model | 5,820 | 5,682 | 5,682 | 1 | 639 | 1,072 | 1,210 | 230 | 5,250 |
| B — shared clock-face model | 7,156 | 7,018 | 8,699 | 0 | 123 | 450 | 746 | 694 | 2,233 |
| C — shared model with diversity preference | 7,202 | 7,064 | 8,699 | 0 | 123 | 450 | 746 | 694 | 2,233 |

The diversity-aware policy removed **3,017** effective quote deficits. Pairwise
AM/PM absolute imbalance fell from **1,068** to **709**.
This demonstrates that the old unique-AM/PM restriction was a substantial, but not sole,
cause of sparsity.

## Current corpus terminology

- Canonical literary quotes: **7,202**
- Unique selectable quotes: **7,064**
- Quote-minute eligibility relationships: **8,699**
- Effective candidates summed across minute pools: **8,699**
- Minutes at 0: **0**
- Minutes below 3: **123**
- Minutes below 5: **450**
- Minutes below 7: **746**
- Minutes at least 7: **694**
- Remaining effective deficit to seven: **2,233**

## 15:46 audit

15:46 now has **1** effective candidate(s). The dedicated audit contains
**6** candidate records and preserves every rejection/evidence decision in
`data/generated/PHASE2C_1546_AUDIT.csv`.

The active phrase is **3:46** in *Vagabonding down the Andes Being the Narrative of a Journey, Chiefly Afoot, from Panama to Buenos Aires* by Franck, Harry Alverson, 1881-1962. It is a `CONTEXT_RESOLVED_PM` relationship supported by `SOURCE_GREETING_MONOTONIC_TIME_SEQUENCE`: 'Buenas tardes' anchors '3:28'; monotonic local sequence: 3:15, 3:20, 3:28, 3:46.

## Human review

**1** daypart-uncertain records can still improve a bucket below seven and
are exported in `data/generated/PHASE2C_CONTEXT_REVIEW.csv`.

## Selector and integrity verification

- Verification status: **PASS**
- pytest: 222 passed in 1.07s
- Ruff check: passed
- Ruff format: 47 files already formatted
- SQLite integrity: integrity_check='ok'; foreign_key_check=0 violations
- Selector/global cooldown tests: passed: cross-bucket quote history, 24-hour exact-quote preference, graceful tiny-pool relaxation, and restart persistence
- Phase 2C semantic/provenance integrity: **{'missing_semantics': 0, 'missing_eligibility': 0, 'import_failures': 0, 'provenance_failures': 0, 'counterfactual_mismatch': 0}**

The selector uses one global quote ID across both minute pools. A 24-hour exact-quote
cooldown is evaluated from persistent display history before book and author cooldowns,
with graceful relaxation only when no alternative exists.

## 50 paired buckets with the largest improvement

| Rank | Pair | Strict model | Shared model | Improvement |
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

## Recommendation

Another independent corpus is still necessary after prioritized contextual review; the shared-clock policy materially improves coverage but does not close every bucket.
