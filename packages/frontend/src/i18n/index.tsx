/**
 * Interface localisation for SmatEconData (spec 0O).
 *
 * Arabic, English and French are all first-class. Two rules the spec is
 * explicit about, and both are enforced here:
 *
 *   1. The INTERFACE language is independent of the SEARCH language. Changing
 *      the UI to French must not stop you typing an Arabic query, and vice
 *      versa. Nothing in this module touches the query.
 *   2. Arabic renders RTL — the `dir` attribute is set on <html>, so layout
 *      mirrors via CSS logical properties rather than per-component overrides.
 *
 * Data is never translated. Provider titles, units and definitions always
 * render in the language the provider published them in.
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

export type UiLanguage = 'en' | 'fr' | 'ar'

const STORAGE_KEY = 'smatecondata_ui_language'

export const LANGUAGES: { id: UiLanguage; label: string; native: string; dir: 'ltr' | 'rtl' }[] = [
  { id: 'en', label: 'English', native: 'English', dir: 'ltr' },
  { id: 'fr', label: 'French', native: 'Français', dir: 'ltr' },
  { id: 'ar', label: 'Arabic', native: 'العربية', dir: 'rtl' },
]

/**
 * Translation keys. English is the source of truth; a missing key in another
 * language falls back to English rather than rendering the raw key.
 */
const EN = {
  'brand.tagline': 'Economic data, easier to find.',
  'brand.author': 'Dr Merwan Roudane',

  'nav.docs': 'Docs',
  'nav.github': 'GitHub',
  'nav.home': 'Home',
  'nav.search': 'Search',
  'nav.browse': 'Browse',
  'nav.cart': 'Data Cart',
  'nav.workspace': 'Workspace',
  'nav.sources': 'Sources',

  'home.title': 'Economic data, easier to find.',
  'home.subtitle':
    'Describe the data you need in Arabic, English or French. Get a documented, refreshable dataset with full source provenance — not just a chart.',
  'home.search.label': 'Search economic data',
  'home.search.button': 'Search',
  'home.search.busy': 'Searching…',
  'home.search.hint':
    'No indicator codes, no provider selection. Advanced filters stay out of the way until you want them.',
  'home.examples.label': 'Try:',
  'home.topics.title': 'Browse by topic',

  'understanding.title': 'What I understood',
  'understanding.countries': 'Countries',
  'understanding.indicators': 'Indicators',
  'understanding.period': 'Period',
  'understanding.frequency': 'Frequency',
  'understanding.sources': 'Sources',
  'understanding.output': 'Output',
  'understanding.notSpecified': 'Not specified',
  'understanding.queryLanguage': '{lang} query',

  'ambiguity.title': 'Which definition do you mean?',
  'ambiguity.note':
    'These terms have several materially different definitions. SmatEconData will not pick one silently.',

  'modes.quick': 'Quick search',
  'modes.guided': 'Guided',
  'modes.advanced': 'Advanced',
  'modes.browse': 'Browse catalog',
  'modes.geography': 'By country',
  'modes.source': 'By source',
  'modes.batch': 'Batch',
  'modes.code': 'Exact code',

  'guided.what': 'What data?',
  'guided.where': 'Where?',
  'guided.when': 'When?',
  'guided.frequency': 'Frequency',
  'guided.source': 'Preferred source',
  'guided.review': 'Review',
  'guided.next': 'Next',
  'guided.back': 'Back',
  'guided.skip': 'Skip',
  'guided.run': 'Retrieve',

  'advanced.provider': 'Provider',
  'advanced.code': 'Indicator code',
  'advanced.topic': 'Topic',
  'advanced.startYear': 'Start year',
  'advanced.endYear': 'End year',
  'advanced.frequency': 'Frequency',
  'advanced.shape': 'Output shape',
  'advanced.apply': 'Apply filters',
  'advanced.reset': 'Reset',

  'batch.label': 'Paste one indicator per line',
  'batch.resolve': 'Resolve all',
  'batch.resolved': 'Resolved',
  'batch.unresolved': 'Not recognised',

  'cart.title': 'Data Cart',
  'cart.empty': 'Your cart is empty.',
  'cart.emptyHint': 'Search for an indicator and add it to build a dataset.',
  'cart.add': 'Add to cart',
  'cart.added': 'In cart',
  'cart.remove': 'Remove',
  'cart.clear': 'Clear cart',
  'cart.build': 'Build Dataset',
  'cart.items': '{count} indicator(s)',
  'cart.countries': 'Countries for all series',
  'cart.period': 'Period for all series',
  'cart.shape': 'Output shape',
  'cart.bulk': 'Bulk actions',
  'cart.duplicateWarning': 'This concept is already in the cart.',

  'workspace.data': 'Data',
  'workspace.metadata': 'Metadata',
  'workspace.stats': 'Descriptive statistics',
  'workspace.profiling': 'Data profiling',
  'workspace.missing': 'Missing data',
  'workspace.charts': 'Charts',
  'workspace.sources': 'Sources & provenance',
  'workspace.export': 'Export',

  'quality.clean': 'Clean',
  'quality.warnings': 'Usable with warnings',
  'quality.unusable': 'Unusable',
  'quality.coverage': 'Coverage',
  'quality.rows': 'Rows',
  'quality.variables': 'Variables',

  'export.title': 'Export Center',
  'export.excel': 'Excel workbook',
  'export.csv': 'CSV',
  'export.parquet': 'Parquet',
  'export.json': 'JSON',
  'export.html': 'HTML report',
  'export.bundle': 'Research bundle (ZIP)',
  'export.recipe': 'Dataset recipe',

  'common.loading': 'Loading…',
  'common.error': 'Something went wrong.',
  'common.retry': 'Try again',
  'common.close': 'Close',
  'common.language': 'Language',
  'common.noResults': 'No exact match',
  'common.noResultsHint':
    'Here are the closest available indicators. You can widen the period or change source.',

  'footer.note':
    'Values come from official providers and are always cited. Nothing is interpolated, imputed, or generated by a language model.',
} as const

