# Phase 4B Standalone Kindle Runtime Report

Date: 2026-09-06

Renderer endpoint: `27a0a5ca5f8b5b159283c97f007fd3907dda0139`

Device: Kindle Paperwhite 4 / 10th Generation, firmware 5.17.1.0.3

Status: **bounded standalone pilot passed; 24/7 deployment not enabled**

This report contains no serial number, account identifier, password, private key, or network
credential. Generated literary bitmaps, the local corpus database, the KindleCron binary, and the
Apple Chancery font remain outside Git.

## 1. Runtime host and architectural result

The Kindle is the runtime host and the only clock authority. The successful path is:

```text
one Kindle-local timestamp snapshot
              ↓
calendar date + minute-of-day from that same snapshot
              ↓
minute TSV pool + bounded persistent selector state
              ↓
one pre-rendered quote frame + one tiny date overlay
              ↓
FBInk framebuffer transaction
              ↓
only after success: atomic history commit
```

The Mac is only the build/deployment machine. It pre-renders the typography, assembles a versioned
bundle, and copies it over USB. No Mac process, Mac clock, Mac timezone, network share, web API,
SSH connection, Python interpreter, Pillow installation, corpus database, or Wi-Fi connection is
required for a normal Kindle update.

Pre-rendering was retained because it preserves the physically approved Georgia body, Apple
Chancery accent, picturesque character rhythm, bounded justification, excerpt decisions,
attribution fitting, and exact host font metrics. Reimplementing those decisions with FBInk text
would create a second layout engine. The Kindle therefore performs only cheap lookup, selection,
bitmap display, and state persistence.

## 2. Actual device capability audit

Read-only audit on the connected device established:

- Linux/ARMv7 with BusyBox 1.34.1 and a root POSIX shell.
- User storage at `/mnt/us` is a writable FSP/FAT-facing volume; `/var/local` is persistent ext3;
  the root filesystem is read-only during this work.
- FBInk 1.25.0 is executable at `/mnt/us/libkh/bin/fbink` and reports a PW4 framebuffer of
  1072 × 1448, 8-bit grayscale, rotation 3 / counter-clockwise.
- The current KMC/KPM post-jailbreak infrastructure is present. USB networking and SSH were not
  installed or enabled.
- `com.lab126.powerd` exposes writable `rtcWakeup` and `rtcWakeup2` properties plus
  `preventScreenSaver`.
- Stock `crond` exists, but ordinary cron alone is not an RTC wake mechanism.
- `/mnt/us` has approximately 6.2 GiB free after the official SpiderCat exploit filler was removed
  in accordance with its guide. No documents or user books were removed.

The audit output remains local under ignored generated/device storage and is not published because
it contains low-level machine detail unnecessary for code review.

## 3. Asset and manifest design

The builder audits the frozen `pw4-v1` renderability gate, then creates one crisp 1-bit PNG per
unique display-safe quote. Shared AM/PM eligibility references the same asset rather than copying
it. The transport bitmap is 1072 × 1448 CCW, which FBInk displays as the approved native
1448 × 1072 landscape composition.

The dynamic renderer-owned date is not baked into every quote. The base bitmap's date rectangle is
blanked, and the builder creates reusable Georgia 32 px date overlays at the frozen native location
near `(72, 54)`. The full design has 2,562 possible weekday/month/day overlays, including leap-day
possibilities. At runtime a date key such as `0-09-06` selects `Sun, Sep 6`. This retains visual
equivalence without quote × date asset explosion or Kindle-side font rendering.

The dependency-free manifest comprises:

- `bundle.meta`: version, corpus fingerprint, renderer contract, geometry, time/date design;
- `minutes.tsv`: minute 0000–1439 to ordered quote IDs;
- `quotes.tsv`: quote ID, frame, hashed book/author identity, checksum, bytes, dimensions;
- `dates.tsv`: date key, overlay frame, coordinates, checksum, bytes, dimensions;
- `checksums.sha256`: deployment-time integrity inventory.

With `SOURCE_DATE_EPOCH`, the build timestamp and all stable manifest content are reproducible.
The manifest contains neither literary source text nor host font paths.

## 4. Full-corpus storage estimate

The full frozen-renderer audit found:

- 7,088 unique display-safe quote assets;
- 8,727 quote-minute relationships;
- 1,440 / 1,440 minute pools display-safe;
- 2,562 reusable date overlays;
- corpus fingerprint
  `9876c659c24077f58852ab8ffa94b3e2f478190614298e2b7c0dccfa736ec936`.

A deterministic, evenly distributed 200-frame sample measured:

