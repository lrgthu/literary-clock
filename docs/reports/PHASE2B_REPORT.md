# Literary Clock Phase 2B Report

Generated: 2026-09-05T15:32:34.747135+00:00

## Acquisition and eligibility

- Method: official compressed Project Gutenberg CSV and RDF bulk catalogs, followed by batched
  selective retrieval of generated UTF-8 plain text from official rsync mirrors.
- Normal `www.gutenberg.org` ebook pages crawled: **0**
- Catalog records: **79,288**
- Eligible English literary books: **37,154**
- Texts scanned before the final metadata audit: **37,596**
- Final eligible books scanned: **37,076**
- Eligible books without an official generated UTF-8 text:
  **77**
- Eligible books that failed text processing:
  **1**
- Bytes / words / characters scanned: **13,750,155,937 / 1,833,491,117 /
  10,098,760,521**
- Rights rule: exact Project Gutenberg metadata assertion “Public domain in the USA.” only.

The monolithic text archive was not duplicated locally. Selective official rsync used at most one
compressed stream per configured official mirror. The pilot cache was retained; processed full-run
texts were pruned only after checksums and candidates were committed. The database is the restart
manifest, and a JSONL manifest is exported after each completed stage.

### Staged rollout

| Stage | New books | Expressions | High confidence | Duplicates | Initial imports | Runtime |
|---|---:|---:|---:|---:|---:|---:|
| pilot-a | 500 | 6,208 | 873 | 608 | 6 | 1.0 min |
| pilot-b | 4,497 | 58,819 | 7,519 | 7,681 | 96 | 64.4 min |
| full | 24,653 | 340,153 | 46,757 | 60,662 | 654 | 380.8 min |

### Processing failures

- PG 39326, The History of Margaret Catchpole, a Suffolk Girl — Cobbold, Richard, 1797-1877: Project Gutenberg END marker not found

## Detection and correctness accounting

- Raw time expressions: **509,396**
- Exact resolved candidates: **91,947**
- High-confidence candidates: **59,813**
- AM/PM ambiguous candidates: **151,723**
- Approximate candidates: **237,902**
- Range candidates: **8,815**
- Invalid candidates: **19,009**
- Duplicates against the pre-existing canonical/Standard Ebooks corpus:
  **34,081**
- Duplicates of earlier Gutenberg candidates: **52,403**
- Duplicate candidates total: **86,484**
- Automatically imported Gutenberg quotes: **558**
- Prioritized human-review records: **702**
- Import-integrity failures (fields / provenance / cap):
  **0 / 0 /
  0**

### Candidate disposition

- DEFERRED_DENSE: 59,255
- IMPORTED: 558
- PENDING_REVIEW: 280,974
- REJECTED_DUPLICATE: 85,254
- REJECTED_FALSE_POSITIVE: 3,318
- REJECTED_INVALID: 7,040
- REJECTED_METADATA: 6,496
- REJECTED_QUALITY: 66,501

The disposition rows are mutually exclusive and sum to the raw candidate total. Duplicate and
false-positive diagnostics below are orthogonal flags, so their totals need not equal one primary
disposition row.

### False positives rejected by category

- CHAPTER_VERSE: 982
- LEGAL_REFERENCE: 2
- SCORE_RATIO: 1,781
- DIMENSION_RATIO: 164
- PAGE_LINE_REFERENCE: 647
- DATE_CATALOG_CODE: 214

## Coverage effect

- Selectable corpus before Phase 2B: **5,124**
- Selectable corpus now: **5,682**
- Newly covered minutes: **2**
- Buckets raised to 3 / 5 / 7: **90 /
  100 / 104**
- Minutes at 0: **1**
- Minutes below 3: **639**
- Minutes below 5: **1,072**
- Minutes below 7: **1,210**
- Minutes at least 7: **230**
- Median selectable quotes/minute: **3.00**
- P10 / P25 / P75 / P90: **1.00 / 2.00 /
  5.00 / 7.00**
- Remaining deficit to seven everywhere: **5,250**

### Contribution by corpus

- Legacy literary-clock corpora: **4,954**
- Standard Ebooks: **308**
- Project Gutenberg: **558**
- Gutenberg contribution diversity: **382 authors /
  432 books**
