"""Command-line interface for fetching, importing, analyzing, and selecting quotes."""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from litclock.bundle import build_pw4_bundle, minute_window
from litclock.db import connect_database, initialize_database
from litclock.gutenberg import acquire_catalog
from litclock.gutenberg_mining import (
    calculate_gutenberg_stats,
    export_gutenberg_review,
    import_gutenberg,
    revalidate_gutenberg_candidates,
    run_all_gutenberg_stages,
    run_gutenberg_stage,
    write_phase2b_report,
)
from litclock.importers import default_source_specs, import_corpora
from litclock.importers.fetch import fetch_sources
from litclock.mining import (
    calculate_mining_stats,
    export_review_queue,
    import_high_confidence,
    mine_standard_ebooks,
    write_phase2a_report,
)
from litclock.models import Quote
from litclock.phase2a5 import run_phase2a5
from litclock.phase2c import (
    activate_phase2c,
    build_phase2c_counterfactual,
    write_phase2c_outputs,
)
from litclock.phase2d import (
    recover_existing_candidates,
    start_phase2d,
    write_phase2d_outputs,
    write_phase2d_review_export,
)
from litclock.render.date_label import current_local_date, parse_date_override
from litclock.render.models import AttributionStyle, DitherMode, RenderMode, TimeEmphasis
from litclock.render.pillow_renderer import PillowRenderer
from litclock.render.preview import load_quote_by_id, run_render_qa, save_rendered_frame
from litclock.render.production import PW4_V1_RENDER_CONFIG, resolve_production_fonts
from litclock.render.profiles import get_device_profile
from litclock.render.suitability import is_renderable_for_device
from litclock.render.typography import FontSelection, discover_font, discover_time_font
from litclock.selector import NoQuoteAvailable, QuoteSelector
from litclock.semantic_adjudication import (
    run_semantic_adjudication,
    write_semantic_adjudication_report,
)
from litclock.semantic_audit import (
    apply_semantic_audit,
    run_semantic_audit,
    write_semantic_audit_artifacts,
    write_semantic_report,
)
from litclock.stats import calculate_stats, write_reports
from litclock.wikisource import acquire_dump
from litclock.wikisource_mining import mine_wikisource_dump, revalidate_wikisource_imports

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "generated" / "litclock.sqlite3"
DEFAULT_REPORT_DIRECTORY = PROJECT_ROOT / "data" / "generated"


def current_hhmm(now_provider: Callable[[], datetime] = datetime.now) -> str:
    return now_provider().strftime("%H:%M")


def terminal_preview(quote: Quote, *, ansi: bool) -> str:
    start, end = quote.highlight_start, quote.highlight_end
    if start is None or end is None:
        rendered = quote.quote
    elif ansi:
        rendered = f"{quote.quote[:start]}\033[1m{quote.quote[start:end]}\033[0m{quote.quote[end:]}"
    else:
        rendered = f"{quote.quote[:start]}**{quote.quote[start:end]}**{quote.quote[end:]}"
    attribution = ", ".join(part for part in (quote.title, quote.author) if part)
    return f"{rendered}\n\n— {attribution}\n\n[{quote.time_24h} · quote {quote.id}]"


def _database_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE, help="SQLite corpus path")


def _font_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--font",
        type=Path,
        help="single regular face fallback (bold/italic unavailable; prefer four face options)",
    )
    parser.add_argument("--font-regular", type=Path, help="regular face for an explicit family")
    parser.add_argument("--font-bold", type=Path, help="bold face for an explicit family")
    parser.add_argument("--font-italic", type=Path, help="italic face for an explicit family")
    parser.add_argument(
        "--font-bold-italic", type=Path, help="bold italic face for an explicit family"
    )
    parser.add_argument(
        "--time-font",
        type=Path,
        help="single accent face used exactly as supplied; prefer the regular/bold pair",
    )
    parser.add_argument("--time-font-regular", type=Path, help="regular accent-family face")
    parser.add_argument("--time-font-bold", type=Path, help="bold accent-family face")


def _font_selection(args: argparse.Namespace):
    selection = discover_font(
        args.font,
        regular_path=args.font_regular,
        bold_path=args.font_bold,
        italic_path=args.font_italic,
        bold_italic_path=args.font_bold_italic,
    )
    if not selection.is_complete_family:
        print(
            "warning: single-face font fallback is active; bold time emphasis and italic title "
            "are unavailable",
            file=sys.stderr,
        )
    return selection


def _time_font_selection(args: argparse.Namespace):
    if not any((args.time_font, args.time_font_regular, args.time_font_bold)):
        return None
    selection = discover_time_font(
        args.time_font,
        regular_path=args.time_font_regular,
        bold_path=args.time_font_bold,
    )
    if not selection.has_bold:
        print(
            "warning: the explicit time font has no verified bold face; the supplied face "
            "will be used honestly (picturesque mode adds its documented measured ink stroke)",
            file=sys.stderr,
        )
    return selection


