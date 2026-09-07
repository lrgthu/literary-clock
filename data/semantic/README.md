# Semantic adjudication data

`english_v2_manual_adjudications.tsv` contains the evidence-backed exceptions and repairs used by
the deterministic English semantic v2 materialization. These rows are data, not hidden quote-ID
conditionals in the classifier.

Each row identifies the original quote/minute relationship, its adjudication action, any corrected
minute(s) or exact highlight, a machine-readable evidence code, a concise evidence statement, and
the reviewer label `semantic-adjudication-v2`. That label records an agent research pass; it does
not claim independent human review.

The complete v1 audit remains in the local generated database. Running `semantic-adjudicate`
creates a new v2 audit and keeps the v1 decisions inspectable. Repairs are revalidated by the same
semantic grammar before `semantic-apply` can expose them through `quote_minute_pool`.

Large detailed audit CSVs and SQLite databases remain under ignored `data/generated/` paths and
are not published with the repository.
