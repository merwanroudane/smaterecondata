"""Missing-data explorer (spec 9).

Reports where the holes are, by variable, by geography and over time, and
builds the matrices the UI and HTML report render as heatmaps.

This module never imputes. If a user later asks for a convenience fill, the
filled column must be marked and the original kept alongside it -- which is why
:func:`fill_marked` returns a new column plus a flag column rather than
overwriting anything.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Sequence

from ..datasets.builder import Dataset
from ..datasets.validation import period_sort_key


@dataclass
class VariableMissingness:
    variable: str
    total: int = 0
    missing: int = 0
    first_valid: str | None = None
    last_valid: str | None = None
    longest_gap: int = 0
    longest_gap_span: tuple[str, str] | None = None
    longest_gap_geography: str | None = None
    missing_periods: list[str] = field(default_factory=list)

    @property
    def present(self) -> int:
        return self.total - self.missing

    @property
    def missing_pct(self) -> float:
        return 0.0 if self.total == 0 else round(100.0 * self.missing / self.total, 2)

    @property
    def coverage_pct(self) -> float:
        return round(100.0 - self.missing_pct, 2)


@dataclass
class GeographyMissingness:
    geography: str
    total: int = 0
    missing: int = 0

    @property
    def missing_pct(self) -> float:
        return 0.0 if self.total == 0 else round(100.0 * self.missing / self.total, 2)

    @property
    def coverage_pct(self) -> float:
        return round(100.0 - self.missing_pct, 2)


@dataclass
class MissingnessReport:
    by_variable: list[VariableMissingness] = field(default_factory=list)
    by_geography: list[GeographyMissingness] = field(default_factory=list)
    total_cells: int = 0
    missing_cells: int = 0
    periods: list[str] = field(default_factory=list)
    variables: list[str] = field(default_factory=list)
    # matrix[variable][period] -> present count / expected count
    coverage_matrix: dict[str, dict[str, float]] = field(default_factory=dict)
    # matrix[variable][geography] -> True when the cell is missing
    missingness_matrix: dict[str, dict[str, bool]] = field(default_factory=dict)

    @property
    def missing_pct(self) -> float:
        if self.total_cells == 0:
            return 0.0
        return round(100.0 * self.missing_cells / self.total_cells, 2)

    @property
    def coverage_pct(self) -> float:
        return round(100.0 - self.missing_pct, 2)

    def as_rows(self) -> list[dict[str, object]]:
        """Flat rows for the Excel ``Missing_Data`` sheet."""
        rows: list[dict[str, object]] = []
        for v in self.by_variable:
            rows.append({
                "scope": "variable",
                "name": v.variable,
                "observations": v.total,
                "present": v.present,
                "missing": v.missing,
                "missing_pct": v.missing_pct,
                "coverage_pct": v.coverage_pct,
                "first_valid": v.first_valid,
                "last_valid": v.last_valid,
                "longest_gap": v.longest_gap,
                "longest_gap_span": (
                    " to ".join(v.longest_gap_span) if v.longest_gap_span else None
                ),
                "longest_gap_geography": v.longest_gap_geography,
            })
        for g in self.by_geography:
            rows.append({
                "scope": "geography",
                "name": g.geography,
                "observations": g.total,
                "present": g.total - g.missing,
                "missing": g.missing,
                "missing_pct": g.missing_pct,
                "coverage_pct": g.coverage_pct,
            })
        return rows


def analyse(dataset: Dataset) -> MissingnessReport:
    """Full missingness analysis of an assembled dataset."""
    columns, rows = dataset.to_wide()
    aliases = [c for c in columns if c not in ("geography", "iso3", "period")]
    report = MissingnessReport(variables=list(aliases))

    if not rows:
        return report

    periods = sorted({str(r["period"]) for r in rows}, key=period_sort_key)
    report.periods = periods

    # -- by variable ------------------------------------------------------
    # Gaps are measured per (variable, geography). Pooling geographies would
    # hide a hole: if Algeria is missing 2002-03 but Morocco has both years,
    # the period looks "present" and the gap silently disappears.
    for alias in aliases:
        stat = VariableMissingness(variable=alias)
        valid_by_geo: dict[str, list[str]] = defaultdict(list)

        for row in rows:
            stat.total += 1
            geo = str(row["iso3"] or row["geography"])
            if row.get(alias) is None:
                stat.missing += 1
            else:
                valid_by_geo[geo].append(str(row["period"]))

        all_valid = [p for ps in valid_by_geo.values() for p in ps]
        if all_valid:
            ordered_all = sorted(set(all_valid), key=period_sort_key)
            stat.first_valid = ordered_all[0]
            stat.last_valid = ordered_all[-1]

            missing_seen: set[str] = set()
            for geo, geo_periods in sorted(valid_by_geo.items()):
                ordered = sorted(set(geo_periods), key=period_sort_key)
                # Longest run of consecutive missing periods *inside* this
                # geography's own coverage window.
                inside = [
                    p for p in periods
                    if period_sort_key(ordered[0]) <= period_sort_key(p)
                    <= period_sort_key(ordered[-1])
                ]
                have = set(ordered)
                run = 0
                run_start: str | None = None
                for period in inside:
                    if period in have:
                        run = 0
                        run_start = None
                        continue
                    run += 1
                    if run_start is None:
                        run_start = period
                    if run > stat.longest_gap:
                        stat.longest_gap = run
                        stat.longest_gap_span = (run_start, period)
                        stat.longest_gap_geography = geo
                    missing_seen.add(period)
            stat.missing_periods = sorted(missing_seen, key=period_sort_key)

        report.by_variable.append(stat)
        report.total_cells += stat.total
        report.missing_cells += stat.missing

    # -- by geography -----------------------------------------------------
    per_geo: dict[str, GeographyMissingness] = {}
    for row in rows:
        geo = str(row["iso3"] or row["geography"])
        entry = per_geo.setdefault(geo, GeographyMissingness(geography=geo))
        for alias in aliases:
            entry.total += 1
            if row.get(alias) is None:
                entry.missing += 1
    report.by_geography = [per_geo[k] for k in sorted(per_geo)]

    # -- matrices ---------------------------------------------------------
    geographies = sorted({str(r["iso3"] or r["geography"]) for r in rows})
    present_by: dict[tuple[str, str], int] = defaultdict(int)
    expected_by: dict[tuple[str, str], int] = defaultdict(int)
    missing_geo: dict[tuple[str, str], int] = defaultdict(int)
    total_geo: dict[tuple[str, str], int] = defaultdict(int)

    for row in rows:
        period = str(row["period"])
        geo = str(row["iso3"] or row["geography"])
        for alias in aliases:
            expected_by[(alias, period)] += 1
            total_geo[(alias, geo)] += 1
            if row.get(alias) is None:
                missing_geo[(alias, geo)] += 1
            else:
                present_by[(alias, period)] += 1

    report.coverage_matrix = {
        alias: {
            period: (
                round(100.0 * present_by[(alias, period)] / expected_by[(alias, period)], 1)
                if expected_by[(alias, period)] else 0.0
            )
            for period in periods
        }
        for alias in aliases
    }
    report.missingness_matrix = {
        alias: {
            geo: total_geo[(alias, geo)] > 0
            and missing_geo[(alias, geo)] == total_geo[(alias, geo)]
            for geo in geographies
        }
        for alias in aliases
    }
    return report


def fill_marked(values: Sequence[float | None],
                method: str = "forward") -> tuple[list[float | None], list[bool]]:
    """Optional convenience fill that is always disclosed (spec 9).

    Returns ``(filled, was_filled)``. The caller keeps the original column and
    exports the flag beside it, so a generated value can never be mistaken for
    an observed one.
    """
    if method not in {"forward", "backward"}:
        raise ValueError(f"unsupported fill method: {method!r}")

    filled: list[float | None] = list(values)
    flags = [False] * len(values)
    order = range(len(values)) if method == "forward" else range(len(values) - 1, -1, -1)

    carry: float | None = None
    for i in order:
        if filled[i] is not None:
            carry = filled[i]
        elif carry is not None:
            filled[i] = carry
            flags[i] = True
    return filled, flags
