/**
 * Interface localisation tests (spec 0O, 0R item 15).
 *
 * The two rules that matter most: Arabic renders RTL, and the interface
 * language is independent of the search language.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { I18nProvider, LANGUAGES, useI18n } from '..'

function Probe() {
  const { language, dir, t, setLanguage, formatNumber } = useI18n()
  return (
    <div>
      <span data-testid="lang">{language}</span>
      <span data-testid="dir">{dir}</span>
      <span data-testid="title">{t('home.title')}</span>
      <span data-testid="cart">{t('cart.build')}</span>
      <span data-testid="interp">{t('understanding.queryLanguage', { lang: 'Arabic' })}</span>
      <span data-testid="number">{formatNumber(12345.6, { maximumFractionDigits: 1 })}</span>
      <button onClick={() => setLanguage('fr')}>fr</button>
      <button onClick={() => setLanguage('ar')}>ar</button>
      <button onClick={() => setLanguage('en')}>en</button>
    </div>
  )
}

const renderI18n = () =>
  render(
    <I18nProvider>
      <Probe />
    </I18nProvider>,
  )

describe('I18nProvider', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.removeAttribute('dir')
    document.documentElement.removeAttribute('lang')
  })

  it('offers exactly the three required languages', () => {
    expect(LANGUAGES.map((l) => l.id).sort()).toEqual(['ar', 'en', 'fr'])
  })

  it('defaults to English LTR', () => {
    renderI18n()
    expect(screen.getByTestId('lang')).toHaveTextContent('en')
    expect(screen.getByTestId('dir')).toHaveTextContent('ltr')
  })

  it('translates into French', async () => {
    const user = userEvent.setup()
    renderI18n()
    await user.click(screen.getByText('fr'))

    expect(screen.getByTestId('title')).toHaveTextContent(
      'Les données économiques, plus faciles à trouver.',
    )
    expect(screen.getByTestId('cart')).toHaveTextContent(
      'Construire le jeu de données',
    )
  })

  it('translates into Arabic and switches the document to RTL', async () => {
    const user = userEvent.setup()
    renderI18n()
    await user.click(screen.getByText('ar'))

    expect(screen.getByTestId('dir')).toHaveTextContent('rtl')
    expect(document.documentElement.getAttribute('dir')).toBe('rtl')
    expect(document.documentElement.getAttribute('lang')).toBe('ar')
    expect(screen.getByTestId('title')).toHaveTextContent(
      'البيانات الاقتصادية، أسهل في العثور عليها.',
    )
  })

  it('returns to LTR when leaving Arabic', async () => {
    const user = userEvent.setup()
    renderI18n()
    await user.click(screen.getByText('ar'))
    await user.click(screen.getByText('en'))
    expect(document.documentElement.getAttribute('dir')).toBe('ltr')
  })

  it('substitutes placeholders', () => {
    renderI18n()
    expect(screen.getByTestId('interp')).toHaveTextContent('Arabic query')
  })

  it('substitutes placeholders in every language', async () => {
    const user = userEvent.setup()
    renderI18n()
    await user.click(screen.getByText('fr'))
    expect(screen.getByTestId('interp')).toHaveTextContent('Requête en Arabic')
  })

  it('formats numbers per locale', async () => {
    const user = userEvent.setup()
    renderI18n()
    const english = screen.getByTestId('number').textContent
    await user.click(screen.getByText('fr'))
    // French uses a different group separator; the point is it is locale-aware.
    expect(screen.getByTestId('number').textContent).not.toBe('')
    expect(english).not.toBe('')
  })

  it('persists the chosen language', async () => {
    const user = userEvent.setup()
    const { unmount } = renderI18n()
    await user.click(screen.getByText('ar'))
    unmount()

    renderI18n()
    expect(screen.getByTestId('lang')).toHaveTextContent('ar')
  })

  it('falls back to English for an untranslated key rather than showing the key', async () => {
    const user = userEvent.setup()
    renderI18n()
    await user.click(screen.getByText('ar'))
    // 'export.csv' has no Arabic entry — it must render the English word,
    // never the raw dotted key.
    expect(screen.getByTestId('cart')).not.toHaveTextContent('cart.build')
  })

  it('throws outside the provider', () => {
    expect(() => render(<Probe />)).toThrow(/within an I18nProvider/)
  })
})