def _render_arguments(
    parser: argparse.ArgumentParser,
    *,
    selection: bool,
    default_preview: bool = False,
) -> None:
    _database_argument(parser)
    parser.add_argument("--device", default="pw4", help="built-in profile or custom")
    parser.add_argument(
        "--orientation",
        choices=("portrait", "landscape"),
        default="landscape",
        help="PW4 orientation (default: landscape)",
    )
    parser.add_argument("--width", type=int, help="custom pixel width (requires --height)")
    parser.add_argument("--height", type=int, help="custom pixel height (requires --width)")
    parser.add_argument("--output", type=Path, help="PNG path; defaults under render_previews")
    _font_arguments(parser)
    parser.add_argument("--mode", choices=tuple(RenderMode), default=RenderMode.GRAYSCALE.value)
    parser.add_argument("--dither", choices=tuple(DitherMode), default=DitherMode.THRESHOLD.value)
    parser.add_argument(
        "--attribution-style",
        choices=tuple(AttributionStyle),
        default=AttributionStyle.BOOK_AUTHOR.value,
    )
    parser.add_argument(
        "--time-emphasis",
        choices=tuple(TimeEmphasis),
        default=TimeEmphasis.SUBTLE_LIFT.value,
        help="inline time treatment (default: subtle-lift)",
    )
    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument(
        "--show-date",
        dest="show_date",
        action="store_true",
        help="show the renderer-owned date label",
    )
    date_group.add_argument(
        "--hide-date",
        dest="show_date",
        action="store_false",
        help="hide the renderer-owned date label",
    )
    parser.set_defaults(show_date=None)
    parser.add_argument(
        "--date",
        help="deterministic local date override in YYYY-MM-DD form",
    )
    parser.add_argument(
        "--date-format",
        choices=("short",),
        default="short",
        help="date label format (default: short)",
    )
    if selection:
        parser.add_argument("--seed", type=int, help="deterministic selector RNG seed")
        parser.add_argument("--sfw-only", action="store_true")
        parser.add_argument(
            "--preview",
            action="store_true",
            default=default_preview,
            help="do not mutate shuffle state or display history",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="litclock", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    fetch_parser = commands.add_parser("fetch", help="download pinned third-party corpus files")
    fetch_parser.add_argument(
        "--force", action="store_true", help="replace existing verified files"
    )

    import_parser = commands.add_parser("import", help="atomically rebuild the normalized corpus")
    _database_argument(import_parser)

    stats_parser = commands.add_parser("stats", help="calculate and write corpus coverage reports")
    _database_argument(stats_parser)
    stats_parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_DIRECTORY)

    show_parser = commands.add_parser("show", help="select a quote for HH:MM")
    show_parser.add_argument("time", help="24-hour time such as 16:37")
    _database_argument(show_parser)
    show_parser.add_argument("--seed", type=int, help="deterministic RNG seed")
    show_parser.add_argument("--sfw-only", action="store_true")

    now_parser = commands.add_parser("show-now", help="select a quote for the current local time")
    _database_argument(now_parser)
    now_parser.add_argument("--seed", type=int, help="deterministic RNG seed")
    now_parser.add_argument("--sfw-only", action="store_true")

    render_parser = commands.add_parser("render", help="select and render a quote for HH:MM")
    render_parser.add_argument("time", help="24-hour display time such as 16:37")
    _render_arguments(render_parser, selection=True)

    render_now_parser = commands.add_parser(
        "render-now", help="select and render a quote for the current local minute"
    )
    _render_arguments(render_now_parser, selection=True)

    render_id_parser = commands.add_parser(
        "render-id", help="render a stable quote ID without changing selector history"
    )
    render_id_parser.add_argument("quote_id", type=int)
    render_id_parser.add_argument(
        "--time", help="eligible HH:MM to use for a shared-clock-face quote"
    )
    _render_arguments(render_id_parser, selection=False)

    render_qa_parser = commands.add_parser(
        "render-qa", help="generate deterministic visual QA frames and reports"
    )
    _database_argument(render_qa_parser)
    _font_arguments(render_qa_parser)
    render_qa_parser.add_argument(
        "--time-emphasis",
        choices=tuple(TimeEmphasis),
        default=TimeEmphasis.SUBTLE_LIFT.value,
    )

    production_parser = commands.add_parser(
        "render-pw4-v1",
        help="render through the frozen, fail-closed PW4 landscape production contract",
    )
    production_parser.add_argument(
        "time",
        nargs="?",
        help="24-hour display time; defaults to the current local minute",
    )
    _database_argument(production_parser)
    production_parser.add_argument("--output", type=Path, help="destination PNG path")
    production_parser.add_argument("--seed", type=int, help="deterministic selector RNG seed")
    production_parser.add_argument("--sfw-only", action="store_true")
    production_parser.add_argument(
        "--preview",
        action="store_true",
        help="do not mutate shuffle state or display history",
    )
    production_parser.add_argument(
        "--date",
        help="deterministic local date override in YYYY-MM-DD form",
    )
    production_parser.set_defaults(
        device="pw4",
        orientation="landscape",
        width=None,
        height=None,
        mode=PW4_V1_RENDER_CONFIG.mode.value,
        dither=PW4_V1_RENDER_CONFIG.dither.value,
        attribution_style=PW4_V1_RENDER_CONFIG.attribution_style.value,
        time_emphasis=PW4_V1_RENDER_CONFIG.time_emphasis.value,
        show_date=PW4_V1_RENDER_CONFIG.show_date,
    )

    bundle_parser = commands.add_parser(
        "build-pw4-bundle",
        help="build or estimate a standalone Kindle bundle through the frozen PW4 contract",
    )
    _database_argument(bundle_parser)
    bundle_parser.add_argument("--output", type=Path, required=True)
    bundle_parser.add_argument(
        "--estimate-only",
        action="store_true",
        help="render a bounded sample and project storage without building the bundle",
    )
    bundle_parser.add_argument("--sample-size", type=int, default=100)
    bundle_parser.add_argument(
        "--pilot-start",
        help="first local HH:MM in a consecutive pilot window",
    )
    bundle_parser.add_argument("--pilot-count", type=int, default=15)
    bundle_parser.add_argument(
        "--pilot-date",
        help="generate only this ISO date overlay for a small pilot",
    )

    semantic_audit_parser = commands.add_parser(
        "semantic-audit",
        help=(
            "classify all selectable English quote-minute relationships without activating changes"
        ),
    )
    _database_argument(semantic_audit_parser)
    semantic_audit_parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_REPORT_DIRECTORY / "semantic-audit",
    )

    semantic_apply_parser = commands.add_parser(
        "semantic-apply",
        help="activate one complete semantic audit and exclude QUARANTINE/REVIEW relationships",
    )
    _database_argument(semantic_apply_parser)
    semantic_apply_parser.add_argument("run_id", type=int)

    semantic_report_parser = commands.add_parser(
        "semantic-report",
        help="write semantic audit impact artifacts and the committed summary report",
    )
    _database_argument(semantic_report_parser)
    semantic_report_parser.add_argument("--run-id", type=int)
    semantic_report_parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_REPORT_DIRECTORY / "semantic-audit",
    )
    semantic_report_parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_ROOT / "docs" / "reports" / "CORPUS_SEMANTIC_REVALIDATION_REPORT.md",
    )
    semantic_adjudicate_parser = commands.add_parser(
        "semantic-adjudicate",
        help="run semantic v2 adjudication and stage validated existing-corpus repairs",
    )
    _database_argument(semantic_adjudicate_parser)
    semantic_adjudicate_parser.add_argument("--prior-run-id", type=int)
    semantic_adjudicate_parser.add_argument(
        "--manual",
        type=Path,
        default=PROJECT_ROOT / "data" / "semantic" / "english_v2_manual_adjudications.tsv",
    )
    semantic_adjudication_report_parser = commands.add_parser(
        "semantic-adjudication-report",
        help="write v2 adjudication transitions, repairs, targets, and final report",
    )
    _database_argument(semantic_adjudication_report_parser)
    semantic_adjudication_report_parser.add_argument("--run-id", type=int)
    semantic_adjudication_report_parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_REPORT_DIRECTORY / "semantic-adjudication",
    )
    semantic_adjudication_report_parser.add_argument(
        "--renderer-audit",
        type=Path,
        default=DEFAULT_REPORT_DIRECTORY / "semantic-adjudication" / "renderer_audit.json",
    )
    semantic_adjudication_report_parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_ROOT / "docs" / "reports" / "CORPUS_SEMANTIC_ADJUDICATION_REPORT.md",
    )

    mine_parser = commands.add_parser(
        "mine-standard-ebooks", help="acquire and mine a bounded Standard Ebooks sample"
    )
    _database_argument(mine_parser)
    mine_parser.add_argument(
        "--limit-books",
        type=int,
        default=20,
        help="maximum new books to acquire/process in this run (default: 20)",
    )
    mine_parser.add_argument("--workers", type=int, default=4, help="acquisition workers (1-8)")
    mine_parser.add_argument("--refresh-catalog", action="store_true")
    mine_parser.add_argument("--reprocess", action="store_true")

    mining_stats_parser = commands.add_parser(
        "mining-stats", help="report accumulated Standard Ebooks mining results"
    )
    _database_argument(mining_stats_parser)

    review_parser = commands.add_parser("review-export", help="export candidates for review")
    _database_argument(review_parser)
    review_parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_REPORT_DIRECTORY / "standard_ebooks_review.csv",
    )

    mined_import_parser = commands.add_parser(
        "import-mined", help="import conservative high-confidence mined candidates"
    )
    _database_argument(mined_import_parser)
    mined_import_parser.add_argument("--confidence", choices=("high",), default="high")
    mined_import_parser.add_argument("--target-per-minute", type=int, default=7)

    phase2a5_parser = commands.add_parser(
        "phase2a5", help="audit and recover candidates from the existing Standard Ebooks corpus"
    )
    _database_argument(phase2a5_parser)
    phase2a5_parser.add_argument(
        "--audit-only", action="store_true", help="generate the audit without importing recoveries"
    )
    phase2a5_parser.add_argument(
        "--desktop-report",
        type=Path,
        default=Path.home() / "Desktop" / "LITERARY_CLOCK_PHASE2A5_REPORT.md",
        help="local copy of the final Markdown report",
    )

    gutenberg_parser = commands.add_parser(
        "mine-gutenberg", help="mine official Project Gutenberg bulk/offline resources"
    )
    _database_argument(gutenberg_parser)
    gutenberg_parser.add_argument(
        "--stage", choices=("pilot-a", "pilot-b", "full", "all"), default="pilot-a"
    )
    gutenberg_parser.add_argument(
        "--limit-books", type=int, help="override the cumulative book limit for one stage"
    )
    gutenberg_parser.add_argument("--workers", type=int, default=4)
    gutenberg_parser.add_argument("--batch-size", type=int, default=250)
    gutenberg_parser.add_argument("--refresh-catalog", action="store_true")
    gutenberg_parser.add_argument(
        "--keep-text-cache",
        action="store_true",
        help="retain processed raw texts during the full scan (requires substantial disk space)",
    )

    gutenberg_stats_parser = commands.add_parser(
        "gutenberg-stats", help="report accumulated Project Gutenberg mining results"
    )
    _database_argument(gutenberg_stats_parser)

    gutenberg_review_parser = commands.add_parser(
        "gutenberg-review-export", help="export sparse-bucket Gutenberg review candidates"
    )
    _database_argument(gutenberg_review_parser)
    gutenberg_review_parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_REPORT_DIRECTORY / "PHASE2B_REVIEW_PRIORITY.csv",
    )

    gutenberg_import_parser = commands.add_parser(
        "import-gutenberg", help="import exact Gutenberg candidates into buckets below seven"
    )
    _database_argument(gutenberg_import_parser)
    gutenberg_import_parser.add_argument("--target-per-minute", type=int, default=7)

    gutenberg_revalidate_parser = commands.add_parser(
        "gutenberg-revalidate",
        help="reapply current metadata, parser, and prose gates to Gutenberg candidates",
    )
    _database_argument(gutenberg_revalidate_parser)

    phase2c_counterfactual = commands.add_parser(
        "phase2c-counterfactual",
        help="audit exact ambiguous clock-face times without changing production eligibility",
    )
    _database_argument(phase2c_counterfactual)
    phase2c_counterfactual.add_argument("--rebuild", action="store_true")

    phase2c_activate = commands.add_parser(
        "phase2c-activate",
        help="activate the saved diversity-aware shared-clock eligibility plan",
    )
    _database_argument(phase2c_activate)

    phase2c_report = commands.add_parser(
        "phase2c-report", help="regenerate Phase 2C reports and review exports"
    )
    _database_argument(phase2c_report)

    phase2d_targets = commands.add_parser(
        "phase2d-targets", help="freeze and export the exact minute pools below three"
    )
    _database_argument(phase2d_targets)

    phase2d_recover = commands.add_parser(
        "phase2d-recover-existing",
        help="re-audit retained candidates that can improve Phase 2D targets",
    )
    _database_argument(phase2d_recover)

    phase2d_revalidate = commands.add_parser(
        "phase2d-revalidate", help="revalidate imported Wikisource quotes under current gates"
    )
    _database_argument(phase2d_revalidate)

    phase2d_review = commands.add_parser(
        "phase2d-review-export", help="export the focused remaining sparse-tail review queue"
    )
    _database_argument(phase2d_review)

    phase2d_report = commands.add_parser(
        "phase2d-report", help="regenerate Phase 2D coverage, review, and final reports"
    )
    _database_argument(phase2d_report)

    wikisource_acquire = commands.add_parser(
        "acquire-wikisource", help="resumably acquire the official English Wikisource XML dump"
    )
    _database_argument(wikisource_acquire)
    wikisource_acquire.add_argument("--dump-date", help="pinned dump date in YYYYMMDD form")

    wikisource_mine = commands.add_parser(
        "mine-wikisource", help="target-mine the acquired Wikisource dump until pools reach three"
    )
    _database_argument(wikisource_mine)
    wikisource_mine.add_argument("--dump-date", help="pinned dump date in YYYYMMDD form")
    return parser


