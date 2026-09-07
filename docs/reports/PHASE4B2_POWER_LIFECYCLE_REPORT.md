# Phase 4B.2 — Power Lifecycle and 24×7 Readiness

Date: 2026-09-06

Device: Kindle Paperwhite 4 / 10th Generation, firmware 5.17.1.0.3

Renderer endpoint: `27a0a5ca5f8b5b159283c97f007fd3907dda0139`

Status: **power modes measured; controlled reboot startup proved; indefinite service not enabled**

This report contains no device serial, account identifier, password, key, or network credential.
The frozen renderer, corpus semantics, literary source data, and frontlight policy were not changed.

## 1. Correctness fixes

Runtime state remains bounded to approximately 25 hours of `H` rows, but refresh cadence no longer
depends on that rolling row count. State format 2 carries a monotonic `DISPLAY_COUNT` that is
incremented atomically only after FBInk reports physical-display success. It survives pruning,
restarts, date changes, and release activation. GC16 cadence uses the successful-display count.

The selector's signal traps now remove the singleton lock and exit nonzero for HUP, INT, and TERM.
A regression test interrupts the runtime before display, verifies that no history or display count
is committed, confirms the lock disappears, and proves that the next invocation can acquire it.

An additional physical finding explained previously erratic displays at 04:30 and 04:35: BusyBox
`awk` interpreted zero-padded minute keys such as `0270` as octal. Fixed-width keys are now compared
as prefixed strings. The regression test proves that 04:30 selects key 0270 rather than decimal 184
(03:04). No corpus record or time semantics changed.

Power-study cleanup no longer sends signals blindly to PIDs saved before a reboot. A PID must still
exist and its current command line must match the expected process. The production stop path resumes
the current `awesome` and `cvm` processes by name, avoiding PID-reuse ambiguity entirely.

Two physical-test findings were also fixed. First, the exact service originally expected KindleCron
under `runtime/tools`, while this device's verified installation is `runtime/bin/kron`; the service
now supports both locations and fails before UI mutation if neither exists. Second, a scheduled stop
that stopped KindleCron too early could be terminated by its own daemon before removing session
state. Durable status/session cleanup now completes before daemon shutdown. Resuming the framework
alone leaves the last e-ink frame visible, so teardown also explicitly requests the modern
`KPP_HOME` view through `com.lab126.appmgrd`, with the legacy Home URI as a fallback.

## 2. Versioned release and integrity model

Runtime code and immutable assets now share one release directory:

```text
/mnt/us/literary-clock/runtime/releases/<version>/
├── bundle/
├── bin/
├── boot/
├── release.meta
└── checksums.sha256
```

One-line `current-release` and `previous-release` pointers activate or roll back code and data
together. Deployment copies to a staging directory, writes a complete inventory, validates it, and
renames the complete release before changing the pointer. Interrupted staging leaves the active
release untouched. The deployed full release has 9,669 checksummed files. Corruption tests cover a
runtime script, an index TSV, and a PNG; every case refuses activation/validation.

Startup performs the expensive SHA-256 inventory check once before touching the framework. The
minute task performs only cheap existence, size, and PNG-signature checks. Missing release pointers,
incompatible metadata, missing scheduler/FBInk, corrupt assets, or a failed first display leave or
restore the stock UI rather than entering a retry loop.

During a forced-reboot experiment, FAT repair renamed the old rollback subset to `FSCK0000.REN`.
Its metadata and all 4,429 checksums validated, so it was restored to its declared name. After the
new full release activated, that obsolete test subset was removed; the previous complete full-day
release is now the rollback target.

## 3. Fullscreen and suspend experiments

All tests used Kindle-local time, Airplane Mode (`wirelessEnable=0`), the existing FBInk 1.25.0
path, unchanged frontlight level 18, and no Mac after launch.