export type TranslationKey = keyof typeof EN

const FR: Partial<Record<TranslationKey, string>> = {
  'brand.tagline': 'Les données économiques, plus faciles à trouver.',
  'nav.docs': 'Documentation',
  'nav.home': 'Accueil',
  'nav.search': 'Recherche',
  'nav.browse': 'Parcourir',
  'nav.cart': 'Panier de données',
  'nav.workspace': 'Espace de travail',
  'nav.sources': 'Sources',

  'home.title': 'Les données économiques, plus faciles à trouver.',
  'home.subtitle':
    "Décrivez les données dont vous avez besoin en arabe, en anglais ou en français. Obtenez un jeu de données documenté et actualisable, avec la provenance complète des sources.",
  'home.search.label': 'Rechercher des données économiques',
  'home.search.button': 'Rechercher',
  'home.search.busy': 'Recherche…',
  'home.search.hint':
    "Pas de codes d'indicateurs, pas de choix de fournisseur. Les filtres avancés restent discrets.",
  'home.examples.label': 'Essayez :',
  'home.topics.title': 'Parcourir par thème',

  'understanding.title': "Ce que j'ai compris",
  'understanding.countries': 'Pays',
  'understanding.indicators': 'Indicateurs',
  'understanding.period': 'Période',
  'understanding.frequency': 'Fréquence',
  'understanding.sources': 'Sources',
  'understanding.output': 'Sortie',
  'understanding.notSpecified': 'Non précisé',
  'understanding.queryLanguage': 'Requête en {lang}',

  'ambiguity.title': 'Quelle définition voulez-vous ?',
  'ambiguity.note':
    "Ces termes ont plusieurs définitions sensiblement différentes. SmatEconData n'en choisira pas une en silence.",

  'modes.quick': 'Recherche rapide',
  'modes.guided': 'Guidée',
  'modes.advanced': 'Avancée',
  'modes.browse': 'Catalogue',
  'modes.geography': 'Par pays',
  'modes.source': 'Par source',
  'modes.batch': 'Par lot',
  'modes.code': 'Code exact',

  'guided.what': 'Quelles données ?',
  'guided.where': 'Où ?',
  'guided.when': 'Quand ?',
  'guided.frequency': 'Fréquence',
  'guided.source': 'Source préférée',
  'guided.review': 'Vérifier',
  'guided.next': 'Suivant',
  'guided.back': 'Retour',
  'guided.skip': 'Passer',
  'guided.run': 'Récupérer',

  'advanced.provider': 'Fournisseur',
  'advanced.code': "Code d'indicateur",
  'advanced.topic': 'Thème',
  'advanced.startYear': 'Année de début',
  'advanced.endYear': 'Année de fin',
  'advanced.frequency': 'Fréquence',
  'advanced.shape': 'Format de sortie',
  'advanced.apply': 'Appliquer',
  'advanced.reset': 'Réinitialiser',

  'batch.label': 'Un indicateur par ligne',
  'batch.resolve': 'Tout résoudre',
  'batch.resolved': 'Résolu',
  'batch.unresolved': 'Non reconnu',

  'cart.title': 'Panier de données',
  'cart.empty': 'Votre panier est vide.',
  'cart.emptyHint': 'Recherchez un indicateur et ajoutez-le pour construire un jeu de données.',
  'cart.add': 'Ajouter au panier',
  'cart.added': 'Dans le panier',
  'cart.remove': 'Retirer',
  'cart.clear': 'Vider le panier',
  'cart.build': 'Construire le jeu de données',
  'cart.items': '{count} indicateur(s)',
  'cart.countries': 'Pays pour toutes les séries',
  'cart.period': 'Période pour toutes les séries',
  'cart.shape': 'Format de sortie',
  'cart.bulk': 'Actions groupées',
  'cart.duplicateWarning': 'Ce concept est déjà dans le panier.',

  'workspace.data': 'Données',
  'workspace.metadata': 'Métadonnées',
  'workspace.stats': 'Statistiques descriptives',
  'workspace.profiling': 'Profilage des données',
  'workspace.missing': 'Données manquantes',
  'workspace.charts': 'Graphiques',
  'workspace.sources': 'Sources et provenance',
  'workspace.export': 'Exporter',

  'quality.clean': 'Propre',
  'quality.warnings': 'Utilisable avec avertissements',
  'quality.unusable': 'Inutilisable',
  'quality.coverage': 'Couverture',
  'quality.rows': 'Lignes',
  'quality.variables': 'Variables',

  'export.title': "Centre d'export",
  'export.excel': 'Classeur Excel',
  'export.html': 'Rapport HTML',
  'export.bundle': 'Dossier de recherche (ZIP)',
  'export.recipe': 'Recette du jeu de données',

  'common.loading': 'Chargement…',
  'common.error': "Une erreur s'est produite.",
  'common.retry': 'Réessayer',
  'common.close': 'Fermer',
  'common.language': 'Langue',
  'common.noResults': 'Aucune correspondance exacte',
  'common.noResultsHint':
    "Voici les indicateurs disponibles les plus proches. Vous pouvez élargir la période ou changer de source.",

  'footer.note':
    "Les valeurs proviennent de fournisseurs officiels et sont toujours citées. Rien n'est interpolé, imputé ni généré par un modèle de langage.",
}

