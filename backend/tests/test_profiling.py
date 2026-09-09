"""Automatic-EDA tests (spec 10A).

The load-bearing requirement is the negative one: profiling is optional, and a
missing or broken profiling backend must never destroy the user's dataset.
These tests therefore pass whether or not ``fg-data-profiling`` imports.
"""

from __future__ import annotations

import os
import secrets

import pytest

os.environ.setdefault("JWT_SECRET", secrets.token_hex(32))
os.environ.setdefault("DISABLE_MCP", "true")

from backend.smatecondata.analytics.profiling import (  # noqa: E402
    ProfileMode,
    ProfileStatus,
    ProfilingService,
    profiling_available,
)
from backend.smatecondata.core.models import (  # noqa: E402
    DatasetSpec,
    Frequency,
    IndicatorMetadata,
    Observation,
)
from backend.smatecondata.datasets.builder import DatasetBuilder  # noqa: E402

GDP = IndicatorMetadata(
    provider="world_bank", series_id="NY.GDP.PCAP.CD",
    title="GDP per capita (current US$)", unit="current US$",
    frequency=Frequency.ANNUAL)

CPI = IndicatorMetadata(
    provider="imf", series_id="PCPIPCH", title="Inflation",
    unit="Annual %", frequency=Frequency.ANNUAL)

NAMES = {"DZA": "Algeria", "MAR": "Morocco"}


def obs(meta, iso3, period, value):
    return Observation(
        provider=meta.provider, series_id=meta.series_id,
        geography=NAMES[iso3], iso3=iso3, period=str(period), value=value,
        unit=meta.unit, frequency=Frequency.ANNUAL)


@pytest.fixture
def dataset():
    spec = DatasetSpec(geographies=["DZA", "MAR"], start_year=2000,
                       end_year=2004, frequency=Frequency.ANNUAL)
    gdp = [obs(GDP, g, y, 1000.0 + i * 50)
           for g in ("DZA", "MAR") for i, y in enumerate(range(2000, 2005))]
    # One hole, so coverage is not trivially 100%.
    cpi = [obs(CPI, "DZA", y, 2.0) for y in (2000, 2001, 2003, 2004)]
    cpi += [obs(CPI, "MAR", y, 1.5) for y in range(2000, 2005)]
    return (DatasetBuilder(spec, name="profile_test")
            .add_series(GDP, gdp, "gdp_per_capita")
            .add_series(CPI, cpi, "inflation")
            .build())


@pytest.fixture
def service():
    return ProfilingService()


# --------------------------------------------------------------------------
# Economic summary -- always available, no third-party dependency
# --------------------------------------------------------------------------


def test_economic_summary_needs_no_third_party_package(service, dataset):
    summary = service.economic_summary(dataset)
    assert summary["rows"] == 10
    assert summary["geography_count"] == 2
    assert summary["periods"] == {"first": "2000", "last": "2004", "count": 5}


def test_summary_identifies_indicator_roles(service, dataset):
    summary = service.economic_summary(dataset)
    columns = {c["column"]: c for c in summary["indicator_columns"]}
    assert set(columns) == {"gdp_per_capita_usd", "inflation_pct"}
    assert columns["gdp_per_capita_usd"]["provider"] == "world_bank"
    assert columns["gdp_per_capita_usd"]["series_id"] == "NY.GDP.PCAP.CD"
    assert columns["inflation_pct"]["unit"] == "Annual %"


def test_summary_detects_a_balanced_panel(service, dataset):
    panel = service.economic_summary(dataset)["panel"]
    assert panel["expected_rows"] == 10
    assert panel["actual_rows"] == 10
    assert panel["balanced"] is True
    assert panel["duplicate_country_period_keys"] == 0


def test_summary_detects_an_unbalanced_panel(service):
    spec = DatasetSpec(geographies=["DZA", "MAR"], start_year=2000, end_year=2002)
    rows = [obs(GDP, "DZA", y, 1.0) for y in range(2000, 2003)]
    rows += [obs(GDP, "MAR", 2000, 2.0)]          # Morocco only has one year
    dataset = DatasetBuilder(spec).add_series(GDP, rows, "gdp").build()

    panel = service.economic_summary(dataset)["panel"]
    assert panel["expected_rows"] == 6
    assert panel["actual_rows"] == 4
    assert panel["balanced"] is False


def test_summary_reports_coverage_per_variable(service, dataset):
    summary = service.economic_summary(dataset)
    assert summary["coverage_by_variable"]["gdp_per_capita_usd"] == 100.0
    assert summary["coverage_by_variable"]["inflation_pct"] == 90.0
    assert summary["missing_cells"] == 1


def test_summary_lists_units_and_frequencies(service, dataset):
    summary = service.economic_summary(dataset)
    assert summary["frequencies"] == ["annual"]
    assert set(summary["units"]) == {"current US$", "Annual %"}


def test_summary_states_the_descriptive_boundary(service, dataset):
    assert "No model estimation" in service.economic_summary(dataset)["note"]


