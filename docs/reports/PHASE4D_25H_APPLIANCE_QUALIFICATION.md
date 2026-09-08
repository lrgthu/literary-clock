# Phase 4D — 25-Hour Appliance Qualification

Date: 2026-09-07 to 2026-09-08

Branch: `phase4c/native-runtime-scaffold`

Phase 4C base: `a5ac8e4823ba63ae95305306d16c8a042a311afa`

## 1. Test envelope

The production release `phase4c-english-v1-native` ran continuously from
2026-09-07 12:32:41 to 2026-09-08 13:37:38 Kindle-local time. Exact elapsed service time was
**90,297 seconds (25:04:57)**. The Kindle reported `EST-0500`; the runtime used that Kindle-local
clock directly and contained no application timezone logic.

The release remained on the frozen corpus fingerprint
`1566562d112ace24be0725d6e6664e9372a4e514e071de86efe0b5c5d7c2e845`, with
`runtime_engine=native`. `phase4b2-runtime-v6` remained the previous-release rollback target and
the shell fallback remained in the active release. Wi-Fi was off, frontlight was unchanged, USB was
disconnected after launch, and no Mac process or network service participated in scheduling.

## 2. Pre-test snapshot

| Measurement | Start |
|---|---:|
| Kindle local timestamp | 2026-09-07 12:31:22 EST-0500 |
| Battery | 98% |
| Frontlight | 18 |
| Wi-Fi | off (`0`) |
| Current/previous release | `phase4c-english-v1-native` / `phase4b2-runtime-v6` |
| Runtime engine | native |
| `DISPLAY_COUNT` | 76 |
| State size | 7,781 bytes |
| History rows | 76 |
| Qualification runtime log | 0 bytes |
| Free user storage | 6,078,280 KiB |
| Scheduler | stopped before launch |
| Last state minute / quote | minute 0725 / quote 2400 |
| Boot hook | disarmed |

## 3. Scheduler and display accounting

The service made one immediate display and KindleCron then executed **1,505 scheduled minute jobs**
from 12:33 through 13:37 the following day. All 1,505 jobs exited zero. The scheduled timestamps
are 1,505 unique consecutive local minutes: there are no gaps and no duplicates.

| Result | Count |
|---|---:|
| Expected scheduled minute jobs | 1,505 |
| Successful scheduled minute jobs | 1,505 |
| Immediate startup displays | 1 |
| Total successful displays | 1,506 |
| Failed displays | 0 |
| Native failures | 0 |
| Scheduler failures | 0 |
| Skipped scheduled minutes | 0 |
| Duplicate scheduled minutes | 0 |

`DISPLAY_COUNT` advanced exactly from 76 to 1,582, a delta of 1,506. It did not reset at midnight,
on date change, or when old history was pruned.

## 4. Date rollover

KindleCron records successful jobs at 23:59, 00:00, 00:01, and every following minute. The
00:31 checkpoint records local date 2026-09-08, minute 0031, successful quote 7512, and count 796.
The user physically observed the correct new renderer-owned date and normal fullscreen operation.
At 00:00 the successful display count was 765; this both triggered the date-change rule and landed
on the configured 15-display GC16 boundary. No stale prior-day overlay or scheduling interruption
was observed.

## 5. Refresh accounting

The successful counts during the test were 77 through 1,582 inclusive. The unchanged deterministic
refresh policy therefore produced exactly **100 GC16** updates (each count divisible by 15,
including the midnight count 765) and **1,406 GL16** updates. The retained rotating runtime tail
confirms the same count-driven pattern. History-row count did not participate in refresh selection.
No severe ghosting was reported; periodic GC16 cleared ordinary e-ink residue acceptably.

## 6. History pruning and bounded state

| Measurement | Start | +24h | +25h | Post-run/fallback |
|---|---:|---:|---:|---:|
| `DISPLAY_COUNT` | 76 | 1,516 | 1,576 | 1,584 |
| History rows | 76 | 1,460 | 1,500 | 1,492 |
| State bytes | 7,781 | 144,535 | 146,959 | 146,379 |

