"""Provider gateway tests (spec 6.4, 36).

Verifies the translation from connector ``NormalizedData`` into canonical
models, and that provider failure is reported as structured data rather than
raising. Uses stub connectors -- no network.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.models import DataPoint, Metadata, NormalizedData
from backend.smatecondata.core.models import Frequency
from backend.smatecondata.datasets.builder import DatasetBuilder
from backend.smatecondata.core.models import DatasetSpec
from backend.smatecondata.providers.gateway import (
    ProviderGateway,
    adapt,
    canonical_period,
    normalise_provider_key,
)


def make_block(*, source="World Bank", indicator="GDP per capita (current US$)",
               country="Algeria", frequency="annual", unit="current US$",
               series_id="NY.GDP.PCAP.CD", points=None,
               source_url="https://data.worldbank.org/indicator/NY.GDP.PCAP.CD"):
    return NormalizedData(
        metadata=Metadata(
            source=source, indicator=indicator, country=country,
            frequency=frequency, unit=unit, seriesId=series_id,
            sourceUrl=source_url, lastUpdated="2026-01-15",
        ),
        data=[DataPoint(date=d, value=v) for d, v in (points or [])],
    )


class StubConnector:
    """Minimal stand-in for a provider connector."""

    def __init__(self, payload=None, *, raises=None, delay=0.0):
        self.payload = payload
        self.raises = raises
        self.delay = delay
        self.calls: list[dict] = []

    async def fetch_data(self, **params):
        self.calls.append(params)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise self.raises
        return self.payload


# --------------------------------------------------------------------------
# Period canonicalisation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("raw,frequency,expected", [
    ("2020-01-01", Frequency.ANNUAL, "2020"),
    ("2020-07-01", Frequency.QUARTERLY, "2020Q3"),
    ("2020-10-01", Frequency.QUARTERLY, "2020Q4"),
    ("2020-07-01", Frequency.MONTHLY, "2020-07"),
    ("2020-07", Frequency.ANNUAL, "2020"),
    ("2020-07", Frequency.QUARTERLY, "2020Q3"),
    ("2020Q2", Frequency.ANNUAL, "2020"),
    ("2020", Frequency.ANNUAL, "2020"),
    ("2020-11-15", Frequency.DAILY, "2020-11-15"),
])
def test_canonical_period(raw, frequency, expected):
    assert canonical_period(raw, frequency) == expected


def test_period_canonicalisation_keeps_the_pivot_square():
    """Annual series reporting ISO dates must not fragment the wide table."""
    block = make_block(points=[("2000-01-01", 1.0), ("2001-01-01", 2.0)])
    _, observations = adapt(block)
    assert [o.period for o in observations] == ["2000", "2001"]


@pytest.mark.parametrize("source,key", [
    ("World Bank", "world_bank"),
    ("worldbank", "world_bank"),
    ("UN Comtrade", "comtrade"),
    ("Statistics Canada", "statscan"),
    ("FRED", "fred"),
    (None, "unknown"),
])
def test_provider_key_normalisation(source, key):
    assert normalise_provider_key(source) == key


# --------------------------------------------------------------------------
# Adaptation
# --------------------------------------------------------------------------


def test_adapt_produces_canonical_metadata():
    metadata, observations = adapt(
        make_block(points=[("2000-01-01", 1800.0), ("2001-01-01", 1920.0)]))

    assert metadata.provider == "world_bank"
    assert metadata.series_id == "NY.GDP.PCAP.CD"
    assert metadata.title == "GDP per capita (current US$)"
    assert metadata.frequency is Frequency.ANNUAL
    assert metadata.unit == "current US$"
    assert metadata.source_reference.startswith("https://")
    assert metadata.geographies == ["DZA"]
    assert len(observations) == 2
    assert observations[0].iso3 == "DZA"
    assert observations[0].geography == "Algeria"


def test_adapt_derives_coverage_from_what_arrived_not_a_claim():
    metadata, _ = adapt(make_block(points=[
        ("2000-01-01", 1.0), ("2001-01-01", None), ("2004-01-01", 4.0)]))
    assert metadata.coverage_start == "2000"
    assert metadata.coverage_end == "2004"


def test_adapt_preserves_a_gap_as_none():
    _, observations = adapt(make_block(points=[
        ("2000-01-01", 1.0), ("2001-01-01", None)]))
    assert observations[1].value is None


def test_adapt_handles_an_unknown_country_without_inventing_iso3():
    metadata, observations = adapt(make_block(country="Atlantis"))
    assert metadata.geographies == []
    assert observations == []


# --------------------------------------------------------------------------
# Gateway: success, partial success, failure (spec 36)
# --------------------------------------------------------------------------


def test_fetch_series_success():
    connector = StubConnector(make_block(points=[("2000-01-01", 1800.0)]))
    gateway = ProviderGateway({"world_bank": connector})

    outcome = asyncio.run(gateway.fetch_series(
        "world_bank", "NY.GDP.PCAP.CD", ["DZA"], start_year=2000, end_year=2001))

    assert outcome.ok
    assert outcome.result.status == "success"
    assert outcome.result.failed_geographies == []
    assert connector.calls[0]["country"] == "DZA"
    assert connector.calls[0]["start_date"] == "2000"


def test_fetch_series_reports_partial_success():
    """A country that returned nothing must be named, not silently dropped."""
    connector = StubConnector([
        make_block(country="Algeria", points=[("2000-01-01", 1800.0)]),
        make_block(country="Morocco", points=[("2000-01-01", None)]),
    ])
    gateway = ProviderGateway({"world_bank": connector})

    outcome = asyncio.run(gateway.fetch_series(
        "world_bank", "NY.GDP.PCAP.CD", ["DZA", "MAR", "TUN"]))

    assert outcome.result.status == "partial_success"
    assert set(outcome.result.failed_geographies) == {"MAR", "TUN"}
    assert outcome.result.successful_geographies == 1
    assert "no observations" in outcome.result.message


def test_fetch_series_handles_a_connector_exception():
    gateway = ProviderGateway({
        "world_bank": StubConnector(raises=RuntimeError("upstream 503"))})

    outcome = asyncio.run(gateway.fetch_series("world_bank", "X", ["DZA"]))

    assert not outcome.ok
    assert outcome.result.status == "failed"
    assert "upstream 503" in outcome.result.message


def test_fetch_series_times_out_without_hanging():
    gateway = ProviderGateway(
        {"world_bank": StubConnector(make_block(), delay=0.5)}, timeout=0.05)

    outcome = asyncio.run(gateway.fetch_series("world_bank", "X", ["DZA"]))

    assert outcome.result.status == "failed"
    assert "timed out" in outcome.result.message


def test_unknown_provider_is_reported_not_raised():
    outcome = asyncio.run(
        ProviderGateway({}).fetch_series("nope", "X", ["DZA"]))
    assert outcome.result.status == "failed"
    assert "not configured" in outcome.result.message


def test_empty_response_is_a_failure_not_an_empty_dataset():
    gateway = ProviderGateway({"world_bank": StubConnector(make_block(points=[]))})
    outcome = asyncio.run(gateway.fetch_series("world_bank", "X", ["DZA"]))
    assert not outcome.ok
    assert outcome.result.status == "failed"


def test_multi_country_uses_the_countries_parameter():
    connector = StubConnector(make_block(points=[("2000-01-01", 1.0)]))
    gateway = ProviderGateway({"world_bank": connector})
    asyncio.run(gateway.fetch_series("world_bank", "X", ["DZA", "MAR"]))
    assert connector.calls[0]["countries"] == ["DZA", "MAR"]
    assert "country" not in connector.calls[0]


# --------------------------------------------------------------------------
# fetch_many
# --------------------------------------------------------------------------


def test_fetch_many_keeps_order_and_isolates_failures():
    gateway = ProviderGateway({
        "world_bank": StubConnector(make_block(points=[("2000-01-01", 1.0)])),
        "imf": StubConnector(raises=RuntimeError("imf down")),
    })

    outcomes = asyncio.run(gateway.fetch_many(
        [("world_bank", "NY.GDP.PCAP.CD", "gdp_per_capita"),
         ("imf", "PCPIPCH", "inflation")],
        ["DZA"]))

    assert [concept for concept, _ in outcomes] == ["gdp_per_capita", "inflation"]
    assert outcomes[0][1].ok                      # one provider down
    assert not outcomes[1][1].ok                  # does not sink the other
    assert outcomes[1][1].result.status == "failed"


def test_gateway_output_feeds_the_dataset_builder():
    """The whole point: live provider output must build a real dataset."""
    gateway = ProviderGateway({
        "world_bank": StubConnector([
            make_block(country="Algeria",
                       points=[("2000-01-01", 1800.0), ("2001-01-01", 1920.0)]),
            make_block(country="Morocco",
                       points=[("2000-01-01", 1500.0), ("2001-01-01", 1600.0)]),
        ]),
    })
    outcome = asyncio.run(gateway.fetch_series(
        "world_bank", "NY.GDP.PCAP.CD", ["DZA", "MAR"],
        start_year=2000, end_year=2001))

    spec = DatasetSpec(geographies=["DZA", "MAR"], start_year=2000, end_year=2001)
    dataset = (DatasetBuilder(spec, name="live")
               .add_series(outcome.metadata, outcome.observations, "gdp_per_capita")
               .build())

    columns, rows = dataset.to_wide()
    assert columns == ["geography", "iso3", "period", "gdp_per_capita_usd"]
    assert len(rows) == 4
    assert dataset.lineage[0].provider == "world_bank"
    assert dataset.lineage[0].citation


def test_from_query_service_collects_available_connectors():
    class FakeService:
        world_bank_provider = StubConnector()
        fred_provider = StubConnector()
        # imf_provider deliberately absent

    gateway = ProviderGateway.from_query_service(FakeService())
    assert "world_bank" in gateway.available
    assert "fred" in gateway.available
    assert "imf" not in gateway.available
