"""Standalone HTML report (spec 16, 0L).

The report must open from a local file with no backend and no network, so
charts are emitted as inline SVG rather than pulled from a charting CDN. That
keeps it readable years later, in an air-gapped environment, and inside a
research bundle -- which is the point of a provenance-carrying export.

Light-first styling per spec 0G, with a dark-mode block and a print stylesheet.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..analytics import missingness as missingness_mod
from ..analytics.descriptive import DescriptiveStats, describe
from ..core.models import DataQualityReport, Severity
from ..datasets.builder import Dataset
from ..datasets.validation import period_sort_key

# Sunrise Research palette (spec 0G).
_CSS = """
:root {
  --bg: #FFF9F4; --surface: #FFFFFF; --surface-alt: #FFF2E8;
  --primary: #F26B4F; --secondary: #2AAE9B; --accent: #F2B84B;
  --text: #25313C; --muted: #66727D; --border: #E9E3DC;
  --ok: #2E9D69; --warn: #D99024; --err: #D84C4C;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1A1F24; --surface: #232A31; --surface-alt: #2B333B;
    --text: #ECEFF2; --muted: #A4AEB8; --border: #39434D;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0 0 4rem;
  background: var(--bg); color: var(--text);
  font: 15px/1.6 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
.wrap { max-width: 1080px; margin: 0 auto; padding: 0 1.5rem; }
header.hero {
  background: linear-gradient(135deg, var(--surface-alt), var(--bg));
  border-bottom: 1px solid var(--border); padding: 2.5rem 0 2rem; margin-bottom: 2rem;
}
h1 { margin: 0 0 .25rem; font-size: 1.9rem; letter-spacing: -.02em; }
h2 {
  font-size: 1.15rem; margin: 2.5rem 0 .75rem; padding-bottom: .4rem;
  border-bottom: 2px solid var(--primary); display: inline-block;
}
h3 { font-size: .95rem; margin: 1.5rem 0 .5rem; color: var(--muted);
     text-transform: uppercase; letter-spacing: .06em; }
.sub { color: var(--muted); margin: 0; }
.cards { display: flex; flex-wrap: wrap; gap: .75rem; margin: 1.25rem 0; }
.card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: .85rem 1.1rem; min-width: 132px; flex: 1 1 132px;
}
.card .k { font-size: .72rem; color: var(--muted); text-transform: uppercase;
           letter-spacing: .06em; }
.card .v { font-size: 1.45rem; font-weight: 650; margin-top: .15rem; }
.tablewrap { overflow-x: auto; border: 1px solid var(--border);
             border-radius: 10px; background: var(--surface); }
table { border-collapse: collapse; width: 100%; font-size: .87rem; }
th {
  background: var(--surface-alt); text-align: left; padding: .55rem .7rem;
  font-weight: 650; white-space: nowrap; border-bottom: 1px solid var(--border);
  position: sticky; top: 0;
}
td { padding: .45rem .7rem; border-bottom: 1px solid var(--border);
     vertical-align: top; }
