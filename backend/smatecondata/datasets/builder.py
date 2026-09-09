"""Dataset assembly (spec 7, 43).

Turns normalised observations from any mix of providers into one coherent
dataset that can be emitted in long or wide shape.

Two rules govern everything here:

* **Never invent an observation.** A gap stays a gap; nothing is interpolated,
  imputed, or carried forward (spec 7, 9).
* **Never silently change frequency or unit.** Conflicts are reported by the
  validator, not quietly reconciled (spec 7.3, 7.4).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Sequence

from ..core.models import (
    ColumnLineage,
    DatasetSpec,
    Frequency,
    IndicatorMetadata,
    Observation,
    OutputShape,
    TransformationRecord,
    utcnow,
)

# Provider codes that carry no meaning for a reader, stripped from aliases.
_ALIAS_NOISE = re.compile(r"[^a-z0-9]+")

# Unit fragments worth keeping in a generated column name.
_UNIT_HINTS: list[tuple[str, str]] = [
    ("constant 2015 us$", "const2015usd"),
    ("constant us$", "constusd"),
    ("current us$", "usd"),
    ("current lcu", "lcu"),
    ("constant lcu", "constlcu"),
    ("ppp", "ppp"),
    ("% of gdp", "pct_gdp"),
    ("percent of gdp", "pct_gdp"),
    ("annual %", "pct"),
    ("%", "pct"),
    # Spelled-out variants: providers are inconsistent, and a unit of
    # "Percent" would otherwise pick up no suffix at all.
    ("percentage", "pct"),
    ("percent", "pct"),
    ("per capita", "pc"),
    ("index", "idx"),
]


def slugify(text: str, *, max_words: int = 6) -> str:
    """Lowercase ASCII slug suitable for a column name."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _ALIAS_NOISE.sub("_", text.lower()).strip("_")
    words = [w for w in text.split("_") if w]
    return "_".join(words[:max_words])


def smart_alias(metadata: IndicatorMetadata,
                concept: str | None = None) -> str:
    """Readable column name that keeps the original code in metadata (spec 43).

    ``NY.GDP.PCAP.CD`` becomes ``gdp_per_capita_usd`` rather than being
    exported as an opaque provider code.
    """
    base = slugify(concept) if concept else slugify(metadata.title, max_words=5)
    if not base:
        base = slugify(metadata.series_id, max_words=4) or "series"

    unit = (metadata.unit or "").lower()
    suffix = ""
    for needle, token in _UNIT_HINTS:
        if needle in unit:
            suffix = token
            break

    if suffix and suffix not in base:
        return f"{base}_{suffix}"
    return base


def dedupe_aliases(aliases: Sequence[str]) -> list[str]:
    """Make column names unique without silently dropping one (spec 43)."""
    seen: Counter[str] = Counter()
    out: list[str] = []
    for alias in aliases:
        seen[alias] += 1
        out.append(alias if seen[alias] == 1 else f"{alias}_{seen[alias]}")
    return out


@dataclass
class Variable:
    """One column of the assembled dataset."""

    alias: str
    metadata: IndicatorMetadata
    concept: str | None = None

    @property
    def key(self) -> str:
        return self.metadata.key


