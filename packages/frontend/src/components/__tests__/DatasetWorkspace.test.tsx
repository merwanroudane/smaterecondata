/**
 * Dataset Workspace tests (spec 0E, 0F, 0L, 42).
 *
 * The behaviours pinned here: all eight panels exist, a gap renders as a
 * marked missing cell rather than a zero, provenance reaches every column,
 * and each panel loads only when opened.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { I18nProvider } from '../../i18n'
import { DatasetWorkspace } from '../DatasetWorkspace'

const SUMMARY = {
  dataset_id: 'abc123',
  name: 'maghreb_macro',
  total_rows: 3,
  variables: ['gdp_per_capita_usd', 'inflation_pct'],
  geographies: ['DZA', 'MAR'],
  period: '2000-2002',
  frequency: 'annual',
  shape: 'wide',
  recipe_hash: '1e0715f972d5f366',
  rows: [
    { geography: 'Algeria', iso3: 'DZA', period: '2000', gdp_per_capita_usd: 1800, inflation_pct: 2.5 },
    { geography: 'Algeria', iso3: 'DZA', period: '2001', gdp_per_capita_usd: 1920, inflation_pct: null },
    { geography: 'Morocco', iso3: 'MAR', period: '2000', gdp_per_capita_usd: 1500, inflation_pct: 1.9 },
  ],
}

const QUALITY = {
  status: 'usable_with_warnings',
  coverage_pct: 83.33,
  rows: 3,
  issues: [],
  warnings: 1,
  errors: 0,
}

const STATS = {
  statistics: [
    { variable: 'gdp_per_capita_usd', count: 3, missing: 0, mean: 1740 },
    { variable: 'inflation_pct', count: 2, missing: 1, mean: 2.2 },
  ],
  note: 'Descriptive statistics only. Missing values are excluded, never imputed.',
}

const MISSING = {
  coverage_pct: 83.33,
  missing_cells: 1,
  by_variable: [
    { name: 'inflation_pct', missing: 1, missing_pct: 33.33, longest_gap: 1 },
  ],
  note: 'Missing values are reported, never imputed.',
}

const LINEAGE = {
  columns: [
    {
      column: 'gdp_per_capita_usd',
      provider: 'world_bank',
      series_id: 'NY.GDP.PCAP.CD',
      unit: 'current US$',
      citation: 'World Bank. "GDP per capita". Retrieved 2026-09-09 via SmatEconData.',
      source_reference: 'https://data.worldbank.org/indicator/NY.GDP.PCAP.CD',
    },
  ],
}

const PROFILE = {
  status: 'unavailable',
  message: 'fg-data-profiling is not installed.',
  summary: { economic: { rows: 3, geography_count: 2, coverage_pct: 83.33 } },
}

function mockApi() {
  const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const target = String(url)
    const ok = (body: unknown) =>
      Promise.resolve({ ok: true, status: 200, json: async () => body })

    if (target.includes('/validate')) return ok(QUALITY)
    if (target.includes('/describe')) return ok(STATS)
    if (target.includes('/missingness')) return ok(MISSING)
    if (target.includes('/lineage')) return ok(LINEAGE)
    if (target.includes('/profile')) return ok(PROFILE)
    if (target.includes('/export')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        blob: async () => new Blob(['x']),
      })
    }
    if (init?.method === 'POST') return ok({})
    return ok(SUMMARY)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const renderWorkspace = () =>
  render(
    <I18nProvider>
      <DatasetWorkspace datasetId="abc123" />
    </I18nProvider>,
  )

describe('DatasetWorkspace', () => {
  beforeEach(() => {
    localStorage.clear()
    mockApi()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('shows the dataset header with the recipe hash', async () => {
    renderWorkspace()
    await waitFor(() => expect(screen.getByText('maghreb_macro')).toBeInTheDocument())
    expect(screen.getByText('1e0715f972d5f366')).toBeInTheDocument()
  })

  it('shows the data-quality status and coverage', async () => {
    renderWorkspace()
    await waitFor(() => screen.getByText(/usable with warnings/i))
    expect(screen.getByText(/83\.33%/)).toBeInTheDocument()
  })

  it('offers all eight workspace panels', async () => {
    renderWorkspace()
    await waitFor(() => screen.getByText('maghreb_macro'))

    const tabs = screen.getAllByRole('tab')
    expect(tabs).toHaveLength(8)
    for (const label of [
      'Data',
      'Metadata',
      'Descriptive statistics',
      'Data profiling',
      'Missing data',
      'Charts',
      'Sources & provenance',
      'Export',
    ]) {
      expect(screen.getByRole('tab', { name: label })).toBeInTheDocument()
    }
  })

  it('opens on the Data panel', async () => {
    renderWorkspace()
    await waitFor(() => screen.getByText('maghreb_macro'))
    expect(screen.getByRole('tab', { name: 'Data' })).toHaveAttribute(
      'aria-selected',
      'true',
    )
  })

  it('renders a gap as a marked missing cell, never a zero', async () => {
    renderWorkspace()
    await waitFor(() => screen.getByText('maghreb_macro'))

    const missingCells = document.querySelectorAll('.ws__cell--missing')
    expect(missingCells).toHaveLength(1)
    expect(missingCells[0].textContent).toBe('—')
    expect(missingCells[0].textContent).not.toBe('0')
  })

  it('right-aligns numeric cells', async () => {
    renderWorkspace()
    await waitFor(() => screen.getByText('maghreb_macro'))
    expect(document.querySelectorAll('.ws__cell--num').length).toBeGreaterThan(0)
  })

  it('loads statistics only when that panel is opened', async () => {
    const fetchMock = mockApi()
    const user = userEvent.setup()
    renderWorkspace()
    await waitFor(() => screen.getByText('maghreb_macro'))

    const describeCalls = () =>
      fetchMock.mock.calls.filter((call) => String(call[0]).includes('/describe'))
    expect(describeCalls()).toHaveLength(0)

    await user.click(screen.getByRole('tab', { name: 'Descriptive statistics' }))
    await waitFor(() => expect(describeCalls().length).toBe(1))
  })

  it('states the descriptive-only boundary', async () => {
    const user = userEvent.setup()
    renderWorkspace()
    await waitFor(() => screen.getByText('maghreb_macro'))

    await user.click(screen.getByRole('tab', { name: 'Descriptive statistics' }))
    await waitFor(() => screen.getByText(/never imputed/i))
  })

  it('shows missing-data analysis with the never-imputed note', async () => {
    const user = userEvent.setup()
    renderWorkspace()
    await waitFor(() => screen.getByText('maghreb_macro'))

    await user.click(screen.getByRole('tab', { name: 'Missing data' }))
    await waitFor(() => screen.getByText(/reported, never imputed/i))
  })

  it('shows a citation and source link for every column', async () => {
    const user = userEvent.setup()
    renderWorkspace()
    await waitFor(() => screen.getByText('maghreb_macro'))

    await user.click(screen.getByRole('tab', { name: 'Sources & provenance' }))
    await waitFor(() => screen.getByText(/via SmatEconData/))

    const link = screen.getByRole('link', {
      name: /data\.worldbank\.org/,
    })
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('degrades gracefully when profiling is unavailable', async () => {
    const user = userEvent.setup()
    renderWorkspace()
    await waitFor(() => screen.getByText('maghreb_macro'))

    await user.click(screen.getByRole('tab', { name: 'Data profiling' }))
    // The economic summary is still shown even though the backend is missing.
    await waitFor(() => screen.getByText(/not installed/i))
    expect(screen.getByText('unavailable')).toBeInTheDocument()
  })

  it('renders inline charts with unit and source', async () => {
    const user = userEvent.setup()
    renderWorkspace()
    await waitFor(() => screen.getByText('maghreb_macro'))

    await user.click(screen.getByRole('tab', { name: 'Charts' }))
    await waitFor(() =>
      expect(document.querySelectorAll('.dchart').length).toBeGreaterThan(0),
    )
    // Provenance travels with the chart (spec 19).
    await waitFor(() => screen.getByText(/current US\$/))
  })

  it('explains that a chart line breaks at a gap', async () => {
    const user = userEvent.setup()
    renderWorkspace()
    await waitFor(() => screen.getByText('maghreb_macro'))

    await user.click(screen.getByRole('tab', { name: 'Charts' }))
    await waitFor(() => screen.getByText(/breaks at a gap/i))
  })

  it('offers every export format', async () => {
    const user = userEvent.setup()
    renderWorkspace()
    await waitFor(() => screen.getByText('maghreb_macro'))

    await user.click(screen.getByRole('tab', { name: 'Export' }))
    for (const label of [
      'Excel workbook',
      'CSV',
      'Parquet',
      'JSON',
      'HTML report',
      'Research bundle (ZIP)',
      'Dataset recipe',
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
  })

  it('requests the chosen export format', async () => {
    const fetchMock = mockApi()
    const user = userEvent.setup()
    renderWorkspace()
    await waitFor(() => screen.getByText('maghreb_macro'))

    await user.click(screen.getByRole('tab', { name: 'Export' }))
    await user.click(screen.getByText('Excel workbook'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/export'))
      expect(call).toBeTruthy()
      expect(JSON.parse(call![1].body)).toEqual({ format: 'xlsx' })
    })
  })

  it('shows a plain error instead of crashing when the API fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network down')))
    renderWorkspace()
    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText(/went wrong/i)).toBeInTheDocument()
  })
})
