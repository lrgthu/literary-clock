"""SQLite schema and row conversion helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from litclock.models import QualityStatus, Quote

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS import_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETE', 'FAILED')),
    raw_record_count INTEGER NOT NULL DEFAULT 0,
    canonical_inserted INTEGER NOT NULL DEFAULT 0,
    exact_duplicates INTEGER NOT NULL DEFAULT 0,
    trivial_variants INTEGER NOT NULL DEFAULT 0,
    malformed_records INTEGER NOT NULL DEFAULT 0,
    invalid_times INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    source_url TEXT NOT NULL,
    source_license TEXT NOT NULL,
    upstream_commit TEXT NOT NULL,
    corpus_path TEXT NOT NULL,
    corpus_sha256 TEXT NOT NULL,
    record_count INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY,
    minute_of_day INTEGER NOT NULL CHECK (minute_of_day BETWEEN 0 AND 1439),
    time_24h TEXT NOT NULL,
    time_text TEXT NOT NULL,
    quote TEXT NOT NULL,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    sfw INTEGER CHECK (sfw IN (0, 1) OR sfw IS NULL),
    language TEXT NOT NULL DEFAULT 'en',
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_license TEXT NOT NULL,
    source_record_id TEXT,
    quote_hash TEXT NOT NULL,
    normalized_quote_hash TEXT NOT NULL,
    highlight_start INTEGER,
    highlight_end INTEGER,
    quality_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (
        (highlight_start IS NULL AND highlight_end IS NULL)
        OR (highlight_start >= 0 AND highlight_end > highlight_start)
    ),
    UNIQUE (minute_of_day, normalized_quote_hash)
);

CREATE INDEX IF NOT EXISTS quotes_minute_idx ON quotes(minute_of_day);
CREATE INDEX IF NOT EXISTS quotes_author_idx ON quotes(author);
CREATE INDEX IF NOT EXISTS quotes_title_idx ON quotes(title);
CREATE INDEX IF NOT EXISTS quotes_quote_hash_idx ON quotes(quote_hash);

CREATE TABLE IF NOT EXISTS quote_provenance (
    id INTEGER PRIMARY KEY,
    quote_id INTEGER NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    import_run_id INTEGER NOT NULL REFERENCES import_runs(id),
    source_record_id TEXT,
    raw_time_24h TEXT NOT NULL,
    raw_time_text TEXT NOT NULL,
    raw_quote TEXT NOT NULL,
    raw_title TEXT NOT NULL,
    raw_author TEXT NOT NULL,
    raw_sfw INTEGER CHECK (raw_sfw IN (0, 1) OR raw_sfw IS NULL),
    raw_quote_hash TEXT NOT NULL,
    validation_status TEXT NOT NULL,
    highlight_start INTEGER,
    highlight_end INTEGER,
    duplicate_kind TEXT NOT NULL,
    raw_payload TEXT NOT NULL,
    UNIQUE (source_id, source_record_id)
);

CREATE INDEX IF NOT EXISTS provenance_quote_idx ON quote_provenance(quote_id);

CREATE TABLE IF NOT EXISTS quote_time_semantics (
    quote_id INTEGER PRIMARY KEY REFERENCES quotes(id) ON DELETE CASCADE,
    time_semantics TEXT NOT NULL,
    clockface_minute INTEGER CHECK (clockface_minute BETWEEN 0 AND 719),
    narrative_resolution TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    evidence_text TEXT,
    source_candidate_type TEXT,
    source_candidate_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quote_minute_eligibility (
    quote_id INTEGER NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
    minute_of_day INTEGER NOT NULL CHECK (minute_of_day BETWEEN 0 AND 1439),
    eligibility_type TEXT NOT NULL,
    confidence TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    evidence_text TEXT,
    source_candidate_type TEXT,
    source_candidate_id INTEGER,
    created_at TEXT NOT NULL,
    PRIMARY KEY (quote_id, minute_of_day)
);

CREATE INDEX IF NOT EXISTS quote_minute_eligibility_minute_idx
    ON quote_minute_eligibility(minute_of_day, quote_id);

CREATE TRIGGER IF NOT EXISTS quotes_default_minute_eligibility
AFTER INSERT ON quotes
WHEN NEW.quality_status IN ('VERIFIED_EXACT', 'VERIFIED_NORMALIZED')
BEGIN
    INSERT OR IGNORE INTO quote_time_semantics (
        quote_id, time_semantics, clockface_minute, narrative_resolution,
        evidence_type, evidence_text, created_at, updated_at
    ) VALUES (
        NEW.id, 'RESOLVED_24H', NEW.minute_of_day % 720, 'RESOLVED',
        'CANONICAL_MINUTE', NEW.time_24h, NEW.created_at, NEW.created_at
    );
    INSERT OR IGNORE INTO quote_minute_eligibility (
        quote_id, minute_of_day, eligibility_type, confidence,
        evidence_type, evidence_text, created_at
    ) VALUES (
        NEW.id, NEW.minute_of_day, 'EXACT_24H', 'VERIFIED',
        'CANONICAL_MINUTE', NEW.time_24h, NEW.created_at
    );
END;

CREATE TRIGGER IF NOT EXISTS quotes_verified_minute_eligibility
AFTER UPDATE OF quality_status ON quotes
WHEN NEW.quality_status IN ('VERIFIED_EXACT', 'VERIFIED_NORMALIZED')
 AND OLD.quality_status NOT IN ('VERIFIED_EXACT', 'VERIFIED_NORMALIZED')
BEGIN
    INSERT OR IGNORE INTO quote_time_semantics (
        quote_id, time_semantics, clockface_minute, narrative_resolution,
        evidence_type, evidence_text, created_at, updated_at
    ) VALUES (
        NEW.id, 'RESOLVED_24H', NEW.minute_of_day % 720, 'RESOLVED',
        'CANONICAL_MINUTE', NEW.time_24h, NEW.created_at, NEW.created_at
    );
    INSERT OR IGNORE INTO quote_minute_eligibility (
        quote_id, minute_of_day, eligibility_type, confidence,
        evidence_type, evidence_text, created_at
    ) VALUES (
        NEW.id, NEW.minute_of_day, 'EXACT_24H', 'VERIFIED',
        'CANONICAL_MINUTE', NEW.time_24h, NEW.created_at
    );
END;

CREATE VIEW IF NOT EXISTS quote_minute_pool AS
SELECT quote_id, minute_of_day, eligibility_type, confidence, evidence_type, evidence_text,
       source_candidate_type, source_candidate_id
FROM quote_minute_eligibility
UNION ALL
SELECT q.id, q.minute_of_day, 'EXACT_24H', 'LEGACY_VERIFIED',
       'LEGACY_CANONICAL_MINUTE', q.time_24h, NULL, NULL
FROM quotes AS q
WHERE q.quality_status IN ('VERIFIED_EXACT', 'VERIFIED_NORMALIZED')
  AND NOT EXISTS (
      SELECT 1 FROM quote_minute_eligibility AS e WHERE e.quote_id = q.id
  );

CREATE TABLE IF NOT EXISTS import_issues (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    import_run_id INTEGER NOT NULL REFERENCES import_runs(id),
    source_record_id TEXT,
    quality_status TEXT NOT NULL,
    error TEXT NOT NULL,
    raw_payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS display_history (
    id INTEGER PRIMARY KEY,
    quote_id INTEGER NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
    minute_of_day INTEGER NOT NULL CHECK (minute_of_day BETWEEN 0 AND 1439),
    displayed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS display_history_time_idx ON display_history(displayed_at);
CREATE INDEX IF NOT EXISTS display_history_quote_idx ON display_history(quote_id);

CREATE TABLE IF NOT EXISTS shuffle_state (
    minute_of_day INTEGER PRIMARY KEY CHECK (minute_of_day BETWEEN 0 AND 1439),
    cycle INTEGER NOT NULL,
    remaining_quote_ids TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS standard_ebooks_books (
    id INTEGER PRIMARY KEY,
    repository TEXT NOT NULL UNIQUE,
    source_url TEXT NOT NULL,
    default_branch TEXT NOT NULL,
    commit_sha TEXT,
    author TEXT,
    title TEXT,
    language TEXT,
    rights TEXT,
    source_license TEXT NOT NULL
        DEFAULT 'CC0 contributions; underlying text public domain in the US',
    acquisition_timestamp TEXT,
    processing_timestamp TEXT,
    processing_status TEXT NOT NULL DEFAULT 'DISCOVERED',
    content_checksum TEXT,
    text_file_count INTEGER NOT NULL DEFAULT 0,
    word_count INTEGER NOT NULL DEFAULT 0,
    character_count INTEGER NOT NULL DEFAULT 0,
    github_updated_at TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS standard_ebooks_status_idx
    ON standard_ebooks_books(processing_status);

CREATE TABLE IF NOT EXISTS mining_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    requested_book_limit INTEGER NOT NULL,
    books_acquired INTEGER NOT NULL DEFAULT 0,
    books_scanned INTEGER NOT NULL DEFAULT 0,
    books_skipped INTEGER NOT NULL DEFAULT 0,
    words_scanned INTEGER NOT NULL DEFAULT 0,
    characters_scanned INTEGER NOT NULL DEFAULT 0,
    expressions_detected INTEGER NOT NULL DEFAULT 0,
    exact_resolved INTEGER NOT NULL DEFAULT 0,
    ambiguous INTEGER NOT NULL DEFAULT 0,
    approximate INTEGER NOT NULL DEFAULT 0,
    ranges INTEGER NOT NULL DEFAULT 0,
    candidates_rejected INTEGER NOT NULL DEFAULT 0,
    duplicates_detected INTEGER NOT NULL DEFAULT 0,
    high_confidence_candidates INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS mined_candidates (
    id INTEGER PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES standard_ebooks_books(id) ON DELETE CASCADE,
    minute_of_day INTEGER CHECK (minute_of_day BETWEEN 0 AND 1439 OR minute_of_day IS NULL),
    time_24h TEXT,
    time_text TEXT NOT NULL,
    quote TEXT NOT NULL,
    author TEXT NOT NULL,
    title TEXT NOT NULL,
    source_repository TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_commit TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_section TEXT,
    source_locator TEXT NOT NULL,
    source_paragraph_index INTEGER NOT NULL,
    source_quote_start INTEGER NOT NULL,
    source_quote_end INTEGER NOT NULL,
    source_expression_start INTEGER NOT NULL,
    source_expression_end INTEGER NOT NULL,
    highlight_start INTEGER NOT NULL,
    highlight_end INTEGER NOT NULL,
    parser_rule TEXT NOT NULL,
    time_confidence TEXT NOT NULL,
    ampm_evidence TEXT,
    context_score REAL NOT NULL,
    literary_quality_score REAL NOT NULL,
    duplicate_status TEXT NOT NULL,
    duplicate_of_quote_id INTEGER REFERENCES quotes(id),
    duplicate_of_candidate_id INTEGER REFERENCES mined_candidates(id) ON DELETE SET NULL,
    target_priority REAL NOT NULL,
    review_status TEXT NOT NULL,
    rejection_reason TEXT,
    candidate_hash TEXT NOT NULL,
    normalized_quote_hash TEXT NOT NULL,
    imported_quote_id INTEGER REFERENCES quotes(id),
    imported_at TEXT,
    contextual_resolution TEXT,
    resolved_minute_of_day INTEGER
        CHECK (resolved_minute_of_day BETWEEN 0 AND 1439 OR resolved_minute_of_day IS NULL),
    evidence_type TEXT,
    evidence_text TEXT,
    evidence_source_locator TEXT,
    resolution_confidence TEXT,
    resolution_status TEXT NOT NULL DEFAULT 'UNPROCESSED',
    resolution_updated_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (
        book_id, source_file, source_paragraph_index,
        source_expression_start, source_expression_end, parser_rule
    )
);

CREATE INDEX IF NOT EXISTS mined_candidates_minute_idx ON mined_candidates(minute_of_day);
CREATE INDEX IF NOT EXISTS mined_candidates_review_idx
    ON mined_candidates(review_status, duplicate_status, time_confidence);
CREATE INDEX IF NOT EXISTS mined_candidates_hash_idx
    ON mined_candidates(normalized_quote_hash);

CREATE TABLE IF NOT EXISTS phase2a5_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETE', 'FAILED')),
    ambiguous_considered INTEGER NOT NULL DEFAULT 0,
    ambiguous_relevant INTEGER NOT NULL DEFAULT 0,
    contextually_resolved INTEGER NOT NULL DEFAULT 0,
    review_exported INTEGER NOT NULL DEFAULT 0,
    recovered_imported INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS gutenberg_books (
    ebook_id INTEGER PRIMARY KEY,
    pg_type TEXT NOT NULL,
    issued TEXT,
    title TEXT NOT NULL,
    language TEXT NOT NULL,
    authors TEXT NOT NULL,
    subjects TEXT NOT NULL,
    locc TEXT NOT NULL,
    bookshelves TEXT NOT NULL,
    rights TEXT,
    source_url TEXT NOT NULL,
    catalog_sha256 TEXT NOT NULL,
    eligibility_status TEXT NOT NULL DEFAULT 'UNASSESSED',
    eligibility_reason TEXT,
    literature_score REAL NOT NULL DEFAULT 0,
    text_path TEXT,
    text_sha256 TEXT,
    text_cached INTEGER NOT NULL DEFAULT 0 CHECK (text_cached IN (0, 1)),
    acquisition_timestamp TEXT,
    processing_status TEXT NOT NULL DEFAULT 'CATALOGED',
    processing_timestamp TEXT,
    byte_count INTEGER NOT NULL DEFAULT 0,
    word_count INTEGER NOT NULL DEFAULT 0,
    character_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS gutenberg_books_eligibility_idx
    ON gutenberg_books(eligibility_status, literature_score DESC);
CREATE INDEX IF NOT EXISTS gutenberg_books_processing_idx
    ON gutenberg_books(processing_status);

CREATE TABLE IF NOT EXISTS gutenberg_runs (
    id INTEGER PRIMARY KEY,
    stage TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETE', 'FAILED')),
    requested_book_limit INTEGER,
    books_acquired INTEGER NOT NULL DEFAULT 0,
    books_scanned INTEGER NOT NULL DEFAULT 0,
    books_skipped INTEGER NOT NULL DEFAULT 0,
    bytes_scanned INTEGER NOT NULL DEFAULT 0,
    words_scanned INTEGER NOT NULL DEFAULT 0,
    characters_scanned INTEGER NOT NULL DEFAULT 0,
    expressions_detected INTEGER NOT NULL DEFAULT 0,
    exact_resolved INTEGER NOT NULL DEFAULT 0,
    ambiguous INTEGER NOT NULL DEFAULT 0,
    approximate INTEGER NOT NULL DEFAULT 0,
    ranges INTEGER NOT NULL DEFAULT 0,
    invalid INTEGER NOT NULL DEFAULT 0,
    false_positives INTEGER NOT NULL DEFAULT 0,
    high_confidence_candidates INTEGER NOT NULL DEFAULT 0,
    duplicates_detected INTEGER NOT NULL DEFAULT 0,
    imported_quotes INTEGER NOT NULL DEFAULT 0,
    runtime_seconds REAL NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS gutenberg_candidates (
    id INTEGER PRIMARY KEY,
    ebook_id INTEGER NOT NULL REFERENCES gutenberg_books(ebook_id) ON DELETE CASCADE,
    minute_of_day INTEGER CHECK (minute_of_day BETWEEN 0 AND 1439 OR minute_of_day IS NULL),
    possible_minute_am INTEGER
        CHECK (possible_minute_am BETWEEN 0 AND 1439 OR possible_minute_am IS NULL),
    possible_minute_pm INTEGER
        CHECK (possible_minute_pm BETWEEN 0 AND 1439 OR possible_minute_pm IS NULL),
    time_24h TEXT,
    time_text TEXT NOT NULL,
    quote TEXT NOT NULL,
    author TEXT NOT NULL,
    title TEXT NOT NULL,
    subjects TEXT NOT NULL,
    bookshelves TEXT NOT NULL,
    rights TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    source_paragraph_index INTEGER NOT NULL,
    source_quote_start INTEGER NOT NULL,
    source_quote_end INTEGER NOT NULL,
    source_expression_start INTEGER NOT NULL,
    source_expression_end INTEGER NOT NULL,
    previous_paragraph TEXT,
    containing_paragraph TEXT NOT NULL,
    following_paragraph TEXT,
    highlight_start INTEGER NOT NULL,
    highlight_end INTEGER NOT NULL,
    parser_rule TEXT NOT NULL,
    time_confidence TEXT NOT NULL,
    ampm_evidence TEXT,
    context_score REAL NOT NULL,
    literary_quality_score REAL NOT NULL,
    false_positive_category TEXT,
    duplicate_status TEXT NOT NULL,
    duplicate_of_quote_id INTEGER REFERENCES quotes(id),
    duplicate_of_candidate_id INTEGER REFERENCES mined_candidates(id) ON DELETE SET NULL,
    duplicate_of_gutenberg_candidate_id INTEGER
        REFERENCES gutenberg_candidates(id) ON DELETE SET NULL,
    target_priority REAL NOT NULL,
    review_status TEXT NOT NULL,
    rejection_reason TEXT,
    candidate_hash TEXT NOT NULL UNIQUE,
    normalized_quote_hash TEXT NOT NULL,
    imported_quote_id INTEGER REFERENCES quotes(id),
    imported_at TEXT,
    created_at TEXT NOT NULL,
    CHECK (highlight_end > highlight_start),
    CHECK (substr(quote, highlight_start + 1, highlight_end - highlight_start) = time_text)
);

CREATE INDEX IF NOT EXISTS gutenberg_candidates_minute_idx
    ON gutenberg_candidates(minute_of_day);
CREATE INDEX IF NOT EXISTS gutenberg_candidates_possible_idx
    ON gutenberg_candidates(possible_minute_am, possible_minute_pm);
CREATE INDEX IF NOT EXISTS gutenberg_candidates_review_idx
    ON gutenberg_candidates(review_status, duplicate_status, time_confidence);
CREATE INDEX IF NOT EXISTS gutenberg_candidates_hash_idx
    ON gutenberg_candidates(normalized_quote_hash);

CREATE TABLE IF NOT EXISTS phase2c_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    counterfactual_saved_at TEXT,
    activated_at TEXT,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('RUNNING', 'COUNTERFACTUAL_COMPLETE', 'ACTIVATING', 'COMPLETE', 'FAILED')
    ),
    counterfactual_sha256 TEXT,
    baseline_json TEXT,
    scenario_b_json TEXT,
    scenario_c_json TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS phase2c_candidate_audit (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES phase2c_runs(id) ON DELETE CASCADE,
    source_candidate_type TEXT NOT NULL,
    source_candidate_id INTEGER NOT NULL,
    possible_minute_am INTEGER CHECK (possible_minute_am BETWEEN 0 AND 719),
    possible_minute_pm INTEGER CHECK (possible_minute_pm BETWEEN 720 AND 1439),
    parser_rule TEXT,
    parser_result TEXT NOT NULL,
    time_semantics TEXT NOT NULL,
    non_ampm_gate_status TEXT NOT NULL,
    gate_reason TEXT,
    displayed_resolution INTEGER CHECK (
        displayed_resolution BETWEEN 0 AND 1439 OR displayed_resolution IS NULL
    ),
    displayed_evidence_type TEXT,
    displayed_evidence_text TEXT,
    source_resolution INTEGER CHECK (
        source_resolution BETWEEN 0 AND 1439 OR source_resolution IS NULL
    ),
    source_evidence_type TEXT,
    source_evidence_text TEXT,
    decision TEXT NOT NULL,
    review_reason TEXT,
    context_score REAL NOT NULL DEFAULT 0,
    literary_quality_score REAL NOT NULL DEFAULT 0,
    author TEXT NOT NULL,
    title TEXT NOT NULL,
    time_text TEXT NOT NULL,
    source_locator TEXT,
    duplicate_status TEXT NOT NULL,
    canonical_quote_id INTEGER REFERENCES quotes(id),
    created_at TEXT NOT NULL,
    UNIQUE (run_id, source_candidate_type, source_candidate_id)
);

CREATE INDEX IF NOT EXISTS phase2c_candidate_audit_decision_idx
    ON phase2c_candidate_audit(run_id, decision, possible_minute_am, possible_minute_pm);

CREATE TABLE IF NOT EXISTS phase2c_plan_relationships (
    run_id INTEGER NOT NULL REFERENCES phase2c_runs(id) ON DELETE CASCADE,
    scenario TEXT NOT NULL CHECK (scenario IN ('B', 'C')),
    source_candidate_type TEXT NOT NULL,
    source_candidate_id INTEGER NOT NULL,
    minute_of_day INTEGER NOT NULL CHECK (minute_of_day BETWEEN 0 AND 1439),
    eligibility_type TEXT NOT NULL,
    PRIMARY KEY (
        run_id, scenario, source_candidate_type, source_candidate_id, minute_of_day
    )
);

CREATE INDEX IF NOT EXISTS phase2c_plan_minute_idx
    ON phase2c_plan_relationships(run_id, scenario, minute_of_day);

CREATE TABLE IF NOT EXISTS phase2d_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('TARGETED', 'RECOVERY_COMPLETE', 'ACQUIRING', 'MINING', 'COMPLETE', 'FAILED')
    ),
    starting_target_minutes INTEGER NOT NULL,
    starting_deficit_to_3 INTEGER NOT NULL,
    existing_candidates_considered INTEGER NOT NULL DEFAULT 0,
    existing_quotes_recovered INTEGER NOT NULL DEFAULT 0,
    dump_id INTEGER REFERENCES wikisource_dumps(id),
    error TEXT
);

CREATE TABLE IF NOT EXISTS phase2d_targets (
    run_id INTEGER NOT NULL REFERENCES phase2d_runs(id) ON DELETE CASCADE,
    minute_of_day INTEGER NOT NULL CHECK (minute_of_day BETWEEN 0 AND 1439),
    starting_count INTEGER NOT NULL CHECK (starting_count BETWEEN 0 AND 2),
    starting_deficit_to_3 INTEGER NOT NULL CHECK (starting_deficit_to_3 BETWEEN 1 AND 3),
    deficit_to_5 INTEGER NOT NULL,
    deficit_to_7 INTEGER NOT NULL,
    existing_quote_ids TEXT NOT NULL,
    authors TEXT NOT NULL,
    books TEXT NOT NULL,
    source_corpora TEXT NOT NULL,
    final_count INTEGER,
    PRIMARY KEY (run_id, minute_of_day)
);

CREATE TABLE IF NOT EXISTS phase2d_existing_candidate_audit (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES phase2d_runs(id) ON DELETE CASCADE,
    source_candidate_type TEXT NOT NULL,
    source_candidate_id INTEGER NOT NULL,
    possible_target_minutes TEXT NOT NULL,
    prior_status TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (
        decision IN ('RECOVERED', 'REVIEW', 'REJECTED', 'ALREADY_REPRESENTED')
    ),
    reason TEXT NOT NULL,
    recovered_quote TEXT,
    recovered_highlight_start INTEGER,
    recovered_highlight_end INTEGER,
    imported_quote_id INTEGER REFERENCES quotes(id),
    created_at TEXT NOT NULL,
    UNIQUE (run_id, source_candidate_type, source_candidate_id)
);

CREATE TABLE IF NOT EXISTS phase2d_checkpoints (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES phase2d_runs(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    pages_processed INTEGER NOT NULL,
    quotes_imported INTEGER NOT NULL,
    target_minutes_remaining INTEGER NOT NULL,
    deficit_to_3_remaining INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (run_id, label)
);

CREATE TABLE IF NOT EXISTS wikisource_dumps (
    id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    dump_date TEXT NOT NULL,
    source_url TEXT NOT NULL,
    checksum_algorithm TEXT NOT NULL,
    expected_checksum TEXT NOT NULL,
    actual_checksum TEXT,
    byte_size INTEGER,
    local_path TEXT NOT NULL,
    index_filename TEXT NOT NULL,
    index_source_url TEXT NOT NULL,
    index_expected_checksum TEXT NOT NULL,
    index_actual_checksum TEXT,
    index_local_path TEXT NOT NULL,
    acquisition_timestamp TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('DISCOVERED', 'DOWNLOADING', 'ACQUIRED', 'FAILED')
    ),
    error TEXT,
    UNIQUE (filename, expected_checksum)
);

CREATE TABLE IF NOT EXISTS wikisource_streams (
    dump_id INTEGER NOT NULL REFERENCES wikisource_dumps(id) ON DELETE CASCADE,
    stream_offset INTEGER NOT NULL,
    next_offset INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (
        status IN ('PENDING', 'RUNNING', 'COMPLETE', 'FAILED', 'SKIPPED')
    ),
    attempts INTEGER NOT NULL DEFAULT 0,
    page_count INTEGER NOT NULL DEFAULT 0,
    mainspace_pages INTEGER NOT NULL DEFAULT 0,
    page_namespace_pages INTEGER NOT NULL DEFAULT 0,
    characters_scanned INTEGER NOT NULL DEFAULT 0,
    expressions_detected INTEGER NOT NULL DEFAULT 0,
    sparse_relevant INTEGER NOT NULL DEFAULT 0,
    processed_at TEXT,
    error TEXT,
    PRIMARY KEY (dump_id, stream_offset)
);

CREATE INDEX IF NOT EXISTS wikisource_streams_status_idx
    ON wikisource_streams(dump_id, status, stream_offset);

CREATE TABLE IF NOT EXISTS wikisource_works (
    dump_id INTEGER NOT NULL REFERENCES wikisource_dumps(id) ON DELETE CASCADE,
    work_key TEXT NOT NULL,
    work_title TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    root_page_id INTEGER,
    root_revision_id INTEGER,
    index_page_title TEXT,
    source_license TEXT,
    license_evidence TEXT,
    license_status TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK (
        license_status IN ('PUBLIC_DOMAIN', 'FREE_LICENSE', 'UNKNOWN', 'RESTRICTED')
    ),
    literary_status TEXT NOT NULL DEFAULT 'UNASSESSED' CHECK (
        literary_status IN ('UNASSESSED', 'LITERARY', 'NONLITERARY', 'REVIEW')
    ),
    metadata_text TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (dump_id, work_key)
);

CREATE TABLE IF NOT EXISTS wikisource_candidates (
    id INTEGER PRIMARY KEY,
    dump_id INTEGER NOT NULL REFERENCES wikisource_dumps(id) ON DELETE CASCADE,
    page_id INTEGER NOT NULL,
    namespace INTEGER NOT NULL,
    page_title TEXT NOT NULL,
    revision_id INTEGER NOT NULL,
    revision_timestamp TEXT,
    work_key TEXT NOT NULL,
    work_title TEXT NOT NULL,
    author TEXT NOT NULL,
    source_license TEXT,
    license_evidence TEXT,
    source_url TEXT NOT NULL,
    dump_date TEXT NOT NULL,
    minute_of_day INTEGER CHECK (minute_of_day BETWEEN 0 AND 1439),
    possible_minute_am INTEGER CHECK (possible_minute_am BETWEEN 0 AND 719),
    possible_minute_pm INTEGER CHECK (possible_minute_pm BETWEEN 720 AND 1439),
    time_text TEXT NOT NULL,
    quote TEXT NOT NULL,
    previous_paragraph TEXT,
    containing_paragraph TEXT NOT NULL,
    following_paragraph TEXT,
    highlight_start INTEGER NOT NULL,
    highlight_end INTEGER NOT NULL,
    parser_rule TEXT NOT NULL,
    time_confidence TEXT NOT NULL,
    time_semantics TEXT NOT NULL,
    contextual_resolution INTEGER CHECK (contextual_resolution BETWEEN 0 AND 1439),
    evidence_type TEXT,
    evidence_text TEXT,
    context_score REAL NOT NULL,
    literary_quality_score REAL NOT NULL,
    duplicate_status TEXT NOT NULL,
    duplicate_of_quote_id INTEGER REFERENCES quotes(id),
    duplicate_of_candidate_id INTEGER REFERENCES wikisource_candidates(id),
    review_status TEXT NOT NULL,
    rejection_reason TEXT,
    normalized_quote_hash TEXT NOT NULL,
    passage_fingerprint TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    source_expression_start INTEGER NOT NULL,
    source_expression_end INTEGER NOT NULL,
    imported_quote_id INTEGER REFERENCES quotes(id),
    imported_at TEXT,
    created_at TEXT NOT NULL,
    CHECK (highlight_end > highlight_start),
    CHECK (substr(quote, highlight_start + 1, highlight_end - highlight_start) = time_text),
    UNIQUE (
        dump_id, page_id, revision_id,
        source_expression_start, source_expression_end, parser_rule
    )
);

CREATE INDEX IF NOT EXISTS wikisource_candidates_minute_idx
    ON wikisource_candidates(minute_of_day, possible_minute_am, possible_minute_pm);
CREATE INDEX IF NOT EXISTS wikisource_candidates_review_idx
    ON wikisource_candidates(review_status, duplicate_status, time_confidence);
CREATE INDEX IF NOT EXISTS wikisource_candidates_hash_idx
    ON wikisource_candidates(normalized_quote_hash);

CREATE TABLE IF NOT EXISTS wikisource_runs (
    id INTEGER PRIMARY KEY,
    phase2d_run_id INTEGER NOT NULL REFERENCES phase2d_runs(id),
    dump_id INTEGER NOT NULL REFERENCES wikisource_dumps(id),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETE', 'FAILED')),
    streams_scanned INTEGER NOT NULL DEFAULT 0,
    pages_scanned INTEGER NOT NULL DEFAULT 0,
    mainspace_pages INTEGER NOT NULL DEFAULT 0,
    page_namespace_pages INTEGER NOT NULL DEFAULT 0,
    literary_works_considered INTEGER NOT NULL DEFAULT 0,
    characters_scanned INTEGER NOT NULL DEFAULT 0,
    expressions_detected INTEGER NOT NULL DEFAULT 0,
    sparse_relevant INTEGER NOT NULL DEFAULT 0,
    duplicates_detected INTEGER NOT NULL DEFAULT 0,
    imported_quotes INTEGER NOT NULL DEFAULT 0,
    relationships_added INTEGER NOT NULL DEFAULT 0,
    runtime_seconds REAL NOT NULL DEFAULT 0,
    error TEXT
);
"""

