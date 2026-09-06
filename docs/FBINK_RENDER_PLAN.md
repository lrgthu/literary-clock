# FBInk Render Plan and Phase 4B Result

This document originated as the Phase 3 boundary plan. Physical Phase 4 testing has now selected
the bitmap approach and validated it on the target Kindle. The current implementation and bounded
pilot results are documented in
[`reports/PHASE4B_STANDALONE_RUNTIME_REPORT.md`](reports/PHASE4B_STANDALONE_RUNTIME_REPORT.md).

The known target is a Kindle Paperwhite 4 / 10th Generation running firmware 5.17.1.0.3. Its
panel is 1072 × 1448 at 300 ppi; the V1 clock orientation is native landscape, 1448 × 1072. The
renderer already has separate `pw4_portrait` and `pw4_landscape` policies and does not rotate a
portrait bitmap.

## Shared boundary

The bundle builder consumes the same validated `RenderQuote` and layout contract used by
workstation previews. The device runner receives only finished bitmaps and hashed selector
identities; it never reinterprets time text or calculates highlight offsets.

## Option A — render a bitmap, display through FBInk/eips

The host or Kindle runtime uses `PillowRenderer` to create a native-resolution 1-bit PNG, then asks
FBInk (or `eips` where appropriate) to display it full-screen.

Advantages:

- Preserves the exact line wrapping, mixed regular/bold spans, italics, optical positioning, and
  Unicode behavior validated in Phase 3.
- Produces a simple, stable device contract: one complete frame in, one refresh out.
- Makes screenshots and pixel-level regression tests reproducible off-device.
- Keeps FBInk usage independent of its text layout and font-discovery features.

Costs:

- Pillow and the chosen font files must be available to whichever machine renders the frame.
- Rendering consumes more CPU/RAM than printing a few FBInk text calls, although a single
  grayscale page at these resolutions is modest.
- Frames need transient storage. Cache only the current/next frame rather than accumulating every
  quote image.
- `eips` behavior and supported PNG formats vary by Kindle generation and must be verified.

## Option B — compose text directly with FBInk

The runner translates `LayoutResult` lines and spans into positioned FBInk text operations.

Advantages:

- Potentially fewer runtime dependencies and no intermediate image file.
- May integrate naturally with FBInk refresh controls and device-native font facilities.
- Could reduce frame-generation latency on devices where PNG decoding is unusually slow.

Costs:

- Mixed weight spans on a single line, kerning, italic attribution, and exact width agreement can
  diverge from the validated Pillow metrics.
- Recreating highlight-aware wrapping on-device risks maintaining two layout engines.
- Available fonts, shaping, Unicode coverage, and FBInk API features depend on the actual model and
  installed FBInk version.
- More positioned draw calls can create alignment artifacts and complicate partial/full refresh
  behavior.

## Implemented decision

Option A is implemented: the Mac builds deduplicated 1-bit quote assets and reusable date overlays,
and a tiny Kindle shell runtime displays them through FBInk. The projected full deployment remains
comfortably below available storage. Option B has no measured advantage that justifies a second
layout engine.

The anti-repeat state remains global and lives with selection, not image files. A normal tick is:

1. capture one Kindle-local timestamp for date and minute;
2. select from the manifest without committing state;
3. validate the quote and date assets;
4. display both through FBInk with one refresh;
5. only after success, atomically commit shuffle/global history;
6. return control to the scheduler/power lifecycle.

## Information still required before 24/7 activation

- A fullscreen framework state that permits powerd suspend on this firmware.
- Measured battery cost of one-minute versus RTC-friendly three-minute-or-longer cadence.
- Multi-hour GL16 ghosting evidence and the final periodic GC16 cadence.
- Reboot/startup recovery behavior with missing or partially deployed assets.
- Explicit authorization to enable a persistent scheduler and startup hook.

No persistent deployment route should be selected from model names alone; validate these facts on
the actual device before activation.
