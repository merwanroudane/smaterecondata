"""Architectural regression tests for the Render 512 MB free instance.

These do not measure megabytes — that is brittle across machines. They pin the
*decisions* that caused the out-of-memory kill, so a future change that
reintroduces one fails here rather than in production:

* the catalogue is queried, never loaded into Python;
* exactly one provider is constructed for a clear query;
* a fallback provider is built only after the primary actually fails;
* openpyxl, pandas and the profiling backend stay out of the search path.

Peak memory itself is measured by ``scripts/measure_instant_memory.py``.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from backend.models import DataPoint, Metadata, NormalizedData
from backend.smatecondata.catalog.store import get_catalog_store
from backend.smatecondata.datasets.instant import InstantDatasetService, summarise
from backend.smatecondata.providers.gateway import ProviderGateway

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

REPO_ROOT = Path(__file__).resolve().parents[2]

HEAVY_LIBRARIES = ("openpyxl", "pandas", "data_profiling", "matplotlib",
                   "xlsxwriter", "pyarrow")


def imported(library: str) -> bool:
    return any(m == library or m.startswith(library + ".") for m in sys.modules)


class RecordingProvider:
    """Provider stub that records construction and calls."""

    instances: list[str] = []

    def __init__(self, key: str, *, fail: bool = False, empty: bool = False):
        self.key = key
        self.fail = fail
        self.empty = empty
        self.calls: list[dict] = []
        RecordingProvider.instances.append(key)

    async def fetch_data(self, **params):
        self.calls.append(params)
        if self.fail:
            raise RuntimeError(f"{self.key} unavailable")
        geos = params.get("countries") or [params.get("country")]
        points = [] if self.empty else [
            DataPoint(date=f"{year}-01-01", value=2.0 + (year % 5))
            for year in range(2000, 2025)
        ]
        return [
            NormalizedData(
                metadata=Metadata(
                    source={"world_bank": "World Bank", "imf": "IMF"}.get(
                        self.key, self.key),
                    indicator="Inflation, consumer prices (annual %)",
                    country={"DZA": "Algeria"}.get(str(g), str(g)),
                    frequency="annual", unit="Annual %",
                    seriesId=str(params.get("indicator")),
                ),
                data=points,
            )
            for g in geos
        ]


def make_service(**flags) -> tuple[InstantDatasetService, ProviderGateway]:
    RecordingProvider.instances = []
    gateway = ProviderGateway(
        factory=lambda key: RecordingProvider(key, **flags.get(key, {})),
        timeout=5.0,
    )
    return InstantDatasetService(gateway), gateway


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# Test A -- one indicator, one country, one provider
# --------------------------------------------------------------------------


def test_a_query_resolves_and_returns_observations():
    service, gateway = make_service()
    result = run(service.build("Inflation in Algeria from 2000 to 2025"))

    assert result.parsed.spec.geographies == ["DZA"]
    assert [i.concept for i in result.parsed.spec.indicators] == ["inflation"]
    assert result.parsed.spec.start_year == 2000
    assert result.parsed.spec.end_year == 2025
    assert result.has_data
    assert result.resolution[0].series_id == "FP.CPI.TOTL.ZG"


def test_a_uses_the_preferred_provider_first():
    service, gateway = make_service()
    run(service.build("Inflation in Algeria from 2000 to 2025"))
    assert gateway.instantiated == ["world_bank"]


def test_a_does_not_instantiate_unrelated_providers():
    """The fan-out that exhausted the 512 MB instance must not return."""
    service, gateway = make_service()
    run(service.build("Inflation in Algeria from 2000 to 2025"))

    for other in ("imf", "eurostat", "oecd", "fred", "bis", "statscan"):
        assert other not in gateway.instantiated, (
            f"{other} was constructed for a query the primary provider answered"
        )
    assert len(RecordingProvider.instances) == 1


def test_a_no_cart_is_involved():
    service, _ = make_service()
    payload = summarise(run(service.build("Inflation in Algeria from 2000 to 2025")),
                        "ds1")
    assert payload["dataset"]["rows"] > 0
    assert "xlsx" in payload["available_exports"]


# --------------------------------------------------------------------------
# Tests B and C -- Arabic and French take the same low-memory path
# --------------------------------------------------------------------------


@pytest.mark.parametrize("query,language", [
    ("التضخم في الجزائر من 2000 إلى 2025", "ar"),
    ("Inflation en Algerie de 2000 a 2025", "fr"),
])
def test_bc_other_languages_resolve_identically(query, language):
    service, gateway = make_service()
    result = run(service.build(query))

    assert result.parsed.understanding()["language"] == language
    assert result.parsed.spec.geographies == ["DZA"]
    assert result.has_data
    assert result.resolution[0].series_id == "FP.CPI.TOTL.ZG"
    # Same single-provider behaviour, not a wider search.
    assert gateway.instantiated == ["world_bank"]


# --------------------------------------------------------------------------
# Tests D and E -- heavy libraries stay out of the hot path
# --------------------------------------------------------------------------


def test_d_search_modules_do_not_import_export_libraries():
    """Importing the search path must not pull in openpyxl or pandas.

    This runs in a subprocess on purpose. Inside the test session another
    module has already imported openpyxl for the export tests, so asserting on
    this process's ``sys.modules`` would measure the test suite rather than the
    application. A fresh interpreter is what a Render worker actually is.
    """
    program = textwrap.dedent(
        """
        import json, sys
        import backend.smatecondata.api.routes            # noqa: F401
        import backend.smatecondata.datasets.instant      # noqa: F401
        import backend.smatecondata.catalog.store         # noqa: F401
        import backend.smatecondata.search.query_parser   # noqa: F401
        heavy = [lib for lib in ("openpyxl", "xlsxwriter", "matplotlib",
                                 "pandas", "pyarrow")
                 if any(m == lib or m.startswith(lib + ".") for m in sys.modules)]
        print(json.dumps(heavy))
        """
    )
    env = {**os.environ, "JWT_SECRET": os.environ.get("JWT_SECRET", "test-secret")}
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    heavy = json.loads(completed.stdout.strip().splitlines()[-1])
    assert heavy == [], (
        f"{heavy} imported by the search path; they belong inside the "
        f"export and profiling handlers"
    )


def test_d_running_a_search_does_not_import_export_libraries():
    before = {lib: imported(lib) for lib in HEAVY_LIBRARIES}
    service, _ = make_service()
    run(service.build("Inflation in Algeria from 2000 to 2025"))

    for library in ("openpyxl", "xlsxwriter", "data_profiling", "matplotlib"):
        if not before[library]:
            assert not imported(library), f"search imported {library}"


def test_e_profiling_is_not_invoked_by_search():
    """Profiling is a separate, explicit action, never a side effect of search.

    (Whether the profiling module is *importable* without being imported is
    covered by the subprocess check above; asserting on ``sys.modules`` here
    would only measure which test module ran first.)
    """
    service, _ = make_service()
    result = run(service.build("Inflation in Algeria from 2000 to 2025"))
    payload = summarise(result, "ds1")
    assert "profile" not in payload
    assert "profile" not in payload["dataset"]


def test_routes_module_does_not_import_openpyxl_at_module_scope():
    """routes.py used to import the Excel writer at module scope, so every
    worker paid for openpyxl at startup even to serve a search."""
    source = importlib.import_module(
        "backend.smatecondata.api.routes"
    ).__file__
    with open(source, "r", encoding="utf-8") as handle:
        header = handle.read().split("router = APIRouter")[0]
    assert "from ..exports.excel import" not in header
    assert "from ..exports.formats import" not in header


# --------------------------------------------------------------------------
# Test F -- fallback is lazy
# --------------------------------------------------------------------------


def test_f_fallback_provider_is_built_only_after_the_primary_fails():
    service, gateway = make_service(world_bank={"fail": True})
    run(service.build("Inflation in Algeria from 2000 to 2025"))

    # The primary is tried first...
    assert gateway.instantiated[0] == "world_bank"
    # ...and no provider is constructed speculatively before it fails.
    assert len(gateway.instantiated) <= 2


def test_f_no_provider_exists_before_the_request():
    _, gateway = make_service()
    assert gateway.instantiated == []


def test_f_a_provider_is_constructed_once_and_reused():
    _, gateway = make_service()
    first = gateway.connector_for("world_bank")
    second = gateway.connector_for("world_bank")
    assert first is second
    assert gateway.instantiated == ["world_bank"]


# --------------------------------------------------------------------------
# The catalogue is queried, not loaded
# --------------------------------------------------------------------------


def test_catalogue_search_is_bounded():
    """A query must never materialise the whole catalogue."""
    store = get_catalog_store()
    if not store.available:
        pytest.skip("catalogue database not built; run scripts/build_catalog_db.py")

    hits = store.search("inflation", limit=5)
    assert len(hits) <= 5
    assert store.count() > 10_000, "the catalogue itself is still large"


def test_catalogue_exact_code_lookup():
    store = get_catalog_store()
    if not store.available:
        pytest.skip("catalogue database not built")

    metadata = store.get("world_bank", "FP.CPI.TOTL.ZG")
    assert metadata is not None
    assert metadata.title.lower().startswith("inflation")


@pytest.mark.parametrize("query", [
    "inflation", "التضخم", "taux de chomage", "PIB par habitant",
])
def test_catalogue_search_is_multilingual(query):
    store = get_catalog_store()
    if not store.available:
        pytest.skip("catalogue database not built")
    assert store.search(query, limit=3), f"no catalogue hits for {query!r}"


def test_stats_do_not_parse_whole_metadata_files():
    """Reading two header fields must not decode a 62 MB JSON document."""
    import tracemalloc

    from backend.smatecondata.catalog.stats import CatalogStatsService

    tracemalloc.start()
    CatalogStatsService().compute()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Generous ceiling: the point is that it is not hundreds of megabytes.
    assert peak / 1048576 < 80, f"catalogue stats peaked at {peak / 1048576:.0f} MB"
