/**
 * Interface language switcher (spec 0O).
 *
 * Deliberately separate from the theme switcher: the spec requires the
 * language control to be independent of the theme control, and the interface
 * language to be independent of the search language. Switching the UI to
 * French does not stop you typing an Arabic query.
 */

import { useI18n } from '../i18n'
import './LanguageSwitcher.css'

export function LanguageSwitcher() {
  const { language, setLanguage, languages, t } = useI18n()

  return (
    <div
      className="lang-switcher"
      role="group"
      aria-label={t('common.language')}
    >
      {languages.map((item) => (
        <button
          key={item.id}
          type="button"
          className="lang-switcher__option"
          lang={item.id}
          aria-pressed={language === item.id}
          title={item.label}
          onClick={() => setLanguage(item.id)}
        >
          {item.id.toUpperCase()}
        </button>
      ))}
    </div>
  )
}
