from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from conftest import empty_database, insert_quote

from litclock.normalize import normalized_quote_hash, text_hash
from litclock.phase2a5 import (
    coverage_snapshot_from_counts,
    high_confidence_accounting,
    import_recovered_candidates,
    resolve_contextual_ampm,
    run_phase2a5,
    simulate_counterfactual,
    sparse_priority,
)


def _insert_book(connection, *, repository: str = "author_book") -> int:
    now = datetime.now(UTC).isoformat()
    return int(
        connection.execute(
            """
            INSERT INTO standard_ebooks_books (
                repository, source_url, default_branch, commit_sha, author, title,
                language, rights, processing_status, content_checksum, created_at, updated_at
            ) VALUES (?, 'https://example.test/book', 'main', 'abc123', 'Author', 'Book',
                      'en-US', 'Public domain test fixture', 'PROCESSED', 'checksum', ?, ?)
            """,
            (repository, now, now),
        ).lastrowid
    )


def _insert_candidate(
    connection,
    book_id: int,
    *,
    quote: str,
    time_text: str,
    minute: int | None = None,
    review_status: str = "PENDING_REVIEW",
    resolution_status: str = "UNPROCESSED",
    resolved_minute: int | None = None,
    paragraph_index: int = 1,
) -> int:
    now = datetime.now(UTC).isoformat()
    start = quote.index(time_text)
    return int(
        connection.execute(
            """
            INSERT INTO mined_candidates (
                book_id, minute_of_day, time_24h, time_text, quote, author, title,
                source_repository, source_url, source_commit, source_file, source_section,
                source_locator, source_paragraph_index, source_quote_start, source_quote_end,
                source_expression_start, source_expression_end, highlight_start, highlight_end,
                parser_rule, time_confidence, ampm_evidence, context_score,
                literary_quality_score, duplicate_status, target_priority, review_status,
                rejection_reason, candidate_hash, normalized_quote_hash, created_at,
                resolved_minute_of_day, contextual_resolution, evidence_type, evidence_text,
                evidence_source_locator, resolution_confidence, resolution_status,
                resolution_updated_at
            ) VALUES (?, ?, ?, ?, ?, 'Author', 'Book', 'author_book',
                      'https://example.test/book', 'abc123', 'src/epub/text/chapter.xhtml',
                      'chapter', ?, ?, 0, ?, ?, ?, ?, ?, 'numeric', 'AMPM_AMBIGUOUS',
                      'no strong AM/PM evidence; 04:19|16:19', 90, 90, 'NEW', 1000, ?,
                      'ampm ambiguous', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book_id,
                minute,
                f"{minute // 60:02d}:{minute % 60:02d}" if minute is not None else None,
                time_text,
                quote,
                f"src/epub/text/chapter.xhtml#chapter:p{paragraph_index}:chars=0-{len(quote)}",
                paragraph_index,
                len(quote),
                start,
                start + len(time_text),
                start,
                start + len(time_text),
                review_status,
                text_hash(f"{quote}-{review_status}-{paragraph_index}"),
                normalized_quote_hash(quote),
                now,
                resolved_minute,
                (
                    f"{resolved_minute // 60:02d}:{resolved_minute % 60:02d}"
                    if resolved_minute is not None
                    else None
                ),
                "CONTAINING_SENTENCE_DAYPART" if resolved_minute is not None else None,
                "afternoon in containing sentence" if resolved_minute is not None else None,
                "fixture locator" if resolved_minute is not None else None,
                "DETERMINISTIC" if resolved_minute is not None else None,
                resolution_status,
                now if resolved_minute is not None else None,
            ),
        ).lastrowid
    )


def _write_fixture_xhtml(project_root: Path, paragraph: str) -> None:
    path = (
        project_root
        / "data"
        / "public_domain"
        / "standard_ebooks"
        / "books"
        / "author_book"
        / "src"
        / "epub"
        / "text"
        / "chapter.xhtml"
    )
    path.parent.mkdir(parents=True)
    path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
        <html xmlns="http://www.w3.org/1999/xhtml"
              xmlns:epub="http://www.idpf.org/2007/ops">
          <body epub:type="bodymatter z3998:fiction"><section id="chapter">
            <p>"""
        + paragraph
        + """</p>
          </section></body>
        </html>""",
        encoding="utf-8",
    )


def test_candidate_accounting_categories_sum_exactly(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "corpus.sqlite3")
    book_id = _insert_book(connection)
    for number in range(7):
        insert_quote(
            connection,
            minute=100,
            time_24h="01:40",
            time_text="1:40",
            quote=f"At 1:40, existing event {number} concluded beside the station.",
            title=f"Legacy {number}",
            author=f"Legacy {number}",
        )
    _insert_candidate(
        connection,
        book_id,
        quote="At 4:19, one deferred event occurred beside the quiet station.",
        time_text="4:19",
        minute=100,
        review_status="DEFERRED_DENSE",
    )
    _insert_candidate(
        connection,
        book_id,
        quote="At 4:19, one imported event occurred beside the quiet station.",
        time_text="4:19",
        minute=101,
        review_status="IMPORTED",
        paragraph_index=2,
    )
    _insert_candidate(
        connection,
        book_id,
        quote="At 4:19, one excess event occurred beside the quiet station.",
        time_text="4:19",
        minute=101,
        review_status="DEFERRED_DENSE",
        paragraph_index=3,
    )
    _insert_candidate(
        connection,
        book_id,
        quote="At 4:19, one unimported event occurred beside the quiet station.",
        time_text="4:19",
        minute=102,
        review_status="HIGH_CONFIDENCE",
        paragraph_index=4,
    )
    accounting = high_confidence_accounting(connection)
    categories = (
        "imported",
        "minute_already_full",
        "lower_priority_same_minute",
        "diversity_excluded",
        "quality_rejected_within_high_confidence",
        "other_eligibility_failure_within_high_confidence",
        "otherwise_eligible_not_imported",
    )
    assert accounting["total"] == sum(accounting.get(key, 0) for key in categories) == 4
    assert accounting["minute_already_full"] == 1
    assert accounting["lower_priority_same_minute"] == 1
    assert accounting["otherwise_eligible_not_imported"] == 1
    connection.close()


def test_counterfactual_coverage_respects_cap() -> None:
    current = {0: 0, 1: 6, 2: 8}
    scenario, counts = simulate_counterfactual(current, 14, {0: 2, 1: 3, 2: 5})
    assert counts == {0: 2, 1: 7, 2: 8}
    assert scenario["selectable_corpus_size"] == 17
    assert scenario["minutes_at_0"] == 1437
    assert scenario["minutes_below_3"] == 1438
    assert scenario["minutes_at_least_7"] == 2
    assert (
        coverage_snapshot_from_counts(current, 14)["remaining_deficit_to_7"]
        > scenario["remaining_deficit_to_7"]
    )


def test_contextual_resolver_accepts_direct_daypart() -> None:
    paragraph = "At 4:19 in the afternoon, the bell rang through the empty house."
    start = paragraph.index("4:19")
    result = resolve_contextual_ampm(paragraph, start, start + 4, (259, 979))
    assert result.minute_of_day == 979
    assert result.evidence_type == "CONTAINING_SENTENCE_DAYPART"
    assert "afternoon" in result.evidence_text


def test_contextual_resolver_rejects_weak_neighboring_daypart() -> None:
    previous = "It had been a difficult morning for everyone in the house."
    paragraph = "At 4:19, the bell rang through the empty rooms."
    start = paragraph.index("4:19")
    result = resolve_contextual_ampm(
        paragraph, start, start + 4, (259, 979), previous_paragraph=previous
    )
    assert result.minute_of_day is None
    assert result.confidence == "UNRESOLVED"
    assert "human narrative judgment" in result.proposed_evidence


def test_contextual_resolver_uses_neighboring_explicit_time_arithmetic() -> None:
    previous = "At 4:10 p.m. he entered the silent country station."
    paragraph = "Twenty minutes later, at half past four, the last train arrived."
    start = paragraph.index("half past four")
    result = resolve_contextual_ampm(
        paragraph,
        start,
        start + len("half past four"),
        (270, 990),
        previous_paragraph=previous,
    )
    assert result.minute_of_day == 990
    assert result.evidence_type == "NEIGHBOR_EXPLICIT_TIME_ELAPSED"
    assert "4:10 p.m." in result.evidence_text
    assert "twenty minutes later" in result.evidence_text.casefold()


def test_contextual_resolver_rejects_daypart_for_a_different_time() -> None:
    paragraph = "I left at 2:55—at nine next morning I was in Chicago."
    start = paragraph.index("2:55")
    result = resolve_contextual_ampm(paragraph, start, start + 4, (175, 895))
    assert result.minute_of_day is None
    assert result.confidence == "UNRESOLVED"

    paragraph = "I landed Friday morning, and left at 2:55—at nine next morning I was in Chicago."
    start = paragraph.index("2:55")
    result = resolve_contextual_ampm(paragraph, start, start + 4, (175, 895))
    assert result.minute_of_day is None


def test_sparse_bucket_priority_bands() -> None:
    assert [sparse_priority(count) for count in (0, 1, 2, 3, 5, 7)] == [6, 5, 4, 3, 2, 0]


def test_recovery_import_is_capped_and_preserves_provenance(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "corpus.sqlite3")
    book_id = _insert_book(connection)
    quote = "At 4:19 in the afternoon, the bell rang through the silent country house."
    candidate_id = _insert_candidate(
        connection,
        book_id,
        quote=quote,
        time_text="4:19",
        resolved_minute=979,
        resolution_status="AUTO_RESOLVED",
    )
    connection.commit()
    assert import_recovered_candidates(connection) == 1
    imported = connection.execute("SELECT * FROM quotes").fetchone()
    assert imported["minute_of_day"] == 979
    assert imported["quote"][imported["highlight_start"] : imported["highlight_end"]] == "4:19"
    candidate = connection.execute(
        "SELECT review_status, imported_quote_id FROM mined_candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    assert candidate["review_status"] == "IMPORTED_CONTEXTUAL"
    assert candidate["imported_quote_id"] == imported["id"]
    assert connection.execute("SELECT COUNT(*) FROM quote_provenance").fetchone()[0] == 1
    assert import_recovered_candidates(connection) == 0
    assert connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 1
    connection.close()


def test_recovery_rejects_a_citation_shaped_numeric_expression(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "corpus.sqlite3")
    book_id = _insert_book(connection)
    quote = "Matthew, 4:19—The editor then continued with the quoted religious passage."
    candidate_id = _insert_candidate(
        connection,
        book_id,
        quote=quote,
        time_text="4:19",
        resolved_minute=979,
        resolution_status="AUTO_RESOLVED",
    )
    connection.commit()
    assert import_recovered_candidates(connection) == 0
    row = connection.execute(
        "SELECT resolution_status, rejection_reason FROM mined_candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    assert row["resolution_status"] == "RESOLVED_QUALITY_REJECTED"
    assert "citation-shaped" in row["rejection_reason"]
    connection.close()


def test_phase2a5_restart_is_reproducible(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    paragraph = "At 4:19 in the afternoon, the bell rang through the silent country house."
    _write_fixture_xhtml(project_root, paragraph)
    connection = empty_database(project_root / "data" / "generated" / "corpus.sqlite3")
    book_id = _insert_book(connection)
    _insert_candidate(connection, book_id, quote=paragraph, time_text="4:19")
    connection.commit()

    first = run_phase2a5(connection, project_root)
    second = run_phase2a5(connection, project_root)

    assert first["newly_imported"] == 1
    assert second["newly_imported"] == 0
    assert connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 1
    assert first["resolution"]["resolved"] == second["resolution"]["resolved"] == 1
    assert (
        connection.execute("SELECT resolution_status FROM mined_candidates").fetchone()[0]
        == "IMPORTED_CONTEXTUAL"
    )
    assert (project_root / "data" / "generated" / "PHASE2A5_REPORT.md").exists()
    connection.close()
