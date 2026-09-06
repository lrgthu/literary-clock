# Phase 2D Sparse-Tail Wikisource Report

Generated: 2026-09-05T21:35:29.275642+00:00

## Outcome

The exact starting deficit to three was **153** relationships across **123** minutes.
Existing retained candidates supplied **4** quotes before Wikisource. Wikisource then supplied **23** quotes and **27** effective relationships.
The retained-candidate pass eliminated **4** of the starting relationship deficit without acquiring a new source.
The remaining deficit is **122** relationships across **97** minutes.

## Acquisition and scan

- Method: official English Wikisource `pages-articles-multistream` XML dump plus its official multistream index; no page crawling.
- Dump: `enwikisource-20260901-pages-articles-multistream.xml.bz2` dated **20260901**.
- Source URL: https://dumps.wikimedia.org/enwikisource/20260901/enwikisource-20260901-pages-articles-multistream.xml.bz2
- SHA-1: `de12529676026ce7366010b680ea4fe9e778be3f` (matched official manifest).
- Acquired: 2026-09-05T20:36:15.152145+00:00.
- XML pages streamed: **4,751,918**.
- Target-filtered content pages rendered: **1,884** (963 mainspace; 921 `Page:`).
- Work metadata records indexed: **214,434**; classified literary: **60,998**.
- Rendered characters scanned: **44,168,357**.
- Time expressions detected on retained pages: **46,253**.
- Sparse-relevant detections: **2,127**.
- Cross-source duplicates: **593**.

## Corpus before and after

| Metric | Before Phase 2D | After Phase 2D |
|---|---:|---:|
| Canonical literary quotes | 7,202 | 7,229 |
| Unique selectable quotes | 7,064 | 7,091 |
| Effective eligibility relationships | 8,699 | 8,730 |
| Minutes at 0 | 0 | 0 |
| Minutes below 3 | 123 | 97 |
| Minutes below 5 | 450 | 450 |
| Minutes below 7 | 746 | 746 |
| Minutes at least 7 | 694 | 694 |
| Deficit to 3 everywhere | 153 | 122 |

Phase 2D imported only into pools below three, so the `<5`, `<7`, and `>=7` bucket memberships intentionally did not change.

## Candidate disposition

All **2,127** sparse-relevant candidates are accounted for below; this equals the stored sparse-relevant total.

- REJECTED_QUALITY: 1,139
- REJECTED_DUPLICATE: 593
- REVIEW_ATTRIBUTION: 324
- REVIEW_LICENSE: 48
- IMPORTED: 23

The focused human-review export contains **3** candidates that could still improve a live `<3` bucket and fail only a reviewable attribution, license, or context condition.

## Source and license distribution

- English Wikisource: 23 canonical quotes
- Project Gutenberg: 1,944 canonical quotes
- Standard Ebooks: 308 canonical quotes
- legacy literary-clock: 4,954 canonical quotes
- English Wikisource `Public domain in the United States`: 23

## Minutes that did not reach three

`00:24`, `00:26`, `00:31`, `00:34`, `01:31`, `01:38`, `01:39`, `01:41`, `01:46`, `01:48`, `01:51`, `02:31`, `02:49`, `03:09`, `03:23`, `03:31`, `03:39`, `03:43`, `04:24`, `04:26`, `04:39`, `04:43`, `05:48`, `06:19`, `06:44`, `07:36`, `07:41`, `07:46`, `07:48`, `09:32`, `09:41`, `09:44`, `10:18`, `10:19`, `10:36`, `11:19`, `11:26`, `11:31`, `11:33`, `11:38`, `12:14`, `12:19`, `12:31`, `13:31`, `13:36`, `13:38`, `13:39`, `13:43`, `13:46`, `13:47`, `13:48`, `13:51`, `14:19`, `14:26`, `14:31`, `14:46`, `14:49`, `15:19`, `15:24`, `15:39`, `15:43`, `15:46`, `16:09`, `16:24`, `16:26`, `16:34`, `16:47`, `17:44`, `17:47`, `18:09`, `18:11`, `18:24`, `18:39`, `18:44`, `19:21`, `19:36`, `19:46`, `20:11`, `20:31`, `20:36`, `20:39`, `21:19`, `21:33`, `21:34`, `21:38`, `21:39`, `21:41`, `21:44`, `21:49`, `22:36`, `22:51`, `22:54`, `23:14`, `23:26`, `23:31`, `23:36`, `23:46`

