"""Official English Wikisource dump discovery, acquisition, and XML streaming."""

from __future__ import annotations

import bz2
import hashlib
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from litclock.db import initialize_database

DUMPS_ROOT = "https://dumps.wikimedia.org/enwikisource"
USER_AGENT = "literary-clock/0.1 (public-domain corpus research; bulk dump client)"
MAIN_NAMESPACE = 0
PAGE_NAMESPACE = 104
INDEX_NAMESPACE = 106
SUPPORTED_NAMESPACES = {MAIN_NAMESPACE, PAGE_NAMESPACE, INDEX_NAMESPACE}


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class WikisourceDumpSpec:
    dump_date: str
    filename: str
    source_url: str
    expected_sha1: str
    index_filename: str
    index_source_url: str
    index_expected_sha1: str


@dataclass(frozen=True, slots=True)
class WikisourcePage:
    page_id: int
    namespace: int
    title: str
    revision_id: int
    revision_timestamp: str | None
    wikitext: str


def _request(url: str, *, range_start: int | None = None):
    headers = {"User-Agent": USER_AGENT}
    if range_start:
        headers["Range"] = f"bytes={range_start}-"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120)


def latest_complete_dump_date() -> str:
    """Return the newest dated dump directory advertised by official infrastructure."""
    with _request(f"{DUMPS_ROOT}/") as response:
        listing = response.read().decode("utf-8", errors="replace")
    dates = sorted(set(re.findall(r'href="(\d{8})/"', listing)), reverse=True)
    for dump_date in dates:
        try:
            with _request(f"{DUMPS_ROOT}/{dump_date}/dumpstatus.json") as response:
                status = json.load(response)
            if status["jobs"]["articlesmultistreamdump"]["status"] == "done":
                return dump_date
        except (KeyError, OSError, ValueError, urllib.error.URLError):
            continue
    raise RuntimeError("no complete English Wikisource multistream dump was advertised")


def parse_sha1sums(text: str) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{40}", parts[0]):
            checksums[parts[1].lstrip("*")] = parts[0].casefold()
    return checksums


def discover_dump(dump_date: str | None = None) -> WikisourceDumpSpec:
    dump_date = dump_date or latest_complete_dump_date()
    if not re.fullmatch(r"\d{8}", dump_date):
        raise ValueError("dump date must use YYYYMMDD")
    filename = f"enwikisource-{dump_date}-pages-articles-multistream.xml.bz2"
    index_filename = f"enwikisource-{dump_date}-pages-articles-multistream-index.txt.bz2"
    base = f"{DUMPS_ROOT}/{dump_date}"
    with _request(f"{base}/enwikisource-{dump_date}-sha1sums.txt") as response:
        checksums = parse_sha1sums(response.read().decode("ascii"))
    missing = [name for name in (filename, index_filename) if name not in checksums]
    if missing:
        raise RuntimeError(f"official checksum manifest is missing: {', '.join(missing)}")
    return WikisourceDumpSpec(
        dump_date,
        filename,
        f"{base}/{filename}",
        checksums[filename],
        index_filename,
        f"{base}/{index_filename}",
        checksums[index_filename],
    )


