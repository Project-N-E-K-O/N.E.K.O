import { ref } from 'vue'

import { fetchBridge } from '@/api/marketBridge'

const LOG_PREFIX = '[github-mirror]'
const STORAGE_KEY = 'neko.market.github-mirror-source.v2'
/** The single-proxy switch that preceded per-source settings. */
const LEGACY_STORAGE_KEY = 'neko.market.github-mirror-source'

const log = {
  warn: (...args: unknown[]) => console.warn(LOG_PREFIX, ...args),
}

export type GithubMirrorMode = 'direct' | 'auto' | 'specified'

/** Proxies the backend can route GitHub Release downloads through; same ids
 *  as ``GITHUB_DOWNLOAD_SOURCES`` in ``plugin/server/infrastructure/package_sources.py``. */
export const GITHUB_PROXY_SOURCES = [
  { id: 'gh-proxy-com', label: 'https://gh-proxy.com/' },
  { id: 'gh-proxy-org', label: 'https://gh-proxy.org/' },
  { id: 'hk-gh-proxy-org', label: 'https://hk.gh-proxy.org/' },
  { id: 'cdn-gh-proxy-org', label: 'https://cdn.gh-proxy.org/' },
  { id: 'edgeone-gh-proxy-org', label: 'https://edgeone.gh-proxy.org/' },
] as const

export type GithubProxySourceId = (typeof GITHUB_PROXY_SOURCES)[number]['id']

interface DownloadPreferences {
  mode: GithubMirrorMode
  specified_source_id: GithubProxySourceId
}

interface ServerDownloadPreferences extends DownloadPreferences {
  /** False until the server has stored a choice of its own. */
  configured: boolean
}

const DEFAULT_PREFERENCES: DownloadPreferences = {
  mode: 'auto',
  specified_source_id: 'gh-proxy-com',
}

function isMode(value: unknown): value is GithubMirrorMode {
  return value === 'direct' || value === 'auto' || value === 'specified'
}

function isProxySourceId(value: unknown): value is GithubProxySourceId {
  return GITHUB_PROXY_SOURCES.some((source) => source.id === value)
}

/**
 * Browser copy of the last known setting. It only fills the form before the
 * server answers, and seeds a server that was never configured so the choice
 * made while this setting lived in the browser carries over.
 */
function loadCachedPreferences(): DownloadPreferences {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) {
      return localStorage.getItem(LEGACY_STORAGE_KEY) === 'github-proxy'
        ? { ...DEFAULT_PREFERENCES, mode: 'specified' }
        : DEFAULT_PREFERENCES
    }
    const parsed = JSON.parse(raw) as { mode?: unknown; specifiedSourceId?: unknown }
    return {
      mode: isMode(parsed.mode) ? parsed.mode : DEFAULT_PREFERENCES.mode,
      specified_source_id: isProxySourceId(parsed.specifiedSourceId)
        ? parsed.specifiedSourceId
        : DEFAULT_PREFERENCES.specified_source_id,
    }
  } catch {
    // Unavailable or malformed storage: the server setting still applies.
    return DEFAULT_PREFERENCES
  }
}

function cachePreferences(preferences: DownloadPreferences) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      mode: preferences.mode,
      specifiedSourceId: preferences.specified_source_id,
    }))
  } catch {
    // Storage is optional; the server keeps the setting.
  }
}

const cached = loadCachedPreferences()
const mode = ref<GithubMirrorMode>(cached.mode)
const specifiedSourceId = ref<GithubProxySourceId>(cached.specified_source_id)
const saveFailed = ref(false)
/** Set on the first edit, so a late server answer cannot revert the form. */
let edited = false
let initialization: Promise<void> | null = null
/** Saves run one at a time, in the order they were made. */
let writes: Promise<void> = Promise.resolve()

function apply(preferences: DownloadPreferences) {
  mode.value = preferences.mode
  specifiedSourceId.value = preferences.specified_source_id
  cachePreferences(preferences)
}

async function requestPreferences(init?: RequestInit): Promise<ServerDownloadPreferences> {
  const response = await fetchBridge('/market/download-preferences', init, {
    throwOnTransportError: true,
  })
  if (!response?.ok) throw new Error('download_preferences_unavailable')
  const value = (await response.json()) as Partial<ServerDownloadPreferences>
  if (!isMode(value.mode) || !isProxySourceId(value.specified_source_id)) {
    throw new Error('invalid_download_preferences')
  }
  return {
    mode: value.mode,
    specified_source_id: value.specified_source_id,
    configured: value.configured === true,
  }
}

function putPreferences(body: DownloadPreferences & { initialize_only?: boolean }) {
  return requestPreferences({
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

async function loadServerPreferences() {
  let preferences = await requestPreferences()
  if (!preferences.configured) {
    // `initialize_only` keeps whatever another window stored in the meantime.
    preferences = await putPreferences({ ...cached, initialize_only: true })
  }
  if (!edited) apply(preferences)
}

function initialize(): Promise<void> {
  if (!initialization) {
    initialization = loadServerPreferences().catch((error: unknown) => {
      // Let the next caller retry instead of caching the failure.
      initialization = null
      throw error
    })
  }
  return initialization
}

function save(next: DownloadPreferences): Promise<void> {
  edited = true
  apply(next)
  writes = writes.then(async () => {
    try {
      await initialize()
      await putPreferences(next)
      saveFailed.value = false
    } catch (error) {
      log.warn('saving download preferences failed', error)
      saveFailed.value = true
    }
  })
  return writes
}

function setMode(next: GithubMirrorMode) {
  void save({ mode: next, specified_source_id: specifiedSourceId.value })
}

function setSpecifiedSourceId(next: GithubProxySourceId) {
  void save({ mode: mode.value, specified_source_id: next })
}

/** Resolve once the server holds the setting shown in the form, since every
 *  install reads its download policy from the server. */
async function ensureDownloadPreferences(): Promise<void> {
  await settleWrites()
  if (saveFailed.value) {
    // Retry the latest edit; the form still shows it.
    void save({ mode: mode.value, specified_source_id: specifiedSourceId.value })
    await settleWrites()
  }
  if (saveFailed.value) throw new Error('download_preferences_save_failed')
  await initialize()
}

/** Wait for every queued save, including those made while waiting. */
async function settleWrites(): Promise<void> {
  let settled: Promise<void>
  do {
    settled = writes
    await settled
  } while (settled !== writes)
}

/** Forget the backend's learned route timings so the next install chooses again. */
async function resetDownloadHistory(): Promise<void> {
  const response = await fetchBridge('/market/download-history/reset', { method: 'POST' }, {
    throwOnTransportError: true,
  })
  if (!response?.ok) throw new Error('download_history_reset_failed')
}

export function useGithubMirrorSource() {
  return {
    mode,
    specifiedSourceId,
    saveFailed,
    sources: GITHUB_PROXY_SOURCES,
    setMode,
    setSpecifiedSourceId,
    ensureDownloadPreferences,
    resetDownloadHistory,
  }
}
