#!/usr/bin/env python3
"""End-to-end demonstration against LIVE provider data.

    natural-language query
      -> DatasetSpec (deterministic, no LLM)
      -> live provider fetch
      -> canonical dataset
      -> validation
      -> descriptive statistics
      -> XLSX + HTML + research bundle

Uses the World Bank, which needs no API key. Run:

    JWT_SECRET=$(openssl rand -hex 32) python scripts/demo_live.py
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# The provider modules read application settings, which require this secret.
os.environ.setdefault("JWT_SECRET", secrets.token_hex(32))

from backend.providers.worldbank import WorldBankProvider  # noqa: E402
from backend.smatecondata.analytics.descriptive import describe  # noqa: E402
from backend.smatecondata.datasets.builder import DatasetBuilder  # noqa: E402
from backend.smatecondata.datasets.validation import validate_dataset  # noqa: E402
from backend.smatecondata.exports.excel import export_workbook  # noqa: E402
from backend.smatecondata.exports.formats import export_research_bundle  # noqa: E402
from backend.smatecondata.exports.html_report import export_report  # noqa: E402
from backend.smatecondata.providers.gateway import ProviderGateway  # noqa: E402
from backend.smatecondata.provenance.recipe import recipe_from_dataset  # noqa: E402
from backend.smatecondata.search.query_parser import parse_query  # noqa: E402

QUERY = ("GDP per capita, inflation and unemployment for Algeria, Morocco and "
         "Tunisia from 2010 to 2023, annual data, Excel and HTML report")

# Concept key -> the World Bank series that answers it.
SERIES = {
    "gdp_per_capita": "NY.GDP.PCAP.CD",
    "inflation": "FP.CPI.TOTL.ZG",
    "unemployment": "SL.UEM.TOTL.ZS",
}


async def main() -> int:
    out_dir = REPO.parent / "_out"

    parsed = parse_query(QUERY)
    spec = parsed.spec
    understanding = parsed.understanding()

    print("Query      :", QUERY)
    print("Understood :")
    for key in ("language", "iso3", "concept_keys", "period", "frequency",
                "output", "needs_clarification"):
        print(f"   {key:20} {understanding[key]}")

    requests = [
        ("world_bank", SERIES[i.concept], i.concept)
        for i in spec.indicators if i.concept in SERIES
    ]
    if not requests:
        print("No known series for the parsed concepts.")
        return 1

    gateway = ProviderGateway({"world_bank": WorldBankProvider()}, timeout=90)
    print(f"\nFetching {len(requests)} series for {spec.geographies} "
          f"({spec.period_label()}) from the World Bank ...")

    outcomes = await gateway.fetch_many(
        requests, spec.geographies,
        start_year=spec.start_year, end_year=spec.end_year)

    builder = DatasetBuilder(spec, name="maghreb_macro_live")
    for concept, outcome in outcomes:
        status = outcome.result.status if outcome.result else "unknown"
        if outcome.ok:
            print(f"   {concept:18} {status:16} "
                  f"{len(outcome.observations):>4} observations  "
                  f"({outcome.metadata.series_id})")
            builder.add_series(outcome.metadata, outcome.observations, concept)
        else:
            print(f"   {concept:18} {status:16} {outcome.result.message}")

    dataset = builder.build()
    if not dataset.variables:
        print("\nNo series could be retrieved.")
        return 1

    quality = validate_dataset(dataset)
    print(f"\nDataset    : {dataset.row_count} rows, "
          f"{len(dataset.variables)} variables, "
          f"{len(dataset.geographies)} countries")
    print(f"Quality    : {quality.status} (coverage {quality.coverage_pct}%, "
          f"{quality.errors} errors, {quality.warnings} warnings)")
    for issue in quality.issues[:8]:
        print(f"   [{issue.severity.value:7}] {issue.code:20} {issue.message}")

    print("\nDescriptive statistics")
    columns = dataset.wide_columns_map()
    _, rows = dataset.to_wide()
    periods = [str(r["period"]) for r in rows]
    units = {v.alias: v.metadata.unit for v in dataset.variables}
    for alias, values in columns.items():
        s = describe(values, variable=alias, periods=periods, unit=units.get(alias))
        print(f"   {alias:26} n={s.count:<4} mean={s.mean:>12,.2f} "
              f"sd={s.std_dev:>11,.2f} min={s.minimum:>10,.2f} "
              f"max={s.maximum:>12,.2f}")

    recipe = recipe_from_dataset(dataset)
    xlsx = export_workbook(dataset, out_dir / f"{dataset.name}.xlsx", quality=quality)
    html = export_report(dataset, out_dir / f"{dataset.name}.html", quality=quality)
    bundle = export_research_bundle(dataset, out_dir / f"{dataset.name}_bundle.zip",
                                    quality=quality)
    recipe.save(out_dir / f"{dataset.name}_recipe.yaml")

    print(f"\nRecipe hash: {recipe.recipe_hash}")
    print(f"XLSX       : {xlsx.path}  ({len(xlsx.sheets)} sheets)")
    print(f"HTML       : {html.path}  ({html.bytes_written:,} bytes)")
    print(f"Bundle     : {bundle.path}  ({len(bundle.entries)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
