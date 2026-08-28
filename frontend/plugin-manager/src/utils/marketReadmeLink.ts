/**
 * Resolve a README asset without ever leaving an untrusted relative URL to the
 * embedded application's default navigation. Only HTTP(S) destinations may be
 * opened by the host's external-browser bridge.
 */
export interface MarketReadmeUrlOptions {
  sourceRef?: string
  resource?: 'link' | 'image'
}

function getGitHubRepository(repositoryUrl?: string): [string, string] | null {
  if (!repositoryUrl) return null
  try {
    const url = new URL(repositoryUrl)
    const [owner, repository] = url.hostname === 'github.com'
      ? url.pathname.split('/').filter(Boolean)
      : []
    return owner && repository ? [owner, repository] : null
  } catch {
    return null
  }
}

function isRelativeUrl(value: string): boolean {
  return !/^[a-z][a-z\d+.-]*:/i.test(value) && !value.startsWith('//')
}

export function resolveMarketReadmeLink(
  href: string,
  repositoryUrl?: string,
  fallbackBase?: string,
  options: MarketReadmeUrlOptions = {},
): string | null {
  const rawHref = href.trim()
  if (!rawHref) return null

  const githubRepository = options.sourceRef && isRelativeUrl(rawHref)
    ? getGitHubRepository(repositoryUrl)
    : null
  if (githubRepository && !rawHref.startsWith('#') && !rawHref.startsWith('?')) {
    try {
      const relativeUrl = new URL(rawHref, 'https://readme.local/')
      const path = relativeUrl.pathname.replace(/^\/+/, '')
      const [owner, repository] = githubRepository
      const sourceRef = encodeURIComponent(options.sourceRef!)
      if (options.resource === 'image') {
        return `https://raw.githubusercontent.com/${owner}/${repository}/${sourceRef}/${path}${relativeUrl.search}`
      }
      return `https://github.com/${owner}/${repository}/blob/${sourceRef}/${path}${relativeUrl.search}${relativeUrl.hash}`
    } catch {
      return null
    }
  }

  const base = repositoryUrl || fallbackBase || 'http://localhost/'
  try {
    const normalizedBase = `${base.replace(/\/+$/, '')}/`
    const url = new URL(rawHref, normalizedBase)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null
  } catch {
    return null
  }
}
