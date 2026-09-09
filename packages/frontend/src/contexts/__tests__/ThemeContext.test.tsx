/**
 * Theme engine tests (spec 0H, 0R items 11-14, 21).
 *
 * The acceptance tests these pin: light is the polished default, dark works,
 * at least four presets exist, the preference persists, and reduced motion is
 * honoured.
 */

import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  THEME_PRESETS,
  ThemeProvider,
  useTheme,
  type ThemePreset,
} from '../ThemeContext'

/** Drive prefers-color-scheme / prefers-reduced-motion for one test. */
function mockMatchMedia(matches: Record<string, boolean>) {
  const listeners = new Map<string, Set<(e: MediaQueryListEvent) => void>>()

  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: matches[query] ?? false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: (_: string, cb: (e: MediaQueryListEvent) => void) => {
        if (!listeners.has(query)) listeners.set(query, new Set())
        listeners.get(query)!.add(cb)
      },
      removeEventListener: (_: string, cb: (e: MediaQueryListEvent) => void) => {
        listeners.get(query)?.delete(cb)
      },
      dispatchEvent: vi.fn(),
    })),
  })

  return {
    change(query: string, value: boolean) {
      matches[query] = value
      listeners.get(query)?.forEach((cb) =>
        cb({ matches: value } as MediaQueryListEvent),
      )
    },
  }
}

function Probe() {
  const theme = useTheme()
  return (
    <div>
      <span data-testid="mode">{theme.mode}</span>
      <span data-testid="resolved">{theme.resolvedMode}</span>
      <span data-testid="preset">{theme.preset}</span>
      <span data-testid="motion-enabled">{String(theme.motionEnabled)}</span>
      <button onClick={() => theme.setMode('dark')}>go dark</button>
      <button onClick={() => theme.setMode('system')}>go system</button>
      <button onClick={() => theme.setPreset('mint')}>go mint</button>
      <button onClick={() => theme.setDensity('compact')}>go compact</button>
      <button onClick={() => theme.setMotion('off')}>motion off</button>
    </div>
  )
}

const renderTheme = () =>
  render(
    <ThemeProvider>
      <Probe />
    </ThemeProvider>,
  )

describe('ThemeProvider', () => {
  beforeEach(() => {
    localStorage.clear()
    const root = document.documentElement
    for (const attr of ['data-mode', 'data-preset', 'data-density', 'data-motion']) {
      root.removeAttribute(attr)
    }
    mockMatchMedia({})
  })

  it('defaults to light with the Sunrise Research preset', () => {
    renderTheme()
    expect(screen.getByTestId('mode')).toHaveTextContent('light')
    expect(screen.getByTestId('preset')).toHaveTextContent('sunrise')
    expect(document.documentElement.getAttribute('data-mode')).toBe('light')
    expect(document.documentElement.getAttribute('data-preset')).toBe('sunrise')
  })

  it('ships at least four light presets', () => {
    expect(THEME_PRESETS.length).toBeGreaterThanOrEqual(4)
    const ids = THEME_PRESETS.map((p) => p.id)
    expect(new Set(ids).size).toBe(ids.length)
    for (const preset of THEME_PRESETS) {
      expect(preset.name).toBeTruthy()
      expect(preset.swatch).toHaveLength(3)
    }
  })

  it('switches to dark and writes the attribute', async () => {
    const user = userEvent.setup()
    renderTheme()
    await user.click(screen.getByText('go dark'))

    expect(screen.getByTestId('resolved')).toHaveTextContent('dark')
    expect(document.documentElement.getAttribute('data-mode')).toBe('dark')
  })

  it('removes data-mode for "system" so CSS can follow the OS', async () => {
    const user = userEvent.setup()
    renderTheme()
    await user.click(screen.getByText('go system'))

    expect(document.documentElement.hasAttribute('data-mode')).toBe(false)
    expect(screen.getByTestId('mode')).toHaveTextContent('system')
  })

  it('resolves "system" against the OS preference', async () => {
    mockMatchMedia({ '(prefers-color-scheme: dark)': true })
    const user = userEvent.setup()
    renderTheme()
    await user.click(screen.getByText('go system'))

    expect(screen.getByTestId('resolved')).toHaveTextContent('dark')
  })

  it('reacts when the OS preference changes while on "system"', async () => {
    const media = mockMatchMedia({ '(prefers-color-scheme: dark)': false })
    const user = userEvent.setup()
    renderTheme()
    await user.click(screen.getByText('go system'))
    expect(screen.getByTestId('resolved')).toHaveTextContent('light')

    act(() => media.change('(prefers-color-scheme: dark)', true))
    expect(screen.getByTestId('resolved')).toHaveTextContent('dark')
  })

  it('persists every preference', async () => {
    const user = userEvent.setup()
    renderTheme()

    await user.click(screen.getByText('go dark'))
    await user.click(screen.getByText('go mint'))
    await user.click(screen.getByText('go compact'))
    await user.click(screen.getByText('motion off'))

    expect(localStorage.getItem('smatecondata_theme_mode')).toBe('dark')
    expect(localStorage.getItem('smatecondata_theme_preset')).toBe('mint')
    expect(localStorage.getItem('smatecondata_theme_density')).toBe('compact')
    expect(localStorage.getItem('smatecondata_theme_motion')).toBe('off')
  })

  it('restores a stored preference on mount', () => {
    localStorage.setItem('smatecondata_theme_mode', 'dark')
    localStorage.setItem('smatecondata_theme_preset', 'lavender')
    renderTheme()

    expect(screen.getByTestId('mode')).toHaveTextContent('dark')
    expect(screen.getByTestId('preset')).toHaveTextContent('lavender')
  })

  it('ignores a corrupted stored value rather than breaking', () => {
    localStorage.setItem('smatecondata_theme_preset', 'not-a-preset')
    renderTheme()
    expect(screen.getByTestId('preset')).toHaveTextContent('sunrise')
  })

  it('survives localStorage throwing', () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked site data')
    })
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked site data')
    })

    expect(() => renderTheme()).not.toThrow()
    expect(screen.getByTestId('preset')).toHaveTextContent('sunrise')

    getItem.mockRestore()
    setItem.mockRestore()
  })

  it('honours prefers-reduced-motion by default', () => {
    mockMatchMedia({ '(prefers-reduced-motion: reduce)': true })
    renderTheme()
    expect(screen.getByTestId('motion-enabled')).toHaveTextContent('false')
  })

  it('lets the user force motion off regardless of the OS', async () => {
    const user = userEvent.setup()
    renderTheme()
    expect(screen.getByTestId('motion-enabled')).toHaveTextContent('true')

    await user.click(screen.getByText('motion off'))
    expect(screen.getByTestId('motion-enabled')).toHaveTextContent('false')
    expect(document.documentElement.getAttribute('data-motion')).toBe('off')
  })

  it('applies each preset as a data attribute', async () => {
    const user = userEvent.setup()
    renderTheme()
    await user.click(screen.getByText('go mint'))
    expect(document.documentElement.getAttribute('data-preset')).toBe('mint')
  })

  it('throws a helpful error when used outside the provider', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => render(<Probe />)).toThrow(/within a ThemeProvider/)
    spy.mockRestore()
  })
})

describe('preset catalogue', () => {
  it('names the four presets the spec calls for', () => {
    const ids = THEME_PRESETS.map((p) => p.id) as ThemePreset[]
    expect(ids).toEqual(
      expect.arrayContaining(['sunrise', 'mint', 'lavender', 'sky']),
    )
  })
})
