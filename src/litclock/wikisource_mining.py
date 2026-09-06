"""Target-aware English Wikisource sparse-tail mining and capped import."""

from __future__ import annotations

import bz2
import json
import math
import os
import re
import sqlite3
import sys
import time
import urllib.parse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from litclock.db import initialize_database
from litclock.gutenberg_text import text_quality_rejection
from litclock.mining import PassageDuplicateIndex
from litclock.models import EligibilityType, TimeConfidence, TimeSemantics
from litclock.normalize import minute_to_time, normalized_quote_hash, text_hash
from litclock.phase2a5 import parse_ambiguous_options
from litclock.phase2c import _resolve_in_context, semantic_display_minutes
from litclock.phase2d import _phase2d_context_rejection, current_sparse_targets
from litclock.timeparse import detect_time_expressions
from litclock.wikisource import (
    INDEX_NAMESPACE,
    MAIN_NAMESPACE,
    PAGE_NAMESPACE,
    WikisourcePage,
    iter_wikimedia_pages,
    multistream_ranges,
    namespace_disposition,
    pages_from_multistream_member,
)
from litclock.wikisource_text import (
    WorkMetadata,
    clean_wikitext,
    extract_work_metadata,
    proofread_quality,
    title_is_literary,
    work_key_for_page,
)
from litclock.xhtml import extract_quote_context

SOURCE_NAME = "english_wikisource"
_TIME_PREFILTER = re.compile(
    r"(?:\b\d{1,2}[.:]\d{2}\b|\b\d{4}\s+hours?\b|o[’']clock|"
    r"\b(?:quarter|half|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
    r"twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty|thirty|forty|fifty)(?:[\s-]+minutes?)?[\s-]+(?:past|after|to|before)\b|"
    r"\b(?:noon|midnight)\b|"
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)[\s-]+"
    r"(?:oh[\s-]+)?(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
    r"twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty|thirty|forty|fifty)\b)",
    re.IGNORECASE,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def raw_time_prefilter(wikitext: str) -> bool:
    """Cheap markup-level prefilter; all semantic decisions occur after AST cleanup."""
    return bool(_TIME_PREFILTER.search(wikitext))


def raw_target_time_prefilter(wikitext: str, target_minutes: frozenset[int]) -> bool:
    """Use the shared parser as a cheap target filter before structural rendering."""
    if not raw_time_prefilter(wikitext):
        return False
    for detection in detect_time_expressions(wikitext):
        if detection.confidence == TimeConfidence.AMPM_AMBIGUOUS:
            options = parse_ambiguous_options(detection.ampm_evidence)
            if options and target_minutes.intersection(options):
                return True
        elif detection.minute_of_day is not None and int(detection.minute_of_day) in target_minutes:
            return True
    return False


def _upsert_work(
    connection: sqlite3.Connection,
    dump_id: int,
    page: WikisourcePage,
    metadata: WorkMetadata,
) -> None:
    key = work_key_for_page(page.namespace, page.title)
    fallback_title = page.title.removeprefix("Index:") if page.namespace == INDEX_NAMESPACE else key
    title = metadata.title or fallback_title
    connection.execute(
        """
        INSERT INTO wikisource_works (
            dump_id, work_key, work_title, author, root_page_id, root_revision_id,
            index_page_title, source_license, license_evidence, license_status,
            literary_status, metadata_text, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(dump_id, work_key) DO UPDATE SET
            work_title = CASE WHEN excluded.work_title != ''
                THEN excluded.work_title ELSE wikisource_works.work_title END,
            author = CASE WHEN excluded.author != ''
                THEN excluded.author ELSE wikisource_works.author END,
            root_page_id = COALESCE(excluded.root_page_id, wikisource_works.root_page_id),
            root_revision_id = COALESCE(excluded.root_revision_id,
                                        wikisource_works.root_revision_id),
            index_page_title = COALESCE(excluded.index_page_title,
                                        wikisource_works.index_page_title),
            source_license = CASE WHEN excluded.license_status = 'PUBLIC_DOMAIN'
                THEN excluded.source_license ELSE wikisource_works.source_license END,
            license_evidence = CASE WHEN excluded.license_status = 'PUBLIC_DOMAIN'
                THEN excluded.license_evidence ELSE wikisource_works.license_evidence END,
            license_status = CASE WHEN excluded.license_status = 'PUBLIC_DOMAIN'
                THEN 'PUBLIC_DOMAIN' ELSE wikisource_works.license_status END,
            literary_status = CASE WHEN excluded.literary_status = 'LITERARY'
                THEN 'LITERARY' ELSE wikisource_works.literary_status END,
            metadata_text = CASE WHEN excluded.metadata_text != ''
                THEN excluded.metadata_text ELSE wikisource_works.metadata_text END,
            updated_at = excluded.updated_at
        """,
        (
            dump_id,
            key,
            title,
            metadata.author,
            page.page_id if page.namespace == MAIN_NAMESPACE else None,
            page.revision_id if page.namespace == MAIN_NAMESPACE else None,
            page.title if page.namespace == INDEX_NAMESPACE else None,
            metadata.source_license,
            metadata.license_evidence,
            metadata.license_status,
            metadata.literary_status,
            metadata.metadata_text,
            _now(),
        ),
    )


def _spool_paths(data_directory: Path) -> tuple[Path, Path, Path]:
    return (
        data_directory / "mainspace_time_pages.jsonl",
        data_directory / "page_namespace_time_pages.jsonl",
        data_directory / "spool_manifest.json",
    )


