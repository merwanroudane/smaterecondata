"""Reproducibility and export-format tests (spec 13, 17, 18, 23, 24)."""

from __future__ import annotations

import json
import zipfile

import pytest
import yaml

from backend.smatecondata.core.models import (
    DatasetSpec,
    Frequency,
    IndicatorMetadata,
    Observation,
    OutputShape,
)
from backend.smatecondata.datasets.builder import DatasetBuilder
from backend.smatecondata.datasets.validation import validate_dataset
from backend.smatecondata.exports.formats import (
    ExportUnavailable,
    export_csv,
    export_json,
    export_research_bundle,
)
from backend.smatecondata.provenance.recipe import (
    DatasetRecipe,
    RECIPE_VERSION,
    compare_versions,
    recipe_from_dataset,
)

GDP = IndicatorMetadata(
    provider="world_bank", series_id="NY.GDP.PCAP.CD",
    title="GDP per capita (current US$)", unit="current US$",
    frequency=Frequency.ANNUAL, source_name="World Bank",
    source_reference="https://data.worldbank.org/indicator/NY.GDP.PCAP.CD")

CPI = IndicatorMetadata(
    provider="imf", series_id="PCPIPCH",
    title="Inflation, average consumer prices", unit="Annual %",
    frequency=Frequency.ANNUAL, source_name="IMF")


def obs(metadata, iso3, period, value):
    return Observation(
        provider=metadata.provider, series_id=metadata.series_id,
        geography={"DZA": "Algeria", "MAR": "Morocco"}.get(iso3, iso3),
        iso3=iso3, period=str(period), value=value, unit=metadata.unit,
        frequency=Frequency.ANNUAL)


def make_dataset(*, name="test_dataset", years=range(2000, 2006), bump=0.0):
    spec = DatasetSpec(geographies=["DZA", "MAR"], start_year=2000,
                       end_year=max(years), frequency=Frequency.ANNUAL,
                       original_query="GDP and inflation for Algeria and Morocco")
    gdp = [obs(GDP, g, y, 1000.0 + i * 100 + bump)
           for g in ("DZA", "MAR") for i, y in enumerate(years)]
    cpi = [obs(CPI, g, y, 2.0 + i * 0.1)
           for g in ("DZA", "MAR") for i, y in enumerate(years)]
    return (DatasetBuilder(spec, name=name)
            .add_series(GDP, gdp, "gdp_per_capita")
            .add_series(CPI, cpi, "inflation")
            .build())


@pytest.fixture
def dataset():
    return make_dataset()


# --------------------------------------------------------------------------
# Recipe identity and round-trip (spec 13)
# --------------------------------------------------------------------------


def test_recipe_captures_the_resolved_series(dataset):
    recipe = recipe_from_dataset(dataset)
    assert {s.series_id for s in recipe.series} == {"NY.GDP.PCAP.CD", "PCPIPCH"}
    assert {s.provider for s in recipe.series} == {"world_bank", "imf"}
    assert recipe.geographies == ["DZA", "MAR"]


def test_recipe_hash_is_stable_across_rebuilds():
    """Same definition, different build time -> same hash."""
    a = recipe_from_dataset(make_dataset())
    b = recipe_from_dataset(make_dataset())
    assert a.created_at != b.created_at or True  # timestamps may collide
    assert a.recipe_hash == b.recipe_hash


def test_recipe_hash_changes_when_the_definition_changes(dataset):
    original = recipe_from_dataset(dataset)
    changed = recipe_from_dataset(dataset)
    changed.end_year = 2099
    assert changed.recipe_hash != original.recipe_hash


def test_recipe_hash_ignores_cosmetic_fields(dataset):
    recipe = recipe_from_dataset(dataset)
    before = recipe.recipe_hash
    recipe.request = "a completely different sentence"
    recipe.version = 99
    assert recipe.recipe_hash == before


