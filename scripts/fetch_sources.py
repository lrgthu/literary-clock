#!/usr/bin/env python3
"""Fetch all pinned upstream corpus inputs."""

from pathlib import Path

from litclock.importers.fetch import fetch_sources

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for path in fetch_sources(root):
        print(path.relative_to(root))
