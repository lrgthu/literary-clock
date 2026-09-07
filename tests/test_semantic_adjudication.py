from __future__ import annotations

import csv
from pathlib import Path

import pytest
from conftest import empty_database, insert_quote

from litclock.semantic import (
    SEMANTIC_AUDIT_V1,
    SemanticAction,
    classify_clock_relationship,
)
from litclock.semantic_adjudication import (
    run_semantic_adjudication,
    validate_semantic_adjudication,
)
from litclock.semantic_audit import apply_semantic_audit, run_semantic_audit

FIXTURE = Path(__file__).parent / "fixtures" / "semantic_v2_cases.tsv"


def _fixture_rows() -> list[dict[str, str]]:
    with FIXTURE.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


@pytest.mark.parametrize("row", _fixture_rows(), ids=lambda row: row["case_id"])
def test_semantic_v2_regression_fixture(row: dict[str, str]) -> None:
    quote = row["quote"]
    phrase = row["phrase"]
    start = quote.index(phrase)
    decision = classify_clock_relationship(
        quote,
        start,
        start + len(phrase),
        int(row["minute"]),
        expected_text=phrase,
        parser_route="semantic-v2-regression",
    )
    assert decision.action == SemanticAction(row["action"])
    assert quote[start : start + len(phrase)] == phrase


@pytest.mark.parametrize(
    "quote",
    [
        "There was one five-minute interval.",
        "Two ten-minute pauses followed.",
        "John 12:16",
        "Chapter 12:16",
        "The ratio was 1:20.",
        "The score was 3:15.",
    ],
)
def test_required_false_positive_families_never_keep(quote: str) -> None:
    phrase = {
        "There was one five-minute interval.": "one five",
        "Two ten-minute pauses followed.": "Two ten",
        "John 12:16": "12:16",
        "Chapter 12:16": "12:16",
        "The ratio was 1:20.": "1:20",
        "The score was 3:15.": "3:15",
    }[quote]
    start = quote.index(phrase)
    decision = classify_clock_relationship(
        quote,
        start,
        start + len(phrase),
        65,
        expected_text=phrase,
        parser_route="adversarial",
    )
    assert decision.action != SemanticAction.KEEP


def test_wrong_minute_is_repaired_and_v1_evidence_is_preserved(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "repair.sqlite3")
    quote_id = insert_quote(
        connection,
        minute=4 * 60 + 15,
        time_24h="04:15",
        time_text="quarter to four",
        quote="At a quarter to four the visitor arrived.",
        title="A Clock Error",
        author="A. Writer",
    )
    v1 = run_semantic_audit(connection, audit_version=SEMANTIC_AUDIT_V1)

    v2 = run_semantic_adjudication(connection, prior_run_id=v1["run_id"])
    assert v2["repaired_relationships"] == 2
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM semantic_time_audit WHERE run_id = ?",
            (v1["run_id"],),
        ).fetchone()[0]
        == 1
    )
    assert (
        connection.execute(
            "SELECT adjudication_action FROM semantic_adjudications WHERE run_id = ?",
            (v2["run_id"],),
        ).fetchone()[0]
        == "REPAIR_MINUTE"
    )

    apply_semantic_audit(connection, v2["run_id"])
    assert [
        row[0]
        for row in connection.execute(
            "SELECT minute_of_day FROM quote_minute_pool WHERE quote_id = ? ORDER BY 1",
            (quote_id,),
        )
    ] == [3 * 60 + 45, 15 * 60 + 45]
    validation = validate_semantic_adjudication(connection, v2["run_id"])
    assert validation == {
        "integrity_check": "ok",
        "foreign_key_violations": 0,
        "orphan_adjudications": 0,
        "duplicate_active_eligibility": 0,
        "invalid_minutes": 0,
        "invalid_highlights": 0,
        "unjustified_production_relationships": 0,
        "invalid_revalidated_repairs": 0,
    }
    connection.close()


