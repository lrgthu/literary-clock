# Phase 4C — Hardened Native Runtime Final

Date: 2026-09-07

Branch: `phase4c/native-runtime-scaffold`

Status: **Phase 4C complete; ready for Phase 4D appliance qualification**

## 1. Frozen corpus integration

The branch merged authoritative `origin/main` at
`2f46133d5f9991be27a159b66077d2cd6d9c062f` without changing corpus semantics or the frozen PW4
renderer. The generated production bundle records the English V1 fingerprint:

`1566562d112ace24be0725d6e6664e9372a4e514e071de86efe0b5c5d7c2e845`

The bundle contains 6,666 unique renderer-safe quote assets, 8,164 minute relationships, 2,562
small date overlays, and all 1,440 minute rows. Its minimum renderer-safe pool is two. The staged
release occupies 95,124,367 bytes and contains no font files.

## 2. Native runtime artifact

The production candidate reports `litclock-native 0.1.0-scaffold` and was built with the official
KOReader KindleHF toolchain 2026.08. The stripped artifact is 402,564 bytes with SHA-256:

`7824f051dca19c08915a9fd5444902adc458309765d4137ba0b9c21e1daee9e3`

`file` identifies it as a static ELF32 little-endian ARM EABI5 executable for GNU/Linux 4.1.0. It
uses the hard-float ARMv7-A/Thumb-2 KindleHF target, has no interpreter, and has no shared-library
dependency.

## 3. Final-bundle compatibility evidence

The C parser successfully selected from each of the final bundle's 1,440 minute pools. The scaffold
commit already tied the unchanged selector/state implementation to 30 deterministic and 25 seeded
randomized shell/C differential cases. Those cases cover cooldowns, all relaxation levels, shared
clockface identities, date and release changes, persistent GC16 boundaries, forward/backward time
jumps, history pruning, transaction failure, signal interruption, lock recovery, and state-format-1
migration.

At the user's direction, the expensive full differential and sanitizer suites were not replayed
after the frozen-corpus merge. Their commit-tied evidence remains applicable because this completion
pass did not change selection, clock, state, refresh, or manifest parsing behavior. The only native
C change removed a real-UID `access(X_OK)` precheck from the display-helper launch path after the
actual KPM execution context falsified that check; `exec` remains the authority for executability.
The known intentional malformed-state difference remains: C discards a nonnumeric `H` timestamp
that BusyBox `awk` can preserve.

## 4. Versioned release integration

One release now atomically identifies both data and code. `release.meta` records:

- release format 1, runtime/state format 2, and renderer preset `pw4-v1`;
- `runtime_engine=native`;
- native version, architecture, binary SHA-256, and frozen corpus fingerprint.

The full checksum inventory contains 9,247 entries: the native binary, shell fallback, display and
service helpers, metadata, all TSV indexes, quote PNGs, and date PNGs. A clean host-side staging
reproduction validated every checksum and the complete release before activation. The active
pointer selects `phase4c-english-v1-native`; `phase4b2-runtime-v6` remains the previous known-good
code-plus-assets rollback target.

## 5. Engine and display layering

The stable launcher reads `runtime_engine` from the active release. `native` execs that release's
`litclock-native`; `shell` executes the existing POSIX implementation. An explicit
`LITCLOCK_RUNTIME_ENGINE=shell` override provides recovery without editing metadata or resetting
state. Both engines use state format 2 and invoke the existing checksummed
`literary-clock-display.sh`, which remains the sole FBInk owner.

The first ARM invocation launched without loader or illegal-instruction errors. It selected the
current Kindle-local minute, rendered the quote and date overlay fullscreen, completed FBInk base,
overlay, and GC16 operations, incremented `DISPLAY_COUNT` to 31, and committed one history row only
after display success. Total measured transaction time was 1,800 ms.

The device test also exposed one production-context bug: KPM supplies an execution context in which
the native process's real UID could not satisfy `access(X_OK)` although the helper was executable by
the effective context and shell runtime. Replacing that speculative precheck with a regular-file
check allowed `exec` to decide permission and made the real display transaction succeed.

## 6. Controlled exact-mode run

The existing Phase 4B.2 service was left unchanged: one-minute KindleCron cadence, `keepawake=3m`,
GC16 every 15 successful displays, GL16 otherwise, Wi-Fi-independent operation, and reversible
`awesome`/`cvm` fullscreen ownership. The bounded service completed **36/36 successful native
displays**, from local minute 11:07 through 11:42, with zero scheduler or display failures. The
screen remained fullscreen, advanced every minute, and showed no Kindle status chrome. The
preconfigured 35-minute stop fired on schedule.

The run produced 34 GL16 and two GC16 refreshes. Timing in milliseconds was:

| Stage | Mean | Median | Maximum |
|---|---:|---:|---:|
| state read | 1.0 | 1.0 | 5 |
| manifest lookup | 85.8 | 46.0 | 1,254 |
| selector | 0.1 | 0.0 | 1 |
| FBInk display | 646.2 | 629.0 | 847 |
| state commit | 17.4 | 17.0 | 49 |
| total transaction | 757.9 | 699.5 | 1,928 |

The earlier Phase 4B.2 shell run averaged 1,184.0 ms total, so the bounded native mean was about
36% lower. As expected, FBInk remained the dominant operation. This short run did not take a new
battery percentage sample; it is not used to create a second battery claim.

Phase 4B.2's unchanged power result remains the applicable battery evidence: exact mode completed
121 displays in 7,223 seconds, changed the coarse battery reading from 100% to 97%, and averaged
about 1.5 percentage points/hour. It used no Wi-Fi, left the frontlight unchanged, and remained
awake because the next minute job was always imminent. Phase 4C does not reopen that power policy.

## 7. Transaction and recovery behavior

The native transaction remains select → validate frame/date → invoke FBInk helper → check success
→ atomically commit state. Display failure, missing assets, or a signal before commit cannot add an
`H` row or increment `DISPLAY_COUNT`. A persistent advisory lock prevents overlapping native
transactions and is released by the kernel on exit. State is written to a same-directory temporary
file, flushed and fsynced, renamed, and followed by a directory fsync.

The shell implementation remains installed inside the same release. A physical roundtrip began at
`DISPLAY_COUNT=68`; shell displayed successfully and wrote count 69, then native read that state,
displayed the next minute, and wrote count 70. A forced native display status 9 immediately after
that left count 70 and logged `history_commit=no`. This proves state continuity in both directions
and the physical display-success transaction boundary.

The current release pointer and previous-release pointer support code-plus-assets rollback. The
existing self-disarming KMC hook was armed once, consumed itself before launch, and started the
native exact service after reboot. The rebooted Kindle then completed five scheduled native updates
at 12:01–12:05. The scheduler and auto-stop both executed successfully. The stock Home repaint did
not become visible after that boot-context stop, although the scheduler was stopped and the hook
was already disarmed; an ordinary hook-free reboot is the deterministic recovery. No additional
repaint workaround was introduced in Phase 4C. This bounded UI-repaint race belongs to Phase 4D
appliance qualification, not the native one-shot engine.

## 8. Scope boundary

No corpus, literary semantics, renderer, typography, bundle format, state format, selector policy,
refresh policy, power policy, or scheduler architecture changed. Generated quote frames, Apple
Chancery, the ARM binary, and device logs remain ignored deployment artifacts rather than public
repository content.

Phase 4C is complete. The remaining Phase 4D work is appliance qualification: longer unattended
observation, operational deployment choice, and elimination or formal handling of the boot-context
Home repaint race. It is not another corpus, renderer, native runtime, or power-design phase.