At hour 25 the rolling 90,000-second retention window had stabilized at 1,500 rows while the
independent display counter continued monotonically. The two post-run interoperability displays
pruned nine expired rows and added two, leaving 1,492. The captured state has one valid version row,
one display counter, valid bounded history/bag/LAST rows, and no duplicate active execution.

The primary runtime log rotated at its 256 KiB bound and measured 216,913 bytes at the +25h
checkpoint. Free user storage changed from 6,078,280 to 6,077,096 KiB, a bounded 1,184 KiB change
including all qualification telemetry.

The qualification exposed one non-runtime logging issue: `service/status.txt` duplicated
KindleCron stdout and reached 449,027 bytes. KindleCron already writes its capped `kron.log`, so the
redundant service redirection was removed after the run. This changes no scheduler or display
semantics and does not require a 25-hour replay. The checkpoint helper also incorrectly reported
`scheduler_alive=0` by looking for a nonexistent PID file and emitted an extra zero on empty failure
counts; both diagnostics were corrected. The authoritative scheduler log and 1,505 zero-exit jobs
make those telemetry defects non-ambiguous.

## 7. Battery and display observations

Battery changed from 98% to 72% over the 25.006-hour checkpoint interval: a coarse rate of
approximately **1.04 percentage points/hour**. Kindle battery readings are integer estimates, but
this is broadly consistent with—and slightly better than—the earlier 1.5 percentage-points/hour
two-hour observation. Exact mode intentionally remains awake because the next job is always within
the three-minute keep-awake threshold. Wi-Fi remained off and frontlight remained 18.

The user observed continuous fullscreen literary frames, minute-by-minute changes, the correct date,
no Kindle status bar, no Home takeover, no rendering corruption, and no objectionable ghosting.

## 8. Post-25h shell/native interoperability

Using the same mature state, shell displayed minute 0826 and advanced `DISPLAY_COUNT` from 1,582 to
1,583. Native then read the shell-written state, displayed minute 0827, and advanced it to 1,584.
Shell took 2,930 ms; native took 704 ms. Both committed only after FBInk success, and history
remained readable and bounded.

## 9. Reboot, startup, and recovery

The existing self-disarming KMC hook launched the active native release after one controlled reboot.
Release validation passed, the current local minute displayed fullscreen, and five subsequent
one-minute jobs at 13:54–13:58 all exited zero. The five-minute bounded stop removed the scheduler;
the hook was already disarmed before service launch.

The known boot-context stock-Home repaint race recurred: the stopped service left the last 13:58
e-ink frame visible instead of repainting Home. This is an accepted V1 recovery limitation, not a
clock-runtime failure. Service and scheduler teardown completed, and an ordinary hook-free reboot
deterministically restores stock Home. Continuous appliance operation does not exercise this
cosmetic stop path.

Final device state was checked over USB: scheduler absent, boot hook disarmed, native release still
active, previous rollback retained, and only clean manual Start/Stop Library controls present. The
default configuration is exact one-minute mode with indefinite duration, but no service or test
process was left running.

## 10. Repository validation

The qualification adds only small shell instrumentation and this report. It does not change the
corpus, renderer, selector, state format, display helper, scheduler cadence, refresh policy, or
native transaction. The post-qualification logging fix only discards redundant uncapped daemon
stdout; the authoritative capped KindleCron log is unchanged.

Final local validation passed:

- 527 Python tests in 35.74 seconds;
- Ruff lint and format checks across the repository;
- warning-clean native host build;
- native unit tests;
- ASan/UBSan build and native unit tests;
- POSIX shell syntax checks for every qualification helper.

GitHub Actions validates the pushed completion commit independently.

## 11. Accepted limitations and final decision

- Exact mode uses approximately one coarse battery percentage point per hour and intentionally does
  not enter deep sleep.
- Kindle battery percentage is too coarse for precise power measurement.
- The boot-context auto-stop may retain the final e-ink image; a hook-free reboot restores Home.
- Qualification-only scheduler-alive and empty-failure diagnostics were incorrect during the run;
  primary logs and state were complete, and the helper is corrected for future use.

None is a V1 correctness blocker.

**PHASE 4D PASS**

**LITERARY CLOCK V1 COMPLETE**
