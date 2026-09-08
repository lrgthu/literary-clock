# Literary Clock V1 Production Deployment

Date: 2026-09-08

Status: **production release deployed and running; production reboot proof declined by operator**

## Source and release

- Production implementation commit: `19291afa4f5bc63293eea21fe57d1223f5847972`
- Completed Phase 4C/4D source merged into `main`: `cc44101700ecd69c53161978e73677679c2d5eff`
- Device release: `literary-clock-v1`
- Renderer preset: `pw4-v1`
- Corpus fingerprint: `1566562d112ace24be0725d6e6664e9372a4e514e071de86efe0b5c5d7c2e845`
- Bundle: 6,666 quote assets, 8,164 relationships, 1,440 minute rows, minimum pool two,
  and 2,562 date overlays
- Native binary: static ARMv7/EABI5 hard-float, 402,564 bytes
- Native SHA-256: `7824f051dca19c08915a9fd5444902adc458309765d4137ba0b9c21e1daee9e3`

The bundle was rebuilt from the frozen semantic-v2 database plus the committed final-recovery
inventory. Its manifest and all 6,666 cached quote frames were revalidated by size and SHA-256;
the deployed release's complete 9,244-file inventory then passed host and Kindle-side checksum
validation. No font file or absolute Mac path is present.

## Device cleanup

The device retains the final `literary-clock-v1` release and exactly one known-good rollback,
`phase4c-english-v1-native`. `current-release` names the former and `previous-release` names the
latter. The Phase 4B v5/v6 releases, Phase 4D telemetry, pilot/power/time-jump artifacts, temporary
frames, legacy global runtime copies, test launchers, and stale scheduler telemetry were moved to
the recoverable local backup:

`~/KindleBackups/literary-clock-v1-production-cleanup-20260908`

Mutable state remains at `/var/local/literary-clock/state/state.tsv`; mature `DISPLAY_COUNT`, `H`,
`BAG`, and `LAST` data were preserved. Qualification logs under `/var/local` were cleared before
the permanent start. The advisory lock inode is retained intentionally. The final runtime log and
KindleCron log are capped at 256 KiB. The unknown `FSCK0000.REN` file was not touched.

The intended steady-state user-storage layout is:

```text
/mnt/us/literary-clock/runtime/
    current-release
    previous-release
    service.conf
    literary-clock-{launch,service-start,service-stop}-current.sh
    bin/kron
    kron/
    releases/literary-clock-v1/
    releases/phase4c-english-v1-native/
/var/local/literary-clock/
    state/state.tsv
    state/.litclock-native.lock
    service/
```

The Library retains the normal Start and Stop/Restore Home controls. The one-time production-start
control could not be removed after launch without stopping the appliance and remounting USB; the
operator explicitly chose to leave the successful continuous run undisturbed.

## Production operation

`literary-clock-v1` records `runtime_engine=native`. The emergency shell implementation remains in
the same checksummed release. A final physical native → shell → native roundtrip advanced
`DISPLAY_COUNT` from 1,590 to 1,593 with three successful framebuffer transactions and continuous
history. The permanent run then completed five consecutive visible minute changes, confirmed by
the operator, with exact one-minute cadence, `keepawake=3m`, GC16 every 15 successful displays,
and `auto_stop_minutes=0`.

Persistent KMC autostart is installed at `/mnt/us/emergency.sh`; its bytes match the active
release's `boot/autostart.sh`. It validates the release before invoking the idempotent service
launcher and remains installed rather than self-disarming. The operator elected not to interrupt
the working appliance for the requested final reboot. Consequently persistent autostart is
installed and test-covered, but this exact production hook has not been observed across a real
reboot. The previously qualified one-shot hook did start the same service successfully after
reboot.

Last exact device observations before the permanent start were `DISPLAY_COUNT=1593` and
6,074,080 KiB free before moving 245,908 KiB of obsolete artifacts off the device. The permanent
run then visibly advanced by at least five displays. The last coarse battery observation remains
72% at the end of Phase 4D; it was not reread because doing so would require interrupting the run.

## Validation and accepted limitation

- Python: 529 passed
- Ruff check and format check: passed
- Native host/unit/ASan/UBSan: passed
- Production release checksum validation: passed on the mounted Kindle
- Final native/shell state compatibility: passed
- Five consecutive permanent-service minute changes: passed
- Auto-stop: absent
- Mac/network runtime dependency: absent
- Production reboot/autostart observation: not performed, by operator choice

The Kindle is left displaying Literary Clock in permanent exact mode. The only unfulfilled strict
deployment acceptance item is the deliberately skipped production reboot proof; no development
change is implied by that operational choice.
