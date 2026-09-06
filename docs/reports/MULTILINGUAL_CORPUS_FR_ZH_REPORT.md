# Multilingual Corpus Track: French and Chinese

Date: 2026-09-06  
Branch: `feature/multilingual-corpus`  
Base: `origin/main` at `83e6fcf70270d4c96489c426400b0d4750b2e1a9`

This is an additive corpus-and-semantics pilot. It does not mix languages in the production
clock, alter the frozen PW4 renderer, change fonts, or touch Kindle power/runtime behavior.
Quoted text and machine-readable review context remain ignored local data; this report contains
only aggregate results.

## 1. Architecture changes

The pipeline now has five independent steps:

1. load a checksum-pinned source manifest and acquire the original document;
2. extract prose through a source adapter;
3. run the language-specific parser and sentence segmenter without rewriting text;
4. retain structured candidates for review and language-scoped deduplication;
5. import only novel, resolved `HIGH` candidates into canonical quotes and minute eligibility.

`multilingual_candidates` retains the exact match, source and quote offsets, original excerpt,
context, possible minutes, semantic/confidence class, parser rule, evidence, review state, hashes,
duplicate linkage, provenance, and eventual canonical quote linkage. `multilingual_runs` records
stage totals. French and Chinese Gutenberg adapters are separate; French and Chinese Wikisource
adapters are separate even though all four normalize into the same candidate model.

Large inputs, SQLite databases, acquisition receipts, and CSV/Markdown review sets are ignored.
Only code, checksum/source manifests, tests, and this sanitized report are tracked.

## 2. Language schema

Schema version 2 adds language at every material layer:

| Layer | Fields / behavior |
|---|---|
| source | `language`, `script_variant`, project, translator/editor, publication and rights evidence |
| raw provenance | `raw_language`, `raw_script_variant`, translator/editor and rights metadata |
| canonical quote | explicit language, optional script, comparison hash, language identity hash |
| mining candidate | language, script, possible minutes, semantic type, confidence class |
| time semantics | language, exact matched text, possible-minutes JSON, semantic confidence |
| minute eligibility | explicit language copied from the canonical quote |
| statistics/query | `en`, `fr`, `zh`, and hypothetical `mixed` scopes |

Existing `en`, `en-GB`, and `en-US` records remain in the English family. New canonical rows use
`fr` or `zh`; Chinese provenance can retain `zh-Hans`/`zh-Hant`. Language is part of identity, so
translations in different languages are never deduplicated together. Simplified and Traditional
passages also retain distinct identities unless their original text is actually identical.

Canonical text is NFC-preserved source text. French accents, curly/straight apostrophes and `œ`
remain intact. Chinese is never converted between `點`/`点` or `時`/`时`. A separate comparison
form case-folds French and removes non-alphanumeric spacing/punctuation, but it does not remove
diacritics, expand ligatures, translate, romanize, or convert Chinese script.

## 3. French sources

FR-0 pinned six original-language Project Gutenberg works (4,977,230 characters scanned):

| eBook | Work | Creator |
|---:|---|---|
| 17489 | *Les misérables Tome I: Fantine* | Victor Hugo |
| 2650 | *Du côté de chez Swann* | Marcel Proust |
| 798 | *Le rouge et le noir: chronique du XIXe siècle* | Stendhal |
| 800 | *Le tour du monde en quatre-vingts jours* | Jules Verne |
| 5154 | *La Bête humaine* | Émile Zola |
| 5711 | *Germinal* | Émile Zola |

Each document has its official eBook page/download URL, release/work metadata, exact SHA-256 and
rights evidence in `corpus/multilingual_sources.json`. The official French Wikisource 2026-09-01
multistream dump is separately date/size/SHA-1 pinned for FR-3, but its four roughly 2.8 GB total
compressed partitions were intentionally not fetched before the pilot precision gate.

## 4. Chinese sources

ZH-0 pinned 21 original-language Project Gutenberg inputs (5,651,588 characters scanned):

