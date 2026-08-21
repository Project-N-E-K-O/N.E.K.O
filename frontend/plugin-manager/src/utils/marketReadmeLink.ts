/**
 * Resolve a README link without ever leaving an untrusted relative URL to the
 * embedded application's default navigation. Only HTTP(S) destinations may be
 * opened by the host's external-browser bridge.
 */
export function resolveMarketReadmeLink(
  href: string,
  repositoryUrl?: string,
  fallbackBase?: string,
): string | null {
  const rawHref = href.trim()
  if (!rawHref) return null

  const base = repositoryUrl || fallbackBase || 'http://localhost/'
  try {
    const normalizedBase = `${base.replace(/\/+$/, '')}/`
    const url = new URL(rawHref, normalizedBase)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null
  } catch {
    return null
  }
}
