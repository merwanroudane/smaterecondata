/**
 * The eight search modes (spec 0B).
 *
 * All of them sit on ONE discovery engine — the `/api/v1/parse`, `/concepts`
 * and `/geographies` endpoints. A mode is a different way to *express* a
 * request, never a different resolver, so a concept found by browsing behaves
 * identically to the same concept found by typing.
 *
 * Quick Search stays the default and the primary experience; every other mode
 * is progressive disclosure behind a tab.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useCart } from '../contexts/CartContext'
import { useI18n, type TranslationKey } from '../i18n'
import './SearchModes.css'

const API_BASE = import.meta.env.VITE_API_URL || '/api'

export type SearchMode =
  | 'quick'
  | 'guided'
  | 'advanced'
  | 'browse'
  | 'geography'
  | 'source'
  | 'batch'
  | 'code'

export const SEARCH_MODES: { id: SearchMode; labelKey: TranslationKey }[] = [
  { id: 'quick', labelKey: 'modes.quick' },
  { id: 'guided', labelKey: 'modes.guided' },
  { id: 'advanced', labelKey: 'modes.advanced' },
  { id: 'browse', labelKey: 'modes.browse' },
  { id: 'geography', labelKey: 'modes.geography' },
  { id: 'source', labelKey: 'modes.source' },
  { id: 'batch', labelKey: 'modes.batch' },
  { id: 'code', labelKey: 'modes.code' },
]

export interface Concept {
  key: string
  label: string
  display: string
  topic: string | null
  ambiguous: boolean
  distinctions: string[]
  aliases: Record<string, string[]>
}

export interface Country {
  iso3: string
  name: string
  /** Names in every supported language, so matching is UI-language agnostic. */
  aliases?: string[]
}

export interface Region {
  key: string
  members: string[]
}

const PROVIDERS = [
  { id: 'world_bank', label: 'World Bank' },
  { id: 'imf', label: 'IMF' },
  { id: 'fred', label: 'FRED' },
  { id: 'eurostat', label: 'Eurostat' },
  { id: 'oecd', label: 'OECD' },
  { id: 'bis', label: 'BIS' },
]

const FREQUENCIES = ['annual', 'quarterly', 'monthly']

