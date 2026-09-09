/**
 * Data Cart tests (spec 0D, 20, 0R items 6-7).
 *
 * The acceptance tests pinned here: a user can add multiple series to a
 * persistent cart, and can change countries/years for the WHOLE cart in one
 * action rather than editing each row.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { CartProvider, useCart } from '../CartContext'

function Probe() {
  const cart = useCart()
  return (
    <div>
      <span data-testid="count">{cart.count}</span>
      <span data-testid="items">{cart.items.map((i) => i.concept).join(',')}</span>
      <span data-testid="labels">{cart.items.map((i) => i.label).join(',')}</span>
      <span data-testid="geos">{cart.settings.geographies.join(',')}</span>
      <span data-testid="years">
        {cart.settings.startYear ?? '-'}:{cart.settings.endYear ?? '-'}
      </span>
      <span data-testid="shape">{cart.settings.shape}</span>
      <span data-testid="dupes">{cart.duplicateWarnings.join(',')}</span>
      <span data-testid="has-gdp">{String(cart.has('gdp'))}</span>

      <button onClick={() => cart.add({ concept: 'gdp', label: 'GDP' })}>add gdp</button>
      <button onClick={() => cart.add({ concept: 'cpi', label: 'Inflation' })}>
        add cpi
      </button>
      <button onClick={() => cart.add({ concept: 'unemp', label: 'GDP' })}>
        add dupe label
      </button>
      <button onClick={() => cart.remove('gdp')}>remove gdp</button>
      <button onClick={() => cart.clear()}>clear</button>
      <button onClick={() => cart.reorder(0, 1)}>swap</button>
      <button onClick={() => cart.rename('gdp', 'GDP per capita')}>rename</button>
      <button
        onClick={() =>
          cart.updateSettings({
            geographies: ['DZA', 'MAR', 'TUN'],
            startYear: 2000,
            endYear: 2023,
          })
        }
      >
        bulk
      </button>
      <button onClick={() => cart.updateSettings({ shape: 'long' })}>long</button>
    </div>
  )
}

const renderCart = () =>
  render(
    <CartProvider>
      <Probe />
    </CartProvider>,
  )

describe('CartProvider', () => {
  beforeEach(() => localStorage.clear())

  it('starts empty', () => {
    renderCart()
    expect(screen.getByTestId('count')).toHaveTextContent('0')
  })

  it('collects series from separate searches', async () => {
    const user = userEvent.setup()
    renderCart()

    await user.click(screen.getByText('add gdp'))
    await user.click(screen.getByText('add cpi'))

    expect(screen.getByTestId('count')).toHaveTextContent('2')
    expect(screen.getByTestId('items')).toHaveTextContent('gdp,cpi')
  })

  it('refuses to add the same concept twice', async () => {
    const user = userEvent.setup()
    renderCart()

    await user.click(screen.getByText('add gdp'))
    await user.click(screen.getByText('add gdp'))

    expect(screen.getByTestId('count')).toHaveTextContent('1')
  })

  it('reports membership', async () => {
    const user = userEvent.setup()
    renderCart()
    expect(screen.getByTestId('has-gdp')).toHaveTextContent('false')

    await user.click(screen.getByText('add gdp'))
    expect(screen.getByTestId('has-gdp')).toHaveTextContent('true')
  })

  it('removes and clears', async () => {
    const user = userEvent.setup()
    renderCart()

    await user.click(screen.getByText('add gdp'))
    await user.click(screen.getByText('add cpi'))
    await user.click(screen.getByText('remove gdp'))
    expect(screen.getByTestId('items')).toHaveTextContent('cpi')

    await user.click(screen.getByText('clear'))
    expect(screen.getByTestId('count')).toHaveTextContent('0')
  })

  it('reorders variables', async () => {
    const user = userEvent.setup()
    renderCart()

    await user.click(screen.getByText('add gdp'))
    await user.click(screen.getByText('add cpi'))
    await user.click(screen.getByText('swap'))

    expect(screen.getByTestId('items')).toHaveTextContent('cpi,gdp')
  })

  it('ignores an out-of-range reorder', async () => {
    const user = userEvent.setup()
    renderCart()
    await user.click(screen.getByText('add gdp'))
    // Only one item: reorder(0,1) must be a no-op, not a crash.
    await user.click(screen.getByText('swap'))
    expect(screen.getByTestId('items')).toHaveTextContent('gdp')
  })

  it('renames a display label without losing the concept', async () => {
    const user = userEvent.setup()
    renderCart()

    await user.click(screen.getByText('add gdp'))
    await user.click(screen.getByText('rename'))

    expect(screen.getByTestId('labels')).toHaveTextContent('GDP per capita')
    expect(screen.getByTestId('items')).toHaveTextContent('gdp')
  })

  it('applies a bulk country and period change to the whole cart', async () => {
    const user = userEvent.setup()
    renderCart()

    await user.click(screen.getByText('add gdp'))
    await user.click(screen.getByText('add cpi'))
    await user.click(screen.getByText('bulk'))

    // One action, both series affected — the point of cart-level settings.
    expect(screen.getByTestId('geos')).toHaveTextContent('DZA,MAR,TUN')
    expect(screen.getByTestId('years')).toHaveTextContent('2000:2023')
    expect(screen.getByTestId('count')).toHaveTextContent('2')
  })

  it('switches output shape for the whole cart', async () => {
    const user = userEvent.setup()
    renderCart()
    await user.click(screen.getByText('long'))
    expect(screen.getByTestId('shape')).toHaveTextContent('long')
  })

  it('warns when two concepts share a display label', async () => {
    const user = userEvent.setup()
    renderCart()

    await user.click(screen.getByText('add gdp'))
    await user.click(screen.getByText('add dupe label'))

    expect(screen.getByTestId('dupes')).toHaveTextContent('gdp')
  })

  it('persists across a remount', async () => {
    const user = userEvent.setup()
    const { unmount } = renderCart()

    await user.click(screen.getByText('add gdp'))
    await user.click(screen.getByText('bulk'))
    unmount()

    renderCart()
    expect(screen.getByTestId('items')).toHaveTextContent('gdp')
    expect(screen.getByTestId('geos')).toHaveTextContent('DZA,MAR,TUN')
  })

  it('recovers from corrupted stored state', () => {
    localStorage.setItem('smatecondata_cart', '{not json')
    expect(() => renderCart()).not.toThrow()
    expect(screen.getByTestId('count')).toHaveTextContent('0')
  })

  it('back-fills settings added after a cart was stored', () => {
    // An older stored cart has no `shape`; it must not come back undefined.
    localStorage.setItem(
      'smatecondata_cart',
      JSON.stringify({ items: [], settings: { geographies: ['DZA'] } }),
    )
    renderCart()
    expect(screen.getByTestId('shape')).toHaveTextContent('wide')
    expect(screen.getByTestId('geos')).toHaveTextContent('DZA')
  })

  it('throws outside the provider', () => {
    expect(() => render(<Probe />)).toThrow(/within a CartProvider/)
  })
})
