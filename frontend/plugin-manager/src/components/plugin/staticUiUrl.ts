const GALGAME_PLUGIN_ID = 'galgame_plugin'

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

export function shouldAttachGalgameStaticUiLocale(pluginId: string, locale: string) {
  return pluginId === GALGAME_PLUGIN_ID && String(locale || '').trim().length > 0
}

export function withGalgameStaticUiLocale(url: string, pluginId: string, locale: string) {
  if (!url || !shouldAttachGalgameStaticUiLocale(pluginId, locale)) return url
  return appendQueryParam(url, 'locale', String(locale))
}

export function buildPluginStaticUiUrl(pluginId: string, cacheBust: number, locale: string) {
  if (!pluginId) return ''
  const url = `/plugin/${encodeURIComponent(pluginId)}/ui/?_ui=${encodeURIComponent(String(cacheBust))}`
  return withGalgameStaticUiLocale(url, pluginId, locale)
}
