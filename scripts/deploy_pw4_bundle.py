#!/usr/bin/env python3
"""Stage/activate or roll back a PW4 runtime bundle over USB."""

from __future__ import annotations

import argparse
from pathlib import Path

from litclock.deploy import deploy_bundle, rollback_bundle, set_boot_hook

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mount", type=Path, default=Path("/Volumes/Kindle"))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--bundle", type=Path)
    group.add_argument("--rollback", action="store_true")
    group.add_argument(
        "--enable-boot-hook",
        action="store_true",
        help="arm the self-disabling KMC hook for one controlled reboot",
    )
    group.add_argument(
        "--disable-boot-hook",
        action="store_true",
        help="disarm the one-shot KMC reboot hook",
    )
    parser.add_argument(
        "--release-version",
        help="versioned code+asset release name (defaults to the bundle asset-set version)",
    )
    args = parser.parse_args()
    if args.enable_boot_hook:
        result = set_boot_hook(args.mount, enabled=True)
        print(f"Literary Clock boot hook is {result}")
    elif args.disable_boot_hook:
        result = set_boot_hook(args.mount, enabled=False)
        print(f"Literary Clock boot hook is {result}")
    elif args.rollback:
        version = rollback_bundle(args.mount)
        print(f"Rolled back active Kindle bundle to {version}")
    else:
        version = deploy_bundle(
            args.bundle,
            args.mount,
            PROJECT_ROOT,
            release_version=args.release_version,
        )
        print(f"Activated Kindle bundle {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
