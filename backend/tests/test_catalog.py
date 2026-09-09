"""Catalogue index and scale-statistics tests (spec 0.1A, 6.2, 26).

Two things are pinned here. First, that trilingual discovery actually reaches
the real provider catalogue rather than a curated concept list. Second — and
more important — that catalogue counts are computed from the live index and
that a milestone badge is never claimed before it is earned.
"""

from __future__ import annotations

import pytest

from backend.smatecondata.catalog.index import CatalogIndex, get_catalog_index
from backend.smatecondata.catalog.stats import (
    EXPANSION_TARGET,
    LEGACY_FLOOR,
    STRETCH_TARGET,
    CatalogStats,
    CatalogStatsService,
)


@pytest.fixture(scope="module")
def index() -> CatalogIndex:
    return get_catalog_index()


@pytest.fixture(scope="module")
def stats(index) -> CatalogStats:
    return CatalogStatsService(index=index).compute()


# --------------------------------------------------------------------------
# Index
# --------------------------------------------------------------------------


def test_catalogue_loads_every_bundled_provider(index):
    providers = index.providers()
    assert set(providers) >= {
        "world_bank", "fred", "eurostat", "oecd", "imf", "statscan", "bis",
    }
    assert len(index) > 40_000, "the bundled catalogue should be tens of thousands"


def test_exact_provider_code_wins(index):
    """An advanced user typing a code must get that series first (spec 0B.8)."""
    hits = index.search("NY.GDP.PCAP.CD", limit=5)
    assert hits
    assert hits[0].metadata.series_id == "NY.GDP.PCAP.CD"
    assert hits[0].metadata.provider == "world_bank"
    assert "code" in hits[0].matched_on
    # Decisively first, not narrowly.
    assert hits[0].score > hits[1].score * 5


def test_title_beats_a_passing_description_mention(index):
    """Regression: a long description mentioning a phrase outranked the series
    actually titled with it."""
    hits = index.search("inflation consumer prices", limit=5)
    titles = [h.metadata.title.lower() for h in hits]
    assert any(t.startswith("inflation, consumer prices") for t in titles)


def test_canonical_series_are_findable_by_plain_english(index):
    for query, expected in [
        ("inflation consumer prices", "FP.CPI.TOTL.ZG"),
        ("foreign direct investment net inflows", "BN.KLT.DINV.CD.DRS"),
    ]:
        ids = [h.metadata.series_id for h in index.search(query, limit=10)]
        assert expected in ids, f"{expected} missing for {query!r}: {ids[:5]}"


@pytest.mark.parametrize(
    "query,needle",
    [
        ("التضخم", "inflation"),
        ("معدل البطالة", "unemployment"),
        ("الناتج المحلي الإجمالي للفرد", "gdp per capita"),
        ("taux de chomage", "unemployment"),
        ("PIB par habitant", "gdp per capita"),
        ("dette publique", "debt"),
    ],
)
def test_arabic_and_french_reach_the_english_catalogue(index, query, needle):
    """The catalogue is written in English; AR/FR must still find it."""
    hits = index.search(query, limit=5)
    assert hits, f"no catalogue hits for {query!r}"
    titles = " ".join(h.metadata.title.lower() for h in hits)
    assert needle in titles, f"{needle!r} not in top hits for {query!r}"


def test_trilingual_queries_score_comparably(index):
    """AR/FR must not be relegated to a bare concept-membership score."""
    english = index.search("GDP per capita", limit=1)
    french = index.search("PIB par habitant", limit=1)
    arabic = index.search("الناتج المحلي الإجمالي للفرد", limit=1)

    assert english and french and arabic
    # Within the same order of magnitude, not 40 vs 280.
    assert french[0].score > english[0].score * 0.5
    assert arabic[0].score > english[0].score * 0.5


def test_search_explains_why_something_matched(index):
    hits = index.search("unemployment rate", limit=3)
    assert all(hit.matched_on for hit in hits)


def test_provider_filter(index):
    hits = index.search("inflation", providers=["fred"], limit=10)
    assert hits
    assert {h.metadata.provider for h in hits} == {"fred"}


