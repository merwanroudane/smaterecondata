"""REST surface tests for the SmatEconData dataset layer (spec 31).

Also pins the spec-0P guarantee: every endpoint exercised here works with no
LLM API key configured. ``conftest`` fixtures are avoided so this module can
run on its own.
"""

from __future__ import annotations

import os
import secrets

import pytest

# Configure a keyless environment *before* the app is imported, which is the
# condition spec 0P requires the application to tolerate.
os.environ.setdefault("JWT_SECRET", secrets.token_hex(32))
os.environ.setdefault("DISABLE_MCP", "true")
os.environ.pop("OPENROUTER_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from backend.smatecondata.api.routes import STORE  # noqa: E402


@pytest.fixture(scope="module")
def client():
    from backend.main import app
    return TestClient(app)


GDP_META = {
    "provider": "world_bank",
    "series_id": "NY.GDP.PCAP.CD",
    "title": "GDP per capita (current US$)",
    "unit": "current US$",
    "frequency": "annual",
    "source_name": "World Bank",
    "source_reference": "https://data.worldbank.org/indicator/NY.GDP.PCAP.CD",
}

CPI_META = {
    "provider": "imf",
    "series_id": "PCPIPCH",
    "title": "Inflation, average consumer prices",
    "unit": "Annual %",
    "frequency": "annual",
    "source_name": "IMF",
}


def _observations(meta, iso3, values):
    return [
        {
            "provider": meta["provider"],
            "series_id": meta["series_id"],
            "geography": {"DZA": "Algeria", "MAR": "Morocco"}[iso3],
            "iso3": iso3,
            "period": str(period),
            "value": value,
            "unit": meta["unit"],
            "frequency": "annual",
        }
        for period, value in values.items()
    ]


def _build_payload(name="api_test"):
    return {
        "name": name,
        "query": "GDP per capita and inflation for Algeria and Morocco "
                 "from 2000 to 2004",
        "series": [
            {
                "metadata": GDP_META,
                "concept": "gdp_per_capita",
                "observations": (
                    _observations(GDP_META, "DZA",
                                  {y: 1800.0 + (y - 2000) * 120 for y in range(2000, 2005)})
                    + _observations(GDP_META, "MAR",
                                    {y: 1500.0 + (y - 2000) * 100 for y in range(2000, 2005)})
                ),
            },
            {
                "metadata": CPI_META,
                "concept": "inflation",
                "observations": (
                    # 2002 deliberately absent for DZA.
                    _observations(CPI_META, "DZA",
                                  {y: 2.5 + (y - 2000) * 0.3
                                   for y in (2000, 2001, 2003, 2004)})
                    + _observations(CPI_META, "MAR",
                                    {y: 1.9 for y in range(2000, 2005)})
                ),
            },
        ],
    }


@pytest.fixture
def dataset_id(client):
    response = client.post("/api/v1/datasets", json=_build_payload())
    assert response.status_code == 201, response.text
    return response.json()["dataset_id"]


# --------------------------------------------------------------------------
# Spec 0P: no LLM key required
# --------------------------------------------------------------------------


def test_app_serves_deterministic_routes_without_an_llm_key(client):
    assert not os.environ.get("OPENROUTER_API_KEY")
    assert client.get("/api/v1/concepts").status_code == 200


@pytest.mark.parametrize("query,language,iso3,concepts", [
    ("Inflation in Algeria from 2000 to 2025", "en", ["DZA"], ["inflation"]),
    ("Inflation en Algerie de 2000 a 2025", "fr", ["DZA"], ["inflation"]),
    ("التضخم في الجزائر من 2000 إلى 2025", "ar", ["DZA"], ["inflation"]),
])
def test_parse_endpoint_is_trilingual(client, query, language, iso3, concepts):
    response = client.post("/api/v1/parse", json={"query": query})
    assert response.status_code == 200
    body = response.json()
    understanding = body["understanding"]
    assert understanding["language"] == language
    assert understanding["iso3"] == iso3
    assert understanding["concept_keys"] == concepts
    assert understanding["period"] == "2000-2025"


def test_parse_surfaces_ambiguity_with_distinctions(client):
    body = client.post("/api/v1/parse",
                       json={"query": "inflation in Algeria"}).json()
    assert body["ambiguous"], "broad terms must offer a choice"
    assert any(a["concept"] == "inflation" for a in body["ambiguous"])
    assert all(a["distinctions"] for a in body["ambiguous"])


def test_parse_rejects_an_empty_query(client):
    assert client.post("/api/v1/parse", json={"query": ""}).status_code == 422


def test_concepts_localise_without_losing_the_key(client):
    body = client.get("/api/v1/concepts", params={"language": "ar"}).json()
    gdp = next(c for c in body["concepts"] if c["key"] == "gdp")
    assert gdp["display"] != gdp["label"]      # Arabic display label
    assert gdp["label"] == "Gross domestic product"  # canonical label intact


def test_geographies_expose_region_presets(client):
    body = client.get("/api/v1/geographies").json()
    maghreb = next(r for r in body["regions"] if r["key"] == "maghreb")
    assert maghreb["members"] == ["DZA", "MAR", "TUN", "LBY", "MRT"]


# --------------------------------------------------------------------------
# Dataset lifecycle
# --------------------------------------------------------------------------


def test_build_returns_a_summary(client):
    response = client.post("/api/v1/datasets", json=_build_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["variables"] == ["gdp_per_capita_usd", "inflation_pct"]
    assert body["geographies"] == ["DZA", "MAR"]
    assert body["rows"] == 10
    assert len(body["recipe_hash"]) == 16


def test_build_requires_spec_or_query(client):
    payload = _build_payload()
    payload.pop("query")
    assert client.post("/api/v1/datasets", json=payload).status_code == 422


def test_build_requires_at_least_one_series(client):
    payload = _build_payload()
    payload["series"] = []
    assert client.post("/api/v1/datasets", json=payload).status_code == 422


def test_get_dataset_paginates(client, dataset_id):
    body = client.get(f"/api/v1/datasets/{dataset_id}",
                      params={"limit": 3, "offset": 2}).json()
    assert body["total_rows"] == 10
    assert len(body["rows"]) == 3
    assert body["offset"] == 2


def test_unknown_dataset_is_404(client):
    assert client.get("/api/v1/datasets/doesnotexist").status_code == 404


def test_validate_reports_the_planted_gap(client, dataset_id):
    body = client.post(f"/api/v1/datasets/{dataset_id}/validate").json()
    assert body["status"] in {"clean", "usable_with_warnings"}
    assert body["missing_cells"] == 1
    assert any(i["code"] == "time_gap" for i in body["issues"])


def test_describe_returns_statistics_and_correlation(client, dataset_id):
    body = client.post(f"/api/v1/datasets/{dataset_id}/describe").json()
    stats = {row["variable"]: row for row in body["statistics"]}
    assert set(stats) == {"gdp_per_capita_usd", "inflation_pct"}
    assert stats["inflation_pct"]["missing"] == 1
    assert body["correlation"]["method"] == "pearson"
    assert "never imputed" in body["note"]


def test_describe_can_skip_correlation(client, dataset_id):
    body = client.post(f"/api/v1/datasets/{dataset_id}/describe",
                       params={"correlation": "none"}).json()
    assert "correlation" not in body


def test_missingness_endpoint(client, dataset_id):
    body = client.get(f"/api/v1/datasets/{dataset_id}/missingness").json()
    assert body["missing_cells"] == 1
    assert body["coverage_pct"] == pytest.approx(95.0)
    assert "coverage_matrix" in body


def test_outliers_are_inspection_only(client, dataset_id):
    body = client.get(f"/api/v1/datasets/{dataset_id}/outliers").json()
    assert "no value was altered" in body["note"]
    assert set(body["flags"]) == {"gdp_per_capita_usd", "inflation_pct"}


def test_lineage_covers_every_column(client, dataset_id):
    body = client.get(f"/api/v1/datasets/{dataset_id}/lineage").json()
    assert {c["column"] for c in body["columns"]} == {
        "gdp_per_capita_usd", "inflation_pct"}
    assert all(c["citation"] for c in body["columns"])


# --------------------------------------------------------------------------
# Transformations (spec 12)
# --------------------------------------------------------------------------


def test_filter_geographies_is_logged(client, dataset_id):
    body = client.post(f"/api/v1/datasets/{dataset_id}/transform",
                       json={"operation": "filter_geographies",
                             "parameters": {"geographies": ["DZA"]}}).json()
    assert body["geographies"] == ["DZA"]
    assert body["transformations"] == 2  # build + filter


def test_rename_variable_rejects_a_collision(client, dataset_id):
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/transform",
        json={"operation": "rename_variable",
              "parameters": {"from": "inflation_pct", "to": "gdp_per_capita_usd"}})
    assert response.status_code == 422


def test_select_variables_rejects_unknown_names(client, dataset_id):
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/transform",
        json={"operation": "select_variables",
              "parameters": {"variables": ["nope"]}})
    assert response.status_code == 422


