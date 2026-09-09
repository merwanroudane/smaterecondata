/**
 * The result of a zero-friction search: the DATA, first.
 *
 * The spreadsheet comes before everything else. Provenance, warnings and the
 * "what I understood" transparency panel sit around it, collapsed by default,
 * because a researcher who typed a clear request wants rows — not a summary of
 * their own sentence.
 *
 * Download Excel is the single primary action and exports the dataset that is
 * already built; it never re-runs the query.
 */

import { useState } from 'react'
import { useI18n } from '../i18n'
import './InstantDatasetResult.css'

const API_BASE = import.meta.env.VITE_API_URL || '/api'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Row = Record<string, any>

export interface Understanding {
  language: string
  countries: string[]
  iso3: string[]
  indicators: string[]
  concept_keys: string[]
  period: string
  frequency: string
  preferred_sources: string[]
  output: string[]
  needs_clarification: string[]
}

export interface ResolvedSeries {
  concept: string
  provider: string
  series_id: string
  official_title: string
  unit: string | null
  frequency: string
  confidence: number
  reason: string
  source_reference: string | null
  alternatives: { provider: string; series_id: string; title: string }[]
}

export interface InstantDataset {
  dataset_id: string | null
  name: string
  rows: number
  columns: string[]
  preview: Row[]
  truncated: boolean
  geographies: string[]
  period: string
  frequency: string
  shape: string
  quality: {
    status: string
    coverage_pct: number | null
    missing_cells: number
    errors: number
    warnings: number
  }
}

export interface InstantResponse {
  query: string
  understanding: Understanding
  resolution: ResolvedSeries[]
  dataset: InstantDataset | null
  warnings: string[]
  unresolved: string[]
  available_exports: string[]
}

const EXTENSIONS: Record<string, string> = { bundle: 'zip', recipe_yaml: 'yaml' }

interface Props {
  result: InstantResponse
  onAddToCart?: (result: InstantResponse) => void
  onChangeSeries?: (concept: string) => void
}

