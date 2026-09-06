"""Device-specific display suitability without changing corpus validity."""

from __future__ import annotations

from dataclasses import replace

from litclock.models import Quote
from litclock.render.layout import LayoutEngine, LayoutError
from litclock.render.models import (
    DirtyRecordStatus,
    RenderabilityResult,
    RenderabilityStatus,
    RenderQuote,
    RenderValidationError,
)
from litclock.render.presentation import excerpt_candidates, validate_excerpt_integrity
from litclock.render.profiles import DeviceProfile
from litclock.render.typography import FontSelection


def _with_strategy(quote: RenderQuote, *, compact: bool) -> RenderQuote:
    metadata = dict(quote.typography_metadata)
    metadata["layout"] = "compact" if compact else "normal"
    return replace(quote, typography_metadata=metadata)


def is_renderable_for_device(
    quote: Quote | RenderQuote,
    profile: DeviceProfile,
    font: FontSelection,
) -> RenderabilityResult:
    """Classify and prepare one quote for a concrete device profile."""
    try:
        full = RenderQuote.from_quote(quote) if isinstance(quote, Quote) else quote
    except RenderValidationError as error:
        return RenderabilityResult(RenderabilityStatus.REJECT_LAYOUT, None, str(error))
    if full.dirty_record_status != DirtyRecordStatus.CLEAN:
        return RenderabilityResult(
            RenderabilityStatus.REJECT_DIRTY,
            None,
            full.dirty_record_status.value,
        )
    if not full.display_title and not full.display_author:
        return RenderabilityResult(
            RenderabilityStatus.REJECT_ATTRIBUTION,
            None,
            "title and creator metadata are both empty",
        )

    engine = LayoutEngine(font)
    failures: list[LayoutError] = []
    for compact in (False, True):
        candidate = _with_strategy(full, compact=compact)
        try:
            layout = engine.layout(candidate, profile, compact=compact)
            return RenderabilityResult(
                RenderabilityStatus.DISPLAY_SAFE_FULL,
                candidate,
                compact_layout=compact,
                diagnostics=layout.diagnostics,
            )
        except LayoutError as error:
            failures.append(error)
            if error.code in {"dirty", "attribution"}:
                status = (
                    RenderabilityStatus.REJECT_DIRTY
                    if error.code == "dirty"
                    else RenderabilityStatus.REJECT_ATTRIBUTION
                )
                return RenderabilityResult(status, None, str(error))

    for excerpt in excerpt_candidates(full):
        if validate_excerpt_integrity(excerpt) != DirtyRecordStatus.CLEAN:
            return RenderabilityResult(
                RenderabilityStatus.REJECT_DIRTY,
                None,
                DirtyRecordStatus.EXCERPT_CORRUPTION.value,
            )
        for compact in (False, True):
            candidate = _with_strategy(excerpt, compact=compact)
            try:
                layout = engine.layout(candidate, profile, compact=compact)
                return RenderabilityResult(
                    RenderabilityStatus.DISPLAY_SAFE_EXCERPT,
                    candidate,
                    compact_layout=compact,
                    diagnostics=layout.diagnostics,
                )
            except LayoutError as error:
                failures.append(error)

    failure_codes = {error.code for error in failures}
    status = (
        RenderabilityStatus.REJECT_TOO_LONG
        if failure_codes and failure_codes <= {"too_long"}
        else RenderabilityStatus.REJECT_LAYOUT
    )
    reason = str(failures[-1]) if failures else "no sentence-aligned excerpt could be created"
    return RenderabilityResult(status, None, reason)
