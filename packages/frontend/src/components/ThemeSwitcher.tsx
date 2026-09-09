/**
 * Theme control (spec 0H).
 *
 * Light / Dark / System, four light presets, density and a motion toggle.
 * Every control is a real button with an accessible name and a visible
 * pressed state, so the current selection is never conveyed by colour alone.
 */

import { useEffect, useRef, useState } from 'react'
import {
  useTheme,
  type MotionPreference,
  type ThemeDensity,
  type ThemeMode,
} from '../contexts/ThemeContext'
import './ThemeSwitcher.css'

const MODES: { id: ThemeMode; label: string; icon: string }[] = [
  { id: 'light', label: 'Light', icon: '☀' },
  { id: 'dark', label: 'Dark', icon: '☾' },
  { id: 'system', label: 'System', icon: '◐' },
]

const DENSITIES: { id: ThemeDensity; label: string }[] = [
  { id: 'comfortable', label: 'Comfortable' },
  { id: 'compact', label: 'Compact' },
]

const MOTIONS: { id: MotionPreference; label: string }[] = [
  { id: 'auto', label: 'Auto' },
  { id: 'on', label: 'On' },
  { id: 'off', label: 'Off' },
]

export function ThemeSwitcher() {
  const {
    mode,
    preset,
    density,
    motion,
    presets,
    setMode,
    setPreset,
    setDensity,
    setMotion,
  } = useTheme()

  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  // Close on outside click and on Escape — a panel that traps the user is worse
  // than no panel.
  useEffect(() => {
    if (!open) return

    const onPointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }

    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  const activePreset = presets.find((p) => p.id === preset)

  return (
    <div className="theme-switcher" ref={containerRef}>
      <button
        type="button"
        className="theme-switcher__trigger"
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label="Appearance settings"
        onClick={() => setOpen((value) => !value)}
      >
        <span className="theme-switcher__dots" aria-hidden="true">
          {activePreset?.swatch.map((colour) => (
            <span key={colour} style={{ background: colour }} />
          ))}
        </span>
        <span className="theme-switcher__trigger-label">Theme</span>
      </button>

      {open && (
        <div className="theme-switcher__panel" role="dialog" aria-label="Appearance">
          <section className="theme-switcher__section">
            <h3>Mode</h3>
            <div className="theme-switcher__segmented" role="group" aria-label="Colour mode">
              {MODES.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className="theme-switcher__segment"
                  aria-pressed={mode === item.id}
                  onClick={() => setMode(item.id)}
                >
                  <span aria-hidden="true">{item.icon}</span>
                  {item.label}
                </button>
              ))}
            </div>
          </section>

          <section className="theme-switcher__section">
            <h3>Palette</h3>
            <ul className="theme-switcher__presets">
              {presets.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className="theme-switcher__preset"
                    aria-pressed={preset === item.id}
                    onClick={() => setPreset(item.id)}
                  >
                    <span className="theme-switcher__dots" aria-hidden="true">
                      {item.swatch.map((colour) => (
                        <span key={colour} style={{ background: colour }} />
                      ))}
                    </span>
                    <span className="theme-switcher__preset-text">
                      <strong>{item.name}</strong>
                      <small>{item.description}</small>
                    </span>
                    {preset === item.id && (
                      <span className="theme-switcher__check" aria-hidden="true">
                        ✓
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          </section>

          <section className="theme-switcher__section">
            <h3>Density</h3>
            <div className="theme-switcher__segmented" role="group" aria-label="Density">
              {DENSITIES.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className="theme-switcher__segment"
                  aria-pressed={density === item.id}
                  onClick={() => setDensity(item.id)}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </section>

          <section className="theme-switcher__section">
            <h3>Decorative motion</h3>
            <div className="theme-switcher__segmented" role="group" aria-label="Motion">
              {MOTIONS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className="theme-switcher__segment"
                  aria-pressed={motion === item.id}
                  onClick={() => setMotion(item.id)}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <p className="theme-switcher__hint">
              Auto follows your system’s reduced-motion setting.
            </p>
          </section>
        </div>
      )}
    </div>
  )
}