- Whole selectable corpus diversity: **1,837 authors /
  2,673 books**

## Empty-minute audit

The audit includes **17** candidate occurrences and does not force AM/PM.

- 15:46: selectable=0; AMPM_AMBIGUOUS=1, CITATION_OR_REFERENCE_FALSE_POSITIVE=3, CONTEXT_QUALITY_FAILURE=1, RANGE=1
- 16:19: selectable=1; CITATION_OR_REFERENCE_FALSE_POSITIVE=4, DUPLICATE=1, EXACT_IMPORTED=1
- 18:17: selectable=3; AMPM_AMBIGUOUS=1, DUPLICATE=1, EXACT_IMPORTED=3

## 50 hardest remaining buckets

1. 15:46 — 0 selectable, deficit 7
2. 00:06 — 1 selectable, deficit 6
3. 00:23 — 1 selectable, deficit 6
4. 00:24 — 1 selectable, deficit 6
5. 00:26 — 1 selectable, deficit 6
6. 00:31 — 1 selectable, deficit 6
7. 00:34 — 1 selectable, deficit 6
8. 00:47 — 1 selectable, deficit 6
9. 00:54 — 1 selectable, deficit 6
10. 00:57 — 1 selectable, deficit 6
11. 01:22 — 1 selectable, deficit 6
12. 01:24 — 1 selectable, deficit 6
13. 01:26 — 1 selectable, deficit 6
14. 01:37 — 1 selectable, deficit 6
15. 01:41 — 1 selectable, deficit 6
16. 01:42 — 1 selectable, deficit 6
17. 01:48 — 1 selectable, deficit 6
18. 01:51 — 1 selectable, deficit 6
19. 01:52 — 1 selectable, deficit 6
20. 01:56 — 1 selectable, deficit 6
21. 02:01 — 1 selectable, deficit 6
22. 02:04 — 1 selectable, deficit 6
23. 02:14 — 1 selectable, deficit 6
24. 02:18 — 1 selectable, deficit 6
25. 02:24 — 1 selectable, deficit 6
26. 02:26 — 1 selectable, deficit 6
27. 02:28 — 1 selectable, deficit 6
28. 02:31 — 1 selectable, deficit 6
29. 02:38 — 1 selectable, deficit 6
30. 02:42 — 1 selectable, deficit 6
31. 02:53 — 1 selectable, deficit 6
32. 02:56 — 1 selectable, deficit 6
33. 02:57 — 1 selectable, deficit 6
34. 03:03 — 1 selectable, deficit 6
35. 03:09 — 1 selectable, deficit 6
36. 03:19 — 1 selectable, deficit 6
37. 03:23 — 1 selectable, deficit 6
38. 03:28 — 1 selectable, deficit 6
39. 03:31 — 1 selectable, deficit 6
40. 03:34 — 1 selectable, deficit 6
41. 03:36 — 1 selectable, deficit 6
42. 03:37 — 1 selectable, deficit 6
43. 03:38 — 1 selectable, deficit 6
44. 03:39 — 1 selectable, deficit 6
45. 03:41 — 1 selectable, deficit 6
46. 03:43 — 1 selectable, deficit 6
47. 03:47 — 1 selectable, deficit 6
48. 03:49 — 1 selectable, deficit 6
49. 03:54 — 1 selectable, deficit 6
50. 03:56 — 1 selectable, deficit 6

## Gutenberg contribution by author

