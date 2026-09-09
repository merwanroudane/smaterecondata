"""Tri-lingual discovery tests (spec 0B.1 "Multilingual quality tests").

These cover the requirement that Arabic, English and French queries for the
same economic concept resolve to the same canonical concept, that geography
and periods are extracted identically in all three languages, and that the
deterministic parser needs no LLM.

Deliberately import-light: nothing here touches a provider, the network, or
``curl_cffi``.
"""

from __future__ import annotations

import pytest

from backend.smatecondata.core.models import Frequency, Language, OutputShape
from backend.smatecondata.search.aliases import get_concept_store
from backend.smatecondata.search.geography import (
    extract_years,
    get_geography_resolver,
)
from backend.smatecondata.search.normalize import (
    detect_language,
    normalize,
    normalize_arabic,
    normalize_french,
)
from backend.smatecondata.search.query_parser import parse_query


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected_fragment",
    [
        ("التضخم", "تضخم"),          # definite article stripped
        ("الجزائر", "جزاير"),         # hamza/alef + ya folded
        ("البطالة", "بطاله"),         # ta marbuta -> ha
        ("٢٠٢٥", "2025"),             # Arabic-Indic digits
        ("الاقتصــاد", "اقتصاد"),      # tatweel removed
    ],
)
def test_arabic_normalisation(raw, expected_fragment):
    assert expected_fragment in normalize_arabic(raw)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Algérie", "algerie"),
        ("d'investissement", "investissement"),
        ("les données", "donnee"),
        ("Produit Intérieur Brut", "produit interieur brut"),
    ],
)
def test_french_normalisation(raw, expected):
    assert normalize_french(raw) == expected


@pytest.mark.parametrize(
    "text,language",
    [
        ("معدل البطالة", Language.AR),
        ("أريد GDP في الجزائر", Language.AR),   # mixed script stays Arabic
        ("taux de chomage", Language.FR),
        ("PIB par habitant en Algerie", Language.FR),
        ("unemployment rate", Language.EN),
        ("gross domestic product", Language.EN),
    ],
)
def test_language_detection(text, language):
    assert detect_language(text) is language


def test_normalisation_does_not_empty_a_stopword_only_phrase():
    """Never normalise a query down to nothing -- it would match everything."""
    assert normalize("the of for") != ""


# --------------------------------------------------------------------------
# Cross-language concept equivalence  (the headline spec 0B.1 requirement)
# --------------------------------------------------------------------------


EQUIVALENT_QUERIES = [
    ("gdp_per_capita",
     "GDP per capita in Algeria",
     "PIB par habitant en Algerie",
     "الناتج المحلي الإجمالي للفرد في الجزائر"),
    ("unemployment",
     "unemployment rate",
     "taux de chomage",
     "معدل البطالة"),
    ("fdi",
     "foreign direct investment",
     "investissements directs etrangers",
     "الاستثمار الأجنبي المباشر"),
    ("inflation",
     "inflation in Algeria",
     "inflation en Algerie",
     "التضخم في الجزائر"),
    ("public_debt",
     "public debt",
     "dette publique",
     "الدين العام"),
    ("exchange_rate",
     "exchange rate",
     "taux de change",
     "سعر الصرف"),
]


@pytest.mark.parametrize("concept_key,en,fr,ar", EQUIVALENT_QUERIES)
def test_same_concept_across_three_languages(concept_key, en, fr, ar):
    store = get_concept_store()
    for query in (en, fr, ar):
        hits = store.find_in_query(query)
        assert hits, f"no concept found for {query!r}"
        assert hits[0].key == concept_key, (
            f"{query!r} resolved to {hits[0].key}, expected {concept_key}"
        )


def test_longer_alias_wins_over_substring():
    """`GDP per capita` must not also register a bare `gdp` column."""
    keys = [c.key for c in get_concept_store().find_in_query("GDP per capita")]
    assert keys[0] == "gdp_per_capita"
    assert "gdp" not in keys


def test_french_par_habitant_does_not_leak_population():
    keys = [c.key for c in get_concept_store().find_in_query("PIB par habitant")]
    assert "population" not in keys


def test_broad_concepts_are_flagged_ambiguous():
    """Spec 6.3: broad words must never be silently mapped to one series."""
    store = get_concept_store()
    for key in ("gdp", "inflation", "unemployment", "public_debt",
                "exchange_rate", "interest_rate", "fdi"):
        concept = store.get(key)
        assert concept is not None, key
        assert concept.ambiguous, f"{key} should be flagged ambiguous"
        assert concept.distinctions, f"{key} needs distinctions to explain"


