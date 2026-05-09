function appendQueryParam(url: string, key: string, value: string) {
  const hashIndex = url.indexOf('#')
  const beforeHash = hashIndex >= 0 ? url.slice(0, hashIndex) : url
  const hash = hashIndex >= 0 ? url.slice(hashIndex) : ''
  const queryIndex = beforeHash.indexOf('?')
  const path = queryIndex >= 0 ? beforeHash.slice(0, queryIndex) : beforeHash
  const query = queryIndex >= 0 ? beforeHash.slice(queryIndex + 1) : ''
  const params = new URLSearchParams(query)
  params.set(key, value)
  return `${path}?${params.toString()}${hash}`
}

/**
 * Append the active UI locale as a query param to a static-panel iframe URL.
 *
 * Used by every plugin's static panel so the iframe's `src` actually changes
 * when the user switches language — without a URL diff Vue won't trigger an
 * iframe reload, leaving the in-iframe `i18n.js` stuck on the locale that was
 * active when the panel first mounted. (Originally galgame-only; promoted to
 * the default after bilibili/mijia hit the same staleness bug.)
 */
export function withStaticUiLocale(url: string, locale: string) {
  if (!url) return url
  const trimmed = String(locale || '').trim()
  if (!trimmed) return url
  return appendQueryParam(url, 'locale', trimmed)
}

/** @deprecated kept as a thin shim while callers migrate to `withStaticUiLocale`. */
export function withGalgameStaticUiLocale(url: string, _pluginId: string, locale: string) {
  return withStaticUiLocale(url, locale)
}

/** @deprecated previously gated the locale query on galgame; now always true when locale is non-empty. */
export function shouldAttachGalgameStaticUiLocale(_pluginId: string, locale: string) {
  return String(locale || '').trim().length > 0
}

export function buildPluginStaticUiUrl(pluginId: string, cacheBust: number, locale: string) {
  if (!pluginId) return ''
  const url = `/plugin/${encodeURIComponent(pluginId)}/ui/?_ui=${encodeURIComponent(String(cacheBust))}`
  return withStaticUiLocale(url, locale)
}