| Measurement | Bytes |
|---|---:|
| Median quote PNG | 13,369 |
| p90 quote PNG | 17,671 |
| Maximum sampled PNG | 19,389 |
| Projected quote payload | 94,759,472 |
| Projected date overlays | 1,080,035 |
| Projected manifest/indexes | 1,434,384 |
| Conservative 32 KiB/file FAT overhead | 316,407,808 |
| Conservative total | 413,681,699 (about 394.5 MiB) |

The conservative total is well below the roughly 6.2 GiB free on the device. Actual native file
payload is approximately 97 MiB; the high total deliberately assumes one full 32 KiB allocation
unit for every small file.

## 5. Versioned deployment and frame integrity

`scripts/deploy_pw4_bundle.py` validates the host bundle, checks device free space, copies only the
manifest allowlist into `runtime/bundles/.staging-<version>`, validates the copied assets, renames
the directory, then atomically replaces the one-line `current` pointer. `previous` is retained for
rollback. An interrupted USB copy cannot replace the active version.

Validation checks nonempty contained paths, file sizes, PNG decodability, exact declared
dimensions, image mode `1`, and SHA-256. Expensive hashes are a build/deploy concern, not a
per-minute battery cost. The runtime performs cheap existence, size, and PNG-signature validation.

The final pilot bundle `phase4b-pilot-v4` contains 180 consecutive minute pools, 1,073 unique quote
assets, 1,073 relationships, one date overlay, and 14,853,651 bytes of payload. The active and
previous bundle pointers are `phase4b-pilot-v4` and `phase4b-pilot-v3`. Older pilot bundles remain
only as scoped rollback data.

## 6. Kindle-native selection and history

The runtime language is POSIX shell with BusyBox `awk`, `sort`, `cksum`, `sync`, and ordinary file
operations. The compact state is TSV, not SQLite or JSON. Its policy is:

- persistent per-minute shuffle bag;
- prefer no exact quote repeat for 24 hours;
- prefer no book repeat for 12 hours;
- prefer no author repeat for 6 hours;
- progressively relax quote/book/author constraints only when necessary;
- retain one global quote identity across shared clock-face minute pools;
- discard history older than 25 hours and cap the log at 256 KiB.

The shuffled order is reproducible from bundle version, timestamp, minute, quote ID, and BusyBox
`cksum`. A bundle-version field in `LAST` ensures activation of a new asset set forces one display
even when it occurs in the same local minute.

## 7. Single time source, timezone, and DST behavior

Each invocation captures exactly once:

```sh
date '+%s|%Y-%m-%d|%H%M|%w-%m-%d'
```

Epoch, displayed calendar date, minute-of-day, and overlay key therefore cannot straddle midnight.
There is no application timezone setting or timezone database. Every wake derives the current pool
directly; wall-clock jumps do not replay missed minutes.

The controlled physical test changed the Kindle timezone through normal settings from UTC−05:00 to
UTC−04:00. The script observed `2026-09-06|0411|...|-0400`; the next runtime snapshot selected the
04:12 pool (minute 0252) and committed quote 756. The user approved the visible frame, then restored
the real UTC−05:00 setting. No application restart, redeployment, network access, or Literary Clock
configuration was needed.

DST will follow the same system-clock behavior, although no separate DST-transition night was run.

## 8. Display-success transaction boundary

`literary-clock-display.sh` validates both PNGs, performs two FBInk framebuffer writes with refresh
suppressed, and issues one physical refresh. It returns nonzero for a missing/corrupt asset,
unavailable FBInk, failed base write, failed overlay write, or failed refresh.

Only after that helper returns zero does the selector:

1. remove the quote from the current minute's bag;
2. append its history record;
3. write all retained state to `state.<pid>.tmp`;
4. `sync`, rename to `state.tsv`, and `sync` again.

Failure injection on the host covered a missing frame, invalid PNG, forced FBInk nonzero exit,
orphaned/interrupted state temp file, process restart, same-minute bundle switch, shared-pool global
cooldown, and a multi-hour time jump. No failing display produced a false history record, and an
orphaned temp file did not replace the last good state.

## 9. Scheduler/RTC investigation and bounded pilot