| Strategy | Cadence | Fullscreen | Suspend / wake result |
|---|---:|---|---|
| Framework running, `preventScreenSaver=0` | 5 min | Fail: Home/status chrome returned | Entered `mem`; one RTC wake succeeded |
| `awesome` + `cvm` paused, prevent=0 | 5 min, short | Pass | One suspend/RTC-wake cycle succeeded |
| Same | 3 min | Pass | One RTC wake succeeded; later wake was displaced by the old cleanup timer |
| Same | 2 min | Pass | Entered suspend after a boundary, missed 12:20, woke late, then displayed current 12:21 without replay |
| Same | 1 min, keepawake off | Pass | Entered suspend and did not RTC-wake; USB woke it eight minutes later |
| Same | 1 min, keepawake threshold | Pass | No suspend; every minute remained current |
| Same | 5 min, two-hour target | Pass | **Fail:** two suspend attempts produced late/missing wakes; screen stuck at 16:31 until USB wake |

Pausing `awesome` and `cvm` is therefore the least invasive tested fullscreen state: it removes the
system clock/status chrome, preserves the literary framebuffer, remains reversible, and can allow
powerd to suspend. The obstacle is not fullscreen persistence; it is unreliable RTC rearming/wake
on this exact PW4/firmware during repeated deep-sleep cycles.