`老殘遊記`, `徬徨`, `紅樓夢`, `朝花夕拾`, `狂人日記`, `阿Ｑ正傳`,
`二十年目睹之怪現狀` (two source editions), `官場現形記` (three source editions), `公墓`,
`文明小史`, `沉沦`, `歐遊雜記`, `商界現形記`, `吶喊`, `海上花列傳`, `孽海花`,
`瞎騙奇聞`, and `鄰女語`.

Titles and creator strings are stored in Chinese; no automatic Anglicization or romanization is
performed. Source-edition overlap remains visible rather than being merged across Simplified and
Traditional text. The official Chinese Wikisource 2026-09-01 multistream XML and index are pinned
by date, size and Wikimedia SHA-1 for ZH-3. The roughly 7.9 GB compressed XML was not downloaded
for the bounded pilot.

## 5. Provenance and licensing

The manifest records source project, official URL, language/script, original title and creator,
translator/editor when applicable, eBook/source ID, publication/release information, pinned
checksum, and the Project Gutenberg catalog rights wording. Acquisition time and verified local
checksum are recorded in the ignored receipt and database. The Wikisource pin manifest records
official dump identity rather than a mutable “latest” pointer.

Project Gutenberg describes these selected files as public domain in the USA. This report does
not extrapolate that metadata into a worldwide legal conclusion. Users outside the United States
must assess local law. No translation was imported; a public-domain original would not by itself
establish the status of a modern translation. Ambiguous rights would be retained for review, not
automatically imported.

## 6. French parser grammar

The dedicated parser uses tested structural numeral conversion and supports:

- `trois heures`, `trois heures cinq/dix`, `et quart`, `et demie`;
- `quatre heures moins le quart`, `quatre heures moins vingt`;
- explicit 24-hour constructions such as `quinze heures trente`;
- `midi`, `minuit`, `midi et demi`, `minuit et quart`;
- observed named-hour minutes such as `midi quarante-sept`, `midi moins vingt`,
  `minuit vingt`, and `minuit moins dix`;
- `du matin`, `de l'après-midi` (straight or curly apostrophe), and `du soir`;
- bounded numeric `h` forms retained behind the same quality gates.

Impossible values such as `25 heures`, `trois heures soixante-dix`, or 24:01 are explicit
rejections. `vingt-quatre heures` is retained as `MEDIUM` because corpus use was overwhelmingly a
duration rather than 00:00. Modifiers such as `vers`, `bientôt`, `près de`, alternatives with
`ou`, and ranges are review-only. The parser never substitutes normalized wording into the quote.

## 7. Chinese parser grammar

The dedicated parser supports Simplified and Traditional markers `点/點`, `时/時`, `钟/鐘`,
`分`, `刻`, `半`, and `差`. Numerals support Arabic digits, `〇/零`, `一` through `十`,
`十一` through `二十四`, and `两/兩`. Invalid digit sequences and values above 24:00 or minute
59 are rejected.

Covered structures include bare marked hours, bell-hour forms, explicit minutes, half/quarter,
three-quarter, and difference constructions such as `差一刻五点` and `差十分五点`. Explicit
24-hour forms such as `十五点三十分` resolve directly. Dayparts include `凌晨`, `清晨`, `早上`,
`上午`, `中午`, `下午/午後`, `傍晚`, `晚上`, `夜间/夜間`, `夜里/夜裡`, and `半夜`.

`下午三点多钟`, `约下午三点`, `三点左右`, and before/after thresholds are `MEDIUM`, not exact.
Sentence extraction recognizes `。！？；` and repeated ellipses while preserving original
full-width punctuation and codepoint offsets.

## 8. Ambiguity rules

Semantic interpretation and minute eligibility remain separate. French `trois heures` carries
`[03:00, 15:00]`; Chinese `三点钟` carries the same two possibilities. These candidates use
`AMBIGUOUS_CLOCKFACE` and receive no automatic eligibility rows. Explicit dayparts resolve one
side, while French hours above 12 and Chinese hours 13–24 resolve directly.