def test_unknown_operation_is_rejected(client, dataset_id):
    response = client.post(f"/api/v1/datasets/{dataset_id}/transform",
                           json={"operation": "drop_database", "parameters": {}})
    assert response.status_code == 422


# --------------------------------------------------------------------------
# Reproducibility
# --------------------------------------------------------------------------


def test_recipe_is_returned_as_yaml_and_json(client, dataset_id):
    yaml_body = client.get(f"/api/v1/datasets/{dataset_id}/recipe").json()
    assert "recipe_version" in yaml_body["yaml"]
    assert len(yaml_body["recipe_hash"]) == 16

    json_body = client.get(f"/api/v1/datasets/{dataset_id}/recipe",
                           params={"fmt": "json"}).json()
    assert json_body["recipe_hash"] == yaml_body["recipe_hash"]
    assert len(json_body["series"]) == 2


def test_recipe_validation_accepts_a_round_trip(client, dataset_id):
    recipe = client.get(f"/api/v1/datasets/{dataset_id}/recipe",
                        params={"fmt": "json"}).json()
    response = client.post("/api/v1/recipes/validate", json=recipe)
    assert response.status_code == 200
    assert response.json()["valid"] is True


def test_recipe_validation_rejects_junk(client):
    response = client.post("/api/v1/recipes/validate",
                           json={"name": "no series here"})
    assert response.status_code == 422


