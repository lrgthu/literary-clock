# Literary Clock native runtime scaffold

This subtree ports the one-shot Phase 4B.2 shell transaction to small C11 modules. The shell
runtime remains the behavioral oracle and recovery implementation. Nothing here installs or
activates the native binary on a Kindle.

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
target audit, parity evidence, and exact handoff after corpus freeze.