const AR: Partial<Record<TranslationKey, string>> = {
  'brand.tagline': 'البيانات الاقتصادية، أسهل في العثور عليها.',
  'nav.docs': 'التوثيق',
  'nav.home': 'الرئيسية',
  'nav.search': 'البحث',
  'nav.browse': 'تصفح',
  'nav.cart': 'سلة البيانات',
  'nav.workspace': 'مساحة العمل',
  'nav.sources': 'المصادر',

  'home.title': 'البيانات الاقتصادية، أسهل في العثور عليها.',
  'home.subtitle':
    'صف البيانات التي تحتاجها بالعربية أو الإنجليزية أو الفرنسية. احصل على مجموعة بيانات موثقة وقابلة للتحديث مع مصدر كامل — وليس مجرد رسم بياني.',
  'home.search.label': 'ابحث في البيانات الاقتصادية',
  'home.search.button': 'بحث',
  'home.search.busy': 'جارٍ البحث…',
  'home.search.hint':
    'بدون رموز المؤشرات، وبدون اختيار مزود. تبقى المرشحات المتقدمة بعيدة حتى تطلبها.',
  'home.examples.label': 'جرب:',
  'home.topics.title': 'تصفح حسب الموضوع',

  'understanding.title': 'ما فهمته',
  'understanding.countries': 'البلدان',
  'understanding.indicators': 'المؤشرات',
  'understanding.period': 'الفترة',
  'understanding.frequency': 'التكرار',
  'understanding.sources': 'المصادر',
  'understanding.output': 'المخرجات',
  'understanding.notSpecified': 'غير محدد',
  'understanding.queryLanguage': 'استعلام بـ{lang}',

  'ambiguity.title': 'أي تعريف تقصد؟',
  'ambiguity.note':
    'لهذه المصطلحات عدة تعريفات مختلفة جوهريًا. لن تختار SmatEconData أحدها دون إخبارك.',

  'modes.quick': 'بحث سريع',
  'modes.guided': 'موجّه',
  'modes.advanced': 'متقدم',
  'modes.browse': 'الفهرس',
  'modes.geography': 'حسب البلد',
  'modes.source': 'حسب المصدر',
  'modes.batch': 'دفعة',
  'modes.code': 'رمز دقيق',

  'guided.what': 'أي بيانات؟',
  'guided.where': 'أين؟',
  'guided.when': 'متى؟',
  'guided.frequency': 'التكرار',
  'guided.source': 'المصدر المفضل',
  'guided.review': 'مراجعة',
  'guided.next': 'التالي',
  'guided.back': 'رجوع',
  'guided.skip': 'تخطي',
  'guided.run': 'استرجاع',

  'advanced.provider': 'المزود',
  'advanced.code': 'رمز المؤشر',
  'advanced.topic': 'الموضوع',
  'advanced.startYear': 'سنة البداية',
  'advanced.endYear': 'سنة النهاية',
  'advanced.frequency': 'التكرار',
  'advanced.shape': 'شكل المخرجات',
  'advanced.apply': 'تطبيق',
  'advanced.reset': 'إعادة تعيين',

  'batch.label': 'مؤشر واحد في كل سطر',
  'batch.resolve': 'حل الكل',
  'batch.resolved': 'تم التعرف',
  'batch.unresolved': 'غير معروف',

  'cart.title': 'سلة البيانات',
  'cart.empty': 'سلتك فارغة.',
  'cart.emptyHint': 'ابحث عن مؤشر وأضفه لبناء مجموعة بيانات.',
  'cart.add': 'أضف إلى السلة',
  'cart.added': 'في السلة',
  'cart.remove': 'إزالة',
  'cart.clear': 'إفراغ السلة',
  'cart.build': 'بناء مجموعة البيانات',
  'cart.items': '{count} مؤشر',
  'cart.countries': 'البلدان لكل السلاسل',
  'cart.period': 'الفترة لكل السلاسل',
  'cart.shape': 'شكل المخرجات',
  'cart.bulk': 'إجراءات جماعية',
  'cart.duplicateWarning': 'هذا المفهوم موجود في السلة بالفعل.',

  'workspace.data': 'البيانات',
  'workspace.metadata': 'البيانات الوصفية',
  'workspace.stats': 'الإحصاءات الوصفية',
  'workspace.profiling': 'تحليل البيانات',
  'workspace.missing': 'البيانات المفقودة',
  'workspace.charts': 'الرسوم البيانية',
  'workspace.sources': 'المصادر والإسناد',
  'workspace.export': 'تصدير',

  'quality.clean': 'سليم',
  'quality.warnings': 'قابل للاستخدام مع تحذيرات',
  'quality.unusable': 'غير قابل للاستخدام',
  'quality.coverage': 'التغطية',
  'quality.rows': 'الصفوف',
  'quality.variables': 'المتغيرات',

  'export.title': 'مركز التصدير',
  'export.excel': 'مصنف إكسل',
  'export.html': 'تقرير HTML',
  'export.bundle': 'حزمة بحثية (ZIP)',
  'export.recipe': 'وصفة مجموعة البيانات',

  'common.loading': 'جارٍ التحميل…',
  'common.error': 'حدث خطأ ما.',
  'common.retry': 'أعد المحاولة',
  'common.close': 'إغلاق',
  'common.language': 'اللغة',
  'common.noResults': 'لا توجد مطابقة تامة',
  'common.noResultsHint':
    'إليك أقرب المؤشرات المتاحة. يمكنك توسيع الفترة أو تغيير المصدر.',

  'footer.note':
    'القيم تأتي من مزودين رسميين ويتم الاستشهاد بها دائمًا. لا شيء يُستكمل أو يُقدَّر أو يُولَّد بواسطة نموذج لغوي.',
}

