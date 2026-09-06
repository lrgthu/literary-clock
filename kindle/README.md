# Kindle-side Phase 4A.2 helpers

These small shell helpers support the finite physical-display test. They do not schedule quote
changes, install a daemon, change the frontlight, or disable suspend through a permanent service.

`literary-clock-power.sh on` saves the current
`com.lab126.powerd preventScreenSaver` value under `/mnt/us/literary-clock/state/` and sets it to
`1`. `literary-clock-power.sh off` restores the saved value (defaulting safely to `0` if no saved
state exists). `status` is read-only.

`literary-clock-static-test.sh` is an official document-scriptlet-compatible finite test. It
temporarily sends `SIGSTOP` to the stock `awesome` and `cvm` UI processes, draws one frame through
FBInk, waits 900 seconds, and restores the UI processes and original screensaver property through
a shell trap. The script must be copied to `/mnt/us/documents/`; the power helper and frame belong
under `/mnt/us/literary-clock/`.

Emergency restoration from a root shell is:

```sh
killall -CONT cvm 2>/dev/null || true
killall -CONT awesome 2>/dev/null || true
/mnt/us/literary-clock/literary-clock-power.sh off
```

A normal reboot also clears process-level `SIGSTOP` state, but should not be necessary.
