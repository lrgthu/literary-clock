"""Staged French/Chinese corpus mining, review export, and conservative import."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from litclock.db import initialize_database
from litclock.models import CandidateConfidence, TimeDetection
from litclock.multilingual_sources import (
    ChineseGutenbergAdapter,
    FrenchGutenbergAdapter,
    MultilingualSource,
    sha256_file,
)
from litclock.normalize import minute_to_time, text_hash
from litclock.timeparse_fr import detect_french_time_expressions
from litclock.timeparse_zh import detect_chinese_time_expressions
from litclock.xhtml import extract_quote_context

_FR_NONPROSE_RE = re.compile(
    r"^(?:table des matières|matières|sommaire|index|notes?|appendice|"
    r"chapitre\s+[\divxlcdm]+|livre\s+[\divxlcdm]+)\b",
    re.IGNORECASE,
)
_ZH_NONPROSE_RE = re.compile(
    r"^(?:目錄|目录|版權|版权|校者|編者|编者|第[〇零一二三四五六七八九十百\d]+[回章節节])\s*$"
)
_FR_DURATION_PREFIX_RE = re.compile(
    r"(?:\b(?:pendant|durant|depuis|après|avant)\s+|\bil\s+y\s+a\s+)$",
    re.IGNORECASE,
)
_FR_DURATION_SUFFIX_RE = re.compile(
    r"^\s+d(?:e|['’])\s*(?:retard|attente|travail|marche|route|voyage|sommeil|repos)\b",
    re.IGNORECASE,
)
_FR_TIME_CONTEXT_RE = re.compile(
    r"(?:\b(?:à|dès|jusqu['’]à)\s+|\b(?:il|ce)\s+(?:était|est|sera)\s+|"
    r"\b(?:horloge|pendule|montre|cloche|heure)\b[^.!?]{0,35}$)",
    re.IGNORECASE,
)
_FR_MIDI_PREFIX_RE = re.compile(
    r"(?:\b(?:à|a|vers|dès|des|après|avant|depuis)\s+|jusqu['’]à\s+)$",
    re.IGNORECASE,
)
_FR_MIDI_SUFFIX_RE = re.compile(
    r"^\s*(?:sonn(?:a|ait|èrent|erait|é)|approch(?:ait|e)|arriv(?:a|ait|e)|"
    r"était|est|sera|précis(?:e)?|passé|moins|et\s+(?:demi|quart)|\d|"
    r"(?:un|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|onze|douze|"
    r"treize|quatorze|quinze|seize|vingt|trente|quarante|cinquante))\b",
    re.IGNORECASE,
)


def _chinese_has_corrupt_script_mixture(text: str) -> bool:
    """Detect severe mojibake/PUA mixtures without rejecting ordinary Latin names."""
    unexpected = 0
    for char in text:
        codepoint = ord(char)
        if (
            char.isspace()
            or 0x3400 <= codepoint <= 0x9FFF
            or 0x20000 <= codepoint <= 0x3134F
            or 0x3000 <= codepoint <= 0x303F
            or 0xFF00 <= codepoint <= 0xFFEF
            or codepoint < 0x100
        ):
            continue
        if unicodedata.category(char).startswith(("L", "N", "C")):
            unexpected += 1
    return unexpected >= 4


def _now() -> str:
    return datetime.now(UTC).isoformat()


def normalized_multilingual_text(value: str) -> str:
    """Comparison form that preserves accents, ligatures, and Chinese script."""
    return "".join(
        char for char in unicodedata.normalize("NFC", value).casefold() if char.isalnum()
    )


def multilingual_identity(language: str, quote: str, possible_minutes: tuple[int, ...]) -> str:
    payload = (
        language
        + "\0"
        + normalized_multilingual_text(quote)
        + "\0"
        + ",".join(str(value) for value in possible_minutes)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def candidate_quality_rejection(
    language: str,
    quote: str,
    detection: TimeDetection,
) -> str | None:
    """Reject source furniture and clear non-time constructions before import."""
    compact = " ".join(quote.split())
    if "project gutenberg" in compact.casefold():
        return "Project Gutenberg boilerplate"
    if "�" in compact:
        return "Unicode replacement character/OCR damage"
    if language == "fr":
        if _FR_NONPROSE_RE.match(compact):
            return "French heading or source furniture"
        words = re.findall(r"\b[^\W\d_]+(?:[’'][^\W\d_]+)?\b", compact, re.UNICODE)
        if len(words) < 8:
            return "French excerpt has fewer than eight prose words"
        local_before = quote[max(0, detection.start - 35) : detection.start]
        local_after = quote[detection.end : min(len(quote), detection.end + 35)]
        if _FR_DURATION_PREFIX_RE.search(local_before) or _FR_DURATION_SUFFIX_RE.match(local_after):
            return "French duration rather than clock time"
        if (
            detection.parser_rule == "fr_midi_minuit"
            and detection.text.casefold().startswith("midi")
            and not _FR_MIDI_PREFIX_RE.search(local_before)
            and not _FR_MIDI_SUFFIX_RE.match(local_after)
        ):
            return "French midi lacks an explicit clock-time context gate"
        if (
            detection.candidate_confidence == CandidateConfidence.HIGH
            and detection.parser_rule in {"fr_hour", "fr_hour_minute", "fr_numeric_h"}
            and "du matin" not in detection.text.casefold()
            and "soir" not in detection.text.casefold()
            and "après-midi" not in detection.text.casefold()
            and "apres-midi" not in detection.text.casefold()
            and not _FR_TIME_CONTEXT_RE.search(local_before)
            and not re.match(r"\s*(?:sonna|sonnait|sonné|précises?|justes?)\b", local_after, re.I)
        ):
            return "French 24-hour form lacks a clock-time context gate"
    else:
        if _ZH_NONPROSE_RE.match(compact):
            return "Chinese heading or source furniture"
        if _chinese_has_corrupt_script_mixture(compact):
            return "Chinese excerpt contains corrupt mixed-script/OCR text"
        han_count = sum("\u3400" <= char <= "\u9fff" for char in compact)
        if han_count < 12:
            return "Chinese excerpt has fewer than twelve Han characters"
        if re.search(r"(?:第[一二三四五六七八九十]+[、，,]){2,}", compact):
            return "Chinese enumerated list"
    if len(compact) > 600:
        return "excerpt exceeds 600 characters"
    return None


def _adapter(language: str):
    if language == "fr":
        return FrenchGutenbergAdapter
    if language == "zh":
        return ChineseGutenbergAdapter
    raise ValueError(language)


def _detector(language: str):
    if language == "fr":
        return detect_french_time_expressions
    if language == "zh":
        return detect_chinese_time_expressions
    raise ValueError(language)


def _upsert_source(
    connection: sqlite3.Connection,
    source: MultilingualSource,
    path: Path,
    imported_at: str,
) -> int:
    name = f"{source.source_project}/{source.source_id}"
    slug = f"{source.language}-{source.source_id}"
    connection.execute(
        """
        INSERT INTO sources (
            name, slug, source_url, source_license, source_project, language,
            script_variant, translator_editor, publication_metadata, rights_evidence,
            upstream_commit, corpus_path, corpus_sha256, imported_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            source_url = excluded.source_url,
            source_license = excluded.source_license,
            source_project = excluded.source_project,
            language = excluded.language,
            script_variant = excluded.script_variant,
            translator_editor = excluded.translator_editor,
            publication_metadata = excluded.publication_metadata,
            rights_evidence = excluded.rights_evidence,
            upstream_commit = excluded.upstream_commit,
            corpus_path = excluded.corpus_path,
            corpus_sha256 = excluded.corpus_sha256,
            imported_at = excluded.imported_at
        """,
        (
            name,
            slug,
            source.source_url,
            source.source_license,
            source.source_project,
            source.language,
            source.script_variant,
            source.translator_editor,
            source.publication_metadata,
            source.rights_evidence,
            f"sha256:{source.expected_sha256}",
            str(path),
            source.expected_sha256,
            imported_at,
        ),
    )
    return int(connection.execute("SELECT id FROM sources WHERE name = ?", (name,)).fetchone()[0])


def _candidate_class(
    language: str, quote: str, detection: TimeDetection
) -> tuple[CandidateConfidence, str | None]:
    candidate_class = detection.candidate_confidence or CandidateConfidence.MEDIUM
    reason = candidate_quality_rejection(language, quote, detection)
    if reason:
        return CandidateConfidence.REJECT, reason
    return candidate_class, detection.rejection_reason


def mine_multilingual_sources(
    connection: sqlite3.Connection,
    sources: list[MultilingualSource],
    data_directory: Path,
    *,
    language: str,
    stage: str = "pilot",
    limit_sources: int | None = None,
) -> dict[str, Any]:
    """Mine a bounded, checksum-pinned language pilot into review candidates."""
    initialize_database(connection)
    started_at = _now()
    cursor = connection.execute(
        "INSERT INTO multilingual_runs (language, stage, started_at, status) "
        "VALUES (?, ?, ?, 'RUNNING')",
        (language, stage, started_at),
    )
    run_id = int(cursor.lastrowid)
    totals: Counter[str] = Counter()
    selected = [source for source in sources if source.language == language]
    if limit_sources is not None:
        selected = selected[:limit_sources]
    detector = _detector(language)
    adapter = _adapter(language)
    try:
        for source in selected:
            path = data_directory / language / source.filename
            if not path.is_file():
                raise FileNotFoundError(f"missing {path}; run multilingual-acquire first")
            actual_sha = sha256_file(path)
            if actual_sha != source.expected_sha256:
                raise ValueError(f"checksum mismatch for {path}")
            source_id = _upsert_source(connection, source, path, started_at)
            text = path.read_text(encoding="utf-8-sig")
            totals["sources_scanned"] += 1
            totals["characters_scanned"] += len(text)
            source_records = 0
            paragraphs = adapter.paragraphs(text)
            for paragraph in paragraphs:
                detections = detector(paragraph.text)
                totals["expressions_detected"] += len(detections)
                for detection in detections:
                    context = extract_quote_context(paragraph.text, detection)
                    quote_detection = replace(
                        detection,
                        start=context.highlight_start,
                        end=context.highlight_end,
                    )
                    candidate_class, rejection_reason = _candidate_class(
                        language, context.quote, quote_detection
                    )
                    if context.rejection_reason and candidate_class == CandidateConfidence.HIGH:
                        candidate_class = CandidateConfidence.REJECT
                        rejection_reason = context.rejection_reason
                    quote_detection_start = context.highlight_start
                    quote_detection_end = context.highlight_end
                    if context.quote[quote_detection_start:quote_detection_end] != detection.text:
                        raise AssertionError("multilingual highlight offsets failed to round-trip")
                    source_expression_start = paragraph.source_offset(detection.start)
                    source_expression_end = paragraph.source_offset(detection.end)
                    normalized_hash = text_hash(normalized_multilingual_text(context.quote))
                    identity = multilingual_identity(
                        language, context.quote, detection.possible_minutes
                    )
                    duplicate = connection.execute(
                        """
                        SELECT id FROM multilingual_candidates
                        WHERE language = ? AND language_identity_hash = ?
                        ORDER BY id LIMIT 1
                        """,
                        (language, identity),
                    ).fetchone()
                    duplicate_status = "NEW" if duplicate is None else "NORMALIZED_DUPLICATE"
                    review_status = (
                        "REJECTED"
                        if candidate_class == CandidateConfidence.REJECT
                        else "NEEDS_REVIEW"
                        if candidate_class
                        in {CandidateConfidence.MEDIUM, CandidateConfidence.AMBIGUOUS_CLOCKFACE}
                        else "UNREVIEWED"
                    )
                    record_id = (
                        f"{source.source_id}:p{paragraph.paragraph_index}:"
                        f"{source_expression_start}-{source_expression_end}"
                    )
                    result = connection.execute(
                        """
                        INSERT OR IGNORE INTO multilingual_candidates (
                            source_id, language, script_variant, source_record_id,
                            source_locator, source_document_checksum, work_title, author,
                            translator_editor, matched_text, quote,
                            source_expression_start, source_expression_end,
                            highlight_start, highlight_end, minute_of_day, possible_minutes,
                            semantic_type, time_confidence, confidence_class, parser_rule,
                            evidence_text, context_before, context_after, review_status,
                            rejection_reason, normalized_quote_hash, language_identity_hash,
                            duplicate_status, duplicate_of_candidate_id, created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                        (
                            source_id,
                            language,
                            source.script_variant,
                            record_id,
                            f"{source.source_url}#paragraph-{paragraph.paragraph_index}",
                            actual_sha,
                            source.title,
                            source.author,
                            source.translator_editor,
                            detection.text,
                            context.quote,
                            source_expression_start,
                            source_expression_end,
                            quote_detection_start,
                            quote_detection_end,
                            detection.minute_of_day,
                            json.dumps(detection.possible_minutes),
                            detection.semantic_type,
                            detection.confidence.value,
                            candidate_class.value,
                            detection.parser_rule,
                            detection.ampm_evidence,
                            paragraph.text[max(0, detection.start - 160) : detection.start],
                            paragraph.text[detection.end : detection.end + 160],
                            review_status,
                            rejection_reason,
                            normalized_hash,
                            identity,
                            duplicate_status,
                            int(duplicate["id"]) if duplicate is not None else None,
                            started_at,
                        ),
                    )
                    if result.rowcount:
                        source_records += 1
                        totals[candidate_class.value] += 1
            connection.execute(
                "UPDATE sources SET record_count = ? WHERE id = ?", (source_records, source_id)
            )
            connection.commit()
        finished_at = _now()
        connection.execute(
            """
            UPDATE multilingual_runs SET finished_at = ?, status = 'COMPLETE',
                sources_scanned = ?, characters_scanned = ?, expressions_detected = ?,
                high_confidence = ?, medium = ?, ambiguous_clockface = ?, rejected = ?
            WHERE id = ?
            """,
            (
                finished_at,
                totals["sources_scanned"],
                totals["characters_scanned"],
                totals["expressions_detected"],
                totals["HIGH"],
                totals["MEDIUM"],
                totals["AMBIGUOUS_CLOCKFACE"],
                totals["REJECT"],
                run_id,
            ),
        )
        connection.commit()
    except Exception as error:
        connection.execute(
            "UPDATE multilingual_runs SET finished_at = ?, status = 'FAILED', error = ? "
            "WHERE id = ?",
            (_now(), str(error), run_id),
        )
        connection.commit()
        raise
    return {"run_id": run_id, "language": language, **dict(totals)}


def _stratified(rows: list[sqlite3.Row], limit: int) -> list[sqlite3.Row]:
    groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        groups[str(row["parser_rule"])].append(row)
    for values in groups.values():
        values.sort(key=lambda row: (str(row["language_identity_hash"]), int(row["id"])))
    selected: list[sqlite3.Row] = []
    keys = sorted(groups)
    while keys and len(selected) < limit:
        next_keys: list[str] = []
        for key in keys:
            if groups[key] and len(selected) < limit:
                selected.append(groups[key].pop(0))
            if groups[key]:
                next_keys.append(key)
        keys = next_keys
    return selected


def export_review_sample(
    connection: sqlite3.Connection,
    output_directory: Path,
    *,
    language: str,
    per_class: int = 100,
) -> tuple[Path, Path, dict[str, int]]:
    """Write deterministic, expression-stratified local CSV/Markdown samples."""
    initialize_database(connection)
    class_queries = {
        "high": ("HIGH",),
        "rejected": ("REJECT",),
        "ambiguous_medium": ("AMBIGUOUS_CLOCKFACE", "MEDIUM"),
    }
    sample: list[tuple[str, sqlite3.Row]] = []
    counts: dict[str, int] = {}
    for label, classes in class_queries.items():
        placeholders = ",".join("?" for _ in classes)
        rows = list(
            connection.execute(
                f"""
                SELECT c.*, s.source_project, s.source_url, s.source_license
                FROM multilingual_candidates AS c JOIN sources AS s ON s.id = c.source_id
                WHERE c.language = ? AND c.confidence_class IN ({placeholders})
                ORDER BY c.id
                """,  # noqa: S608 - placeholders are generated from internal constants
                (language, *classes),
            )
        )
        selected = _stratified(rows, per_class)
        counts[label] = len(selected)
        sample.extend((label, row) for row in selected)
    output_directory.mkdir(parents=True, exist_ok=True)
    csv_path = output_directory / f"{language}_review_sample.csv"
    fields = [
        "sample_class",
        "candidate_id",
        "confidence_class",
        "parser_rule",
        "matched_text",
        "minute_of_day",
        "possible_minutes",
        "quote",
        "work_title",
        "author",
        "source_locator",
        "source_license",
        "rejection_reason",
        "human_label",
        "review_notes",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for label, row in sample:
            writer.writerow(
                {
                    "sample_class": label,
                    "candidate_id": row["id"],
                    "confidence_class": row["confidence_class"],
                    "parser_rule": row["parser_rule"],
                    "matched_text": row["matched_text"],
                    "minute_of_day": row["minute_of_day"],
                    "possible_minutes": row["possible_minutes"],
                    "quote": row["quote"],
                    "work_title": row["work_title"],
                    "author": row["author"],
                    "source_locator": row["source_locator"],
                    "source_license": row["source_license"],
                    "rejection_reason": row["rejection_reason"],
                    "human_label": "",
                    "review_notes": "",
                }
            )
    markdown_path = output_directory / f"{language}_review_sample.md"
    lines = [
        f"# {language} deterministic parser review sample",
        "",
        "Machine-generated local review material; not committed.",
        "",
        "| Class | Candidate | Rule | Match | Work |",
        "|---|---:|---|---|---|",
    ]
    for label, row in sample:
        matched = str(row["matched_text"]).replace("|", "\\|")
        title = str(row["work_title"]).replace("|", "\\|")
        lines.append(f"| {label} | {row['id']} | {row['parser_rule']} | {matched} | {title} |")
    temporary = markdown_path.with_suffix(".md.tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, markdown_path)
    return csv_path, markdown_path, counts


def import_high_confidence(
    connection: sqlite3.Connection,
    *,
    language: str,
) -> dict[str, int]:
    """Import only novel HIGH candidates; ambiguous/medium candidates remain review-only."""
    initialize_database(connection)
    now = _now()
    run_id = int(
        connection.execute(
            "INSERT INTO import_runs (started_at, status) VALUES (?, 'RUNNING')", (now,)
        ).lastrowid
    )
    imported = duplicates = 0
    rows = connection.execute(
        """
        SELECT c.*, s.name AS source_name, s.source_url, s.source_license,
               s.publication_metadata, s.rights_evidence
        FROM multilingual_candidates AS c JOIN sources AS s ON s.id = c.source_id
        WHERE c.language = ? AND c.confidence_class = 'HIGH'
          AND c.duplicate_status = 'NEW' AND c.imported_quote_id IS NULL
          AND c.minute_of_day IS NOT NULL
        ORDER BY c.id
        """,
        (language,),
    ).fetchall()
    for row in rows:
        existing = connection.execute(
            """
            SELECT id FROM quotes
            WHERE language = ? AND minute_of_day = ? AND text_normalized_hash = ?
            ORDER BY id LIMIT 1
            """,
            (language, row["minute_of_day"], row["normalized_quote_hash"]),
        ).fetchone()
        if existing is not None:
            quote_id = int(existing["id"])
            duplicate_kind = "TRIVIAL_VARIANT"
            duplicates += 1
        else:
            legacy_normalized_hash = text_hash(f"{language}:{row['normalized_quote_hash']}")
            quote_id = int(
                connection.execute(
                    """
                    INSERT INTO quotes (
                        minute_of_day, time_24h, time_text, quote, title, author, sfw,
                        language, script_variant, source_name, source_url, source_license,
                        source_record_id, quote_hash, normalized_quote_hash,
                        text_normalized_hash, language_identity_hash,
                        highlight_start, highlight_end, quality_status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              'VERIFIED_EXACT', ?)
                    """,
                    (
                        row["minute_of_day"],
                        minute_to_time(int(row["minute_of_day"])),
                        row["matched_text"],
                        row["quote"],
                        row["work_title"],
                        row["author"],
                        language,
                        row["script_variant"],
                        row["source_name"],
                        row["source_url"],
                        row["source_license"],
                        row["source_record_id"],
                        text_hash(str(row["quote"])),
                        legacy_normalized_hash,
                        row["normalized_quote_hash"],
                        row["language_identity_hash"],
                        row["highlight_start"],
                        row["highlight_end"],
                        now,
                    ),
                ).lastrowid
            )
            duplicate_kind = "CANONICAL"
            imported += 1
        connection.execute(
            """
            INSERT OR IGNORE INTO quote_provenance (
                quote_id, source_id, import_run_id, source_record_id,
                raw_time_24h, raw_time_text, raw_quote, raw_title, raw_author, raw_sfw,
                raw_language, raw_script_variant, translator_editor, publication_metadata,
                rights_evidence, raw_quote_hash, validation_status, highlight_start,
                highlight_end, duplicate_kind, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, 'VERIFIED_EXACT',
                      ?, ?, ?, ?)
            """,
            (
                quote_id,
                row["source_id"],
                run_id,
                row["source_record_id"],
                minute_to_time(int(row["minute_of_day"])),
                row["matched_text"],
                row["quote"],
                row["work_title"],
                row["author"],
                language,
                row["script_variant"],
                row["translator_editor"],
                row["publication_metadata"],
                row["rights_evidence"],
                text_hash(str(row["quote"])),
                row["highlight_start"],
                row["highlight_end"],
                duplicate_kind,
                json.dumps({"multilingual_candidate_id": row["id"]}),
            ),
        )
        connection.execute(
            """
            UPDATE quote_time_semantics SET
                language = ?, matched_text = ?, possible_minutes = ?,
                semantic_confidence = 'HIGH', time_semantics = ?,
                narrative_resolution = 'RESOLVED', evidence_type = 'LANGUAGE_PARSER',
                evidence_text = ?, source_candidate_type = 'MULTILINGUAL',
                source_candidate_id = ?, updated_at = ?
            WHERE quote_id = ?
            """,
            (
                language,
                row["matched_text"],
                row["possible_minutes"],
                row["semantic_type"],
                row["evidence_text"],
                row["id"],
                now,
                quote_id,
            ),
        )
        connection.execute(
            """
            UPDATE quote_minute_eligibility SET language = ?,
                evidence_type = 'LANGUAGE_PARSER', evidence_text = ?,
                source_candidate_type = 'MULTILINGUAL', source_candidate_id = ?
            WHERE quote_id = ? AND minute_of_day = ?
            """,
            (language, row["evidence_text"], row["id"], quote_id, row["minute_of_day"]),
        )
        connection.execute(
            "UPDATE multilingual_candidates SET imported_quote_id = ?, imported_at = ? "
            "WHERE id = ?",
            (quote_id, now, row["id"]),
        )
    connection.execute(
        """
        UPDATE import_runs SET finished_at = ?, status = 'COMPLETE', raw_record_count = ?,
            canonical_inserted = ?, trivial_variants = ? WHERE id = ?
        """,
        (_now(), len(rows), imported, duplicates, run_id),
    )
    connection.commit()
    return {"considered": len(rows), "imported": imported, "duplicates": duplicates}
