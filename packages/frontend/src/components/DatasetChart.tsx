/**
 * Inline dataset charts (spec 19, 0E).
 *
 * Plain SVG, no charting library: the same approach the HTML report takes, so
 * what you see in the workspace is what lands in the exported report.
 *
 * Two rules carried over from the report renderer:
 *   - the line BREAKS at a gap rather than drawing through it, because a line
 *     spanning a missing year implies data that does not exist;
 *   - every chart states its unit and source, so a chart can never be read
 *     without its provenance (spec 19).
 */

import { useMemo } from 'react'
import { useI18n } from '../i18n'
import './DatasetChart.css'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Row = Record<string, any>

interface Props {
  rows: Row[]
  variable: string
  unit?: string | null
  source?: string | null
  height?: number
}

const SERIES_COLOURS = [
  'var(--chart-1)',
  'var(--chart-2)',
  'var(--chart-3)',
  'var(--chart-4)',
  'var(--chart-5)',
  'var(--chart-6)',
]

/** Sortable key for the period formats the backend emits. */
function periodKey(period: string): number {
  const annual = /^(\d{4})$/.exec(period)
  if (annual) return Number(annual[1]) * 100
  const quarterly = /^(\d{4})-?Q([1-4])$/i.exec(period)
  if (quarterly) return Number(quarterly[1]) * 100 + Number(quarterly[2])
  const monthly = /^(\d{4})-(\d{2})$/.exec(period)
  if (monthly) return Number(monthly[1]) * 100 + Number(monthly[2])
  return 0
}

export function DatasetChart({ rows, variable, unit, source, height = 240 }: Props) {
  const { formatNumber } = useI18n()

  const model = useMemo(() => {
    const byGeography = new Map<string, { period: string; value: number }[]>()
    const periods = new Set<string>()

    for (const row of rows) {
      const value = row[variable]
      const period = String(row.period ?? '')
      if (!period) continue
      periods.add(period)
      if (value === null || value === undefined || typeof value !== 'number') continue
      const geography = String(row.iso3 ?? row.geography ?? '—')
      if (!byGeography.has(geography)) byGeography.set(geography, [])
      byGeography.get(geography)!.push({ period, value })
    }

    const orderedPeriods = [...periods].sort((a, b) => periodKey(a) - periodKey(b))
    const values = [...byGeography.values()].flat().map((p) => p.value)
    return { byGeography, orderedPeriods, values }
  }, [rows, variable])

  if (!model.values.length) {
    return (
      <figure className="dchart dchart--empty">
        <figcaption>{variable} — no plottable observations</figcaption>
      </figure>
    )
  }

  const width = 720
  const padLeft = 62
  const padRight = 116
  const padTop = 12
  const padBottom = 30
  const plotWidth = width - padLeft - padRight
  const plotHeight = height - padTop - padBottom

  const { orderedPeriods, byGeography } = model
  const xOf = (period: string) => {
    const index = orderedPeriods.indexOf(period)
    return padLeft + (plotWidth * index) / Math.max(orderedPeriods.length - 1, 1)
  }

  let low = Math.min(...model.values)
  let high = Math.max(...model.values)
  if (low === high) {
    low -= 1
    high += 1
  }
  const span = high - low
  low -= span * 0.08
  high += span * 0.08
  const yOf = (value: number) =>
    padTop + plotHeight - ((value - low) / (high - low)) * plotHeight

  const gridlines = [0, 0.25, 0.5, 0.75, 1].map((fraction) => {
    const value = low + (high - low) * fraction
    return { value, y: yOf(value) }
  })

  const labelStep = Math.max(1, Math.ceil(orderedPeriods.length / 10))
  const series = [...byGeography.entries()]

  return (
    <figure className="dchart">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        role="img"
        aria-label={`${variable} by country over time`}
      >
        {gridlines.map((line) => (
          <g key={line.y}>
            <line
              x1={padLeft}
              y1={line.y}
              x2={padLeft + plotWidth}
              y2={line.y}
              stroke="var(--border)"
              strokeWidth="1"
            />
            <text
              x={padLeft - 8}
              y={line.y + 4}
              textAnchor="end"
              fontSize="10"
              fill="var(--text-muted)"
            >
              {formatNumber(line.value, { maximumFractionDigits: 1 })}
            </text>
          </g>
        ))}

        {orderedPeriods.map((period, index) =>
          index % labelStep === 0 ? (
            <text
              key={period}
              x={xOf(period)}
              y={height - 10}
              textAnchor="middle"
              fontSize="10"
              fill="var(--text-muted)"
            >
              {period}
            </text>
          ) : null,
        )}

        {series.map(([geography, points], index) => {
          const colour = SERIES_COLOURS[index % SERIES_COLOURS.length]
          const ordered = [...points].sort(
            (a, b) => periodKey(a.period) - periodKey(b.period),
          )

          // Break the path at gaps rather than drawing through them.
          const runs: { period: string; value: number }[][] = []
          let previousIndex: number | null = null
          for (const point of ordered) {
            const position = orderedPeriods.indexOf(point.period)
            if (previousIndex === null || position !== previousIndex + 1) runs.push([])
            runs[runs.length - 1].push(point)
            previousIndex = position
          }

          return (
            <g key={geography}>
              {runs.map((run, runIndex) =>
                run.length === 1 ? (
                  <circle
                    key={runIndex}
                    cx={xOf(run[0].period)}
                    cy={yOf(run[0].value)}
                    r="3"
                    fill={colour}
                  />
                ) : (
                  <path
                    key={runIndex}
                    d={run
                      .map(
                        (point, i) =>
                          `${i === 0 ? 'M' : 'L'}${xOf(point.period).toFixed(1)},${yOf(
                            point.value,
                          ).toFixed(1)}`,
                      )
                      .join(' ')}
                    fill="none"
                    stroke={colour}
                    strokeWidth="2"
                    strokeLinejoin="round"
                    strokeLinecap="round"
                  />
                ),
              )}
              <rect
                x={padLeft + plotWidth + 14}
                y={padTop + 6 + index * 18}
                width="10"
                height="10"
                rx="2"
                fill={colour}
              />
              <text
                x={padLeft + plotWidth + 30}
                y={padTop + 15 + index * 18}
                fontSize="11"
                fill="var(--text-primary)"
              >
                {geography}
              </text>
            </g>
          )
        })}
      </svg>

      <figcaption>
        <strong>{variable}</strong>
        {unit ? ` · ${unit}` : ''}
        {source ? ` · ${source}` : ''}
      </figcaption>
    </figure>
  )
}
