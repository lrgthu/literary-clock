# Phase 4C — Native Runtime Scaffold

Date: 2026-09-07

Base: `origin/main` at `8fc8ae42773195a63c6183e2189ccf44d54370bb`

Branch: `phase4c/native-runtime-scaffold`

Status: **host parity scaffold complete; not deployed or activated on Kindle**

## 1. Scope

This branch ports only the high-frequency, one-shot Kindle update transaction from POSIX shell to
C11. It does not change the corpus, renderer, bundle format, selector policy, refresh interval,
power policy, scheduler policy, deployment activation, boot recovery, or Kindle state. The existing
shell runtime remains checked in as the oracle, fallback, and recovery aid.

The implementation consumes any compatible activated bundle. It has no compiled quote count,
relationship count, minute-pool size, or corpus fingerprint. Capacity limits are corruption bounds,
not assumptions about the V1 corpus: 4,096 candidates in one active pool, 4 MiB of input state,
2 MiB of bag strings, 1,440 bag rows, and 2,048 retained history rows. The healthy one-minute
schedule produces approximately 1,500 history rows within the existing 90,000-second window.

## 2. Shell oracle and preserved behavior

`kindle/runtime/literary-clock-runtime.sh` and `literary-clock-display.sh` are authoritative. The C
port preserves:

- one injected or Kindle-local timestamp snapshot for epoch, date, minute, and overlay key;
- POSIX `cksum` over `release_version:epoch:minute_key:quote_id`, numeric ordering, and persistent
  per-minute shuffle bags;
- global quote identity across shared AM/PM pools;
- 24-hour quote, 12-hour book, and 6-hour author cooldowns;
- the same eight relaxation levels, in the same candidate order;
- release-aware same-minute idempotence through `LAST`;
- state-format-1 display-count migration and state-format-2 `DISPLAY_COUNT`;
- 90,000-second history retention and count-independent GC16 cadence;
- GC16 for the first successful display, a date change, or each 15th successful display; GL16
  otherwise;
- quote removal, history, bag, `LAST`, and display-count mutation only after display success.

Malformed nonessential state rows are discarded. One deliberately safer edge differs from BusyBox
`awk`: a nonnumeric malformed `H` timestamp that `awk` may preserve because of string comparison is
not written back by C. Valid state and safely recoverable corrupt-state fixtures are byte-for-byte
equivalent after commit.

## 3. C module architecture

| Module | Responsibility |
|---|---|
| `clock.c` | Capture one epoch and derive all local calendar fields, or validate one injected snapshot. |
| `manifest.c` | Validate release/bundle compatibility and scan the current minute, candidate metadata, and date overlay. |
| `selector.c` | Reproduce POSIX-CRC shuffle order, cooldown checks, and relaxation levels. |
| `state.c` | Read TSV versions 1/2, bound mutable state, apply history, and atomically commit version 2. |
| `refresh.c` | Reproduce persisted successful-display-count refresh policy. |
| `display.c` | Validate PNG signatures and use a fake backend or exec the proven display helper. |
| `process.c` | Advisory single-instance lock, signal flags, and interruptible test delays. |
| `util.c` | Checked parsing, contained paths, bounded CSV parsing, CRC, and small filesystem helpers. |
| `main.c` | Maintain the selection → validation → display → commit transaction boundary. |

There are no threads, networking, JSON, SQLite, plugins, recursion, or third-party runtime
libraries. Allocation is limited to the active pool, bounded manifest line buffer, and bounded bag
strings. Assets and manifests remain immutable.

## 4. Build system and host interface

The plain Makefile supports:

```sh
make host
make test
make sanitize
make armv7
```

The host build uses C11 plus `-Wall -Wextra -Wpedantic -Wconversion -Wshadow
-Wstrict-prototypes`. `make sanitize` builds both the runtime and native unit tests with ASan and
UBSan. No toolchain or sysroot is committed.

The diagnostic interface is deliberately narrow: `--release-root`, `--state`, `--timestamp`,
`--log`, `--display-helper`, `--full-refresh-interval`, `--dry-run`, and fake display status. The
environment variables already used by the shell runtime remain accepted, so the eventual stable
launcher can invoke the C program without a general-purpose CLI layer. `--version` currently emits:

```text
litclock-native 0.1.0-scaffold release-format=1 manifest-format=1 runtime-format=2
```

## 5. Target audit and link strategy

Committed Phase 4B evidence establishes Linux/ARMv7, BusyBox 1.34.1, firmware 5.17.1.0.3, writable
FSP/FAT-facing `/mnt/us`, persistent ext3 `/var/local`, and an executable FBInk 1.25.0 binary at
`/mnt/us/libkh/bin/fbink`. Therefore the existing release `bin` directory on `/mnt/us` is already a
proven executable location, immutable assets can remain there, and mutable state remains under
`/var/local/literary-clock`.

