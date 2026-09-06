# Kindle runtime helpers

This directory contains the small shell runtime used by the bounded Phase 4B pilot on a
jailbroken Kindle Paperwhite 4. The production design is deliberately offline: the Mac builds and
deploys immutable bitmaps, while the Kindle owns its local clock, quote selection, display history,
and FBInk calls. No font, corpus database, Python interpreter, network service, or Mac connection is
needed at runtime.

The scripts do **not** install a boot hook or enable an indefinite scheduler. The 24/7 lifecycle is
intentionally deferred until the power/suspend behavior is measured over a longer authorized test.

## Components

- `phase4b-device-audit.sh` records a read-only device capability inventory on user storage. It
  omits the device serial.
- `runtime/literary-clock-runtime.sh` captures one local timestamp, chooses from the matching
  manifest pool, invokes the display helper, and atomically commits history only after success.
- `runtime/literary-clock-display.sh` validates and draws one quote bitmap plus one date overlay.
  It has no selector or history side effects.
- `runtime/literary-clock-pilot.sh` is a finite seven-minute document scriptlet. It registers the
  minute job before starting KindleCron, restores the framework and screensaver setting in a trap,
  and leaves no job or daemon enabled afterward.
- `runtime/literary-clock-time-jump-test.sh` is a finite one-shot test for a user-initiated timezone
  change through normal Kindle settings.
- `literary-clock-power.sh` saves/restores `com.lab126.powerd preventScreenSaver` for bounded
  physical tests.

Runtime state is under `/mnt/us/literary-clock/runtime/state/`. The selector keeps a bounded TSV
history and a per-minute shuffle bag. Exact quote, book, and author preferences are 24, 12, and 6
hours respectively and relax progressively for sparse pools. One canonical quote ID is global even
when shared 12-hour semantics place it in two minute pools.

## Host build and deployment

The bundle builder is fail-closed: it uses the frozen `pw4-v1` renderer contract and requires the
locally installed accent font. It never copies that font into the bundle.

```bash
export LITCLOCK_TIME_FONT='/local/path/to/Apple Chancery.ttf'

# Storage projection from a representative sample.
uv run litclock build-pw4-bundle \
  --output data/generated/pw4-bundle-estimate \
  --estimate-only --sample-size 200

# Full 1,440-minute bundle.
SOURCE_DATE_EPOCH=1788712440 uv run litclock build-pw4-bundle \
  --output data/generated/pw4-v1-001

# Staged, verified USB deployment. This does not enable a scheduler.
uv run python scripts/deploy_pw4_bundle.py \
  --mount /Volumes/Kindle \
  --bundle data/generated/pw4-v1-001
```

Deployment copies to a versioned staging directory, validates the copied indexes, dimensions,
1-bit mode, file sizes, and SHA-256 values, then atomically changes the `current` pointer. The
previous version remains available:

```bash
uv run python scripts/deploy_pw4_bundle.py --mount /Volumes/Kindle --rollback
```

Generated bundles and the local production font stay outside Git. KindleCron is also not bundled;
the pilot used the official standalone `kron` v0.2.0 binary directly from `/mnt/us`, without its
optional root-filesystem setup or any boot hook.

## Time and display transaction

One command supplies epoch, date, minute, and date-overlay key:

```sh
date '+%s|%Y-%m-%d|%H%M|%w-%m-%d'
```

The runtime contains no timezone database. A Kindle timezone, DST, manual-time, or NTP change is
therefore reflected on the next invocation. It does not replay missed minutes.

The success boundary is:

1. select and validate the current frame;
2. draw the base and date overlay with FBInk;
3. issue one framebuffer refresh and check the exit code;
4. only then write a temporary state file, `sync`, rename, and `sync` again.

A missing/corrupt frame or FBInk failure does not consume the quote. Logs are capped at 256 KiB.

## Current limitation and recovery

On the tested PW4 firmware, `preventScreenSaver=1` kept the bounded pilot in the `active` power
state. That preserves fullscreen output but is not a valid proof of deep sleep. KindleCron's
documented `rtcWakeup` path is the leading scheduler candidate, but minute-level scheduling and a
deep-sleep-compatible fullscreen lifecycle remain deliberately disabled pending a longer battery
experiment.

Emergency restoration from a root shell is:

```sh
killall -CONT cvm 2>/dev/null || true
killall -CONT awesome 2>/dev/null || true
/mnt/us/literary-clock/literary-clock-power.sh off
/mnt/us/literary-clock/runtime/bin/kron \
  -dir /mnt/us/literary-clock/runtime/kron remove literary-clock-minute 2>/dev/null || true
/mnt/us/literary-clock/runtime/bin/kron \
  -dir /mnt/us/literary-clock/runtime/kron stop 2>/dev/null || true
```

A normal reboot also clears process-level `SIGSTOP`, but should not be needed.
