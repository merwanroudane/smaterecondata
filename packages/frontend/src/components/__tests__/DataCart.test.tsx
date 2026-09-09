/**
 * Data Cart panel tests (spec 0D, 0R items 6-8).
 *
 * Includes a regression test for a bug found in the browser: the country chip
 * list is capped for length, and a region preset could select a country that
 * fell outside the visible window — leaving it selected but impossible to see
 * or deselect.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CartProvider } from '../../contexts/CartContext'
import { I18nProvider } from '../../i18n'
import { DataCart } from '../DataCart'

/** 60 countries, so the 40-chip cap is genuinely exceeded. */
const COUNTRIES = Array.from({ length: 60 }, (_, i) => ({
  iso3: `C${String(i).padStart(2, '0')}`,
  name: `Country ${i}`,
})).concat([
  { iso3: 'DZA', name: 'Algeria', aliases: ['Algeria', 'Algerie', 'الجزائر'] },
  { iso3: 'MAR', name: 'Morocco', aliases: ['Morocco', 'Maroc', 'المغرب'] },
  { iso3: 'TUN', name: 'Tunisia', aliases: ['Tunisia', 'Tunisie', 'تونس'] },
  { iso3: 'LBY', name: 'Libya', aliases: ['Libya', 'Libye', 'ليبيا'] },
  { iso3: 'MRT', name: 'Mauritania', aliases: ['Mauritania', 'Mauritanie', 'موريتانيا'] },
])

const REGIONS = [{ key: 'maghreb', members: ['DZA', 'MAR', 'TUN', 'LBY', 'MRT'] }]

function mockCatalog() {
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    const target = String(url)
    if (target.includes('/v1/concepts')) {
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ concepts: [] }) })
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => ({ countries: COUNTRIES, regions: REGIONS }),
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderCart(onBuild = vi.fn()) {
  const utils = render(
    <I18nProvider>
      <CartProvider>
        <DataCart open onClose={vi.fn()} onBuild={onBuild} />
      </CartProvider>
    </I18nProvider>,
  )
  return { ...utils, onBuild }
}