def test_compare_two_versions(client):
    first = client.post("/api/v1/datasets", json=_build_payload("v1")).json()
    payload = _build_payload("v2")
    payload["series"][0]["observations"][0]["value"] = 9999.0
    second = client.post("/api/v1/datasets", json=payload).json()

    body = client.post(
        f"/api/v1/datasets/{first['dataset_id']}/compare/{second['dataset_id']}"
    ).json()
    assert body["updated_observations"] == 1
    assert body["has_changes"] is True


# --------------------------------------------------------------------------
# Export (spec 15, 16, 17, 18)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("fmt,signature", [
    ("xlsx", b"PK"),      # zip container
    ("csv", b"geography"),
    ("json", b"{"),
    ("html", b"<!doctype html"),
    ("bundle", b"PK"),
])
def test_export_formats(client, dataset_id, fmt, signature):
    response = client.post(f"/api/v1/datasets/{dataset_id}/export",
                           json={"format": fmt})
    assert response.status_code == 200, response.text
    assert response.content[:len(signature)].lower() == signature.lower()


def test_export_recipe_yaml_is_inline(client, dataset_id):
    body = client.post(f"/api/v1/datasets/{dataset_id}/export",
                       json={"format": "recipe_yaml"}).json()
    assert body["filename"].endswith("_recipe.yaml")
    assert "series:" in body["content"]


def test_export_rejects_an_unknown_format(client, dataset_id):
    response = client.post(f"/api/v1/datasets/{dataset_id}/export",
                           json={"format": "exe"})
    assert response.status_code == 422


def test_store_evicts_to_stay_bounded():
    """The dataset store is a cache, not permanent storage (spec 34)."""
    from backend.smatecondata.api.routes import MAX_DATASETS
    assert len(STORE._items) <= MAX_DATASETS


# --------------------------------------------------------------------------
# MCP surface (spec 29)
# --------------------------------------------------------------------------


def test_mcp_exposes_granular_tools_not_one_opaque_tool(monkeypatch):
    """Spec 29: 'Do not expose only one giant generic tool.'"""
    monkeypatch.setenv("DISABLE_MCP", "false")
    import importlib

    from backend import config

    config.get_settings.cache_clear()
    import backend.main as main

    main = importlib.reload(main)
    names = {getattr(t, "name", t) for t in main.mcp.tools}

    assert len(names) > 1
    for required in ("build_dataset", "validate_dataset", "describe_dataset",
                     "summarize_missingness", "export_dataset",
                     "save_dataset_recipe", "compare_dataset_versions"):
        assert required in names, f"missing granular MCP tool: {required}"