# --------------------------------------------------------------------------
# Profiling never breaks the dataset (the spec-10A guarantee)
# --------------------------------------------------------------------------


def test_profile_always_returns_a_result_never_raises(service, dataset):
    result = service.profile_dataset(dataset)
    assert result.status in {
        ProfileStatus.OK, ProfileStatus.UNAVAILABLE, ProfileStatus.FAILED}
    # Whatever happened, the economic summary is still there.
    assert result.summary["economic"]["rows"] == 10


def test_unavailable_backend_still_returns_the_economic_summary(service, dataset,
                                                                monkeypatch):
    monkeypatch.setattr(
        "backend.smatecondata.analytics.profiling.profiling_available",
        lambda: (False, "simulated: package missing"))

    result = service.profile_dataset(dataset)
    assert result.status is ProfileStatus.UNAVAILABLE
    assert result.message == "simulated: package missing"
    assert result.summary["economic"]["coverage_pct"] == 95.0


def test_a_raising_backend_is_caught(service, dataset, monkeypatch):
    """A third-party failure must not destroy the retrieved dataset."""
    monkeypatch.setattr(
        "backend.smatecondata.analytics.profiling.profiling_available",
        lambda: (True, None))

    import sys
    import types

    stub = types.ModuleType("data_profiling")

    def explode(*args, **kwargs):
        raise RuntimeError("profiling blew up")

    stub.ProfileReport = explode
    monkeypatch.setitem(sys.modules, "data_profiling", stub)

    result = service.profile_dataset(dataset)
    assert result.status is ProfileStatus.FAILED
    assert "profiling blew up" in result.message
    assert result.summary["economic"]["rows"] == 10


def test_empty_dataset_is_skipped_not_failed(service):
    spec = DatasetSpec(geographies=["DZA"], start_year=2000, end_year=2000)
    dataset = DatasetBuilder(spec).add_series(GDP, [], "gdp").build()
    result = service.profile_dataset(dataset)
    assert result.status is ProfileStatus.SKIPPED


def test_availability_reason_is_actionable():
    available, reason = profiling_available()
    if not available:
        assert reason
        # Whatever the cause, the user is told how to fix it.
        assert "pip install" in reason


def test_large_dataset_triggers_disclosed_sampling(monkeypatch):
    """Sampling must be disclosed, never silent."""
    service = ProfilingService(sample_threshold=10, sample_rows=5)
    spec = DatasetSpec(geographies=["DZA"], start_year=2000, end_year=2029)
    rows = [obs(GDP, "DZA", y, float(y)) for y in range(2000, 2030)]
    dataset = DatasetBuilder(spec).add_series(GDP, rows, "gdp").build()

    result = service.profile_dataset(dataset)
    if result.status is ProfileStatus.OK:
        assert result.sampled is True
        assert result.sample_rows == 5
        assert "sample" in (result.message or "").lower()
    else:
        pytest.skip(f"profiling backend unavailable: {result.message}")


def test_result_serialises_for_the_api(service, dataset):
    payload = service.profile_dataset(dataset).as_dict()
    for key in ("status", "mode", "sampled", "total_rows", "summary"):
        assert key in payload
    assert payload["mode"] == ProfileMode.QUICK.value


# --------------------------------------------------------------------------
# REST surface
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from backend.main import app
    return TestClient(app)


def _make_dataset_via_api(client):
    payload = {
        "name": "profiling_api",
        "query": "GDP per capita for Algeria from 2000 to 2004",
        "series": [{
            "metadata": {
                "provider": "world_bank", "series_id": "NY.GDP.PCAP.CD",
                "title": "GDP per capita (current US$)", "unit": "current US$",
                "frequency": "annual",
            },
            "concept": "gdp_per_capita",
            "observations": [{
                "provider": "world_bank", "series_id": "NY.GDP.PCAP.CD",
                "geography": "Algeria", "iso3": "DZA", "period": str(y),
                "value": 1800.0 + i * 100, "unit": "current US$",
                "frequency": "annual",
            } for i, y in enumerate(range(2000, 2005))],
        }],
    }
    response = client.post("/api/v1/datasets", json=payload)
    assert response.status_code == 201
    return response.json()["dataset_id"]


def test_profiling_status_endpoint(client):
    body = client.get("/api/v1/profiling/status").json()
    assert isinstance(body["available"], bool)
    assert body["modes"] == ["quick", "full"]
    if not body["available"]:
        assert body["reason"]


def test_profile_endpoint_degrades_but_never_500s(client):
    dataset_id = _make_dataset_via_api(client)
    response = client.post(f"/api/v1/datasets/{dataset_id}/profile")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "unavailable", "failed"}
    assert body["summary"]["economic"]["rows"] == 5


def test_profile_summary_endpoint_always_works(client):
    dataset_id = _make_dataset_via_api(client)
    body = client.get(f"/api/v1/datasets/{dataset_id}/profile/summary").json()
    summary = body["summary"]
    assert summary["geography_count"] == 1
    assert summary["panel"]["balanced"] is True
