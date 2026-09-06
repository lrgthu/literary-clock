"""Coverage calculation and report generation."""

from __future__ import annotations

import csv
import json
import sqlite3
import statistics
from pathlib import Path
from typing import Any

from litclock.normalize import minute_to_time

_RENDERABLE = ("VERIFIED_EXACT", "VERIFIED_NORMALIZED")


def _language_filter(language: str | None, alias: str = "q") -> tuple[str, tuple[str, ...]]:
    if language in {None, "mixed"}:
        return "1 = 1", ()
    return f"({alias}.language = ? OR {alias}.language LIKE ?)", (language, f"{language}-%")


def _percentile(values: list[int], fraction: float) -> float:
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    weight = position - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def _group_counts(
    connection: sqlite3.Connection, field: str, language: str | None, limit: int = 20
) -> list[dict[str, Any]]:
    if field not in {"author", "title"}:
        raise ValueError(f"unsupported grouping field: {field}")
    language_sql, language_params = _language_filter(language, "quotes")
    rows = connection.execute(
        f"""
        SELECT {field} AS name, COUNT(*) AS quote_count
        FROM quotes
        WHERE TRIM({field}) != '' AND {language_sql}
        GROUP BY {field}
        ORDER BY quote_count DESC, name COLLATE NOCASE
        LIMIT ?
        """,  # noqa: S608 - field is allow-listed above
        (*language_params, limit),
    )
    return [dict(row) for row in rows]


def _extreme_quote(
    connection: sqlite3.Connection, direction: str, language: str | None
) -> dict[str, Any] | None:
    if direction not in {"ASC", "DESC"}:
        raise ValueError(direction)
    language_sql, language_params = _language_filter(language, "quotes")
    row = connection.execute(
        f"""
        SELECT id, time_24h, title, author, LENGTH(quote) AS characters
        FROM quotes
        WHERE {language_sql}
        ORDER BY characters {direction}, id
        LIMIT 1
        """,  # noqa: S608 - direction/language_sql are internally constrained
        language_params,
    ).fetchone()
    return dict(row) if row else None


