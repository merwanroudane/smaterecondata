"""Tabular export formats and the research bundle (spec 17, 18).

CSV, JSON and the recipe formats have no third-party requirement. Parquet and
Feather need PyArrow, and Stata ``.dta`` needs pandas; each is optional and
degrades to a clear error rather than a traceback, because the spec makes them
non-essential for the MVP.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from ..analytics import missingness as missingness_mod
from ..analytics.descriptive import describe
from ..core.models import DataQualityReport
from ..datasets.builder import Dataset
from ..provenance.recipe import recipe_from_dataset


class ExportUnavailable(RuntimeError):
    """An optional export dependency is not installed."""


def _headers_and_rows(dataset: Dataset) -> tuple[list[str], list[dict]]:
    rows = dataset.rows()
    if not rows:
        columns, _ = dataset.to_wide()
        return list(columns), []
    # Preserve column order from the dataset rather than dict iteration order.
    from ..core.models import OutputShape
    if dataset.spec.output_shape is OutputShape.LONG:
        headers = ["geography", "iso3", "period", "indicator", "value", "unit",
                   "frequency", "provider", "series_id", "status"]
    else:
        headers, _ = dataset.to_wide()
    return list(headers), rows


def export_csv(dataset: Dataset, path: str | Path,
               *, na_rep: str = "", delimiter: str = ",") -> Path:
    """CSV with a genuine blank for a gap, never a sentinel number."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    headers, rows = _headers_and_rows(dataset)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers,
                                delimiter=delimiter, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                h: (na_rep if row.get(h) is None else row.get(h))
                for h in headers
            })
    return path


def export_json(dataset: Dataset, path: str | Path,
                *, indent: int = 2) -> Path:
    """JSON carrying the data *and* its provenance, not bare rows."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    headers, rows = _headers_and_rows(dataset)
    lineage = dataset.lineage or dataset.build_lineage()

    payload = {
        "name": dataset.name,
        "retrieved_at": dataset.retrieved_at.isoformat(),
        "shape": dataset.spec.output_shape.value,
        "columns": headers,
        "rows": rows,
        "variables": [
            {
                "column": line.column,
                "provider": line.provider,
                "series_id": line.series_id,
                "title": line.series_title,
                "unit": line.unit,
                "frequency": line.frequency.value,
                "source_reference": line.source_reference,
                "citation": line.citation,
            }
            for line in lineage
        ],
        "transformations": [
            {"operation": t.operation, "parameters": t.parameters,
             "timestamp": t.timestamp.isoformat()}
            for t in dataset.transformations
        ],
    }
    path.write_text(json.dumps(payload, indent=indent, ensure_ascii=False,
                               default=str), encoding="utf-8", newline="\n")
    return path


def _to_arrow_table(dataset: Dataset):
    try:
        import pyarrow as pa
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ExportUnavailable(
            "Parquet/Feather export needs PyArrow: pip install pyarrow"
        ) from exc

    headers, rows = _headers_and_rows(dataset)
    columns = {h: [row.get(h) for row in rows] for h in headers}
    return pa.table(columns)


def export_parquet(dataset: Dataset, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover
        raise ExportUnavailable(
            "Parquet export needs PyArrow: pip install pyarrow") from exc
    pq.write_table(_to_arrow_table(dataset), path)
    return path


def export_feather(dataset: Dataset, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from pyarrow import feather
    except ImportError as exc:  # pragma: no cover
        raise ExportUnavailable(
            "Feather export needs PyArrow: pip install pyarrow") from exc
    feather.write_feather(_to_arrow_table(dataset), path)
    return path


def export_stata(dataset: Dataset, path: str | Path) -> Path:
    """Stata ``.dta``.

    Stata variable names may not exceed 32 characters and cannot start with a
    digit, so aliases are trimmed here rather than letting pandas raise.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover
        raise ExportUnavailable(
            "Stata export needs pandas: pip install pandas") from exc

    headers, rows = _headers_and_rows(dataset)
    frame = pd.DataFrame(rows, columns=headers)

    renames: dict[str, str] = {}
    seen: set[str] = set()
    for column in frame.columns:
        name = str(column)[:32]
        if name and name[0].isdigit():
            name = f"v_{name}"[:32]
        candidate, n = name, 1
        while candidate in seen:
            n += 1
            candidate = f"{name[:29]}_{n}"
        seen.add(candidate)
        renames[column] = candidate
    frame = frame.rename(columns=renames)

    labels = {
        renames[v.alias]: v.metadata.title[:80]
        for v in dataset.variables if v.alias in renames
    }
    frame.to_stata(path, write_index=False, variable_labels=labels,
                   version=118)
    return path


