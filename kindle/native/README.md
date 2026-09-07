# Literary Clock native runtime

This subtree implements the one-shot Phase 4B.2 transaction in small C11 modules. The shell
runtime remains the behavioral oracle and recovery implementation. A release may select the native
engine with `runtime_engine\tnative` in `release.meta`; the stable launcher dispatches to the
checksummed `bin/litclock-native` from that same release. Setting
`LITCLOCK_RUNTIME_ENGINE=shell` is the explicit state-preserving fallback.

```sh
make host
make test
make sanitize

# Requires the official KOReader KindleHF cross compiler in PATH.
make armv7
```

The host binary accepts injected paths and one timestamp for differential tests:

```sh
build/host/litclock-native \
  --release-root /path/to/release \
  --state /tmp/litclock-state.tsv \
  --timestamp '1788712440|2026-09-06|1234|0-09-06' \
  --fake-display-success
```

Production defaults remain environment-compatible with the existing launcher and service. The
production display backend forks the checksummed `literary-clock-display.sh` helper, which owns the
already-proven FBInk command sequence. `--dry-run` validates and reports a selection without display
or state mutation. `--version` reports the runtime and supported format versions.

See [the Phase 4C scaffold report](../../docs/reports/PHASE4C_NATIVE_RUNTIME_SCAFFOLD.md) for the
design and parity evidence, and the [Phase 4C final report](../../docs/reports/PHASE4C_NATIVE_RUNTIME_FINAL.md)
for the frozen release and physical PW4 cutover evidence.