def calculate_stats(
    connection: sqlite3.Connection, *, language: str | None = "en"
) -> dict[str, Any]:
    """Calculate coverage for one language family or the multilingual union."""
    if language not in {None, "mixed", "en", "fr", "zh"}:
        raise ValueError(f"unsupported language: {language}")
    language_sql, language_params = _language_filter(language)
    counts_by_minute = {
        int(row["minute_of_day"]): int(row["quote_count"])
        for row in connection.execute(
            f"""
            SELECT pool.minute_of_day, COUNT(*) AS quote_count
            FROM quote_minute_pool AS pool
            JOIN quotes AS q ON q.id = pool.quote_id
            WHERE {language_sql}
            GROUP BY pool.minute_of_day
            """,  # noqa: S608 - language_sql is internally generated
            language_params,
        )
    }
    renderable_by_minute = {
        int(row["minute_of_day"]): int(row["quote_count"])
        for row in connection.execute(
            f"""
            SELECT pool.minute_of_day, COUNT(*) AS quote_count
            FROM quote_minute_pool AS pool
            JOIN quotes AS q ON q.id = pool.quote_id
            WHERE q.quality_status IN (?, ?) AND {language_sql}
            GROUP BY pool.minute_of_day
            """,  # noqa: S608 - language_sql is internally generated
            (*_RENDERABLE, *language_params),
        )
    }
    books_by_minute = {
        int(row["minute_of_day"]): int(row["value_count"])
        for row in connection.execute(
            f"""
            SELECT pool.minute_of_day,
                   COUNT(DISTINCT NULLIF(TRIM(q.title), '')) AS value_count
            FROM quote_minute_pool AS pool JOIN quotes AS q ON q.id = pool.quote_id
            WHERE {language_sql}
            GROUP BY pool.minute_of_day
            """,  # noqa: S608 - language_sql is internally generated
            language_params,
        )
    }
    authors_by_minute = {
        int(row["minute_of_day"]): int(row["value_count"])
        for row in connection.execute(
            f"""
            SELECT pool.minute_of_day,
                   COUNT(DISTINCT NULLIF(TRIM(q.author), '')) AS value_count
            FROM quote_minute_pool AS pool JOIN quotes AS q ON q.id = pool.quote_id
            WHERE {language_sql}
            GROUP BY pool.minute_of_day
            """,  # noqa: S608 - language_sql is internally generated
            language_params,
        )
    }
    minute_rows: list[dict[str, int | str]] = []
    for minute in range(1440):
        count = counts_by_minute.get(minute, 0)
        renderable = renderable_by_minute.get(minute, 0)
        minute_rows.append(
            {
                "minute_of_day": minute,
                "time_24h": minute_to_time(minute),
                "quote_count": count,
                "renderable_count": renderable,
                "unique_books": books_by_minute.get(minute, 0),
                "unique_authors": authors_by_minute.get(minute, 0),
                "deficit_to_7": max(0, 7 - renderable),
            }
        )
    ranked = sorted(
        minute_rows,
        key=lambda row: (
            row["renderable_count"],
            row["quote_count"],
            row["unique_authors"],
            row["unique_books"],
            row["minute_of_day"],
        ),
    )
    for rank, row in enumerate(ranked, 1):
        row["target_rank"] = rank
    rank_by_minute = {int(row["minute_of_day"]): int(row["target_rank"]) for row in ranked}
    for row in minute_rows:
        row["target_rank"] = rank_by_minute[int(row["minute_of_day"])]

    candidate_counts = sorted(int(row["renderable_count"]) for row in minute_rows)
    if language in {"en", "mixed", None}:
        run_totals = connection.execute(
            """
            SELECT COALESCE(SUM(exact_duplicates), 0) AS exact_duplicates,
                   COALESCE(SUM(trivial_variants), 0) AS trivial_variants
            FROM import_runs WHERE status = 'COMPLETE'
            """
        ).fetchone()
        exact_duplicates = int(run_totals["exact_duplicates"])
        trivial_variants = int(run_totals["trivial_variants"])
    else:
        exact_duplicates = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM multilingual_candidates
                WHERE language = ? AND duplicate_status != 'NEW'
                """,
                (language,),
            ).fetchone()[0]
        )
        trivial_variants = 0
    source_language_sql, source_language_params = _language_filter(language, "s")
    provenance_count = int(
        connection.execute(
            f"""
            SELECT COUNT(*) FROM quote_provenance AS p
            JOIN quotes AS q ON q.id = p.quote_id
            WHERE {language_sql}
            """,  # noqa: S608 - language_sql is internally generated
            language_params,
        ).fetchone()[0]
    )
    issue_count = int(
        connection.execute(
            f"""
            SELECT COUNT(*) FROM import_issues AS i
            JOIN sources AS s ON s.id = i.source_id
            WHERE {source_language_sql}
            """,  # noqa: S608 - source_language_sql is internally generated
            source_language_params,
        ).fetchone()[0]
    )
    total_raw_records = provenance_count + issue_count
    canonical_count = int(
        connection.execute(
            f"SELECT COUNT(*) FROM quotes AS q WHERE {language_sql}",  # noqa: S608
            language_params,
        ).fetchone()[0]
    )
    status_counts = {
        row["quality_status"]: int(row["status_count"])
        for row in connection.execute(
            f"""
            SELECT quality_status, COUNT(*) AS status_count
            FROM quotes AS q WHERE {language_sql}
            GROUP BY quality_status ORDER BY quality_status
            """,  # noqa: S608 - language_sql is internally generated
            language_params,
        )
    }
    for row in connection.execute(
        f"""
        SELECT quality_status, COUNT(*) AS status_count
        FROM import_issues AS i JOIN sources AS s ON s.id = i.source_id
        WHERE {source_language_sql}
        GROUP BY quality_status ORDER BY quality_status
        """,  # noqa: S608 - source_language_sql is internally generated
        source_language_params,
    ):
        status_counts[row["quality_status"]] = status_counts.get(row["quality_status"], 0) + int(
            row["status_count"]
        )
    for status in (
        "VERIFIED_EXACT",
        "VERIFIED_NORMALIZED",
        "AMBIGUOUS",
        "TIME_TEXT_NOT_FOUND",
        "INVALID_TIME",
        "MALFORMED",
    ):
        status_counts.setdefault(status, 0)

    thresholds = {
        "zero": sum(count == 0 for count in candidate_counts),
        "below_3": sum(count < 3 for count in candidate_counts),
        "below_5": sum(count < 5 for count in candidate_counts),
        "below_7": sum(count < 7 for count in candidate_counts),
        "at_least_7": sum(count >= 7 for count in candidate_counts),
        "at_least_14": sum(count >= 14 for count in candidate_counts),
    }
    relationship_count = sum(candidate_counts)
    unique_selectable_count = int(
        connection.execute(
            f"""
            SELECT COUNT(DISTINCT q.id)
            FROM quote_minute_pool AS pool JOIN quotes AS q ON q.id = pool.quote_id
            WHERE q.quality_status IN (?, ?) AND {language_sql}
            """,  # noqa: S608 - language_sql is internally generated
            (*_RENDERABLE, *language_params),
        ).fetchone()[0]
    )
    candidate_language_sql = "1 = 1" if language in {None, "mixed"} else "language = ?"
    candidate_language_params: tuple[str, ...] = () if language in {None, "mixed"} else (language,)
    confidence_class_counts = {
        str(row["confidence_class"]): int(row["n"])
        for row in connection.execute(
            f"""
            SELECT confidence_class, COUNT(*) AS n FROM multilingual_candidates
            WHERE {candidate_language_sql}
            GROUP BY confidence_class ORDER BY confidence_class
            """,  # noqa: S608 - candidate_language_sql is internally generated
            candidate_language_params,
        )
    }
    expression_type_counts = {
        str(row["parser_rule"]): int(row["n"])
        for row in connection.execute(
            f"""
            SELECT parser_rule, COUNT(*) AS n FROM multilingual_candidates
            WHERE {candidate_language_sql}
            GROUP BY parser_rule ORDER BY parser_rule
            """,  # noqa: S608 - candidate_language_sql is internally generated
            candidate_language_params,
        )
    }
    result: dict[str, Any] = {
        "total_raw_records": total_raw_records,
        "total_canonical_quotes": canonical_count,
        "total_renderable_quotes": unique_selectable_count,
        "unique_selectable_quotes": unique_selectable_count,
        "quote_minute_eligibility_relationships": relationship_count,
        "total_effective_candidates": relationship_count,
        "language": "mixed" if language is None else language,
        "exact_duplicates_removed": exact_duplicates,
        "trivial_variants_merged": trivial_variants,
        "minutes_covered": 1440 - thresholds["zero"],
        "missing_minutes": thresholds["zero"],
        "mean_quotes_per_minute": relationship_count / 1440,
        "median_quotes_per_minute": statistics.median(candidate_counts),
        "percentiles": {
            "p10": _percentile(candidate_counts, 0.10),
            "p25": _percentile(candidate_counts, 0.25),
            "p75": _percentile(candidate_counts, 0.75),
            "p90": _percentile(candidate_counts, 0.90),
        },
        "min_quotes_per_minute": min(candidate_counts),
        "max_quotes_per_minute": max(candidate_counts),
        "minute_thresholds": thresholds,
        "minutes_with_1": sum(count == 1 for count in candidate_counts),
        "minutes_with_2": sum(count == 2 for count in candidate_counts),
        "minutes_at_least_3": sum(count >= 3 for count in candidate_counts),
        "minutes_at_least_5": sum(count >= 5 for count in candidate_counts),
        "minutes_at_least_7": thresholds["at_least_7"],
        "ampm_ambiguity_count": confidence_class_counts.get("AMBIGUOUS_CLOCKFACE", 0),
        "confidence_class_counts": confidence_class_counts,
        "expression_type_counts": expression_type_counts,
        "remaining_quote_deficit_to_7": sum(max(0, 7 - count) for count in candidate_counts),
        "unique_books": int(
            connection.execute(
                f"""SELECT COUNT(DISTINCT title) FROM quotes AS q
                WHERE TRIM(title) != '' AND {language_sql}""",  # noqa: S608
                language_params,
            ).fetchone()[0]
        ),
        "unique_authors": int(
            connection.execute(
                f"""SELECT COUNT(DISTINCT author) FROM quotes AS q
                WHERE TRIM(author) != '' AND {language_sql}""",  # noqa: S608
                language_params,
            ).fetchone()[0]
        ),
        "shortest_quote": _extreme_quote(connection, "ASC", language),
        "longest_quote": _extreme_quote(connection, "DESC", language),
        "validation_status_counts": status_counts,
        "top_authors": _group_counts(connection, "author", language),
        "top_books": _group_counts(connection, "title", language),
        "urgent_minutes": ranked[:30],
        "minute_coverage": minute_rows,
    }
    return result


def _escape_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _markdown_report(stats: dict[str, Any]) -> str:
    thresholds = stats["minute_thresholds"]
    lines = [
        "# Literary Clock Coverage Report",
        "",
        f"Language scope: `{stats.get('language', 'en')}`.",
        "",
        "Generated from the pinned third-party inputs recorded in the database.",
        "Coverage metrics count selectable quotes with verified highlight offsets;",
        "canonical totals also include retained ambiguous and unmatched records.",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Raw upstream records | {stats['total_raw_records']:,} |",
        f"| Canonical quotes | {stats['total_canonical_quotes']:,} |",
        f"| Unique selectable quotes | {stats['unique_selectable_quotes']:,} |",
        f"| Quote-minute eligibility relationships | "
        f"{stats['quote_minute_eligibility_relationships']:,} |",
        f"| Effective candidates across minute pools | {stats['total_effective_candidates']:,} |",
        f"| Exact duplicates merged | {stats['exact_duplicates_removed']:,} |",
        f"| Trivial variants merged | {stats['trivial_variants_merged']:,} |",
        f"| Minutes covered | {stats['minutes_covered']:,} / 1,440 |",
        f"| Missing minutes | {stats['missing_minutes']:,} |",
        f"| Mean selectable quotes/minute | {stats['mean_quotes_per_minute']:.3f} |",
        f"| Median selectable quotes/minute | {stats['median_quotes_per_minute']:.3f} |",
        f"| P10 / P25 / P75 / P90 | {stats['percentiles']['p10']:.2f} / "
        f"{stats['percentiles']['p25']:.2f} / {stats['percentiles']['p75']:.2f} / "
        f"{stats['percentiles']['p90']:.2f} |",
        f"| Min / max quotes per minute | {stats['min_quotes_per_minute']} / "
        f"{stats['max_quotes_per_minute']} |",
        f"| Minutes with 0 / <3 / <5 / <7 | {thresholds['zero']} / "
        f"{thresholds['below_3']} / {thresholds['below_5']} / {thresholds['below_7']} |",
        f"| Minutes with >=7 / >=14 | {thresholds['at_least_7']} / {thresholds['at_least_14']} |",
        f"| Minutes with exactly 1 / exactly 2 | {stats['minutes_with_1']} / "
        f"{stats['minutes_with_2']} |",
        f"| Minutes with >=3 / >=5 | {stats['minutes_at_least_3']} / "
        f"{stats['minutes_at_least_5']} |",
        f"| AM/PM-ambiguous candidates | {stats['ampm_ambiguity_count']:,} |",
        f"| Remaining quote deficit to >=7 everywhere | "
        f"{stats['remaining_quote_deficit_to_7']:,} |",
        f"| Unique books / authors | {stats['unique_books']:,} / {stats['unique_authors']:,} |",
        "",
        "## Validation statuses",
        "",
        "| Status | Count |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {_escape_cell(status)} | {count:,} |"
        for status, count in sorted(stats["validation_status_counts"].items())
    )
    lines.extend(
        [
            "",
            "## Highest-priority minutes for new-book mining",
            "",
            "Ranking is ascending by renderable quotes, total canonical quotes, author diversity, "
            "book diversity, then clock time. Ties are genuinely equal-evidence priorities.",
            "",
            "| Rank | Time | Renderable | Canonical | Authors | Books | Deficit to 7 |",
            "|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in stats["urgent_minutes"]:
        lines.append(
            f"| {row['target_rank']} | {row['time_24h']} | {row['renderable_count']} | "
            f"{row['quote_count']} | {row['unique_authors']} | {row['unique_books']} | "
            f"{row['deficit_to_7']} |"
        )
    for heading, key in (
        ("Overrepresented authors", "top_authors"),
        ("Overrepresented books", "top_books"),
    ):
        lines.extend(["", f"## {heading}", "", "| Name | Quotes |", "|---|---:|"])
        lines.extend(
            f"| {_escape_cell(row['name'])} | {row['quote_count']:,} |" for row in stats[key]
        )
    for label, key in (("Shortest quote", "shortest_quote"), ("Longest quote", "longest_quote")):
        row = stats[key]
        if row:
            lines.extend(
                [
                    "",
                    f"## {label}",
                    "",
                    f"Quote `{row['id']}` at {row['time_24h']}: {row['characters']:,} characters, "
                    f"{_escape_cell(row['title'])} — {_escape_cell(row['author'])}.",
                ]
            )
    return "\n".join(lines) + "\n"


def write_reports(stats: dict[str, Any], output_directory: Path) -> tuple[Path, Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    language = str(stats.get("language", "en"))
    suffix = "" if language == "en" else f".{language}"
    json_path = output_directory / f"coverage{suffix}.json"
    csv_path = output_directory / f"minute_coverage{suffix}.csv"
    markdown_path = output_directory / f"COVERAGE_REPORT{suffix}.md"
    json_payload = {key: value for key, value in stats.items() if key != "minute_coverage"}
    json_path.write_text(
        json.dumps(json_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    fieldnames = [
        "target_rank",
        "minute_of_day",
        "time_24h",
        "quote_count",
        "renderable_count",
        "unique_books",
        "unique_authors",
        "deficit_to_7",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(stats["minute_coverage"])
    markdown_path.write_text(_markdown_report(stats), encoding="utf-8")
    return json_path, csv_path, markdown_path
