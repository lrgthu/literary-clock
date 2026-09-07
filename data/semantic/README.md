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

`english_v1_final_recovery.tsv` is the bounded final-tail inventory applied after semantic v2.
It contains only the 47 source-backed quotations needed to eliminate empty and renderer-safe
singleton minute pools. Each of its 50 relationships is reclassified by semantic v2 during
materialization; the recovery layer cannot bypass a non-`KEEP` decision. Apply it to a semantic-v2
database with:

```bash
uv run python -m litclock.final_recovery \
  --db data/generated/litclock.english-v1-frozen.sqlite3 \
  --input data/semantic/english_v1_final_recovery.tsv
```

The generated SQLite database remains ignored. The committed TSV, semantic v2 rules, and v2
adjudications are the reproducible English V1 freeze inputs.
