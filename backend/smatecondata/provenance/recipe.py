"""Dataset recipes, hashing and versioning (spec 13, 24).

A recipe is the machine-readable definition of a dataset request: enough to
rebuild the same table later, share it with a co-author, or refresh it when new
observations land. It records *what was asked for and which series answered*,
never the observations themselves -- data is re-fetched from the providers so a
refresh genuinely reflects the source.

The recipe hash covers only the request-defining fields. Rebuilding the same
recipe tomorrow yields the same hash even though the retrieval timestamp moved,
which is what makes "did the definition change?" answerable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..core.models import (
    DatasetSpec,
    Frequency,
    IndicatorRequest,
    Language,
    OutputShape,
    utcnow,
)
from ..datasets.builder import Dataset

RECIPE_VERSION = "1.0"


@dataclass
class SeriesEntry:
    """One resolved provider series inside a recipe."""

    alias: str
    provider: str
    series_id: str
    title: str | None = None
    unit: str | None = None
    concept: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in {
            "alias": self.alias,
            "provider": self.provider,
            "series_id": self.series_id,
            "title": self.title,
            "unit": self.unit,
            "concept": self.concept,
        }.items() if v is not None}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SeriesEntry":
        return cls(
            alias=raw["alias"],
            provider=raw["provider"],
            series_id=raw["series_id"],
            title=raw.get("title"),
            unit=raw.get("unit"),
            concept=raw.get("concept"),
        )


@dataclass
class DatasetRecipe:
    """Reproducible definition of a dataset (spec 13)."""

    name: str
    geographies: list[str] = field(default_factory=list)
    series: list[SeriesEntry] = field(default_factory=list)
    start_year: int | None = None
    end_year: int | None = None
    frequency: Frequency = Frequency.ANNUAL
    output_shape: OutputShape = OutputShape.WIDE
    preferred_sources: list[str] = field(default_factory=list)
    transformations: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[str] = field(default_factory=lambda: ["xlsx"])
    request: str | None = None
    request_language: Language | None = None
    validate: bool = True
    created_at: datetime = field(default_factory=utcnow)
    version: int = 1
    recipe_version: str = RECIPE_VERSION

    # -- identity ---------------------------------------------------------

    def defining_fields(self) -> dict[str, Any]:
        """Only what determines *which data* this recipe describes.

        Deliberately excludes ``created_at``, ``version`` and the free-text
        request, so an unchanged definition keeps a stable hash across rebuilds.
        """
        return {
            "geographies": sorted(self.geographies),
            "series": sorted(
                (s.provider, s.series_id, s.alias) for s in self.series
            ),
            "start_year": self.start_year,
            "end_year": self.end_year,
            "frequency": self.frequency.value,
            "output_shape": self.output_shape.value,
            "preferred_sources": sorted(self.preferred_sources),
            "transformations": [
                {"operation": t.get("operation"), "parameters": t.get("parameters")}
                for t in self.transformations
            ],
            "recipe_version": self.recipe_version,
        }

    @property
    def recipe_hash(self) -> str:
        payload = json.dumps(self.defining_fields(), sort_keys=True,
                             default=str, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_version": self.recipe_version,
            "name": self.name,
            "version": self.version,
            "created_at": self.created_at.isoformat(),
            "recipe_hash": self.recipe_hash,
            "request": self.request,
            "request_language": (
                self.request_language.value if self.request_language else None
            ),
            "geographies": list(self.geographies),
            "period": {"start_year": self.start_year, "end_year": self.end_year},
            "frequency": self.frequency.value,
            "output_shape": self.output_shape.value,
            "preferred_sources": list(self.preferred_sources),
            "series": [s.to_dict() for s in self.series],
            "transformations": list(self.transformations),
            "validation": {"run": self.validate},
            "outputs": list(self.outputs),
        }

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), sort_keys=False,
                              allow_unicode=True, default_flow_style=False)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = self.to_json() if path.suffix == ".json" else self.to_yaml()
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    # -- loading -----------------------------------------------------------

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "DatasetRecipe":
        if not isinstance(raw, dict):
            raise ValueError("recipe must be a mapping")
        for required in ("name", "series"):
            if required not in raw:
                raise ValueError(f"recipe is missing required field: {required}")

        declared = str(raw.get("recipe_version", RECIPE_VERSION))
        if declared.split(".")[0] != RECIPE_VERSION.split(".")[0]:
            raise ValueError(
                f"unsupported recipe_version {declared!r}; "
                f"this build understands {RECIPE_VERSION}"
            )

        period = raw.get("period") or {}
        created = raw.get("created_at")
        language = raw.get("request_language")

        return cls(
            name=raw["name"],
            geographies=list(raw.get("geographies") or []),
            series=[SeriesEntry.from_dict(s) for s in raw["series"]],
            start_year=period.get("start_year"),
            end_year=period.get("end_year"),
            frequency=Frequency.coerce(raw.get("frequency")),
            output_shape=OutputShape(raw.get("output_shape", "wide")),
            preferred_sources=list(raw.get("preferred_sources") or []),
            transformations=list(raw.get("transformations") or []),
            outputs=list(raw.get("outputs") or ["xlsx"]),
            request=raw.get("request"),
            request_language=Language(language) if language else None,
            validate=bool((raw.get("validation") or {}).get("run", True)),
            created_at=(
                datetime.fromisoformat(created) if isinstance(created, str)
                else utcnow()
            ),
            version=int(raw.get("version", 1)),
            recipe_version=declared,
        )

    @classmethod
    def load(cls, path: str | Path) -> "DatasetRecipe":
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        raw = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
        return cls.from_dict(raw)

    # -- execution ---------------------------------------------------------

    def to_spec(self) -> DatasetSpec:
        """The :class:`DatasetSpec` this recipe re-executes."""
        return DatasetSpec(
            geographies=list(self.geographies),
            indicators=[
                IndicatorRequest(
                    concept=s.concept or s.alias,
                    alias=s.alias,
                    resolved_provider=s.provider,
                    resolved_series_id=s.series_id,
                )
                for s in self.series
            ],
            start_year=self.start_year,
            end_year=self.end_year,
            frequency=self.frequency,
            preferred_sources=list(self.preferred_sources),
            output_shape=self.output_shape,
            export_formats=list(self.outputs),
            original_query=self.request,
            query_language=self.request_language,
        )


def recipe_from_dataset(dataset: Dataset,
                        outputs: list[str] | None = None) -> DatasetRecipe:
    """Derive a recipe from an assembled dataset."""
    spec = dataset.spec
    return DatasetRecipe(
        name=dataset.name,
        geographies=list(spec.geographies),
        series=[
            SeriesEntry(
                alias=v.alias,
                provider=v.metadata.provider,
                series_id=v.metadata.series_id,
                title=v.metadata.title,
                unit=v.metadata.unit,
                concept=v.concept,
            )
            for v in dataset.variables
        ],
        start_year=spec.start_year,
        end_year=spec.end_year,
        frequency=spec.frequency,
        output_shape=spec.output_shape,
        preferred_sources=list(spec.preferred_sources),
        transformations=[
            {"operation": t.operation, "parameters": t.parameters}
            for t in dataset.transformations
            if t.operation != "build_dataset"
        ],
        outputs=outputs or list(spec.export_formats),
        request=spec.original_query,
        request_language=spec.query_language,
    )


# --------------------------------------------------------------------------
# Refresh / version comparison (spec 23, 24)
# --------------------------------------------------------------------------


@dataclass
class ChangeSummary:
    """What changed between two builds of the same recipe."""

    new_observations: int = 0
    updated_observations: int = 0
    removed_observations: int = 0
    metadata_changes: int = 0
    recipe_hash_changed: bool = False
    details: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.new_observations or self.updated_observations
                    or self.removed_observations or self.metadata_changes
                    or self.recipe_hash_changed)

    def as_text(self) -> str:
        return (f"New observations: {self.new_observations}\n"
                f"Updated observations: {self.updated_observations}\n"
                f"Removed observations: {self.removed_observations}\n"
                f"Metadata changes: {self.metadata_changes}")


def _observation_index(dataset: Dataset) -> dict[tuple[str, str, str, str], float | None]:
    index: dict[tuple[str, str, str, str], float | None] = {}
    for o in dataset.observations:
        index[(o.provider, o.series_id, o.iso3 or o.geography, o.period)] = o.value
    return index


def compare_versions(previous: Dataset, current: Dataset,
                     *, tolerance: float = 1e-9) -> ChangeSummary:
    """Diff two builds. The earlier version is never overwritten (spec 23)."""
    before = _observation_index(previous)
    after = _observation_index(current)
    summary = ChangeSummary()

    for key, value in after.items():
        if key not in before:
            summary.new_observations += 1
        else:
            old = before[key]
            if old is None and value is None:
                continue
            if old is None or value is None or abs(old - value) > tolerance:
                summary.updated_observations += 1
                if len(summary.details) < 25:
                    summary.details.append(
                        f"{key[1]} {key[2]} {key[3]}: {old} -> {value}")

    summary.removed_observations = sum(1 for key in before if key not in after)

    before_meta = {v.metadata.key: v.metadata for v in previous.variables}
    for variable in current.variables:
        old = before_meta.get(variable.metadata.key)
        if old is None:
            summary.metadata_changes += 1
            continue
        for attribute in ("title", "unit", "frequency", "coverage_end"):
            if getattr(old, attribute) != getattr(variable.metadata, attribute):
                summary.metadata_changes += 1
                if len(summary.details) < 25:
                    summary.details.append(
                        f"{variable.alias}.{attribute}: "
                        f"{getattr(old, attribute)} -> "
                        f"{getattr(variable.metadata, attribute)}")
                break

    summary.recipe_hash_changed = (
        recipe_from_dataset(previous).recipe_hash
        != recipe_from_dataset(current).recipe_hash
    )
    return summary
