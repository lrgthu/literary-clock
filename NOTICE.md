# Third-Party Corpus Notices

The MIT license in `LICENSE` applies only to original source code and documentation in this
repository. It does not relicense third-party quote data, quoted literary works, or artifacts
derived from those corpora.

Bulk corpora, legacy quote files, the normalized SQLite database, candidate queues, and rendered
quote images are intentionally excluded from the public Git repository. The paths below describe
the reproducible local data layout. Pinned legacy inputs are fetched on demand and checksum
verified; corpus-derived aggregate report snapshots are retained under `docs/reports/`.

## `kapoorankush/litclock`

- Repository: <https://github.com/kapoorankush/litclock>
- Pinned commit: `2d644ae640e74f0b1823fb14f50e12e7f4c089c8`
- Imported file: `image-gen/litclock_annotated.csv`
- Local snapshot after `litclock fetch` (Git-ignored):
  `data/third_party/kapoorankush-litclock/litclock_annotated.csv`
- Corpus SHA-256: `eaf30e5a037a3901a52ad8e3b54ed488a9e548d6d4bbcdcda18eae50a4b6bec5`
- Upstream corpus license: Creative Commons Attribution-NonCommercial-ShareAlike 4.0
  International (CC BY-NC-SA 4.0).

Upstream explicitly separates its licenses: its software is MIT, while the assembled quote
database and corpus-derived images are CC BY-NC-SA 4.0. The fetcher also verifies the upstream
`LICENSE` and `NOTICE.md` in the ignored local snapshot.

## `zenbuffy/LiteraryClock`

- Repository: <https://github.com/zenbuffy/LiteraryClock>
- Pinned commit: `465801e55f1877fe4d02f2090eca5bc87d1480ca`
- Imported file: `litclock.yaml`
- Local snapshot after `litclock fetch` (Git-ignored):
  `data/third_party/zenbuffy-literary-clock/litclock.yaml`
- Corpus SHA-256: `f5041a82eeed2f6e9741d7c0872fe3752cdb83e344d41636bd9c49a1e02eb6b9`
- License metadata recorded by this project: `NOASSERTION`.

The upstream repository has no explicit root license for its corpus. Its README states that a
significant portion of the quote data was merged from the JohsEnevoldsen corpus described below.
That statement does not establish a license for every contribution in the assembled Zenbuffy
file. The fetch manifest preserves the repository URL, checksum, and README; this project makes no
MIT claim for the data and grants no new rights. Users should resolve licensing for their intended
use.

The YAML, rather than the repository's generated CSV, is pinned because it is the current
structured source and correctly preserves multiline quotations.

## `JohsEnevoldsen/literature-clock`

- Repository: <https://github.com/JohsEnevoldsen/literature-clock>
- Pinned commit: `febdd2821b62e0ff060346a023426f9e2e6456b4`
- Imported file: `litclock_annotated.csv`
- Local snapshot after `litclock fetch` (Git-ignored):
  `data/third_party/johsenevoldsen-literature-clock/litclock_annotated.csv`
- Corpus SHA-256: `21a7f457d15984c225852e234c5dc4e7e5a940535c7a68c9521d6256994362d0`
- Upstream work license: Creative Commons Attribution-NonCommercial-ShareAlike 2.5 Generic
  (CC BY-NC-SA 2.5).

The fetcher verifies the upstream `LICENCE.md` in the ignored local snapshot.

## Standard Ebooks public-domain source repositories

- Repository index: <https://github.com/standardebooks>
- Source-layout documentation:
  <https://standardebooks.org/contribute/a-basic-standard-ebooks-source-folder>
- Public-domain explanation:
  <https://standardebooks.org/about/standard-ebooks-and-the-public-domain>
- Local catalog: `data/public_domain/standard_ebooks/catalog.json`
- Local per-book manifest: `data/public_domain/standard_ebooks/manifest.jsonl`
- Working cache: `data/public_domain/standard_ebooks/books/` (ignored by Git)

