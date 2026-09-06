"""Official Project Gutenberg bulk catalog and selective mirror acquisition."""

from __future__ import annotations

import bz2
import csv
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from litclock.db import initialize_database

CATALOG_CSV_URL = "https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv.gz"
CATALOG_RDF_URL = "https://www.gutenberg.org/cache/epub/feeds/rdf-files.tar.bz2"
RSYNC_MIRRORS = (
    "gutenberg.pglaf.org::gutenberg-epub/",
    "rsync.ibiblio.org::gutenberg-epub/",
)
PUBLIC_DOMAIN_RIGHTS = "Public domain in the USA."
CATALOG_USER_AGENT = "literary-clock/0.1 (bulk catalog client; no ebook page crawling)"
CATALOG_INDEXER_VERSION = 7

_POSITIVE_TERMS = {
    "adventure stories",
    "autobiographies",
    "biographical fiction",
    "children's stories",
    "children's literature",
    "drama",
    "fairy tales",
    "fiction",
    "historical fiction",
    "legends",
    "literature",
    "memoirs",
    "novel",
    "plays",
    "romance fiction",
    "short stories",
    "travel",
}
_POSITIVE_SHELF_TERMS = {
    "adventure",
    "children",
    "fiction",
    "literature",
    "plays",
    "school stories",
    "short stories",
}
_NEGATIVE_TERMS = {
    "almanacs",
    "bibliography",
    "bible",
    "catalogs",
    "congresses",
    "dictionaries",
    "directories",
    "encyclopedias",
    "guidebooks",
    "handbooks",
    "history and criticism",
    "indexes",
    "law",
    "legislation",
    "manuals",
    "meteorology",
    "parliament",
    "periodicals",
    "railroad travel",
    "railroad company",
    "sermons",
    "statistics",
    "technical manuals",
    "theology",
}
_WEAK_SHELF_LABELS = (
    "nobel prizes in literature",
    "children's history",
    "children's instructional books",
)
_LITERARY_LOCC_PREFIXES = (
    "PZ",
    "PR",
    "PS",
    "PN",
    "PG",
    "PH",
    "PJ",
    "PK",
    "PL",
    "PM",
    "PQ",
    "PT",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class GutenbergMetadata:
    ebook_id: int
    pg_type: str
    issued: str
    title: str
    language: str
    authors: str
    subjects: str
    locc: str
    bookshelves: str
    rights: str | None = None

    @property
    def source_url(self) -> str:
        return f"https://www.gutenberg.org/ebooks/{self.ebook_id}"


def _download_with_resume(
    url: str,
    destination: Path,
    *,
    retries: int = 5,
    timeout: int = 120,
) -> Path:
    """Download one official bulk feed with HTTP Range restart support."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size:
        return destination
    partial = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(retries):
        offset = partial.stat().st_size if partial.exists() else 0
        request = urllib.request.Request(url, headers={"User-Agent": CATALOG_USER_AGENT})
        if offset:
            request.add_header("Range", f"bytes={offset}-")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = getattr(response, "status", None)
                append = offset > 0 and status == 206
                if offset and not append:
                    offset = 0
                mode = "ab" if append else "wb"
                with partial.open(mode) as handle:
                    shutil.copyfileobj(response, handle, length=1024 * 1024)
            os.replace(partial, destination)
            return destination
        except (OSError, urllib.error.URLError):
            if attempt + 1 >= retries:
                raise
            time.sleep(min(30, 2**attempt))
    raise AssertionError("unreachable")


def iter_catalog_csv(path: Path) -> Iterator[GutenbergMetadata]:
    """Yield rows from Project Gutenberg's official compressed CSV catalog."""
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "Text#",
            "Type",
            "Issued",
            "Title",
            "Language",
            "Authors",
            "Subjects",
            "LoCC",
            "Bookshelves",
        }
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError("unexpected Project Gutenberg CSV catalog schema")

        def cell(row: dict[str, str], name: str) -> str:
            return " ".join((row.get(name) or "").split())

        for row in reader:
            identifier = (row.get("Text#") or "").strip()
            if not identifier.isdigit():
                continue
            yield GutenbergMetadata(
                ebook_id=int(identifier),
                pg_type=cell(row, "Type"),
                issued=cell(row, "Issued"),
                title=cell(row, "Title"),
                language=cell(row, "Language"),
                authors=cell(row, "Authors"),
                subjects=cell(row, "Subjects"),
                locc=cell(row, "LoCC"),
                bookshelves=cell(row, "Bookshelves"),
            )