_MINED_CANDIDATE_MIGRATIONS = {
    "contextual_resolution": "TEXT",
    "resolved_minute_of_day": "INTEGER CHECK (resolved_minute_of_day BETWEEN 0 AND 1439)",
    "evidence_type": "TEXT",
    "evidence_text": "TEXT",
    "evidence_source_locator": "TEXT",
    "resolution_confidence": "TEXT",
    "resolution_status": "TEXT NOT NULL DEFAULT 'UNPROCESSED'",
    "resolution_updated_at": "TEXT",
}

_GUTENBERG_BOOK_MIGRATIONS = {
    "text_cached": "INTEGER NOT NULL DEFAULT 0 CHECK (text_cached IN (0, 1))",
}


def connect_database(path: Path | str) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    columns = {
        str(row["name"]) for row in connection.execute("PRAGMA table_info(mined_candidates)")
    }
    for name, declaration in _MINED_CANDIDATE_MIGRATIONS.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE mined_candidates ADD COLUMN {name} {declaration}")
    gutenberg_columns = {
        str(row["name"]) for row in connection.execute("PRAGMA table_info(gutenberg_books)")
    }
    for name, declaration in _GUTENBERG_BOOK_MIGRATIONS.items():
        if name not in gutenberg_columns:
            connection.execute(f"ALTER TABLE gutenberg_books ADD COLUMN {name} {declaration}")
    connection.commit()