def _print_stats(stats: dict[str, object]) -> None:
    thresholds = stats["minute_thresholds"]
    percentiles = stats["percentiles"]
    assert isinstance(thresholds, dict)
    assert isinstance(percentiles, dict)
    print(f"Raw records:              {stats['total_raw_records']:,}")
    print(f"Canonical quotes:         {stats['total_canonical_quotes']:,}")
    print(f"Selectable quotes:        {stats['total_renderable_quotes']:,}")
    print(f"Exact duplicates merged: {stats['exact_duplicates_removed']:,}")
    print(f"Minutes covered:          {stats['minutes_covered']:,} / 1,440")
    print(f"Missing minutes:          {stats['missing_minutes']:,}")
    mean = stats["mean_quotes_per_minute"]
    median = stats["median_quotes_per_minute"]
    print(f"Mean / median:            {mean:.3f} / {median:.3f}")
    print(
        "P10 / P25 / P75 / P90:  "
        f"{percentiles['p10']:.2f} / {percentiles['p25']:.2f} / "
        f"{percentiles['p75']:.2f} / {percentiles['p90']:.2f}"
    )
    print(
        "Minutes 0 / <3 / <5 / <7: "
        f"{thresholds['zero']} / {thresholds['below_3']} / "
        f"{thresholds['below_5']} / {thresholds['below_7']}"
    )
    print(f"Minutes >=7 / >=14:      {thresholds['at_least_7']} / {thresholds['at_least_14']}")


