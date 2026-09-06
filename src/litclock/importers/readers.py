"""Readers that translate source-specific files into common raw records."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from typing import Any

import yaml

from litclock.models import MalformedRecord, RawQuote, SourceSpec


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _parse_sfw(value: Any, style: str | None) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = _text(value).strip().casefold()
    if not normalized or normalized in {"unknown", "null", "none", "?"}:
        return None
    if style == "nsfw_yes_no":
        if normalized == "yes":
            return False
        if normalized == "no":
            return True
    if normalized in {"sfw", "safe", "true", "1", "yes"}:
        return True
    if normalized in {"nsfw", "nswf", "unsafe", "false", "0", "no"}:
        return False
    return None


def _read_pipe_csv(spec: SourceSpec) -> Iterator[RawQuote | MalformedRecord]:
    with spec.corpus_path.open(encoding="utf-8-sig", newline="") as handle:
        # Upstream uses pipes as its only field syntax. Prose quotation marks are literal and
        # are not RFC CSV quoting: some rows open with ASCII `"` and close with a curly quote.
        # Enabling CSV quoting makes the reader swallow later physical records into that field.
        reader = csv.reader(handle, delimiter="|", quoting=csv.QUOTE_NONE)
        try:
            for index, row in enumerate(reader, 1):
                record_id = str(index)
                if len(row) not in {5, 6}:
                    yield MalformedRecord(
                        record_id,
                        f"expected 5 or 6 pipe-delimited fields, got {len(row)}",
                        row,
                    )
                    continue
                sfw = _parse_sfw(row[5], spec.sfw_style) if len(row) == 6 else None
                yield RawQuote(
                    source_record_id=record_id,
                    time_24h=row[0],
                    time_text=row[1],
                    quote=row[2],
                    title=row[3],
                    author=row[4],
                    sfw=sfw,
                    raw_payload={"row": row, "csv_line_end": reader.line_num},
                )
        except csv.Error as error:
            yield MalformedRecord(
                str(reader.line_num), f"CSV parse error: {error}", {"line": reader.line_num}
            )


def _read_yaml(spec: SourceSpec) -> Iterator[RawQuote | MalformedRecord]:
    try:
        data = yaml.safe_load(spec.corpus_path.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as error:
        yield MalformedRecord(None, f"YAML parse error: {error}", {})
        return
    if not isinstance(data, list):
        yield MalformedRecord(None, "YAML root must be a list", data)
        return
    for index, item in enumerate(data, 1):
        record_id = str(index)
        if not isinstance(item, dict):
            yield MalformedRecord(record_id, "YAML record must be a mapping", item)
            continue
        required = {"time", "time_name", "quote"}
        missing = sorted(required - item.keys())
        if missing:
            yield MalformedRecord(record_id, f"missing required keys: {', '.join(missing)}", item)
            continue
        yield RawQuote(
            source_record_id=_text(item.get("id")) or record_id,
            time_24h=_text(item.get("time")),
            time_text=_text(item.get("time_name")),
            quote=_text(item.get("quote")),
            title=_text(item.get("source")),
            author=_text(item.get("author")),
            sfw=_parse_sfw(item.get("sfw"), "sfw_label"),
            raw_payload=item,
        )


def read_source(spec: SourceSpec) -> Iterator[RawQuote | MalformedRecord]:
    if spec.format == "pipe_csv":
        yield from _read_pipe_csv(spec)
    elif spec.format == "yaml":
        yield from _read_yaml(spec)
    else:
        yield MalformedRecord(None, f"unsupported source format: {spec.format}", {})