def _page_payload(page: WikisourcePage) -> str:
    return json.dumps(
        {
            "page_id": page.page_id,
            "namespace": page.namespace,
            "title": page.title,
            "revision_id": page.revision_id,
            "revision_timestamp": page.revision_timestamp,
            "wikitext": page.wikitext,
        },
        ensure_ascii=False,
    )


def _scan_stream_batch(
    task: tuple[str, tuple[tuple[int, int], ...], tuple[int, ...]],
) -> tuple[
    dict[str, int],
    list[tuple[WikisourcePage, WorkMetadata]],
    list[WikisourcePage],
    list[WikisourcePage],
]:
    """Decompress and structurally classify a deterministic batch of dump members."""
    archive_name, ranges, raw_targets = task
    target_minutes = frozenset(raw_targets)
    counts: Counter[str] = Counter()
    metadata_pages: list[tuple[WikisourcePage, WorkMetadata]] = []
    main_pages: list[WikisourcePage] = []
    transcription_pages: list[WikisourcePage] = []
    with Path(archive_name).open("rb") as archive:
        for start, end in ranges:
            archive.seek(start)
            member = bz2.decompress(archive.read(end - start))
            for page in pages_from_multistream_member(member):
                counts["xml_pages_streamed"] += 1
                disposition = namespace_disposition(page.namespace, page.title)
                if disposition == "EXCLUDED_NAMESPACE":
                    counts["excluded_namespace_pages"] += 1
                    continue
                if page.namespace == INDEX_NAMESPACE or (
                    page.namespace == MAIN_NAMESPACE and "/" not in page.title
                ):
                    metadata_pages.append((page, extract_work_metadata(page.wikitext)))
                    counts["work_metadata_pages"] += 1
                if page.namespace not in {MAIN_NAMESPACE, PAGE_NAMESPACE}:
                    continue
                if page.wikitext.lstrip().casefold().startswith("#redirect"):
                    counts["redirects_skipped"] += 1
                    continue
                if page.namespace == PAGE_NAMESPACE:
                    quality = proofread_quality(page.wikitext)
                    if quality is None or quality < 3:
                        counts["unproofread_page_namespace_skipped"] += 1
                        continue
                if not raw_time_prefilter(page.wikitext):
                    continue
                counts["broad_time_prefilter_pages"] += 1
                if not raw_target_time_prefilter(page.wikitext, target_minutes):
                    continue
                if page.namespace == MAIN_NAMESPACE:
                    main_pages.append(page)
                    counts["mainspace_spooled"] += 1
                else:
                    transcription_pages.append(page)
                    counts["page_spooled"] += 1
    return dict(counts), metadata_pages, main_pages, transcription_pages


def _multistream_tasks(
    archive_path: Path,
    index_path: Path,
    target_minutes: tuple[int, ...],
    *,
    members_per_task: int = 16,
) -> list[tuple[str, tuple[tuple[int, int], ...], tuple[int, ...]]]:
    ranges = multistream_ranges(index_path, archive_path.stat().st_size)
    return [
        (
            str(archive_path),
            tuple(ranges[index : index + members_per_task]),
            target_minutes,
        )
        for index in range(0, len(ranges), members_per_task)
    ]


def _write_scan_result(
    connection: sqlite3.Connection,
    dump_id: int,
    result,
    main_handle,
    page_handle,
    counts: Counter[str],
) -> None:
    batch_counts, metadata_pages, main_pages, transcription_pages = result
    counts.update(batch_counts)
    for page, metadata in metadata_pages:
        _upsert_work(connection, dump_id, page, metadata)
    for page in main_pages:
        main_handle.write(_page_payload(page) + "\n")
    for page in transcription_pages:
        page_handle.write(_page_payload(page) + "\n")


