# Future FBInk Render Plan

Phase 3 stops at deterministic device-independent layout and bitmap generation. No Kindle has
been connected, configured, mounted, jailbroken, or modified.

The known target is a Kindle Paperwhite 4 / 10th Generation running firmware 5.17.1.0.3. Its
panel is 1072 × 1448 at 300 ppi; the V1 clock orientation is native landscape, 1448 × 1072. The
renderer already has separate `pw4_portrait` and `pw4_landscape` policies and does not rotate a
portrait bitmap.

## Shared boundary

The future device runner should consume the same validated `RenderQuote` and `LayoutResult` used
by workstation previews. Quote selection remains a separate operation with the existing SQLite
shuffle/history state. The runner should never reinterpret time text or calculate highlight
offsets.

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

## Recommendation for the first device experiment

Start with Option A: render one native 1448 × 1072 thresholded 1-bit PNG and display it through
FBInk/eips. It offers the smallest experiment that preserves the visual evidence from the
PW4-landscape QA pass.
Compare Option B only after the bitmap path works on the actual device and only if measured CPU,
latency, storage, or dependency constraints justify a second composition path.

The anti-repeat state remains global and lives with selection, not image files. A normal tick is:

1. derive the current local `HH:MM`;
2. select once, persisting its per-minute shuffle bag and global display history;
3. validate/build `RenderQuote`;
4. render one temporary frame for the exact device profile;
5. display it with the minimum correct refresh operation;
6. replace or delete the previous temporary frame.

## Information still required before Phase 4

- Effective full-screen viewport and rotation behavior after any system-reserved chrome.
- Which serif font files and weights exist locally and may legally be used.
- Whether the owner explicitly authorizes any later device access or launcher setup; Phase 3
  assumes none.
- Whether FBInk is installed and its version/build flags, or whether only `eips` is available.
- Confirmed commands for full and partial refresh on that model.
- Available Python/Pillow runtime, CPU/RAM constraints, and writable temporary directory.

No deployment route should be selected from model names alone; verify these facts on the device
only after explicit authorization.