def parse_rdf_rights(payload: bytes) -> tuple[int | None, str | None]:
    """Extract ebook identifier and rights text from one RDF catalog record."""
    root = ET.fromstring(payload)
    ebook_id: int | None = None
    rights: str | None = None
    for element in root.iter():
        local = element.tag.rsplit("}", 1)[-1]
        if local == "ebook":
            for value in element.attrib.values():
                match = re.search(r"(?:ebooks/|ebooks:)(\d+)$", value)
                if match:
                    ebook_id = int(match.group(1))
                    break
        elif local == "rights":
            candidate = " ".join("".join(element.itertext()).split())
            if candidate:
                rights = candidate
    return ebook_id, rights


def iter_rdf_rights(path: Path) -> Iterator[tuple[int, str]]:
    """Stream the official bzip2 tar without extracting a second copy."""
    with path.open("rb") as raw, bz2.BZ2File(raw) as decompressed:
        with tarfile.open(fileobj=decompressed, mode="r|") as archive:
            for member in archive:
                if not member.isfile() or not member.name.endswith(".rdf"):
                    continue
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                try:
                    ebook_id, rights = parse_rdf_rights(handle.read())
                except ET.ParseError:
                    continue
                if ebook_id is not None and rights:
                    yield ebook_id, rights


def rights_are_eligible(rights: str | None) -> tuple[bool, str]:
    """Accept only an explicit U.S. public-domain assertion."""
    normalized = " ".join((rights or "").casefold().split())
    if normalized == PUBLIC_DOMAIN_RIGHTS.casefold():
        return True, "explicit Project Gutenberg public-domain assertion"
    if "copyright" in normalized or "permission" in normalized:
        return False, "copyright/permission-restricted rights metadata"
    return False, "missing or non-explicit public-domain rights metadata"


def language_is_english(language: str) -> bool:
    values = {part.strip().casefold() for part in re.split(r"[;,]", language) if part.strip()}
    return values == {"en"} or values == {"english"}


def literature_score(metadata: GutenbergMetadata) -> tuple[float, str]:
    """Score literary usefulness from metadata, with hard nonliterary exclusions."""
    if metadata.pg_type.casefold() != "text":
        return -1000.0, "catalog type is not Text"
    if not language_is_english(metadata.language):
        return -1000.0, "language is not exclusively English"
    shelf_text = metadata.bookshelves.casefold()
    for weak_label in _WEAK_SHELF_LABELS:
        shelf_text = shelf_text.replace(weak_label, "")
    haystack = f"{metadata.title}; {metadata.subjects}; {shelf_text}".casefold()
    negative_haystack = f"{haystack}; {metadata.authors.casefold()}"
    negative = sorted(term for term in _NEGATIVE_TERMS if term in negative_haystack)
    positive = sorted(term for term in _POSITIVE_TERMS if term in haystack)
    shelves = sorted(term for term in _POSITIVE_SHELF_TERMS if term in shelf_text)
    locc_values = [value.strip().upper() for value in re.split(r"[;,]", metadata.locc)]
    literary_locc = any(value.startswith(_LITERARY_LOCC_PREFIXES) for value in locc_values)
    literary_subject = bool(
        re.search(
            r"\b(?:fiction|drama|poetry|literature|short stories|fairy tales|legends?)\b",
            metadata.subjects,
            re.IGNORECASE,
        )
    )
    if (
        "category: science -" in shelf_text
        and "travel writing" not in shelf_text
        and not literary_locc
        and not literary_subject
    ):
        return -500.0, "nonliterary science metadata without literary or travel evidence"
    score = min(100.0, len(positive) * 45.0 + len(shelves) * 25.0 + (25.0 if literary_locc else 0))
    if negative:
        return -500.0, "nonliterary metadata: " + ", ".join(negative)
    if score < 25:
        return score, "no positive literary subject, bookshelf, or LoCC evidence"
    evidence = positive + [f"bookshelf:{value}" for value in shelves]
    if literary_locc:
        evidence.append("literary LoCC")
    return score, "; ".join(evidence)


