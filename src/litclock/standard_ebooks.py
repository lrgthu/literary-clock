"""Standard Ebooks catalog discovery, sparse acquisition, and OPF metadata parsing."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GITHUB_ORG = "standardebooks"
GITHUB_API = f"https://api.github.com/orgs/{GITHUB_ORG}/repos"
BOOK_DESCRIPTION_PREFIX = "Epub source for the Standard Ebooks edition of"
SOURCE_LICENSE = "CC0 contributions; underlying text public domain in the US"
_SAFE_REPOSITORY = re.compile(r"^[a-z0-9][a-z0-9_.-]+$")
_DC = "{http://purl.org/dc/elements/1.1/}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class CatalogRepository:
    name: str
    source_url: str
    clone_url: str
    default_branch: str
    description: str
    github_updated_at: str


@dataclass(frozen=True, slots=True)
class BookMetadata:
    title: str
    author: str
    language: str
    rights: str
    genres: tuple[str, ...]
    metadata_word_count: int | None


@dataclass(frozen=True, slots=True)
class AcquiredBook:
    repository: CatalogRepository
    root: Path
    commit_sha: str
    content_checksum: str
    acquisition_timestamp: str
    metadata: BookMetadata
    text_files: tuple[Path, ...]


def _github_token() -> str | None:
    for variable in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(variable):
            return os.environ[variable]
    if shutil.which("gh"):
        result = subprocess.run(
            ["gh", "auth", "token"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


def _request_json(url: str, *, retries: int = 4) -> tuple[Any, dict[str, str]]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "literary-clock-standard-ebooks-miner/0.2",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                return json.load(response), dict(response.headers.items())
        except (OSError, urllib.error.HTTPError, urllib.error.URLError):
            if attempt + 1 == retries:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable retry loop")


def fetch_catalog(directory: Path, *, refresh: bool = False) -> list[CatalogRepository]:
    """Fetch and cache the organization's complete book-repository index."""
    directory.mkdir(parents=True, exist_ok=True)
    catalog_path = directory / "catalog.json"
    if catalog_path.exists() and not refresh:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
        return [CatalogRepository(**entry) for entry in payload["repositories"]]

    repositories: list[CatalogRepository] = []
    page = 1
    while True:
        payload, _ = _request_json(f"{GITHUB_API}?type=public&per_page=100&page={page}")
        if not isinstance(payload, list):
            raise ValueError("GitHub repositories endpoint did not return a list")
        for item in payload:
            description = item.get("description") or ""
            if (
                not description.startswith(BOOK_DESCRIPTION_PREFIX)
                or item.get("fork")
                or item.get("archived")
            ):
                continue
            repositories.append(
                CatalogRepository(
                    name=item["name"],
                    source_url=item["html_url"],
                    clone_url=item["clone_url"],
                    default_branch=item["default_branch"],
                    description=description,
                    github_updated_at=item["updated_at"],
                )
            )
        if len(payload) < 100:
            break
        page += 1
    repositories.sort(key=lambda item: item.name)
    output = {
        "source": GITHUB_API,
        "fetched_at": _now(),
        "book_repository_count": len(repositories),
        "repositories": [asdict(repository) for repository in repositories],
    }
    temporary = catalog_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, catalog_path)
    return repositories


def select_diverse_repositories(
    catalog: list[CatalogRepository], completed: set[str], limit: int
) -> list[CatalogRepository]:
    """Choose a deterministic, author-diverse sample without a hardcoded title list."""
    remaining = [repository for repository in catalog if repository.name not in completed]
    remaining.sort(key=lambda repository: hashlib.sha256(repository.name.encode()).hexdigest())
    selected: list[CatalogRepository] = []
    deferred: list[CatalogRepository] = []
    authors: set[str] = set()
    for repository in remaining:
        author_slug = repository.name.split("_", 1)[0]
        if author_slug in authors:
            deferred.append(repository)
            continue
        selected.append(repository)
        authors.add(author_slug)
        if len(selected) == limit:
            return selected
    selected.extend(deferred[: max(0, limit - len(selected))])
    return selected


