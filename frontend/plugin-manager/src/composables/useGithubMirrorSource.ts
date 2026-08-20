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
export const GITHUB_DIRECT_SOURCE = {
  id: 'github-direct',
  baseUrl: 'https://github.com/',
} as const
export const GITHUB_SPEED_TEST_SOURCES = [GITHUB_DIRECT_SOURCE, ...GITHUB_PROXY_SOURCES] as const
export type GithubMirrorSourceId = typeof GITHUB_SPEED_TEST_SOURCES[number]['id']

const STORAGE_KEY = 'neko.market.github-mirror-source.v2'
const LEGACY_STORAGE_KEY = 'neko.market.github-mirror-source'
const DEFAULT_PROXY_ID: GithubProxySourceId = 'gh-proxy-com'
export const AUTO_MIRROR_MEASUREMENT_MAX_AGE_MS = 5 * 60 * 1000

interface StoredMirrorSource {
  mode: GithubMirrorMode
  specifiedSourceId: GithubProxySourceId
  autoSourceId: GithubMirrorSourceId | null
  autoMeasuredAt: number | null
}

export interface GithubMirrorMeasurement {
  id: GithubMirrorSourceId
  latency_ms: number | null
  available: boolean
  status_code?: number | null
}

export class GithubMirrorMeasurementError extends Error {
  readonly code: 'service_unavailable' | 'invalid_response'

  constructor(code: 'service_unavailable' | 'invalid_response') {
    super(code)
    this.name = 'GithubMirrorMeasurementError'
    this.code = code
  }
}

function isSourceId(value: unknown): value is GithubProxySourceId {
  return typeof value === 'string' && GITHUB_PROXY_SOURCES.some((source) => source.id === value)
}

function isMirrorSourceId(value: unknown): value is GithubMirrorSourceId {
  return typeof value === 'string' && GITHUB_SPEED_TEST_SOURCES.some((source) => source.id === value)
}

/** Whether a URL is a credential-free GitHub Release asset safe to proxy. */
export function isGithubReleaseDownloadUrl(url: string): boolean {
  try {
    const parsed = new URL(url)
    return (
      parsed.protocol === 'https:'
      && parsed.hostname.toLowerCase() === 'github.com'
      && !parsed.username
      && !parsed.password
      && parsed.pathname.includes('/releases/download/')
    )
  } catch {
    return false
  }
}

