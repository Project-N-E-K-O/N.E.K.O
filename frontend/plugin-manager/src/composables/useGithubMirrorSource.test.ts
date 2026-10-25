// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchBridge } from '@/api/marketBridge'

vi.mock('@/api/marketBridge', () => ({ fetchBridge: vi.fn() }))

const STORAGE_KEY = 'neko.market.github-mirror-source.v2'
const preferences = { mode: 'auto', specified_source_id: 'gh-proxy-com', configured: true }

function reply(value: unknown): Response {
  return { ok: true, json: async () => value } as Response
}

function putBodies() {
  return vi.mocked(fetchBridge).mock.calls
    .filter(([, init]) => init?.method === 'PUT')
    .map(([, init]) => JSON.parse(String(init?.body)))
}

async function loadComposable() {
  const { useGithubMirrorSource } = await import('./useGithubMirrorSource')
  return useGithubMirrorSource()
}

beforeEach(() => {
  vi.resetModules()
  vi.resetAllMocks()
  vi.spyOn(console, 'warn').mockImplementation(() => undefined)
  localStorage.clear()
  vi.mocked(fetchBridge).mockResolvedValue(reply(preferences))
})

describe('server download preferences', () => {
  it('uses existing server settings instead of stale browser settings', async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ mode: 'direct' }))
    const mirror = await loadComposable()

    await mirror.ensureDownloadPreferences()

    expect(mirror.mode.value).toBe('auto')
    expect(fetchBridge).toHaveBeenCalledTimes(1)
  })

  it('seeds an unconfigured server once from the browser setting', async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      mode: 'specified',
      specifiedSourceId: 'cdn-gh-proxy-org',
    }))
    vi.mocked(fetchBridge)
      .mockResolvedValueOnce(reply({ ...preferences, configured: false }))
      .mockResolvedValueOnce(reply({
        mode: 'specified',
        specified_source_id: 'cdn-gh-proxy-org',
        configured: true,
      }))
    const mirror = await loadComposable()

    await Promise.all([mirror.ensureDownloadPreferences(), mirror.ensureDownloadPreferences()])

    expect(putBodies()).toEqual([
      { mode: 'specified', specified_source_id: 'cdn-gh-proxy-org', initialize_only: true },
    ])
    expect(mirror.specifiedSourceId.value).toBe('cdn-gh-proxy-org')
    expect(fetchBridge).toHaveBeenCalledTimes(2)
  })

  it('serializes edits and does not overwrite a user edit during initialization', async () => {
    const mirror = await loadComposable()

    mirror.setMode('specified')
    mirror.setSpecifiedSourceId('cdn-gh-proxy-org')
    await mirror.ensureDownloadPreferences()

    expect(putBodies()).toEqual([
      { mode: 'specified', specified_source_id: 'gh-proxy-com' },
      { mode: 'specified', specified_source_id: 'cdn-gh-proxy-org' },
    ])
    expect(mirror.mode.value).toBe('specified')
    expect(mirror.specifiedSourceId.value).toBe('cdn-gh-proxy-org')
  })

  it('seeds an unconfigured server from the legacy single-proxy setting', async () => {
    localStorage.setItem('neko.market.github-mirror-source', 'github-proxy')
    vi.mocked(fetchBridge)
      .mockResolvedValueOnce(reply({ ...preferences, configured: false }))
      .mockResolvedValueOnce(reply({ ...preferences, mode: 'specified' }))
    const mirror = await loadComposable()

    await mirror.ensureDownloadPreferences()

    expect(putBodies()).toEqual([
      { mode: 'specified', specified_source_id: 'gh-proxy-com', initialize_only: true },
    ])
  })

  it('waits for a save made while an earlier one was pending', async () => {
    let releaseFirst!: () => void
    vi.mocked(fetchBridge)
      .mockResolvedValueOnce(reply(preferences))
      .mockImplementationOnce(() => new Promise((resolve) => {
        releaseFirst = () => resolve(reply(preferences))
      }))
    const mirror = await loadComposable()
    let installed = false

    mirror.setMode('direct')
    const ready = mirror.ensureDownloadPreferences().then(() => { installed = true })
    await vi.waitFor(() => expect(putBodies()).toHaveLength(1))
    mirror.setMode('specified')
    releaseFirst()
    await ready

    expect(installed).toBe(true)
    expect(putBodies()).toEqual([
      { mode: 'direct', specified_source_id: 'gh-proxy-com' },
      { mode: 'specified', specified_source_id: 'gh-proxy-com' },
    ])
  })

  it('blocks installation while the chosen setting cannot be saved', async () => {
    vi.mocked(fetchBridge).mockResolvedValueOnce(reply(preferences)).mockResolvedValue(null)
    const mirror = await loadComposable()

    mirror.setMode('specified')

    await expect(mirror.ensureDownloadPreferences()).rejects.toThrow(
      'download_preferences_save_failed'
    )
    expect(mirror.saveFailed.value).toBe(true)
  })

  it('retries a failed save before an install proceeds', async () => {
    vi.mocked(fetchBridge)
      .mockResolvedValueOnce(reply(preferences))
      .mockResolvedValueOnce(null)
      .mockResolvedValue(reply({ ...preferences, mode: 'direct' }))
    const mirror = await loadComposable()

    mirror.setMode('direct')
    await expect(mirror.ensureDownloadPreferences()).resolves.toBeUndefined()

    expect(putBodies()).toEqual([
      { mode: 'direct', specified_source_id: 'gh-proxy-com' },
      { mode: 'direct', specified_source_id: 'gh-proxy-com' },
    ])
    expect(mirror.saveFailed.value).toBe(false)
  })

  it('retries initialization after the server was unreachable', async () => {
    vi.mocked(fetchBridge).mockRejectedValueOnce(new Error('offline'))
    const mirror = await loadComposable()

    await expect(mirror.ensureDownloadPreferences()).rejects.toThrow('offline')
    await expect(mirror.ensureDownloadPreferences()).resolves.toBeUndefined()

    expect(fetchBridge).toHaveBeenCalledTimes(2)
  })

  it('clears learned route timings on the backend', async () => {
    const mirror = await loadComposable()

    await mirror.resetDownloadHistory()

    expect(vi.mocked(fetchBridge).mock.calls.map(([path, init]) => [path, init?.method])).toEqual([
      ['/market/download-history/reset', 'POST'],
    ])
  })

  it('reports a failed reset', async () => {
    vi.mocked(fetchBridge).mockResolvedValueOnce({ ok: false } as Response)
    const mirror = await loadComposable()

    await expect(mirror.resetDownloadHistory()).rejects.toThrow('download_history_reset_failed')
  })
})
