"""Domain models shared by import, validation, reporting, and selection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class QualityStatus(StrEnum):
    VERIFIED_EXACT = "VERIFIED_EXACT"
    VERIFIED_NORMALIZED = "VERIFIED_NORMALIZED"
    AMBIGUOUS = "AMBIGUOUS"
    TIME_TEXT_NOT_FOUND = "TIME_TEXT_NOT_FOUND"
    INVALID_TIME = "INVALID_TIME"
    MALFORMED = "MALFORMED"


class DuplicateKind(StrEnum):
    CANONICAL = "CANONICAL"
    EXACT = "EXACT"
    TRIVIAL_VARIANT = "TRIVIAL_VARIANT"


class TimeConfidence(StrEnum):
    EXACT_24H = "EXACT_24H"
    EXACT_AM = "EXACT_AM"
    EXACT_PM = "EXACT_PM"
    EXACT_CONTEXTUAL = "EXACT_CONTEXTUAL"
    AMPM_AMBIGUOUS = "AMPM_AMBIGUOUS"
    APPROXIMATE = "APPROXIMATE"
    RANGE = "RANGE"
    INVALID = "INVALID"


class TimeSemantics(StrEnum):
    CLOCKFACE_12H = "CLOCKFACE_12H"
    RESOLVED_24H = "RESOLVED_24H"
    NOON = "NOON"
    MIDNIGHT = "MIDNIGHT"
    APPROXIMATE = "APPROXIMATE"
    RANGE = "RANGE"
    INVALID = "INVALID"


class EligibilityType(StrEnum):
    EXACT_24H = "EXACT_24H"
    EXPLICIT_AM = "EXPLICIT_AM"
    EXPLICIT_PM = "EXPLICIT_PM"
    CONTEXT_RESOLVED_AM = "CONTEXT_RESOLVED_AM"
    CONTEXT_RESOLVED_PM = "CONTEXT_RESOLVED_PM"
    CLOCKFACE_SHARED_AM = "CLOCKFACE_SHARED_AM"
    CLOCKFACE_SHARED_PM = "CLOCKFACE_SHARED_PM"


class CandidateConfidence(StrEnum):
    """Review/import class used by language-specific corpus miners."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    AMBIGUOUS_CLOCKFACE = "AMBIGUOUS_CLOCKFACE"
    REJECT = "REJECT"


@dataclass(frozen=True, slots=True)
class HighlightResult:
    status: QualityStatus
    start: int | None = None
    end: int | None = None


@dataclass(frozen=True, slots=True)
class TimeDetection:
    start: int
    end: int
    text: str
    minute_of_day: int | None
    confidence: TimeConfidence
    parser_rule: str
    ampm_evidence: str | None = None
    rejection_reason: str | None = None
    language: str = "en"
    possible_minutes: tuple[int, ...] = ()
    semantic_type: str = ""
    candidate_confidence: CandidateConfidence | None = None

    @property
    def matched_text(self) -> str:
        """Alias used by the multilingual candidate schema."""
        return self.text


@dataclass(frozen=True, slots=True)
class ExtractedParagraph:
    text: str
    source_file: str
    section: str | None
    paragraph_index: int
    document_offset: int


@dataclass(frozen=True, slots=True)
class QuoteContext:
    quote: str
    quote_start: int
    quote_end: int
    highlight_start: int
    highlight_end: int
    context_score: float
    literary_quality_score: float
    rejection_reason: str | None = None


@dataclass(frozen=True, slots=True)
class SourceSpec:
    name: str
    slug: str
    url: str
    license: str
    commit: str
    corpus_path: Path
    corpus_sha256: str
    format: str
    language: str = "en"
    sfw_style: str | None = None


@dataclass(frozen=True, slots=True)
class RawQuote:
    source_record_id: str | None
    time_24h: str
    time_text: str
    quote: str
    title: str
    author: str
    sfw: bool | None
    raw_payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class MalformedRecord:
    source_record_id: str | None
    error: str
    raw_payload: Any


@dataclass(frozen=True, slots=True)
class Quote:
    id: int
    minute_of_day: int
    time_24h: str
    time_text: str
    quote: str
    title: str
    author: str
    sfw: bool | None
    language: str
    source_name: str
    source_url: str
    source_license: str
    source_record_id: str | None
    quote_hash: str
    normalized_quote_hash: str
    highlight_start: int | None
    highlight_end: int | None
    quality_status: QualityStatus

    @property
    def highlighted_substring(self) -> str | None:
        if self.highlight_start is None or self.highlight_end is None:
            return None
        return self.quote[self.highlight_start : self.highlight_end]


@dataclass(frozen=True, slots=True)
class ImportSummary:
    raw_records: int
    canonical_quotes: int
    exact_duplicates: int
    trivial_variants: int
    malformed_records: int
    invalid_times: int