def _manifest_path(data_directory: Path) -> Path:
    return data_directory / "manifest.json"


def _write_manifest(data_directory: Path, payload: dict[str, object]) -> Path:
    path = _manifest_path(data_directory)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def acquire_catalog(
    connection,
    data_directory: Path,
    *,
    refresh: bool = False,
) -> dict[str, int | str]:
    """Acquire and index official CSV/RDF bulk metadata reproducibly."""
    initialize_database(connection)
    catalog_directory = data_directory / "catalog"
    csv_path = catalog_directory / "pg_catalog.csv.gz"
    rdf_path = catalog_directory / "rdf-files.tar.bz2"
    if refresh:
        csv_path.unlink(missing_ok=True)
        rdf_path.unlink(missing_ok=True)
    _download_with_resume(CATALOG_CSV_URL, csv_path)
    _download_with_resume(CATALOG_RDF_URL, rdf_path)
    csv_sha = sha256_file(csv_path)
    rdf_sha = sha256_file(rdf_path)
    combined_sha = hashlib.sha256(f"{csv_sha}:{rdf_sha}".encode()).hexdigest()

    existing = connection.execute(
        "SELECT COUNT(*) FROM gutenberg_books WHERE catalog_sha256 = ?", (combined_sha,)
    ).fetchone()[0]
    manifest = {}
    if _manifest_path(data_directory).exists():
        manifest = json.loads(_manifest_path(data_directory).read_text(encoding="utf-8"))
    if existing and manifest.get("indexer_version") == CATALOG_INDEXER_VERSION:
        eligible = connection.execute(
            "SELECT COUNT(*) FROM gutenberg_books WHERE eligibility_status = 'ELIGIBLE'"
        ).fetchone()[0]
        return {
            "catalog_records": int(existing),
            "eligible_books": int(eligible),
            "catalog_sha256": combined_sha,
        }

    rights = dict(iter_rdf_rights(rdf_path))
    now = _now()
    rows: list[tuple[object, ...]] = []
    eligible = 0
    total = 0
    for metadata in iter_catalog_csv(csv_path):
        total += 1
        metadata = GutenbergMetadata(
            metadata.ebook_id,
            metadata.pg_type,
            metadata.issued,
            metadata.title,
            metadata.language,
            metadata.authors,
            metadata.subjects,
            metadata.locc,
            metadata.bookshelves,
            rights.get(metadata.ebook_id),
        )
        rights_ok, rights_reason = rights_are_eligible(metadata.rights)
        score, literary_reason = literature_score(metadata)
        if not rights_ok:
            status, reason = "INELIGIBLE_RIGHTS", rights_reason
        elif not language_is_english(metadata.language):
            status, reason = "INELIGIBLE_LANGUAGE", "language is not exclusively English"
        elif score < 25:
            status, reason = "INELIGIBLE_NONLITERARY", literary_reason
        else:
            status, reason = "ELIGIBLE", literary_reason
            eligible += 1
        rows.append(
            (
                metadata.ebook_id,
                metadata.pg_type,
                metadata.issued,
                metadata.title,
                metadata.language,
                metadata.authors,
                metadata.subjects,
                metadata.locc,
                metadata.bookshelves,
                metadata.rights,
                metadata.source_url,
                combined_sha,
                status,
                reason,
                score,
                now,
                now,
            )
        )
        if len(rows) >= 2000:
            _upsert_catalog_rows(connection, rows)
            rows.clear()
    if rows:
        _upsert_catalog_rows(connection, rows)
    connection.commit()
    _write_manifest(
        data_directory,
        {
            "acquired_at": now,
            "indexer_version": CATALOG_INDEXER_VERSION,
            "method": "official compressed CSV/RDF bulk feeds plus selective official rsync",
            "catalog_records": total,
            "eligible_books": eligible,
            "feeds": {
                "csv": {
                    "url": CATALOG_CSV_URL,
                    "sha256": csv_sha,
                    "bytes": csv_path.stat().st_size,
                },
                "rdf": {
                    "url": CATALOG_RDF_URL,
                    "sha256": rdf_sha,
                    "bytes": rdf_path.stat().st_size,
                },
            },
            "rsync_mirrors": list(RSYNC_MIRRORS),
        },
    )
    return {"catalog_records": total, "eligible_books": eligible, "catalog_sha256": combined_sha}


