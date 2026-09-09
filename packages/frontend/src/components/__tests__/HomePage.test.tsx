/**
 * Home page tests — the zero-friction contract.
 *
 * The rule under test: **Search means get data.** Pressing Search must show
 * actual rows and a working Excel button, without a cart, a provider picker,
 * an indicator code, or the country being entered twice.
 *
 * "What I understood" still exists for transparency but is no longer the
 * destination, so it is asserted as a collapsed disclosure, not a result.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CartProvider } from '../../contexts/CartContext'
import { ThemeProvider } from '../../contexts/ThemeContext'
import { I18nProvider } from '../../i18n'
import { HomePage } from '../HomePage'

const ARABIC_INSTANT = {
  query: 'التضخم في الجزائر من 2000 إلى 2025',
  understanding: {
    language: 'ar',
    countries: ['الجزائر'],
    iso3: ['DZA'],
    indicators: ['التضخم'],
    concept_keys: ['inflation'],
    period: '2000-2025',
    frequency: 'annual',
    preferred_sources: ['automatic'],
    output: ['xlsx'],
    needs_clarification: [],
  },
  resolution: [
    {
      concept: 'inflation',
      provider: 'world_bank',
      series_id: 'FP.CPI.TOTL.ZG',
      official_title: 'Inflation, consumer prices (annual %)',
      unit: 'Annual %',
      frequency: 'annual',
      confidence: 0.97,
      reason: 'recommended series for this concept',
      source_reference: 'https://data.worldbank.org/indicator/FP.CPI.TOTL.ZG',
      alternatives: [],
    },
  ],
  dataset: {
    dataset_id: 'abc123',
    name: 'inflation_dza_2000-2025',
    rows: 3,
    columns: ['geography', 'iso3', 'period', 'inflation_pct'],
    preview: [
      { geography: 'Algeria', iso3: 'DZA', period: '2000', inflation_pct: 0.34 },
      { geography: 'Algeria', iso3: 'DZA', period: '2001', inflation_pct: 4.23 },
      { geography: 'Algeria', iso3: 'DZA', period: '2002', inflation_pct: null },
    ],
    truncated: false,
    geographies: ['DZA'],
    period: '2000-2025',
    frequency: 'annual',
    shape: 'wide',
    quality: {
      status: 'usable_with_warnings',
      coverage_pct: 66.7,
      missing_cells: 1,
      errors: 0,
      warnings: 1,
    },
  },
  warnings: ['2025 is not yet available from this source; data runs to 2024.'],
  unresolved: [],
  available_exports: ['xlsx', 'csv', 'json', 'html', 'bundle'],
}

function renderHome() {
  return render(
    <I18nProvider>
      <ThemeProvider>
        <CartProvider>
          <HomePage />
        </CartProvider>
      </ThemeProvider>
    </I18nProvider>,
  )
}

/** Route by URL: the page also loads catalogue and stats on mount. */
function mockApi(body: unknown = ARABIC_INSTANT, ok = true, status = 200) {
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    const target = String(url)
    const json = (payload: unknown) =>
      Promise.resolve({ ok: true, status: 200, json: async () => payload })

    if (target.includes('/v1/concepts')) return json({ concepts: [] })
    if (target.includes('/v1/geographies')) return json({ countries: [], regions: [] })
    if (target.includes('/v1/catalog/stats')) {
      return json({
        searchable_series_count: 43907,
        provider_count: 10,
        country_or_area_count: 57,
        display_count: '43,907 indexed series',
        milestone: 'below_floor',
        last_catalog_sync: '2025-11-26',
      })
    }
    if (target.includes('/export')) {
      return Promise.resolve({ ok: true, status: 200, blob: async () => new Blob(['x']) })
    }
    return Promise.resolve({ ok, status, json: async () => body })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const instantCalls = (mock: ReturnType<typeof vi.fn>) =>
  mock.mock.calls.filter((call) => String(call[0]).includes('/v1/instant-dataset'))

async function search(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(screen.getByRole('searchbox'), text)
  await user.click(screen.getByRole('button', { name: /^search$/i }))
}

describe('HomePage — zero-friction search', () => {
  beforeEach(() => {
    localStorage.clear()
    // jsdom has no object-URL plumbing for the download path.
    if (!URL.createObjectURL) {
      Object.defineProperty(URL, 'createObjectURL', { value: vi.fn(), writable: true })
      Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), writable: true })
    }
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  // -- Test A ------------------------------------------------------------

  it('sends Search to instant-dataset, not to the parser', async () => {
    const fetchMock = mockApi()
    const user = userEvent.setup()
    renderHome()

    await search(user, 'Inflation in Algeria from 2000 to 2025')

    await waitFor(() => expect(instantCalls(fetchMock)).toHaveLength(1))
    // The parse-only endpoint must no longer be the search path.
    expect(
      fetchMock.mock.calls.filter((c) => String(c[0]).endsWith('/v1/parse')),
    ).toHaveLength(0)
  })

  it('shows actual data rows after one search', async () => {
    mockApi()
    const user = userEvent.setup()
    renderHome()

    await search(user, 'Inflation in Algeria from 2000 to 2025')

    await waitFor(() => expect(screen.getByRole('table')).toBeInTheDocument())
    const table = screen.getByRole('table')
    expect(within(table).getByText('inflation_pct')).toBeInTheDocument()
    expect(within(table).getAllByText('Algeria').length).toBeGreaterThan(0)
    expect(within(table).getByText('2000')).toBeInTheDocument()
  })

  it('names the official series and source without being asked', async () => {
    mockApi()
    const user = userEvent.setup()
    renderHome()

    await search(user, 'Inflation in Algeria from 2000 to 2025')

    await waitFor(() =>
      expect(screen.getByText('Inflation, consumer prices (annual %)')).toBeInTheDocument(),
    )
    expect(screen.getByText('FP.CPI.TOTL.ZG')).toBeInTheDocument()
  })

  it('offers Download Excel as the primary action', async () => {
    mockApi()
    const user = userEvent.setup()
    renderHome()

    await search(user, 'Inflation in Algeria from 2000 to 2025')
    const excel = await screen.findByRole('button', { name: /download excel/i })
    expect(excel).toBeEnabled()
  })

  it('exports the dataset already built rather than re-running the query', async () => {
    const fetchMock = mockApi()
    const user = userEvent.setup()
    renderHome()

    await search(user, 'Inflation in Algeria from 2000 to 2025')
    await user.click(await screen.findByRole('button', { name: /download excel/i }))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/export'))
      expect(call).toBeTruthy()
      expect(String(call![0])).toContain('/datasets/abc123/export')
      expect(JSON.parse(call![1].body)).toEqual({ format: 'xlsx' })
    })
    // Still exactly one retrieval.
    expect(instantCalls(fetchMock)).toHaveLength(1)
  })

  it('never requires a cart, provider or indicator code to see data', async () => {
    mockApi()
    const user = userEvent.setup()
    renderHome()

    await search(user, 'Inflation in Algeria from 2000 to 2025')
    await waitFor(() => screen.getByRole('table'))

    expect(screen.queryByLabelText(/provider/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/indicator code/i)).not.toBeInTheDocument()
    // The cart is reachable, but nothing forced the user through it.
    expect(screen.getByRole('button', { name: /data cart/i })).toBeInTheDocument()
  })

  // -- Tests B and C -------------------------------------------------------

  it.each([
    ['Arabic', 'التضخم في الجزائر من 2000 إلى 2025'],
    ['French', 'Inflation en Algérie de 2000 à 2025'],
    ['English', 'Inflation in Algeria from 2000 to 2025'],
  ])('%s queries follow the same zero-friction path', async (_label, query) => {
    const fetchMock = mockApi()
    const user = userEvent.setup()
    renderHome()

    await search(user, query)

    await waitFor(() => expect(screen.getByRole('table')).toBeInTheDocument())
    expect(JSON.parse(instantCalls(fetchMock)[0][1].body).query).toBe(query)
  })

  // -- "What I understood" is now secondary --------------------------------

  it('keeps the understanding panel but collapsed, below the data', async () => {
    mockApi()
    const user = userEvent.setup()
    renderHome()

    await search(user, 'Inflation in Algeria from 2000 to 2025')
    await waitFor(() => screen.getByRole('table'))

    const disclosure = screen.getByRole('button', { name: /what i understood/i })
    expect(disclosure).toHaveAttribute('aria-expanded', 'false')

    await user.click(disclosure)
    expect(disclosure).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Countries')).toBeInTheDocument()
  })

  // -- Warnings and honesty -------------------------------------------------

  it('surfaces provider warnings without hiding the data', async () => {
    mockApi()
    const user = userEvent.setup()
    renderHome()

    await search(user, 'Inflation in Algeria from 2000 to 2025')
    await waitFor(() => screen.getByRole('table'))
    expect(screen.getByText(/2025 is not yet available/i)).toBeInTheDocument()
  })

  it('renders a gap as a marked cell, never a zero', async () => {
    mockApi()
    const user = userEvent.setup()
    renderHome()

    await search(user, 'Inflation in Algeria from 2000 to 2025')
    await waitFor(() => screen.getByRole('table'))

    const missing = document.querySelectorAll('.instant__cell--missing')
    expect(missing).toHaveLength(1)
    expect(missing[0].textContent).toBe('—')
  })

  it('explains an empty result instead of dead-ending', async () => {
    mockApi({ ...ARABIC_INSTANT, dataset: null, warnings: ['No country was detected.'] })
    const user = userEvent.setup()
    renderHome()

    await search(user, 'inflation')
    await waitFor(() => expect(screen.getByText('No country was detected.')).toBeInTheDocument())
  })

  it('shows plain language on failure, never a stack trace', async () => {
    mockApi({}, false, 503)
    const user = userEvent.setup()
    renderHome()

    await search(user, 'inflation in Algeria')
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('503')
    expect(alert.textContent).not.toMatch(/at .*\(.*:\d+:\d+\)/)
  })

  // -- Test E: cart stays optional -----------------------------------------

  it('offers Add to Data Cart as a secondary action on the result', async () => {
    mockApi()
    const user = userEvent.setup()
    renderHome()

    await search(user, 'Inflation in Algeria from 2000 to 2025')
    await waitFor(() => screen.getByRole('table'))

    const add = screen.getByRole('button', { name: /add to cart/i })
    await user.click(add)

    // The series AND the parsed countries/period are copied — nothing retyped.
    const stored = JSON.parse(localStorage.getItem('smatecondata_cart')!)
    expect(stored.items.map((i: { concept: string }) => i.concept)).toEqual(['inflation'])
    expect(stored.settings.geographies).toEqual(['DZA'])
    expect(stored.settings.startYear).toBe(2000)
    expect(stored.settings.endYear).toBe(2025)
  })

  // -- Page furniture -------------------------------------------------------

  it('leads with the search box', () => {
    mockApi()
    renderHome()
    expect(screen.getByRole('searchbox')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^search$/i })).toBeInTheDocument()
  })

  it('disables submit while the field is empty', () => {
    mockApi()
    renderHome()
    expect(screen.getByRole('button', { name: /^search$/i })).toBeDisabled()
  })

  it('runs an example chip as a full retrieval', async () => {
    const fetchMock = mockApi()
    const user = userEvent.setup()
    renderHome()

    await user.click(
      screen.getByRole('button', { name: 'Inflation in Algeria from 2000 to 2025' }),
    )
    await waitFor(() => expect(instantCalls(fetchMock).length).toBeGreaterThan(0))
    expect(JSON.parse(instantCalls(fetchMock)[0][1].body).query).toBe(
      'Inflation in Algeria from 2000 to 2025',
    )
  })

  it('shows the product promise and attribution', () => {
    mockApi()
    renderHome()
    expect(
      screen.getByRole('heading', { level: 1, name: /economic data, easier to find/i }),
    ).toBeInTheDocument()
    expect(screen.getByText(/Dr Merwan Roudane/)).toBeInTheDocument()
  })
})