Each acquired repository's own license file says that its source text and artwork are believed to
be in the United States public domain, that they may remain copyrighted elsewhere, and that the
ebook creators and contributors dedicate their contributions to the worldwide public domain under
CC0 1.0 Universal. The pipeline preserves that file, OPF rights metadata, repository URL, exact
commit SHA, acquisition timestamp, and a checksum of the selected source material.

Neither this project's MIT license nor Standard Ebooks' CC0 dedication is asserted to erase rights
that may subsist in an underlying work outside the United States. Users remain responsible for
their jurisdiction and intended use.

## Project Gutenberg public-domain texts

- Bulk catalog feeds: <https://www.gutenberg.org/cache/epub/feeds/>
- Official mirroring guidance: <https://www.gutenberg.org/help/mirroring.html>
- Local catalog cache: `data/public_domain/gutenberg/catalog/` (ignored by Git)
- Local book manifest: `data/public_domain/gutenberg/books_manifest.jsonl` (ignored by Git)
- Working text cache: `data/public_domain/gutenberg/books/` (ignored by Git)

Phase 2B uses Project Gutenberg's machine-readable CSV and RDF catalogs and retrieves generated
plain-text files through official rsync mirrors. It does not crawl ebook landing pages. The
pipeline automatically mines only English literary works whose RDF metadata states exactly
`Public domain in the USA.` Works described as copyrighted or distributed with permission are
excluded. Ebook ID, title, authors, language, subjects, bookshelves, rights text, release date,
source identifier, catalog checksum, downloaded-file checksum, and acquisition time are retained.

Project Gutenberg's name and associated marks are not licensed for arbitrary reuse merely because
an underlying text is public domain. Project Gutenberg boilerplate, production credits, and its
license/trademark footer are excluded from mined quotations. This repository is not affiliated
with or endorsed by Project Gutenberg. Public-domain status is jurisdiction-specific; users must
evaluate use outside the United States.

## English Wikisource database dump

- Official dump root: <https://dumps.wikimedia.org/enwikisource/>
- Acquisition type: dated `pages-articles-multistream.xml.bz2` plus the corresponding official
  multistream index and SHA-1 manifest
- Local cache: `data/public_domain/wikisource/` (ignored by Git)
- Quote provenance: page title and ID, revision ID and timestamp, dump date, canonical source URL,
  work title and author, source locator, source license, and exact license evidence

The project does not crawl individual Wikisource pages. Wikisource is not treated as a uniformly
public-domain corpus. Source works may be public domain, freely licensed, permission-restricted, or
translations with separate rights; Wikisource contributor material is generally subject to
Wikimedia terms including CC BY-SA. Automatic Phase 2D import is limited to passages with
machine-verifiable work-level U.S. public-domain evidence. Unknown or non-public-domain licensing is
quarantined for review. Navigation, proofreading metadata, contributor apparatus, and license
templates are not included in extracted quotations. Users remain responsible for jurisdiction- and
edition-specific rights analysis.

## Literary text and derived outputs

Database-compilation terms do not necessarily resolve copyright in each quoted passage. Many
quoted books remain copyrighted. Downstream users are responsible for evaluating quotation,
attribution, noncommercial, share-alike, and underlying-work obligations for their jurisdiction
and use.

`data/generated/litclock.sqlite3`, machine-readable coverage files, review queues, and local phase
reports are corpus-derived outputs. Their generation does not erase the notices or license
metadata stored with each source and canonical quote. The curated Markdown copies in
`docs/reports/` remain corpus-derived and are provided for project review, not relicensed as MIT.

No font binaries from any upstream repository are copied into this project.

## KindleCron runtime dependency

The bounded Phase 4B device pilot used the official KindleCron (`kron`) v0.2.0 release from
<https://github.com/lennardollesch/KindleCron>. KindleCron is licensed upstream under the GNU Affero
General Public License v3.0. Its standalone binary is a local device dependency and is not copied
into or distributed by this repository. This project's MIT license does not apply to KindleCron.
