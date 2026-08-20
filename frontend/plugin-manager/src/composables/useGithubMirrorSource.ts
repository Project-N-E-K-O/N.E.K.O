import { computed, ref } from 'vue'

export type GithubMirrorMode = 'direct' | 'auto' | 'specified'

export const GITHUB_PROXY_SOURCES = [
  { id: 'gh-proxy-com', label: 'https://gh-proxy.com/', baseUrl: 'https://gh-proxy.com/' },
  { id: 'gh-proxy-org', label: 'https://gh-proxy.org/', baseUrl: 'https://gh-proxy.org/' },
  { id: 'hk-gh-proxy-org', label: 'https://hk.gh-proxy.org/', baseUrl: 'https://hk.gh-proxy.org/' },
  { id: 'cdn-gh-proxy-org', label: 'https://cdn.gh-proxy.org/', baseUrl: 'https://cdn.gh-proxy.org/' },
  { id: 'edgeone-gh-proxy-org', label: 'https://edgeone.gh-proxy.org/', baseUrl: 'https://edgeone.gh-proxy.org/' },
] as const

export type GithubProxySourceId = typeof GITHUB_PROXY_SOURCES[number]['id']

const STORAGE_KEY = 'neko.market.github-mirror-source.v2'
const LEGACY_STORAGE_KEY = 'neko.market.github-mirror-source'
const DEFAULT_PROXY_ID: GithubProxySourceId = 'gh-proxy-com'

interface StoredMirrorSource {
  mode: GithubMirrorMode
  specifiedSourceId: GithubProxySourceId
  autoSourceId: GithubProxySourceId | null
}

function isSourceId(value: unknown): value is GithubProxySourceId {
  return typeof value === 'string' && GITHUB_PROXY_SOURCES.some((source) => source.id === value)
}

function loadSettings(): StoredMirrorSource {
  const defaults: StoredMirrorSource = {
    mode: 'direct',
    specifiedSourceId: DEFAULT_PROXY_ID,
    autoSourceId: null,
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<StoredMirrorSource>
      return {
        mode: parsed.mode === 'auto' || parsed.mode === 'specified' ? parsed.mode : 'direct',
        specifiedSourceId: isSourceId(parsed.specifiedSourceId) ? parsed.specifiedSourceId : DEFAULT_PROXY_ID,
        autoSourceId: isSourceId(parsed.autoSourceId) ? parsed.autoSourceId : null,
      }
    }
    // Keep the former single-source setting working after the upgrade.
    if (window.localStorage.getItem(LEGACY_STORAGE_KEY) === 'github-proxy') {
      return { ...defaults, mode: 'specified' }
    }
  } catch {
    // Fall back to the direct source when storage is unavailable or malformed.
  }
  return defaults
}

const initial = loadSettings()
const mode = ref<GithubMirrorMode>(initial.mode)
const specifiedSourceId = ref<GithubProxySourceId>(initial.specifiedSourceId)
const autoSourceId = ref<GithubProxySourceId | null>(initial.autoSourceId)

function persist() {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({
      mode: mode.value,
      specifiedSourceId: specifiedSourceId.value,
      autoSourceId: autoSourceId.value,
    }))
  } catch {
    // Keep the in-memory choice when localStorage is unavailable.
  }
}

function setMode(next: GithubMirrorMode) {
  mode.value = next
  persist()
}

function setSpecifiedSourceId(next: GithubProxySourceId) {
  specifiedSourceId.value = next
  persist()
}

function setAutoSourceId(next: GithubProxySourceId) {
  autoSourceId.value = next
  persist()
}

const activeSource = computed(() => {
  if (mode.value === 'specified') {
    return GITHUB_PROXY_SOURCES.find((source) => source.id === specifiedSourceId.value) ?? null
  }
  if (mode.value === 'auto' && autoSourceId.value) {
    return GITHUB_PROXY_SOURCES.find((source) => source.id === autoSourceId.value) ?? null
  }
  return null
})

/** Return a GitHub Release URL through the selected mirror when applicable. */
function resolveGithubDownloadUrl(url: string): string {
  const source = activeSource.value
  if (!source) return url

  try {
    const parsed = new URL(url)
    if (parsed.protocol !== 'https:' || parsed.hostname.toLowerCase() !== 'github.com') {
      return url
    }
  } catch {
    return url
  }

  return `${source.baseUrl}${url}`
}

export function useGithubMirrorSource() {
  return {
    mode,
    specifiedSourceId,
    autoSourceId,
    activeSource,
    sources: GITHUB_PROXY_SOURCES,
    setMode,
    setSpecifiedSourceId,
    setAutoSourceId,
    resolveGithubDownloadUrl,
  }
}
