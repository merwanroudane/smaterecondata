#!/usr/bin/env python3
"""End-to-end demonstration of the SmatEconData pipeline.

query -> DatasetSpec -> dataset -> validation -> statistics -> XLSX + HTML

Uses fixture observations rather than live providers so it runs offline and
deterministically. Live provider wiring is a separate layer.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from backend.smatecondata.core.models import (  # noqa: E402
    Frequency, IndicatorMetadata, Observation,
)
from backend.smatecondata.datasets.builder import DatasetBuilder  # noqa: E402
from backend.smatecondata.datasets.validation import validate_dataset  # noqa: E402
from backend.smatecondata.exports.excel import export_workbook  # noqa: E402
from backend.smatecondata.exports.html_report import export_report  # noqa: E402
from backend.smatecondata.search.query_parser import parse_query  # noqa: E402

QUERY = ("GDP per capita, inflation and unemployment for Algeria, Morocco and "
         "Tunisia from 2000 to 2012, annual data, Excel and HTML report")

GDP = IndicatorMetadata(
    provider="world_bank", series_id="NY.GDP.PCAP.CD",
    title="GDP per capita (current US$)",
    description="Gross domestic product divided by midyear population.",
    unit="current US$", frequency=Frequency.ANNUAL, source_name="World Bank",
    source_reference="https://data.worldbank.org/indicator/NY.GDP.PCAP.CD",
    coverage_start="1960", coverage_end="2024", topic="Economic Growth")

CPI = IndicatorMetadata(
    provider="imf", series_id="PCPIPCH",
    title="Inflation, average consumer prices",
    description="Annual percentage change in the average consumer price index.",
    unit="Annual %", frequency=Frequency.ANNUAL, source_name="IMF WEO",
    source_reference="https://www.imf.org/en/Publications/WEO",
    coverage_start="1980", coverage_end="2029", topic="Prices & Inflation")

UNEMP = IndicatorMetadata(
    provider="world_bank", series_id="SL.UEM.TOTL.ZS",
    title="Unemployment, total (% of total labor force) (modeled ILO estimate)",
    description="Share of the labour force without work but available for and "
                "seeking employment.",
    unit="% of total labor force", frequency=Frequency.ANNUAL,
    source_name="World Bank", coverage_start="1991", coverage_end="2024",
    source_reference="https://data.worldbank.org/indicator/SL.UEM.TOTL.ZS",
    topic="Labor Market")

# Illustrative fixture values, shaped like the real series but not the real
# numbers. Nothing here is presented to a user as an official observation.
FIXTURES: dict[str, dict[str, dict[int, float | None]]] = {
    "DZA": {
        "gdp": {y: 1800 + (y - 2000) * 210 for y in range(2000, 2013)},
        "cpi": {**{y: round(2.4 + (y - 2000) * 0.28, 2) for y in range(2000, 2013)},
                2004: None, 2005: None},
        "unemp": {y: round(29.0 - (y - 2000) * 1.6, 1) for y in range(2000, 2013)},
    },
    "MAR": {
        "gdp": {y: 1400 + (y - 2000) * 165 for y in range(2000, 2013)},
        "cpi": {**{y: round(1.6 + (y - 2000) * 0.19, 2) for y in range(2000, 2013)},
                2008: 24.5},  # deliberate spike: exercises the jump check
        "unemp": {y: round(13.4 - (y - 2000) * 0.35, 1) for y in range(2000, 2013)},
    },
    "TUN": {
        "gdp": {y: 2100 + (y - 2000) * 195 for y in range(2000, 2013)},
        "cpi": {y: round(2.9 + (y - 2000) * 0.22, 2) for y in range(2000, 2013)},
        "unemp": {**{y: round(14.2 - (y - 2000) * 0.12, 1)
                     for y in range(2000, 2013)},
                  2011: None, 2012: None},  # series ends early
    },
}

NAMES = {"DZA": "Algeria", "MAR": "Morocco", "TUN": "Tunisia"}


def observations(metadata: IndicatorMetadata, key: str) -> list[Observation]:
    out: list[Observation] = []
    for iso3, block in FIXTURES.items():
        for year, value in sorted(block[key].items()):
            out.append(Observation(
                provider=metadata.provider, series_id=metadata.series_id,
                geography=NAMES[iso3], iso3=iso3, period=str(year),
                value=value, unit=metadata.unit, frequency=Frequency.ANNUAL,
            ))
    return out


def main() -> int:
    out_dir = REPO.parent / "_out"

    parsed = parse_query(QUERY)
    print("Query    :", QUERY)
    understanding = parsed.understanding()
    print("Understood:")
    for key in ("language", "iso3", "concept_keys", "period", "frequency",
                "output", "needs_clarification"):
        print(f"  {key:20} {understanding[key]}")

    dataset = (
        DatasetBuilder(parsed.spec, name="maghreb_macro_2000_2012")
        .add_series(GDP, observations(GDP, "gdp"), "gdp_per_capita")
        .add_series(CPI, observations(CPI, "cpi"), "inflation")
        .add_series(UNEMP, observations(UNEMP, "unemp"), "unemployment")
        .build()
    )

    quality = validate_dataset(dataset)
    print(f"\nDataset  : {dataset.row_count} rows, "
          f"{len(dataset.variables)} variables, "
          f"{len(dataset.geographies)} countries")
    print(f"Quality  : {quality.status} "
          f"(coverage {quality.coverage_pct}%, "
          f"{quality.errors} errors, {quality.warnings} warnings)")
    for issue in quality.issues:
        print(f"  [{issue.severity.value:7}] {issue.code:22} {issue.message}")

    xlsx = export_workbook(dataset, out_dir / f"{dataset.name}.xlsx",
                           quality=quality)
    report = export_report(dataset, out_dir / f"{dataset.name}.html",
                           quality=quality)

    print(f"\nXLSX     : {xlsx.path}  ({len(xlsx.sheets)} sheets)")
    print(f"HTML     : {report.path}  ({report.bytes_written:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
