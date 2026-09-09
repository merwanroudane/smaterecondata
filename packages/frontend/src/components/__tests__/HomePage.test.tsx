/**
 * Home page tests (spec 0A.1, 0J, 0M, 0R).
 *
 * Pins the Direct Search Contract: one query, no provider picker, no indicator
 * code, ambiguity surfaced rather than guessed, and errors shown as plain
 * language instead of a stack trace.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CartProvider } from '../../contexts/CartContext'
import { ThemeProvider } from '../../contexts/ThemeContext'
import { I18nProvider } from '../../i18n'
import { HomePage } from '../HomePage'

const ARABIC_RESPONSE = {
  understanding: {
    language: 'ar',
    countries: ['الجزائر'],
    iso3: ['DZA'],
    indicators: ['التضخم', 'معدل البطالة'],
    concept_keys: ['inflation', 'unemployment'],
    period: '2000-2025',
    frequency: 'annual',
    preferred_sources: ['automatic'],
    output: ['xlsx'],
    needs_clarification: ['inflation', 'unemployment'],
  },
  ambiguous: [
    {
      concept: 'inflation',
      label: 'Inflation',
      distinctions: ['CPI inflation, annual percent', 'GDP deflator inflation'],
    },
  ],
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

/**
 * Route the stub by URL: the home page also loads the concept and geography
 * catalogues for the mode tabs, and those must not receive a parse payload.
 */
function mockParse(body: unknown, ok = true, status = 200) {
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    const target = String(url)
    if (target.includes('/v1/concepts')) {
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ concepts: [] }) })
    }
    if (target.includes('/v1/geographies')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ countries: [], regions: [] }),
      })
    }
    return Promise.resolve({ ok, status, json: async () => body })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** Only the /v1/parse calls, ignoring catalogue traffic. */
function parseCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter((call) => String(call[0]).includes('/v1/parse'))
}