The evaluated scheduler is [KindleCron](https://github.com/lennardollesch/KindleCron) v0.2.0, a
single static binary whose current release explicitly includes this PW4/firmware combination. Its
published release-archive SHA-256
`c0601b4c7ece0473b31d335a9ccf0cb87b6126041fa35c76a7d81d0841fd1a59` was verified. The optional
`setup` command was not run, so no root-filesystem symlink, boot hook, KUAL extension, or persistent
installation was made. The pilot invoked the binary from `/mnt/us`.

KindleCron waits for powerd's `readyToSuspend` window, sets `rtcWakeup`, lets the Kindle suspend,
and runs due work after the RTC wakes it. Its own documentation warns that jobs more frequent than
roughly three minutes keep the system from reaching suspend. This makes it useful evidence for RTC
wake capability but leaves a fundamental battery tradeoff for a clock that visibly changes every
minute.

The first pilot displayed 02:43 once but did not advance. Inspection showed that the pilot had
started the daemon before registering its job. KindleCron arms its next timer at daemon startup or
on later timer/power events; with `preventScreenSaver=1`, no event caused the newly added job to be
noticed. The fix registers/replaces the job before daemon startup. A regression test enforces this
ordering.

The corrected 420-second offline pilot then produced eight autonomous successes:

- initial 03:02 display;
- scheduled displays at 03:03, 03:04, 03:05, 03:06, 03:07, 03:08, and 03:09;
- every scheduler job exited zero;
- every successful display committed exactly one history row;
- seven transactions took about 1 second and one measured 0 seconds at shell resolution, giving an
  approximate mean of 0.875 seconds;
- the Mac/USB path was disconnected and no network dependency was used;
- battery read 99% before and after the seven-minute run;
- frontlight remained 18 before and after;
- the user approved the visible result.

The pilot trap removed its job, stopped the daemon, restored `preventScreenSaver=0`, resumed the
Kindle framework, and returned to the home UI. No scheduler is now active.

## 10. Fullscreen, suspend, refresh, and battery boundary

The bounded display path temporarily stops `awesome` and `cvm`, sets `preventScreenSaver=1`, and
uses FBInk in framebuffer/pass-through mode. It reliably removes stock status chrome and keeps the
Literary Clock visible, and it is idempotently reversible. It is **not** accepted as the permanent
framework lifecycle.

On this firmware, `preventScreenSaver=1` left powerd in `active` after the pilot. Consequently:

- fullscreen visibility is proven;
- system suspend/deep sleep during this exact configuration is **not** proven and did not occur;
- the seven-minute battery sample is too short to extrapolate 24-hour consumption;
- reboot startup and recovery were intentionally not installed or tested;
- long-term Wi-Fi is unnecessary, but long-term battery viability remains unmeasured.

The experimental refresh policy is GL16 for ordinary minute transitions and GC16 on first display,
date change, and every 15th successful history entry. The physical consecutive GL16 sequence was
approved with no reported objectionable artifact. A multi-hour ghosting/battery run and the exact
GC16 cadence remain open measurements rather than frozen claims.

## 11. Reboot and failure behavior

The runtime itself is restart-safe: it recovers a stale lock whose PID is gone, ignores orphaned
temporary state, skips the already displayed minute/version, and re-derives current wall time after
any gap. Missing configuration fails nonzero and leaves the previous e-ink frame visible.

There is deliberately no reboot hook yet. The intended reboot lifecycle—wait for a usable system
clock, validate current bundle, establish fullscreen/keep-visible state, display immediately, then
arm RTC work—requires an explicitly authorized long-duration phase. It must also restore the stock
framework automatically if the asset set is unusable.

## 12. Validation status

Host tests cover manifest completeness, shared-asset deduplication, cross-midnight dates,
single-snapshot time, selector cooldowns, display transaction ordering, interrupted writes, time
jumps, deployment integrity/rollback, crisp image validation, shell syntax, and pilot scheduler
ordering. Final validation passed:

- `uv sync`: success;
- `uv run pytest`: **370 passed**;
- `uv run ruff check .`: success;
- `uv run ruff format --check .`: **87 files already formatted**;
- device shell scripts: `sh -n` success (`shellcheck` was not installed locally);
- production SQLite `PRAGMA integrity_check`: `ok`;
- production SQLite `PRAGMA foreign_key_check`: zero rows;
- fresh full bundle audit: 7,088 assets, 8,727 relationships, and 1,440 / 1,440 nonempty
  display-safe minute pools, with the same corpus fingerprint as the original estimate.

The production SQLite corpus and frozen visual renderer were unchanged by Phase 4B.

## 13. Remaining risks and next decision

The bounded standalone runtime hypothesis is supported: the device selected and displayed correct
local-minute assets repeatedly with no Mac or network, persisted state only after successful FBInk,
and followed a timezone jump.

It is **not ready for unattended 24/7 deployment**. The next authorized experiment should isolate
the minimum fullscreen strategy that allows powerd to enter suspend, then compare either:

1. a one-minute visible-update cadence that may necessarily remain awake; or
2. a battery-saving cadence of at least three minutes using KindleCron RTC wake, accepting that the
   displayed literary minute is stale between updates.

That experiment needs hours of battery/power-state logging, reboot recovery, ghosting inspection,
and a reversible startup hook. None of those persistent behaviors are enabled by this phase.