/** Shared catalogue fetch — one request serves every mode that needs it. */
export function useCatalog() {
  const { language } = useI18n()
  const [concepts, setConcepts] = useState<Concept[]>([])
  const [countries, setCountries] = useState<Country[]>([])
  const [regions, setRegions] = useState<Region[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)

    Promise.all([
      fetch(`${API_BASE}/v1/concepts?language=${language}`).then((r) => r.json()),
      fetch(`${API_BASE}/v1/geographies?language=${language}`).then((r) => r.json()),
    ])
      .then(([conceptBody, geoBody]) => {
        if (cancelled) return
        setConcepts(conceptBody.concepts ?? [])
        setCountries(geoBody.countries ?? [])
        setRegions(geoBody.regions ?? [])
        setError(null)
      })
      .catch(() => {
        if (!cancelled) setError('Could not load the catalogue.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [language])

  return { concepts, countries, regions, error, loading }
}


export interface CatalogHit {
  provider: string
  series_id: string
  title: string
  description: string | null
  unit: string | null
  frequency: string
  topic: string | null
  source_reference: string | null
  score: number
  matched_on: string[]
}

/**
 * Search the real provider catalogue (tens of thousands of series), as opposed
 * to `useCatalog`, which serves the curated multilingual concept list.
 */
export function useCatalogSearch() {
  const [hits, setHits] = useState<CatalogHit[]>([])
  const [size, setSize] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)

  const run = useCallback(
    async (query: string, options: { provider?: string; limit?: number } = {}) => {
      if (!query.trim()) {
        setHits([])
        return
      }
      setBusy(true)
      try {
        const params = new URLSearchParams({
          q: query,
          limit: String(options.limit ?? 20),
        })
        if (options.provider) params.set('provider', options.provider)
        const response = await fetch(`${API_BASE}/v1/indicators/search?${params}`)
        if (!response.ok) throw new Error(String(response.status))
        const body = await response.json()
        setHits(body.results ?? [])
        setSize(body.catalog_size ?? null)
      } catch {
        setHits([])
      } finally {
        setBusy(false)
      }
    },
    [],
  )

  return { hits, size, busy, run }
}

interface ModeProps {
  onQuery: (query: string) => void
}

/* ------------------------------------------------------------------ *
 * Mode 2 — Guided Search Builder
 * What data? -> Where? -> When? -> Frequency -> Source -> Review
 * Every step is searchable and skippable (spec 0B mode 2).
 * ------------------------------------------------------------------ */

const GUIDED_STEPS: TranslationKey[] = [
  'guided.what',
  'guided.where',
  'guided.when',
  'guided.frequency',
  'guided.source',
  'guided.review',
]

export function GuidedSearch({ onQuery }: ModeProps) {
  const { t } = useI18n()
  const { concepts, countries } = useCatalog()
  const [step, setStep] = useState(0)
  const [picked, setPicked] = useState<string[]>([])
  const [geos, setGeos] = useState<string[]>([])
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [frequency, setFrequency] = useState('annual')
  const [provider, setProvider] = useState('')
  const [filter, setFilter] = useState('')

  const toggle = (list: string[], value: string, set: (v: string[]) => void) =>
    set(list.includes(value) ? list.filter((v) => v !== value) : [...list, value])

  const built = useMemo(() => {
    const parts: string[] = []
    if (picked.length) {
      parts.push(
        picked
          .map((k) => concepts.find((c) => c.key === k)?.label ?? k)
          .join(', '),
      )
    }
    if (geos.length) parts.push(`for ${geos.join(', ')}`)
    if (start && end) parts.push(`from ${start} to ${end}`)
    else if (start) parts.push(`since ${start}`)
    if (frequency) parts.push(`${frequency} data`)
    if (provider) parts.push(`from ${PROVIDERS.find((p) => p.id === provider)?.label}`)
    return parts.join(' ')
  }, [picked, geos, start, end, frequency, provider, concepts])

  const visibleConcepts = concepts.filter(
    (c) =>
      !filter ||
      c.label.toLowerCase().includes(filter.toLowerCase()) ||
      c.display.includes(filter),
  )
  const visibleCountries = countries.filter(
    (c) => !filter || c.name.toLowerCase().includes(filter.toLowerCase()) ||
      c.iso3.toLowerCase().includes(filter.toLowerCase()),
  )

  return (
    <div className="mode mode--guided">
      <ol className="mode__steps">
        {GUIDED_STEPS.map((key, index) => (
          <li key={key}>
            <button
              type="button"
              className="mode__step"
              aria-current={index === step ? 'step' : undefined}
              onClick={() => setStep(index)}
            >
              <span className="mode__step-num">{index + 1}</span>
              {t(key)}
            </button>
          </li>
        ))}
      </ol>

      <div className="mode__body">
        {step === 0 && (
          <>
            <input
              className="mode__filter"
              placeholder={t('guided.what')}
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              dir="auto"
            />
            <div className="mode__chips">
              {visibleConcepts.map((concept) => (
                <button
                  key={concept.key}
                  type="button"
                  className="mode__chip"
                  aria-pressed={picked.includes(concept.key)}
                  onClick={() => toggle(picked, concept.key, setPicked)}
                >
                  {concept.display}
                </button>
              ))}
            </div>
          </>
        )}

        {step === 1 && (
          <>
            <input
              className="mode__filter"
              placeholder={t('guided.where')}
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              dir="auto"
            />
            <div className="mode__chips">
              {visibleCountries.slice(0, 60).map((country) => (
                <button
                  key={country.iso3}
                  type="button"
                  className="mode__chip"
                  aria-pressed={geos.includes(country.iso3)}
                  onClick={() => toggle(geos, country.iso3, setGeos)}
                >
                  {country.name}
                </button>
              ))}
            </div>
          </>
        )}

        {step === 2 && (
          <div className="mode__grid">
            <label>
              {t('advanced.startYear')}
              <input
                type="number"
                min={1900}
                max={2100}
                value={start}
                onChange={(e) => setStart(e.target.value)}
              />
            </label>
            <label>
              {t('advanced.endYear')}
              <input
                type="number"
                min={1900}
                max={2100}
                value={end}
                onChange={(e) => setEnd(e.target.value)}
              />
            </label>
          </div>
        )}

        {step === 3 && (
          <div className="mode__chips">
            {FREQUENCIES.map((f) => (
              <button
                key={f}
                type="button"
                className="mode__chip"
                aria-pressed={frequency === f}
                onClick={() => setFrequency(f)}
              >
                {f}
              </button>
            ))}
          </div>
        )}

        {step === 4 && (
          <div className="mode__chips">
            <button
              type="button"
              className="mode__chip"
              aria-pressed={provider === ''}
              onClick={() => setProvider('')}
            >
              automatic
            </button>
            {PROVIDERS.map((p) => (
              <button
                key={p.id}
                type="button"
                className="mode__chip"
                aria-pressed={provider === p.id}
                onClick={() => setProvider(p.id)}
              >
                {p.label}
              </button>
            ))}
          </div>
        )}

        {step === 5 && (
          <div className="mode__review">
            <p className="mode__review-query" dir="auto">
              {built || t('understanding.notSpecified')}
            </p>
          </div>
        )}
      </div>

      <div className="mode__actions">
        <button
          type="button"
          className="mode__button"
          disabled={step === 0}
          onClick={() => {
            setFilter('')
            setStep((s) => Math.max(0, s - 1))
          }}
        >
          {t('guided.back')}
        </button>
        {step < GUIDED_STEPS.length - 1 ? (
          <>
            <button
              type="button"
              className="mode__button mode__button--ghost"
              onClick={() => {
                setFilter('')
                setStep((s) => s + 1)
              }}
            >
              {t('guided.skip')}
            </button>
            <button
              type="button"
              className="mode__button mode__button--primary"
              onClick={() => {
                setFilter('')
                setStep((s) => s + 1)
              }}
            >
              {t('guided.next')}
            </button>
          </>
        ) : (
          <button
            type="button"
            className="mode__button mode__button--primary"
            disabled={!built}
            onClick={() => onQuery(built)}
          >
            {t('guided.run')}
          </button>
        )}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ *
 * Mode 3 — Advanced Search (expert filters, progressive disclosure)
 * ------------------------------------------------------------------ */

export function AdvancedSearch({ onQuery }: ModeProps) {
  const { t, formatNumber } = useI18n()
  const { concepts, countries } = useCatalog()
  const { hits, size, busy, run } = useCatalogSearch()
  const { add, has } = useCart()

  const [concept, setConcept] = useState('')
  const [code, setCode] = useState('')
  const [provider, setProvider] = useState('')
  const [topic, setTopic] = useState('')
  const [geos, setGeos] = useState<string[]>([])
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [frequency, setFrequency] = useState('annual')
  const [searched, setSearched] = useState(false)

  const topics = useMemo(
    () => [...new Set(concepts.map((c) => c.topic).filter(Boolean))] as string[],
    [concepts],
  )

  const reset = () => {
    setConcept('')
    setCode('')
    setProvider('')
    setTopic('')
    setGeos([])
    setStart('')
    setEnd('')
    setFrequency('annual')
    setSearched(false)
  }

  /**
   * Advanced search hits the catalogue endpoint with STRUCTURED filters
   * rather than composing a sentence for the natural-language parser — that
   * is the whole point of the mode (spec 0B mode 3).
   */
  const apply = () => {
    const term =
      code.trim() || concepts.find((c) => c.key === concept)?.label || topic || ''
    if (!term) return
    setSearched(true)
    void run(term, { provider: provider || undefined, limit: 40 })
  }

  /** Hand a chosen series to the retrieval flow with the period applied. */
  const retrieve = (title: string) => {
    const parts = [title]
    if (geos.length) parts.push(`for ${geos.join(', ')}`)
    if (start && end) parts.push(`from ${start} to ${end}`)
    if (frequency) parts.push(`${frequency} data`)
    onQuery(parts.join(' '))
  }

  return (
    <div className="mode mode--advanced">
      <div className="mode__grid">
        <label>
          {t('advanced.topic')}
          <select value={concept} onChange={(e) => setConcept(e.target.value)}>
            <option value="">—</option>
            {concepts.map((c) => (
              <option key={c.key} value={c.key}>
                {c.display}
              </option>
            ))}
          </select>
        </label>

        <label>
          {t('advanced.code')}
          <input
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="NY.GDP.PCAP.CD"
            spellCheck={false}
          />
        </label>

        <label>
          {t('advanced.provider')}
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="">automatic</option>
            {PROVIDERS.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </label>

        <label>
          Category
          <select value={topic} onChange={(e) => setTopic(e.target.value)}>
            <option value="">—</option>
            {topics.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>

        <label>
          {t('advanced.frequency')}
          <select value={frequency} onChange={(e) => setFrequency(e.target.value)}>
            {FREQUENCIES.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </label>

        <label>
          {t('advanced.startYear')}
          <input
            type="number"
            min={1900}
            max={2100}
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </label>

        <label>
          {t('advanced.endYear')}
          <input
            type="number"
            min={1900}
            max={2100}
            value={end}
            onChange={(e) => setEnd(e.target.value)}
          />
        </label>
      </div>

      <fieldset className="mode__fieldset">
        <legend>{t('guided.where')}</legend>
        <div className="mode__chips mode__chips--scroll">
          {countries.slice(0, 60).map((country) => (
            <button
              key={country.iso3}
              type="button"
              className="mode__chip"
              aria-pressed={geos.includes(country.iso3)}
              onClick={() =>
                setGeos((g) =>
                  g.includes(country.iso3)
                    ? g.filter((v) => v !== country.iso3)
                    : [...g, country.iso3],
                )
              }
            >
              {country.name}
            </button>
          ))}
        </div>
      </fieldset>

      <div className="mode__actions">
        <button type="button" className="mode__button" onClick={reset}>
          {t('advanced.reset')}
        </button>
        <button
          type="button"
          className="mode__button mode__button--primary"
          onClick={apply}
          disabled={busy}
        >
          {busy ? t('common.loading') : t('advanced.apply')}
        </button>
      </div>

      {searched && !busy && hits.length === 0 && (
        <p className="mode__note">
          {t('common.noResults')} — {t('common.noResultsHint')}
        </p>
      )}

      {hits.length > 0 && (
        <>
          <ul className="mode__list">
            {hits.map((hit) => (
              <li key={`${hit.provider}:${hit.series_id}`}>
                <button
                  type="button"
                  className="mode__list-main"
                  onClick={() => retrieve(hit.title)}
                >
                  <span className="mode__list-title">{hit.title}</span>
                  <span className="mode__list-sub">
                    {hit.provider} · {hit.series_id}
                    {hit.unit ? ` · ${hit.unit}` : ''}
                    {hit.topic ? ` · ${hit.topic}` : ''}
                  </span>
                </button>
                <button
                  type="button"
                  className="mode__add"
                  aria-pressed={has(hit.series_id)}
                  onClick={() =>
                    add({
                      concept: hit.series_id,
                      label: hit.title,
                      provider: hit.provider,
                      seriesId: hit.series_id,
                      unit: hit.unit ?? undefined,
                      topic: hit.topic ?? undefined,
                    })
                  }
                >
                  {has(hit.series_id) ? t('cart.added') : t('cart.add')}
                </button>
              </li>
            ))}
          </ul>
          {size !== null && (
            <p className="mode__note">
              {formatNumber(hits.length)} of {formatNumber(size)} indexed series.
            </p>
          )}
        </>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ *
 * Mode 4 — Browse Catalog (no typing required)
 * ------------------------------------------------------------------ */

export function BrowseCatalog({ onQuery }: ModeProps) {
  const { concepts, loading } = useCatalog()
  const { t } = useI18n()
  const { add, has } = useCart()

  const byTopic = useMemo(() => {
    const groups = new Map<string, Concept[]>()
    for (const concept of concepts) {
      const topic = concept.topic ?? 'Other'
      if (!groups.has(topic)) groups.set(topic, [])
      groups.get(topic)!.push(concept)
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b))
  }, [concepts])

  if (loading) return <p className="mode__note">{t('common.loading')}</p>

  return (
    <div className="mode mode--browse">
      {byTopic.map(([topic, items]) => (
        <section key={topic} className="mode__topic">
          <h3>{topic}</h3>
          <ul className="mode__list">
            {items.map((concept) => (
              <li key={concept.key}>
                <button
                  type="button"
                  className="mode__list-main"
                  onClick={() => onQuery(concept.label)}
                >
                  <span className="mode__list-title">{concept.display}</span>
                  {concept.ambiguous && (
                    <span className="mode__flag" title={concept.distinctions.join(' · ')}>
                      {concept.distinctions.length} definitions
                    </span>
                  )}
                </button>
                <button
                  type="button"
                  className="mode__add"
                  aria-pressed={has(concept.key)}
                  onClick={() =>
                    add({
                      concept: concept.key,
                      label: concept.display,
                      topic: concept.topic ?? undefined,
                      ambiguous: concept.ambiguous,
                    })
                  }
                >
                  {has(concept.key) ? t('cart.added') : t('cart.add')}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}

/* ------------------------------------------------------------------ *
 * Mode 5 — Geography-first
 * ------------------------------------------------------------------ */

export function GeographyFirst({ onQuery }: ModeProps) {
  const { countries, regions, loading } = useCatalog()
  const { t } = useI18n()
  const [selected, setSelected] = useState<string[]>([])
  const [filter, setFilter] = useState('')

  const visible = countries.filter(
    (c) =>
      !filter ||
      c.name.toLowerCase().includes(filter.toLowerCase()) ||
      c.iso3.toLowerCase().includes(filter.toLowerCase()),
  )

  if (loading) return <p className="mode__note">{t('common.loading')}</p>

  return (
    <div className="mode mode--geography">
      <div className="mode__regions">
        {regions.map((region) => (
          <button
            key={region.key}
            type="button"
            className="mode__chip mode__chip--region"
            onClick={() => setSelected(region.members)}
          >
            {region.key.replace(/_/g, ' ')}
            <small>{region.members.length}</small>
          </button>
        ))}
      </div>

      <input
        className="mode__filter"
        placeholder={t('guided.where')}
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        dir="auto"
      />

      <div className="mode__chips mode__chips--scroll">
        {visible.map((country) => (
          <button
            key={country.iso3}
            type="button"
            className="mode__chip"
            aria-pressed={selected.includes(country.iso3)}
            onClick={() =>
              setSelected((s) =>
                s.includes(country.iso3)
                  ? s.filter((v) => v !== country.iso3)
                  : [...s, country.iso3],
              )
            }
          >
            {country.name}
          </button>
        ))}
      </div>

      <div className="mode__actions">
        <button
          type="button"
          className="mode__button"
          onClick={() => setSelected([])}
          disabled={!selected.length}
        >
          {t('advanced.reset')}
        </button>
        <button
          type="button"
          className="mode__button mode__button--primary"
          disabled={!selected.length}
          onClick={() => onQuery(`GDP per capita and inflation for ${selected.join(', ')}`)}
        >
          {t('guided.run')} ({selected.length})
        </button>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ *
 * Mode 6 — Source-first
 * ------------------------------------------------------------------ */

export function SourceFirst({ onQuery }: ModeProps) {
  const { t, formatNumber } = useI18n()
  const { hits, size, busy, run } = useCatalogSearch()
  const { add, has } = useCart()
  const [provider, setProvider] = useState(PROVIDERS[0].id)
  const [term, setTerm] = useState('')

  // Search within one provider's real catalogue (spec 0B mode 6).
  useEffect(() => {
    void run(term || 'gdp inflation unemployment', { provider, limit: 25 })
  }, [provider, term, run])

  return (
    <div className="mode mode--source">
      <div className="mode__chips">
        {PROVIDERS.map((p) => (
          <button
            key={p.id}
            type="button"
            className="mode__chip"
            aria-pressed={provider === p.id}
            onClick={() => setProvider(p.id)}
          >
            {p.label}
          </button>
        ))}
      </div>

      <input
        className="mode__filter"
        placeholder={t('home.search.label')}
        value={term}
        onChange={(event) => setTerm(event.target.value)}
        dir="auto"
      />

      {busy && <p className="mode__note">{t('common.loading')}</p>}

      {!busy && hits.length === 0 && (
        <p className="mode__note">
          {t('common.noResults')} — {t('common.noResultsHint')}
        </p>
      )}

      <ul className="mode__list">
        {hits.map((hit) => (
          <li key={`${hit.provider}:${hit.series_id}`}>
            <button
              type="button"
              className="mode__list-main"
              onClick={() => onQuery(hit.title)}
            >
              <span className="mode__list-title">{hit.title}</span>
              <span className="mode__list-sub">
                {hit.provider} · {hit.series_id}
                {hit.unit ? ` · ${hit.unit}` : ''}
              </span>
            </button>
            <button
              type="button"
              className="mode__add"
              aria-pressed={has(hit.series_id)}
              onClick={() =>
                add({
                  concept: hit.series_id,
                  label: hit.title,
                  provider: hit.provider,
                  seriesId: hit.series_id,
                  unit: hit.unit ?? undefined,
                  topic: hit.topic ?? undefined,
                })
              }
            >
              {has(hit.series_id) ? t('cart.added') : t('cart.add')}
            </button>
          </li>
        ))}
      </ul>

      {size !== null && (
        <p className="mode__note">Searching {formatNumber(size)} indexed series.</p>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ *
 * Mode 7 — Batch search
 * ------------------------------------------------------------------ */

interface BatchRow {
  input: string
  concepts: string[]
  countries: string[]
  resolved: boolean
}

export function BatchSearch() {
  const { t } = useI18n()
  const { add, has } = useCart()
  const [text, setText] = useState('')
  const [rows, setRows] = useState<BatchRow[]>([])
  const [busy, setBusy] = useState(false)

  const resolve = useCallback(async () => {
    const lines = text
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
    if (!lines.length) return

    setBusy(true)
    try {
      // One request per line, in parallel. The parse endpoint is deterministic
      // and cheap, so a 20-line paste resolves in one round trip's latency.
      const results = await Promise.all(
        lines.map(async (line) => {
          try {
            const response = await fetch(`${API_BASE}/v1/parse`, {
              method: 'POST',
              headers: { 'content-type': 'application/json' },
              body: JSON.stringify({ query: line }),
            })
            if (!response.ok) throw new Error(String(response.status))
            const body = await response.json()
            const u = body.understanding
            return {
              input: line,
              concepts: u.concept_keys ?? [],
              countries: u.iso3 ?? [],
              resolved: (u.concept_keys ?? []).length > 0,
            }
          } catch {
            return { input: line, concepts: [], countries: [], resolved: false }
          }
        }),
      )
      setRows(results)
    } finally {
      setBusy(false)
    }
  }, [text])

  return (
    <div className="mode mode--batch">
      <label className="mode__label" htmlFor="batch-input">
        {t('batch.label')}
      </label>
      <textarea
        id="batch-input"
        className="mode__textarea"
        rows={7}
        value={text}
        onChange={(e) => setText(e.target.value)}
        dir="auto"
        placeholder={'GDP per capita\ninflation\nunemployment\nFDI inflows\ngovernment debt'}
      />
      <div className="mode__actions">
        <button
          type="button"
          className="mode__button mode__button--primary"
          onClick={() => void resolve()}
          disabled={busy || !text.trim()}
        >
          {busy ? t('common.loading') : t('batch.resolve')}
        </button>
      </div>

      {rows.length > 0 && (
        <table className="mode__table">
          <thead>
            <tr>
              <th>{t('batch.label')}</th>
              <th>{t('understanding.indicators')}</th>
              <th>{t('understanding.countries')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.input}>
                <td dir="auto">{row.input}</td>
                <td>
                  {row.resolved ? (
                    row.concepts.join(', ')
                  ) : (
                    <span className="mode__flag mode__flag--warn">
                      {t('batch.unresolved')}
                    </span>
                  )}
                </td>
                <td>{row.countries.join(', ') || '—'}</td>
                <td>
                  {row.resolved && (
                    <button
                      type="button"
                      className="mode__add"
                      aria-pressed={has(row.concepts[0])}
                      onClick={() =>
                        add({ concept: row.concepts[0], label: row.concepts[0] })
                      }
                    >
                      {has(row.concepts[0]) ? t('cart.added') : t('cart.add')}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ *
 * Mode 8 — Exact code search
 * ------------------------------------------------------------------ */

export function ExactCodeSearch({ onQuery }: ModeProps) {
  const { t } = useI18n()
  const [provider, setProvider] = useState(PROVIDERS[0].id)
  const [code, setCode] = useState('')

  return (
    <div className="mode mode--code">
      <div className="mode__grid">
        <label>
          {t('advanced.provider')}
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            {PROVIDERS.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t('advanced.code')}
          <input
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="NY.GDP.PCAP.CD / PCPIPCH / GDPC1"
            spellCheck={false}
            autoComplete="off"
          />
        </label>
      </div>
      <div className="mode__actions">
        <button
          type="button"
          className="mode__button mode__button--primary"
          disabled={!code.trim()}
          onClick={() =>
            onQuery(`${code.trim()} from ${PROVIDERS.find((p) => p.id === provider)?.label}`)
          }
        >
          {t('guided.run')}
        </button>
      </div>
    </div>
  )
}
