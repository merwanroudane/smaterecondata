/**
 * Theme engine for SmatEconData (spec 0H).
 *
 * Holds four independent preferences, each persisted:
 *
 *   mode     light | dark | system   — `system` writes no attribute, so the
 *                                      CSS media query takes over
 *   preset   one of four light palettes
 *   density  comfortable | compact
 *   motion   auto | on | off         — `auto` honours prefers-reduced-motion
 *
 * State is written to `document.documentElement` as data attributes; the CSS
 * in `theme.css` does the rest. Nothing here contains a colour.
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

export type ThemeMode = 'light' | 'dark' | 'system'
export type ThemePreset = 'sunrise' | 'mint' | 'lavender' | 'sky'
export type ThemeDensity = 'comfortable' | 'compact'
export type MotionPreference = 'auto' | 'on' | 'off'

export interface PresetInfo {
  id: ThemePreset
  name: string
  description: string
  /** Swatch colours for the picker, read from the preset's own palette. */
  swatch: [string, string, string]
}

export const THEME_PRESETS: PresetInfo[] = [
  {
    id: 'sunrise',
    name: 'Sunrise Research',
    description: 'Coral, teal and warm neutrals',
    swatch: ['#F26B4F', '#2AAE9B', '#F2B84B'],
  },
  {
    id: 'mint',
    name: 'Mint Lab',
    description: 'Emerald, aqua and white',
    swatch: ['#0F9D76', '#2C8CA8', '#64C9A0'],
  },
  {
    id: 'lavender',
    name: 'Lavender Paper',
    description: 'Violet, rose and soft grey',
    swatch: ['#7256C9', '#C25F97', '#E0A3C4'],
  },
  {
    id: 'sky',
    name: 'Sky Citrus',
    description: 'Blue sky and citrus',
    swatch: ['#1F7FC4', '#EF8F2E', '#F4C145'],
  },
]

const STORAGE_KEYS = {
  mode: 'smatecondata_theme_mode',
  preset: 'smatecondata_theme_preset',
  density: 'smatecondata_theme_density',
  motion: 'smatecondata_theme_motion',
} as const

interface ThemeContextValue {
  mode: ThemeMode
  preset: ThemePreset
  density: ThemeDensity
  motion: MotionPreference
  /** The mode actually in force once `system` is resolved. */
  resolvedMode: 'light' | 'dark'
  /** Whether decorative motion should run right now. */
  motionEnabled: boolean
  presets: PresetInfo[]
  setMode: (mode: ThemeMode) => void
  setPreset: (preset: ThemePreset) => void
  setDensity: (density: ThemeDensity) => void
  setMotion: (motion: MotionPreference) => void
}

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined)

/** localStorage can throw (private mode, blocked site data) — never let it break the app. */
function readStored<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const value = window.localStorage.getItem(key)
    if (value && (allowed as readonly string[]).includes(value)) {
      return value as T
    }
  } catch {
    /* ignore */
  }
  return fallback
}

function writeStored(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    /* ignore */
  }
}

function prefersDark(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-color-scheme: dark)').matches
  )
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(() =>
    readStored(STORAGE_KEYS.mode, ['light', 'dark', 'system'] as const, 'light'),
  )
  const [preset, setPresetState] = useState<ThemePreset>(() =>
    readStored(
      STORAGE_KEYS.preset,
      ['sunrise', 'mint', 'lavender', 'sky'] as const,
      'sunrise',
    ),
  )
  const [density, setDensityState] = useState<ThemeDensity>(() =>
    readStored(STORAGE_KEYS.density, ['comfortable', 'compact'] as const, 'comfortable'),
  )
  const [motion, setMotionState] = useState<MotionPreference>(() =>
    readStored(STORAGE_KEYS.motion, ['auto', 'on', 'off'] as const, 'auto'),
  )

  const [systemDark, setSystemDark] = useState<boolean>(prefersDark)
  const [systemReducedMotion, setSystemReducedMotion] = useState<boolean>(prefersReducedMotion)

  // Track the OS preferences so `system` and `auto` stay live.
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return

    const darkQuery = window.matchMedia('(prefers-color-scheme: dark)')
    const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)')

    const onDark = (event: MediaQueryListEvent) => setSystemDark(event.matches)
    const onMotion = (event: MediaQueryListEvent) => setSystemReducedMotion(event.matches)

    darkQuery.addEventListener('change', onDark)
    motionQuery.addEventListener('change', onMotion)
    return () => {
      darkQuery.removeEventListener('change', onDark)
      motionQuery.removeEventListener('change', onMotion)
    }
  }, [])

  const resolvedMode: 'light' | 'dark' =
    mode === 'system' ? (systemDark ? 'dark' : 'light') : mode

  const motionEnabled = motion === 'on' || (motion === 'auto' && !systemReducedMotion)

  // Project state onto the document; CSS does the rest.
  useEffect(() => {
    const root = document.documentElement
    if (mode === 'system') {
      // No attribute: the prefers-color-scheme media query governs.
      root.removeAttribute('data-mode')
    } else {
      root.setAttribute('data-mode', mode)
    }
    root.setAttribute('data-preset', preset)
    root.setAttribute('data-density', density)
    root.setAttribute('data-motion', motion === 'auto' ? 'auto' : motion)

    // Keep the browser UI (form controls, scrollbars) in step.
    root.style.colorScheme = resolvedMode
  }, [mode, preset, density, motion, resolvedMode])

  // Pause decorative animation while the tab is hidden (spec 0G.1).
  useEffect(() => {
    const onVisibility = () => {
      document.documentElement.setAttribute(
        'data-hidden',
        document.hidden ? 'true' : 'false',
      )
    }
    onVisibility()
    document.addEventListener('visibilitychange', onVisibility)
    return () => document.removeEventListener('visibilitychange', onVisibility)
  }, [])

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next)
    writeStored(STORAGE_KEYS.mode, next)
  }, [])

  const setPreset = useCallback((next: ThemePreset) => {
    setPresetState(next)
    writeStored(STORAGE_KEYS.preset, next)
  }, [])

  const setDensity = useCallback((next: ThemeDensity) => {
    setDensityState(next)
    writeStored(STORAGE_KEYS.density, next)
  }, [])

  const setMotion = useCallback((next: MotionPreference) => {
    setMotionState(next)
    writeStored(STORAGE_KEYS.motion, next)
  }, [])

  const value = useMemo<ThemeContextValue>(
    () => ({
      mode,
      preset,
      density,
      motion,
      resolvedMode,
      motionEnabled,
      presets: THEME_PRESETS,
      setMode,
      setPreset,
      setDensity,
      setMotion,
    }),
    [
      mode,
      preset,
      density,
      motion,
      resolvedMode,
      motionEnabled,
      setMode,
      setPreset,
      setDensity,
      setMotion,
    ],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext)
  if (!context) {
    throw new Error('useTheme must be used within a ThemeProvider')
  }
  return context
}