`midi` is 12:00 and `minuit` is 00:00. Chinese `中午十二点` is 12:00, `下午三点` is 15:00,
`晚上八点` is 20:00, and conservative overnight markers resolve only their plausible ranges.
Contradictory daypart/hour combinations are rejected or held for review rather than guessed from
narrative mood.

## 9. False-positive controls

French controls reject/demote source furniture, short fragments, duration phrases, geographic or
figurative `Midi`, approximate/range language, 24-hour-duration idioms, and uncontextualized
24-hour-looking forms. The pilot specifically falsified an early assumption that every `midi`
match was temporal; phrases about the French South were removed from `HIGH`.

Chinese bare `一点/三点` remains `MEDIUM` unless there is a structural marker or temporal gate.
`一点也不`, `一点办法`, `三点意见`, numbered lists, idiomatic durations, impossible clocks,
headings, short fragments, and corrupt mixed-script/OCR passages are rejected. Approximate `多钟`
and before/after thresholds are not eligible. Precision was not traded for minute coverage.

## 10. Manual precision review

The ignored deterministic export is stratified by parser rule, then ordered by stable language
identity hash. The final post-tuning review produced:

| Language | `HIGH` reviewed | True positives | False positives | Point precision | Reject sample | Ambiguous/medium sample |
|---|---:|---:|---:|---:|---:|---:|
| French | 100 | 100 | 0 | 100% | 100 | 100 |
| Chinese | 36 (complete `HIGH` census) | 36 | 0 | 100% | 100 | 100 |

French therefore passes the requested approximately 98% point-precision pilot target. Chinese
also passes on every available high-confidence candidate, but the bounded source set yielded only
36 before deduplication, so it cannot honestly provide a 100-item `HIGH` review sample. This is a
sample-size limitation, not a reason to promote ambiguous candidates. The next ZH-3 expansion
must repeat the precision audit on new material; these are post-tuning audits, not independent
held-out estimates.

Major tuning errors found and removed were geographic/figurative French `Midi`, duration/tolerance
uses of `vingt-quatre heures`, approximate French modifiers, non-temporal Chinese “points,” severe
mixed-script corruption, approximate `多钟`, and before/after thresholds.

A post-gate scout over 16 additional official Project Gutenberg Chinese fiction files found only
three tentative `HIGH` expressions before provenance admission. That low-yield set was not added
to the pinned corpus because it did not materially close the 100-item review gap. This negative
result supports moving the next expansion to the already pinned Wikisource bulk source instead of
weakening the grammar or accumulating unreviewed one-off inputs.

## 11. French coverage

Only the 148 novel resolved `HIGH` candidates were imported.

| Metric | French |
|---|---:|
| Canonical quotes | 148 |
| Unique selectable quotes | 148 |
| Quote-minute relationships | 148 |
| Minutes covered / 1,440 | 39 / 1,440 |
| Empty minutes | 1,401 |
| Minutes with exactly 1 / exactly 2 | 15 / 7 |
| Minutes with at least 3 / 5 / 7 | 17 / 5 / 4 |
| Median candidates/minute | 0 |
| P10 / P25 / P75 / P90 | 0 / 0 / 0 / 0 |
| Maximum candidates/minute | 28 |
| AM/PM-ambiguous review candidates | 562 |

All-candidate expression counts are: hour 733, hour+minute 122, `midi/minuit` 145,
`et demie` 50, `et quart` 3, `moins` 11, and numeric-`h` 1. These counts include review/reject
classes and must not be confused with selectable imports.

## 12. Chinese coverage

The 36 final `HIGH` candidates contained nine source-edition duplicates; 27 novel candidates were
imported. Script/source variants remain auditable in the candidate table.