The current [KindleCron documentation](https://github.com/lennardollesch/KindleCron) describes
`readyToSuspend`-time RTC programming and warns that jobs more frequent than roughly three minutes
prevent suspend. That warning matches the one-minute measurements. However, the long five-minute
experiment falsified the stronger hypothesis that a nominally suspend-friendly cadence is reliable
on this device: the log contains repeated `rtc arm FAILED` events and a 28-minute missed interval.

## 4. Cadence and battery measurements

### Exact one-minute mode

The clean two-hour run lasted 7,223 seconds and completed 121 displays with zero job errors:

- battery: 100% → 97%, a bounded coarse estimate of approximately **1.50 percentage points/hour**;
- Wi-Fi: 0 before and after;
- frontlight: 18 before and after;
- average total transaction: **1,184.0 ms**;
- selector: **489.0 ms** average;
- FBInk display: **640.6 ms** average;
- state commit on `/var/local`: **36.9 ms** average;
- refreshes: 112 GL16 and 9 GC16;
- 109 `readyToSuspend` attempts were intentionally aborted because the next job was imminent;
- no actual suspend occurred.

This measurement projects roughly 2–3 days per full charge if the coarse percentage readings stay
linear. It is a two-hour bounded result, not a battery-life guarantee.

### Eco mode

Two-, three-, and five-minute tests demonstrated that suspend is possible and that every wake reads
fresh wall time rather than replaying missed minutes. They did **not** establish a reliable recurring
cycle. The two-hour five-minute target displayed at 16:18, 16:20, 16:25, and a late 16:31, then
remained stale until USB woke the device at 16:59. Because the intended run did not complete, no
honest eco battery percentage/hour can be calculated.

The runtime keeps cadence configurable at 1, 2, 3, or 5 minutes, but configuration flexibility is
not evidence that all modes are production-ready.

## 5. State storage and write cost

Immutable images/indexes remain on `/mnt/us`. Mutable selector/service state is now configurable and
the production service uses persistent ext3 `/var/local/literary-clock`. In comparable physical
runs, USB-visible state commits were commonly about 40–80 ms; `/var/local` commits were commonly
about 20–50 ms and averaged 36.9 ms in the two-hour exact run. `/var/local` also avoids high-frequency
FAT writes and survived reboot.

The current transaction retains `sync → atomic rename → sync`. Its measured cost is small relative
to the roughly 641 ms FBInk update. The device audit confirmed BusyBox `fsync` is available, but a
file-only flush would not prove persistence of the containing-directory rename on this ext3 build.
The known-safe global sync sequence is therefore retained. No durability trade was made solely for
a theoretical optimization.

## 6. Refresh and ghosting

The persistent successful-display counter drives the current policy:

- GC16 on first display;
- GC16 on date change;
- GC16 every 15 successful displays;
- GL16 otherwise.

The two-hour exact test exercised 112 GL16 and nine GC16 updates. The user reported the physical
screen looked good; no objectionable residual glyphs, date ghosts, or status chrome were reported.
Intervals 10, 30, and 60 were not run for hours because the 15-update result was already clean and
changing the accepted cadence would add flashing/ghosting risk without stronger evidence.

## 7. Production lifecycle and reboot safety

The exact-mode service is a bounded, idempotent wrapper around the proven one-shot runtime. It:

1. resolves the atomic current release;
2. validates full checksums and compatibility before any UI mutation;
3. verifies a sane Kindle-local clock and scheduler availability;
4. records the original `preventScreenSaver` value and boot ID;
5. registers jobs before starting KindleCron;
6. verifies the daemon remains alive;
7. pauses the UI only after those checks pass;
8. displays the current local minute immediately;
9. restores the UI and original power property on any setup/display failure.

The first reboot experiment falsified the assumption that a KUAL-style `onboot` extension would run
on this installation: the PW4 has KMC/KPM shell integration but no active extension loader, and the
clock did not start. That ineffective hook was removed without changing the root filesystem.

The actual installed `/etc/upstart/kmc.conf` starts on `framework_ready` and explicitly executes
`/mnt/us/emergency.sh` when present, matching the
[current KindleModding Hotfix documentation](https://kindlemodding.org/kindle-hacking/hotfix.html).
The replacement controlled-test hook uses that existing KMC path, moves itself to
`runtime/emergency.sh.used` *before* launch, syncs, and then invokes the current-release service
wrapper. It therefore cannot repeat after a failed launch or a later reboot.
The second controlled reboot auto-started the clock, stayed fullscreen without stock status chrome,
and advanced every minute. The service stop fired at its scheduled ten-minute boundary. Internal
teardown was complete, but the passive e-ink clock image remained until Home was pressed; this led
to the explicit KPP Home repaint fix described above. A subsequent bounded three-minute run fired
its stop job on time, and the user confirmed that Home appeared automatically without a button
press. No armed reboot hook or indefinite service is left enabled.

## 8. Failure recovery

Host tests cover:

- persistent display count after more than 25 hours of simulated history;
- signal interruption before display and before history mutation;
- overlapping invocation lock rejection and subsequent recovery;
- missing/corrupt image and nonzero FBInk result;
- orphaned state temporary file;
- current-time jump without replay;
- missing current pointer and incompatible release/runtime metadata;
- runtime/index/PNG checksum corruption;
- staged activation failure and code+asset rollback;
- missing scheduler before framework mutation;
- first-display failure followed by UI/power restoration;
- one-shot hook self-disarming before launch;
- missing scheduler at the device's actual path;
- stop-state cleanup before scheduler self-termination;
- explicit modern KPP Home restoration.

The runtime does not poll, contact a Mac, or access a network. Logs are bounded. The previous frame
is harmlessly retained when a minute transaction fails, and no failed display is recorded as shown.

## 9. Current decision

The evidence rejects **ECO MODE READY**: repeated RTC/deep-sleep operation is unreliable on this
specific device/firmware even at five-minute cadence. Exact one-minute operation is correct,
fullscreen, offline, reboot-launchable, and measured at about 1.5 battery percentage points/hour.

The measured cadence recommendation is **A. EXACT MODE READY**. This means the exact runtime is the
only technically supported mode from this study; it does not mean an indefinite service has been
activated. The reboot mechanism tested here is deliberately one-shot because `/mnt/us/emergency.sh`
is KMC's recovery path, not a general persistent application supervisor. True unattended recovery
across every future reboot therefore remains a production-startup blocker. The device is safe and
stock-operable now, with no boot hook and no scheduler active.

Final local validation: 400 pytest tests passed; Ruff lint and format checks passed; all 1,440
manifest minute pools are nonempty; the production SQLite database reports `integrity_check=ok` and
zero foreign-key errors. Device shell scripts pass `sh -n` (ShellCheck was not installed locally).