def _upsert_catalog_rows(connection, rows: Iterable[tuple[object, ...]]) -> None:
    connection.executemany(
        """
        INSERT INTO gutenberg_books (
            ebook_id, pg_type, issued, title, language, authors, subjects, locc,
            bookshelves, rights, source_url, catalog_sha256, eligibility_status,
            eligibility_reason, literature_score, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ebook_id) DO UPDATE SET
            pg_type = excluded.pg_type, issued = excluded.issued, title = excluded.title,
            language = excluded.language, authors = excluded.authors,
            subjects = excluded.subjects, locc = excluded.locc,
            bookshelves = excluded.bookshelves, rights = excluded.rights,
            source_url = excluded.source_url, catalog_sha256 = excluded.catalog_sha256,
            eligibility_status = excluded.eligibility_status,
            eligibility_reason = excluded.eligibility_reason,
            literature_score = excluded.literature_score, updated_at = excluded.updated_at
        """,
        rows,
    )


def select_books(connection, *, cumulative_limit: int | None) -> list[int]:
    """Select eligible books deterministically, preserving already processed pilot membership."""
    sql = """
        SELECT ebook_id FROM gutenberg_books
        WHERE eligibility_status = 'ELIGIBLE'
        ORDER BY
            CASE processing_status WHEN 'PROCESSED' THEN 0 WHEN 'ACQUIRED' THEN 1 ELSE 2 END,
            literature_score DESC,
            (abs(ebook_id * 1103515245 + 12345) % 2147483647), ebook_id
    """
    parameters: tuple[int, ...] = ()
    if cumulative_limit is not None:
        sql += " LIMIT ?"
        parameters = (cumulative_limit,)
    return [int(row[0]) for row in connection.execute(sql, parameters)]


