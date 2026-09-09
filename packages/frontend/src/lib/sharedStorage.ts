/**
 * Cookie helpers for SmatEconData.
 *
 * Cookies are host-only by default, which is the correct behaviour for a
 * self-hosted deployment and for `localhost` during development. A deployment
 * that genuinely serves the app from several subdomains of one registrable
 * domain can opt into shared cookies by setting `VITE_COOKIE_ROOT_DOMAIN`
 * (for example `.example.org`); leaving it unset keeps cookies host-only.
 */

const RAW_ROOT_DOMAIN = (import.meta.env.VITE_COOKIE_ROOT_DOMAIN ?? '').trim()

/** Normalised registrable domain, always leading-dot, or undefined. */
const COOKIE_ROOT_DOMAIN: string | undefined = RAW_ROOT_DOMAIN
  ? RAW_ROOT_DOMAIN.startsWith('.')
    ? RAW_ROOT_DOMAIN
    : `.${RAW_ROOT_DOMAIN}`
  : undefined

function isBrowser(): boolean {
  return typeof window !== 'undefined' && typeof document !== 'undefined'
}

/**
 * True when the current hostname sits under the configured root domain.
 *
 * Compares hostnames only -- `window.location.hostname` never contains a
 * port, so a configured value carrying one would never match.
 */
function canUseRootDomainCookie(hostname: string): boolean {
  if (!COOKIE_ROOT_DOMAIN) {
    return false
  }
  const bare = COOKIE_ROOT_DOMAIN.replace(/^\./, '')
  return hostname === bare || hostname.endsWith(`.${bare}`)
}

function resolveCookieDomain(): string | undefined {
  if (!isBrowser()) {
    return undefined
  }
  return canUseRootDomainCookie(window.location.hostname) ? COOKIE_ROOT_DOMAIN : undefined
}

function buildCookie(name: string, value: string, maxAgeSeconds: number, domain?: string): string {
  const parts = [
    `${name}=${encodeURIComponent(value)}`,
    'Path=/',
    'SameSite=Lax',
    `Max-Age=${maxAgeSeconds}`,
  ]

  if (domain) {
    parts.push(`Domain=${domain}`)
  }

  if (isBrowser() && window.location.protocol === 'https:') {
    parts.push('Secure')
  }

  return parts.join('; ')
}

export function setSharedCookie(name: string, value: string, maxAgeSeconds: number): void {
  if (!isBrowser()) {
    return
  }

  const domain = resolveCookieDomain()
  document.cookie = buildCookie(name, value, maxAgeSeconds, domain)
}

export function getCookie(name: string): string | null {
  if (!isBrowser()) {
    return null
  }

  const prefix = `${name}=`
  const match = document.cookie
    .split(';')
    .map((entry) => entry.trim())
    .find((entry) => entry.startsWith(prefix))

  if (!match) {
    return null
  }

  return decodeURIComponent(match.slice(prefix.length))
}

export function removeSharedCookie(name: string): void {
  if (!isBrowser()) {
    return
  }

  const domain = resolveCookieDomain()
  document.cookie = buildCookie(name, '', 0, domain)
  // Also clear host-only cookie variant for safety.
  document.cookie = buildCookie(name, '', 0)
}

/** True when cross-subdomain cookie sharing is configured and active. */
export function isSharedCookieDomainActive(): boolean {
  return isBrowser() && canUseRootDomainCookie(window.location.hostname)
}