@dataclass
class Dataset:
    """An assembled, provenance-carrying dataset.

    Observations are held in canonical long form; ``to_wide`` pivots without
    another provider round-trip, so the user can switch shape freely (spec 7.1).
    """

    spec: DatasetSpec
    variables: list[Variable] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    transformations: list[TransformationRecord] = field(default_factory=list)
    lineage: list[ColumnLineage] = field(default_factory=list)
    retrieved_at: datetime = field(default_factory=utcnow)
    name: str = "smatecondata_dataset"

    # -- indexes ---------------------------------------------------------

    def _alias_for(self, observation: Observation) -> str | None:
        for variable in self.variables:
            if (variable.metadata.provider == observation.provider
                    and variable.metadata.series_id == observation.series_id):
                return variable.alias
        return None

    @property
    def aliases(self) -> list[str]:
        return [v.alias for v in self.variables]

    @property
    def geographies(self) -> list[str]:
        seen: dict[str, None] = {}
        for obs in self.observations:
            seen.setdefault(obs.iso3 or obs.geography, None)
        return list(seen)

    @property
    def periods(self) -> list[str]:
        return sorted({obs.period for obs in self.observations})

    @property
    def frequencies(self) -> set[Frequency]:
        return {obs.frequency for obs in self.observations}

    # -- shapes ----------------------------------------------------------

    def to_long(self) -> list[dict[str, object]]:
        """``geography | iso3 | period | indicator | value | unit | source``."""
        rows: list[dict[str, object]] = []
        for obs in self.observations:
            alias = self._alias_for(obs)
            rows.append({
                "geography": obs.geography,
                "iso3": obs.iso3,
                "period": obs.period,
                "indicator": alias or f"{obs.provider}:{obs.series_id}",
                "value": obs.value,
                "unit": obs.unit,
                "frequency": obs.frequency.value,
                "provider": obs.provider,
                "series_id": obs.series_id,
                "status": obs.status,
            })
        rows.sort(key=lambda r: (str(r["iso3"] or r["geography"]),
                                 str(r["period"]), str(r["indicator"])))
        return rows

    def to_wide(self) -> tuple[list[str], list[dict[str, object]]]:
        """One row per geography-period, one column per variable.

        Cells with no observation are ``None``. That is a real gap and is left
        visible rather than filled.
        """
        grid: dict[tuple[str, str, str], list[float | None]] = defaultdict(list)
        geo_names: dict[str, str] = {}

        for obs in self.observations:
            alias = self._alias_for(obs)
            if alias is None:
                continue
            iso3 = obs.iso3 or obs.geography
            geo_names.setdefault(iso3, obs.geography)
            grid[(iso3, obs.period, alias)].append(obs.value)

        columns = ["geography", "iso3", "period", *self.aliases]
        keys = sorted({(iso3, period) for iso3, period, _ in grid})

        rows: list[dict[str, object]] = []
        for iso3, period in keys:
            row: dict[str, object] = {
                "geography": geo_names.get(iso3, iso3),
                "iso3": iso3,
                "period": period,
            }
            for alias in self.aliases:
                values = grid.get((iso3, period, alias), [])
                # A duplicate key is a data problem, not something to average
                # away: keep the first and let the validator report it.
                row[alias] = values[0] if values else None
            rows.append(row)
        return columns, rows

    def column_values(self, alias: str) -> tuple[list[str], list[float | None]]:
        """``(period labels, values)`` for one variable, across all geographies."""
        _, rows = self.to_wide()
        labels = [f"{r['iso3']} {r['period']}" for r in rows]
        values = [r.get(alias) for r in rows]  # type: ignore[arg-type]
        return labels, values  # type: ignore[return-value]

    def wide_columns_map(self) -> dict[str, list[float | None]]:
        _, rows = self.to_wide()
        return {
            alias: [r.get(alias) for r in rows]  # type: ignore[misc]
            for alias in self.aliases
        }

    # -- bookkeeping ------------------------------------------------------

    def record(self, operation: str, *, parameters: dict | None = None,
               input_columns: Sequence[str] = (),
               output_columns: Sequence[str] = (),
               note: str | None = None, lossy: bool = False) -> None:
        """Append to the transformation log. Nothing mutates silently (spec 12)."""
        self.transformations.append(TransformationRecord(
            operation=operation,
            parameters=dict(parameters or {}),
            input_columns=list(input_columns),
            output_columns=list(output_columns),
            note=note,
            lossy=lossy,
        ))

    def build_lineage(self) -> list[ColumnLineage]:
        """Proof-carrying provenance for every exported column (spec 14)."""
        applied = [t.operation for t in self.transformations]
        lineage: list[ColumnLineage] = []
        for variable in self.variables:
            md = variable.metadata
            lineage.append(ColumnLineage(
                column=variable.alias,
                provider=md.provider,
                series_id=md.series_id,
                series_title=md.title,
                definition=md.description,
                unit=md.unit,
                frequency=md.frequency,
                coverage_start=md.coverage_start,
                coverage_end=md.coverage_end,
                source_reference=md.source_reference,
                retrieved_at=self.retrieved_at,
                requested_geographies=list(self.spec.geographies),
                requested_period=self.spec.period_label(),
                transformations=applied,
                citation=citation_for(md, self.retrieved_at),
            ))
        self.lineage = lineage
        return lineage

    def rows(self) -> list[dict[str, object]]:
        """Rows in the shape the spec asked for."""
        if self.spec.output_shape is OutputShape.LONG:
            return self.to_long()
        _, rows = self.to_wide()
        return rows

    @property
    def row_count(self) -> int:
        return len(self.rows())


def citation_for(metadata: IndicatorMetadata,
                 retrieved_at: datetime | None = None) -> str:
    """Citation text built from retrieved metadata only (spec 44)."""
    when = (retrieved_at or utcnow()).astimezone(timezone.utc).strftime("%Y-%m-%d")
    source = metadata.source_name or metadata.provider
    parts = [f"{source}. \"{metadata.title}\"", f"series {metadata.series_id}"]
    if metadata.unit:
        parts.append(metadata.unit)
    tail = f"Retrieved {when} via SmatEconData"
    if metadata.source_reference:
        tail += f". {metadata.source_reference}"
    return ". ".join(parts) + ". " + tail + "."


class DatasetBuilder:
    """Assemble a :class:`Dataset` from per-series observations."""

    def __init__(self, spec: DatasetSpec, name: str | None = None):
        self.spec = spec
        self.name = name or "smatecondata_dataset"
        self._entries: list[tuple[IndicatorMetadata, str | None, list[Observation]]] = []

    def add_series(self, metadata: IndicatorMetadata,
                   observations: Iterable[Observation],
                   concept: str | None = None) -> "DatasetBuilder":
        """Add one provider series.

        The metadata is deep-copied. Two datasets built from the same catalogue
        record must not share it: comparing an old version against a refreshed
        one relies on each snapshot holding the metadata *as it was at the time*,
        and a shared object would make every historical version silently adopt
        the newest title, unit or coverage.
        """
        self._entries.append(
            (metadata.model_copy(deep=True), concept, list(observations))
        )
        return self

    def build(self) -> Dataset:
        aliases = dedupe_aliases(
            [smart_alias(md, concept) for md, concept, _ in self._entries]
        )

        variables: list[Variable] = []
        observations: list[Observation] = []
        for alias, (metadata, concept, series_obs) in zip(aliases, self._entries):
            variables.append(Variable(alias=alias, metadata=metadata,
                                      concept=concept))
            observations.extend(series_obs)

        dataset = Dataset(
            spec=self.spec,
            variables=variables,
            observations=observations,
            name=self.name,
        )
        dataset.record(
            "build_dataset",
            parameters={
                "geographies": list(self.spec.geographies),
                "period": self.spec.period_label(),
                "frequency": self.spec.frequency.value,
                "shape": self.spec.output_shape.value,
                "series": [v.key for v in variables],
            },
            output_columns=[v.alias for v in variables],
            note=f"Assembled {len(variables)} series into one dataset.",
        )
        dataset.build_lineage()
        return dataset
