# Report Snapshots

These Markdown files are curated snapshots from the local operational corpus. They let reviewers
inspect phase outcomes without publishing the multi-gigabyte SQLite database, ebook caches,
candidate queues, or bitmap suite.

Reports contain aggregate results and limited audit context. They are corpus-derived and are not
relicensed as MIT; see [`../../NOTICE.md`](../../NOTICE.md). Machine-readable JSON/CSV and full
review exports remain under ignored `data/generated/` in the local working tree.

The renderer endpoint is `PHASE4A4_1_PRODUCTION_CLEANUP_REPORT.md`, following the physical
comparison in `PHASE4A4_PHYSICAL_COMPARISON_REPORT.md`. The subsequent
`PHASE4B_STANDALONE_RUNTIME_REPORT.md` records the bounded, offline Kindle runtime pilot and clearly
separates its successful autonomy/time-jump evidence from the still-unproven 24/7 deep-sleep
lifecycle. Earlier reports document how the corpus progressed through normalization,
public-domain mining, clock-face eligibility, and sparse-tail recovery.

`PHASE3_RENDER_REPORT.md` is the historical pre-finalization renderer report; it records the
long-quote/data-handoff failure that the finalization pass later traced and fixed. Use
`PHASE3_RENDER_FINALIZATION_REPORT.md`, `PHASE3_1_CLEANUP_REPORT.md`,
`PHASE4A3_DATE_AND_TIME_FONT_REPORT.md`, `PHASE4A4_PHYSICAL_COMPARISON_REPORT.md`, and
`PHASE4A4_1_PRODUCTION_CLEANUP_REPORT.md` for current renderer status.

Preview bitmaps and contact sheets are deliberately not published because they contain third-party
quotation text and are fully regenerable from a licensed local corpus with `litclock render-qa`.