# --------------------------------------------------------------------------
# Research bundle (spec 18)
# --------------------------------------------------------------------------


@dataclass
class BundleResult:
    path: Path
    entries: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _bundle_readme(dataset: Dataset,
                   quality: DataQualityReport | None) -> str:
    """Auto-generated README explaining the bundle (spec 18)."""
    lineage = dataset.lineage or dataset.build_lineage()
    miss = missingness_mod.analyse(dataset)
    spec = dataset.spec
    recipe = recipe_from_dataset(dataset)

    lines: list[str] = [
        f"# {dataset.name}",
        "",
        "Built with **SmatEconData** — economic data, easier to find.",
        "Author: Dr Merwan Roudane · "
        "https://github.com/merwanroudane/smaterecondata",
        "",
        "## What this dataset contains",
        "",
        f"- Countries/areas: {', '.join(spec.geographies) or 'n/a'}",
        f"- Period requested: {spec.period_label()}",
        f"- Frequency: {spec.frequency.value}",
        f"- Shape: {spec.output_shape.value}",
        f"- Rows: {dataset.row_count}",
        f"- Variables: {len(dataset.variables)}",
        f"- Overall coverage: {miss.coverage_pct:g}%",
        "",
        "## Variables and sources",
        "",
        "| Column | Provider | Series ID | Unit | Source |",
        "| --- | --- | --- | --- | --- |",
    ]
    for line in lineage:
        lines.append(
            f"| `{line.column}` | {line.provider} | `{line.series_id}` | "
            f"{line.unit or '—'} | {line.source_reference or '—'} |"
        )

    lines += ["", "## Citations", ""]
    lines += [f"- {line.citation}" for line in lineage]

    lines += ["", "## Retrieval", "",
              f"- Retrieved at: {dataset.retrieved_at.isoformat()}",
              f"- Recipe hash: `{recipe.recipe_hash}`", ""]

    lines += ["## Transformations applied", ""]
    if dataset.transformations:
        for i, t in enumerate(dataset.transformations, start=1):
            note = f" — {t.note}" if t.note else ""
            lines.append(f"{i}. `{t.operation}`{note}")
    else:
        lines.append("None.")

    lines += ["", "## Warnings", ""]
    if quality and quality.issues:
        lines.append(f"Status: **{quality.status.replace('_', ' ')}** "
                     f"({quality.errors} errors, {quality.warnings} warnings)")
        lines.append("")
        for issue in quality.issues:
            where = " / ".join(x for x in (issue.variable, issue.geography) if x)
            lines.append(f"- **{issue.severity.value}** `{issue.code}`"
                         f"{f' ({where})' if where else ''}: {issue.message}")
    else:
        lines.append("No validation issues were raised.")

    lines += [
        "",
        "## Missing data",
        "",
        "Blank cells are genuine gaps in the source data. Nothing in this "
        "bundle was interpolated, imputed, or carried forward.",
        "",
        "## How to refresh this dataset",
        "",
        "`dataset_recipe.yaml` fully defines the request. Re-running it "
        "re-queries the providers, rebuilds the table, re-runs validation and "
        "creates a **new version** — the snapshot in this bundle is never "
        "overwritten.",
        "",
        "```bash",
        "smatecondata refresh dataset_recipe.yaml",
        "```",
        "",
        "## Files",
        "",
        "| File | Contents |",
        "| --- | --- |",
        "| `data.xlsx` | Full workbook: data, metadata, statistics, "
        "missingness, sources, transformations, query |",
        "| `data.csv` | The dataset as CSV |",
        "| `data.parquet` | The dataset as Parquet (when PyArrow is available) |",
        "| `report.html` | Standalone HTML report — opens offline |",
        "| `metadata.csv` | One row per variable |",
        "| `sources.csv` | Provenance and citations |",
        "| `transformations.json` | Ordered transformation log |",
        "| `dataset_recipe.yaml` | Reproducible definition |",
        "",
    ]
    return "\n".join(lines)