describe('DataCart', () => {
  beforeEach(() => {
    localStorage.clear()
    mockCatalog()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('shows a helpful empty state rather than a blank panel', () => {
    renderCart()
    expect(screen.getByText('Your cart is empty.')).toBeInTheDocument()
    expect(
      screen.getByText(/Search for an indicator and add it/i),
    ).toBeInTheDocument()
  })

  it('renders collected items with their order', async () => {
    localStorage.setItem(
      'smatecondata_cart',
      JSON.stringify({
        items: [
          { concept: 'gdp', label: 'GDP per capita', addedAt: 1 },
          { concept: 'cpi', label: 'Inflation', addedAt: 2 },
        ],
        settings: {},
      }),
    )
    renderCart()

    expect(screen.getByText('GDP per capita')).toBeInTheDocument()
    expect(screen.getByText('Inflation')).toBeInTheDocument()
    expect(screen.getByText('Data Cart')).toBeInTheDocument()
  })

  it('keeps Build disabled until a country is chosen', async () => {
    localStorage.setItem(
      'smatecondata_cart',
      JSON.stringify({
        items: [{ concept: 'gdp', label: 'GDP', addedAt: 1 }],
        settings: { geographies: [] },
      }),
    )
    renderCart()
    expect(screen.getByRole('button', { name: 'Build Dataset' })).toBeDisabled()
  })

  it('applies a region preset to the whole cart in one click', async () => {
    localStorage.setItem(
      'smatecondata_cart',
      JSON.stringify({
        items: [{ concept: 'gdp', label: 'GDP', addedAt: 1 }],
        settings: { geographies: [] },
      }),
    )
    const user = userEvent.setup()
    renderCart()

    await waitFor(() => screen.getByRole('button', { name: 'maghreb' }))
    await user.click(screen.getByRole('button', { name: 'maghreb' }))

    const stored = JSON.parse(localStorage.getItem('smatecondata_cart')!)
    expect(stored.settings.geographies).toEqual(['DZA', 'MAR', 'TUN', 'LBY', 'MRT'])
    expect(screen.getByRole('button', { name: 'Build Dataset' })).toBeEnabled()
  })

  it('keeps every selected country visible even past the chip cap', async () => {
    // Regression: the Maghreb preset selects TUN, which sorts beyond the
    // 40-chip window. It must still render, and render as pressed.
    localStorage.setItem(
      'smatecondata_cart',
      JSON.stringify({
        items: [{ concept: 'gdp', label: 'GDP', addedAt: 1 }],
        settings: { geographies: ['DZA', 'MAR', 'TUN', 'LBY', 'MRT'] },
      }),
    )
    renderCart()

    await waitFor(() => screen.getByRole('button', { name: 'TUN' }))

    for (const iso3 of ['DZA', 'MAR', 'TUN', 'LBY', 'MRT']) {
      const chip = screen.getByRole('button', { name: iso3 })
      expect(chip).toBeInTheDocument()
      expect(chip).toHaveAttribute('aria-pressed', 'true')
    }
  })

  it('lets a user deselect a country chosen by a preset', async () => {
    localStorage.setItem(
      'smatecondata_cart',
      JSON.stringify({
        items: [{ concept: 'gdp', label: 'GDP', addedAt: 1 }],
        settings: { geographies: ['DZA', 'MAR', 'TUN', 'LBY', 'MRT'] },
      }),
    )
    const user = userEvent.setup()
    renderCart()

    await waitFor(() => screen.getByRole('button', { name: 'TUN' }))
    await user.click(screen.getByRole('button', { name: 'TUN' }))

    const stored = JSON.parse(localStorage.getItem('smatecondata_cart')!)
    expect(stored.settings.geographies).not.toContain('TUN')
  })

  it('hands the full request to onBuild', async () => {
    localStorage.setItem(
      'smatecondata_cart',
      JSON.stringify({
        items: [
          { concept: 'gdp', label: 'GDP', addedAt: 1 },
          { concept: 'cpi', label: 'Inflation', addedAt: 2 },
        ],
        settings: {
          geographies: ['DZA', 'MAR'],
          startYear: 2010,
          endYear: 2023,
          frequency: 'annual',
          shape: 'wide',
        },
      }),
    )
    const user = userEvent.setup()
    const { onBuild } = renderCart()

    await user.click(screen.getByRole('button', { name: 'Build Dataset' }))

    expect(onBuild).toHaveBeenCalledWith({
      concepts: ['gdp', 'cpi'],
      geographies: ['DZA', 'MAR'],
      startYear: 2010,
      endYear: 2023,
      frequency: 'annual',
      shape: 'wide',
    })
  })

  // -- Test G: the country box selects, it does not merely filter ----------

  it.each([
    ['English', 'Tunisia'],
    ['French', 'Tunisie'],
    ['Arabic', 'تونس'],
  ])('selects TUN when %s is typed and Enter pressed', async (_label, typed) => {
    localStorage.setItem(
      'smatecondata_cart',
      JSON.stringify({
        items: [{ concept: 'gdp', label: 'GDP', addedAt: 1 }],
        settings: { geographies: [] },
      }),
    )
    const user = userEvent.setup()
    renderCart()

    const box = await screen.findByPlaceholderText(/press Enter/i)
    await user.type(box, typed)
    await user.keyboard('{Enter}')

    const stored = JSON.parse(localStorage.getItem('smatecondata_cart')!)
    expect(stored.settings.geographies).toContain('TUN')
    expect(screen.getByRole('button', { name: 'Build Dataset' })).toBeEnabled()
  })

  it('offers clickable suggestions', async () => {
    localStorage.setItem(
      'smatecondata_cart',
      JSON.stringify({
        items: [{ concept: 'gdp', label: 'GDP', addedAt: 1 }],
        settings: { geographies: [] },
      }),
    )
    const user = userEvent.setup()
    renderCart()

    await user.type(await screen.findByPlaceholderText(/press Enter/i), 'Moroc')
    const listbox = await screen.findByRole('listbox')
    await user.click(within(listbox).getByRole('button', { name: /Morocco/ }))

    const stored = JSON.parse(localStorage.getItem('smatecondata_cart')!)
    expect(stored.settings.geographies).toEqual(['MAR'])
  })

  it('clears the box after a selection so the next country can be typed', async () => {
    localStorage.setItem(
      'smatecondata_cart',
      JSON.stringify({
        items: [{ concept: 'gdp', label: 'GDP', addedAt: 1 }],
        settings: { geographies: [] },
      }),
    )
    const user = userEvent.setup()
    renderCart()
    const box = (await screen.findByPlaceholderText(/press Enter/i)) as HTMLInputElement
    await user.type(box, 'Tunisia')
    await user.keyboard('{Enter}')
    expect(box.value).toBe('')
  })

  it('warns about a duplicate display label', async () => {
    localStorage.setItem(
      'smatecondata_cart',
      JSON.stringify({
        items: [
          { concept: 'gdp', label: 'GDP', addedAt: 1 },
          { concept: 'unemp', label: 'GDP', addedAt: 2 },
        ],
        settings: {},
      }),
    )
    renderCart()
    expect(screen.getByRole('status')).toHaveTextContent(/already in the cart/i)
  })

  it('flags an item that still needs a definition', () => {
    localStorage.setItem(
      'smatecondata_cart',
      JSON.stringify({
        items: [{ concept: 'inflation', label: 'Inflation', ambiguous: true, addedAt: 1 }],
        settings: {},
      }),
    )
    renderCart()
    expect(screen.getByText('needs a definition')).toBeInTheDocument()
  })
})
