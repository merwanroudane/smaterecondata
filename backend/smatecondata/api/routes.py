"""Versioned REST surface for the SmatEconData dataset layer (spec 31).

Mounted by ``backend/main.py`` as a router so the existing application file
does not grow further. Everything here is provider-agnostic: a caller supplies
resolved series plus their observations, or a saved recipe, and gets back a
validated, documented, exportable dataset.

Nothing in this module fabricates an observation. Endpoints that would need
data they were not given return an explicit error instead.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import gettempdir
from threading import Lock
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# Heavy modules (openpyxl, pandas, pyarrow, profiling) are imported inside
# the handlers that need them. Importing them here pulled openpyxl and
# numpy into every worker at startup, on a 512 MB instance whose entire
# job for most requests is a search that touches neither.
from ..analytics import missingness as missingness_mod
from ..catalog.stats import get_catalog_stats_service
from ..catalog.store import get_catalog_store
from ..analytics.profiling import ProfileMode
from ..analytics.descriptive import correlation_matrix, describe, iqr_flags, zscore_flags
from ..core.models import (
    DatasetSpec,
    Frequency,
    IndicatorMetadata,
    Language,
    Observation,
    OutputShape,
)
from ..datasets.builder import Dataset, DatasetBuilder
from ..datasets.instant import InstantDatasetService, summarise
from ..datasets.validation import validate_dataset
from ..providers.gateway import ProviderGateway
from ..provenance.recipe import DatasetRecipe, compare_versions, recipe_from_dataset
from ..search.aliases import get_concept_store
from ..search.geography import COUNTRY_ALIASES, REGIONS, get_geography_resolver
from ..search.query_parser import parse_query

router = APIRouter(prefix="/api/v1", tags=["smatecondata"])

DATASET_TTL = timedelta(hours=6)
MAX_DATASETS = 200
EXPORT_ROOT = Path(gettempdir()) / "smatecondata_exports"


class _DatasetStore:
    """In-memory dataset store with a TTL.

    Datasets are working artefacts, not permanent storage (spec 34): the
    recipe is what persists, and re-running it rebuilds the table from the
    providers.
    """

    def __init__(self) -> None:
        self._items: dict[str, tuple[datetime, Dataset]] = {}
        self._lock = Lock()

    def put(self, dataset: Dataset) -> str:
        dataset_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._evict()
            self._items[dataset_id] = (datetime.now(timezone.utc), dataset)
        return dataset_id

    def get(self, dataset_id: str) -> Dataset:
        with self._lock:
            entry = self._items.get(dataset_id)
        if entry is None:
            raise HTTPException(404, f"Unknown dataset id: {dataset_id}")
        created, dataset = entry
        if datetime.now(timezone.utc) - created > DATASET_TTL:
            with self._lock:
                self._items.pop(dataset_id, None)
            raise HTTPException(
                410, f"Dataset {dataset_id} expired. Re-run its recipe to rebuild it.")
        return dataset

    def _evict(self) -> None:
        now = datetime.now(timezone.utc)
        for key in [k for k, (t, _) in self._items.items()
                    if now - t > DATASET_TTL]:
            self._items.pop(key, None)
        while len(self._items) >= MAX_DATASETS:
            oldest = min(self._items, key=lambda k: self._items[k][0])
            self._items.pop(oldest, None)


STORE = _DatasetStore()


# --------------------------------------------------------------------------
# Payloads
# --------------------------------------------------------------------------


class ParseRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    language: Language | None = None
    current_year: int | None = None


class SeriesPayload(BaseModel):
    """One resolved series plus the observations retrieved for it."""

    metadata: IndicatorMetadata
    observations: list[Observation] = Field(default_factory=list)
    concept: str | None = None


class BuildRequest(BaseModel):
    name: str = Field(default="smatecondata_dataset", max_length=120)
    spec: DatasetSpec | None = None
    query: str | None = None
    series: list[SeriesPayload] = Field(min_length=1)


class TransformRequest(BaseModel):
    operation: Literal["filter_geographies", "filter_periods",
                       "select_variables", "rename_variable", "set_shape"]
    parameters: dict[str, Any] = Field(default_factory=dict)


class ExportRequest(BaseModel):
    format: Literal["xlsx", "csv", "json", "parquet", "feather", "dta",
                    "html", "bundle", "recipe_yaml", "recipe_json"]


# --------------------------------------------------------------------------
# Discovery (works with no LLM and no providers configured -- spec 0P)
# --------------------------------------------------------------------------


@router.post("/parse", operation_id="parse_data_request")
def parse(request: ParseRequest) -> dict[str, Any]:
    """Natural-language request -> transparent ``What I understood`` panel."""
    parsed = parse_query(request.query, language=request.language,
                         current_year=request.current_year)
    return {
        "understanding": parsed.understanding(),
        "spec": parsed.spec.model_dump(mode="json"),
        "ambiguous": [
            {
                "concept": c.key,
                "label": c.label,
                "distinctions": list(c.distinctions),
            }
            for c in parsed.ambiguous_concepts
        ],
    }


@router.get("/concepts", operation_id="list_concepts")
def list_concepts(language: Language = Language.EN) -> dict[str, Any]:
    store = get_concept_store()
    return {
        "count": len(store.concepts),
        "concepts": [
            {
                "key": c.key,
                "label": c.label,
                "display": c.display(language),
                "topic": c.topic,
                "ambiguous": c.ambiguous,
                "distinctions": list(c.distinctions),
                "aliases": {k: list(v) for k, v in c.aliases.items()},
            }
            for c in store.concepts.values()
        ],
    }


@router.get("/geographies", operation_id="list_geographies")
def list_geographies(language: Language = Language.EN) -> dict[str, Any]:
    """Countries and region presets.

    Each country carries its aliases in ALL three languages, not just the
    requested display language. The interface language is independent of the
    search language (spec 0O), so a French UI must still resolve `تونس` — the
    client cannot match trilingually if it only ever receives one name.
    """
    resolver = get_geography_resolver()
    return {
        "countries": [
            {
                "iso3": iso3,
                "name": resolver.display_name(iso3, language),
                "aliases": list(COUNTRY_ALIASES[iso3]),
            }
            for iso3 in sorted(COUNTRY_ALIASES)
        ],
        "regions": [
            {"key": key, "members": list(members)}
            for key, members in sorted(REGIONS.items())
        ],
    }


# --------------------------------------------------------------------------
# Datasets
# --------------------------------------------------------------------------


def _summarise(dataset_id: str, dataset: Dataset) -> dict[str, Any]:
    return {
        "dataset_id": dataset_id,
        "name": dataset.name,
        "rows": dataset.row_count,
        "variables": dataset.aliases,
        "geographies": dataset.geographies,
        "period": dataset.spec.period_label(),
        "frequency": dataset.spec.frequency.value,
        "shape": dataset.spec.output_shape.value,
        "retrieved_at": dataset.retrieved_at.isoformat(),
        "recipe_hash": recipe_from_dataset(dataset).recipe_hash,
    }


@router.post("/datasets", status_code=201, operation_id="build_dataset")
def build_dataset(request: BuildRequest) -> dict[str, Any]:
    """Assemble a dataset from already-resolved series."""
    if request.spec is not None:
        spec = request.spec
    elif request.query:
        spec = parse_query(request.query).spec
    else:
        raise HTTPException(422, "Provide either 'spec' or 'query'.")

    builder = DatasetBuilder(spec, name=request.name)
    for entry in request.series:
        builder.add_series(entry.metadata, entry.observations, entry.concept)
    dataset = builder.build()

    dataset_id = STORE.put(dataset)
    return _summarise(dataset_id, dataset)


@router.get("/datasets/{dataset_id}", operation_id="get_dataset")
def get_dataset(dataset_id: str,
                limit: int = Query(500, ge=1, le=50_000),
                offset: int = Query(0, ge=0)) -> dict[str, Any]:
    dataset = STORE.get(dataset_id)
    rows = dataset.rows()
    return {
        **_summarise(dataset_id, dataset),
        "total_rows": len(rows),
        "offset": offset,
        "rows": rows[offset:offset + limit],
    }


@router.post("/datasets/{dataset_id}/validate", operation_id="validate_dataset")
def validate(dataset_id: str) -> dict[str, Any]:
    report = validate_dataset(STORE.get(dataset_id))
    return report.model_dump(mode="json") | {
        "warnings": report.warnings,
        "errors": report.errors,
    }


@router.post("/datasets/{dataset_id}/describe", operation_id="describe_dataset")
def describe_dataset(dataset_id: str,
                     correlation: Literal["pearson", "spearman", "none"] = "pearson",
                     ) -> dict[str, Any]:
    dataset = STORE.get(dataset_id)
    columns = dataset.wide_columns_map()
    _, rows = dataset.to_wide()
    periods = [str(r["period"]) for r in rows]
    units = {v.alias: v.metadata.unit for v in dataset.variables}

    stats = [
        describe(values, variable=alias, periods=periods, unit=units.get(alias))
        for alias, values in columns.items()
    ]
    payload: dict[str, Any] = {
        "dataset_id": dataset_id,
        "statistics": [s.as_row() for s in stats],
        "note": ("Descriptive statistics only. Missing values are excluded, "
                 "never imputed. No model estimation is performed."),
    }
    if correlation != "none" and len(columns) > 1:
        payload["correlation"] = {
            "method": correlation,
            "matrix": correlation_matrix(columns, method=correlation),
        }
    return payload


@router.get("/datasets/{dataset_id}/missingness", operation_id="summarize_missingness")
def get_missingness(dataset_id: str) -> dict[str, Any]:
    report = missingness_mod.analyse(STORE.get(dataset_id))
    return {
        "dataset_id": dataset_id,
        "total_cells": report.total_cells,
        "missing_cells": report.missing_cells,
        "missing_pct": report.missing_pct,
        "coverage_pct": report.coverage_pct,
        "by_variable": report.as_rows(),
        "coverage_matrix": report.coverage_matrix,
        "periods": report.periods,
        "note": "Missing values are reported, never imputed.",
    }


@router.get("/datasets/{dataset_id}/outliers", operation_id="flag_outliers")
def get_outliers(dataset_id: str,
                 method: Literal["zscore", "iqr"] = "zscore",
                 threshold: float = Query(3.0, gt=0)) -> dict[str, Any]:
    dataset = STORE.get(dataset_id)
    _, rows = dataset.to_wide()
    periods = [f"{r['iso3']} {r['period']}" for r in rows]
    detector = zscore_flags if method == "zscore" else iqr_flags

    flagged = {
        alias: [
            {"period": f.period, "value": f.value, "score": f.score,
             "reason": f.reason}
            for f in detector(values, periods, threshold)
        ]
        for alias, values in dataset.wide_columns_map().items()
    }
    return {
        "dataset_id": dataset_id,
        "method": method,
        "threshold": threshold,
        "flags": flagged,
        "note": "Flags are for inspection only; no value was altered.",
    }


@router.get("/datasets/{dataset_id}/lineage", operation_id="get_dataset_lineage")
def get_lineage(dataset_id: str) -> dict[str, Any]:
    dataset = STORE.get(dataset_id)
    lineage = dataset.lineage or dataset.build_lineage()
    return {
        "dataset_id": dataset_id,
        "columns": [line.model_dump(mode="json") for line in lineage],
    }


@router.post("/datasets/{dataset_id}/transform", operation_id="create_basic_transformation")
def transform(dataset_id: str, request: TransformRequest) -> dict[str, Any]:
    """Explicit, logged, non-destructive transformations (spec 12)."""
    dataset = STORE.get(dataset_id)
    params = request.parameters
    before = list(dataset.aliases)

    if request.operation == "filter_geographies":
        keep = {str(g).upper() for g in params.get("geographies", [])}
        if not keep:
            raise HTTPException(422, "'geographies' must be a non-empty list.")
        dataset.observations = [
            o for o in dataset.observations
            if (o.iso3 or o.geography).upper() in keep
        ]
    elif request.operation == "filter_periods":
        start, end = params.get("start"), params.get("end")
        dataset.observations = [
            o for o in dataset.observations
            if (start is None or o.period >= str(start))
            and (end is None or o.period <= str(end))
        ]
    elif request.operation == "select_variables":
        keep = set(params.get("variables") or [])
        unknown = keep - set(dataset.aliases)
        if unknown:
            raise HTTPException(422, f"Unknown variables: {sorted(unknown)}")
        dataset.variables = [v for v in dataset.variables if v.alias in keep]
    elif request.operation == "rename_variable":
        old, new = params.get("from"), params.get("to")
        if not old or not new:
            raise HTTPException(422, "'from' and 'to' are required.")
        if new in dataset.aliases:
            raise HTTPException(422, f"Alias already in use: {new}")
        for variable in dataset.variables:
            if variable.alias == old:
                variable.alias = str(new)
                break
        else:
            raise HTTPException(422, f"Unknown variable: {old}")
    elif request.operation == "set_shape":
        dataset.spec.output_shape = OutputShape(params.get("shape", "wide"))

    dataset.record(request.operation, parameters=params,
                   input_columns=before, output_columns=list(dataset.aliases))
    dataset.build_lineage()
    return _summarise(dataset_id, dataset) | {
        "transformations": len(dataset.transformations)
    }


# --------------------------------------------------------------------------
# Reproducibility
# --------------------------------------------------------------------------


@router.get("/datasets/{dataset_id}/recipe", operation_id="save_dataset_recipe")
def get_recipe(dataset_id: str, fmt: Literal["yaml", "json"] = "yaml") -> Any:
    recipe = recipe_from_dataset(STORE.get(dataset_id))
    if fmt == "json":
        return recipe.to_dict()
    return {"recipe_hash": recipe.recipe_hash, "yaml": recipe.to_yaml()}


@router.post("/recipes/validate", operation_id="load_dataset_recipe")
def validate_recipe(recipe: dict[str, Any]) -> dict[str, Any]:
    """Check an uploaded recipe before it is executed (spec 47)."""
    try:
        loaded = DatasetRecipe.from_dict(recipe)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, f"Invalid recipe: {exc}") from exc
    return {
        "valid": True,
        "name": loaded.name,
        "recipe_hash": loaded.recipe_hash,
        "series": [s.to_dict() for s in loaded.series],
        "spec": loaded.to_spec().model_dump(mode="json"),
    }


@router.post("/datasets/{dataset_id}/compare/{other_id}", operation_id="compare_dataset_versions")
def compare(dataset_id: str, other_id: str) -> dict[str, Any]:
    summary = compare_versions(STORE.get(dataset_id), STORE.get(other_id))
    return {
        "previous": dataset_id,
        "current": other_id,
        "new_observations": summary.new_observations,
        "updated_observations": summary.updated_observations,
        "removed_observations": summary.removed_observations,
        "metadata_changes": summary.metadata_changes,
        "recipe_hash_changed": summary.recipe_hash_changed,
        "has_changes": summary.has_changes,
        "details": summary.details,
    }


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------

_MEDIA = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "json": "application/json",
    "parquet": "application/vnd.apache.parquet",
    "feather": "application/vnd.apache.arrow.file",
    "dta": "application/x-stata-dta",
    "html": "text/html",
    "bundle": "application/zip",
}


@router.post("/datasets/{dataset_id}/export", operation_id="export_dataset")
def export(dataset_id: str, request: ExportRequest) -> Any:
    dataset = STORE.get(dataset_id)
    fmt = request.format

    if fmt == "recipe_yaml":
        return {"filename": f"{dataset.name}_recipe.yaml",
                "content": recipe_from_dataset(dataset).to_yaml()}
    if fmt == "recipe_json":
        return recipe_from_dataset(dataset).to_dict()

    # Imported here, not at module scope: openpyxl/pyarrow/pandas cost tens of
    # MB and are only needed once the user actually asks for a file.
    from ..exports.excel import export_workbook
    from ..exports.formats import (
        ExportUnavailable,
        export_csv,
        export_feather,
        export_json,
        export_parquet,
        export_research_bundle,
        export_stata,
    )
    from ..exports.html_report import export_report

    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    suffix = "zip" if fmt == "bundle" else fmt
    path = EXPORT_ROOT / f"{dataset_id}_{dataset.name}.{suffix}"
    quality = validate_dataset(dataset)

    try:
        if fmt == "xlsx":
            export_workbook(dataset, path, quality=quality)
        elif fmt == "html":
            export_report(dataset, path, quality=quality)
        elif fmt == "csv":
            export_csv(dataset, path)
        elif fmt == "json":
            export_json(dataset, path)
        elif fmt == "parquet":
            export_parquet(dataset, path)
        elif fmt == "feather":
            export_feather(dataset, path)
        elif fmt == "dta":
            export_stata(dataset, path)
        elif fmt == "bundle":
            export_research_bundle(dataset, path, quality=quality)
    except ExportUnavailable as exc:
        raise HTTPException(501, str(exc)) from exc

    return FileResponse(path, media_type=_MEDIA.get(fmt, "application/octet-stream"),
                        filename=path.name)


# --------------------------------------------------------------------------
# Automatic EDA / profiling (spec 10A)
# --------------------------------------------------------------------------


@router.get("/profiling/status", operation_id="get_profiling_status")
def profiling_status() -> dict[str, Any]:
    """Whether automatic profiling is usable, and why not if it is not."""
    from ..analytics.profiling import profiling_available

    available, reason = profiling_available()
    return {
        "available": available,
        "reason": reason,
        "modes": [m.value for m in ProfileMode],
        "note": ("Profiling is an optional enhancement. Dataset building, "
                 "statistics and exports work without it."),
    }


@router.post("/datasets/{dataset_id}/profile", operation_id="profile_dataset")
def profile_dataset(dataset_id: str,
                    mode: ProfileMode = ProfileMode.QUICK,
                    correlations: bool = True) -> dict[str, Any]:
    """Run automatic EDA.

    Never fails the request: when the profiling backend is missing or raises,
    the response carries a status and the economic-data summary, so the user
    keeps everything they retrieved (spec 10A).
    """
    from ..analytics.profiling import get_profiling_service

    dataset = STORE.get(dataset_id)
    result = get_profiling_service().profile_dataset(
        dataset, mode=mode, correlations=correlations)
    return {"dataset_id": dataset_id, **result.as_dict()}


@router.get("/datasets/{dataset_id}/profile/summary",
            operation_id="get_profile_summary")
def profile_summary(dataset_id: str) -> dict[str, Any]:
    """Economic-data summary: panel balance, coverage, roles, units."""
    from ..analytics.profiling import get_profiling_service

    dataset = STORE.get(dataset_id)
    return {
        "dataset_id": dataset_id,
        "summary": get_profiling_service().economic_summary(dataset),
    }


@router.get("/datasets/{dataset_id}/profile/report.html",
            operation_id="export_profile_html")
def profile_html(dataset_id: str,
                 mode: ProfileMode = ProfileMode.QUICK) -> Any:
    dataset = STORE.get(dataset_id)
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    path = EXPORT_ROOT / f"{dataset_id}_{dataset.name}_profile.html"

    from ..analytics.profiling import get_profiling_service

    result = get_profiling_service().profile_dataset(
        dataset, mode=mode, html_path=path)
    if not result.ok or result.html_path is None:
        raise HTTPException(
            501, result.message or "Profiling is not available on this server.")
    return FileResponse(path, media_type="text/html", filename=path.name)


@router.get("/datasets/{dataset_id}/profile/report.json",
            operation_id="export_profile_json")
def profile_json(dataset_id: str,
                 mode: ProfileMode = ProfileMode.QUICK) -> Any:
    dataset = STORE.get(dataset_id)
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    path = EXPORT_ROOT / f"{dataset_id}_{dataset.name}_profile.json"

    from ..analytics.profiling import get_profiling_service

    result = get_profiling_service().profile_dataset(
        dataset, mode=mode, json_path=path)
    if not result.ok or result.json_path is None:
        raise HTTPException(
            501, result.message or "Profiling is not available on this server.")
    return FileResponse(path, media_type="application/json", filename=path.name)


# --------------------------------------------------------------------------
# Catalogue (spec 0.1A, 6.2, 25, 26)
# --------------------------------------------------------------------------


@router.get("/catalog/stats", operation_id="get_catalog_stats")
def catalog_stats() -> dict[str, Any]:
    """Live catalogue statistics.

    Every figure is computed from the actual index. The compact `1M+` label is
    only emitted once the index genuinely holds a million records (spec 0.1A
    truth-in-marketing rule).
    """
    return get_catalog_stats_service().compute().as_dict()


@router.get("/indicators/search", operation_id="search_indicators")
def search_indicators(
    q: str = Query(min_length=1, max_length=400),
    language: Language | None = None,
    provider: str | None = None,
    topic: str | None = None,
    limit: int = Query(20, ge=1, le=200),
) -> dict[str, Any]:
    """Search the provider catalogue (metadata only, no observations)."""
    store = get_catalog_store()
    hits = store.search(
        q,
        language=language,
        providers=[provider] if provider else None,
        topic=topic,
        limit=limit,
    )
    return {
        "query": q,
        "count": len(hits),
        "catalog_size": store.count(),
        "results": [
            {
                "provider": hit.metadata.provider,
                "series_id": hit.metadata.series_id,
                "title": hit.metadata.title,
                "description": hit.metadata.description,
                "unit": hit.metadata.unit,
                "frequency": hit.metadata.frequency.value,
                "topic": hit.metadata.topic,
                "source_name": hit.metadata.source_name,
                "source_reference": hit.metadata.source_reference,
                "score": hit.score,
                "matched_on": hit.matched_on,
            }
            for hit in hits
        ],
        "note": "Catalogue metadata only. Retrieval happens through the dataset endpoints.",
    }


@router.get("/indicators/{provider}/{series_id:path}",
            operation_id="get_indicator_metadata")
def indicator_metadata(provider: str, series_id: str) -> dict[str, Any]:
    """Official metadata for one provider series."""
    metadata = get_catalog_store().get(provider, series_id)
    if metadata is None:
        raise HTTPException(404, f"Unknown series: {provider}:{series_id}")
    return metadata.model_dump(mode="json")


@router.get("/catalog/topics", operation_id="list_topics")
def list_topics() -> dict[str, Any]:
    store = get_catalog_store()
    return {
        "topics": [
            {"topic": topic, "series": count}
            for topic, count in store.topic_counts().items()
        ],
        "catalog_size": store.count(),
    }


@router.get("/providers", operation_id="list_providers")
def list_providers() -> dict[str, Any]:
    store = get_catalog_store()
    counts = store.provider_counts()
    return {
        "providers": [
            {"provider": provider, "series": count}
            for provider, count in counts.items()
        ],
        "count": len(counts),
    }


# --------------------------------------------------------------------------
# Zero-friction instant dataset (Search means get data)
# --------------------------------------------------------------------------


class InstantRequest(BaseModel):
    """Only `query` is required; everything else has a safe default."""

    query: str = Field(min_length=1, max_length=2000)
    output_shape: OutputShape | None = None
    provider: str | None = None
    frequency: Frequency | None = None
    language: Language | None = None
    preview_limit: int = Field(300, ge=1, le=5000)
    name: str | None = Field(default=None, max_length=120)


class CartSeriesItem(BaseModel):
    concept: str | None = None
    provider: str | None = None
    series_id: str | None = None


class CartBuildRequest(BaseModel):
    """Structured build from the Data Cart — same engine as Quick Search."""

    series: list[CartSeriesItem] = Field(min_length=1)
    geographies: list[str] = Field(default_factory=list)
    start_year: int | None = None
    end_year: int | None = None
    frequency: Frequency = Frequency.ANNUAL
    output_shape: OutputShape = OutputShape.WIDE
    preview_limit: int = Field(300, ge=1, le=5000)
    name: str | None = Field(default=None, max_length=120)


# Provider key -> "module:Class". Resolved one at a time, on demand.
PROVIDER_IMPORTS: dict[str, str] = {
    "world_bank": "backend.providers.worldbank:WorldBankProvider",
    "imf": "backend.providers.imf:IMFProvider",
    "eurostat": "backend.providers.eurostat:EurostatProvider",
    "oecd": "backend.providers.oecd:OECDProvider",
    "fred": "backend.providers.fred:FREDProvider",
    "bis": "backend.providers.bis:BISProvider",
    "statscan": "backend.providers.statscan:StatsCanProvider",
}


def _make_provider(key: str) -> Any | None:
    """Import and instantiate exactly one connector.

    Called by the gateway on first use of a provider. Instantiating all seven
    to answer one question wasted the import cost of every connector's
    dependency tree; on a 512 MB instance that waste is what got the process
    killed. A fallback provider is only ever built if the primary actually
    failed.
    """
    target = PROVIDER_IMPORTS.get(key)
    if not target:
        return None
    module_name, class_name = target.split(":")
    try:
        module = __import__(module_name, fromlist=[class_name])
        return getattr(module, class_name)()
    except Exception as exc:  # connector deps are optional per deployment
        logging.getLogger(__name__).warning(
            "provider %s unavailable: %s", key, exc
        )
        return None


def _instant_service() -> InstantDatasetService:
    """Instant-search service with lazily-created providers."""
    return InstantDatasetService(
        ProviderGateway(factory=_make_provider, timeout=20.0)
    )


@router.post("/instant-dataset", operation_id="create_economic_dataset")
async def instant_dataset(request: InstantRequest) -> dict[str, Any]:
    """Natural-language request in, real dataset out.

    This is the zero-friction path: parse, resolve, retrieve, build, validate
    and store in one call, so Search produces data rather than an explanation.
    """
    service = _instant_service()
    result = await service.build(
        request.query,
        output_shape=request.output_shape,
        provider=request.provider,
        frequency=request.frequency,
        language=request.language,
        name=request.name,
    )

    dataset_id = STORE.put(result.dataset) if result.dataset is not None else None
    return summarise(result, dataset_id, preview_limit=request.preview_limit)


@router.post("/instant-dataset/from-selection", operation_id="build_from_selection")
async def instant_from_selection(request: CartBuildRequest) -> dict[str, Any]:
    """Build from explicit Data Cart selections, through the same engine."""
    service = _instant_service()
    result = await service.build_from_selection(
        series=[item.model_dump() for item in request.series],
        geographies=request.geographies,
        start_year=request.start_year,
        end_year=request.end_year,
        frequency=request.frequency,
        output_shape=request.output_shape,
        name=request.name,
    )

    dataset_id = STORE.put(result.dataset) if result.dataset is not None else None
    return summarise(result, dataset_id, preview_limit=request.preview_limit)