def _show(database: Path, time_24h: str, seed: int | None, sfw_only: bool) -> None:
    connection = connect_database(database)
    try:
        selector = QuoteSelector(connection, rng=random.Random(seed))
        quote = selector.select(time_24h, sfw_only=sfw_only)
    finally:
        connection.close()
    ansi = sys.stdout.isatty() and "NO_COLOR" not in os.environ
    print(terminal_preview(quote, ansi=ansi))


def _render_quote(
    args: argparse.Namespace,
    *,
    time_24h: str | None = None,
    quote_id: int | None = None,
    font_override: FontSelection | None = None,
    time_font_override: FontSelection | None = None,
    production_preset: str | None = None,
) -> tuple[Path, Path]:
    profile = get_device_profile(
        args.device,
        width=args.width,
        height=args.height,
        orientation=args.orientation,
    )
    font = font_override or _font_selection(args)
    time_font = time_font_override or _time_font_selection(args)
    show_date = profile.show_date_by_default if args.show_date is None else args.show_date
    if args.date and args.show_date is False:
        raise ValueError("--date cannot be combined with --hide-date")
    display_date = parse_date_override(args.date) if args.date else current_local_date()
    connection = connect_database(args.db)
    try:
        if quote_id is not None:
            display_minute = None
            if args.time is not None:
                from litclock.normalize import parse_time_24h

                display_minute, _ = parse_time_24h(args.time)
            quote = load_quote_by_id(connection, quote_id, display_minute)
            suitability = is_renderable_for_device(
                quote,
                profile,
                font,
                time_emphasis=TimeEmphasis(args.time_emphasis),
                time_font=time_font,
                show_date=show_date,
                display_date=display_date,
            )
            if suitability.quote is None:
                raise ValueError(
                    f"quote {quote.id} is {suitability.status.value}: {suitability.reason}"
                )
        else:
            assert time_24h is not None
            selector = QuoteSelector(connection, rng=random.Random(args.seed))
            suitability_by_id = {}

            def display_safe(candidate: Quote) -> bool:
                result = is_renderable_for_device(
                    candidate,
                    profile,
                    font,
                    time_emphasis=TimeEmphasis(args.time_emphasis),
                    time_font=time_font,
                    show_date=show_date,
                    display_date=display_date,
                )
                suitability_by_id[candidate.id] = result
                return result.quote is not None

            quote = selector.select_compatible(
                time_24h,
                display_safe,
                sfw_only=args.sfw_only,
                preview=args.preview,
            )
            suitability = suitability_by_id[quote.id]
    finally:
        connection.close()
    assert suitability.quote is not None
    render_quote = suitability.quote
    mode = RenderMode(args.mode)
    output = args.output
    if output is None:
        compact_time = render_quote.display_time.replace(":", "")
        output = (
            DEFAULT_REPORT_DIRECTORY
            / "render_previews"
            / f"{compact_time}_q{quote.id}_{profile.name}_{mode.value}.png"
        )
    renderer = PillowRenderer(font, time_font=time_font)
    image_path, metadata_path, _ = save_rendered_frame(
        renderer,
        render_quote,
        profile,
        output,
        mode=mode,
        dither=DitherMode(args.dither),
        attribution_style=AttributionStyle(args.attribution_style),
        time_emphasis=TimeEmphasis(args.time_emphasis),
        show_date=show_date,
        display_date=display_date,
        production_preset=production_preset,
    )
    return image_path, metadata_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "fetch":
            written = fetch_sources(PROJECT_ROOT, force=args.force)
            print(
                "Verified all pinned corpus/provenance inputs under data/third_party "
                f"({len(written)} files refreshed)."
            )
        elif args.command == "import":
            summary = import_corpora(args.db, default_source_specs(PROJECT_ROOT))
            print(
                f"Imported {summary.raw_records:,} raw records into "
                f"{summary.canonical_quotes:,} canonical quotes "
                f"({summary.exact_duplicates:,} exact duplicates; "
                f"{summary.trivial_variants:,} trivial variants)."
            )
            if summary.invalid_times or summary.malformed_records:
                print(
                    f"Quarantined {summary.invalid_times} invalid-time and "
                    f"{summary.malformed_records} malformed records."
                )
        elif args.command == "stats":
            connection = connect_database(args.db)
            try:
                stats = calculate_stats(connection)
            finally:
                connection.close()
            paths = write_reports(stats, args.output_dir)
            _print_stats(stats)
            print("Reports: " + ", ".join(str(path.relative_to(PROJECT_ROOT)) for path in paths))
        elif args.command == "show":
            _show(args.db, args.time, args.seed, args.sfw_only)
        elif args.command == "show-now":
            _show(args.db, current_hhmm(), args.seed, args.sfw_only)
        elif args.command == "render":
            image_path, metadata_path = _render_quote(args, time_24h=args.time)
            print(f"Frame: {image_path}")
            print(f"Metadata: {metadata_path}")
        elif args.command == "render-now":
            image_path, metadata_path = _render_quote(args, time_24h=current_hhmm())
            print(f"Frame: {image_path}")
            print(f"Metadata: {metadata_path}")
        elif args.command == "render-id":
            image_path, metadata_path = _render_quote(args, quote_id=args.quote_id)
            print(f"Frame: {image_path}")
            print(f"Metadata: {metadata_path}")
        elif args.command == "render-pw4-v1":
            body_font, time_font = resolve_production_fonts()
            image_path, metadata_path = _render_quote(
                args,
                time_24h=args.time or current_hhmm(),
                font_override=body_font,
                time_font_override=time_font,
                production_preset=PW4_V1_RENDER_CONFIG.name,
            )
            print(f"Frame: {image_path}")
            print(f"Metadata: {metadata_path}")
        elif args.command == "build-pw4-bundle":
            selected_minutes = None
            if args.pilot_start:
                from litclock.normalize import parse_time_24h

                start, _ = parse_time_24h(args.pilot_start)
                selected_minutes = minute_window(start, args.pilot_count)
            elif args.pilot_date:
                raise ValueError("--pilot-date requires --pilot-start")
            connection = connect_database(args.db)
            try:
                summary = build_pw4_bundle(
                    connection,
                    args.output,
                    minutes=selected_minutes,
                    estimate_only=args.estimate_only,
                    sample_size=args.sample_size,
                    pilot_date=(
                        parse_date_override(args.pilot_date)
                        if args.pilot_date
                        else (current_local_date() if selected_minutes else None)
                    ),
                )
            finally:
                connection.close()
            print(json.dumps(summary.as_dict(), indent=2))
        elif args.command == "semantic-audit":
            connection = connect_database(args.db)
            try:
                result = run_semantic_audit(connection)
                artifacts = write_semantic_audit_artifacts(
                    connection, int(result["run_id"]), args.output_dir
                )
            finally:
                connection.close()
            print(json.dumps({**result, "artifacts": artifacts}, indent=2))
        elif args.command == "semantic-apply":
            connection = connect_database(args.db)
            try:
                result = apply_semantic_audit(connection, args.run_id)
            finally:
                connection.close()
            print(json.dumps(result, indent=2))
        elif args.command == "semantic-report":
            connection = connect_database(args.db)
            try:
                initialize_database(connection)
                run_id = args.run_id
                if run_id is None:
                    row = connection.execute(
                        "SELECT id FROM semantic_audit_runs ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                    if row is None:
                        raise ValueError("no semantic audit run exists")
                    run_id = int(row["id"])
                artifacts = write_semantic_audit_artifacts(connection, run_id, args.output_dir)
                result = write_semantic_report(
                    connection,
                    run_id,
                    args.report,
                    args.output_dir,
                )
            finally:
                connection.close()
            print(json.dumps({**result, "artifacts": artifacts}, indent=2))
        elif args.command == "semantic-adjudicate":
            connection = connect_database(args.db)
            try:
                result = run_semantic_adjudication(
                    connection,
                    prior_run_id=args.prior_run_id,
                    manual_path=args.manual,
                )
            finally:
                connection.close()
            print(json.dumps(result, indent=2))
        elif args.command == "semantic-adjudication-report":
            connection = connect_database(args.db)
            try:
                initialize_database(connection)
                run_id = args.run_id
                if run_id is None:
                    row = connection.execute(
                        """
                        SELECT id FROM semantic_audit_runs
                        WHERE audit_version = 'english-clock-semantics-v2'
                        ORDER BY id DESC LIMIT 1
                        """
                    ).fetchone()
                    if row is None:
                        raise ValueError("no semantic v2 adjudication run exists")
                    run_id = int(row["id"])
                result = write_semantic_adjudication_report(
                    connection,
                    run_id,
                    args.report,
                    args.output_dir,
                    renderer_audit_path=args.renderer_audit,
                )
            finally:
                connection.close()
            print(json.dumps(result, indent=2))
        elif args.command == "render-qa":
            connection = connect_database(args.db)
            try:
                payload = run_render_qa(
                    connection,
                    PROJECT_ROOT,
                    font=_font_selection(args),
                    time_font=_time_font_selection(args),
                    time_emphasis=TimeEmphasis(args.time_emphasis),
                )
            finally:
                connection.close()
            summary = payload["summary"]
            print(
                f"Rendered {summary['successful_frames']:,}/{summary['render_attempts']:,} "
                f"QA frames; clipping={summary['clipping_frames']}, "
                f"layout failures={summary['layout_failures']}."
            )
            print(
                "Reports: data/generated/RENDER_QA.md, "
                "data/generated/PHASE3_RENDER_FINALIZATION_REPORT.md"
            )
        elif args.command == "mine-standard-ebooks":
            connection = connect_database(args.db)
            try:
                mining = mine_standard_ebooks(
                    connection,
                    PROJECT_ROOT,
                    limit_books=args.limit_books,
                    workers=args.workers,
                    refresh_catalog=args.refresh_catalog,
                    reprocess=args.reprocess,
                )
                report = write_phase2a_report(connection, PROJECT_ROOT)
            finally:
                connection.close()
            print(
                f"Scanned {mining['books_scanned']:,} books cumulatively; detected "
                f"{mining['expressions_detected']:,} candidates, including "
                f"{mining['high_confidence_candidates']:,} high-confidence candidates."
            )
            print(f"Report: {report.relative_to(PROJECT_ROOT)}")
        elif args.command == "mining-stats":
            connection = connect_database(args.db)
            try:
                initialize_database(connection)
                mining = calculate_mining_stats(connection)
                report = write_phase2a_report(connection, PROJECT_ROOT)
            finally:
                connection.close()
            for key, value in mining.items():
                if not isinstance(value, dict):
                    print(f"{key}: {value:,}" if isinstance(value, int) else f"{key}: {value}")
            print(f"Report: {report.relative_to(PROJECT_ROOT)}")
        elif args.command == "review-export":
            connection = connect_database(args.db)
            try:
                initialize_database(connection)
                count = export_review_queue(connection, args.output)
            finally:
                connection.close()
            print(f"Exported {count:,} candidates to {args.output}")
        elif args.command == "import-mined":
            connection = connect_database(args.db)
            try:
                accepted = import_high_confidence(
                    connection, target_per_minute=args.target_per_minute
                )
                report = write_phase2a_report(connection, PROJECT_ROOT)
            finally:
                connection.close()
            print(f"Imported {accepted:,} high-confidence mined quotes.")
            print(f"Report: {report.relative_to(PROJECT_ROOT)}")
        elif args.command == "phase2a5":
            connection = connect_database(args.db)
            try:
                result = run_phase2a5(
                    connection,
                    PROJECT_ROOT,
                    recover=not args.audit_only,
                    desktop_report=args.desktop_report,
                )
            finally:
                connection.close()
            stats = result["stats"]
            thresholds = stats["minute_thresholds"]
            print(
                f"Accounted for {result['accounting']['total']:,} high-confidence candidates; "
                f"recovered {result['newly_imported']:,} additional quotes."
            )
            print(
                f"Selectable corpus: {stats['total_renderable_quotes']:,}; minutes "
                f"0/<3/<5/<7/>=7: {thresholds['zero']}/{thresholds['below_3']}/"
                f"{thresholds['below_5']}/{thresholds['below_7']}/{thresholds['at_least_7']}."
            )
            print(f"Report: {result['paths'][-1].relative_to(PROJECT_ROOT)}")
            print(f"Desktop report: {args.desktop_report}")
        elif args.command == "mine-gutenberg":
            if args.stage == "all" and args.limit_books is not None:
                raise ValueError("--limit-books cannot be combined with --stage all")
            connection = connect_database(args.db)
            try:
                if args.stage == "all":
                    results = run_all_gutenberg_stages(
                        connection,
                        PROJECT_ROOT,
                        workers=args.workers,
                        batch_size=args.batch_size,
                        refresh_catalog=args.refresh_catalog,
                        prune_full_cache=not args.keep_text_cache,
                    )
                else:
                    results = [
                        run_gutenberg_stage(
                            connection,
                            PROJECT_ROOT,
                            stage=args.stage,
                            cumulative_limit=args.limit_books,
                            workers=args.workers,
                            batch_size=args.batch_size,
                            refresh_catalog=args.refresh_catalog,
                            prune_processed_cache=(
                                args.stage == "full" and not args.keep_text_cache
                            ),
                        )
                    ]
            finally:
                connection.close()
            for result in results:
                run = result["run"]
                print(
                    f"{result['stage']}: scanned {run.get('books_scanned', 0):,} new books, "
                    f"detected {run.get('expressions_detected', 0):,} expressions, "
                    f"imported {run.get('imported_quotes', 0):,} quotes."
                )
            print("Report: data/generated/PHASE2B_REPORT.md")
        elif args.command == "gutenberg-stats":
            connection = connect_database(args.db)
            try:
                mining = calculate_gutenberg_stats(connection, PROJECT_ROOT)
                report = write_phase2b_report(connection, PROJECT_ROOT)
            finally:
                connection.close()
            for key in (
                "catalog_size",
                "eligible_books",
                "books_scanned",
                "raw_time_expressions",
                "high_confidence_candidates",
                "ambiguous_candidates",
                "duplicates_against_existing",
                "imported_quotes",
            ):
                print(f"{key}: {mining[key]:,}")
            print(f"Report: {report.relative_to(PROJECT_ROOT)}")
        elif args.command == "gutenberg-review-export":
            connection = connect_database(args.db)
            try:
                initialize_database(connection)
                count = export_gutenberg_review(connection, args.output)
            finally:
                connection.close()
            print(f"Exported {count:,} candidates to {args.output}")
        elif args.command == "import-gutenberg":
            connection = connect_database(args.db)
            try:
                accepted = import_gutenberg(connection, target_per_minute=args.target_per_minute)
                report = write_phase2b_report(connection, PROJECT_ROOT)
            finally:
                connection.close()
            print(f"Imported {accepted:,} high-confidence Gutenberg quotes.")
            print(f"Report: {report.relative_to(PROJECT_ROOT)}")
        elif args.command == "gutenberg-revalidate":
            connection = connect_database(args.db)
            try:
                acquire_catalog(
                    connection,
                    PROJECT_ROOT / "data" / "public_domain" / "gutenberg",
                )
                result = revalidate_gutenberg_candidates(connection)
                report = write_phase2b_report(connection, PROJECT_ROOT)
            finally:
                connection.close()
            print(
                f"Revoked {result['imports_revoked']:,} imports and accepted "
                f"{result['replacement_imports']:,} replacements."
            )
            print(f"Report: {report.relative_to(PROJECT_ROOT)}")
        elif args.command == "phase2c-counterfactual":
            connection = connect_database(args.db)
            try:
                result = build_phase2c_counterfactual(
                    connection, PROJECT_ROOT, rebuild=args.rebuild
                )
            finally:
                connection.close()
            print(
                "Strict/shared/diversity-aware remaining deficits: "
                f"{result['A']['remaining_deficit_to_7']:,} / "
                f"{result['B']['remaining_deficit_to_7']:,} / "
                f"{result['C']['remaining_deficit_to_7']:,}."
            )
            print("Report: data/generated/PHASE2C_COUNTERFACTUAL.md")
        elif args.command == "phase2c-activate":
            connection = connect_database(args.db)
            try:
                result = activate_phase2c(connection, PROJECT_ROOT)
            finally:
                connection.close()
            stats = result["stats"]
            thresholds = stats["minute_thresholds"]
            print(
                f"Canonical/selectable/relationships: {stats['total_canonical_quotes']:,} / "
                f"{stats['unique_selectable_quotes']:,} / "
                f"{stats['quote_minute_eligibility_relationships']:,}."
            )
            print(
                "Minutes 0/<3/<5/<7/>=7: "
                f"{thresholds['zero']}/{thresholds['below_3']}/{thresholds['below_5']}/"
                f"{thresholds['below_7']}/{thresholds['at_least_7']}."
            )
            print("Report: data/generated/PHASE2C_REPORT.md")
        elif args.command == "phase2c-report":
            connection = connect_database(args.db)
            try:
                initialize_database(connection)
                run = connection.execute(
                    "SELECT id, status FROM phase2c_runs ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if run is None or run["status"] != "COMPLETE":
                    raise ValueError("Phase 2C has not been activated")
                result = write_phase2c_outputs(connection, PROJECT_ROOT, int(run["id"]))
            finally:
                connection.close()
            print(f"Report: {Path(result['report']).relative_to(PROJECT_ROOT)}")
        elif args.command == "phase2d-targets":
            connection = connect_database(args.db)
            try:
                result = start_phase2d(connection, PROJECT_ROOT)
            finally:
                connection.close()
            print(
                f"Starting targets/deficit to three: "
                f"{result['starting_target_minutes']:,} / "
                f"{result['starting_deficit_to_3']:,}."
            )
            print(f"Report: {Path(result['path']).relative_to(PROJECT_ROOT)}")
        elif args.command == "phase2d-recover-existing":
            connection = connect_database(args.db)
            try:
                initialize_database(connection)
                run = connection.execute(
                    "SELECT id FROM phase2d_runs ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if run is None:
                    raise ValueError("run phase2d-targets first")
                result = recover_existing_candidates(connection, PROJECT_ROOT, int(run["id"]))
            finally:
                connection.close()
            print(
                f"Existing candidates considered/recovered: {result['considered']:,} / "
                f"{result['recovered']:,}."
            )
            print(
                f"Remaining targets/deficit: {result['remaining_targets']:,} / "
                f"{result['remaining_deficit']:,}."
            )
        elif args.command == "phase2d-revalidate":
            connection = connect_database(args.db)
            try:
                result = revalidate_wikisource_imports(connection)
            finally:
                connection.close()
            print(
                f"Wikisource imports checked/removed: {result['checked']:,} / "
                f"{result['removed_quotes']:,}; "
                f"relationships removed: {result['removed_relationships']:,}."
            )
        elif args.command == "phase2d-review-export":
            connection = connect_database(args.db)
            try:
                path, count = write_phase2d_review_export(
                    connection, DEFAULT_REPORT_DIRECTORY / "PHASE2D_REVIEW_PRIORITY.csv"
                )
            finally:
                connection.close()
            print(f"Review candidates: {count:,}. Report: {path.relative_to(PROJECT_ROOT)}")
        elif args.command == "phase2d-report":
            connection = connect_database(args.db)
            try:
                result = write_phase2d_outputs(connection, PROJECT_ROOT)
            finally:
                connection.close()
            print(f"Phase 2D report: {Path(result['report']).relative_to(PROJECT_ROOT)}")
            print(
                f"Remaining targets/deficit: {result['remaining_targets']:,} / "
                f"{result['remaining_deficit']:,}."
            )
        elif args.command in {"acquire-wikisource", "mine-wikisource"}:
            connection = connect_database(args.db)
            try:
                initialize_database(connection)
                run = connection.execute(
                    "SELECT id, status FROM phase2d_runs ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if run is None or run["status"] not in {"RECOVERY_COMPLETE", "COMPLETE"}:
                    raise ValueError("run phase2d-targets and phase2d-recover-existing first")
                if run["status"] == "COMPLETE" and args.command == "mine-wikisource":
                    raise ValueError("Phase 2D mining is already complete")
                connection.execute(
                    "UPDATE phase2d_runs SET status = 'ACQUIRING', error = NULL WHERE id = ?",
                    (run["id"],),
                )
                connection.commit()
                try:
                    dump = acquire_dump(
                        connection,
                        PROJECT_ROOT / "data" / "public_domain" / "wikisource",
                        dump_date=args.dump_date,
                    )
                except Exception:
                    connection.execute(
                        "UPDATE phase2d_runs SET status = 'RECOVERY_COMPLETE' WHERE id = ?",
                        (run["id"],),
                    )
                    connection.commit()
                    raise
                connection.execute(
                    """
                    UPDATE phase2d_runs SET status = 'RECOVERY_COMPLETE', dump_id = ?
                    WHERE id = ?
                    """,
                    (dump["id"], run["id"]),
                )
                connection.commit()
                if args.command == "mine-wikisource":
                    result = mine_wikisource_dump(
                        connection, PROJECT_ROOT, int(run["id"]), int(dump["id"])
                    )
                else:
                    result = None
            finally:
                connection.close()
            print(f"Acquired {dump['filename']} ({int(dump['byte_size']) / (1024**3):.2f} GiB).")
            if result is not None:
                print(
                    f"Wikisource imported {result['imported_quotes']:,} quotes / "
                    f"{result['relationships_added']:,} relationships; "
                    f"{result['remaining_targets']:,} target minutes remain."
                )
        return 0
    except (FileNotFoundError, NoQuoteAvailable, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