- Verne, Jules, 1828-1905: 11 quotes across 6 books
- Le Queux, William, 1864-1927: 9 quotes across 5 books
- Le Blond, Aubrey, Mrs., 1861-1934: 8 quotes across 3 books
- Griffiths, Arthur, 1838-1908: 7 quotes across 1 books
- Tracy, Louis, 1863-1928: 7 quotes across 5 books
- Dorling, H. Taprell (Henry Taprell), 1883-1968; Williams, C. Fleming [Illustrator]: 6 quotes across 1 books
- Harper, Charles G. (Charles George), 1863-1943: 6 quotes across 5 books
- Krepps, Robert W., 1919-1980; Terry, W. E. (Willis E.), 1921-1992 [Illustrator]: 6 quotes across 2 books
- Leinster, Murray, 1896-1975: 6 quotes across 4 books
- Bellew, H. W. (Henry Walter), 1834-1892: 5 quotes across 1 books
- Evans, A. J. (Alfred John), 1889-1960: 5 quotes across 1 books
- Heffner, George H.: 5 quotes across 1 books
- Raphael, Rick, 1919-1994; Freas, Kelly, 1922-2005 [Illustrator]: 5 quotes across 1 books
- Sayler, H. L. (Harry Lincoln), 1863-1913; Riesenberg, Sidney H., 1885-1971 [Illustrator]: 5 quotes across 2 books
- Barbour, Ralph Henry, 1870-1944; Chickering, Charles R. (Charles Ransom), 1891-1970 [Illustrator]: 4 quotes across 1 books
- Burton, Richard Francis, Sir, 1821-1890: 4 quotes across 4 books
- Gregory, Augustus Charles, 1819-1905; Gregory, Francis Thomas, 1821-1888: 4 quotes across 1 books
- McKinlay, John, 1819-1872: 4 quotes across 1 books
- Moorhouse, Herbert Joseph, 1882-: 4 quotes across 1 books
- Stanley, Henry M. (Henry Morton), 1841-1904: 4 quotes across 3 books

## Gutenberg contribution by book

- The Passenger from Calais — Griffiths, Arthur, 1838-1908: 7 quotes
- Pincher Martin, O.D.: A Story of the Inner Life of the Royal Navy — Dorling, H. Taprell (Henry Taprell), 1883-1968; Williams, C. Fleming [Illustrator]: 6 quotes
- Don't Panic! — Krepps, Robert W., 1919-1980; Terry, W. E. (Willis E.), 1921-1992 [Illustrator]: 5 quotes
- From the Indus to the Tigris — Bellew, H. W. (Henry Walter), 1834-1892: 5 quotes
- Make Mine Homogenized — Raphael, Rick, 1919-1994; Freas, Kelly, 1922-2005 [Illustrator]: 5 quotes
- The Escaping Club — Evans, A. J. (Alfred John), 1889-1960: 5 quotes
- The Youthful Wanderer An Account of a Tour through England, France, Belgium, Holland, Germany and the Rhine, Switzerland, Italy, and Egypt, Adapted to the Wants of Young Americans Taking Their First Glimpses at the Old World — Heffner, George H.: 5 quotes
- Benton's Venture — Barbour, Ralph Henry, 1870-1944; Chickering, Charles R. (Charles Ransom), 1891-1970 [Illustrator]: 4 quotes
- Every Man for Himself — Moorhouse, Herbert Joseph, 1882-: 4 quotes
- Journals of Australian Explorations — Gregory, Augustus Charles, 1819-1905; Gregory, Francis Thomas, 1821-1888: 4 quotes
- McKinlay's Journal of Exploration in the Interior of Australia — McKinlay, John, 1819-1872: 4 quotes
- The Invasion of 1910, with a full account of the siege of London — Le Queux, William, 1864-1927: 4 quotes
- True Tales of Mountain Adventures: For Non-Climbers Young and Old — Le Blond, Aubrey, Mrs., 1861-1934: 4 quotes
- Twenty Thousand Leagues under the Sea — Verne, Jules, 1828-1905: 4 quotes
- A Cruise in the Sky; or, The Legend of the Great Pink Pearl — Sayler, H. L. (Harry Lincoln), 1863-1913; Riesenberg, Sidney H., 1885-1971 [Illustrator]: 3 quotes
- Across Unknown South America — Landor, Arnold Henry Savage, 1865-1924: 3 quotes
- Adventures on the Roof of the World — Le Blond, Aubrey, Mrs., 1861-1934: 3 quotes
- Operation Terror — Leinster, Murray, 1896-1975: 3 quotes
- Scrambles Amongst the Alps in the Years 1860-69 — Whymper, Edward, 1840-1911: 3 quotes
- The Black Bag — Vance, Louis Joseph, 1879-1933; Fogarty, Thomas, 1873-1938 [Illustrator]: 3 quotes

## Recommendation for Phase 2C

Project Gutenberg alone is insufficient to reach seven verified quotes for every minute. Phase 2C should first review the exported AM/PM queue for the sparsest buckets, then add another independent public-domain corpus for the remaining deficits.