def test_source_backed_highlight_repair_is_revalidated(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "highlight.sqlite3")
    quote = "The face says half-past seven whenever anyone passes."
    quote_id = insert_quote(
        connection,
        minute=7 * 60 + 30,
        time_24h="07:30",
        time_text="seven",
        quote=quote,
        title="A Clock Face",
        author="B. Writer",
    )
    v1 = run_semantic_audit(connection, audit_version=SEMANTIC_AUDIT_V1)
    manual = tmp_path / "manual.tsv"
    manual.write_text(
        "quote_id\toriginal_minute\tadjudication_action\tcorrected_minutes\t"
        "corrected_highlight\tevidence_code\tevidence_text\treviewed_by\n"
        f"{quote_id}\t450\tREPAIR_MINUTE_AND_HIGHLIGHT\t450,1170\t"
        "half-past seven\tSOURCE_BACKED_HIGHLIGHT\tThe complete phrase is visible.\t"
        "semantic-adjudication-v2\n",
        encoding="utf-8",
    )

    v2 = run_semantic_adjudication(
        connection,
        prior_run_id=v1["run_id"],
        manual_path=manual,
    )
    apply_semantic_audit(connection, v2["run_id"])
    stored = connection.execute(
        "SELECT time_text, highlight_start, highlight_end, quote FROM quotes WHERE id = ?",
        (quote_id,),
    ).fetchone()
    assert stored["time_text"] == "half-past seven"
    assert (
        stored["quote"][stored["highlight_start"] : stored["highlight_end"]] == stored["time_text"]
    )
    assert [
        row[0]
        for row in connection.execute(
            "SELECT minute_of_day FROM quote_minute_pool WHERE quote_id = ? ORDER BY 1",
            (quote_id,),
        )
    ] == [450, 1170]

    apply_semantic_audit(connection, v1["run_id"])
    restored = connection.execute(
        "SELECT time_text, highlight_start, highlight_end, quote FROM quotes WHERE id = ?",
        (quote_id,),
    ).fetchone()
    assert restored["time_text"] == "seven"
    assert restored["quote"][restored["highlight_start"] : restored["highlight_end"]] == "seven"

    apply_semantic_audit(connection, v2["run_id"])
    reapplied = connection.execute(
        "SELECT time_text FROM quotes WHERE id = ?", (quote_id,)
    ).fetchone()
    assert reapplied["time_text"] == "half-past seven"
    connection.close()


def test_manual_adjudication_rejects_duplicate_or_unvalidated_data(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "invalid.sqlite3")
    quote_id = insert_quote(
        connection,
        minute=65,
        time_24h="01:05",
        time_text="one five",
        quote="There was one five-minute interval.",
        title="Duration",
        author="C. Writer",
    )
    v1 = run_semantic_audit(connection, audit_version=SEMANTIC_AUDIT_V1)
    manual = tmp_path / "manual.tsv"
    manual.write_text(
        "quote_id\toriginal_minute\tadjudication_action\tcorrected_minutes\t"
        "corrected_highlight\tevidence_code\tevidence_text\treviewed_by\n"
        f"{quote_id}\t65\tREPAIR_MINUTE\t65\t\tBAD_OVERRIDE\tNo valid evidence.\t"
        "semantic-adjudication-v2\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="repair failed v2 validation"):
        run_semantic_adjudication(
            connection,
            prior_run_id=v1["run_id"],
            manual_path=manual,
        )
    connection.close()


def test_reactivating_prior_audit_restores_original_time_text(tmp_path: Path) -> None:
    connection = empty_database(tmp_path / "time-text.sqlite3")
    quote_id = insert_quote(
        connection,
        minute=0,
        time_24h="00:00",
        time_text="Midnight",
        quote="Midnight came quietly.",
        title="Night",
        author="D. Writer",
    )
    connection.execute("UPDATE quotes SET time_text = 'midnight' WHERE id = ?", (quote_id,))
    connection.commit()
    v1 = run_semantic_audit(connection, audit_version=SEMANTIC_AUDIT_V1)
    manual = tmp_path / "manual.tsv"
    manual.write_text(
        "quote_id\toriginal_minute\tadjudication_action\tcorrected_minutes\t"
        "corrected_highlight\tevidence_code\tevidence_text\treviewed_by\n"
        f"{quote_id}\t0\tREPAIR_HIGHLIGHT\t0\tMidnight\tEXACT_SOURCE_HIGHLIGHT\t"
        "Preserve exact source case.\tsemantic-adjudication-v2\n",
        encoding="utf-8",
    )
    v2 = run_semantic_adjudication(
        connection,
        prior_run_id=v1["run_id"],
        manual_path=manual,
    )
    apply_semantic_audit(connection, v2["run_id"])
    assert (
        connection.execute("SELECT time_text FROM quotes WHERE id = ?", (quote_id,)).fetchone()[0]
        == "Midnight"
    )
    apply_semantic_audit(connection, v1["run_id"])
    assert (
        connection.execute("SELECT time_text FROM quotes WHERE id = ?", (quote_id,)).fetchone()[0]
        == "midnight"
    )
    connection.close()
