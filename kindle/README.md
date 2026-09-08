# Kindle runtime helpers

This directory contains the small shell runtime used by the bounded Phase 4B pilot on a
jailbroken Kindle Paperwhite 4. The production design is deliberately offline: the Mac builds and
deploys immutable bitmaps, while the Kindle owns its local clock, quote selection, display history,
and FBInk calls. No font, corpus database, Python interpreter, network service, or Mac connection is
needed at runtime.

Deployment does **not** install a boot hook or enable an indefinite scheduler. A reversible,
one-shot KMC `framework_ready` hook exists only for an explicitly controlled reboot test; it is
activated and deactivated by separate host commands and disarms itself before service launch.

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
- `runtime/literary-clock-service-start.sh` and `service-stop.sh` own the exact-mode lifecycle,
  including full startup validation, scheduler start, fullscreen UI pause, and stock-UI recovery.
- `runtime/literary-clock-validate-release.sh` checks the full SHA-256 release inventory at startup,
  never during a minute update.
- `runtime/literary-clock-boot-once.sh` is the self-disarming `/mnt/us/emergency.sh` payload used
  only for one explicitly controlled reboot. It relies on the KMC bridge already installed by the
  jailbreak and does not modify the root filesystem.
- `literary-clock-power.sh` saves/restores `com.lab126.powerd preventScreenSaver` for bounded
  physical tests.

The production service keeps mutable state under persistent ext3
`/var/local/literary-clock/state/`; bounded pilots may override it. The selector keeps a bounded TSV
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

Deployment copies code and assets to one versioned staging directory, validates copied indexes,
dimensions, 1-bit mode, file sizes, and the complete SHA-256 inventory, then atomically changes the
`current-release` pointer. The previous matching code+asset release remains available:

```bash
uv run python scripts/deploy_pw4_bundle.py --mount /Volumes/Kindle --rollback
```

The exact service has one small mutable configuration file. `keepawake=3m` means KindleCron aborts
suspend when the next one-minute job is imminent; it is not a three-minute time-to-live:

```ini
cadence_minutes=1
keepawake=3m
full_refresh_interval=15
auto_stop_minutes=0
```

For a bounded physical or reboot test, set `auto_stop_minutes` to a positive value. Manual service
commands are:

```sh
/mnt/us/literary-clock/runtime/literary-clock-service-start-current.sh
/mnt/us/literary-clock/runtime/literary-clock-service-stop-current.sh
```

The one-shot user-storage reboot-test hook is deliberately separate from deployment:

```bash
uv run python scripts/deploy_pw4_bundle.py --mount /Volumes/Kindle --enable-boot-hook
uv run python scripts/deploy_pw4_bundle.py --mount /Volumes/Kindle --disable-boot-hook
```

Enabling stages the active release's checksummed hook as `/mnt/us/emergency.sh`. The installed KMC
bridge runs that path at `framework_ready`; the hook first moves itself to
`runtime/emergency.sh.used`, then starts the service. A failed launch therefore cannot repeat on the
next boot. Disabling moves an armed hook out of the KMC path. Neither operation alters the root
filesystem. This is a bounded reboot-test mechanism, not persistent production startup.

The final appliance release is packaged with `--production-release`, which omits pilot,
time-jump, power-study, and qualification helpers. After activation, persistent KMC autostart is
managed explicitly:

```bash
uv run python scripts/deploy_pw4_bundle.py \
  --mount /Volumes/Kindle \
  --bundle data/generated/literary-clock-v1 \
  --release-version literary-clock-v1 \
  --production-release
uv run python scripts/deploy_pw4_bundle.py \
  --mount /Volumes/Kindle --enable-production-autostart
```

The production hook remains at `/mnt/us/emergency.sh` across boots, bounds its boot log, and calls
the same idempotent service launcher used by the Library control. Disable it with
`--disable-production-autostart`; no root filesystem file is modified.

The start path validates the full release and scheduler before pausing `awesome`/`cvm`. The stop
path resumes both processes, restores the original `preventScreenSaver` value, and explicitly asks
`appmgrd` to repaint the modern KPP Home view; a legacy Home URI is retained as fallback.

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

## Measured power modes and recovery

On the tested PW4, a two-hour exact-mode run completed 121 displays without a missed minute, used
no Wi-Fi, and changed the coarse battery reading from 100% to 97% (about 1.5 percentage points/hour
over that bounded interval). The device remained awake: KindleCron aborted suspend because the next
job was always imminent.

Deep sleep is possible with the fullscreen frame visible, but repeated RTC wake was not reliable.
A five-minute two-hour target stalled after a late wake and remained stale until USB wake. Eco mode
is therefore not ready on this device/firmware. See the
[Phase 4B.2 report](../docs/reports/PHASE4B2_POWER_LIFECYCLE_REPORT.md) for the cadence matrix and
limitations.

Emergency restoration from a root shell is:

```sh
/mnt/us/literary-clock/runtime/literary-clock-service-stop-current.sh
```

If the active release itself is unavailable, resume the stock UI by name and restore the normal
screensaver setting:

```sh
killall -CONT cvm 2>/dev/null || true
killall -CONT awesome 2>/dev/null || true
lipc-set-prop com.lab126.powerd preventScreenSaver 0
```

A reboot also clears process-level `SIGSTOP`; the boot hook remains disabled unless explicitly
activated from the host.