## Hardest remaining minutes

| Minute | Effective | Deficit to 3 | Authors | Books |
|---|---:|---:|---|---|
| 01:41 | 1 | 2 | E.W. Hornung | The Amateur Cracksman |
| 01:51 | 1 | 2 | Deon Meyer | Trackers |
| 03:31 | 1 | 2 | Marlin Desault | Shroud of Eden |
| 03:39 | 1 | 2 | William Jablonsky | The Clockwork Man |
| 04:24 | 1 | 2 | Carlos J. Cortes | Perfect Circle |
| 07:36 | 1 | 2 | John Ajvide Lindqvist | Let The Right One In |
| 07:46 | 1 | 2 | Henning Mankell | The Dogs of Riga |
| 09:41 | 1 | 2 | S.M. Stirling | The Stone Dogs |
| 11:19 | 1 | 2 | Connie Willis | Blackout |
| 13:36 | 1 | 2 | Fergus Hume | The Mystery of a Hansom Cab |
| 13:38 | 1 | 2 | Adam-Troy Castro | Sunday Night Yams at Minnie and Earl's |
| 13:43 | 1 | 2 | Jef Geeraerts | The Public Prosecutor |
| 14:31 | 1 | 2 | Matt Shaw | The Vampire's Treaty |
| 14:49 | 1 | 2 | Carsten Stroud | The Homecoming |
| 15:46 | 1 | 2 | Franck, Harry Alverson, 1881-1962 | Vagabonding down the Andes Being the Narrative of a Journey, Chiefly Afoot, from Panama to Buenos Aires |
| 16:24 | 1 | 2 | Travis Thrasher | Teardrop |
| 16:34 | 1 | 2 | Steven Hall | The Raw Shark Texts |
| 16:47 | 1 | 2 | Chad Harbach | The Art of Fielding |
| 18:39 | 1 | 2 | James Barrington | Manhunt |
| 19:36 | 1 | 2 | David Renwick | One Foot in the Grave |
| 21:19 | 1 | 2 | Ed McBain | Money, Money, Money |
| 21:44 | 1 | 2 | Charles Stough | Stone Flute |
| 21:49 | 1 | 2 | Malorie Blackman | Hacker |
| 22:36 | 1 | 2 | Hammond Innes | Air Bridge |
| 23:26 | 1 | 2 | Neil Gaiman | American Gods |
| 01:39 | 2 | 1 | Joanne Harris | blueeyedboy |
| 01:46 | 2 | 1 | J.W. Stockton | Fardnock's Revenge |
| 13:46 | 2 | 1 | Julian May | Jack the Bodiless |
| 14:46 | 2 | 1 | Robert Ludlum | The Parsifal Mosaic |
| 13:39 | 2 | 1 | Mark Haddon | The Curious Incident of the Dog in the Night-Time; The Curious Incident of the Dog in the Night-time |

## Verification

- Status: **PASS**
- pytest: 252 passed in 1.60s
- Ruff check: All checks passed
- Ruff format check: 54 files already formatted
- SQLite integrity: ok; foreign_key_check returned 0 rows
- Selector smoke test: PASS on a cloned production database; restart selected a Wikisource quote and respected prior history

## Recommendation

Do not lower correctness or literary-quality gates. The official usable Wikisource dump is exhausted for automatic Phase 2D imports, but the V1 three-per-minute threshold is not complete: 97 minutes remain. Review the compact queue first, then use a genuinely independent corpus only for the residual target set.
