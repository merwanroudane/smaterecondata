/**
 * Data Cart panel (spec 0D, 20).
 *
 * Shows what has been collected and exposes the BULK actions the spec calls
 * for — countries, period, frequency and shape apply to every series at once,
 * which is the difference between building a five-indicator panel in one step
 * and editing five rows by hand.
 *
 * `Build Dataset` is the single visually obvious primary action.
 */

import { useState } from 'react'
import { useCart } from '../contexts/CartContext'
import { useI18n } from '../i18n'
import { useCatalog } from './SearchModes'
import './DataCart.css'

const FREQUENCIES = ['annual', 'quarterly', 'monthly']

const PROVIDERS = [
  { id: '', label: 'automatic' },
  { id: 'world_bank', label: 'World Bank' },
  { id: 'imf', label: 'IMF' },
  { id: 'fred', label: 'FRED' },
  { id: 'eurostat', label: 'Eurostat' },
  { id: 'oecd', label: 'OECD' },
]

interface DataCartProps {
  open: boolean
  onClose: () => void
  onBuild: (payload: {
    concepts: string[]
    geographies: string[]
    startYear: number | null
    endYear: number | null
    frequency: string
    shape: string
  }) => void
}

export function DataCart({ open, onClose, onBuild }: DataCartProps) {
  const { t, formatNumber } = useI18n()
  const {
    items,
    settings,
    count,
    remove,
    clear,
    reorder,
    updateSettings,
    duplicateWarnings,
  } = useCart()
  const { countries, regions } = useCatalog()
  const [geoFilter, setGeoFilter] = useState('')

  if (!open) return null

  const toggleGeo = (iso3: string) => {
    updateSettings({
      geographies: settings.geographies.includes(iso3)
        ? settings.geographies.filter((g) => g !== iso3)
        : [...settings.geographies, iso3],
    })
  }

  // The chip list is capped for length, but a SELECTED country must always be
  // visible — otherwise a region preset can select a country the user can
  // neither see nor deselect. Selected first, then the rest of the matches.
  const matching = countries.filter(
    (c) =>
      !geoFilter ||
      c.name.toLowerCase().includes(geoFilter.toLowerCase()) ||
      c.iso3.toLowerCase().includes(geoFilter.toLowerCase()),
  )
  const selectedFirst = [
    ...matching.filter((c) => settings.geographies.includes(c.iso3)),
    ...matching.filter((c) => !settings.geographies.includes(c.iso3)),
  ]
  const CHIP_LIMIT = 40
  const visibleCountries = selectedFirst.slice(
    0,
    Math.max(CHIP_LIMIT, settings.geographies.length),
  )

  const canBuild = count > 0 && settings.geographies.length > 0

  return (
    <aside className="cart" role="complementary" aria-label={t('cart.title')}>
      <header className="cart__head">
        <h2>
          {t('cart.title')}
          <span className="cart__count">{formatNumber(count)}</span>
        </h2>
        <button type="button" className="cart__close" onClick={onClose}>
          {t('common.close')}
        </button>
      </header>

      {count === 0 ? (
        <div className="cart__empty">
          <p className="cart__empty-title">{t('cart.empty')}</p>
          <p className="cart__empty-hint">{t('cart.emptyHint')}</p>
        </div>
      ) : (
        <>
          {duplicateWarnings.length > 0 && (
            <p className="cart__warning" role="status">
              {t('cart.duplicateWarning')} — {duplicateWarnings.join(', ')}
            </p>
          )}

          <ul className="cart__items">
            {items.map((item, index) => (
              <li key={item.concept} className="cart__item">
                <span className="cart__item-order">{index + 1}</span>
                <span className="cart__item-body">
                  <span className="cart__item-label" dir="auto">
                    {item.label}
                  </span>
                  <span className="cart__item-meta">
                    {item.topic ?? item.concept}
                    {item.ambiguous && (
                      <span className="cart__badge">needs a definition</span>
                    )}
                  </span>
                </span>
                <span className="cart__item-actions">
                  <button
                    type="button"
                    aria-label={`Move ${item.label} up`}
                    disabled={index === 0}
                    onClick={() => reorder(index, index - 1)}
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    aria-label={`Move ${item.label} down`}
                    disabled={index === items.length - 1}
                    onClick={() => reorder(index, index + 1)}
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    className="cart__remove"
                    aria-label={`${t('cart.remove')} ${item.label}`}
                    onClick={() => remove(item.concept)}
                  >
                    ✕
                  </button>
                </span>
              </li>
            ))}
          </ul>

          <section className="cart__section">
            <h3>{t('cart.bulk')}</h3>

            <label className="cart__field">
              <span>{t('cart.countries')}</span>
              <input
                value={geoFilter}
                onChange={(event) => setGeoFilter(event.target.value)}
                placeholder={t('guided.where')}
                dir="auto"
              />
            </label>

            <div className="cart__regions">
              {regions.map((region) => (
                <button
                  key={region.key}
                  type="button"
                  className="cart__region"
                  onClick={() => updateSettings({ geographies: region.members })}
                >
                  {region.key.replace(/_/g, ' ')}
                </button>
              ))}
            </div>

            <div className="cart__geos">
              {visibleCountries.map((country) => (
                <button
                  key={country.iso3}
                  type="button"
                  className="cart__geo"
                  aria-pressed={settings.geographies.includes(country.iso3)}
                  onClick={() => toggleGeo(country.iso3)}
                >
                  {country.iso3}
                </button>
              ))}
            </div>

            <div className="cart__row">
              <label className="cart__field">
                <span>{t('advanced.startYear')}</span>
                <input
                  type="number"
                  min={1900}
                  max={2100}
                  value={settings.startYear ?? ''}
                  onChange={(event) =>
                    updateSettings({
                      startYear: event.target.value ? Number(event.target.value) : null,
                    })
                  }
                />
              </label>
              <label className="cart__field">
                <span>{t('advanced.endYear')}</span>
                <input
                  type="number"
                  min={1900}
                  max={2100}
                  value={settings.endYear ?? ''}
                  onChange={(event) =>
                    updateSettings({
                      endYear: event.target.value ? Number(event.target.value) : null,
                    })
                  }
                />
              </label>
            </div>

            <div className="cart__row">
              <label className="cart__field">
                <span>{t('advanced.frequency')}</span>
                <select
                  value={settings.frequency}
                  onChange={(event) => updateSettings({ frequency: event.target.value })}
                >
                  {FREQUENCIES.map((f) => (
                    <option key={f} value={f}>
                      {f}
                    </option>
                  ))}
                </select>
              </label>
              <label className="cart__field">
                <span>{t('cart.shape')}</span>
                <select
                  value={settings.shape}
                  onChange={(event) =>
                    updateSettings({ shape: event.target.value as 'wide' | 'long' })
                  }
                >
                  <option value="wide">wide</option>
                  <option value="long">long</option>
                </select>
              </label>
            </div>

            <label className="cart__field">
              <span>{t('advanced.provider')}</span>
              <select
                value={settings.preferredProvider ?? ''}
                onChange={(event) =>
                  updateSettings({ preferredProvider: event.target.value || null })
                }
              >
                {PROVIDERS.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>
          </section>

          <footer className="cart__foot">
            <button type="button" className="cart__clear" onClick={clear}>
              {t('cart.clear')}
            </button>
            <button
              type="button"
              className="cart__build"
              disabled={!canBuild}
              onClick={() =>
                onBuild({
                  concepts: items.map((item) => item.concept),
                  geographies: settings.geographies,
                  startYear: settings.startYear,
                  endYear: settings.endYear,
                  frequency: settings.frequency,
                  shape: settings.shape,
                })
              }
            >
              {t('cart.build')}
            </button>
            {!canBuild && count > 0 && (
              <p className="cart__hint">{t('cart.countries')}</p>
            )}
          </footer>
        </>
      )}
    </aside>
  )
}
