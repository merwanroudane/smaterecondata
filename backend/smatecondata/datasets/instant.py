"""Zero-friction instant dataset orchestration.

One call turns a natural-language request into a real dataset:

    query -> parse -> resolve concepts and geography -> rank catalogue
          -> select series -> fetch observations (with fallback)
          -> build -> validate -> store -> preview

This exists because the pieces were all present but nothing joined them up:
``/parse`` stopped at interpretation and ``/datasets`` required series that had
already been resolved *and* fetched. The browser could reach neither end of the
pipeline on its own, so "Search" only ever produced an explanation.

Nothing here re-implements the engine. It orchestrates the existing parser,
concept store, catalogue index, provider gateway, builder and validator.

**Search means get data.** The only reason this returns without observations is
that no provider had any.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..catalog.store import get_catalog_store
from ..core.models import (
    DatasetSpec,
    Frequency,
    IndicatorMetadata,
    IndicatorRequest,
    Language,
    Observation,
    OutputShape,
)
from ..datasets.builder import Dataset, DatasetBuilder
from ..datasets.validation import validate_dataset
from ..providers.gateway import ProviderGateway
from ..search.aliases import get_concept_store
from ..search.query_parser import ParsedQuery, parse_query

logger = logging.getLogger(__name__)


def rss_mb() -> float:
    """Resident set size in MB, or 0.0 when unavailable.

    Deliberately stdlib-only: adding psutil to diagnose a memory problem would
    be self-defeating. /proc is read on Linux (which is what Render runs);
    other platforms fall back to resource, then to 0.0.
    """
    try:
        with open("/proc/self/statm", "r") as handle:
            pages = int(handle.read().split()[1])
        return pages * 4096 / 1048576
    except (OSError, IndexError, ValueError):
        pass
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports KB, macOS reports bytes.
        return peak / 1024 if peak > 1_000_000 else peak / 1024
    except Exception:
        return 0.0

# Ordered by how reliably each provider serves cross-country macro series
# without a key. Used to break ties and to pick a fallback.
PROVIDER_PRIORITY: list[str] = [
    "world_bank", "imf", "eurostat", "oecd", "ilostat", "ecb", "fred",
    "unsd", "bis", "statscan",
]

# A recommended series per concept. The catalogue can rank text well, but for
# the handful of concepts researchers ask for constantly a curated default is
# more defensible than "whatever scored highest" -- and it is transparent,
# because the chosen series id is always shown to the user.
RECOMMENDED: dict[str, list[tuple[str, str]]] = {
    "gdp": [("world_bank", "NY.GDP.MKTP.CD")],
    "gdp_per_capita": [("world_bank", "NY.GDP.PCAP.CD")],
    "gdp_growth": [("world_bank", "NY.GDP.MKTP.KD.ZG")],
    "inflation": [("world_bank", "FP.CPI.TOTL.ZG")],
    "cpi": [("world_bank", "FP.CPI.TOTL")],
    "unemployment": [("world_bank", "SL.UEM.TOTL.ZS")],
    "employment": [("world_bank", "SL.EMP.TOTL.SP.ZS")],
    "labor_force": [("world_bank", "SL.TLF.CACT.ZS")],
    "public_debt": [("world_bank", "GC.DOD.TOTL.GD.ZS")],
    "external_debt": [("world_bank", "DT.DOD.DECT.CD")],
    "fdi": [("world_bank", "BX.KLT.DINV.CD.WD")],
    "exchange_rate": [("world_bank", "PA.NUS.FCRF")],
    "interest_rate": [("world_bank", "FR.INR.RINR")],
    "money_supply": [("world_bank", "FM.LBL.BMNY.GD.ZS")],
    "exports": [("world_bank", "NE.EXP.GNFS.CD")],
    "imports": [("world_bank", "NE.IMP.GNFS.CD")],
    "trade_balance": [("world_bank", "NE.RSB.GNFS.CD")],
    "current_account": [("world_bank", "BN.CAB.XOKA.CD")],
    "population": [("world_bank", "SP.POP.TOTL")],
    "government_expenditure": [("world_bank", "GC.XPN.TOTL.GD.ZS")],
    "government_revenue": [("world_bank", "GC.REV.XGRT.GD.ZS")],
    "budget_balance": [("world_bank", "GC.BAL.CASH.GD.ZS")],
    "poverty": [("world_bank", "SI.POV.DDAY")],
    "energy_use": [("world_bank", "EG.USE.PCAP.KG.OE")],
    "renewable_energy": [("world_bank", "EG.FEC.RNEW.ZS")],
    "co2_emissions": [("world_bank", "EN.GHG.CO2.PC.CE.AR5")],
}


@dataclass
class ResolvedSeries:
    """One concept mapped onto a concrete provider series."""

    concept: str
    provider: str
    series_id: str
    official_title: str
    unit: str | None = None
    frequency: str = "unknown"
    confidence: float = 0.0
    reason: str = ""
    source_reference: str | None = None
    alternatives: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "concept": self.concept,
            "provider": self.provider,
            "series_id": self.series_id,
            "official_title": self.official_title,
            "unit": self.unit,
            "frequency": self.frequency,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "source_reference": self.source_reference,
            "alternatives": self.alternatives,
        }


@dataclass
class InstantResult:
    """Everything the browser needs from a single search."""

    query: str
    parsed: ParsedQuery
    resolution: list[ResolvedSeries] = field(default_factory=list)
    dataset: Dataset | None = None
    dataset_id: str | None = None
    warnings: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)

    @property
    def has_data(self) -> bool:
        return self.dataset is not None and bool(self.dataset.observations)


class InstantDatasetService:
    """Turns one query into one dataset (spec: Search means get data)."""

    def __init__(self, gateway: ProviderGateway, *, preview_limit: int = 300):
        self.gateway = gateway
        self.preview_limit = preview_limit

    # -- resolution -------------------------------------------------------

    def resolve_series(
        self,
        concept_key: str,
        *,
        preferred_provider: str | None = None,
        language: Language | None = None,
    ) -> ResolvedSeries | None:
        """Pick the series that best answers one concept.

        Order follows the spec's selection policy: a curated recommendation
        first (highest confidence and defensible), then the catalogue ranking.
        A title that merely shares words is never enough on its own -- the
        catalogue hit must also carry the concept tag.
        """
        store = get_catalog_store()
        concept = get_concept_store().get(concept_key)
        label = concept.label if concept else concept_key

        # 1. Curated recommendation, honoured only if it is really in the index.
        for provider, series_id in RECOMMENDED.get(concept_key, []):
            if preferred_provider and provider != preferred_provider:
                continue
            metadata = store.get(provider, series_id)
            if metadata is not None:
                return ResolvedSeries(
                    concept=concept_key,
                    provider=metadata.provider,
                    series_id=metadata.series_id,
                    official_title=metadata.title,
                    unit=metadata.unit,
                    frequency=metadata.frequency.value,
                    confidence=0.97,
                    reason="recommended series for this concept",
                    source_reference=metadata.source_reference,
                    alternatives=self._alternatives(label, metadata.series_id,
                                                    preferred_provider, language),
                )

        # 2. Catalogue ranking, restricted to entries actually tagged with the
        #    concept where possible.
        hits = store.search(
            label,
            language=language,
            providers=[preferred_provider] if preferred_provider else None,
            limit=25,
        )
        tagged = [h for h in hits if f"concept:{concept_key}" in h.matched_on]
        candidates = tagged or hits
        if not candidates:
            return None

        best = max(
            candidates,
            key=lambda hit: (
                hit.score,
                -PROVIDER_PRIORITY.index(hit.metadata.provider)
                if hit.metadata.provider in PROVIDER_PRIORITY
                else -99,
            ),
        )
        top = max(h.score for h in candidates) or 1.0
        return ResolvedSeries(
            concept=concept_key,
            provider=best.metadata.provider,
            series_id=best.metadata.series_id,
            official_title=best.metadata.title,
            unit=best.metadata.unit,
            frequency=best.metadata.frequency.value,
            confidence=round(min(0.94, 0.55 + 0.4 * (best.score / top)), 3),
            reason="best catalogue match for the requested concept",
            source_reference=best.metadata.source_reference,
            alternatives=self._alternatives(label, best.metadata.series_id,
                                            preferred_provider, language),
        )

    @staticmethod
    def _alternatives(label: str, chosen: str, provider: str | None,
                      language: Language | None) -> list[dict[str, Any]]:
        store = get_catalog_store()
        out: list[dict[str, Any]] = []
        for hit in store.search(label, language=language,
                                providers=[provider] if provider else None,
                                limit=6):
            if hit.metadata.series_id == chosen:
                continue
            out.append({
                "provider": hit.metadata.provider,
                "series_id": hit.metadata.series_id,
                "title": hit.metadata.title,
                "unit": hit.metadata.unit,
            })
            if len(out) == 3:
                break
        return out

    # -- retrieval --------------------------------------------------------

    async def _fetch_with_fallback(
        self,
        resolved: ResolvedSeries,
        geographies: Sequence[str],
        start_year: int | None,
        end_year: int | None,
        warnings: list[str],
    ) -> tuple[IndicatorMetadata, list[Observation]] | None:
        """Fetch one series, falling back to an equivalent official source.

        A fallback only happens between series the concept store considers the
        same measure; a different definition is never substituted silently.
        """
        attempts: list[tuple[str, str]] = [(resolved.provider, resolved.series_id)]
        for provider, series_id in RECOMMENDED.get(resolved.concept, []):
            if (provider, series_id) not in attempts:
                attempts.append((provider, series_id))

        for provider, series_id in attempts:
            outcome = await self.gateway.fetch_series(
                provider, series_id, list(geographies),
                start_year=start_year, end_year=end_year,
            )
            if outcome.ok and outcome.metadata is not None:
                if (provider, series_id) != (resolved.provider, resolved.series_id):
                    warnings.append(
                        f"{provider} was used for '{resolved.concept}' because "
                        f"{resolved.provider} was unavailable."
                    )
                    resolved.provider = provider
                    resolved.series_id = series_id
                    resolved.official_title = outcome.metadata.title
                if outcome.result and outcome.result.failed_geographies:
                    warnings.append(
                        f"No observations for {resolved.concept} in "
                        f"{', '.join(outcome.result.failed_geographies)}."
                    )
                return outcome.metadata, outcome.observations

            if outcome.result and outcome.result.message:
                logger.info("instant fetch failed %s:%s — %s",
                            provider, series_id, outcome.result.message)

        warnings.append(
            f"No official source returned data for '{resolved.concept}'."
        )
        return None

    # -- orchestration ----------------------------------------------------

    async def build(
        self,
        query: str,
        *,
        output_shape: OutputShape | None = None,
        provider: str | None = None,
        frequency: Frequency | None = None,
        language: Language | None = None,
        name: str | None = None,
    ) -> InstantResult:
        """The whole pipeline, in one call."""
        import time

        t0 = time.perf_counter()
        timings: dict[str, float] = {}

        parsed = parse_query(query, language=language)
        timings["parse_ms"] = (time.perf_counter() - t0) * 1000
        spec = parsed.spec

        if output_shape is not None:
            spec.output_shape = output_shape
        if frequency is not None:
            spec.frequency = frequency
        # "automatic" is the default and means: do not pin a provider.
        preferred = None if provider in (None, "", "automatic") else provider
        if preferred:
            spec.preferred_sources = [preferred]

        result = InstantResult(query=query, parsed=parsed)

        if not spec.geographies:
            result.warnings.append(
                "No country was detected in the request. Add a country or area "
                "to retrieve data."
            )
            return result
        if not spec.indicators:
            result.warnings.append(
                "No economic concept was recognised in the request."
            )
            return result

        # 1. Resolve every concept to a concrete series.
        for request in spec.indicators:
            resolved = self.resolve_series(
                request.concept, preferred_provider=preferred, language=parsed.language
            )
            if resolved is None:
                result.unresolved.append(request.concept)
                result.warnings.append(
                    f"No catalogue series matched '{request.concept}'."
                )
                continue
            request.resolved_provider = resolved.provider
            request.resolved_series_id = resolved.series_id
            result.resolution.append(resolved)

        timings["resolve_ms"] = (time.perf_counter() - t0) * 1000 - timings["parse_ms"]

        if not result.resolution:
            self._log(query, timings, t0, result)
            return result

        # 2. Retrieve all of them in parallel; a failure is a warning, not an
        #    abort, so a partial multi-indicator request still returns data.
        fetch_started = time.perf_counter()
        fetched = await asyncio.gather(*[
            self._fetch_with_fallback(
                resolved, spec.geographies, spec.start_year, spec.end_year,
                result.warnings,
            )
            for resolved in result.resolution
        ])

        timings["fetch_ms"] = (time.perf_counter() - fetch_started) * 1000
        build_started = time.perf_counter()

        builder = DatasetBuilder(
            spec, name=name or self._dataset_name(parsed)
        )
        added = 0
        for resolved, payload in zip(list(result.resolution), fetched):
            if payload is None:
                result.unresolved.append(resolved.concept)
                result.resolution.remove(resolved)
                continue
            metadata, observations = payload
            builder.add_series(metadata, observations, resolved.concept)
            added += 1

        if not added:
            self._log(query, timings, t0, result)
            return result

        dataset = builder.build()
        result.dataset = dataset
        timings["build_ms"] = (time.perf_counter() - build_started) * 1000

        # 3. Honest reporting on the period actually returned.
        periods = sorted({o.period for o in dataset.observations if o.value is not None})
        if periods and spec.end_year:
            last = periods[-1][:4]
            if last.isdigit() and int(last) < spec.end_year:
                result.warnings.append(
                    f"{spec.end_year} is not yet available from this source; "
                    f"data runs to {last}."
                )

        self._log(query, timings, t0, result)
        return result

    def _log(self, query: str, timings: dict[str, float], started: float,
             result: "InstantResult") -> None:
        """One structured line per request (spec 13). Never logs secrets."""
        import time

        parts = [f"{k}={v:.0f}" for k, v in timings.items()]
        parts.append(f"total_ms={(time.perf_counter() - started) * 1000:.0f}")
        parts.append(f"providers={'+'.join(self.gateway.instantiated) or 'none'}")
        parts.append(f"series={len(result.resolution)}")
        parts.append(f"rows={result.dataset.row_count if result.dataset else 0}")
        memory = rss_mb()
        if memory:
            parts.append(f"rss_mb={memory:.0f}")
        logger.info("instant_dataset %s", " ".join(parts))

    @staticmethod
    def _dataset_name(parsed: ParsedQuery) -> str:
        """A human-friendly filename stem from the request itself."""
        spec = parsed.spec
        parts = [c.key for c in parsed.concepts[:3]] or ["dataset"]
        geo = "_".join(spec.geographies[:3]).lower() or "world"
        period = spec.period_label().replace(" ", "_")
        return f"{'_'.join(parts)}_{geo}_{period}"[:80]

    # -- structured (Data Cart) entry point -------------------------------

    async def build_from_selection(
        self,
        *,
        series: Sequence[dict[str, Any]],
        geographies: Sequence[str],
        start_year: int | None = None,
        end_year: int | None = None,
        frequency: Frequency = Frequency.ANNUAL,
        output_shape: OutputShape = OutputShape.WIDE,
        name: str | None = None,
    ) -> InstantResult:
        """Build from explicit selections — the Data Cart path.

        Deliberately the SAME engine as the natural-language path, so the cart
        cannot drift into a second, differently-behaving data pipeline.
        """
        spec = DatasetSpec(
            geographies=list(geographies),
            indicators=[
                IndicatorRequest(concept=str(item.get("concept") or item.get("series_id")))
                for item in series
            ],
            start_year=start_year,
            end_year=end_year,
            frequency=frequency,
            output_shape=output_shape,
            original_query=None,
        )
        parsed = ParsedQuery(spec=spec, language=Language.EN)
        result = InstantResult(query="", parsed=parsed)

        if not series:
            result.warnings.append("No series were selected.")
            return result
        if not geographies:
            result.warnings.append("Select at least one country or area.")
            return result

        store = get_catalog_store()
        for item in series:
            concept = str(item.get("concept") or "")
            provider = item.get("provider")
            series_id = item.get("series_id")

            if provider and series_id:
                metadata = store.get(str(provider), str(series_id))
                resolved = ResolvedSeries(
                    concept=concept or str(series_id),
                    provider=str(provider),
                    series_id=str(series_id),
                    official_title=metadata.title if metadata else str(series_id),
                    unit=metadata.unit if metadata else None,
                    confidence=1.0,
                    reason="explicitly selected",
                    source_reference=metadata.source_reference if metadata else None,
                )
            else:
                found = self.resolve_series(concept)
                if found is None:
                    result.unresolved.append(concept)
                    result.warnings.append(f"No series matched '{concept}'.")
                    continue
                resolved = found
            result.resolution.append(resolved)

        if not result.resolution:
            return result

        fetched = await asyncio.gather(*[
            self._fetch_with_fallback(
                resolved, spec.geographies, start_year, end_year, result.warnings
            )
            for resolved in result.resolution
        ])

        builder = DatasetBuilder(spec, name=name or "cart_dataset")
        added = 0
        for resolved, payload in zip(list(result.resolution), fetched):
            if payload is None:
                result.unresolved.append(resolved.concept)
                result.resolution.remove(resolved)
                continue
            metadata, observations = payload
            builder.add_series(metadata, observations, resolved.concept)
            added += 1

        if added:
            result.dataset = builder.build()
        return result


def summarise(result: InstantResult, dataset_id: str | None,
              preview_limit: int = 300) -> dict[str, Any]:
    """Serialise an :class:`InstantResult` for the browser."""
    understanding = result.parsed.understanding()
    payload: dict[str, Any] = {
        "query": result.query,
        "understanding": understanding,
        "resolution": [r.as_dict() for r in result.resolution],
        "warnings": result.warnings,
        "unresolved": result.unresolved,
        "dataset": None,
        "available_exports": [],
    }

    if result.dataset is None:
        return payload

    dataset = result.dataset
    columns, rows = dataset.to_wide()
    if dataset.spec.output_shape is OutputShape.LONG:
        rows = dataset.to_long()
        columns = ["geography", "iso3", "period", "indicator", "value", "unit",
                   "frequency", "provider", "series_id", "status"]

    quality = validate_dataset(dataset)
    payload["dataset"] = {
        "dataset_id": dataset_id,
        "name": dataset.name,
        "rows": len(rows),
        "columns": list(columns),
        "preview": rows[:preview_limit],
        "truncated": len(rows) > preview_limit,
        "geographies": dataset.geographies,
        "period": dataset.spec.period_label(),
        "frequency": dataset.spec.frequency.value,
        "shape": dataset.spec.output_shape.value,
        "quality": {
            "status": quality.status,
            "coverage_pct": quality.coverage_pct,
            "missing_cells": quality.missing_cells,
            "errors": quality.errors,
            "warnings": quality.warnings,
        },
    }
    payload["available_exports"] = [
        "xlsx", "csv", "json", "parquet", "html", "bundle", "recipe_yaml",
    ]
    return payload
