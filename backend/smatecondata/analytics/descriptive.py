"""Descriptive statistics (spec 10, 11).

Explicitly descriptive, never inferential. The spec draws a hard boundary at
section 3: no model estimation, no significance testing presented as a feature,
no forecasting. What lives here is what a researcher needs to *understand the
data they just retrieved*.

Missing values are excluded from every statistic and reported separately --
they are never imputed (spec 9).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

DEFAULT_PERCENTILES: tuple[float, ...] = (0.10, 0.25, 0.50, 0.75, 0.90)


def _clean(values: Iterable[float | None]) -> list[float]:
    """Finite numeric values only. NaN and inf are treated as missing."""
    out: list[float] = []
    for v in values:
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out.append(f)
    return out


def quantile(sorted_values: Sequence[float], q: float) -> float | None:
    """Linear-interpolation quantile (the ``numpy`` / R type-7 definition)."""
    n = len(sorted_values)
    if n == 0:
        return None
    if n == 1:
        return float(sorted_values[0])
    pos = (n - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(sorted_values[int(pos)])
    frac = pos - lo
    return float(sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac)


@dataclass
class DescriptiveStats:
    """Descriptive summary of one variable."""

    variable: str
    count: int = 0
    missing: int = 0
    mean: float | None = None
    median: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    variance: float | None = None
    std_dev: float | None = None
    q1: float | None = None
    q3: float | None = None
    iqr: float | None = None
    percentiles: dict[str, float | None] = field(default_factory=dict)
    coefficient_of_variation: float | None = None
    skewness: float | None = None
    kurtosis: float | None = None
    total: float | None = None
    first_period: str | None = None
    last_period: str | None = None
    periods_available: int = 0
    unit: str | None = None

    @property
    def missing_pct(self) -> float | None:
        total = self.count + self.missing
        return None if total == 0 else round(100.0 * self.missing / total, 2)

    def as_row(self) -> dict[str, object]:
        """Flat mapping for the Excel ``Descriptive_Stats`` sheet."""
        row: dict[str, object] = {
            "variable": self.variable,
            "unit": self.unit,
            "count": self.count,
            "missing": self.missing,
            "missing_pct": self.missing_pct,
            "mean": self.mean,
            "median": self.median,
            "std_dev": self.std_dev,
            "variance": self.variance,
            "min": self.minimum,
            "q1": self.q1,
            "q3": self.q3,
            "max": self.maximum,
            "iqr": self.iqr,
            "cv": self.coefficient_of_variation,
            "skewness": self.skewness,
            "kurtosis": self.kurtosis,
            "sum": self.total,
            "first_period": self.first_period,
            "last_period": self.last_period,
            "periods_available": self.periods_available,
        }
        for label, value in self.percentiles.items():
            row[f"p{label}"] = value
        return row


def describe(
    values: Sequence[float | None],
    *,
    variable: str = "value",
    periods: Sequence[str] | None = None,
    unit: str | None = None,
    percentiles: Sequence[float] = DEFAULT_PERCENTILES,
    sum_is_meaningful: bool = False,
) -> DescriptiveStats:
    """Describe one variable.

    ``sum_is_meaningful`` defaults to False: summing a rate or an index is
    nonsense, so the total is only reported when the caller says the variable
    is an additive quantity.
    """
    clean = _clean(values)
    stats = DescriptiveStats(
        variable=variable,
        count=len(clean),
        missing=len(values) - len(clean),
        unit=unit,
    )

    if periods:
        observed = [
            p for p, v in zip(periods, values)
            if v is not None and math.isfinite(_as_float(v))
        ]
        if observed:
            stats.first_period = min(observed)
            stats.last_period = max(observed)
            stats.periods_available = len(observed)

    if not clean:
        return stats

    n = len(clean)
    ordered = sorted(clean)
    mean = sum(clean) / n

    stats.mean = mean
    stats.minimum = ordered[0]
    stats.maximum = ordered[-1]
    stats.median = quantile(ordered, 0.5)
    stats.q1 = quantile(ordered, 0.25)
    stats.q3 = quantile(ordered, 0.75)
    if stats.q1 is not None and stats.q3 is not None:
        stats.iqr = stats.q3 - stats.q1
    stats.percentiles = {
        f"{int(round(q * 100))}": quantile(ordered, q) for q in percentiles
    }

    if sum_is_meaningful:
        stats.total = sum(clean)

    # Sample (n-1) variance: these are observed samples of a series, and a
    # single observation has no dispersion to report.
    if n > 1:
        variance = sum((x - mean) ** 2 for x in clean) / (n - 1)
        stats.variance = variance
        stats.std_dev = math.sqrt(variance)

        # CV is only meaningful for a ratio-scale variable with a non-zero
        # mean; a series straddling zero makes it uninterpretable.
        if stats.std_dev is not None and mean != 0 and ordered[0] >= 0:
            stats.coefficient_of_variation = stats.std_dev / abs(mean)

    stats.skewness = _skewness(clean, mean)
    stats.kurtosis = _excess_kurtosis(clean, mean)
    return stats


def _as_float(v: object) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")


def _skewness(values: Sequence[float], mean: float) -> float | None:
    """Sample skewness (the adjusted Fisher-Pearson standardised moment G1)."""
    n = len(values)
    if n < 3:
        return None
    m2 = sum((x - mean) ** 2 for x in values) / n
    if m2 == 0:
        return None
    m3 = sum((x - mean) ** 3 for x in values) / n
    g1 = m3 / (m2 ** 1.5)
    return math.sqrt(n * (n - 1)) / (n - 2) * g1


def _excess_kurtosis(values: Sequence[float], mean: float) -> float | None:
    """Sample excess kurtosis (G2); 0 for a normal distribution."""
    n = len(values)
    if n < 4:
        return None
    m2 = sum((x - mean) ** 2 for x in values) / n
    if m2 == 0:
        return None
    m4 = sum((x - mean) ** 4 for x in values) / n
    g2 = m4 / (m2 ** 2) - 3.0
    return ((n - 1) * ((n + 1) * g2 + 6)) / ((n - 2) * (n - 3))


# --------------------------------------------------------------------------
# Correlation / covariance (spec 11)
# --------------------------------------------------------------------------


def _pairwise(x: Sequence[float | None],
              y: Sequence[float | None]) -> tuple[list[float], list[float]]:
    """Pairwise-complete observations, the standard choice for panel data."""
    xs: list[float] = []
    ys: list[float] = []
    for a, b in zip(x, y):
        if a is None or b is None:
            continue
        fa, fb = _as_float(a), _as_float(b)
        if math.isfinite(fa) and math.isfinite(fb):
            xs.append(fa)
            ys.append(fb)
    return xs, ys


def covariance(x: Sequence[float | None], y: Sequence[float | None]) -> float | None:
    xs, ys = _pairwise(x, y)
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / (n - 1)


def pearson(x: Sequence[float | None], y: Sequence[float | None]) -> float | None:
    xs, ys = _pairwise(x, y)
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    sy = math.sqrt(sum((b - my) ** 2 for b in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / (sx * sy)


def _rank(values: Sequence[float]) -> list[float]:
    """Average ranks, so tied values do not distort Spearman."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def spearman(x: Sequence[float | None], y: Sequence[float | None]) -> float | None:
    xs, ys = _pairwise(x, y)
    if len(xs) < 2:
        return None
    return pearson(_rank(xs), _rank(ys))


