"""Zero-friction search tests — acceptance tests A-G of the redesign spec.

The rule under test is one sentence: **Search means get data.** A query must
produce actual observations, not an interpretation of itself, and must do it
without a cart, a provider picker, an indicator code or a repeated country.

Stub connectors throughout; no network.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.models import DataPoint, Metadata, NormalizedData
from backend.smatecondata.core.models import Frequency, OutputShape
from backend.smatecondata.datasets.instant import (
    InstantDatasetService,
    summarise,
)
from backend.smatecondata.providers.gateway import ProviderGateway

COUNTRY_NAMES = {"DZA": "Algeria", "MAR": "Morocco", "TUN": "Tunisia"}

# Real-shaped World Bank responses, keyed by series id.
SERIES = {
    "FP.CPI.TOTL.ZG": ("Inflation, consumer prices (annual %)", "Annual %", 2.5),
    "NY.GDP.PCAP.CD": ("GDP per capita (current US$)", "current US$", 1800.0),
    "SL.UEM.TOTL.ZS": ("Unemployment, total (% of labor force)", "%", 12.0),
    "BX.KLT.DINV.CD.WD": ("Foreign direct investment, net inflows", "current US$", 1.2e9),
}


class StubWorldBank:
    """Returns plausible data for any known series, for any country."""

    def __init__(self, *, fail: set[str] | None = None, empty: set[str] | None = None):
        self.fail = fail or set()
        self.empty = empty or set()
        self.calls: list[dict] = []

    async def fetch_data(self, **params):
        self.calls.append(params)
        indicator = str(params.get("indicator"))
        if indicator in self.fail:
            raise RuntimeError(f"provider down for {indicator}")

        title, unit, base = SERIES.get(indicator, ("Unknown series", None, 1.0))
        geos = params.get("countries") or [params.get("country")]
        start = int(params.get("start_date") or 2000)
        end = int(params.get("end_date") or 2005)

        blocks = []
        for iso3 in geos:
            points = []
            if indicator not in self.empty:
                for offset, year in enumerate(range(start, min(end, 2024) + 1)):
                    points.append(DataPoint(date=f"{year}-01-01", value=base + offset))
            blocks.append(NormalizedData(
                metadata=Metadata(
                    source="World Bank", indicator=title,
                    country=COUNTRY_NAMES.get(str(iso3), str(iso3)),
                    frequency="annual", unit=unit or "", seriesId=indicator,
                    sourceUrl=f"https://data.worldbank.org/indicator/{indicator}",
                ),
                data=points,
            ))
        return blocks


def make_service(**kwargs) -> InstantDatasetService:
    return InstantDatasetService(
        ProviderGateway({"world_bank": StubWorldBank(**kwargs)}, timeout=10)
    )


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# Test A — one indicator, one country
# --------------------------------------------------------------------------


def test_a_search_returns_actual_rows_with_no_cart():
    result = run(make_service().build("Inflation in Algeria from 2000 to 2025"))

    assert result.has_data, "Search must produce data, not an interpretation"
    payload = summarise(result, "ds1")

    dataset = payload["dataset"]
    assert dataset is not None
    assert dataset["rows"] > 0
    assert dataset["preview"], "rows must be present in the first response"
    assert "inflation_pct" in dataset["columns"]


def test_a_resolution_is_shown_without_the_user_choosing_a_provider():
    result = run(make_service().build("Inflation in Algeria from 2000 to 2025"))
    assert len(result.resolution) == 1
    resolved = result.resolution[0]
    assert resolved.provider == "world_bank"
    assert resolved.series_id == "FP.CPI.TOTL.ZG"
    assert resolved.official_title
    assert resolved.confidence > 0.9
    assert resolved.reason


def test_a_export_formats_are_offered_immediately():
    payload = summarise(run(make_service().build("Inflation in Algeria 2000 to 2010")),
                        "ds1")
    assert "xlsx" in payload["available_exports"]


def test_a_no_indicator_code_or_iso_needed():
    """The query uses plain words only — no codes anywhere."""
    result = run(make_service().build("unemployment in Morocco from 2000 to 2010"))
    assert result.has_data
    assert result.resolution[0].series_id == "SL.UEM.TOTL.ZS"


# --------------------------------------------------------------------------
# Tests B and C — Arabic and French take the same path
# --------------------------------------------------------------------------


@pytest.mark.parametrize("query,language", [
    ("التضخم في الجزائر من 2000 إلى 2025", "ar"),
    ("Inflation en Algerie de 2000 a 2025", "fr"),
    ("Inflation in Algeria from 2000 to 2025", "en"),
])
def test_bc_trilingual_queries_all_return_data(query, language):
    result = run(make_service().build(query))
    payload = summarise(result, "ds1")

    assert result.has_data, f"{language} query returned no data"
    assert payload["understanding"]["language"] == language
    assert payload["understanding"]["iso3"] == ["DZA"]
    assert payload["dataset"]["rows"] > 0
    assert result.resolution[0].series_id == "FP.CPI.TOTL.ZG"


def test_c_french_gdp_per_capita():
    result = run(make_service().build("PIB par habitant en Algerie de 2000 a 2023"))
    assert result.has_data
    assert result.resolution[0].series_id == "NY.GDP.PCAP.CD"


# --------------------------------------------------------------------------
# Test D — multiple indicators and countries in one dataset
# --------------------------------------------------------------------------


def test_d_multi_indicator_multi_country_is_one_wide_dataset():
    result = run(make_service().build(
        "GDP per capita, unemployment and FDI for Morocco and Tunisia "
        "from 2000 to 2025"
    ))
    payload = summarise(result, "ds1")
    dataset = payload["dataset"]

    assert {r.concept for r in result.resolution} == {
        "gdp_per_capita", "unemployment", "fdi"}
    assert set(dataset["geographies"]) == {"MAR", "TUN"}
    assert dataset["shape"] == "wide"
    # One row per country-period, one column per indicator.
    value_columns = [c for c in dataset["columns"]
                     if c not in ("geography", "iso3", "period")]
    assert len(value_columns) == 3


def test_d_partial_failure_still_returns_the_successful_series():
    """One provider failure must not throw away the data that did arrive."""
    service = InstantDatasetService(
        ProviderGateway(
            {"world_bank": StubWorldBank(fail={"SL.UEM.TOTL.ZS"})}, timeout=10
        )
    )
    result = run(service.build(
        "GDP per capita and unemployment for Morocco from 2000 to 2010"))

    assert result.has_data
    assert "unemployment" in result.unresolved
    assert any("unemployment" in w for w in result.warnings)
    assert [r.concept for r in result.resolution] == ["gdp_per_capita"]


# --------------------------------------------------------------------------
# Test F — cart build actually builds
# --------------------------------------------------------------------------


def test_f_cart_build_produces_a_dataset_not_a_reparse():
    result = run(make_service().build_from_selection(
        series=[
            {"concept": "gdp_per_capita", "provider": "world_bank",
             "series_id": "NY.GDP.PCAP.CD"},
            {"concept": "inflation", "provider": "world_bank",
             "series_id": "FP.CPI.TOTL.ZG"},
        ],
        geographies=["DZA", "MAR"],
        start_year=2000,
        end_year=2010,
    ))

    assert result.has_data
    payload = summarise(result, "ds1")
    assert payload["dataset"]["rows"] > 0
    assert len(result.resolution) == 2
    assert all(r.confidence == 1.0 for r in result.resolution)


def test_f_cart_build_resolves_a_bare_concept_too():
    result = run(make_service().build_from_selection(
        series=[{"concept": "inflation"}],
        geographies=["DZA"], start_year=2000, end_year=2005,
    ))
    assert result.has_data
    assert result.resolution[0].series_id == "FP.CPI.TOTL.ZG"


def test_f_cart_build_needs_a_country():
    result = run(make_service().build_from_selection(
        series=[{"concept": "inflation"}], geographies=[]))
    assert not result.has_data
    assert any("country" in w.lower() for w in result.warnings)


# --------------------------------------------------------------------------
# Smart defaults and honest reporting
# --------------------------------------------------------------------------


def test_defaults_are_wide_annual_and_automatic_provider():
    result = run(make_service().build("Inflation in Algeria from 2000 to 2010"))
    assert result.parsed.spec.output_shape is OutputShape.WIDE
    assert result.parsed.spec.frequency is Frequency.ANNUAL
    assert result.parsed.spec.preferred_sources == []


def test_requesting_a_future_year_warns_instead_of_failing():
    """Requested 2025, source ends 2024: return data and say so."""
    result = run(make_service().build("Inflation in Algeria from 2000 to 2025"))
    assert result.has_data
    assert any("2025" in w and "not yet available" in w for w in result.warnings)


def test_missing_country_is_explained_not_silently_empty():
    result = run(make_service().build("inflation"))
    assert not result.has_data
    assert any("country" in w.lower() for w in result.warnings)


def test_unrecognised_concept_is_explained():
    result = run(make_service().build("blorptrons in Algeria"))
    assert not result.has_data
    assert result.warnings


def test_explicit_provider_is_honoured():
    result = run(make_service().build(
        "Inflation in Algeria from 2000 to 2010", provider="world_bank"))
    assert result.has_data
    assert result.resolution[0].provider == "world_bank"


def test_long_shape_is_available_on_request():
    result = run(make_service().build(
        "Inflation in Algeria from 2000 to 2010", output_shape=OutputShape.LONG))
    payload = summarise(result, "ds1")
    assert payload["dataset"]["shape"] == "long"
    assert "indicator" in payload["dataset"]["columns"]


def test_payload_carries_everything_the_ui_needs():
    payload = summarise(run(make_service().build(
        "Inflation in Algeria from 2000 to 2010")), "ds1")
    for key in ("query", "understanding", "resolution", "dataset",
                "warnings", "unresolved", "available_exports"):
        assert key in payload
    dataset = payload["dataset"]
    for key in ("dataset_id", "name", "rows", "columns", "preview",
                "geographies", "period", "frequency", "shape", "quality"):
        assert key in dataset


def test_preview_is_capped_but_truncation_is_declared():
    payload = summarise(
        run(make_service().build("Inflation in Algeria from 2000 to 2024")),
        "ds1", preview_limit=3,
    )
    assert len(payload["dataset"]["preview"]) == 3
    assert payload["dataset"]["truncated"] is True


def test_dataset_name_is_human_friendly():
    result = run(make_service().build("Inflation in Algeria from 2000 to 2010"))
    assert result.dataset is not None
    name = result.dataset.name
    assert "inflation" in name
    assert "dza" in name
    assert " " not in name


def test_gaps_are_never_imputed():
    """An empty provider response must not become zeros."""
    service = InstantDatasetService(
        ProviderGateway(
            {"world_bank": StubWorldBank(empty={"SL.UEM.TOTL.ZS"})}, timeout=10
        )
    )
    result = run(service.build(
        "GDP per capita and unemployment for Morocco from 2000 to 2010"))
    # The empty series is reported as unresolved rather than filled with 0.
    assert "unemployment" in result.unresolved
    assert result.has_data