export function InstantDatasetResult({ result, onAddToCart, onChangeSeries }: Props) {
  const { t, formatNumber } = useI18n()
  const [showUnderstanding, setShowUnderstanding] = useState(false)
  const [busyFormat, setBusyFormat] = useState<string | null>(null)
  const [addedToCart, setAddedToCart] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)

  const dataset = result.dataset

  const download = async (format: string) => {
    if (!dataset?.dataset_id) return
    setBusyFormat(format)
    setExportError(null)
    try {
      // Export the dataset that already exists — never rebuild on export.
      const response = await fetch(
        `${API_BASE}/v1/datasets/${dataset.dataset_id}/export`,
        {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ format }),
        },
      )
      if (!response.ok) throw new Error(String(response.status))
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${dataset.name}.${EXTENSIONS[format] ?? format}`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      URL.revokeObjectURL(url)
    } catch {
      setExportError(t('common.error'))
    } finally {
      setBusyFormat(null)
    }
  }

  // No data: say why, offer the next step, never a dead end (spec 0M).
  if (!dataset) {
    return (
      <section className="instant instant--empty" role="status">
        <h2>{t('common.noResults')}</h2>
        {result.warnings.map((warning) => (
          <p key={warning} className="instant__warning">
            {warning}
          </p>
        ))}
        <p className="instant__hint">{t('common.noResultsHint')}</p>
      </section>
    )
  }

  const qualityClass =
    { clean: 'ok', usable_with_warnings: 'warn', unusable: 'err' }[
      dataset.quality.status
    ] ?? 'info'

  return (
    <section className="instant" aria-label={dataset.name}>
      {/* ---- what was used, stated plainly, above the data ---- */}
      <header className="instant__head">
        <div className="instant__using">
          {result.resolution.map((series) => (
            <div key={series.series_id} className="instant__series">
              <span className="instant__series-title">{series.official_title}</span>
              <span className="instant__series-meta">
                {series.provider} · <code>{series.series_id}</code>
                {series.unit ? ` · ${series.unit}` : ''}
              </span>
              {onChangeSeries && series.alternatives.length > 0 && (
                <button
                  type="button"
                  className="instant__change"
                  onClick={() => onChangeSeries(series.concept)}
                >
                  {t('instant.change')}
                </button>
              )}
            </div>
          ))}
        </div>

        <div className="instant__facts">
          <span>
            <strong>{formatNumber(dataset.rows)}</strong> {t('quality.rows').toLowerCase()}
          </span>
          <span>{dataset.geographies.join(', ')}</span>
          <span>{dataset.period}</span>
          <span>{dataset.frequency}</span>
          {dataset.quality.coverage_pct !== null && (
            <span className={`instant__badge instant__badge--${qualityClass}`}>
              {t('quality.coverage')} {dataset.quality.coverage_pct}%
            </span>
          )}
        </div>
      </header>

      {/* ---- primary action: one click to Excel ---- */}
      <div className="instant__actions">
        <button
          type="button"
          className="instant__primary"
          disabled={busyFormat !== null}
          onClick={() => void download('xlsx')}
        >
          {busyFormat === 'xlsx' ? t('common.loading') : t('instant.downloadExcel')}
        </button>

        {['csv', 'html', 'json', 'bundle'].map((format) => (
          <button
            key={format}
            type="button"
            className="instant__secondary"
            disabled={busyFormat !== null}
            onClick={() => void download(format)}
          >
            {format === 'bundle' ? 'ZIP' : format.toUpperCase()}
          </button>
        ))}

        {onAddToCart && (
          <button
            type="button"
            className="instant__secondary instant__secondary--cart"
            aria-pressed={addedToCart}
            onClick={() => {
              onAddToCart(result)
              setAddedToCart(true)
            }}
          >
            {addedToCart ? t('cart.added') : t('cart.add')}
          </button>
        )}
      </div>

      {exportError && (
        <p className="instant__warning" role="alert">
          {exportError}
        </p>
      )}

      {result.warnings.length > 0 && (
        <ul className="instant__warnings">
          {result.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}

      {/* ---- the data ---- */}
      <div className="instant__tablewrap">
        <table className="instant__table">
          <thead>
            <tr>
              {dataset.columns.map((column) => (
                <th key={column}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {dataset.preview.map((row, index) => (
              <tr key={index}>
                {dataset.columns.map((column) => {
                  const value = row[column]
                  const missing = value === null || value === undefined
                  return (
                    <td
                      key={column}
                      className={
                        missing
                          ? 'instant__cell--missing'
                          : typeof value === 'number'
                            ? 'instant__cell--num'
                            : undefined
                      }
                    >
                      {missing
                        ? '—'
                        : typeof value === 'number'
                          ? formatNumber(value, { maximumFractionDigits: 4 })
                          : String(value)}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {dataset.truncated && (
        <p className="instant__hint">
          {t('instant.truncated', { shown: formatNumber(dataset.preview.length) })}
        </p>
      )}

      {/* ---- transparency, collapsed: the data came first ---- */}
      <div className="instant__understanding">
        <button
          type="button"
          className="instant__disclose"
          aria-expanded={showUnderstanding}
          onClick={() => setShowUnderstanding((open) => !open)}
        >
          {t('understanding.title')}: {result.understanding.countries.join(', ')} ·{' '}
          {result.understanding.indicators.join(', ')} · {result.understanding.period}
          <span aria-hidden="true">{showUnderstanding ? ' ▾' : ' ▸'}</span>
        </button>

        {showUnderstanding && (
          <dl className="instant__details">
            <div>
              <dt>{t('understanding.countries')}</dt>
              <dd dir="auto">{result.understanding.countries.join(', ')}</dd>
            </div>
            <div>
              <dt>{t('understanding.indicators')}</dt>
              <dd dir="auto">{result.understanding.indicators.join(', ')}</dd>
            </div>
            <div>
              <dt>{t('understanding.period')}</dt>
              <dd>{result.understanding.period}</dd>
            </div>
            <div>
              <dt>{t('understanding.frequency')}</dt>
              <dd>{result.understanding.frequency}</dd>
            </div>
            <div>
              <dt>{t('understanding.sources')}</dt>
              <dd>
                {result.resolution
                  .map((series) => `${series.provider}:${series.series_id}`)
                  .join(', ')}
              </dd>
            </div>
            <div>
              <dt>{t('common.language')}</dt>
              <dd>{result.understanding.language}</dd>
            </div>
          </dl>
        )}
      </div>
    </section>
  )
}