def correlation_matrix(
    columns: dict[str, Sequence[float | None]],
    method: str = "pearson",
) -> dict[str, dict[str, float | None]]:
    """Square correlation matrix over ``columns``.

    ``method`` is ``pearson`` or ``spearman``. Kendall is intentionally left
    out until it is needed; a wrong tau is worse than an absent one.
    """
    funcs = {"pearson": pearson, "spearman": spearman}
    if method not in funcs:
        raise ValueError(f"unsupported correlation method: {method!r}")
    func = funcs[method]

    names = list(columns)
    matrix: dict[str, dict[str, float | None]] = {}
    for a in names:
        row: dict[str, float | None] = {}
        for b in names:
            row[b] = 1.0 if a == b else func(columns[a], columns[b])
        matrix[a] = row
    return matrix


# --------------------------------------------------------------------------
# Outlier flags (inspection only -- spec 11)
# --------------------------------------------------------------------------


@dataclass
class OutlierFlag:
    index: int
    period: str | None
    value: float
    reason: str
    score: float


def zscore_flags(values: Sequence[float | None],
                 periods: Sequence[str] | None = None,
                 threshold: float = 3.0) -> list[OutlierFlag]:
    clean = _clean(values)
    if len(clean) < 3:
        return []
    mean = sum(clean) / len(clean)
    variance = sum((x - mean) ** 2 for x in clean) / (len(clean) - 1)
    sd = math.sqrt(variance)
    if sd == 0:
        return []

    flags: list[OutlierFlag] = []
    for i, v in enumerate(values):
        if v is None:
            continue
        f = _as_float(v)
        if not math.isfinite(f):
            continue
        z = (f - mean) / sd
        if abs(z) >= threshold:
            flags.append(OutlierFlag(
                index=i,
                period=periods[i] if periods and i < len(periods) else None,
                value=f,
                reason=f"|z| >= {threshold:g}",
                score=round(z, 3),
            ))
    return flags


def iqr_flags(values: Sequence[float | None],
              periods: Sequence[str] | None = None,
              multiplier: float = 1.5) -> list[OutlierFlag]:
    clean = sorted(_clean(values))
    if len(clean) < 4:
        return []
    q1 = quantile(clean, 0.25)
    q3 = quantile(clean, 0.75)
    if q1 is None or q3 is None:
        return []
    iqr = q3 - q1
    if iqr == 0:
        return []
    low = q1 - multiplier * iqr
    high = q3 + multiplier * iqr

    flags: list[OutlierFlag] = []
    for i, v in enumerate(values):
        if v is None:
            continue
        f = _as_float(v)
        if not math.isfinite(f) or low <= f <= high:
            continue
        distance = (low - f) / iqr if f < low else (f - high) / iqr
        flags.append(OutlierFlag(
            index=i,
            period=periods[i] if periods and i < len(periods) else None,
            value=f,
            reason=f"outside [Q1-{multiplier:g}*IQR, Q3+{multiplier:g}*IQR]",
            score=round(distance, 3),
        ))
    return flags