The retained device audit does **not** record the device libc version or dynamic-loader path, and
this scaffold did not access the Kindle to fill that gap. The official
[KOReader toolchain matrix](https://github.com/koreader/koxtoolchain) maps every Kindle on firmware
5.16.3 or newer to `kindlehf`; its reference environment uses
`arm-kindlehf-linux-gnueabihf`, ARMv7-A, Cortex-A9, NEON, hard-float, and Thumb mode. That is a better
target than a generic distribution ARM compiler.

The proof build used the official koxtoolchain `2026.08` `kindlehf.tar.zst` archive (verified
SHA-256 `8cc7dfbd71abd78f9e947d6b2e20670288a4402edc7b07176bca791f7eaf87d0`), GCC 14.4.0, and its
glibc 2.20 sysroot. The default candidate is statically linked:

```text
ELF32 little-endian ARM
EABI5, hard-float ABI
ARMv7-A, Thumb-2, VFPv3/NEON
GNU/Linux minimum ABI note: 4.1.0
interpreter: none
DT_NEEDED: none
```

The current measured sizes and digest are recorded in section 12 after the final source build.

A dynamic comparison is only 26,228 bytes stripped but requests `/lib/ld-linux-armhf.so.3` and
`libc.so.6`. It is plausible with the official KindleHF sysroot, but the exact live loader/libc was
not retained in repository evidence. The roughly 0.4 MiB static binary is trivial beside the image
bundle and removes that uncertainty, so static official-KindleHF glibc is the scaffold
recommendation. A static musl build is technically viable for this POSIX subset, but it is not the
device-specific supported toolchain and offers no meaningful storage benefit here; it was not used.

No ARM binary was deployed or executed on the Kindle.

## 6. FBInk integration decision

Two production paths were evaluated:

1. Fork/exec the existing checksummed display helper. This uses the FBInk 1.25.0 executable already
   proved on the exact device, preserves the three error-reporting stages (base, overlay, refresh),
   and adds no FBInk build dependency to the C core.
2. Link the public FBInk C interface. FBInk supports static and shared library use, but repository
   evidence proves only the installed executable. Direct linking would require pinning headers and
   a library, enlarging the compatibility and licensing surface, and retesting the framebuffer
   transaction without removing a meaningful bottleneck.

The recommendation is **exec the proven shell display helper** for the first production C runtime.
The physical update averaged about 641 ms in Phase 4B.2; the host fork/exec and selector overhead is
negligible relative to that transaction. Direct library integration has no measured need in this
scaffold.

## 7. Manifest compatibility and lookup

The C runtime reads format-1 `release.meta`, `bundle.meta`, `minutes.tsv`, `quotes.tsv`, and
`dates.tsv` directly. It validates compatible release/runtime/manifest versions, a safe release
version, four-digit minutes in 0–1439, bounded canonical numeric quote IDs, contained relative asset
paths, nonempty identity and integrity fields, date coordinates, and relevant asset metadata.

Lookup scans `minutes.tsv`, retains only the active pool, scans `quotes.tsv` for those IDs, and scans
`dates.tsv` for the one overlay. This keeps the format unchanged and avoids loading the corpus. An
early benchmark exposed expensive maximum-capacity allocation for every inactive minute row; the
final parser validates inactive rows without allocation and allocates exactly the active-pool size.
No offset index or bundle-format change was justified.

## 8. State compatibility and transaction

The C runtime consumes existing TSV state without a reset:

```text
VERSION\t1|2
DISPLAY_COUNT\tN
H\tepoch\tdate\tminute\tquote_id\tbook_id\tauthor_id
BAG\tminute\tremaining_ids
LAST\tdate\tminute\tepoch\tquote_id\trelease_version
```

Version 1 migrates exactly by counting history rows once; version 2 reads the independent display
counter. Candidate selection uses quote, book, and author maxima from history. `LAST` prevents a
duplicate same-release display while allowing a new release in the same local minute. Corrupt
unknown and nonessential rows are ignored; an incompatible state version or invalid display count
fails before display.

After display success, C writes a same-directory `state.<pid>.tmp`, flushes stdio, calls `fsync` on
the file, renames it over `state.tsv`, and calls `fsync` on the containing directory. Display
failure or a signal at a pre-display/pre-commit checkpoint leaves the prior state untouched.

Single-instance protection uses a persistent advisory `fcntl` write lock. Kernel lock release on
exit, signal, crash, or reboot means stale lock-file contents never block later work. HUP, INT, and
TERM handlers only set `volatile sig_atomic_t`; child termination and cleanup occur in normal
control flow.

## 9. Differential-test design and results

`tests/test_native_runtime.py` creates synthetic compatible releases and independent clones of the
same starting TSV state. It invokes the shell oracle and host C binary with the same release
version, manifest, state, timestamp, refresh interval, and fake display result, then compares exit
status, selected quote, relaxation level, refresh mode, display result, history-commit result,
`DISPLAY_COUNT`, and the complete resulting TSV state.

The deterministic matrix covers:

1. fresh state;
2. same-minute idempotence;
3. quote cooldown;
4. book cooldown;
5. author cooldown;
6. all eight progressive relaxation levels;
7. shared AM/PM pool and global quote identity;
8. release switch in the same minute;
9. date change;
10. periodic GC16 count;
11. history pruning at the 90,000-second boundary;
12. time jump forward;
13. time jump backward;
14. missing frame;
15. recoverable corrupt state;
16. display failure;
17. signal before display;
18. signal after display and before commit;
19. live overlap rejection and stale-lock recovery;
20. empty active minute row;
21. incompatible release format;
22. incompatible bundle runtime;
23. state-format-1 migration;
24. contained-path rejection.

There are 30 collected deterministic shell/C parity cases, plus 25 seeded randomized pool/history
cases (55 parity cases total), and one native-only traversal rejection. Multi-step cases also compare
state across repeated invocation, release switch, and forward/backward timestamp sequences. All
parity comparisons pass. The only observed edge divergence during development was the malformed
nonnumeric-history preservation described in section 2; it is an intentional safe discard, not a
selector-policy change.

## 10. CI, sanitizers, and static analysis

Portable CI now always runs:

- host C build with aggressive warnings;
- native C unit tests;
- ASan/UBSan build and native unit tests;
- the entire differential matrix using the sanitized binary;
- all pre-existing Python tests separately;
- Ruff lint and format checks.

The ARM cross compiler is intentionally not a CI dependency. The optional `make armv7` path fails
with a precise toolchain message when the official compiler is unavailable.

ASan and UBSan pass the native unit and full differential suites. Apple Clang static analyzer passes
all C translation units with no findings after two development findings (an analyzer-visible parsed
value and an `errno` lifetime) were corrected. `clang-tidy` and `cppcheck` were not locally
installed, so no optional result is claimed.

## 11. Host performance baseline

`kindle/native/tests/benchmark.py` ran 50 consecutive successful fake-display transactions against
the existing full Phase 4B.2 bundle (7,088 quote rows, 1,440 minute rows, and 2,562 date rows) on the
Apple Silicon host. This is a host lookup baseline, not a Kindle performance claim:

| Measurement | Result |
|---|---:|
| one-shot wall time, median | 7.274 ms |
| one-shot wall time, p95 | 9.579 ms |
| state parse, mean | <0.5 ms (logged as 0.000 ms at 1 ms resolution) |
| manifest lookup/validation, mean | 2.940 ms |
| selector, mean | <0.5 ms (logged as 0.000 ms at 1 ms resolution) |
| fsynced state write/rename, mean | 0.420 ms |
| in-process duration, mean | 3.940 ms |

The host Mach-O binary is built with debug information; its final measured size is recorded below.
Actual Kindle timing remains a post-freeze, explicitly controlled device step.

## 12. Build artifacts

Final measurements (updated after the last source change):

- C production source plus public header: **2,253 physical LOC** (2,149 nonblank);
- host ARM64 Mach-O with debug information: **60,144 bytes**;
- ARMv7 static unstripped: **2,890,204 bytes**;
- ARMv7 static stripped: **402,564 bytes**;
- ARMv7 static SHA-256: `b607fa6431ab5f897555ef80d28b288f1c37d0045984b2fe0be3e4da9722d16b`;
- dynamic comparison, stripped: 26,228 bytes, interpreter `/lib/ld-linux-armhf.so.3`, dependency
  `libc.so.6`.

Build outputs remain ignored and are not committed as source. The Makefile plus pinned toolchain
identity and archive digest reproduce the candidate.

## 13. Remaining blockers and exact next step

The scaffold deliberately stops before four production actions:

- the English V1 semantic corpus must be frozen and merged to `main`;
- parity must be rerun against the final compatible full release and representative migrated state;
- the ARM artifact must be added to the release inventory and the high-frequency launcher switched
  under the existing shell-controlled install/recovery lifecycle;
- the candidate must receive a bounded on-device ABI, transaction, and timing validation before
  activation.

After corpus freeze, merge the new `main` into this branch, build the final full bundle, rerun the
host shell/C differential suite against that release, package the static KindleHF binary in the
checksummed release, and perform the controlled device swap with the shell runtime retained as the
fallback. No additional architecture or selector-design phase is required.
