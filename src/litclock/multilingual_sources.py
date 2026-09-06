"""Pinned multilingual source manifests, acquisition, and source adapters."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from litclock.gutenberg_text import GutenbergParagraph, extract_gutenberg_paragraphs
from litclock.wikisource_text import clean_wikitext

USER_AGENT = "literary-clock/0.1 (multilingual public-domain corpus research)"


@dataclass(frozen=True, slots=True)
class MultilingualSource:
    source_project: str
    source_id: str
    language: str
    script_variant: str | None
    title: str
    author: str
    translator_editor: str | None
    source_url: str
    download_url: str
    source_license: str
    rights_evidence: str
    publication_metadata: str
    expected_sha256: str
    filename: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_source_manifest(path: Path) -> list[MultilingualSource]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    sources = [MultilingualSource(**row) for row in payload["sources"]]
    for source in sources:
        if source.language not in {"fr", "zh"}:
            raise ValueError(f"unsupported manifest language: {source.language}")
        if source.source_project not in {
            "Project Gutenberg",
            "French Wikisource",
            "Chinese Wikisource",
        }:
            raise ValueError(f"unsupported source project: {source.source_project}")
        if len(source.expected_sha256) != 64:
            raise ValueError(f"source {source.source_id} is not SHA-256 pinned")
    return sources


def _download_with_resume(url: str, destination: Path, *, retries: int = 5) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(retries):
        offset = partial.stat().st_size if partial.exists() else 0
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        if offset:
            request.add_header("Range", f"bytes={offset}-")
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                append = bool(offset and getattr(response, "status", None) == 206)
                with partial.open("ab" if append else "wb") as handle:
                    shutil.copyfileobj(response, handle, length=1024 * 1024)
            os.replace(partial, destination)
            return destination
        except (OSError, urllib.error.URLError):
            if attempt + 1 >= retries:
                raise
            time.sleep(min(20, 2**attempt))
    raise AssertionError("unreachable")


def acquire_sources(
    sources: list[MultilingualSource],
    data_directory: Path,
    *,
    language: str,
    force: bool = False,
) -> dict[str, object]:
    """Acquire selected pinned inputs and record local timestamps/checksums."""
    acquired: list[dict[str, object]] = []
    for source in sources:
        if source.language != language:
            continue
        destination = data_directory / language / source.filename
        if destination.exists():
            actual = sha256_file(destination)
            if actual != source.expected_sha256:
                if not force:
                    raise ValueError(
                        f"checksum mismatch for {destination}; use --force to reacquire"
                    )
                destination.unlink()
            else:
                acquired.append(
                    {
                        "source_id": source.source_id,
                        "path": str(destination),
                        "sha256": actual,
                        "acquired_at": datetime.fromtimestamp(
                            destination.stat().st_mtime, UTC
                        ).isoformat(),
                    }
                )
                continue
        _download_with_resume(source.download_url, destination)
        actual = sha256_file(destination)
        if actual != source.expected_sha256:
            destination.unlink(missing_ok=True)
            raise ValueError(
                f"checksum mismatch for {source.source_id}: "
                f"expected {source.expected_sha256}, got {actual}"
            )
        acquired.append(
            {
                "source_id": source.source_id,
                "path": str(destination),
                "sha256": actual,
                "acquired_at": datetime.now(UTC).isoformat(),
            }
        )
    receipt = {
        "language": language,
        "manifest_sources": len(acquired),
        "sources": acquired,
        "written_at": datetime.now(UTC).isoformat(),
    }
    receipt_path = data_directory / language / "acquisition.json"
    temporary = receipt_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, receipt_path)
    return receipt


class FrenchGutenbergAdapter:
    language = "fr"

    @staticmethod
    def paragraphs(text: str) -> list[GutenbergParagraph]:
        return extract_gutenberg_paragraphs(text)


class ChineseGutenbergAdapter:
    language = "zh"

    @staticmethod
    def paragraphs(text: str) -> list[GutenbergParagraph]:
        return extract_gutenberg_paragraphs(text)


class FrenchWikisourceAdapter:
    language = "fr"

    @staticmethod
    def paragraphs(wikitext: str) -> list[str]:
        return clean_wikitext(wikitext)


class ChineseWikisourceAdapter:
    language = "zh"

    @staticmethod
    def paragraphs(wikitext: str) -> list[str]:
        return clean_wikitext(wikitext)
