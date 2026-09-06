#!/usr/bin/env python3
"""Stage/activate or roll back a PW4 runtime bundle over USB."""

from __future__ import annotations

import argparse
from pathlib import Path

from litclock.deploy import deploy_bundle, rollback_bundle

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mount", type=Path, default=Path("/Volumes/Kindle"))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--bundle", type=Path)
    group.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    if args.rollback:
        version = rollback_bundle(args.mount)
        print(f"Rolled back active Kindle bundle to {version}")
    else:
        version = deploy_bundle(args.bundle, args.mount, PROJECT_ROOT)
        print(f"Activated Kindle bundle {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
