/**
 * Dataset Workspace (spec 0E, 0F, 0L, 10A, 42).
 *
 * After retrieval the user lands here rather than in a wall of JSON. Eight
 * panels over one dataset: Data, Metadata, Descriptive statistics, Profiling,
 * Missing data, Charts, Sources & provenance, Export.
 *
 * Two product rules are visible in the UI itself:
 *   - a blank cell is a genuine gap, rendered as a marked "missing" cell and
 *     never as a zero;
 *   - every column can be traced to a provider series and a citation.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useI18n, type TranslationKey } from '../i18n'
import { DatasetChart } from './DatasetChart'
import './DatasetWorkspace.css'

const API_BASE = import.meta.env.VITE_API_URL || '/api'

type Tab =
  | 'data'
  | 'metadata'
  | 'stats'
  | 'profiling'
  | 'missing'
  | 'charts'
  | 'sources'
  | 'export'

const TABS: { id: Tab; labelKey: TranslationKey }[] = [
  { id: 'data', labelKey: 'workspace.data' },
  { id: 'metadata', labelKey: 'workspace.metadata' },
  { id: 'stats', labelKey: 'workspace.stats' },
  { id: 'profiling', labelKey: 'workspace.profiling' },
  { id: 'missing', labelKey: 'workspace.missing' },
  { id: 'charts', labelKey: 'workspace.charts' },
  { id: 'sources', labelKey: 'workspace.sources' },
  { id: 'export', labelKey: 'workspace.export' },
]

const EXPORTS: { format: string; labelKey: TranslationKey }[] = [
  { format: 'xlsx', labelKey: 'export.excel' },
  { format: 'csv', labelKey: 'export.csv' },
  { format: 'parquet', labelKey: 'export.parquet' },
  { format: 'json', labelKey: 'export.json' },
  { format: 'html', labelKey: 'export.html' },
  { format: 'bundle', labelKey: 'export.bundle' },
  { format: 'recipe_yaml', labelKey: 'export.recipe' },
]

interface DatasetSummary {
  dataset_id: string
  name: string
  rows: number
  variables: string[]
  geographies: string[]
  period: string
  frequency: string
  shape: string
  recipe_hash: string
  total_rows?: number
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  rows_data?: Record<string, any>[]
}

interface Props {
  datasetId: string
  onClose?: () => void
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Json = Record<string, any>

async function getJson(path: string): Promise<Json> {
  const response = await fetch(`${API_BASE}/v1${path}`)
  if (!response.ok) throw new Error(`${response.status}`)
  return response.json()
}

async function postJson(path: string): Promise<Json> {
  const response = await fetch(`${API_BASE}/v1${path}`, { method: 'POST' })
  if (!response.ok) throw new Error(`${response.status}`)
  return response.json()
}

export function DatasetWorkspace({ datasetId, onClose }: Props) {
  const { t, formatNumber } = useI18n()
  const [tab, setTab] = useState<Tab>('data')
  const [summary, setSummary] = useState<DatasetSummary | null>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [rows, setRows] = useState<Record<string, any>[]>([])
  const [quality, setQuality] = useState<Json | null>(null)
  const [stats, setStats] = useState<Json | null>(null)
  const [missing, setMissing] = useState<Json | null>(null)
  const [lineage, setLineage] = useState<Json | null>(null)
  const [profile, setProfile] = useState<Json | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busyTab, setBusyTab] = useState<Tab | null>(null)

  // Load the dataset plus its quality report up front — the header shows both.
  useEffect(() => {
    let cancelled = false
    setLoading(true)

    Promise.all([
      getJson(`/datasets/${datasetId}?limit=500`),
      postJson(`/datasets/${datasetId}/validate`),
    ])
      .then(([datasetBody, qualityBody]) => {
        if (cancelled) return
        setSummary(datasetBody as DatasetSummary)
        setRows(datasetBody.rows ?? [])
        setQuality(qualityBody)
        setError(null)
      })
      .catch(() => {
        if (!cancelled) setError(t('common.error'))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [datasetId, t])

  // Each panel fetches only when first opened, so the workspace stays fast.
  const ensure = useCallback(
    async (target: Tab) => {
      try {
        setBusyTab(target)
        if (target === 'stats' && !stats) {
          setStats(await postJson(`/datasets/${datasetId}/describe`))
        } else if (target === 'missing' && !missing) {
          setMissing(await getJson(`/datasets/${datasetId}/missingness`))
        } else if (target === 'sources' && !lineage) {
          setLineage(await getJson(`/datasets/${datasetId}/lineage`))
        } else if (target === 'charts' && !lineage) {
          setLineage(await getJson(`/datasets/${datasetId}/lineage`))
        } else if (target === 'profiling' && !profile) {
          setProfile(await postJson(`/datasets/${datasetId}/profile`))
        }
      } catch {
        setError(t('common.error'))
      } finally {
        setBusyTab(null)
      }
    },
    [datasetId, stats, missing, lineage, profile, t],
  )

  const select = (target: Tab) => {
    setTab(target)
    void ensure(target)
  }

  const EXTENSIONS: Record<string, string> = {
    bundle: 'zip',
    recipe_yaml: 'yaml',
  }

  const download = (format: string) => {
    // The export endpoint takes a JSON body, so a plain <form> POST cannot
    // reach it. Fetch the bytes and hand them to an object URL instead.
    void fetch(`${API_BASE}/v1/datasets/${datasetId}/export`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ format }),
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(String(response.status))
        const blob = await response.blob()
        const url = URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = url
        link.download = `${summary?.name ?? 'dataset'}.${
          EXTENSIONS[format] ?? format
        }`
        document.body.appendChild(link)
        link.click()
        document.body.removeChild(link)
        URL.revokeObjectURL(url)
      })
      .catch(() => setError(t('common.error')))
  }

  const columns = useMemo(
    () => (rows.length ? Object.keys(rows[0]) : []),
    [rows],
  )

  const statusClass = quality
    ? { clean: 'ok', usable_with_warnings: 'warn', unusable: 'err' }[
        String(quality.status)
      ] ?? 'info'
    : 'info'

  if (loading) {
    return <div className="ws ws--loading">{t('common.loading')}</div>
  }

  return (
    <section className="ws" aria-label={summary?.name}>
      <header className="ws__head">
        <div>
          <h2 className="ws__title">{summary?.name}</h2>
          <p className="ws__meta">
            {formatNumber(summary?.total_rows ?? summary?.rows ?? 0)}{' '}
            {t('quality.rows').toLowerCase()} ·{' '}
            {summary?.variables.length} {t('quality.variables').toLowerCase()} ·{' '}
            {summary?.geographies.length} · {summary?.period} · {summary?.frequency}
          </p>
        </div>
        <div className="ws__head-actions">
          {quality && (
            <span className={`ws__badge ws__badge--${statusClass}`}>
              {String(quality.status).replace(/_/g, ' ')}
              {typeof quality.coverage_pct === 'number' &&
                ` · ${t('quality.coverage')} ${quality.coverage_pct}%`}
            </span>
          )}
          <code className="ws__hash" title="recipe hash">
            {summary?.recipe_hash}
          </code>
          {onClose && (
            <button type="button" className="ws__close" onClick={onClose}>
              {t('common.close')}
            </button>
          )}
        </div>
      </header>

      {error && (
        <p className="ws__error" role="alert">
          {error}
        </p>
      )}

      <div className="ws__tabs" role="tablist">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            className="ws__tab"
            aria-selected={tab === item.id}
            onClick={() => select(item.id)}
          >
            {t(item.labelKey)}
          </button>
        ))}
      </div>

      <div className="ws__panel" role="tabpanel">
        {busyTab === tab && <p className="ws__note">{t('common.loading')}</p>}

        {tab === 'data' && (
          <div className="ws__tablewrap">
            <table className="ws__table">
              <thead>
                <tr>
                  {columns.map((column) => (
                    <th key={column}>{column}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => (
                  <tr key={index}>
                    {columns.map((column) => (
                      <td
                        key={column}
                        className={
                          row[column] === null || row[column] === undefined
                            ? 'ws__cell--missing'
                            : typeof row[column] === 'number'
                              ? 'ws__cell--num'
                              : undefined
                        }
                      >
                        {row[column] === null || row[column] === undefined
                          ? '—'
                          : typeof row[column] === 'number'
                            ? formatNumber(row[column])
                            : String(row[column])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {tab === 'metadata' && (
          <ul className="ws__cards">
            {summary?.variables.map((variable) => (
              <li key={variable} className="ws__card">
                <strong>{variable}</strong>
              </li>
            ))}
          </ul>
        )}

        {tab === 'stats' && stats && (
          <div className="ws__tablewrap">
            <table className="ws__table">
              <thead>
                <tr>
                  {Object.keys(
                    (stats.statistics as Json[])?.[0] ?? {},
                  ).map((key) => (
                    <th key={key}>{key}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(stats.statistics as Json[])?.map((row, index) => (
                  <tr key={index}>
                    {Object.values(row).map((value, i) => (
                      <td
                        key={i}
                        className={typeof value === 'number' ? 'ws__cell--num' : undefined}
                      >
                        {value === null || value === undefined
                          ? '—'
                          : typeof value === 'number'
                            ? formatNumber(value, { maximumFractionDigits: 4 })
                            : String(value)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="ws__note">{String(stats.note ?? '')}</p>
          </div>
        )}

        {tab === 'missing' && missing && (
          <>
            <p className="ws__note">
              {t('quality.coverage')}: {String(missing.coverage_pct)}% ·{' '}
              {formatNumber(Number(missing.missing_cells ?? 0))} missing cells
            </p>
            <div className="ws__tablewrap">
              <table className="ws__table">
                <thead>
                  <tr>
                    {Object.keys((missing.by_variable as Json[])?.[0] ?? {}).map((key) => (
                      <th key={key}>{key}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(missing.by_variable as Json[])?.map((row, index) => (
                    <tr key={index}>
                      {Object.values(row).map((value, i) => (
                        <td key={i}>{value === null ? '—' : String(value)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="ws__note">{String(missing.note ?? '')}</p>
          </>
        )}

        {tab === 'profiling' && profile && (
          <div className="ws__profile">
            <p className={`ws__badge ws__badge--${profile.status === 'ok' ? 'ok' : 'warn'}`}>
              {String(profile.status)}
            </p>
            {profile.message && <p className="ws__note">{String(profile.message)}</p>}
            {(profile.summary as Json)?.economic && (
              <dl className="ws__kv">
                {Object.entries((profile.summary as Json).economic as Json)
                  .filter(([, value]) => typeof value !== 'object')
                  .map(([key, value]) => (
                    <div key={key}>
                      <dt>{key.replace(/_/g, ' ')}</dt>
                      <dd>{String(value)}</dd>
                    </div>
                  ))}
              </dl>
            )}
          </div>
        )}

        {tab === 'sources' && lineage && (
          <ul className="ws__cards">
            {(lineage.columns as Json[])?.map((column) => (
              <li key={String(column.column)} className="ws__card">
                <strong>{String(column.column)}</strong>
                <span className="ws__card-meta">
                  {String(column.provider)} · {String(column.series_id)} ·{' '}
                  {String(column.unit ?? '—')}
                </span>
                <p className="ws__citation">{String(column.citation ?? '')}</p>
                {column.source_reference && (
                  <a
                    href={String(column.source_reference)}
                    rel="noopener noreferrer"
                    target="_blank"
                  >
                    {String(column.source_reference)}
                  </a>
                )}
              </li>
            ))}
          </ul>
        )}

        {tab === 'charts' && (
          <>
            {(summary?.variables ?? []).map((variable) => {
              const line = (lineage?.columns as Json[] | undefined)?.find(
                (column) => String(column.column) === variable,
              )
              return (
                <DatasetChart
                  key={variable}
                  rows={rows}
                  variable={variable}
                  unit={line ? (line.unit as string | null) : null}
                  source={line ? (line.provider as string | null) : null}
                />
              )
            })}
            <p className="ws__note">
              A line breaks at a gap rather than drawing through it — a
              continuous line across a missing period would imply data that
              does not exist. The same charts appear in the HTML report.
            </p>
          </>
        )}

        {tab === 'export' && (
          <div className="ws__exports">
            {EXPORTS.map((item) => (
              <button
                key={item.format}
                type="button"
                className="ws__export"
                onClick={() => download(item.format)}
              >
                <strong>{t(item.labelKey)}</strong>
                <small>.{EXTENSIONS[item.format] ?? item.format}</small>
              </button>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