def acquire_texts(
    connection,
    data_directory: Path,
    ebook_ids: Iterable[int],
    *,
    retries: int = 3,
) -> dict[str, int]:
    """Fetch generated UTF-8 texts with at most one bulk stream per official mirror."""
    ids = sorted(set(ebook_ids))
    books_directory = data_directory / "books"
    books_directory.mkdir(parents=True, exist_ok=True)
    pending = []
    for ebook_id in ids:
        path = books_directory / str(ebook_id) / f"pg{ebook_id}.txt"
        row = connection.execute(
            "SELECT text_cached, text_sha256 FROM gutenberg_books WHERE ebook_id = ?",
            (ebook_id,),
        ).fetchone()
        tracked = bool(row and row["text_cached"] and row["text_sha256"])
        if path.exists() and tracked and sha256_file(path) == row["text_sha256"]:
            continue
        if path.exists() and not tracked:
            # An interrupted legacy --partial transfer may have left a truncated final path.
            path.unlink()
        pending.append(ebook_id)
    if pending:
        connection_count = min(len(RSYNC_MIRRORS), 2, len(pending))
        groups = [pending[index::connection_count] for index in range(connection_count)]

        def transfer(item: tuple[int, list[int]]) -> str:
            index, group = item
            files_path = data_directory / f"rsync-files-{index}.txt"
            temporary = files_path.with_suffix(".txt.tmp")
            temporary.write_text(
                "".join(f"{ebook_id}/pg{ebook_id}.txt\n" for ebook_id in group),
                encoding="utf-8",
            )
            os.replace(temporary, files_path)
            last_error = ""
            for attempt in range(retries):
                mirror = RSYNC_MIRRORS[(index + attempt) % len(RSYNC_MIRRORS)]
                result = subprocess.run(
                    [
                        "rsync",
                        "-rtz",
                        "--partial",
                        "--partial-dir=.rsync-partial",
                        "--ignore-existing",
                        "--timeout=120",
                        f"--files-from={files_path}",
                        mirror,
                        str(books_directory) + "/",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    return ""
                last_error = (result.stderr or result.stdout).strip()
                time.sleep(min(20, 2**attempt))
            return last_error

        with ThreadPoolExecutor(max_workers=connection_count) as executor:
            errors = list(executor.map(transfer, enumerate(groups)))
        if all(errors) and not any(
            (books_directory / str(value) / f"pg{value}.txt").exists() for value in pending
        ):
            raise RuntimeError("Project Gutenberg rsync acquisition failed: " + " | ".join(errors))

    acquired = missing = 0
    now = _now()
    for ebook_id in ids:
        path = books_directory / str(ebook_id) / f"pg{ebook_id}.txt"
        if not path.exists():
            connection.execute(
                """
                UPDATE gutenberg_books SET processing_status = 'MISSING_TEXT',
                    error = 'generated UTF-8 text absent from official rsync mirror', updated_at = ?
                WHERE ebook_id = ?
                """,
                (now, ebook_id),
            )
            missing += 1
            continue
        checksum = sha256_file(path)
        connection.execute(
            """
            UPDATE gutenberg_books SET text_path = ?, text_sha256 = ?, acquisition_timestamp = ?,
                processing_status = CASE WHEN processing_status = 'PROCESSED' THEN 'PROCESSED'
                                         ELSE 'ACQUIRED' END,
                text_cached = 1, byte_count = ?, error = NULL, updated_at = ? WHERE ebook_id = ?
            """,
            (
                path.relative_to(data_directory).as_posix(),
                checksum,
                now,
                path.stat().st_size,
                now,
                ebook_id,
            ),
        )
        acquired += 1
    connection.commit()
    return {"requested": len(ids), "acquired": acquired, "missing": missing}


def prune_processed_texts(connection, data_directory: Path, ebook_ids: Iterable[int]) -> int:
    """Remove only completed raw caches while retaining checksums and restart state."""
    removed = 0
    for ebook_id in sorted(set(ebook_ids)):
        row = connection.execute(
            """
            SELECT text_path, processing_status FROM gutenberg_books WHERE ebook_id = ?
            """,
            (ebook_id,),
        ).fetchone()
        if row is None or row["processing_status"] != "PROCESSED" or not row["text_path"]:
            continue
        path = data_directory / row["text_path"]
        expected = data_directory / "books" / str(ebook_id) / f"pg{ebook_id}.txt"
        if path.resolve() != expected.resolve():
            raise ValueError(f"refusing to prune unexpected Gutenberg path: {path}")
        if path.exists():
            path.unlink()
            removed += 1
        connection.execute(
            "UPDATE gutenberg_books SET text_cached = 0, updated_at = ? WHERE ebook_id = ?",
            (_now(), ebook_id),
        )
        try:
            path.parent.rmdir()
        except OSError:
            pass
    connection.commit()
    return removed


def export_book_manifest(connection, data_directory: Path) -> Path:
    """Write a restart/audit manifest without placing the downloaded texts in Git."""
    path = data_directory / "books_manifest.jsonl"
    temporary = path.with_suffix(".jsonl.tmp")
    fields = (
        "ebook_id",
        "title",
        "authors",
        "language",
        "subjects",
        "bookshelves",
        "rights",
        "source_url",
        "issued",
        "catalog_sha256",
        "text_path",
        "text_sha256",
        "text_cached",
        "acquisition_timestamp",
        "processing_status",
        "processing_timestamp",
        "byte_count",
        "word_count",
        "character_count",
        "eligibility_status",
        "eligibility_reason",
    )
    with temporary.open("w", encoding="utf-8") as handle:
        for row in connection.execute(
            f"SELECT {', '.join(fields)} FROM gutenberg_books "
            "WHERE text_path IS NOT NULL ORDER BY ebook_id"
        ):
            handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
    os.replace(temporary, path)
    return path


def read_text(path: Path) -> str:
    payload = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"could not decode Gutenberg text {path}")