def test_empty_query_returns_nothing(index):
    assert index.search("   ") == []


def test_unknown_series_lookup_returns_none(index):
    assert index.get("world_bank", "NOT.A.REAL.CODE") is None


def test_known_series_lookup(index):
    metadata = index.get("world_bank", "NY.GDP.PCAP.CD")
    assert metadata is not None
    assert metadata.title.startswith("GDP per capita")
    assert metadata.source_reference and metadata.source_reference.startswith("https://")


# --------------------------------------------------------------------------
# Statistics and the truth-in-marketing rule (spec 0.1A)
# --------------------------------------------------------------------------


def test_counts_come_from_the_index_not_a_constant(stats, index):
    assert stats.searchable_series_count == len(index)
    assert stats.provider_count == len(index.providers())


def test_breakdowns_sum_to_the_total(stats):
    assert sum(stats.provider_breakdown.values()) == stats.searchable_series_count
    assert sum(stats.frequency_breakdown.values()) == stats.searchable_series_count


def test_last_sync_is_reported(stats):
    assert stats.last_catalog_sync is not None


def test_raw_and_indexed_counts_are_tracked_separately(stats):
    """Spec 0.1A asks for raw provider counts and indexed counts apart."""
    assert stats.raw_provider_series_count > 0
    assert stats.deduplicated_concept_count >= 0


@pytest.mark.parametrize(
    "count,expected",
    [
        (0, "below_floor"),
        (LEGACY_FLOOR - 1, "below_floor"),
        (LEGACY_FLOOR, "floor"),
        (EXPANSION_TARGET - 1, "floor"),
        (EXPANSION_TARGET, "expansion"),
        (STRETCH_TARGET - 1, "expansion"),
        (STRETCH_TARGET, "stretch"),
    ],
)
def test_milestone_bands(count, expected):
    assert CatalogStats(searchable_series_count=count).milestone() == expected


def test_never_claims_a_million_before_reaching_it():
    """The headline rule of spec 0.1A."""
    for count in (0, 41_767, LEGACY_FLOOR, EXPANSION_TARGET, STRETCH_TARGET - 1):
        rendered = CatalogStats(searchable_series_count=count).display_count()
        assert "1M+" not in rendered
        assert f"{count:,}" in rendered


def test_uses_the_compact_label_only_once_earned():
    assert "1M+" in CatalogStats(searchable_series_count=STRETCH_TARGET).display_count()
    assert "1M+" in CatalogStats(searchable_series_count=2_500_000).display_count()


def test_next_milestone_points_at_the_right_target():
    below = CatalogStats(searchable_series_count=41_767).next_milestone()
    assert below["name"] == "legacy_floor"
    assert below["target"] == LEGACY_FLOOR
    assert below["remaining"] == LEGACY_FLOOR - 41_767

    top = CatalogStats(searchable_series_count=STRETCH_TARGET).next_milestone()
    assert top is None


def test_stats_payload_is_complete(stats):
    payload = stats.as_dict()
    for key in (
        "searchable_series_count", "raw_provider_series_count",
        "deduplicated_concept_count", "provider_count", "concept_count",
        "country_or_area_count", "frequency_breakdown", "provider_breakdown",
        "last_catalog_sync", "new_series_last_30_days",
        "updated_series_last_30_days", "display_count", "milestone",
        "next_milestone", "thresholds",
    ):
        assert key in payload, f"missing stat: {key}"


def test_bundled_catalogue_is_honestly_below_the_legacy_floor(stats):
    """The repository ships ~42K series, not the 331K production floor.

    Reporting that honestly IS the requirement (spec 0.1A: "never fabricate
    the count"). If a future catalogue sync lifts the index past the floor,
    this assertion flips and the milestone must move with it.
    """
    if stats.searchable_series_count < LEGACY_FLOOR:
        assert stats.milestone() == "below_floor"
        assert stats.meets_legacy_floor is False
        assert "1M+" not in stats.display_count()
    else:
        assert stats.milestone() in {"floor", "expansion", "stretch"}
        assert stats.meets_legacy_floor is True