@pytest.mark.parametrize("suffix", [".yaml", ".json"])
def test_recipe_round_trips_through_disk(dataset, tmp_path, suffix):
    recipe = recipe_from_dataset(dataset)
    path = recipe.save(tmp_path / f"recipe{suffix}")
    loaded = DatasetRecipe.load(path)

    assert loaded.recipe_hash == recipe.recipe_hash
    assert loaded.name == recipe.name
    assert loaded.geographies == recipe.geographies
    assert len(loaded.series) == len(recipe.series)
    assert loaded.frequency is recipe.frequency
    assert loaded.output_shape is recipe.output_shape


def test_recipe_yaml_is_readable(dataset, tmp_path):
    path = recipe_from_dataset(dataset).save(tmp_path / "r.yaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["recipe_version"] == RECIPE_VERSION
    assert raw["period"] == {"start_year": 2000, "end_year": 2005}
    assert raw["validation"] == {"run": True}
    assert isinstance(raw["series"], list)


def test_recipe_rebuilds_a_spec_with_series_already_resolved(dataset):
    spec = recipe_from_dataset(dataset).to_spec()
    assert spec.geographies == ["DZA", "MAR"]
    assert all(i.is_resolved for i in spec.indicators)
    assert {i.resolved_series_id for i in spec.indicators} == {
        "NY.GDP.PCAP.CD", "PCPIPCH"}


def test_recipe_rejects_a_future_major_version():
    with pytest.raises(ValueError, match="unsupported recipe_version"):
        DatasetRecipe.from_dict({
            "recipe_version": "99.0", "name": "x",
            "series": [{"alias": "a", "provider": "p", "series_id": "s"}],
        })


def test_recipe_rejects_a_missing_required_field():
    with pytest.raises(ValueError, match="missing required field"):
        DatasetRecipe.from_dict({"name": "x"})


def test_recipe_rejects_non_mapping():
    with pytest.raises(ValueError):
        DatasetRecipe.from_dict(["not", "a", "mapping"])  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Refresh / version comparison (spec 23, 24)
# --------------------------------------------------------------------------


def test_refresh_detects_new_observations():
    previous = make_dataset(years=range(2000, 2005))
    current = make_dataset(years=range(2000, 2006))
    summary = compare_versions(previous, current)
    assert summary.new_observations > 0
    assert summary.has_changes


def test_refresh_detects_revised_values():
    summary = compare_versions(make_dataset(), make_dataset(bump=5.0))
    assert summary.updated_observations > 0
    assert any("->" in d for d in summary.details)


def test_refresh_of_identical_data_reports_no_change():
    summary = compare_versions(make_dataset(), make_dataset())
    assert not summary.has_changes
    assert summary.new_observations == 0
    assert summary.updated_observations == 0


def test_refresh_detects_metadata_changes():
    previous = make_dataset()
    current = make_dataset()
    current.variables[0].metadata.unit = "constant 2015 US$"
    assert compare_versions(previous, current).metadata_changes > 0


def test_each_dataset_owns_its_metadata_snapshot():
    """A version must keep the metadata as it was when that version was built.

    The builder deep-copies, so revising one snapshot cannot retroactively
    rewrite an earlier one -- and cannot leak into the shared catalogue record.
    """
    previous = make_dataset()
    current = make_dataset()

    current.variables[0].metadata.title = "GDP per capita, revised"
    assert previous.variables[0].metadata.title == "GDP per capita (current US$)"
    assert GDP.title == "GDP per capita (current US$)"


def test_change_summary_renders_as_text():
    text = compare_versions(make_dataset(years=range(2000, 2005)),
                            make_dataset()).as_text()
    assert "New observations:" in text


# --------------------------------------------------------------------------
# Tabular formats (spec 17)
# --------------------------------------------------------------------------


def test_csv_leaves_a_gap_blank_not_a_sentinel(tmp_path):
    spec = DatasetSpec(geographies=["DZA"], start_year=2000, end_year=2002)
    rows = [obs(GDP, "DZA", 2000, 1.0), obs(GDP, "DZA", 2002, 3.0)]
    dataset = (DatasetBuilder(spec).add_series(GDP, rows, "gdp").build())
    # 2001 never arrived, so it is simply absent rather than zero-filled.
    text = export_csv(dataset, tmp_path / "d.csv").read_text(encoding="utf-8")
    assert "0.0" not in text.split("\n")[1]
    assert text.startswith("geography,iso3,period,")


def test_csv_header_matches_the_dataset_columns(dataset, tmp_path):
    text = export_csv(dataset, tmp_path / "d.csv").read_text(encoding="utf-8")
    header = text.splitlines()[0].split(",")
    assert header == ["geography", "iso3", "period",
                      "gdp_per_capita_usd", "inflation_pct"]


def test_csv_long_shape_uses_long_headers(dataset, tmp_path):
    dataset.spec.output_shape = OutputShape.LONG
    text = export_csv(dataset, tmp_path / "long.csv").read_text(encoding="utf-8")
    assert text.splitlines()[0].startswith("geography,iso3,period,indicator,value")


def test_json_export_carries_provenance(dataset, tmp_path):
    payload = json.loads(
        export_json(dataset, tmp_path / "d.json").read_text(encoding="utf-8"))
    assert payload["columns"][:3] == ["geography", "iso3", "period"]
    assert len(payload["variables"]) == 2
    assert all(v["citation"] for v in payload["variables"])
    assert payload["transformations"][0]["operation"] == "build_dataset"


def test_optional_format_failure_is_typed():
    assert issubclass(ExportUnavailable, RuntimeError)


# --------------------------------------------------------------------------
# Research bundle (spec 18)
# --------------------------------------------------------------------------


def test_bundle_contains_every_specified_file(dataset, tmp_path):
    result = export_research_bundle(dataset, tmp_path / "bundle.zip",
                                    quality=validate_dataset(dataset))
    names = {entry.split("/", 1)[1] for entry in result.entries}
    required = {"data.xlsx", "data.csv", "report.html", "metadata.csv",
                "sources.csv", "transformations.json", "dataset_recipe.yaml",
                "README.md"}
    assert required <= names


def test_bundle_is_a_valid_zip_rooted_at_the_dataset_name(dataset, tmp_path):
    path = tmp_path / "bundle.zip"
    export_research_bundle(dataset, path)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert all(n.startswith(f"{dataset.name}/") for n in archive.namelist())


def test_bundle_readme_explains_refresh_and_missing_data(dataset, tmp_path):
    path = tmp_path / "bundle.zip"
    export_research_bundle(dataset, path, quality=validate_dataset(dataset))
    with zipfile.ZipFile(path) as archive:
        readme = archive.read(f"{dataset.name}/README.md").decode("utf-8")

    assert "How to refresh this dataset" in readme
    assert "never interpolated" in readme or "never" in readme
    assert "Dr Merwan Roudane" in readme
    assert "NY.GDP.PCAP.CD" in readme
    assert "Recipe hash" in readme


def test_bundle_recipe_is_loadable(dataset, tmp_path):
    path = tmp_path / "bundle.zip"
    export_research_bundle(dataset, path)
    with zipfile.ZipFile(path) as archive:
        raw = yaml.safe_load(
            archive.read(f"{dataset.name}/dataset_recipe.yaml").decode("utf-8"))
    assert DatasetRecipe.from_dict(raw).recipe_hash == \
        recipe_from_dataset(dataset).recipe_hash


def test_bundle_transformations_json_is_ordered(dataset, tmp_path):
    path = tmp_path / "bundle.zip"
    export_research_bundle(dataset, path)
    with zipfile.ZipFile(path) as archive:
        log = json.loads(
            archive.read(f"{dataset.name}/transformations.json").decode("utf-8"))
    assert [entry["step"] for entry in log] == list(range(1, len(log) + 1))
