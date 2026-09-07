# English V1 Corpus Freeze

Status: **FROZEN FOR PHASE 4C**

Semantic base: `english-clock-semantics-v2`

Freeze materialization: `english-v1-corpus-freeze-v1`
Corpus fingerprint: `1566562d112ace24be0725d6e6664e9372a4e514e071de86efe0b5c5d7c2e845`

## Final result

The bounded completion pass added **47 canonical literary quotes** and **50 quote-minute
relationships**. It targeted only the five empty semantic minutes and the renderer-safe singleton
tail. No already-sufficient minute was deliberately mined.

| Metric | Semantic v2 | Frozen English V1 |
|---|---:|---:|
| Canonical quotes | 7,229 | 7,276 |
| Selectable quotes | 6,621 | 6,668 |
| Quote-minute relationships | 8,116 | 8,166 |
| Covered minutes | 1,435 / 1,440 | **1,440 / 1,440** |
| Empty minutes | 5 | **0** |
| Exactly one | 37 | **0** |
| Exactly two | 122 | 162 |
| Minimum pool | 0 | **2** |
| p10 / p25 / median / p75 / p90 | 2 / 4 / 5 / 7 / 7 | 2 / 4 / 5 / 7 / 7 |

The prior renderer audit established 8,114 display-safe relationships, with two active
relationships rejected by the unchanged dirty-record gate. Every one of the 47 new canonical
quotes passed the frozen PW4 production renderability gate as `DISPLAY_SAFE_FULL`. The resulting
renderer-safe inventory is therefore **8,164 relationships**, **1,440 / 1,440 minutes**, with a
minimum renderer-safe pool of **2**, zero singletons, and zero empty minutes.

## Recovery provenance

The additions use the exact user-supplied recovery leads, official/publisher excerpts, direct
author-hosted fiction, and already-located public-domain primary texts. The largest source groups
are Internet Archive primary scans (6), Royal Road author pages (6), Project Gutenberg texts (5),
publisher previews (5), Penguin Random House material (4), and Simon & Schuster material (3).
Each TSV row records its source URL, license/evidence statement, locator or source identity,
semantic rationale, and a checksum where one was available.

Explicit AM/PM and 24-hour expressions receive only their proven side. Shared relationships are
limited to genuinely unresolved clock-face expressions. Known invalid-side leads were not used on
the wrong side.

## Validation and invariants

- All 50 added relationships independently return semantic-v2 `KEEP`.
- The production freeze audit contains only `KEEP` relationships; historical v1/v2
  `QUARANTINE` and `REVIEW` evidence remains preserved but inactive.
- Highlight text and offsets are exact and unchanged by semantic classification.
- All 47 added quotes pass the unchanged frozen PW4 renderer without clipping or excerpting.
- The five former gaps (`00:31`, `12:31`, `13:36`, `17:44`, `18:44`) now have two or more
  production relationships.
- No duration, reference, score, ratio, or unrelated-number construction was added.
- Renderer and Kindle runtime code were not changed.

## Frozen inputs

- `data/semantic/english_v2_manual_adjudications.tsv`
- `data/semantic/english_v1_final_recovery.tsv`
- semantic v2 classifier and audit/materialization code

The generated database is intentionally ignored and reproducible from the frozen corpus snapshot,
semantic v2 adjudications, and final recovery inventory.

## Accepted limitations

Some final-tail entries are brief publisher-authorized or author-posted excerpts rather than
public-domain works. Their provenance and copyright status are recorded per row; this repository
does not distribute the generated bitmap/database bundle. Corpus growth or aesthetic optimization
is V2 work and does not block the native runtime.

**English V1 corpus is frozen for Phase 4C.**