describe('HomePage', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.useRealTimers()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('leads with the search box, not a configuration form', () => {
    mockParse(ARABIC_RESPONSE)
    renderHome()

    expect(screen.getByRole('searchbox')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /search/i })).toBeInTheDocument()
    // Spec 0A.1: nothing must be chosen before the first result.
    expect(screen.queryByLabelText(/provider/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/indicator code/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/export format/i)).not.toBeInTheDocument()
  })

  it('shows the product promise and attribution', () => {
    mockParse(ARABIC_RESPONSE)
    renderHome()
    expect(
      screen.getByRole('heading', { level: 1, name: /economic data, easier to find/i }),
    ).toBeInTheDocument()
    expect(screen.getByText(/Dr Merwan Roudane/)).toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: /github\.com\/merwanroudane\/smaterecondata/ }),
    ).toBeInTheDocument()
  })

  it('resolves a query in a single action', async () => {
    const fetchMock = mockParse(ARABIC_RESPONSE)
    const user = userEvent.setup()
    renderHome()

    await user.type(
      screen.getByRole('searchbox'),
      'التضخم والبطالة في الجزائر من 2000 إلى 2025',
    )
    await user.click(screen.getByRole('button', { name: /search/i }))

    await waitFor(() =>
      expect(screen.getByText(/what i understood/i)).toBeInTheDocument(),
    )

    const calls = parseCalls(fetchMock)
    expect(calls).toHaveLength(1)
    expect(calls[0][1].method).toBe('POST')
  })

  it('shows every interpreted field before retrieval', async () => {
    mockParse(ARABIC_RESPONSE)
    const user = userEvent.setup()
    renderHome()

    await user.type(screen.getByRole('searchbox'), 'inflation Algeria')
    await user.click(screen.getByRole('button', { name: /search/i }))

    await waitFor(() => screen.getByText(/what i understood/i))
    for (const label of ['Countries', 'Indicators', 'Period', 'Frequency', 'Sources', 'Output']) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
    expect(screen.getByText('2000-2025')).toBeInTheDocument()
    expect(screen.getByText('الجزائر')).toBeInTheDocument()
  })

  it('reports the detected query language', async () => {
    mockParse(ARABIC_RESPONSE)
    const user = userEvent.setup()
    renderHome()

    await user.type(screen.getByRole('searchbox'), 'التضخم')
    await user.click(screen.getByRole('button', { name: /search/i }))

    await waitFor(() => screen.getByText(/arabic query/i))
  })

  it('surfaces ambiguity instead of silently choosing', async () => {
    mockParse(ARABIC_RESPONSE)
    const user = userEvent.setup()
    renderHome()

    await user.type(screen.getByRole('searchbox'), 'inflation')
    await user.click(screen.getByRole('button', { name: /search/i }))

    await waitFor(() => screen.getByText(/which definition do you mean/i))
    expect(screen.getByText(/will not pick one silently/i)).toBeInTheDocument()
    expect(screen.getByText('CPI inflation, annual percent')).toBeInTheDocument()
    expect(screen.getByText('GDP deflator inflation')).toBeInTheDocument()
  })

  it('runs an example chip as a full search', async () => {
    const fetchMock = mockParse(ARABIC_RESPONSE)
    const user = userEvent.setup()
    renderHome()

    await user.click(
      screen.getByRole('button', { name: 'Inflation in Algeria from 2000 to 2025' }),
    )

    await waitFor(() => expect(parseCalls(fetchMock).length).toBeGreaterThan(0))
    const body = JSON.parse(parseCalls(fetchMock)[0][1].body)
    expect(body.query).toBe('Inflation in Algeria from 2000 to 2025')
  })

  it('shows plain language on failure, never a stack trace', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) => {
        const target = String(url)
        if (target.includes('/v1/concepts') || target.includes('/v1/geographies')) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: async () => ({ concepts: [], countries: [], regions: [] }),
          })
        }
        return Promise.reject(new Error('Failed to fetch'))
      }),
    )
    const user = userEvent.setup()
    renderHome()

    await user.type(screen.getByRole('searchbox'), 'inflation')
    await user.click(screen.getByRole('button', { name: /search/i }))

    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText(/search unavailable/i)).toBeInTheDocument()
    expect(alert.textContent).not.toMatch(/at .*\(.*:\d+:\d+\)/)
  })

  it('reports a non-OK response without crashing', async () => {
    mockParse({}, false, 503)
    const user = userEvent.setup()
    renderHome()

    await user.type(screen.getByRole('searchbox'), 'inflation')
    await user.click(screen.getByRole('button', { name: /search/i }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('503')
  })

  it('disables submit while the field is empty', () => {
    mockParse(ARABIC_RESPONSE)
    renderHome()
    expect(screen.getByRole('button', { name: /search/i })).toBeDisabled()
  })

  it('does not fire a request for whitespace', async () => {
    const fetchMock = mockParse(ARABIC_RESPONSE)
    const user = userEvent.setup()
    renderHome()

    await user.type(screen.getByRole('searchbox'), '   ')
    expect(screen.getByRole('button', { name: /search/i })).toBeDisabled()
    expect(parseCalls(fetchMock)).toHaveLength(0)
  })

  it('keeps the search usable while the hero animation runs', () => {
    mockParse(ARABIC_RESPONSE)
    renderHome()
    const box = screen.getByRole('searchbox')
    expect(box).toBeEnabled()
    // The decorative hero is hidden from assistive tech and never overlays input.
    expect(document.querySelector('.hero-anim')).toHaveAttribute('aria-hidden', 'true')
  })

  it('offers topic browsing for users who do not want to type', () => {
    mockParse(ARABIC_RESPONSE)
    renderHome()
    expect(screen.getByText(/browse by topic/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Economic Growth/ })).toBeInTheDocument()
  })

  it('states that nothing is imputed or model-generated', () => {
    mockParse(ARABIC_RESPONSE)
    renderHome()
    expect(
      screen.getByText(/nothing is\s+interpolated, imputed, or generated by a language model/i),
    ).toBeInTheDocument()
  })
})