tr:last-child td { border-bottom: 0; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
td.na { color: var(--muted); background: repeating-linear-gradient(
        45deg, transparent, transparent 4px, var(--surface-alt) 4px, var(--surface-alt) 8px); }
.badge { display: inline-block; padding: .12rem .5rem; border-radius: 999px;
         font-size: .74rem; font-weight: 650; }
.badge.ok   { background: #E6F4EC; color: var(--ok); }
.badge.warn { background: #FDF0D5; color: var(--warn); }
.badge.err  { background: #FBE3E3; color: var(--err); }
.badge.info { background: #E7F0FB; color: #4C8BF5; }
.issue { display: flex; gap: .6rem; align-items: flex-start; padding: .5rem 0;
         border-bottom: 1px solid var(--border); }
.issue:last-child { border-bottom: 0; }
.chart { background: var(--surface); border: 1px solid var(--border);
         border-radius: 10px; padding: 1rem; margin: .75rem 0; }
figcaption { color: var(--muted); font-size: .8rem; margin-top: .4rem; }
code { background: var(--surface-alt); padding: .1rem .35rem; border-radius: 4px;
       font-size: .85em; }
footer { margin-top: 3rem; padding-top: 1.25rem; border-top: 1px solid var(--border);
         color: var(--muted); font-size: .84rem; }
a { color: var(--primary); }
@media print {
  body { background: #fff; }
  header.hero { background: none; }
  .tablewrap, .card, .chart { break-inside: avoid; }
  th { position: static; }
}
"""

_SERIES_COLOURS = ["#F26B4F", "#2AAE9B", "#F2B84B", "#4C8BF5",
                   "#9B6BF2", "#2E9D69", "#D84C4C", "#66727D"]


@dataclass
class HtmlReportResult:
    path: Path
    bytes_written: int


class Raw(str):
    """Markup this module built itself and therefore trusts.

    Everything else -- provider titles, definitions, units, user queries -- is
    escaped. Deciding by inspecting the content (say, "does it start with
    ``<``?") would let a provider title containing markup inject into the
    report, so trust is carried by type instead.
    """

    __slots__ = ()


def _esc(value: object) -> str:
    if isinstance(value, Raw):
        return str(value)
    return html.escape("" if value is None else str(value))


def _fmt(value: object, digits: int = 2) -> str:
    if value is None:
        return "&mdash;"
    if isinstance(value, float):
        if value != value:  # NaN
            return "&mdash;"
        if abs(value) >= 1000:
            return f"{value:,.0f}"
        return f"{value:,.{digits}f}"
    return _esc(value)


def _table(headers: Sequence[str], rows: Sequence[Sequence[object]],
           numeric_from: int = 0) -> str:
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = []
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            if cell is None:
                cells.append('<td class="na">&mdash;</td>')
            elif isinstance(cell, (int, float)) and not isinstance(cell, bool) \
                    and i >= numeric_from:
                cells.append(f'<td class="num">{_fmt(cell)}</td>')
            else:
                cells.append(f"<td>{_esc(cell)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (f'<div class="tablewrap"><table><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def _line_chart(series: dict[str, list[tuple[str, float]]], *,
                title: str, unit: str | None,
                width: int = 940, height: int = 260) -> str:
    """Inline SVG multi-series line chart. No JavaScript, no network."""
    points = [v for pts in series.values() for _, v in pts]
    if not points:
        return ""

    pad_l, pad_r, pad_t, pad_b = 62, 130, 14, 34
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    labels = sorted({p for pts in series.values() for p, _ in pts},
                    key=period_sort_key)
    x_of = {p: (pad_l + (plot_w * i / max(len(labels) - 1, 1)))
            for i, p in enumerate(labels)}

    lo, hi = min(points), max(points)
    if lo == hi:
        lo, hi = lo - 1, hi + 1
    span = hi - lo
    lo -= span * 0.08
    hi += span * 0.08

    def y_of(v: float) -> float:
        return pad_t + plot_h - (v - lo) / (hi - lo) * plot_h

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" '
             f'role="img" aria-label="{_esc(title)}" '
             f'xmlns="http://www.w3.org/2000/svg">']

    # Horizontal gridlines with value labels.
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        y = y_of(v)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l + plot_w}" '
                     f'y2="{y:.1f}" stroke="var(--border)" stroke-width="1"/>')
        parts.append(f'<text x="{pad_l - 8}" y="{y + 4:.1f}" text-anchor="end" '
                     f'font-size="10" fill="var(--muted)">{_fmt(v, 1)}</text>')

    # X labels, thinned so they never collide.
    step = max(1, len(labels) // 12)
    for i, p in enumerate(labels):
        if i % step:
            continue
        parts.append(f'<text x="{x_of[p]:.1f}" y="{height - 12}" '
                     f'text-anchor="middle" font-size="10" '
                     f'fill="var(--muted)">{_esc(p)}</text>')

    for idx, (name, pts) in enumerate(series.items()):
        colour = _SERIES_COLOURS[idx % len(_SERIES_COLOURS)]
        ordered = sorted(pts, key=lambda t: period_sort_key(t[0]))
        # Break the path at gaps rather than drawing through them: a line
        # spanning a missing year would imply data that does not exist.
        runs: list[list[tuple[str, float]]] = []
        previous_index = None
        for period, value in ordered:
            i = labels.index(period)
            if previous_index is None or i != previous_index + 1:
                runs.append([])
            runs[-1].append((period, value))
            previous_index = i
        for run in runs:
            if len(run) == 1:
                p, v = run[0]
                parts.append(f'<circle cx="{x_of[p]:.1f}" cy="{y_of(v):.1f}" '
                             f'r="3" fill="{colour}"/>')
                continue
            d = " ".join(
                f"{'M' if i == 0 else 'L'}{x_of[p]:.1f},{y_of(v):.1f}"
                for i, (p, v) in enumerate(run)
            )
            parts.append(f'<path d="{d}" fill="none" stroke="{colour}" '
                         f'stroke-width="2" stroke-linejoin="round" '
                         f'stroke-linecap="round"/>')
        parts.append(f'<rect x="{pad_l + plot_w + 14}" '
                     f'y="{pad_t + 6 + idx * 18}" width="10" height="10" '
                     f'rx="2" fill="{colour}"/>')
        parts.append(f'<text x="{pad_l + plot_w + 30}" '
                     f'y="{pad_t + 15 + idx * 18}" font-size="11" '
                     f'fill="var(--text)">{_esc(name)}</text>')

    parts.append("</svg>")
    caption = _esc(title) + (f" &middot; {_esc(unit)}" if unit else "")
    return (f'<figure class="chart">{"".join(parts)}'
            f'<figcaption>{caption}</figcaption></figure>')


def _coverage_heatmap(report, *, width: int = 940) -> str:
    """Coverage by variable and period, as an inline SVG heatmap."""
    variables = report.variables
    periods = report.periods
    if not variables or not periods:
        return ""

    label_w, row_h, pad_t = 190, 22, 22
    cell_w = max(6.0, (width - label_w - 16) / len(periods))
    height = pad_t + row_h * len(variables) + 26

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" '
             f'role="img" aria-label="Coverage heatmap" '
             f'xmlns="http://www.w3.org/2000/svg">']

    step = max(1, len(periods) // 14)
    for i, period in enumerate(periods):
        if i % step:
            continue
        parts.append(f'<text x="{label_w + i * cell_w + cell_w / 2:.1f}" '
                     f'y="{pad_t - 8}" text-anchor="middle" font-size="9" '
                     f'fill="var(--muted)">{_esc(period)}</text>')

    for r, variable in enumerate(variables):
        y = pad_t + r * row_h
        parts.append(f'<text x="{label_w - 10}" y="{y + 15}" text-anchor="end" '
                     f'font-size="11" fill="var(--text)">{_esc(variable)}</text>')
        row = report.coverage_matrix.get(variable, {})
        for i, period in enumerate(periods):
            pct = row.get(period, 0.0)
            if pct >= 99.9:
                fill, op = "#2AAE9B", 0.85
            elif pct <= 0.01:
                fill, op = "#D84C4C", 0.28
            else:
                fill, op = "#F2B84B", 0.35 + 0.5 * (pct / 100.0)
            parts.append(f'<rect x="{label_w + i * cell_w:.1f}" y="{y + 2}" '
                         f'width="{max(cell_w - 1.2, 1):.1f}" height="{row_h - 5}" '
                         f'rx="2" fill="{fill}" fill-opacity="{op:.2f}">'
                         f'<title>{_esc(variable)} {_esc(period)}: {pct:g}%</title>'
                         f'</rect>')

    parts.append(f'<text x="{label_w}" y="{height - 6}" font-size="10" '
                 f'fill="var(--muted)">Teal = complete &middot; amber = partial '
                 f'&middot; red = no data</text>')
    parts.append("</svg>")
    return (f'<figure class="chart">{"".join(parts)}'
            f'<figcaption>Coverage by variable and period</figcaption></figure>')


_SEVERITY_CLASS = {Severity.ERROR: "err", Severity.WARNING: "warn",
                   Severity.INFO: "info"}


def export_report(
    dataset: Dataset,
    path: str | Path,
    *,
    quality: DataQualityReport | None = None,
    stats: Sequence[DescriptiveStats] | None = None,
    max_data_rows: int = 500,
) -> HtmlReportResult:
    """Render the standalone HTML report."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lineage = dataset.lineage or dataset.build_lineage()
    miss = missingness_mod.analyse(dataset)
    columns, wide_rows = dataset.to_wide()
    aliases = dataset.aliases
    spec = dataset.spec

    if stats is None:
        column_map = dataset.wide_columns_map()
        periods = [str(r["period"]) for r in wide_rows]
        units = {v.alias: v.metadata.unit for v in dataset.variables}
        stats = [describe(v, variable=a, periods=periods, unit=units.get(a))
                 for a, v in column_map.items()]

    status_badge = ""
    if quality:
        cls = {"clean": "ok", "usable_with_warnings": "warn",
               "unusable": "err"}[quality.status]
        status_badge = (f'<span class="badge {cls}">'
                        f'{_esc(quality.status.replace("_", " "))}</span>')

    out: list[str] = []
    a = out.append

    a("<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    a("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    a(f"<title>SmatEconData &middot; {_esc(dataset.name)}</title>")
    a(f"<style>{_CSS}</style></head><body>")

    # -- header / request summary ----------------------------------------
    a("<header class='hero'><div class='wrap'>")
    a(f"<h1>{_esc(dataset.name)}</h1>")
    a("<p class='sub'>SmatEconData &middot; economic data, easier to find</p>")
    if spec.original_query:
        a(f"<p class='sub' style='margin-top:.75rem'>Request: "
          f"<code>{_esc(spec.original_query)}</code></p>")
    a("</div></header><div class='wrap'>")

    # -- overview cards ---------------------------------------------------
    a("<div class='cards'>")
    for key, value in [
        ("Rows", len(wide_rows)),
        ("Variables", len(aliases)),
        ("Countries", len(dataset.geographies)),
        ("Period", spec.period_label()),
        ("Frequency", spec.frequency.value),
        ("Coverage", f"{miss.coverage_pct:g}%"),
    ]:
        a(f"<div class='card'><div class='k'>{_esc(key)}</div>"
          f"<div class='v'>{_esc(value)}</div></div>")
    a("</div>")
    if status_badge:
        a(f"<p>Data quality: {status_badge}</p>")

    # -- selected indicators ----------------------------------------------
    a("<h2>Selected indicators</h2>")
    a(_table(
        ["Variable", "Provider", "Series ID", "Official title", "Unit",
         "Frequency", "Coverage"],
        [[line.column, line.provider, line.series_id, line.series_title,
          line.unit, line.frequency.value,
          f"{line.coverage_start or '?'}–{line.coverage_end or '?'}"]
         for line in lineage],
    ))

    # -- charts ------------------------------------------------------------
    a("<h2>Charts</h2>")
    any_chart = False
    for variable in dataset.variables:
        series: dict[str, list[tuple[str, float]]] = {}
        for row in wide_rows:
            value = row.get(variable.alias)
            if value is None:
                continue
            series.setdefault(str(row["iso3"] or row["geography"]), []).append(
                (str(row["period"]), float(value)))
        if series:
            svg = _line_chart(series, title=variable.metadata.title,
                              unit=variable.metadata.unit)
            if svg:
                a(svg)
                any_chart = True
    if not any_chart:
        a("<p class='sub'>No plottable observations.</p>")

    # -- coverage / missingness -------------------------------------------
    a("<h2>Missing data</h2>")
    heatmap = _coverage_heatmap(miss)
    if heatmap:
        a(heatmap)
    a(_table(
        ["Variable", "Observations", "Present", "Missing", "Missing %",
         "First valid", "Last valid", "Longest gap", "Gap span", "Where"],
        [[v.variable, v.total, v.present, v.missing, v.missing_pct,
          v.first_valid, v.last_valid, v.longest_gap,
          " to ".join(v.longest_gap_span) if v.longest_gap_span else None,
          v.longest_gap_geography]
         for v in miss.by_variable],
        numeric_from=1,
    ))
    a("<h3>By country</h3>")
    a(_table(
        ["Country", "Cells", "Missing", "Missing %", "Coverage %"],
        [[g.geography, g.total, g.missing, g.missing_pct, g.coverage_pct]
         for g in miss.by_geography],
        numeric_from=1,
    ))
    a("<p class='sub'>Missing values are never imputed. A blank cell is a "
      "genuine gap in the source data.</p>")

    # -- quality warnings ---------------------------------------------------
    if quality and quality.issues:
        a("<h2>Data quality warnings</h2>")
        a("<div class='tablewrap' style='padding:.75rem 1rem'>")
        for issue in quality.issues:
            cls = _SEVERITY_CLASS.get(issue.severity, "info")
            where = " &middot; ".join(
                _esc(x) for x in (issue.variable, issue.geography) if x)
            a(f"<div class='issue'><span class='badge {cls}'>"
              f"{_esc(issue.severity.value)}</span><div>"
              f"<strong>{_esc(issue.code)}</strong>"
              f"{' &mdash; ' + where if where else ''}<br>"
              f"<span class='sub'>{_esc(issue.message)}</span></div></div>")
        a("</div>")

    # -- descriptive statistics --------------------------------------------
    a("<h2>Descriptive statistics</h2>")
    a(_table(
        ["Variable", "Unit", "N", "Missing", "Mean", "Median", "Std dev",
         "Min", "Q1", "Q3", "Max", "Skew", "Kurtosis"],
        [[s.variable, s.unit, s.count, s.missing, s.mean, s.median, s.std_dev,
          s.minimum, s.q1, s.q3, s.maximum, s.skewness, s.kurtosis]
         for s in stats],
        numeric_from=2,
    ))
    a("<p class='sub'>Descriptive only. This report contains no model "
      "estimation, inference, or forecasting.</p>")

    # -- data preview --------------------------------------------------------
    a("<h2>Data</h2>")
    preview = wide_rows[:max_data_rows]
    a(_table(columns, [[r.get(c) for c in columns] for r in preview],
             numeric_from=3))
    if len(wide_rows) > len(preview):
        a(f"<p class='sub'>Showing {len(preview)} of {len(wide_rows)} rows. "
          f"The full dataset is in the accompanying data files.</p>")

    # -- sources / provenance -------------------------------------------------
    a("<h2>Sources and provenance</h2>")
    a(_table(
        ["Variable", "Citation"],
        [[line.column,
          (Raw(f'<a href="{html.escape(line.source_reference, quote=True)}" '
               f'rel="noopener noreferrer">{html.escape(line.citation or "")}</a>')
           if line.source_reference
           and line.source_reference.startswith(("http://", "https://"))
           else line.citation)]
         for line in lineage],
    ))

    # -- transformation history ------------------------------------------------
    a("<h2>Transformation history</h2>")
    a(_table(
        ["#", "Operation", "Parameters", "Outputs", "When", "Note"],
        [[i, t.operation,
          "; ".join(f"{k}={v}" for k, v in t.parameters.items()),
          ", ".join(t.output_columns),
          t.timestamp.strftime("%Y-%m-%d %H:%M UTC"), t.note]
         for i, t in enumerate(dataset.transformations, start=1)],
    ))

    # -- reproducibility --------------------------------------------------------
    a("<h2>Reproducibility</h2>")
    a(_table(
        ["Field", "Value"],
        [["Geographies", ", ".join(spec.geographies)],
         ["Indicators", ", ".join(i.concept for i in spec.indicators)],
         ["Period", spec.period_label()],
         ["Frequency", spec.frequency.value],
         ["Output shape", spec.output_shape.value],
         ["Preferred sources", ", ".join(spec.preferred_sources) or "automatic"],
         ["Retrieved at", dataset.retrieved_at.strftime("%Y-%m-%d %H:%M UTC")]],
    ))

    a("<footer>Generated by <strong>SmatEconData</strong> &middot; "
      "Dr Merwan Roudane &middot; "
      "<a href='https://github.com/merwanroudane/smaterecondata'>"
      "github.com/merwanroudane/smaterecondata</a><br>"
      f"Retrieved {dataset.retrieved_at.strftime('%Y-%m-%d %H:%M UTC')}. "
      "Values come from the cited official sources; none were generated by "
      "this tool.</footer>")
    a("</div></body></html>")

    markup = "".join(out)
    path.write_text(markup, encoding="utf-8")
    return HtmlReportResult(path=path, bytes_written=len(markup.encode("utf-8")))
