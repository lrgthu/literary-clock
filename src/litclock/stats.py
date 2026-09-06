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


def _percentile(values: list[int], fraction: float) -> float:
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    weight = position - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def _group_counts(
    connection: sqlite3.Connection, field: str, limit: int = 20
) -> list[dict[str, Any]]:
    if field not in {"author", "title"}:
        raise ValueError(f"unsupported grouping field: {field}")
    rows = connection.execute(
        f"""
        SELECT {field} AS name, COUNT(*) AS quote_count
        FROM quotes
        WHERE TRIM({field}) != ''
        GROUP BY {field}
        ORDER BY quote_count DESC, name COLLATE NOCASE
        LIMIT ?
        """,  # noqa: S608 - field is allow-listed above
        (limit,),
    )
    return [dict(row) for row in rows]


def _extreme_quote(connection: sqlite3.Connection, direction: str) -> dict[str, Any] | None:
    if direction not in {"ASC", "DESC"}:
        raise ValueError(direction)
    row = connection.execute(
        f"""
        SELECT id, time_24h, title, author, LENGTH(quote) AS characters
        FROM quotes
        ORDER BY characters {direction}, id
        LIMIT 1
        """  # noqa: S608 - direction is allow-listed above
    ).fetchone()
    return dict(row) if row else None


def calculate_stats(connection: sqlite3.Connection) -> dict[str, Any]:
    counts_by_minute = {
        int(row["minute_of_day"]): int(row["quote_count"])
        for row in connection.execute(
            """
            SELECT pool.minute_of_day, COUNT(*) AS quote_count
            FROM quote_minute_pool AS pool
            JOIN quotes AS q ON q.id = pool.quote_id
            GROUP BY pool.minute_of_day
            """
        )
    }
    renderable_by_minute = {
        int(row["minute_of_day"]): int(row["quote_count"])
        for row in connection.execute(
            """
            SELECT pool.minute_of_day, COUNT(*) AS quote_count
            FROM quote_minute_pool AS pool
            JOIN quotes AS q ON q.id = pool.quote_id
            WHERE q.quality_status IN (?, ?)
            GROUP BY pool.minute_of_day
            """,
            _RENDERABLE,
        )
    }
    books_by_minute = {
        int(row["minute_of_day"]): int(row["value_count"])
        for row in connection.execute(
            """
            SELECT pool.minute_of_day,
                   COUNT(DISTINCT NULLIF(TRIM(q.title), '')) AS value_count
            FROM quote_minute_pool AS pool JOIN quotes AS q ON q.id = pool.quote_id
            GROUP BY pool.minute_of_day
            """
        )
    }
    authors_by_minute = {
        int(row["minute_of_day"]): int(row["value_count"])
        for row in connection.execute(
            """
            SELECT pool.minute_of_day,
                   COUNT(DISTINCT NULLIF(TRIM(q.author), '')) AS value_count
            FROM quote_minute_pool AS pool JOIN quotes AS q ON q.id = pool.quote_id
            GROUP BY pool.minute_of_day
            """
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
    run_totals = connection.execute(
        """
        SELECT COALESCE(SUM(exact_duplicates), 0) AS exact_duplicates,
               COALESCE(SUM(trivial_variants), 0) AS trivial_variants
        FROM import_runs WHERE status = 'COMPLETE'
        """
    ).fetchone()
    total_raw_records = int(
        connection.execute(
            "SELECT (SELECT COUNT(*) FROM quote_provenance) + (SELECT COUNT(*) FROM import_issues)"
        ).fetchone()[0]
    )
    canonical_count = int(connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0])
    status_counts = {
        row["quality_status"]: int(row["status_count"])
        for row in connection.execute(
            """
            SELECT quality_status, COUNT(*) AS status_count
            FROM quotes GROUP BY quality_status ORDER BY quality_status
            """
        )
    }
    for row in connection.execute(
        """
        SELECT quality_status, COUNT(*) AS status_count
        FROM import_issues GROUP BY quality_status ORDER BY quality_status
        """
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
            """
            SELECT COUNT(DISTINCT q.id)
            FROM quote_minute_pool AS pool JOIN quotes AS q ON q.id = pool.quote_id
            WHERE q.quality_status IN (?, ?)
            """,
            _RENDERABLE,
        ).fetchone()[0]
    )
    result: dict[str, Any] = {
        "total_raw_records": total_raw_records,
        "total_canonical_quotes": canonical_count,
        "total_renderable_quotes": unique_selectable_count,
        "unique_selectable_quotes": unique_selectable_count,
        "quote_minute_eligibility_relationships": relationship_count,
        "total_effective_candidates": relationship_count,
        "exact_duplicates_removed": int(run_totals["exact_duplicates"]),
        "trivial_variants_merged": int(run_totals["trivial_variants"]),
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
        "remaining_quote_deficit_to_7": sum(max(0, 7 - count) for count in candidate_counts),
        "unique_books": int(
            connection.execute(
                "SELECT COUNT(DISTINCT title) FROM quotes WHERE TRIM(title) != ''"
            ).fetchone()[0]
        ),
        "unique_authors": int(
            connection.execute(
                "SELECT COUNT(DISTINCT author) FROM quotes WHERE TRIM(author) != ''"
            ).fetchone()[0]
        ),
        "shortest_quote": _extreme_quote(connection, "ASC"),
        "longest_quote": _extreme_quote(connection, "DESC"),
        "validation_status_counts": status_counts,
        "top_authors": _group_counts(connection, "author"),
        "top_books": _group_counts(connection, "title"),
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
    json_path = output_directory / "coverage.json"
    csv_path = output_directory / "minute_coverage.csv"
    markdown_path = output_directory / "COVERAGE_REPORT.md"
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
