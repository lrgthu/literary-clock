"""Reproducibly fetch pinned corpus and provenance files from GitHub."""

from __future__ import annotations

import hashlib
import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Download:
    source_slug: str
    commit: str
    upstream_path: str
    local_name: str
    sha256: str

    @property
    def url(self) -> str:
        owner_repo = {
            "kapoorankush-litclock": "kapoorankush/litclock",
            "zenbuffy-literary-clock": "zenbuffy/LiteraryClock",
            "johsenevoldsen-literature-clock": "JohsEnevoldsen/literature-clock",
        }[self.source_slug]
        return f"https://raw.githubusercontent.com/{owner_repo}/{self.commit}/{self.upstream_path}"


DOWNLOADS = (
    Download(
        "kapoorankush-litclock",
        "2d644ae640e74f0b1823fb14f50e12e7f4c089c8",
        "image-gen/litclock_annotated.csv",
        "litclock_annotated.csv",
        "eaf30e5a037a3901a52ad8e3b54ed488a9e548d6d4bbcdcda18eae50a4b6bec5",
    ),
    Download(
        "kapoorankush-litclock",
        "2d644ae640e74f0b1823fb14f50e12e7f4c089c8",
        "LICENSE",
        "UPSTREAM_LICENSE",
        "92aacbc180a66b4b810894378c7406446d159f7d7c3c8cb7e0163f32bb21bcba",
    ),
    Download(
        "kapoorankush-litclock",
        "2d644ae640e74f0b1823fb14f50e12e7f4c089c8",
        "NOTICE.md",
        "UPSTREAM_NOTICE.md",
        "a249d2d4dc82488cc66ef7bd0d21b3321e60597241b84851f532604beee92db1",
    ),
    Download(
        "zenbuffy-literary-clock",
        "465801e55f1877fe4d02f2090eca5bc87d1480ca",
        "litclock.yaml",
        "litclock.yaml",
        "f5041a82eeed2f6e9741d7c0872fe3752cdb83e344d41636bd9c49a1e02eb6b9",
    ),
    Download(
        "zenbuffy-literary-clock",
        "465801e55f1877fe4d02f2090eca5bc87d1480ca",
        "README.md",
        "UPSTREAM_README.md",
        "a0eaca624cd9f472d895e4dca5b3be00344dc46a18271dcf6bb44afce4f2722d",
    ),
    Download(
        "johsenevoldsen-literature-clock",
        "febdd2821b62e0ff060346a023426f9e2e6456b4",
        "litclock_annotated.csv",
        "litclock_annotated.csv",
        "21a7f457d15984c225852e234c5dc4e7e5a940535c7a68c9521d6256994362d0",
    ),
    Download(
        "johsenevoldsen-literature-clock",
        "febdd2821b62e0ff060346a023426f9e2e6456b4",
        "LICENCE.md",
        "UPSTREAM_LICENCE.md",
        "527cc36b79865544f8508f32b107d0bc38d34ee75dc8f45e01253ea1b5b3e324",
    ),
    Download(
        "johsenevoldsen-literature-clock",
        "febdd2821b62e0ff060346a023426f9e2e6456b4",
        "README.md",
        "UPSTREAM_README.md",
        "f5e3a8d688f4af00ff382cdd2f0ba9cda7eae726db80fa8dcee58ee66d09bc9f",
    ),
)

SOURCE_METADATA = {
    "kapoorankush-litclock": {
        "source_name": "kapoorankush/litclock",
        "source_url": "https://github.com/kapoorankush/litclock",
        "corpus_license": "CC BY-NC-SA 4.0",
        "license_note": "The upstream MIT license applies to software, not the quote database.",
    },
    "zenbuffy-literary-clock": {
        "source_name": "zenbuffy/LiteraryClock",
        "source_url": "https://github.com/zenbuffy/LiteraryClock",
        "corpus_license": "NOASSERTION",
        "license_note": (
            "No explicit root corpus license; upstream credits substantial data derived from "
            "JohsEnevoldsen/literature-clock."
        ),
    },
    "johsenevoldsen-literature-clock": {
        "source_name": "JohsEnevoldsen/literature-clock",
        "source_url": "https://github.com/JohsEnevoldsen/literature-clock",
        "corpus_license": "CC BY-NC-SA 2.5 Generic",
        "license_note": "See the preserved UPSTREAM_LICENCE.md.",
    },
}


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_sources(project_root: Path, *, force: bool = False) -> list[Path]:
    target_root = project_root / "data" / "third_party"
    written: list[Path] = []
    manifests: dict[str, list[dict[str, str]]] = {}
    for download in DOWNLOADS:
        directory = target_root / download.source_slug
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / download.local_name
        if target.exists() and not force:
            data = target.read_bytes()
        else:
            request = urllib.request.Request(
                download.url, headers={"User-Agent": "literary-clock-corpus-fetcher/0.1"}
            )
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                data = response.read()
        actual = _digest(data)
        if actual != download.sha256:
            raise ValueError(
                f"checksum mismatch for {target}: expected {download.sha256}, got {actual}"
            )
        if not target.exists() or force:
            target.write_bytes(data)
            written.append(target)
        manifests.setdefault(download.source_slug, []).append(
            {
                "upstream_url": download.url,
                "upstream_commit": download.commit,
                "upstream_path": download.upstream_path,
                "local_file": download.local_name,
                "sha256": download.sha256,
            }
        )
    for slug, files in manifests.items():
        manifest = target_root / slug / "SOURCE.json"
        payload = {"source_slug": slug, **SOURCE_METADATA[slug], "files": files}
        manifest.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
        written.append(manifest)
    return written
