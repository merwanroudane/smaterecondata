"""Automatic exploratory data analysis (spec 10A).

Wraps the ``fg-data-profiling`` package behind a service adapter, so neither
the API nor the UI is coupled to a third-party library. Two things the spec is
emphatic about, and this module enforces:

* **Profiling must never take the dataset down.** The library is optional and
  every failure path is caught -- a profiling error returns a status, not an
  exception, and the user keeps the data they retrieved.
* **It stays descriptive.** Data-quality EDA only. No modelling.

Above the generic report sits an economic-data summary that recognises the
roles this product actually produces: a geography column, a period column, and
indicator columns carrying units and frequencies from provider metadata.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ..datasets.builder import Dataset
from ..datasets.validation import period_sort_key
from . import missingness as missingness_mod

logger = logging.getLogger(__name__)

# Above this many cells, profile a sample and say so.
DEFAULT_SAMPLE_THRESHOLD = 250_000
DEFAULT_SAMPLE_ROWS = 10_000


class ProfileMode(str, Enum):
    QUICK = "quick"
    FULL = "full"


class ProfileStatus(str, Enum):
    OK = "ok"
    UNAVAILABLE = "unavailable"   # library not installed
    FAILED = "failed"             # library raised
    SKIPPED = "skipped"           # nothing to profile


@dataclass
class ProfileResult:
    status: ProfileStatus
    mode: ProfileMode = ProfileMode.QUICK
    message: str | None = None
    sampled: bool = False
    sample_rows: int | None = None
    total_rows: int = 0
    summary: dict[str, Any] = field(default_factory=dict)
    html_path: Path | None = None
    json_path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.status is ProfileStatus.OK

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "mode": self.mode.value,
            "message": self.message,
            "sampled": self.sampled,
            "sample_rows": self.sample_rows,
            "total_rows": self.total_rows,
            "summary": self.summary,
            "html_path": str(self.html_path) if self.html_path else None,
            "json_path": str(self.json_path) if self.json_path else None,
        }


def profiling_available() -> tuple[bool, str | None]:
    """Whether the profiling backend can be imported, and why not if it can't.

    The message is deliberately specific. The most common failure today is not
    a missing package but ``pkg_resources``: fg-data-profiling still imports
    it, and setuptools removed it in v81, so a fully installed package can
    still fail to import.
    """
    try:
        import data_profiling  # noqa: F401
    except ModuleNotFoundError as exc:
        if exc.name == "pkg_resources":
            return False, (
                "fg-data-profiling is installed but cannot import: it requires "
                "'pkg_resources', which setuptools removed in v81. Fix with: "
                "pip install 'setuptools<81'"
            )
        if exc.name and exc.name.startswith("data_profiling"):
            return False, (
                "fg-data-profiling is not installed. "
                "Install it with: pip install -U fg-data-profiling"
            )
        return False, f"fg-data-profiling could not be imported: {exc}"
    except ImportError as exc:
        return False, f"fg-data-profiling could not be imported: {exc}"

    try:
        import pandas  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        return False, f"pandas is required for profiling ({exc})"
    return True, None


class ProfilingService:
    """Adapter over the profiling library (spec 10A).

    ``profile_dataset`` is the entry point; ``economic_summary`` is usable on
    its own and needs no third-party package at all, so the workspace always
    has something meaningful to show even when profiling is unavailable.
    """

    def __init__(self, *,
                 sample_threshold: int = DEFAULT_SAMPLE_THRESHOLD,
                 sample_rows: int = DEFAULT_SAMPLE_ROWS):
        self.sample_threshold = sample_threshold
        self.sample_rows = sample_rows

    # -- economic-data-aware layer (no third-party dependency) -----------

    def economic_summary(self, dataset: Dataset) -> dict[str, Any]:
        """Dataset roles a generic profiler cannot infer (spec 10A)."""
        columns, rows = dataset.to_wide()
        indicator_columns = [
            c for c in columns if c not in ("geography", "iso3", "period")
        ]
        periods = sorted({str(r["period"]) for r in rows}, key=period_sort_key)
        geographies = sorted({str(r["iso3"] or r["geography"]) for r in rows})
        miss = missingness_mod.analyse(dataset)

        # A balanced panel has an observation slot for every country-period.
        expected = len(geographies) * len(periods)
        duplicate_keys = len(rows) - len({(r["iso3"], r["period"]) for r in rows})

        return {
            "shape": dataset.spec.output_shape.value,
            "rows": len(rows),
            "identifier_columns": {
                "geography": "iso3",
                "period": "period",
            },
            "indicator_columns": [
                {
                    "column": v.alias,
                    "title": v.metadata.title,
                    "provider": v.metadata.provider,
                    "series_id": v.metadata.series_id,
                    "unit": v.metadata.unit,
                    "frequency": v.metadata.frequency.value,
                }
                for v in dataset.variables
            ],
            "geographies": geographies,
            "geography_count": len(geographies),
            "periods": {"first": periods[0] if periods else None,
                        "last": periods[-1] if periods else None,
                        "count": len(periods)},
            "panel": {
                "expected_rows": expected,
                "actual_rows": len(rows),
                "balanced": expected == len(rows) and duplicate_keys == 0,
                "duplicate_country_period_keys": duplicate_keys,
            },
            "coverage_pct": miss.coverage_pct,
            "missing_cells": miss.missing_cells,
            "coverage_by_variable": {
                v.variable: v.coverage_pct for v in miss.by_variable
            },
            "coverage_by_geography": {
                g.geography: g.coverage_pct for g in miss.by_geography
            },
            "frequencies": sorted(f.value for f in dataset.frequencies),
            "units": sorted({v.metadata.unit for v in dataset.variables
                             if v.metadata.unit}),
            "note": ("Descriptive data-quality summary only. No model "
                     "estimation is performed."),
        }

    # -- full profiling ---------------------------------------------------

    def profile_dataset(
        self,
        dataset: Dataset,
        *,
        mode: ProfileMode = ProfileMode.QUICK,
        html_path: str | Path | None = None,
        json_path: str | Path | None = None,
        title: str | None = None,
        correlations: bool = True,
    ) -> ProfileResult:
        """Run automatic EDA. Never raises."""
        economic = self.economic_summary(dataset)
        columns, rows = dataset.to_wide()

        if not rows:
            return ProfileResult(
                status=ProfileStatus.SKIPPED, mode=mode,
                message="The dataset has no rows to profile.",
                summary={"economic": economic})

        available, reason = profiling_available()
        if not available:
            # Degrade to the economic summary rather than failing outright.
            return ProfileResult(
                status=ProfileStatus.UNAVAILABLE, mode=mode, message=reason,
                total_rows=len(rows), summary={"economic": economic})

        try:
            import pandas as pd
            from data_profiling import ProfileReport
        except Exception as exc:  # pragma: no cover - guarded above
            return ProfileResult(
                status=ProfileStatus.UNAVAILABLE, mode=mode,
                message=f"{type(exc).__name__}: {exc}",
                total_rows=len(rows), summary={"economic": economic})

        frame = pd.DataFrame(rows, columns=list(columns))
        total_rows = len(frame)

        sampled = False
        cells = total_rows * max(len(columns), 1)
        if cells > self.sample_threshold and total_rows > self.sample_rows:
            frame = frame.sample(n=self.sample_rows, random_state=0)
            sampled = True

        try:
            report = ProfileReport(
                frame,
                title=title or f"SmatEconData — {dataset.name}",
                explorative=(mode is ProfileMode.FULL),
                minimal=(mode is ProfileMode.QUICK),
                correlations=None if correlations else {
                    "auto": {"calculate": False},
                },
                progress_bar=False,
            )
            raw = json.loads(report.to_json())
        except Exception as exc:
            # Spec 10A: a third-party profiling failure must never destroy the
            # user's retrieved dataset.
            logger.warning("profiling failed for %s: %s", dataset.name, exc)
            return ProfileResult(
                status=ProfileStatus.FAILED, mode=mode,
                message=f"{type(exc).__name__}: {exc}",
                sampled=sampled, total_rows=total_rows,
                summary={"economic": economic})

        result = ProfileResult(
            status=ProfileStatus.OK, mode=mode, sampled=sampled,
            sample_rows=len(frame) if sampled else None,
            total_rows=total_rows,
            summary={
                "economic": economic,
                "table": _compact_table(raw),
                "alerts": _compact_alerts(raw),
                "variables": _compact_variables(raw),
            },
        )
        if sampled:
            result.message = (
                f"Profiled a random sample of {len(frame):,} of {total_rows:,} "
                f"rows. Statistics describe the sample, not the full dataset."
            )

        for path, writer in ((html_path, report.to_file), (json_path, None)):
            if path is None:
                continue
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if writer is not None:
                    writer(path)
                    result.html_path = path
                else:
                    path.write_text(json.dumps(raw, indent=2, default=str),
                                    encoding="utf-8")
                    result.json_path = path
            except Exception as exc:
                logger.warning("could not write profile artefact %s: %s", path, exc)
                result.message = ((result.message or "") +
                                  f" Could not write {path.name}: {exc}").strip()
        return result


def _compact_table(raw: dict[str, Any]) -> dict[str, Any]:
    """Headline numbers only -- an MCP client must not receive a whole report."""
    table = raw.get("table") or {}
    keep = ("n", "n_var", "memory_size", "n_cells_missing", "p_cells_missing",
            "n_vars_with_missing", "n_duplicates", "p_duplicates")
    return {k: table[k] for k in keep if k in table}


def _compact_alerts(raw: dict[str, Any], limit: int = 30) -> list[str]:
    alerts = raw.get("alerts") or []
    return [str(a) for a in alerts[:limit]]


def _compact_variables(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    keep = ("type", "n_distinct", "p_distinct", "n_missing", "p_missing",
            "mean", "std", "min", "max", "n_zeros", "n_negative")
    out: dict[str, dict[str, Any]] = {}
    for name, block in (raw.get("variables") or {}).items():
        if isinstance(block, dict):
            out[name] = {k: block[k] for k in keep if k in block}
    return out


_service: ProfilingService | None = None


def get_profiling_service() -> ProfilingService:
    global _service
    if _service is None:
        _service = ProfilingService()
    return _service
