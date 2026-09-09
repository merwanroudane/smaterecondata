"""Dataset validation and quality checks (spec 8).

Validation here is about *data integrity*, never econometric assumptions. It
answers "can a researcher trust the shape of this table?" -- duplicates, gaps,
mixed frequencies, conflicting units -- and returns structured, severity-ranked
issues rather than raising.

Nothing in this module repairs anything. Detecting a problem and silently
fixing it would defeat the point.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

from ..core.models import (
    DataQualityReport,
    Frequency,
    Severity,
    ValidationIssue,
)
from .builder import Dataset

# Period shapes we can order and reason about.
_ANNUAL = re.compile(r"^(\d{4})$")
_QUARTERLY = re.compile(r"^(\d{4})[-]?Q([1-4])$", re.IGNORECASE)
_MONTHLY = re.compile(r"^(\d{4})[-](\d{2})$")
_DAILY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

# Units that are conceptually incompatible even when the titles look alike.
_UNIT_FAMILIES: list[tuple[str, tuple[str, ...]]] = [
    ("percent", ("%", "percent", "pct", "annual %")),
    ("index", ("index", "idx", "2010 = 100", "2015 = 100")),
    ("current_usd", ("current us$", "current usd")),
    ("constant_usd", ("constant us$", "constant usd", "constant 2015 us$")),
    ("ppp", ("ppp",)),
    ("lcu", ("lcu", "local currency")),
    ("people", ("people", "persons", "number")),
]


def unit_family(unit: str | None) -> str | None:
    if not unit:
        return None
    lowered = unit.lower()
    for family, needles in _UNIT_FAMILIES:
        if any(n in lowered for n in needles):
            return family
    return None


def period_sort_key(period: str) -> tuple[int, int]:
    """Sortable ``(year, sub-period)`` for the period formats we support."""
    if m := _ANNUAL.match(period):
        return int(m.group(1)), 0
    if m := _QUARTERLY.match(period):
        return int(m.group(1)), int(m.group(2))
    if m := _MONTHLY.match(period):
        return int(m.group(1)), int(m.group(2))
    if m := _DAILY.match(period):
        return int(m.group(1)), int(m.group(2)) * 100 + int(m.group(3))
    return 0, 0


def infer_frequency(period: str) -> Frequency:
    if _ANNUAL.match(period):
        return Frequency.ANNUAL
    if _QUARTERLY.match(period):
        return Frequency.QUARTERLY
    if _MONTHLY.match(period):
        return Frequency.MONTHLY
    if _DAILY.match(period):
        return Frequency.DAILY
    return Frequency.UNKNOWN


def _median(values: list[float]) -> float:
    """Median of a non-empty list."""
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _modified_z_scores(values: list[float]) -> list[float] | None:
    """Iglewicz-Hoaglin modified z-scores, or None when not computable.

    ``0.6745 * (x - median) / MAD``. When more than half the values are
    identical the MAD is exactly zero and that formula degenerates, so the
    published fallback based on the mean absolute deviation is used instead:
    ``(x - median) / (1.253314 * MeanAD)``. A series that is genuinely
    constant has no outliers and yields None.

    Applied to levels, a strongly trending series can flag its own endpoints;
    these are INFO-level hints for inspection, never corrections.
    """
    n = len(values)
    if n < 5:
        return None
    centre = _median(values)
    deviations = [abs(v - centre) for v in values]

    mad = _median(deviations)
    if mad > 0:
        return [0.6745 * (v - centre) / mad for v in values]

    mean_ad = sum(deviations) / n
    if mean_ad > 0:
        return [(v - centre) / (1.253314 * mean_ad) for v in values]
    return None


def _expected_step(frequency: Frequency) -> int | None:
    """Periods per year, used to detect gaps in an otherwise regular series."""
    return frequency.periods_per_year


class DatasetValidator:
    """Run every spec-8 check and return a :class:`DataQualityReport`."""

    def __init__(self, *, jump_threshold: float = 3.5):
        # 3.5 is the conventional Iglewicz-Hoaglin cut-off. This flags a value
        # for *inspection*; it never claims the observation is wrong.
        self.jump_threshold = jump_threshold

    def validate(self, dataset: Dataset) -> DataQualityReport:
        issues: list[ValidationIssue] = []

        duplicates = self._check_duplicates(dataset, issues)
        self._check_invalid_periods(dataset, issues)
        self._check_non_numeric(dataset, issues)
        frequency_conflicts = self._check_frequency(dataset, issues)
        unit_conflicts = self._check_units(dataset, issues)
        self._check_empty_variables(dataset, issues)
        self._check_gaps(dataset, issues)
        self._check_short_coverage(dataset, issues)
        self._check_extreme_jumps(dataset, issues)
        self._check_provider_flags(dataset, issues)

        columns, rows = dataset.to_wide()
        value_columns = [c for c in columns if c not in ("geography", "iso3", "period")]
        total_cells = len(rows) * len(value_columns)
        missing_cells = sum(
            1 for r in rows for c in value_columns if r.get(c) is None
        )
        coverage = (
            None if total_cells == 0
            else round(100.0 * (total_cells - missing_cells) / total_cells, 2)
        )

        report = DataQualityReport(
            rows=len(rows),
            countries=len(dataset.geographies),
            indicators=len(dataset.variables),
            requested_period=dataset.spec.period_label(),
            coverage_pct=coverage,
            duplicate_keys=duplicates,
            missing_cells=missing_cells,
            frequency_conflicts=frequency_conflicts,
            unit_conflicts=unit_conflicts,
            issues=issues,
        )
        report.status = (
            "unusable" if report.errors
            else "usable_with_warnings" if report.warnings
            else "clean"
        )
        return report

    # -- individual checks ------------------------------------------------

    def _check_duplicates(self, dataset: Dataset,
                          issues: list[ValidationIssue]) -> int:
        counts: Counter[tuple[str, str, str]] = Counter()
        for obs in dataset.observations:
            counts[(obs.iso3 or obs.geography, obs.period,
                    f"{obs.provider}:{obs.series_id}")] += 1

        duplicates = {k: n for k, n in counts.items() if n > 1}
        for (geo, period, series), n in sorted(duplicates.items())[:20]:
            issues.append(ValidationIssue(
                code="duplicate_key",
                severity=Severity.ERROR,
                message=(f"{n} observations for {series} / {geo} / {period}. "
                         f"The first was kept; the duplicates were not merged."),
                geography=geo,
                details={"period": period, "series": series, "count": n},
            ))
        return len(duplicates)

    def _check_invalid_periods(self, dataset: Dataset,
                               issues: list[ValidationIssue]) -> None:
        bad = sorted({
            obs.period for obs in dataset.observations
            if infer_frequency(obs.period) is Frequency.UNKNOWN
        })
        if bad:
            issues.append(ValidationIssue(
                code="invalid_period",
                severity=Severity.ERROR,
                message=(f"{len(bad)} period label(s) are not a recognised "
                         f"date format: {', '.join(bad[:5])}"),
                details={"periods": bad[:50]},
            ))

    def _check_non_numeric(self, dataset: Dataset,
                           issues: list[ValidationIssue]) -> None:
        bad = 0
        for obs in dataset.observations:
            if obs.value is None:
                continue
            if not isinstance(obs.value, (int, float)) or not math.isfinite(obs.value):
                bad += 1
        if bad:
            issues.append(ValidationIssue(
                code="non_numeric_value",
                severity=Severity.ERROR,
                message=f"{bad} observation(s) hold a non-finite numeric value.",
                details={"count": bad},
            ))

    def _check_frequency(self, dataset: Dataset,
                         issues: list[ValidationIssue]) -> int:
        conflicts = 0
        for variable in dataset.variables:
            observed = {
                infer_frequency(o.period)
                for o in dataset.observations
                if o.provider == variable.metadata.provider
                and o.series_id == variable.metadata.series_id
            }
            observed.discard(Frequency.UNKNOWN)
            if len(observed) > 1:
                conflicts += 1
                issues.append(ValidationIssue(
                    code="mixed_frequency",
                    severity=Severity.ERROR,
                    message=(f"'{variable.alias}' mixes "
                             f"{', '.join(sorted(f.value for f in observed))} "
                             f"periods. They were not aggregated."),
                    variable=variable.alias,
                    details={"frequencies": sorted(f.value for f in observed)},
                ))

        # Requested vs delivered frequency.
        requested = dataset.spec.frequency
        delivered = {
            infer_frequency(o.period) for o in dataset.observations
        } - {Frequency.UNKNOWN}
        if requested is not Frequency.UNKNOWN and delivered and requested not in delivered:
            conflicts += 1
            issues.append(ValidationIssue(
                code="frequency_not_available",
                severity=Severity.WARNING,
                message=(f"{requested.value} data was requested but the "
                         f"providers returned "
                         f"{', '.join(sorted(f.value for f in delivered))}. "
                         f"No conversion was applied."),
                details={"requested": requested.value,
                         "delivered": sorted(f.value for f in delivered)},
            ))
        return conflicts

    def _check_units(self, dataset: Dataset,
                     issues: list[ValidationIssue]) -> int:
        conflicts = 0

        # (a) one variable carrying more than one unit
        for variable in dataset.variables:
            units = {
                o.unit for o in dataset.observations
                if o.provider == variable.metadata.provider
                and o.series_id == variable.metadata.series_id and o.unit
            }
            if len(units) > 1:
                conflicts += 1
                issues.append(ValidationIssue(
                    code="unit_conflict",
                    severity=Severity.ERROR,
                    message=(f"'{variable.alias}' arrived with several units "
                             f"({', '.join(sorted(units))}). Nothing was converted."),
                    variable=variable.alias,
                    details={"units": sorted(units)},
                ))

        # (b) same concept, incompatible unit families across variables
        by_concept: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
        for variable in dataset.variables:
            if variable.concept:
                by_concept[variable.concept].append(
                    (variable.alias, unit_family(variable.metadata.unit))
                )
        for concept, entries in by_concept.items():
            families = {f for _, f in entries if f}
            if len(families) > 1:
                conflicts += 1
                issues.append(ValidationIssue(
                    code="incompatible_units_same_concept",
                    severity=Severity.WARNING,
                    message=(f"Variables for '{concept}' use incompatible units "
                             f"({', '.join(sorted(families))}). Compare them with care."),
                    details={"concept": concept,
                             "variables": [a for a, _ in entries]},
                ))
        return conflicts

    def _check_empty_variables(self, dataset: Dataset,
                               issues: list[ValidationIssue]) -> None:
        for variable in dataset.variables:
            values = [
                o.value for o in dataset.observations
                if o.provider == variable.metadata.provider
                and o.series_id == variable.metadata.series_id
            ]
            present = [v for v in values if v is not None]
            if not values:
                issues.append(ValidationIssue(
                    code="missing_variable",
                    severity=Severity.ERROR,
                    message=(f"'{variable.alias}' returned no observations at all."),
                    variable=variable.alias,
                ))
            elif not present:
                issues.append(ValidationIssue(
                    code="all_values_missing",
                    severity=Severity.ERROR,
                    message=(f"'{variable.alias}' returned {len(values)} rows but "
                             f"every value is missing."),
                    variable=variable.alias,
                ))
            elif len(present) / len(values) < 0.5:
                issues.append(ValidationIssue(
                    code="sparse_variable",
                    severity=Severity.WARNING,
                    message=(f"'{variable.alias}' is sparse: "
                             f"{len(present)}/{len(values)} values present."),
                    variable=variable.alias,
                    details={"present": len(present), "total": len(values)},
                ))

    def _check_gaps(self, dataset: Dataset,
                    issues: list[ValidationIssue]) -> None:
        """Interior gaps in an otherwise regular series."""
        grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
        for obs in dataset.observations:
            if obs.value is None:
                continue
            alias = None
            for v in dataset.variables:
                if (v.metadata.provider == obs.provider
                        and v.metadata.series_id == obs.series_id):
                    alias = v.alias
                    break
            if alias:
                grouped[(alias, obs.iso3 or obs.geography)].append(obs.period)

        for (alias, geo), periods in sorted(grouped.items()):
            frequency = infer_frequency(periods[0]) if periods else Frequency.UNKNOWN
            step = _expected_step(frequency)
            if step is None or len(periods) < 3:
                continue
            keys = sorted(period_sort_key(p) for p in periods)
            expected = self._expected_slots(keys, frequency)
            missing = expected - len(keys)
            if missing > 0:
                issues.append(ValidationIssue(
                    code="time_gap",
                    severity=Severity.WARNING,
                    message=(f"'{alias}' for {geo} has {missing} missing "
                             f"{frequency.value} period(s) inside its coverage."),
                    variable=alias,
                    geography=geo,
                    details={"missing_periods": missing,
                             "first": periods[0], "last": periods[-1]},
                ))

    @staticmethod
    def _expected_slots(keys: list[tuple[int, int]], frequency: Frequency) -> int:
        first, last = keys[0], keys[-1]
        if frequency is Frequency.ANNUAL:
            return last[0] - first[0] + 1
        if frequency is Frequency.QUARTERLY:
            return (last[0] * 4 + last[1]) - (first[0] * 4 + first[1]) + 1
        if frequency is Frequency.MONTHLY:
            return (last[0] * 12 + last[1]) - (first[0] * 12 + first[1]) + 1
        return len(keys)

    def _check_short_coverage(self, dataset: Dataset,
                              issues: list[ValidationIssue]) -> None:
        """A series stopping earlier than the user asked for (spec 8)."""
        end = dataset.spec.end_year
        if not end:
            return
        # Per geography: pooling them hides the case this check exists for --
        # one country's series stopping early while the others run to the end.
        for variable in dataset.variables:
            by_geo: dict[str, list[str]] = defaultdict(list)
            for o in dataset.observations:
                if (o.provider == variable.metadata.provider
                        and o.series_id == variable.metadata.series_id
                        and o.value is not None):
                    by_geo[o.iso3 or o.geography].append(o.period)

            for geo, periods in sorted(by_geo.items()):
                last_year = max(period_sort_key(p)[0] for p in periods)
                if last_year and last_year < end:
                    issues.append(ValidationIssue(
                        code="coverage_ends_early",
                        severity=Severity.WARNING,
                        message=(f"'{variable.alias}' for {geo} ends in "
                                 f"{last_year}, before the requested {end}."),
                        variable=variable.alias,
                        geography=geo,
                        details={"last_year": last_year, "requested_end": end},
                    ))

    def _check_extreme_jumps(self, dataset: Dataset,
                             issues: list[ValidationIssue]) -> None:
        """Flag implausible period-on-period moves for inspection only."""
        series: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
        for obs in dataset.observations:
            if obs.value is None:
                continue
            for v in dataset.variables:
                if (v.metadata.provider == obs.provider
                        and v.metadata.series_id == obs.series_id):
                    series[(v.alias, obs.iso3 or obs.geography)].append(
                        (obs.period, float(obs.value)))
                    break

        for (alias, geo), points in sorted(series.items()):
            if len(points) < 5:
                continue
            points.sort(key=lambda p: period_sort_key(p[0]))
            values = [v for _, v in points]

            # Iglewicz-Hoaglin modified z-score on the levels. Mean/sd is the
            # wrong tool here: a single spike inflates its own standard
            # deviation enough to hide itself, which is precisely the case this
            # check exists to catch. Levels rather than first differences,
            # because one spike produces two large differences (up then back
            # down) that mask each other.
            score = _modified_z_scores(values)
            if score is None:
                continue

            for (period, value), z in zip(points, score):
                if abs(z) >= self.jump_threshold:
                    issues.append(ValidationIssue(
                        code="extreme_jump",
                        severity=Severity.INFO,
                        message=(f"'{alias}' for {geo} has an unusual value at "
                                 f"{period} ({value:g}). Flagged for inspection "
                                 f"only; nothing was changed."),
                        variable=alias,
                        geography=geo,
                        details={"period": period, "value": value,
                                 "modified_z": round(z, 2)},
                    ))

    def _check_provider_flags(self, dataset: Dataset,
                              issues: list[ValidationIssue]) -> None:
        flagged: Counter[str] = Counter()
        for obs in dataset.observations:
            if obs.status:
                flagged[obs.status] += 1
        for status, count in flagged.most_common(10):
            issues.append(ValidationIssue(
                code="provider_flag",
                severity=Severity.INFO,
                message=f"{count} observation(s) carry the provider flag '{status}'.",
                details={"status": status, "count": count},
            ))


def validate_dataset(dataset: Dataset) -> DataQualityReport:
    return DatasetValidator().validate(dataset)