| Metric | Chinese |
|---|---:|
| Canonical quotes | 27 |
| Unique selectable quotes | 27 |
| Quote-minute relationships | 27 |
| Minutes covered / 1,440 | 12 / 1,440 |
| Empty minutes | 1,428 |
| Minutes with exactly 1 / exactly 2 | 5 / 3 |
| Minutes with at least 3 / 5 / 7 | 4 / 1 / 0 |
| Median candidates/minute | 0 |
| P10 / P25 / P75 / P90 | 0 / 0 / 0 / 0 |
| Maximum candidates/minute | 5 |
| AM/PM-ambiguous review candidates | 449 |

All-candidate expression counts are: bare marked hour 2,529, bell-hour 456, half 17,
hour+minute 5, and quarter 29. Most bare occurrences are intentionally `MEDIUM`; high-confidence
coverage is concentrated in explicit daypart forms.

## 13. Multilingual-union coverage

The French+Chinese union has 175 canonical/selectable quotes and 175 relationships covering 42
minutes: 1,398 empty, 13 with exactly one candidate, 9 with two, 20 with at least three, 12 with
at least five, and 5 with at least seven. Median and P10/P25/P75/P90 remain zero.

The hypothetical English+French+Chinese `mixed` query has 7,404 canonical quotes, 7,266 unique
selectable quotes and 8,905 relationships. It remains 1,440/1,440 only because English was already
complete; this combined result is not presented as French or Chinese coverage.

## 14. English regression check

The migration and multilingual import were executed on a copy-on-write clone of the frozen local
English database. Before/after results were identical:

| Check | Before | After |
|---|---:|---:|
| English-family canonical rows | 7,229 | 7,229 |
| Unique selectable English quotes | 7,091 | 7,091 |
| English quote-minute relationships | 8,730 | 8,730 |
| English minutes covered | 1,440 | 1,440 |
| English P10 / P25 / median / P75 / P90 | 3 / 4 / 6 / 7 / 7 | 3 / 4 / 6 / 7 / 7 |

The ordered snapshot hash over all legacy quote text, language, metadata, hashes, and highlight
offsets was unchanged:
`897354589039ee564a6b80690882170802da59f24f28b6538daf236abf0a2d7c`.
The ordered legacy relationship snapshot was also unchanged:
`698e9e4799105dfa9a6c37182dacf4817b8ff993b17ef249f5d2726a39663b81`.
The default selector now explicitly scopes the existing English family, so future multilingual
rows cannot leak into the frozen English product behavior.

Validation completed with SQLite `integrity_check = ok`, zero foreign-key violations, schema
version 2, 455 passing tests, clean Ruff lint, and clean Ruff format check.

## 15. Unresolved sparse minutes

French has 1,401 empty minutes and Chinese 1,428. French coverage is concentrated on whole hours,
00:00/12:00, and a small number of observed minute constructions. Chinese covers only 01:00,
02:00, 03:00, 08:00, 13:00–17:00, 20:00, 21:00, and 23:00 in the resolved pilot. The
French+Chinese union covers 42 minutes.

This is the true precision-first sparse tail. The pipeline does not activate the 562 French or
449 Chinese clock-face-ambiguous candidates, and it does not weaken semantics to approach
1,440/1,440.

## 16. Rendering implications

No renderer or device file changed. French likely fits the current Latin character repertoire,
but production English font/typography remains frozen. Chinese requires a CJK-capable font,
line-breaking rules, punctuation handling, physical grayscale/dither tests, and separate layout
validation. Those are explicitly outside this corpus branch.

## 17. Recommended next multilingual phase

Proceed only after review of this branch:

1. run FR-3/ZH-3 against bounded portions of the pinned official Wikisource dumps, preserving
   work-level license/edition evidence and avoiding contributor/user pages;
2. obtain at least 100 newly reviewed Chinese `HIGH` examples without promoting bare or
   approximate forms, then report an independent precision estimate;
3. review the ambiguous clock-face pools as semantics, not automatic eligibility;
4. expand source diversity before sparse-minute targeting;
5. begin a separate multilingual renderer phase with CJK font and physical-device typography QA.

Do not merge French/Chinese into the production selector or alter the frozen PW4 typography as
part of that review.