const DICTIONARIES: Record<UiLanguage, Partial<Record<TranslationKey, string>>> = {
  en: EN,
  fr: FR,
  ar: AR,
}

interface I18nContextValue {
  language: UiLanguage
  dir: 'ltr' | 'rtl'
  setLanguage: (language: UiLanguage) => void
  /** Translate a key, with optional `{placeholder}` substitution. */
  t: (key: TranslationKey, vars?: Record<string, string | number>) => string
  /** Locale-aware number formatting (spec 0O). */
  formatNumber: (value: number, options?: Intl.NumberFormatOptions) => string
  languages: typeof LANGUAGES
}

const I18nContext = createContext<I18nContextValue | undefined>(undefined)

function detectInitial(): UiLanguage {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === 'en' || stored === 'fr' || stored === 'ar') return stored
  } catch {
    /* ignore */
  }
  const nav = typeof navigator !== 'undefined' ? navigator.language ?? '' : ''
  const prefix = nav.slice(0, 2).toLowerCase()
  if (prefix === 'ar' || prefix === 'fr') return prefix
  return 'en'
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<UiLanguage>(detectInitial)

  const dir = LANGUAGES.find((l) => l.id === language)?.dir ?? 'ltr'

  useEffect(() => {
    const root = document.documentElement
    root.setAttribute('lang', language)
    root.setAttribute('dir', dir)
  }, [language, dir])

  const setLanguage = useCallback((next: UiLanguage) => {
    setLanguageState(next)
    try {
      window.localStorage.setItem(STORAGE_KEY, next)
    } catch {
      /* ignore */
    }
  }, [])

  const t = useCallback(
    (key: TranslationKey, vars?: Record<string, string | number>) => {
      // Fall back to English rather than exposing a raw key to the user.
      const template = DICTIONARIES[language][key] ?? EN[key] ?? key
      if (!vars) return template
      return Object.entries(vars).reduce(
        // split/join rather than replaceAll: the tsconfig lib target is
        // below ES2021, and this is equivalent for a literal needle.
        (text, [name, value]) => text.split(`{${name}}`).join(String(value)),
        template,
      )
    },
    [language],
  )

  const formatNumber = useCallback(
    (value: number, options?: Intl.NumberFormatOptions) => {
      const locale = language === 'ar' ? 'ar' : language === 'fr' ? 'fr-FR' : 'en-GB'
      try {
        return new Intl.NumberFormat(locale, options).format(value)
      } catch {
        return String(value)
      }
    },
    [language],
  )

  const value = useMemo<I18nContextValue>(
    () => ({ language, dir, setLanguage, t, formatNumber, languages: LANGUAGES }),
    [language, dir, setLanguage, t, formatNumber],
  )

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n(): I18nContextValue {
  const context = useContext(I18nContext)
  if (!context) {
    throw new Error('useI18n must be used within an I18nProvider')
  }
  return context
}