# --------------------------------------------------------------------------
# Geography
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,iso3",
    [
        ("Inflation in Algeria", "DZA"),
        ("Inflation en Algerie", "DZA"),
        ("التضخم في الجزائر", "DZA"),
        ("unemployment in Morocco", "MAR"),
        ("chomage au Maroc", "MAR"),
        ("البطالة في المغرب", "MAR"),
    ],
)
def test_country_resolution_across_languages(query, iso3):
    matches = get_geography_resolver().find_in_query(query)
    assert [m.iso3 for m in matches] == [iso3]


@pytest.mark.parametrize(
    "query",
    ["GDP for Maghreb countries", "PIB des pays du Maghreb",
     "الناتج المحلي في المغرب العربي"],
)
def test_region_preset_expands_identically(query):
    matches = get_geography_resolver().find_in_query(query)
    assert [m.iso3 for m in matches] == ["DZA", "MAR", "TUN", "LBY", "MRT"]
    assert all(m.via_region == "maghreb" for m in matches)


def test_longest_country_alias_wins():
    """`South Africa` must resolve to ZAF, never to a bare `Africa` region."""
    matches = get_geography_resolver().find_in_query("unemployment in South Africa")
    assert [m.iso3 for m in matches] == ["ZAF"]


@pytest.mark.parametrize(
    "query,expected",
    [
        ("from 2000 to 2025", (2000, 2025)),
        ("de 2000 a 2025", (2000, 2025)),
        ("من ٢٠٠٠ إلى ٢٠٢٥", (2000, 2025)),
        ("in 1995", (1995, None)),
        ("no years here", (None, None)),
    ],
)
def test_year_extraction(query, expected):
    assert extract_years(query) == expected


# --------------------------------------------------------------------------
# End-to-end parse  (spec 0A.1 Direct Search Contract)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "Inflation in Algeria from 2000 to 2025",
        "Inflation en Algerie de 2000 a 2025",
        "التضخم في الجزائر من 2000 إلى 2025",
    ],
)
def test_direct_search_contract_one_query_is_enough(query):
    """A concept + geography + period query must fully resolve in one action."""
    parsed = parse_query(query)
    spec = parsed.spec

    assert spec.geographies == ["DZA"]
    assert [i.concept for i in spec.indicators] == ["inflation"]
    assert spec.start_year == 2000
    assert spec.end_year == 2025
    # Sensible defaults, not a blocking wizard.
    assert spec.frequency is Frequency.ANNUAL
    assert spec.output_shape is OutputShape.WIDE
    assert spec.export_formats == ["xlsx"]


def test_multi_indicator_multi_country_request():
    parsed = parse_query(
        "Get GDP per capita, unemployment, FDI inflows and public debt for "
        "Algeria, Morocco, Tunisia, Egypt and Jordan from 1995 to 2025. "
        "Use annual official data."
    )
    spec = parsed.spec
    assert set(spec.geographies) == {"DZA", "MAR", "TUN", "EGY", "JOR"}
    assert set(i.concept for i in spec.indicators) == {
        "gdp_per_capita", "unemployment", "fdi", "public_debt",
    }
    assert (spec.start_year, spec.end_year) == (1995, 2025)
    assert spec.frequency is Frequency.ANNUAL


def test_provider_preference_is_detected():
    parsed = parse_query("unemployment in Morocco from World Bank")
    assert parsed.spec.preferred_sources == ["world_bank"]


def test_frequency_wording_in_three_languages():
    assert parse_query("GDP in France, quarterly").spec.frequency is Frequency.QUARTERLY
    assert parse_query("PIB en France, trimestriel").spec.frequency is Frequency.QUARTERLY
    assert parse_query("الناتج المحلي في فرنسا ربع سنوي").spec.frequency is Frequency.QUARTERLY


def test_last_n_years_window():
    parsed = parse_query("unemployment in Tunisia last 10 years", current_year=2026)
    assert (parsed.spec.start_year, parsed.spec.end_year) == (2017, 2026)


def test_understanding_panel_is_complete():
    """Spec 0B mode 9: every interpreted field must be shown to the user."""
    parsed = parse_query("التضخم والبطالة في الجزائر من 2000 إلى 2025")
    u = parsed.understanding()
    for field in ("countries", "iso3", "indicators", "concept_keys", "period",
                  "frequency", "preferred_sources", "output", "language",
                  "needs_clarification"):
        assert field in u
    assert u["language"] == "ar"
    assert u["countries"] == ["الجزائر"]


def test_ambiguous_concepts_are_surfaced_for_clarification():
    parsed = parse_query("inflation in Algeria")
    assert "inflation" in parsed.understanding()["needs_clarification"]


def test_parser_requires_no_llm_and_no_network(monkeypatch):
    """Spec 0P: core discovery must work with no API key configured."""
    for var in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    parsed = parse_query("PIB par habitant en Algerie de 2000 a 2025")
    assert parsed.spec.geographies == ["DZA"]
    assert [i.concept for i in parsed.spec.indicators] == ["gdp_per_capita"]