def parse_opf(path: Path) -> BookMetadata:
    root = ET.parse(path).getroot()
    metadata = next((element for element in root if element.tag.endswith("metadata")), None)
    if metadata is None:
        raise ValueError(f"missing OPF metadata in {path}")

    def first_text(tag: str) -> str:
        element = metadata.find(f"{_DC}{tag}")
        return "" if element is None or element.text is None else element.text.strip()

    creators = {
        element.attrib.get("id", ""): (element.text or "").strip()
        for element in metadata.findall(f"{_DC}creator")
    }
    author_ids = {
        element.attrib.get("refines", "").removeprefix("#")
        for element in metadata
        if element.attrib.get("property") == "role" and (element.text or "").strip() == "aut"
    }
    authors = [name for identifier, name in creators.items() if identifier in author_ids and name]
    if not authors:
        authors = [name for name in creators.values() if name]
    genres = tuple(
        (element.text or "").strip()
        for element in metadata
        if element.attrib.get("property") == "schema:genre" and (element.text or "").strip()
    )
    word_count_text = next(
        (
            (element.text or "").strip()
            for element in metadata
            if element.attrib.get("property") == "schema:wordCount"
        ),
        "",
    )
    return BookMetadata(
        title=first_text("title"),
        author=", ".join(authors),
        language=first_text("language"),
        rights=first_text("rights"),
        genres=genres,
        metadata_word_count=int(word_count_text) if word_count_text.isdigit() else None,
    )


def _content_checksum(root: Path, files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _run_git(arguments: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=240,
    )
    return result.stdout.strip()


def _load_acquired(repository: CatalogRepository, target: Path) -> AcquiredBook:
    manifest = json.loads((target / ".source.json").read_text(encoding="utf-8"))
    metadata = BookMetadata(
        title=manifest["title"],
        author=manifest["author"],
        language=manifest["language"],
        rights=manifest["rights"],
        genres=tuple(manifest.get("genres", [])),
        metadata_word_count=manifest.get("metadata_word_count"),
    )
    text_files = tuple(sorted((target / "src" / "epub" / "text").glob("*.xhtml")))
    return AcquiredBook(
        repository=repository,
        root=target,
        commit_sha=manifest["commit_sha"],
        content_checksum=manifest["content_checksum"],
        acquisition_timestamp=manifest["acquisition_timestamp"],
        metadata=metadata,
        text_files=text_files,
    )


def acquire_repository(
    repository: CatalogRepository,
    books_directory: Path,
    *,
    retries: int = 3,
) -> AcquiredBook:
    """Sparse-clone one source at HEAD and retain only text, OPF, and license paths."""
    if not _SAFE_REPOSITORY.fullmatch(repository.name):
        raise ValueError(f"unsafe repository name: {repository.name!r}")
    books_directory.mkdir(parents=True, exist_ok=True)
    target = books_directory / repository.name
    if (target / ".source.json").is_file() and (target / "src/epub/content.opf").is_file():
        return _load_acquired(repository, target)

    for attempt in range(retries):
        partial = books_directory / f".{repository.name}.partial"
        if partial.exists():
            shutil.rmtree(partial)
        if target.exists():
            shutil.rmtree(target)
        try:
            _run_git(
                [
                    "clone",
                    "--quiet",
                    "--depth",
                    "1",
                    "--filter=blob:none",
                    "--sparse",
                    "--branch",
                    repository.default_branch,
                    repository.clone_url,
                    str(partial),
                ]
            )
            _run_git(
                [
                    "sparse-checkout",
                    "set",
                    "--no-cone",
                    "/src/epub/text/*.xhtml",
                    "/src/epub/content.opf",
                    "/LICENSE*",
                ],
                cwd=partial,
            )
            commit = _run_git(["rev-parse", "HEAD"], cwd=partial)
            opf = partial / "src" / "epub" / "content.opf"
            metadata = parse_opf(opf)
            text_files = sorted((partial / "src" / "epub" / "text").glob("*.xhtml"))
            selected_files = [opf, *text_files, *partial.glob("LICENSE*")]
            checksum = _content_checksum(partial, selected_files)
            acquired_at = _now()
            manifest = {
                "repository": repository.name,
                "source_url": repository.source_url,
                "commit_sha": commit,
                "default_branch": repository.default_branch,
                "author": metadata.author,
                "title": metadata.title,
                "language": metadata.language,
                "rights": metadata.rights,
                "source_license": SOURCE_LICENSE,
                "genres": list(metadata.genres),
                "metadata_word_count": metadata.metadata_word_count,
                "acquisition_timestamp": acquired_at,
                "content_checksum": checksum,
                "files": [path.relative_to(partial).as_posix() for path in selected_files],
            }
            (partial / ".source.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            os.replace(partial, target)
            return _load_acquired(repository, target)
        except (OSError, subprocess.SubprocessError, ET.ParseError, ValueError):
            if partial.exists():
                shutil.rmtree(partial)
            if attempt + 1 == retries:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable retry loop")
