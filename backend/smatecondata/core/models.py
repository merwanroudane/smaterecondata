"""Canonical data models for SmatEconData.

Every provider observation is normalised into these shapes before it reaches
the dataset builder, so the rest of the application never has to know which
provider a number came from.  See spec section 33.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Frequency(str, Enum):
    """Observation frequency.

    ``UNKNOWN`` is deliberate: some provider catalogues do not declare a
    frequency and guessing one would corrupt provenance.
    """

    ANNUAL = "annual"
    QUARTERLY = "quarterly"
    MONTHLY = "monthly"
    WEEKLY = "weekly"
    DAILY = "daily"
    UNKNOWN = "unknown"

    @property
    def periods_per_year(self) -> int | None:
        return {
            Frequency.ANNUAL: 1,
            Frequency.QUARTERLY: 4,
            Frequency.MONTHLY: 12,
            Frequency.WEEKLY: 52,
            Frequency.DAILY: 365,
        }.get(self)

    @property
    def rank(self) -> int:
        """Coarser frequencies rank lower. Used to detect down/up-sampling."""
        order = [
            Frequency.ANNUAL,
            Frequency.QUARTERLY,
            Frequency.MONTHLY,
            Frequency.WEEKLY,
            Frequency.DAILY,
        ]
        return order.index(self) if self in order else -1

    @classmethod
    def coerce(cls, raw: Any) -> "Frequency":
        """Map assorted provider spellings onto the canonical enum."""
        if isinstance(raw, Frequency):
            return raw
        if raw is None:
            return cls.UNKNOWN
        token = str(raw).strip().lower()
        table = {
            "a": cls.ANNUAL, "annual": cls.ANNUAL, "yearly": cls.ANNUAL,
            "year": cls.ANNUAL, "y": cls.ANNUAL, "annuel": cls.ANNUAL,
            "q": cls.QUARTERLY, "quarterly": cls.QUARTERLY, "quarter": cls.QUARTERLY,
            "trimestriel": cls.QUARTERLY,
            "m": cls.MONTHLY, "monthly": cls.MONTHLY, "month": cls.MONTHLY,
            "mensuel": cls.MONTHLY,
            "w": cls.WEEKLY, "weekly": cls.WEEKLY, "week": cls.WEEKLY,
            "d": cls.DAILY, "daily": cls.DAILY, "day": cls.DAILY,
        }
        return table.get(token, cls.UNKNOWN)


class OutputShape(str, Enum):
    LONG = "long"
    WIDE = "wide"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Language(str, Enum):
    EN = "en"
    FR = "fr"
    AR = "ar"


# --------------------------------------------------------------------------
# Catalogue / metadata
# --------------------------------------------------------------------------


class IndicatorMetadata(BaseModel):
    """Canonical provider-series metadata.

    Fields mirror what providers actually publish. Localised aliases are a
    *discovery aid* and never overwrite the official title (spec 0B.1).
    """

    model_config = ConfigDict(extra="allow")

    provider: str
    series_id: str
    title: str
    description: str | None = None
    unit: str | None = None
    frequency: Frequency = Frequency.UNKNOWN
    topic: str | None = None
    source_name: str | None = None
    source_reference: str | None = None
    coverage_start: str | None = None
    coverage_end: str | None = None
    last_updated: str | None = None
    geographies: list[str] = Field(default_factory=list)
    seasonal_adjustment: str | None = None
    concept: str | None = None
    aliases: dict[str, list[str]] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.series_id}"

    def searchable_text(self) -> str:
        parts = [
            self.title,
            self.description or "",
            self.series_id,
            self.unit or "",
            self.topic or "",
            self.source_name or "",
        ]
        for values in self.aliases.values():
            parts.extend(values)
        return " ".join(p for p in parts if p)


class Observation(BaseModel):
    """A single normalised data point. ``value`` is None for a real gap."""

    provider: str
    series_id: str
    geography: str
    iso3: str | None = None
    period: str
    value: float | None = None
    unit: str | None = None
    frequency: Frequency = Frequency.UNKNOWN
    status: str | None = None
    retrieved_at: datetime = Field(default_factory=utcnow)


class Coverage(BaseModel):
    series_id: str
    provider: str
    geographies: list[str] = Field(default_factory=list)
    start: str | None = None
    end: str | None = None
    observation_count: int = 0
    missing_count: int = 0

    @property
    def completeness(self) -> float | None:
        total = self.observation_count + self.missing_count
        return None if total == 0 else self.observation_count / total


# --------------------------------------------------------------------------
# Request specification
# --------------------------------------------------------------------------


class IndicatorRequest(BaseModel):
    """One concept the user asked for, before a series has been chosen."""

    concept: str
    alias: str | None = None
    unit_preference: str | None = None
    definition_preference: str | None = None
    provider_preference: str | None = None
    resolved_provider: str | None = None
    resolved_series_id: str | None = None

    @property
    def is_resolved(self) -> bool:
        return bool(self.resolved_provider and self.resolved_series_id)


class DatasetSpec(BaseModel):
    """Strongly-typed description of a data request (spec 6.1)."""

    geographies: list[str] = Field(default_factory=list)
    indicators: list[IndicatorRequest] = Field(default_factory=list)
    start_year: int | None = None
    end_year: int | None = None
    frequency: Frequency = Frequency.ANNUAL
    preferred_sources: list[str] = Field(default_factory=list)
    excluded_sources: list[str] = Field(default_factory=list)
    output_shape: OutputShape = OutputShape.WIDE
    include_descriptive_statistics: bool = True
    include_charts: bool = False
    export_formats: list[str] = Field(default_factory=lambda: ["xlsx"])
    original_query: str | None = None
    query_language: Language | None = None

    @field_validator("geographies")
    @classmethod
    def _upper_iso(cls, v: list[str]) -> list[str]:
        return [g.strip().upper() for g in v if g and g.strip()]

    def period_label(self) -> str:
        if self.start_year and self.end_year:
            return f"{self.start_year}-{self.end_year}"
        if self.start_year:
            return f"{self.start_year}-latest"
        return "latest available"


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------


class ScoreBreakdown(BaseModel):
    """Transparent suitability score (spec 6.5).

    This is a *retrieval suitability* score, not a statistical quality measure.
    """

    MAXIMA: ClassVar[dict[str, float]] = {
        "concept_match": 25.0,
        "frequency_match": 15.0,
        "geography_coverage": 15.0,
        "time_coverage": 15.0,
        "unit_match": 10.0,
        "missingness": 10.0,
        "metadata_quality": 5.0,
        "source_preference": 5.0,
    }
    LABELS: ClassVar[dict[str, str]] = {
        "concept_match": "Concept match",
        "frequency_match": "Frequency match",
        "geography_coverage": "Country coverage",
        "time_coverage": "Time coverage",
        "unit_match": "Unit match",
        "missingness": "Missingness",
        "metadata_quality": "Metadata quality",
        "source_preference": "Source preference",
    }

    concept_match: float = 0.0
    frequency_match: float = 0.0
    geography_coverage: float = 0.0
    time_coverage: float = 0.0
    unit_match: float = 0.0
    missingness: float = 0.0
    metadata_quality: float = 0.0
    source_preference: float = 0.0

    @property
    def total(self) -> float:
        return round(sum(getattr(self, f) for f in self.MAXIMA), 2)

    def explain(self) -> list[str]:
        return [
            f"{self.LABELS[f]}: {getattr(self, f):g}/{m:g}"
            for f, m in self.MAXIMA.items()
        ]


class SearchHit(BaseModel):
    metadata: IndicatorMetadata
    score: float = 0.0
    breakdown: ScoreBreakdown | None = None
    matched_on: list[str] = Field(default_factory=list)

    @property
    def suitability(self) -> float:
        return self.breakdown.total if self.breakdown else self.score


class AmbiguityChoice(BaseModel):
    """One candidate presented when a broad term has several meanings."""

    metadata: IndicatorMetadata
    distinction: str
    score: float = 0.0


class AmbiguityReport(BaseModel):
    concept: str
    is_ambiguous: bool
    reason: str | None = None
    choices: list[AmbiguityChoice] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Validation / quality
# --------------------------------------------------------------------------


class ValidationIssue(BaseModel):
    code: str
    severity: Severity
    message: str
    variable: str | None = None
    geography: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class DataQualityReport(BaseModel):
    status: Literal["clean", "usable_with_warnings", "unusable"] = "clean"
    rows: int = 0
    countries: int = 0
    indicators: int = 0
    requested_period: str | None = None
    coverage_pct: float | None = None
    duplicate_keys: int = 0
    missing_cells: int = 0
    frequency_conflicts: int = 0
    unit_conflicts: int = 0
    issues: list[ValidationIssue] = Field(default_factory=list)

    @property
    def warnings(self) -> int:
        return sum(1 for i in self.issues if i.severity is Severity.WARNING)

    @property
    def errors(self) -> int:
        return sum(1 for i in self.issues if i.severity is Severity.ERROR)


# --------------------------------------------------------------------------
# Transformations / provenance
# --------------------------------------------------------------------------


class TransformationRecord(BaseModel):
    operation: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utcnow)
    input_columns: list[str] = Field(default_factory=list)
    output_columns: list[str] = Field(default_factory=list)
    note: str | None = None
    lossy: bool = False


class ColumnLineage(BaseModel):
    """Proof-carrying provenance for one exported column (spec 14)."""

    column: str
    provider: str
    series_id: str
    series_title: str
    definition: str | None = None
    unit: str | None = None
    frequency: Frequency = Frequency.UNKNOWN
    coverage_start: str | None = None
    coverage_end: str | None = None
    source_reference: str | None = None
    retrieved_at: datetime = Field(default_factory=utcnow)
    requested_geographies: list[str] = Field(default_factory=list)
    requested_period: str | None = None
    transformations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    citation: str | None = None


class DatasetArtifact(BaseModel):
    dataset_id: str
    version: int = 1
    name: str
    dataset_spec: DatasetSpec
    variables: list[IndicatorMetadata] = Field(default_factory=list)
    lineage: list[ColumnLineage] = Field(default_factory=list)
    row_count: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    retrieval_timestamp: datetime = Field(default_factory=utcnow)
    validation_summary: dict[str, Any] = Field(default_factory=dict)
    transformations: list[TransformationRecord] = Field(default_factory=list)
    recipe_hash: str | None = None


class ProviderResult(BaseModel):
    """Structured provider outcome, including partial success (spec 36)."""

    provider: str
    status: Literal["success", "partial_success", "failed"] = "success"
    requested_geographies: int = 0
    successful_geographies: int = 0
    failed_geographies: list[str] = Field(default_factory=list)
    message: str | None = None