def _csv_string(headers: Sequence[str], rows: Sequence[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(headers),
                            extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({h: ("" if row.get(h) is None else row.get(h))
                         for h in headers})
    return buffer.getvalue()


def export_research_bundle(
    dataset: Dataset,
    path: str | Path,
    *,
    quality: DataQualityReport | None = None,
) -> BundleResult:
    """One-click ZIP containing everything needed to use and re-run the data."""
    from .excel import export_workbook
    from .html_report import export_report

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    root = dataset.name
    lineage = dataset.lineage or dataset.build_lineage()
    entries: list[str] = []
    skipped: list[str] = []

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        xlsx = tmpdir / "data.xlsx"
        html = tmpdir / "report.html"
        export_workbook(dataset, xlsx, quality=quality)
        export_report(dataset, html, quality=quality)

        parquet: Path | None = tmpdir / "data.parquet"
        try:
            export_parquet(dataset, parquet)
        except ExportUnavailable as exc:
            skipped.append(f"data.parquet ({exc})")
            parquet = None

        headers, rows = _headers_and_rows(dataset)

        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as bundle:
            def write(name: str, data: str | bytes) -> None:
                target = f"{root}/{name}"
                bundle.writestr(
                    target, data.encode("utf-8") if isinstance(data, str) else data)
                entries.append(target)

            write("data.xlsx", xlsx.read_bytes())
            write("report.html", html.read_bytes())
            if parquet is not None:
                write("data.parquet", parquet.read_bytes())
            write("data.csv", _csv_string(headers, rows))

            write("metadata.csv", _csv_string(
                ["variable_name", "provider", "series_id", "series_title",
                 "definition", "unit", "frequency", "coverage_start",
                 "coverage_end", "retrieved_at", "source_reference"],
                [{
                    "variable_name": l.column, "provider": l.provider,
                    "series_id": l.series_id, "series_title": l.series_title,
                    "definition": l.definition, "unit": l.unit,
                    "frequency": l.frequency.value,
                    "coverage_start": l.coverage_start,
                    "coverage_end": l.coverage_end,
                    "retrieved_at": l.retrieved_at.isoformat(),
                    "source_reference": l.source_reference,
                } for l in lineage]))

            write("sources.csv", _csv_string(
                ["variable_name", "provider", "series_id", "source_reference",
                 "retrieved_at", "citation"],
                [{
                    "variable_name": l.column, "provider": l.provider,
                    "series_id": l.series_id,
                    "source_reference": l.source_reference,
                    "retrieved_at": l.retrieved_at.isoformat(),
                    "citation": l.citation,
                } for l in lineage]))

            write("transformations.json", json.dumps([
                {
                    "step": i,
                    "operation": t.operation,
                    "parameters": t.parameters,
                    "input_columns": t.input_columns,
                    "output_columns": t.output_columns,
                    "timestamp": t.timestamp.isoformat(),
                    "lossy": t.lossy,
                    "note": t.note,
                }
                for i, t in enumerate(dataset.transformations, start=1)
            ], indent=2, ensure_ascii=False, default=str))

            write("dataset_recipe.yaml", recipe_from_dataset(dataset).to_yaml())
            write("README.md", _bundle_readme(dataset, quality))

    return BundleResult(path=path, entries=entries, skipped=skipped)
