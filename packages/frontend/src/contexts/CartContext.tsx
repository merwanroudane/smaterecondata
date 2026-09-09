/**
 * Data Cart (spec 0D, 20).
 *
 * A researcher searches for one indicator at a time, adds the best match, keeps
 * searching, then builds ONE dataset from everything collected. That is the
 * whole point of the cart, and it is what makes this different from a chatbot
 * that answers one question at a time.
 *
 * The cart holds *concept selections*, not observations: countries, period,
 * frequency and shape are cart-level settings applied to every series, so the
 * bulk actions the spec asks for ("change countries for all series", "change
 * date range for all") are a single state update rather than an N-item edit.
 *
 * Persisted to localStorage so a reload does not lose a half-built dataset.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

const STORAGE_KEY = 'smatecondata_cart'

export type OutputShape = 'wide' | 'long'

export interface CartItem {
  /** Internal concept key, e.g. `gdp_per_capita`. */
  concept: string
  /** Human label in the language it was discovered in. */
  label: string
  /** Provider preference for this concept, if the user pinned one. */
  provider?: string
  /** Resolved provider series, once chosen. */
  seriesId?: string
  unit?: string
  frequency?: string
  topic?: string
  /** True when the concept has several materially different definitions. */
  ambiguous?: boolean
  addedAt: number
}

export interface CartSettings {
  geographies: string[]
  startYear: number | null
  endYear: number | null
  frequency: string
  shape: OutputShape
  preferredProvider: string | null
}

interface CartState {
  items: CartItem[]
  settings: CartSettings
}

const DEFAULT_SETTINGS: CartSettings = {
  geographies: [],
  startYear: null,
  endYear: null,
  frequency: 'annual',
  shape: 'wide',
  preferredProvider: null,
}

interface CartContextValue extends CartState {
  count: number
  has: (concept: string) => boolean
  add: (item: Omit<CartItem, 'addedAt'>) => { added: boolean; reason?: string }
  remove: (concept: string) => void
  clear: () => void
  reorder: (from: number, to: number) => void
  rename: (concept: string, label: string) => void
  /** Pin a provider for one series without touching the others. */
  setItemProvider: (concept: string, provider: string | undefined) => void
  updateSettings: (patch: Partial<CartSettings>) => void
  /** Concepts appearing more than once by label — surfaced as a warning. */
  duplicateWarnings: string[]
}

const CartContext = createContext<CartContextValue | undefined>(undefined)

function readStored(): CartState {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return { items: [], settings: DEFAULT_SETTINGS }
    const parsed = JSON.parse(raw) as Partial<CartState>
    return {
      items: Array.isArray(parsed.items) ? parsed.items : [],
      // Merge rather than replace: a settings field added in a later release
      // must not come back undefined for someone with an older stored cart.
      settings: { ...DEFAULT_SETTINGS, ...(parsed.settings ?? {}) },
    }
  } catch {
    return { items: [], settings: DEFAULT_SETTINGS }
  }
}

export function CartProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<CartState>(readStored)

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
    } catch {
      /* a full or blocked store must not break the cart */
    }
  }, [state])

  const has = useCallback(
    (concept: string) => state.items.some((item) => item.concept === concept),
    [state.items],
  )

  const add = useCallback(
    (item: Omit<CartItem, 'addedAt'>) => {
      let result: { added: boolean; reason?: string } = { added: true }
      setState((current) => {
        if (current.items.some((existing) => existing.concept === item.concept)) {
          result = { added: false, reason: 'duplicate' }
          return current
        }
        return {
          ...current,
          items: [...current.items, { ...item, addedAt: Date.now() }],
        }
      })
      return result
    },
    [],
  )

  const remove = useCallback((concept: string) => {
    setState((current) => ({
      ...current,
      items: current.items.filter((item) => item.concept !== concept),
    }))
  }, [])

  const clear = useCallback(() => {
    setState((current) => ({ ...current, items: [] }))
  }, [])

  const reorder = useCallback((from: number, to: number) => {
    setState((current) => {
      if (
        from === to ||
        from < 0 ||
        to < 0 ||
        from >= current.items.length ||
        to >= current.items.length
      ) {
        return current
      }
      const items = [...current.items]
      const [moved] = items.splice(from, 1)
      items.splice(to, 0, moved)
      return { ...current, items }
    })
  }, [])

  const rename = useCallback((concept: string, label: string) => {
    setState((current) => ({
      ...current,
      items: current.items.map((item) =>
        item.concept === concept ? { ...item, label } : item,
      ),
    }))
  }, [])

  const setItemProvider = useCallback((concept: string, provider: string | undefined) => {
    setState((current) => ({
      ...current,
      items: current.items.map((item) =>
        item.concept === concept ? { ...item, provider } : item,
      ),
    }))
  }, [])

  const updateSettings = useCallback((patch: Partial<CartSettings>) => {
    setState((current) => ({ ...current, settings: { ...current.settings, ...patch } }))
  }, [])

  // Two different concepts sharing a display label would produce two columns a
  // reader cannot tell apart, so warn rather than silently rename.
  const duplicateWarnings = useMemo(() => {
    const seen = new Map<string, number>()
    for (const item of state.items) {
      const key = item.label.trim().toLowerCase()
      seen.set(key, (seen.get(key) ?? 0) + 1)
    }
    return [...seen.entries()].filter(([, n]) => n > 1).map(([label]) => label)
  }, [state.items])

  const value = useMemo<CartContextValue>(
    () => ({
      ...state,
      count: state.items.length,
      has,
      add,
      remove,
      clear,
      reorder,
      rename,
      setItemProvider,
      updateSettings,
      duplicateWarnings,
    }),
    [
      state,
      has,
      add,
      remove,
      clear,
      reorder,
      rename,
      setItemProvider,
      updateSettings,
      duplicateWarnings,
    ],
  )

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>
}

export function useCart(): CartContextValue {
  const context = useContext(CartContext)
  if (!context) {
    throw new Error('useCart must be used within a CartProvider')
  }
  return context
}