def file_sha1(path: Path) -> str:
    digest = hashlib.sha1()  # noqa: S324 - required by the upstream integrity manifest
    with path.open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _download_resumable(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    existing = partial.stat().st_size if partial.exists() else 0
    with _request(url, range_start=existing) as response:
        status = getattr(response, "status", response.getcode())
        if existing and status != 206:
            existing = 0
            mode = "wb"
        else:
            mode = "ab" if existing else "wb"
        downloaded = existing
        next_report = ((downloaded // (256 * 1024 * 1024)) + 1) * 256 * 1024 * 1024
        with partial.open(mode) as handle:
            while block := response.read(8 * 1024 * 1024):
                handle.write(block)
                downloaded += len(block)
                if downloaded >= next_report:
                    print(
                        f"Wikisource download: {downloaded / (1024**3):.2f} GiB",
                        file=sys.stderr,
                        flush=True,
                    )
                    next_report += 256 * 1024 * 1024
            handle.flush()
            os.fsync(handle.fileno())
    os.replace(partial, destination)


def _ensure_download(url: str, path: Path, expected_sha1: str) -> str:
    if path.is_file() and file_sha1(path) == expected_sha1:
        return expected_sha1
    _download_resumable(url, path)
    actual = file_sha1(path)
    if actual != expected_sha1:
        raise RuntimeError(
            f"checksum mismatch for {path.name}: expected {expected_sha1}, found {actual}"
        )
    return actual


def acquire_dump(
    connection: sqlite3.Connection,
    data_directory: Path,
    *,
    dump_date: str | None = None,
) -> sqlite3.Row:
    """Acquire the official multistream XML and index with restart-safe checksums."""
    initialize_database(connection)
    spec = discover_dump(dump_date)
    archive_path = data_directory / spec.filename
    index_path = data_directory / spec.index_filename
    connection.execute(
        """
        INSERT INTO wikisource_dumps (
            filename, dump_date, source_url, checksum_algorithm, expected_checksum,
            local_path, index_filename, index_source_url, index_expected_checksum,
            index_local_path, status
        ) VALUES (?, ?, ?, 'SHA1', ?, ?, ?, ?, ?, ?, 'DISCOVERED')
        ON CONFLICT(filename, expected_checksum) DO UPDATE SET
            source_url = excluded.source_url, index_source_url = excluded.index_source_url
        """,
        (
            spec.filename,
            spec.dump_date,
            spec.source_url,
            spec.expected_sha1,
            str(archive_path),
            spec.index_filename,
            spec.index_source_url,
            spec.index_expected_sha1,
            str(index_path),
        ),
    )
    dump_id = int(
        connection.execute(
            "SELECT id FROM wikisource_dumps WHERE filename = ? AND expected_checksum = ?",
            (spec.filename, spec.expected_sha1),
        ).fetchone()[0]
    )
    connection.execute(
        "UPDATE wikisource_dumps SET status = 'DOWNLOADING', error = NULL WHERE id = ?",
        (dump_id,),
    )
    connection.commit()
    try:
        index_sha1 = _ensure_download(spec.index_source_url, index_path, spec.index_expected_sha1)
        archive_sha1 = _ensure_download(spec.source_url, archive_path, spec.expected_sha1)
        connection.execute(
            """
            UPDATE wikisource_dumps SET actual_checksum = ?, index_actual_checksum = ?,
                byte_size = ?, acquisition_timestamp = ?, status = 'ACQUIRED', error = NULL
            WHERE id = ?
            """,
            (archive_sha1, index_sha1, archive_path.stat().st_size, _now(), dump_id),
        )
        connection.commit()
    except Exception as error:
        connection.execute(
            "UPDATE wikisource_dumps SET status = 'FAILED', error = ? WHERE id = ?",
            (str(error), dump_id),
        )
        connection.commit()
        raise
    manifest = data_directory / "manifest.json"
    row = connection.execute("SELECT * FROM wikisource_dumps WHERE id = ?", (dump_id,)).fetchone()
    manifest.write_text(json.dumps(dict(row), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return row


def _open_xml(path: Path) -> BinaryIO:
    if path.suffix == ".bz2":
        return bz2.open(path, "rb")
    return path.open("rb")


def iter_wikimedia_pages(path: Path) -> Iterator[WikisourcePage]:
    """Stream current revisions from a Wikimedia XML dump with bounded memory."""
    with _open_xml(path) as handle:
        for _, element in ET.iterparse(handle, events=("end",)):
            if element.tag.rsplit("}", 1)[-1] != "page":
                continue

            def child(parent: ET.Element, name: str) -> ET.Element | None:
                return next(
                    (item for item in parent if item.tag.rsplit("}", 1)[-1] == name),
                    None,
                )

            title = child(element, "title")
            namespace = child(element, "ns")
            page_id = child(element, "id")
            revision = child(element, "revision")
            if title is None or namespace is None or page_id is None or revision is None:
                element.clear()
                continue
            revision_id = child(revision, "id")
            timestamp = child(revision, "timestamp")
            text = child(revision, "text")
            if revision_id is not None:
                yield WikisourcePage(
                    int(page_id.text or 0),
                    int(namespace.text or 0),
                    title.text or "",
                    int(revision_id.text or 0),
                    timestamp.text if timestamp is not None else None,
                    text.text if text is not None and text.text is not None else "",
                )
            element.clear()


def multistream_ranges(index_path: Path, archive_size: int) -> list[tuple[int, int]]:
    """Return the byte ranges of the independently compressed dump members."""
    offsets: list[int] = []
    with bz2.open(index_path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            raw_offset = line.split(":", 1)[0]
            if not raw_offset.isdigit():
                continue
            offset = int(raw_offset)
            if not offsets or offsets[-1] != offset:
                offsets.append(offset)
    if not offsets:
        raise ValueError("Wikimedia multistream index contains no stream offsets")
    return list(zip(offsets, [*offsets[1:], archive_size], strict=True))


def pages_from_multistream_member(payload: bytes) -> list[WikisourcePage]:
    """Parse one decompressed multistream member, which has no XML root element."""
    payload = payload.replace(b"</mediawiki>", b"")
    root = ET.fromstring(b"<mediawiki>" + payload + b"</mediawiki>")
    pages: list[WikisourcePage] = []

    def child(parent: ET.Element, name: str) -> ET.Element | None:
        return next(
            (item for item in parent if item.tag.rsplit("}", 1)[-1] == name),
            None,
        )

    for element in root:
        if element.tag.rsplit("}", 1)[-1] != "page":
            continue
        title = child(element, "title")
        namespace = child(element, "ns")
        page_id = child(element, "id")
        revision = child(element, "revision")
        if title is None or namespace is None or page_id is None or revision is None:
            continue
        revision_id = child(revision, "id")
        timestamp = child(revision, "timestamp")
        text = child(revision, "text")
        if revision_id is not None:
            pages.append(
                WikisourcePage(
                    int(page_id.text or 0),
                    int(namespace.text or 0),
                    title.text or "",
                    int(revision_id.text or 0),
                    timestamp.text if timestamp is not None else None,
                    text.text if text is not None and text.text is not None else "",
                )
            )
    return pages


def namespace_disposition(namespace: int, title: str) -> str:
    """Classify dump namespaces explicitly before any prose processing."""
    if namespace == MAIN_NAMESPACE:
        return "MAINSPACE"
    if namespace == PAGE_NAMESPACE and title.startswith("Page:"):
        return "PAGE_TRANSCRIPTION"
    if namespace == INDEX_NAMESPACE and title.startswith("Index:"):
        return "INDEX_METADATA"
    return "EXCLUDED_NAMESPACE"
