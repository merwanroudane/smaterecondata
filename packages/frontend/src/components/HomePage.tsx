/**
 * SmatEconData home page (spec 0J, 0A.1, 0B, 0C, 0D).
 *
 * Quick Search is the default and the primary experience: type what you know,
 * press Enter, see exactly what was understood before anything is retrieved.
 * The other seven modes sit behind tabs as progressive disclosure — they never
 * clutter the default flow.
 *
 * The rotating placeholder cycles Arabic, English and French so the trilingual
 * capability is discoverable without a settings trip. It is independent of the
 * interface language: a French UI can still take an Arabic query.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useCart } from '../contexts/CartContext'
import { useI18n } from '../i18n'
import { DataCart } from './DataCart'
import { HeroAnimation } from './HeroAnimation'
import { LanguageSwitcher } from './LanguageSwitcher'
import {
  AdvancedSearch,
  BatchSearch,
  BrowseCatalog,
  ExactCodeSearch,
  GeographyFirst,
  GuidedSearch,
  SEARCH_MODES,
  SourceFirst,
  type SearchMode,
} from './SearchModes'
import { ThemeSwitcher } from './ThemeSwitcher'
import './HomePage.css'

const API_BASE = import.meta.env.VITE_API_URL || '/api'

interface Understanding {
  language: string
  countries: string[]
  iso3: string[]
  indicators: string[]
  concept_keys: string[]
  period: string
  frequency: string
  preferred_sources: string[]
  output: string[]
  needs_clarification: string[]
}

interface AmbiguousConcept {
  concept: string
  label: string
  distinctions: string[]
}

interface CatalogStats {
  searchable_series_count: number
  provider_count: number
  country_or_area_count: number
  display_count: string
  milestone: string
  last_catalog_sync: string | null
}

interface ParseResponse {
  understanding: Understanding
  ambiguous: AmbiguousConcept[]
}

const PLACEHOLDERS = [
  { lang: 'en', dir: 'ltr' as const, text: 'Inflation in Algeria from 2000 to 2025' },
  { lang: 'ar', dir: 'rtl' as const, text: 'التضخم والبطالة في الجزائر من 2000 إلى 2025' },
  {
    lang: 'fr',
    dir: 'ltr' as const,
    text: "Trouver les données d'investissement en Afrique du Nord",
  },
  { lang: 'en', dir: 'ltr' as const, text: 'GDP per capita for Maghreb countries' },
  { lang: 'ar', dir: 'rtl' as const, text: 'الناتج المحلي الإجمالي للفرد في المغرب العربي' },
  { lang: 'fr', dir: 'ltr' as const, text: 'PIB par habitant et chômage au Maghreb' },
]

const EXAMPLES = [
  'Inflation in Algeria from 2000 to 2025',
  'GDP per capita, unemployment and FDI for Morocco and Tunisia',
  'التضخم في مصر من 2010 إلى 2024',
  'PIB par habitant en Algérie de 2000 à 2023',
  'public debt for MENA countries, annual',
]

const TOPICS = [
  { key: 'Economic Growth', hint: 'GDP per capita' },
  { key: 'Prices & Inflation', hint: 'inflation' },
  { key: 'Labor Market', hint: 'unemployment' },
  { key: 'Trade', hint: 'exports and imports' },
  { key: 'Fiscal', hint: 'public debt' },
  { key: 'Monetary', hint: 'exchange rate' },
]

const LANGUAGE_NAMES: Record<string, string> = {
  ar: 'Arabic',
  en: 'English',
  fr: 'French',
}

export function HomePage() {
  const { t, formatNumber } = useI18n()
  const { count: cartCount, add, has } = useCart()

  const [mode, setMode] = useState<SearchMode>('quick')
  const [query, setQuery] = useState('')
  const [result, setResult] = useState<ParseResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [cartOpen, setCartOpen] = useState(false)
  const [placeholderIndex, setPlaceholderIndex] = useState(0)
  const [catalog, setCatalog] = useState<CatalogStats | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Rotate slowly enough not to distract (spec 0G.1), and stop once typing.
  useEffect(() => {
    if (query) return
    const id = window.setInterval(
      () => setPlaceholderIndex((i) => (i + 1) % PLACEHOLDERS.length),
      4200,
    )
    return () => window.clearInterval(id)
  }, [query])

  // Live catalogue statistics (spec 0J item 9). The number shown is always
  // whatever the index actually holds — never a hard-coded marketing claim.
  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE}/v1/catalog/stats`)
      .then((response) => (response.ok ? response.json() : null))
      .then((body) => {
        if (!cancelled && body) setCatalog(body as CatalogStats)
      })
      .catch(() => {
        /* the counter is decorative; its absence must not break the page */
      })
    return () => {
      cancelled = true
    }
  }, [])

  const placeholder = PLACEHOLDERS[placeholderIndex]

  const runSearch = useCallback(async (text: string) => {
    const trimmed = text.trim()
    if (!trimmed) return

    setBusy(true)
    setError(null)
    try {
      const response = await fetch(`${API_BASE}/v1/parse`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ query: trimmed }),
      })
      if (!response.ok) {
        throw new Error(`Search failed (${response.status})`)
      }
      setResult((await response.json()) as ParseResponse)
    } catch (cause) {
      // Never show a stack trace to a researcher (spec 0M).
      setError(
        cause instanceof Error && cause.message
          ? cause.message
          : 'Could not reach the search service. Please try again.',
      )
      setResult(null)
    } finally {
      setBusy(false)
    }
  }, [])

  const onSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    void runSearch(query)
  }

  const useExample = useCallback(
    (text: string) => {
      setMode('quick')
      setQuery(text)
      inputRef.current?.focus()
      void runSearch(text)
    },
    [runSearch],
  )

  const understood = result?.understanding
  const languageLabel = useMemo(
    () =>
      understood ? LANGUAGE_NAMES[understood.language] ?? understood.language : null,
    [understood],
  )

  return (
    <div className="home">
      <header className="home__bar">
        <a className="home__brand" href="/">
          <img src="/favicon.svg" width="28" height="28" alt="" aria-hidden="true" />
          <span>SmatEconData</span>
        </a>
        <div className="home__bar-actions">
          <a className="home__bar-link" href="/docs">
            {t('nav.docs')}
          </a>
          <a
            className="home__bar-link"
            href="https://github.com/merwanroudane/smaterecondata"
          >
            {t('nav.github')}
          </a>
          <button
            type="button"
            className="home__cart-button"
            aria-expanded={cartOpen}
            onClick={() => setCartOpen((open) => !open)}
          >
            {t('nav.cart')}
            {cartCount > 0 && (
              <span className="home__cart-count">{formatNumber(cartCount)}</span>
            )}
          </button>
          <LanguageSwitcher />
          <ThemeSwitcher />
        </div>
      </header>

      <main className="home__main">
        <section className="home__hero">
          <div className="home__hero-copy">
            <h1 className="home__title">{t('home.title')}</h1>
            <p className="home__subtitle">{t('home.subtitle')}</p>

            <form className="home__search" onSubmit={onSubmit} role="search">
              <label className="home__search-label" htmlFor="home-search">
                {t('home.search.label')}
              </label>
              <div className="home__search-field">
                <svg viewBox="0 0 24 24" width="19" height="19" aria-hidden="true">
                  <circle
                    cx="10.5"
                    cy="10.5"
                    r="6.5"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  />
                  <path
                    d="M15.5 15.5 L21 21"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                  />
                </svg>
                <input
                  id="home-search"
                  ref={inputRef}
                  className="home__search-input"
                  type="search"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder={placeholder.text}
                  dir={query ? 'auto' : placeholder.dir}
                  lang={query ? undefined : placeholder.lang}
                  autoComplete="off"
                  spellCheck={false}
                  enterKeyHint="search"
                />
                <button
                  type="submit"
                  className="home__search-button"
                  disabled={busy || !query.trim()}
                >
                  {busy ? t('home.search.busy') : t('home.search.button')}
                </button>
              </div>
              <p className="home__search-hint">{t('home.search.hint')}</p>
            </form>

            <div className="home__examples">
              <span className="home__examples-label">{t('home.examples.label')}</span>
              {EXAMPLES.map((example) => (
                <button
                  key={example}
                  type="button"
                  className="home__example"
                  dir="auto"
                  onClick={() => useExample(example)}
                >
                  {example}
                </button>
              ))}
            </div>
          </div>

          <HeroAnimation />
        </section>

        {catalog && (
          <section className="home__catalog" aria-label="Catalogue coverage">
            <span className="home__catalog-stat">
              <strong>{catalog.display_count}</strong>
            </span>
            <span className="home__catalog-stat">
              {formatNumber(catalog.provider_count)} official sources
            </span>
            <span className="home__catalog-stat">
              {formatNumber(catalog.country_or_area_count)} countries and areas
            </span>
            {catalog.last_catalog_sync && (
              <span className="home__catalog-stat home__catalog-stat--muted">
                synced {catalog.last_catalog_sync.slice(0, 10)}
              </span>
            )}
          </section>
        )}

        {/* Modes 2-8: progressive disclosure, never in the default path. */}
        <section className="home__modes">
          <div className="mode-tabs" role="tablist" aria-label={t('nav.search')}>
            {SEARCH_MODES.map((item) => (
              <button
                key={item.id}
                type="button"
                role="tab"
                className="mode-tab"
                aria-selected={mode === item.id}
                onClick={() => setMode(item.id)}
              >
                {t(item.labelKey)}
              </button>
            ))}
          </div>

          {mode === 'guided' && <GuidedSearch onQuery={useExample} />}
          {mode === 'advanced' && <AdvancedSearch onQuery={useExample} />}
          {mode === 'browse' && <BrowseCatalog onQuery={useExample} />}
          {mode === 'geography' && <GeographyFirst onQuery={useExample} />}
          {mode === 'source' && <SourceFirst onQuery={useExample} />}
          {mode === 'batch' && <BatchSearch />}
          {mode === 'code' && <ExactCodeSearch onQuery={useExample} />}
        </section>

        {error && (
          <section className="home__panel home__panel--error" role="alert">
            <strong>Search unavailable.</strong> {error}
          </section>
        )}

        {understood && (
          <section className="home__panel" aria-live="polite">
            <header className="home__panel-head">
              <h2>{t('understanding.title')}</h2>
              <span className="home__badge home__badge--lang">
                {t('understanding.queryLanguage', { lang: languageLabel ?? '' })}
              </span>
            </header>

            <dl className="home__understanding">
              <div>
                <dt>{t('understanding.countries')}</dt>
                <dd dir="auto">
                  {understood.countries.length
                    ? understood.countries.join(', ')
                    : t('understanding.notSpecified')}
                </dd>
              </div>
              <div>
                <dt>{t('understanding.indicators')}</dt>
                <dd dir="auto">
                  {understood.indicators.length
                    ? understood.indicators.join(', ')
                    : t('understanding.notSpecified')}
                </dd>
              </div>
              <div>
                <dt>{t('understanding.period')}</dt>
                <dd>{understood.period}</dd>
              </div>
              <div>
                <dt>{t('understanding.frequency')}</dt>
                <dd>{understood.frequency}</dd>
              </div>
              <div>
                <dt>{t('understanding.sources')}</dt>
                <dd>{understood.preferred_sources.join(', ')}</dd>
              </div>
              <div>
                <dt>{t('understanding.output')}</dt>
                <dd>{understood.output.join(', ')}</dd>
              </div>
            </dl>

            {understood.concept_keys.length > 0 && (
              <div className="home__resolved">
                {understood.concept_keys.map((concept, index) => (
                  <button
                    key={concept}
                    type="button"
                    className="home__resolved-add"
                    aria-pressed={has(concept)}
                    onClick={() =>
                      add({
                        concept,
                        label: understood.indicators[index] ?? concept,
                        ambiguous: understood.needs_clarification.includes(concept),
                      })
                    }
                  >
                    {has(concept) ? t('cart.added') : `${t('cart.add')}: ${concept}`}
                  </button>
                ))}
              </div>
            )}

            {result.ambiguous.length > 0 && (
              <div className="home__ambiguity">
                <h3>{t('ambiguity.title')}</h3>
                <p className="home__ambiguity-note">{t('ambiguity.note')}</p>
                <ul>
                  {result.ambiguous.map((item) => (
                    <li key={item.concept}>
                      <strong>{item.label}</strong>
                      <span className="home__distinctions">
                        {item.distinctions.map((distinction) => (
                          <span key={distinction} className="home__distinction">
                            {distinction}
                          </span>
                        ))}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        )}

        <section className="home__topics">
          <h2 className="home__section-title">{t('home.topics.title')}</h2>
          <div className="home__topic-grid">
            {TOPICS.map((topic) => (
              <button
                key={topic.key}
                type="button"
                className="home__topic"
                onClick={() => useExample(topic.hint)}
              >
                <strong>{topic.key}</strong>
                <small>{topic.hint}</small>
              </button>
            ))}
          </div>
        </section>
      </main>

      <DataCart
        open={cartOpen}
        onClose={() => setCartOpen(false)}
        onBuild={(payload) => {
          // The cart defines the request; the dataset layer builds it.
          const parts = [
            payload.concepts.join(', '),
            `for ${payload.geographies.join(', ')}`,
            payload.startYear && payload.endYear
              ? `from ${payload.startYear} to ${payload.endYear}`
              : '',
            `${payload.frequency} data`,
          ].filter(Boolean)
          setCartOpen(false)
          useExample(parts.join(' '))
        }}
      />

      <footer className="home__footer">
        <p>
          <strong>SmatEconData</strong> — {t('brand.tagline')}
        </p>
        <p>
          {t('brand.author')} ·{' '}
          <a href="https://github.com/merwanroudane/smaterecondata">
            github.com/merwanroudane/smaterecondata
          </a>
        </p>
        <p className="home__footer-note">{t('footer.note')}</p>
      </footer>
    </div>
  )
}