function loadSettings(): StoredMirrorSource {
  const defaults: StoredMirrorSource = {
    mode: 'auto',
    specifiedSourceId: DEFAULT_PROXY_ID,
    autoSourceId: null,
    autoMeasuredAt: null,
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<StoredMirrorSource>
      return {
        mode: parsed.mode === 'auto' || parsed.mode === 'specified' ? parsed.mode : 'direct',
        specifiedSourceId: isSourceId(parsed.specifiedSourceId) ? parsed.specifiedSourceId : DEFAULT_PROXY_ID,
        autoSourceId: isMirrorSourceId(parsed.autoSourceId) ? parsed.autoSourceId : null,
        autoMeasuredAt: typeof parsed.autoMeasuredAt === 'number' && parsed.autoMeasuredAt > 0
          ? parsed.autoMeasuredAt
          : null,
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
const autoSourceId = ref<GithubMirrorSourceId | null>(initial.autoSourceId)
const autoMeasuredAt = ref<number | null>(initial.autoMeasuredAt)
const autoMeasurementClock = ref(Date.now())
let autoMeasurementExpiryTimer: ReturnType<typeof setTimeout> | null = null

function scheduleAutoMeasurementExpiry() {
  if (autoMeasurementExpiryTimer !== null) {
    clearTimeout(autoMeasurementExpiryTimer)
    autoMeasurementExpiryTimer = null
  }
  if (autoMeasuredAt.value === null) return

  const remaining = autoMeasuredAt.value + AUTO_MIRROR_MEASUREMENT_MAX_AGE_MS - Date.now()
  if (remaining <= 0) {
    autoMeasurementClock.value = Date.now()
    return
  }
  autoMeasurementExpiryTimer = setTimeout(() => {
    autoMeasurementExpiryTimer = null
    autoMeasurementClock.value = Date.now()
  }, remaining)
}

function persist() {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({
      mode: mode.value,
      specifiedSourceId: specifiedSourceId.value,
      autoSourceId: autoSourceId.value,
      autoMeasuredAt: autoMeasuredAt.value,
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

function setAutoSourceId(next: GithubMirrorSourceId, measuredAt = Date.now()) {
  autoSourceId.value = next
  autoMeasuredAt.value = measuredAt
  autoMeasurementClock.value = Date.now()
  scheduleAutoMeasurementExpiry()
  persist()
}

function clearAutoSource() {
  autoSourceId.value = null
  autoMeasuredAt.value = null
  autoMeasurementClock.value = Date.now()
  scheduleAutoMeasurementExpiry()
  persist()
}

function isAutoMeasurementFresh() {
  const now = Math.max(autoMeasurementClock.value, Date.now())
  return (
    autoSourceId.value !== null
    && autoMeasuredAt.value !== null
    && now - autoMeasuredAt.value < AUTO_MIRROR_MEASUREMENT_MAX_AGE_MS
  )
}

const autoMeasurementFresh = computed(() => isAutoMeasurementFresh())

const activeSource = computed(() => {
  if (mode.value === 'specified') {
    return GITHUB_PROXY_SOURCES.find((source) => source.id === specifiedSourceId.value) ?? null
  }
  if (mode.value === 'auto' && autoSourceId.value && isAutoMeasurementFresh()) {
    return GITHUB_SPEED_TEST_SOURCES.find((source) => source.id === autoSourceId.value) ?? null
  }
  return null
})

scheduleAutoMeasurementExpiry()

async function measureSpeedTestSources(): Promise<GithubMirrorMeasurement[]> {
  let response: Response
  try {
    response = await fetch('/market/github-proxy/measure')
  } catch {
    throw new GithubMirrorMeasurementError('service_unavailable')
  }
  if (!response.ok) throw new GithubMirrorMeasurementError('service_unavailable')

  let data: { sources?: GithubMirrorMeasurement[] }
  try {
    data = await response.json() as { sources?: GithubMirrorMeasurement[] }
  } catch {
    throw new GithubMirrorMeasurementError('invalid_response')
  }
  return (data.sources ?? []).filter((item) => (
    GITHUB_SPEED_TEST_SOURCES.some((source) => source.id === item.id)
  ))
}

function fastestAvailableSource(measurements: GithubMirrorMeasurement[]): GithubMirrorMeasurement | null {
  return measurements
    .filter((item) => item.available && typeof item.latency_ms === 'number')
    .sort((left, right) => Number(left.latency_ms) - Number(right.latency_ms))[0] ?? null
}

async function refreshAutoSource() {
  const measurements = await measureSpeedTestSources()
  const fastest = fastestAvailableSource(measurements)
  if (fastest) setAutoSourceId(fastest.id)
  else clearAutoSource()
  return { measurements, fastest }
}

/** Refresh an expired automatic result before a Market installation uses it. */
async function ensureAutoSource() {
  if (mode.value !== 'auto' || isAutoMeasurementFresh()) return null
  return refreshAutoSource()
}

/** Return a GitHub Release URL through the selected mirror when applicable. */
function resolveGithubDownloadUrl(url: string): string {
  if (mode.value === 'auto' && !isAutoMeasurementFresh()) return url
  const source = activeSource.value
  if (!source) return url
  if (source.id === GITHUB_DIRECT_SOURCE.id) return url

  if (!isGithubReleaseDownloadUrl(url)) return url

  return `${source.baseUrl}${url}`
}

export function useGithubMirrorSource() {
  return {
    mode,
    specifiedSourceId,
    autoSourceId,
    autoMeasuredAt,
    autoMeasurementFresh,
    activeSource,
    sources: GITHUB_PROXY_SOURCES,
    speedTestSources: GITHUB_SPEED_TEST_SOURCES,
    setMode,
    setSpecifiedSourceId,
    setAutoSourceId,
    clearAutoSource,
    measureSpeedTestSources,
    refreshAutoSource,
    ensureAutoSource,
    resolveGithubDownloadUrl,
  }
}