def spool_dump(
    connection: sqlite3.Connection,
    dump: sqlite3.Row,
    data_directory: Path,
) -> dict[str, int]:
    """Stream XML once, index work metadata, and spool only time-like content pages."""
    main_path, page_path, manifest_path = _spool_paths(data_directory)
    target_minutes = tuple(target.minute_of_day for target in current_sparse_targets(connection))
    if manifest_path.is_file() and main_path.is_file() and page_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_targets = set(manifest.get("target_minutes", []))
        if manifest.get("dump_sha1") == dump["actual_checksum"] and set(target_minutes).issubset(
            manifest_targets
        ):
            return {key: int(value) for key, value in manifest["counts"].items()}

    main_tmp = main_path.with_suffix(".jsonl.tmp")
    page_tmp = page_path.with_suffix(".jsonl.tmp")
    counts: Counter[str] = Counter()
    data_directory.mkdir(parents=True, exist_ok=True)
    with (
        main_tmp.open("w", encoding="utf-8") as main_handle,
        page_tmp.open("w", encoding="utf-8") as page_handle,
    ):
        archive_path = Path(dump["local_path"])
        index_path = Path(dump["index_local_path"])
        if index_path.is_file() and dump["index_actual_checksum"]:
            workers = max(1, min(6, (os.cpu_count() or 2) - 1))
            tasks = _multistream_tasks(archive_path, index_path, target_minutes)
            print(
                f"Wikisource XML: {len(tasks):,} deterministic multistream batches "
                f"across {workers} workers",
                file=sys.stderr,
                flush=True,
            )
            with ProcessPoolExecutor(max_workers=workers) as executor:
                for batch_number, result in enumerate(
                    executor.map(_scan_stream_batch, tasks, chunksize=1), start=1
                ):
                    _write_scan_result(
                        connection,
                        int(dump["id"]),
                        result,
                        main_handle,
                        page_handle,
                        counts,
                    )
                    if batch_number % 50 == 0:
                        connection.commit()
                        print(
                            "Wikisource XML: "
                            f"{counts['xml_pages_streamed']:,} pages; "
                            f"{counts['mainspace_spooled']:,} mainspace and "
                            f"{counts['page_spooled']:,} Page: records spooled",
                            file=sys.stderr,
                            flush=True,
                        )
        else:
            for page in iter_wikimedia_pages(archive_path):
                counts["xml_pages_streamed"] += 1
                if counts["xml_pages_streamed"] % 100_000 == 0:
                    connection.commit()
                    print(
                        "Wikisource XML: "
                        f"{counts['xml_pages_streamed']:,} pages; "
                        f"{counts['mainspace_spooled']:,} mainspace and "
                        f"{counts['page_spooled']:,} Page: records spooled",
                        file=sys.stderr,
                        flush=True,
                    )
                disposition = namespace_disposition(page.namespace, page.title)
                if disposition == "EXCLUDED_NAMESPACE":
                    counts["excluded_namespace_pages"] += 1
                    continue
                if page.namespace == INDEX_NAMESPACE or (
                    page.namespace == MAIN_NAMESPACE and "/" not in page.title
                ):
                    metadata = extract_work_metadata(page.wikitext)
                    _upsert_work(connection, int(dump["id"]), page, metadata)
                    counts["work_metadata_pages"] += 1
                if page.namespace not in {MAIN_NAMESPACE, PAGE_NAMESPACE}:
                    continue
                if page.wikitext.lstrip().casefold().startswith("#redirect"):
                    counts["redirects_skipped"] += 1
                    continue
                if page.namespace == PAGE_NAMESPACE:
                    quality = proofread_quality(page.wikitext)
                    if quality is None or quality < 3:
                        counts["unproofread_page_namespace_skipped"] += 1
                        continue
                if not raw_time_prefilter(page.wikitext):
                    continue
                counts["broad_time_prefilter_pages"] += 1
                if not raw_target_time_prefilter(page.wikitext, frozenset(target_minutes)):
                    continue
                handle = main_handle if page.namespace == MAIN_NAMESPACE else page_handle
                handle.write(_page_payload(page) + "\n")
                key = "mainspace_spooled" if page.namespace == MAIN_NAMESPACE else "page_spooled"
                counts[key] += 1
        main_handle.flush()
        page_handle.flush()
        os.fsync(main_handle.fileno())
        os.fsync(page_handle.fileno())
    connection.commit()
    os.replace(main_tmp, main_path)
    os.replace(page_tmp, page_path)
    manifest_tmp = manifest_path.with_suffix(".json.tmp")
    manifest_tmp.write_text(
        json.dumps(
            {
                "dump_sha1": dump["actual_checksum"],
                "target_minutes": list(target_minutes),
                "counts": dict(counts),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(manifest_tmp, manifest_path)
    return dict(counts)


def _page_from_payload(payload: dict[str, Any]) -> WikisourcePage:
    return WikisourcePage(
        int(payload["page_id"]),
        int(payload["namespace"]),
        str(payload["title"]),
        int(payload["revision_id"]),
        payload.get("revision_timestamp"),
        str(payload["wikitext"]),
    )


def _load_works(connection: sqlite3.Connection, dump_id: int) -> dict[str, dict[str, Any]]:
    return {
        str(row["work_key"]): dict(row)
        for row in connection.execute(
            "SELECT * FROM wikisource_works WHERE dump_id = ?", (dump_id,)
        )
    }


def _merge_metadata(page: WikisourcePage, inherited: dict[str, Any] | None) -> dict[str, Any]:
    direct = extract_work_metadata(page.wikitext)
    inherited = inherited or {}
    license_status = direct.license_status
    source_license = direct.source_license
    evidence = direct.license_evidence
    if license_status != "PUBLIC_DOMAIN" and inherited.get("license_status") == "PUBLIC_DOMAIN":
        license_status = "PUBLIC_DOMAIN"
        source_license = inherited.get("source_license")
        evidence = inherited.get("license_evidence")
    key = work_key_for_page(page.namespace, page.title)
    title = direct.title or str(inherited.get("work_title") or key.removeprefix("Index:"))
    author = direct.author or str(inherited.get("author") or "")
    literary = (
        "NONLITERARY"
        if not title_is_literary(title)
        else "LITERARY"
        if author and title
        else "REVIEW"
    )
    return {
        "work_key": key,
        "work_title": title,
        "author": author,
        "source_license": source_license,
        "license_evidence": evidence,
        "license_status": license_status,
        "literary_status": literary,
    }


def _coverage_counts(connection: sqlite3.Connection) -> dict[int, int]:
    return {
        int(row["minute_of_day"]): int(row["n"])
        for row in connection.execute(
            """
            SELECT minute_of_day, COUNT(*) AS n FROM quote_minute_pool
            GROUP BY minute_of_day
            """
        )
    }


def _resolution_details(
    detection,
    paragraph: str,
    previous: str,
    following: str,
) -> tuple[tuple[int, ...], str, int | None, str, str]:
    if detection.rejection_reason:
        return (
            (),
            TimeSemantics.INVALID.value,
            None,
            detection.rejection_reason,
            detection.text,
        )
    displayed = semantic_display_minutes(
        detection.text,
        source_context=(paragraph, detection.start, detection.end, previous, following),
    )
    if detection.confidence != TimeConfidence.AMPM_AMBIGUOUS:
        semantics = (
            TimeSemantics.NOON.value
            if detection.parser_rule == "named" and detection.text.casefold() == "noon"
            else TimeSemantics.MIDNIGHT.value
            if detection.parser_rule == "named" and detection.text.casefold() == "midnight"
            else TimeSemantics.RESOLVED_24H.value
        )
        evidence_type = detection.confidence.value
        return (
            displayed,
            semantics,
            detection.minute_of_day,
            evidence_type,
            (detection.ampm_evidence or detection.text),
        )
    options = parse_ambiguous_options(detection.ampm_evidence)
    if options is None:
        return (), TimeSemantics.INVALID.value, None, "INVALID_OPTIONS", ""
    resolution = _resolve_in_context(
        paragraph,
        detection.start,
        detection.end,
        options,
        previous=previous,
        following=following,
    )
    if resolution.minute_of_day is not None:
        return (
            (int(resolution.minute_of_day),),
            TimeSemantics.CLOCKFACE_12H.value,
            int(resolution.minute_of_day),
            resolution.evidence_type or "CONTEXT_RESOLVED",
            resolution.evidence_text or "",
        )
    if (resolution.proposed_evidence or "").casefold().startswith("conflicting daypart cues"):
        return (
            options,
            TimeSemantics.CLOCKFACE_12H.value,
            None,
            "CONTEXT_REVIEW_REQUIRED",
            resolution.proposed_evidence or "conflicting daypart evidence",
        )
    return (
        options,
        TimeSemantics.CLOCKFACE_12H.value,
        None,
        "CLOCKFACE_NO_DAYPART",
        "displayed text and trusted source context contain no deterministic daypart",
    )


def _candidate_quality_reason(quote: str, paragraph: str, context, detection) -> str | None:
    if detection.rejection_reason:
        return detection.rejection_reason
    before = quote[: context.highlight_start]
    after = quote[context.highlight_end :]
    if (
        context.highlight_start == 0
        and detection.confidence == TimeConfidence.AMPM_AMBIGUOUS
        and re.match(r"\s+(?:[A-Z\"“]|\()", after)
    ):
        return "bare leading numeric label/reference"
    if re.search(r"\b(?:see|cf\.?|supra|infra|compare)\b", before, re.I):
        return "citation/reference context"
    if re.match(
        r"^At\s+[^,]{1,45},\s+(?:stopped|started|arrived|left|proceeded)\b",
        quote,
        re.I,
    ):
        return "telegraphic log entry without a grammatical subject"
    if context.context_score < 85 or context.literary_quality_score < 80:
        return "quality score below raised Phase 2D threshold"
    reason = context.rejection_reason or text_quality_rejection(paragraph, quote)
    if reason:
        return reason
    return _phase2d_context_rejection(paragraph, quote)


def _eligibility_type(detection, minute: int, resolution: int | None) -> str:
    if detection.confidence == TimeConfidence.EXACT_AM:
        return EligibilityType.EXPLICIT_AM.value
    if detection.confidence == TimeConfidence.EXACT_PM:
        return EligibilityType.EXPLICIT_PM.value
    if detection.confidence == TimeConfidence.EXACT_CONTEXTUAL or resolution is not None:
        return (
            EligibilityType.CONTEXT_RESOLVED_AM.value
            if minute < 720
            else EligibilityType.CONTEXT_RESOLVED_PM.value
        )
    if detection.confidence == TimeConfidence.AMPM_AMBIGUOUS:
        return (
            EligibilityType.CLOCKFACE_SHARED_AM.value
            if minute < 720
            else EligibilityType.CLOCKFACE_SHARED_PM.value
        )
    return EligibilityType.EXACT_24H.value


def _ensure_source_and_import_run(
    connection: sqlite3.Connection, dump: sqlite3.Row
) -> tuple[int, int]:
    timestamp = _now()
    source_table_name = f"english_wikisource/{dump['dump_date']}"
    connection.execute(
        """
        INSERT INTO sources (
            name, slug, source_url, source_license, upstream_commit,
            corpus_path, corpus_sha256, record_count, imported_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
        ON CONFLICT(name) DO UPDATE SET imported_at = excluded.imported_at
        """,
        (
            source_table_name,
            "english-wikisource-" + str(dump["dump_date"]),
            dump["source_url"],
            "MIXED SOURCE RIGHTS; each quote provenance records work-level evidence",
            "dump:" + str(dump["dump_date"]),
            dump["local_path"],
            dump["actual_checksum"],
            timestamp,
        ),
    )
    source_id = int(
        connection.execute(
            "SELECT id FROM sources WHERE name = ?", (source_table_name,)
        ).fetchone()[0]
    )
    import_run_id = int(
        connection.execute(
            "INSERT INTO import_runs (started_at, status) VALUES (?, 'RUNNING')", (timestamp,)
        ).lastrowid
    )
    connection.commit()
    return source_id, import_run_id


def _insert_candidate(
    connection: sqlite3.Connection,
    dump: sqlite3.Row,
    page: WikisourcePage,
    metadata: dict[str, Any],
    detection,
    context,
    previous: str,
    paragraph: str,
    following: str,
    display_minutes: tuple[int, ...],
    time_semantics: str,
    resolution: int | None,
    evidence_type: str,
    evidence_text: str,
    duplicate,
    review_status: str,
    rejection_reason: str | None,
    source_expression_start: int,
) -> int:
    source_url = "https://en.wikisource.org/wiki/" + urllib.parse.quote(
        page.title.replace(" ", "_"), safe="/:"
    )
    minute = display_minutes[0] if len(display_minutes) == 1 else None
    possible_am = next((value for value in display_minutes if value < 720), None)
    possible_pm = next((value for value in display_minutes if value >= 720), None)
    locator = (
        f"{page.title}#revision={page.revision_id}:rendered-chars="
        f"{source_expression_start}-{source_expression_start + len(detection.text)}"
    )
    fingerprint = text_hash(
        f"{dump['actual_checksum']}\0{page.page_id}\0{page.revision_id}\0"
        f"{source_expression_start}\0{detection.parser_rule}"
    )
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO wikisource_candidates (
            dump_id, page_id, namespace, page_title, revision_id, revision_timestamp,
            work_key, work_title, author, source_license, license_evidence, source_url,
            dump_date, minute_of_day, possible_minute_am, possible_minute_pm, time_text,
            quote, previous_paragraph, containing_paragraph, following_paragraph,
            highlight_start, highlight_end, parser_rule, time_confidence, time_semantics,
            contextual_resolution, evidence_type, evidence_text, context_score,
            literary_quality_score, duplicate_status, duplicate_of_quote_id,
            duplicate_of_candidate_id, review_status, rejection_reason,
            normalized_quote_hash, passage_fingerprint, source_locator,
            source_expression_start, source_expression_end, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            dump["id"],
            page.page_id,
            page.namespace,
            page.title,
            page.revision_id,
            page.revision_timestamp,
            metadata["work_key"],
            metadata["work_title"],
            metadata["author"],
            metadata["source_license"],
            metadata["license_evidence"],
            source_url,
            dump["dump_date"],
            minute,
            possible_am,
            possible_pm,
            detection.text,
            context.quote,
            previous,
            paragraph,
            following,
            context.highlight_start,
            context.highlight_end,
            detection.parser_rule,
            detection.confidence.value,
            time_semantics,
            resolution,
            evidence_type,
            evidence_text,
            context.context_score,
            context.literary_quality_score,
            duplicate.status,
            duplicate.quote_id,
            duplicate.wikisource_candidate_id,
            review_status,
            rejection_reason,
            normalized_quote_hash(context.quote),
            fingerprint,
            locator,
            source_expression_start,
            source_expression_start + len(detection.text),
            _now(),
        ),
    )
    if cursor.rowcount:
        return int(cursor.lastrowid)
    row = connection.execute(
        """
        SELECT id FROM wikisource_candidates
        WHERE dump_id = ? AND page_id = ? AND revision_id = ?
          AND source_expression_start = ? AND source_expression_end = ? AND parser_rule = ?
        """,
        (
            dump["id"],
            page.page_id,
            page.revision_id,
            source_expression_start,
            source_expression_start + len(detection.text),
            detection.parser_rule,
        ),
    ).fetchone()
    return int(row["id"])


def _import_candidate(
    connection: sqlite3.Connection,
    candidate_id: int,
    detection,
    useful_minutes: list[int],
    source_id: int,
    import_run_id: int,
) -> int:
    candidate = connection.execute(
        "SELECT * FROM wikisource_candidates WHERE id = ?", (candidate_id,)
    ).fetchone()
    if candidate["imported_quote_id"] is not None:
        return int(candidate["imported_quote_id"])
    anchor = min(useful_minutes)
    timestamp = _now()
    quote_id = int(
        connection.execute(
            """
            INSERT INTO quotes (
                minute_of_day, time_24h, time_text, quote, title, author, sfw, language,
                source_name, source_url, source_license, source_record_id, quote_hash,
                normalized_quote_hash, highlight_start, highlight_end, quality_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'en', ?, ?, ?, ?, ?, ?, ?, ?,
                      'VERIFIED_EXACT', ?)
            """,
            (
                anchor,
                minute_to_time(anchor),
                candidate["time_text"],
                candidate["quote"],
                candidate["work_title"],
                candidate["author"],
                SOURCE_NAME,
                candidate["source_url"],
                candidate["source_license"],
                f"{candidate['page_id']}:{candidate['revision_id']}:{candidate['source_expression_start']}",
                text_hash(candidate["quote"]),
                candidate["normalized_quote_hash"],
                candidate["highlight_start"],
                candidate["highlight_end"],
                timestamp,
            ),
        ).lastrowid
    )
    payload = json.dumps(
        {
            "source": SOURCE_NAME,
            "page_title": candidate["page_title"],
            "page_id": candidate["page_id"],
            "revision_id": candidate["revision_id"],
            "revision_timestamp": candidate["revision_timestamp"],
            "dump_date": candidate["dump_date"],
            "license_evidence": candidate["license_evidence"],
            "source_locator": candidate["source_locator"],
            "parser_rule": candidate["parser_rule"],
            "time_semantics": candidate["time_semantics"],
            "eligibility_minutes": useful_minutes,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    connection.execute(
        """
        INSERT INTO quote_provenance (
            quote_id, source_id, import_run_id, source_record_id, raw_time_24h,
            raw_time_text, raw_quote, raw_title, raw_author, raw_sfw, raw_quote_hash,
            validation_status, highlight_start, highlight_end, duplicate_kind, raw_payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 'VERIFIED_EXACT', ?, ?,
                  'CANONICAL', ?)
        """,
        (
            quote_id,
            source_id,
            import_run_id,
            f"{candidate['page_id']}:{candidate['revision_id']}:{candidate['source_expression_start']}",
            minute_to_time(anchor),
            candidate["time_text"],
            candidate["quote"],
            candidate["work_title"],
            candidate["author"],
            text_hash(candidate["quote"]),
            candidate["highlight_start"],
            candidate["highlight_end"],
            payload,
        ),
    )
    connection.execute("DELETE FROM quote_time_semantics WHERE quote_id = ?", (quote_id,))
    connection.execute("DELETE FROM quote_minute_eligibility WHERE quote_id = ?", (quote_id,))
    narrative = (
        "UNRESOLVED"
        if candidate["time_semantics"] == TimeSemantics.CLOCKFACE_12H.value
        and candidate["contextual_resolution"] is None
        else "AM"
        if anchor < 720
        else "PM"
    )
    connection.execute(
        """
        INSERT INTO quote_time_semantics (
            quote_id, time_semantics, clockface_minute, narrative_resolution,
            evidence_type, evidence_text, source_candidate_type, source_candidate_id,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'WIKISOURCE', ?, ?, ?)
        """,
        (
            quote_id,
            candidate["time_semantics"],
            anchor % 720,
            narrative,
            candidate["evidence_type"],
            candidate["evidence_text"],
            candidate_id,
            timestamp,
            timestamp,
        ),
    )
    for minute in useful_minutes:
        connection.execute(
            """
            INSERT INTO quote_minute_eligibility (
                quote_id, minute_of_day, eligibility_type, confidence, evidence_type,
                evidence_text, source_candidate_type, source_candidate_id, created_at
            ) VALUES (?, ?, ?, 'CLOCKFACE_EXACT', ?, ?, 'WIKISOURCE', ?, ?)
            """,
            (
                quote_id,
                minute,
                _eligibility_type(detection, minute, candidate["contextual_resolution"]),
                candidate["evidence_type"],
                candidate["evidence_text"],
                candidate_id,
                timestamp,
            ),
        )
    connection.execute(
        """
        UPDATE wikisource_candidates SET review_status = 'IMPORTED',
            imported_quote_id = ?, imported_at = ? WHERE id = ?
        """,
        (quote_id, timestamp, candidate_id),
    )
    connection.execute(
        "UPDATE sources SET record_count = record_count + 1 WHERE id = ?", (source_id,)
    )
    return quote_id


def _record_checkpoint(
    connection: sqlite3.Connection,
    phase2d_run_id: int,
    label: str,
    pages: int,
    imported: int,
) -> None:
    targets = current_sparse_targets(connection)
    connection.execute(
        """
        INSERT OR IGNORE INTO phase2d_checkpoints (
            run_id, label, pages_processed, quotes_imported, target_minutes_remaining,
            deficit_to_3_remaining, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            phase2d_run_id,
            label,
            pages,
            imported,
            len(targets),
            sum(target.deficit_to_3 for target in targets),
            _now(),
        ),
    )


def mine_wikisource_dump(
    connection: sqlite3.Connection,
    project_root: Path,
    phase2d_run_id: int,
    dump_id: int,
) -> dict[str, int | float]:
    """Stream, clean, validate, deduplicate, and import only into pools below three."""
    initialize_database(connection)
    phase_run = connection.execute(
        "SELECT * FROM phase2d_runs WHERE id = ?", (phase2d_run_id,)
    ).fetchone()
    dump = connection.execute("SELECT * FROM wikisource_dumps WHERE id = ?", (dump_id,)).fetchone()
    if phase_run is None or dump is None:
        raise ValueError("unknown Phase 2D run or Wikisource dump")
    if phase_run["status"] == "COMPLETE":
        latest = connection.execute(
            "SELECT * FROM wikisource_runs WHERE phase2d_run_id = ? ORDER BY id DESC LIMIT 1",
            (phase2d_run_id,),
        ).fetchone()
        return dict(latest) if latest is not None else {}
    if phase_run["status"] != "RECOVERY_COMPLETE":
        raise ValueError("existing retained candidates must be audited before Wikisource mining")
    if dump["status"] != "ACQUIRED":
        raise ValueError("Wikisource dump must be acquired and checksum-verified first")

    started = time.monotonic()
    connection.execute(
        "UPDATE phase2d_runs SET status = 'MINING', dump_id = ? WHERE id = ?",
        (dump_id, phase2d_run_id),
    )
    wiki_run_id = int(
        connection.execute(
            """
            INSERT INTO wikisource_runs (phase2d_run_id, dump_id, started_at, status)
            VALUES (?, ?, ?, 'RUNNING')
            """,
            (phase2d_run_id, dump_id, _now()),
        ).lastrowid
    )
    connection.commit()
    data_directory = project_root / "data" / "public_domain" / "wikisource"
    try:
        spool_counts = spool_dump(connection, dump, data_directory)
        works = _load_works(connection, dump_id)
        duplicate_index = PassageDuplicateIndex(connection)
        counts = _coverage_counts(connection)
        source_id, import_run_id = _ensure_source_and_import_run(connection, dump)
        stats: Counter[str] = Counter(spool_counts)
        initial_targets = int(phase_run["starting_target_minutes"])
        _record_checkpoint(connection, phase2d_run_id, "existing-recovery", 0, 0)
        pages_processed = imported = relationships = 0
        stop = False
        main_path, page_path, _ = _spool_paths(data_directory)
        for spool_path in (main_path, page_path):
            with spool_path.open(encoding="utf-8") as handle:
                for line in handle:
                    active_targets = {minute for minute in range(1440) if counts.get(minute, 0) < 3}
                    if not active_targets:
                        stop = True
                        break
                    page = _page_from_payload(json.loads(line))
                    pages_processed += 1
                    stats["content_pages_scanned"] += 1
                    metadata = _merge_metadata(
                        page, works.get(work_key_for_page(page.namespace, page.title))
                    )
                    paragraphs = clean_wikitext(page.wikitext)
                    stats["characters_scanned"] += sum(len(paragraph) for paragraph in paragraphs)
                    page_offset = 0
                    for index, paragraph in enumerate(paragraphs):
                        previous = paragraphs[index - 1] if index else ""
                        following = paragraphs[index + 1] if index + 1 < len(paragraphs) else ""
                        for detection in detect_time_expressions(paragraph):
                            stats["expressions_detected"] += 1
                            possible, semantics, resolution, evidence_type, evidence_text = (
                                _resolution_details(detection, paragraph, previous, following)
                            )
                            relevant = tuple(
                                minute for minute in possible if minute in active_targets
                            )
                            if not relevant:
                                continue
                            stats["sparse_relevant"] += 1
                            context = extract_quote_context(paragraph, detection)
                            quality_reason = _candidate_quality_reason(
                                context.quote, paragraph, context, detection
                            )
                            duplicate = duplicate_index.check(context.quote)
                            if duplicate.status != "NEW":
                                review_status = "REJECTED_DUPLICATE"
                                rejection = f"duplicate passage: {duplicate.status}"
                                stats["duplicates"] += 1
                            elif quality_reason:
                                review_status = "REJECTED_QUALITY"
                                rejection = quality_reason
                                stats["quality_rejected"] += 1
                            elif metadata["literary_status"] != "LITERARY":
                                review_status = "REVIEW_ATTRIBUTION"
                                rejection = (
                                    "work attribution or literary classification needs review"
                                )
                                stats["review_attribution"] += 1
                            elif metadata["license_status"] != "PUBLIC_DOMAIN":
                                review_status = "REVIEW_LICENSE"
                                rejection = (
                                    "work-level public-domain status is not machine-verifiable"
                                )
                                stats["review_license"] += 1
                            elif evidence_type == "CONTEXT_REVIEW_REQUIRED":
                                review_status = "REVIEW_CONTEXT"
                                rejection = "contextual daypart evidence requires review"
                                stats["review_context"] += 1
                            else:
                                review_status = "HIGH_CONFIDENCE"
                                rejection = None
                                stats["high_confidence"] += 1
                            source_expression_start = page_offset + detection.start
                            candidate_id = _insert_candidate(
                                connection,
                                dump,
                                page,
                                metadata,
                                detection,
                                context,
                                previous,
                                paragraph,
                                following,
                                possible,
                                semantics,
                                resolution,
                                evidence_type,
                                evidence_text,
                                duplicate,
                                review_status,
                                rejection,
                                source_expression_start,
                            )
                            if review_status == "HIGH_CONFIDENCE":
                                useful = [
                                    minute for minute in relevant if counts.get(minute, 0) < 3
                                ]
                                if useful:
                                    quote_id = _import_candidate(
                                        connection,
                                        candidate_id,
                                        detection,
                                        useful,
                                        source_id,
                                        import_run_id,
                                    )
                                    duplicate_index.add(
                                        "LEGACY", quote_id, context.quote, min(useful)
                                    )
                                    imported += 1
                                    relationships += len(useful)
                                    for minute in useful:
                                        counts[minute] = counts.get(minute, 0) + 1
                                    stats["imported_quotes"] += 1
                                    stats["relationships_added"] += len(useful)
                                    if imported == 1:
                                        _record_checkpoint(
                                            connection,
                                            phase2d_run_id,
                                            "first-useful",
                                            pages_processed,
                                            imported,
                                        )
                            duplicate_index.add(
                                "WIKISOURCE", candidate_id, context.quote, min(relevant)
                            )
                        page_offset += len(paragraph) + 2
                    if pages_processed % 500 == 0:
                        connection.commit()
                        remaining_count = sum(value < 3 for value in counts.values())
                        print(
                            "Wikisource mining: "
                            f"{pages_processed:,} filtered pages; {imported:,} quotes imported; "
                            f"{remaining_count:,} target minutes remain",
                            file=sys.stderr,
                            flush=True,
                        )
                        completed = initial_targets - remaining_count
                        for fraction, label in (
                            (0.25, "25-percent"),
                            (0.50, "50-percent"),
                            (0.75, "75-percent"),
                        ):
                            if completed >= math.ceil(initial_targets * fraction):
                                _record_checkpoint(
                                    connection,
                                    phase2d_run_id,
                                    label,
                                    pages_processed,
                                    imported,
                                )
            if stop:
                break
        if stop:
            _record_checkpoint(
                connection, phase2d_run_id, "all-targets-complete", pages_processed, imported
            )
        elapsed = time.monotonic() - started
        remaining = current_sparse_targets(connection)
        connection.execute(
            """
            UPDATE import_runs SET finished_at = ?, status = 'COMPLETE',
                raw_record_count = ?, canonical_inserted = ? WHERE id = ?
            """,
            (_now(), int(stats["sparse_relevant"]), imported, import_run_id),
        )
        connection.execute(
            """
            UPDATE wikisource_runs SET finished_at = ?, status = 'COMPLETE',
                streams_scanned = 1, pages_scanned = ?, mainspace_pages = ?,
                page_namespace_pages = ?, literary_works_considered = ?,
                characters_scanned = ?, expressions_detected = ?, sparse_relevant = ?,
                duplicates_detected = ?, imported_quotes = ?, relationships_added = ?,
                runtime_seconds = ? WHERE id = ?
            """,
            (
                _now(),
                pages_processed,
                int(spool_counts.get("mainspace_spooled", 0)),
                int(spool_counts.get("page_spooled", 0)),
                len(works),
                int(stats["characters_scanned"]),
                int(stats["expressions_detected"]),
                int(stats["sparse_relevant"]),
                int(stats["duplicates"]),
                imported,
                relationships,
                elapsed,
                wiki_run_id,
            ),
        )
        connection.execute(
            """
            UPDATE phase2d_runs SET status = 'COMPLETE', finished_at = ? WHERE id = ?
            """,
            (_now(), phase2d_run_id),
        )
        connection.executemany(
            """
            UPDATE phase2d_targets SET final_count = ?
            WHERE run_id = ? AND minute_of_day = ?
            """,
            [(counts.get(minute, 0), phase2d_run_id, minute) for minute in range(1440)],
        )
        connection.commit()
        for path in (main_path, page_path):
            path.unlink(missing_ok=True)
        return {
            **{key: int(value) for key, value in stats.items()},
            "pages_processed": pages_processed,
            "imported_quotes": imported,
            "relationships_added": relationships,
            "remaining_targets": len(remaining),
            "remaining_deficit": sum(target.deficit_to_3 for target in remaining),
            "runtime_seconds": elapsed,
        }
    except Exception as error:
        connection.rollback()
        connection.execute(
            "UPDATE wikisource_runs SET finished_at = ?, status = 'FAILED', error = ? WHERE id = ?",
            (_now(), str(error), wiki_run_id),
        )
        connection.execute(
            "UPDATE phase2d_runs SET status = 'RECOVERY_COMPLETE', error = ? WHERE id = ?",
            (str(error), phase2d_run_id),
        )
        connection.commit()
        raise


def revalidate_wikisource_imports(connection: sqlite3.Connection) -> dict[str, int]:
    """Remove Wikisource imports that fail the current deterministic quality gates."""
    initialize_database(connection)
    rows = connection.execute(
        """
        SELECT candidate.*, provenance.import_run_id
        FROM wikisource_candidates AS candidate
        LEFT JOIN quote_provenance AS provenance
          ON provenance.quote_id = candidate.imported_quote_id
        WHERE candidate.review_status = 'IMPORTED'
          AND candidate.imported_quote_id IS NOT NULL
        ORDER BY candidate.id
        """
    ).fetchall()
    removed_quotes = 0
    removed_relationships = 0
    import_run_counts: Counter[int] = Counter()
    for row in rows:
        reason = _phase2d_context_rejection(str(row["containing_paragraph"]), str(row["quote"]))
        if reason is None:
            continue
        quote_id = int(row["imported_quote_id"])
        relationship_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM quote_minute_eligibility WHERE quote_id = ?",
                (quote_id,),
            ).fetchone()[0]
        )
        connection.execute(
            """
            UPDATE wikisource_candidates SET review_status = 'REJECTED_QUALITY',
                rejection_reason = ?, imported_quote_id = NULL, imported_at = NULL
            WHERE id = ?
            """,
            (reason, row["id"]),
        )
        connection.execute("DELETE FROM quotes WHERE id = ?", (quote_id,))
        removed_quotes += 1
        removed_relationships += relationship_count
        if row["import_run_id"] is not None:
            import_run_counts[int(row["import_run_id"])] += 1

    if removed_quotes:
        connection.execute(
            """
            UPDATE sources SET record_count = MAX(0, record_count - ?)
            WHERE name LIKE 'english_wikisource/%'
            """,
            (removed_quotes,),
        )
        connection.execute(
            """
            UPDATE wikisource_runs SET imported_quotes = imported_quotes - ?,
                relationships_added = relationships_added - ?
            WHERE id = (SELECT MAX(id) FROM wikisource_runs)
            """,
            (removed_quotes, removed_relationships),
        )
        for run_id, count in import_run_counts.items():
            connection.execute(
                """
                UPDATE import_runs SET canonical_inserted = MAX(0, canonical_inserted - ?)
                WHERE id = ?
                """,
                (count, run_id),
            )
        latest_phase = connection.execute(
            "SELECT id FROM phase2d_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if latest_phase is not None:
            connection.execute(
                """
                UPDATE phase2d_targets
                SET final_count = (
                    SELECT COUNT(*) FROM quote_minute_pool
                    WHERE minute_of_day = phase2d_targets.minute_of_day
                )
                WHERE run_id = ?
                """,
                (latest_phase["id"],),
            )
    connection.commit()
    return {
        "checked": len(rows),
        "removed_quotes": removed_quotes,
        "removed_relationships": removed_relationships,
    }