def backfill_primary_eligibility(connection: sqlite3.Connection) -> int:
    """Materialize the pre-Phase-2C one-quote/one-minute model without changing its meaning."""
    before = int(connection.execute("SELECT COUNT(*) FROM quote_minute_eligibility").fetchone()[0])
    connection.execute(
        """
        INSERT OR IGNORE INTO quote_time_semantics (
            quote_id, time_semantics, clockface_minute, narrative_resolution,
            evidence_type, evidence_text, created_at, updated_at
        )
        SELECT id, 'RESOLVED_24H', minute_of_day % 720, 'RESOLVED',
               'CANONICAL_MINUTE', time_24h, created_at, created_at
        FROM quotes
        WHERE quality_status IN ('VERIFIED_EXACT', 'VERIFIED_NORMALIZED')
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO quote_minute_eligibility (
            quote_id, minute_of_day, eligibility_type, confidence,
            evidence_type, evidence_text, created_at
        )
        SELECT id, minute_of_day, 'EXACT_24H', 'VERIFIED',
               'CANONICAL_MINUTE', time_24h, created_at
        FROM quotes
        WHERE quality_status IN ('VERIFIED_EXACT', 'VERIFIED_NORMALIZED')
        """
    )
    connection.commit()
    after = int(connection.execute("SELECT COUNT(*) FROM quote_minute_eligibility").fetchone()[0])
    return after - before


def row_to_quote(row: sqlite3.Row) -> Quote:
    sfw_value = row["sfw"]
    keys = set(row.keys())
    minute_of_day = (
        row["display_minute_of_day"] if "display_minute_of_day" in keys else row["minute_of_day"]
    )
    time_24h = row["display_time_24h"] if "display_time_24h" in keys else row["time_24h"]
    return Quote(
        id=row["id"],
        minute_of_day=minute_of_day,
        time_24h=time_24h,
        time_text=row["time_text"],
        quote=row["quote"],
        title=row["title"],
        author=row["author"],
        sfw=None if sfw_value is None else bool(sfw_value),
        language=row["language"],
        source_name=row["source_name"],
        source_url=row["source_url"],
        source_license=row["source_license"],
        source_record_id=row["source_record_id"],
        quote_hash=row["quote_hash"],
        normalized_quote_hash=row["normalized_quote_hash"],
        highlight_start=row["highlight_start"],
        highlight_end=row["highlight_end"],
        quality_status=QualityStatus(row["quality_status"]),
    )
