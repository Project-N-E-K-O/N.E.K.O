// @vitest-environment happy-dom
import { beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { usePluginConsentState } from './pluginConsentState'
import {
  analyzePluginCapabilities,
  approvePluginConsent,
  getPluginConsent,
  type PluginConsent,
} from '@/api/pluginConsent'

vi.mock('@/api/pluginConsent', () => ({
  getPluginConsent: vi.fn(),
  approvePluginConsent: vi.fn(),
  rejectPluginConsent: vi.fn(),
  revokePluginConsent: vi.fn(),
  getPluginConsentNotices: vi.fn(),
  analyzePluginCapabilities: vi.fn(),
}))
function response(decision: 'approved' | 'denied' | null, status: 'queued' | 'completed' | 'failed'): PluginConsent {
  return {
    plugin_id: 'demo',
    version: '1',
    decision,
    decided_at: decision ? 'now' : null,
    approved_capabilities: decision === 'approved' ? ['memory.read'] : [],
    unconfirmed: [],
    state: decision === 'approved' ? 'clear' : status === 'queued' ? 'analyzing' : 'confirm',
    upgraded: false,
    analysis: { status, capabilities: [], evidence: [], warnings: [] },
  }
}
beforeEach(() => {
  setActivePinia(createPinia())
  vi.resetAllMocks()
})

it('a read during a mutation waits for its result rather than publishing older server state', async () => {
  let resolve!: (value: PluginConsent) => void
  vi.mocked(analyzePluginCapabilities).mockReturnValue(
    new Promise((done) => {
      resolve = done
    })
  )
  const store = usePluginConsentState()
  const analysis = store.analyze('demo')
  const read = store.load('demo')
  await Promise.resolve()
  expect(getPluginConsent).not.toHaveBeenCalled()
  resolve(response(null, 'queued'))
  await Promise.all([analysis, read])
  expect(store.details.demo?.analysis.status).toBe('queued')
})

it('a late GET cannot replace a newly queued analysis', async () => {
  let resolve!: (value: PluginConsent) => void
  vi.mocked(getPluginConsent).mockReturnValue(
    new Promise((done) => {
      resolve = done
    })
  )
  vi.mocked(analyzePluginCapabilities).mockResolvedValue(response(null, 'queued'))
  const store = usePluginConsentState()
  const old = store.load('demo')
  await store.analyze('demo')
  resolve(response(null, 'completed'))
  await old
  expect(store.details.demo?.analysis.status).toBe('queued')
})

it('every surface observes persisted consent even if a previous GET finishes later', async () => {
  let resolve!: (value: PluginConsent) => void
  vi.mocked(getPluginConsent).mockReturnValue(
    new Promise((done) => {
      resolve = done
    })
  )
  vi.mocked(approvePluginConsent).mockResolvedValue(response('approved', 'completed'))
  const store = usePluginConsentState()
  const old = store.load('demo')
  await store.approve('demo', { version: '1', unconfirmed: [], analysis_status: 'completed' })
  resolve(response(null, 'completed'))
  await old
  expect(store.details.demo?.decision).toBe('approved')
})

it('the shared poller only refetches watched plugins that are still analyzing', async () => {
  vi.useFakeTimers()
  try {
    vi.mocked(getPluginConsent).mockResolvedValue(response(null, 'queued'))
    const store = usePluginConsentState()
    store.details.demo = response(null, 'queued')
    store.details.done = { ...response('approved', 'completed'), plugin_id: 'done', state: 'clear' }
    store.watchPlugin('demo')
    store.watchPlugin('done')
    await vi.advanceTimersByTimeAsync(2100)
    expect(getPluginConsent).toHaveBeenCalledTimes(1)
    expect(getPluginConsent).toHaveBeenCalledWith('demo')
    store.details.demo = { ...store.details.demo, state: 'confirm' }
    await vi.advanceTimersByTimeAsync(2100)
    expect(getPluginConsent).toHaveBeenCalledTimes(1)
    store.unwatchPlugin('demo')
    store.unwatchPlugin('done')
  } finally {
    vi.useRealTimers()
  }
})

it('seedSummaries feeds stateOf and matching-version details win', async () => {
  vi.mocked(getPluginConsent).mockResolvedValue(response(null, 'completed'))
  const store = usePluginConsentState()
  store.seedSummaries([
    { id: 'demo', version: '1', consent: { state: 'confirm', decision: null, upgraded: false } },
  ])
  expect(store.stateOf('demo')?.state).toBe('confirm')
  await store.load('demo')
  // detail state (confirm, version '1') matches the plugin version -> wins
  expect(store.stateOf('demo')?.state).toBe('confirm')
  // a stale detail is ignored once the summary moves to a new version
  store.seedSummaries([
    { id: 'demo', version: '2', consent: { state: 'analyzing', decision: null, upgraded: false } },
  ])
  expect(store.stateOf('demo')?.state).toBe('analyzing')
})

it('requestConfirmation dedupes identical sets and resolves on approve', async () => {
  const store = usePluginConsentState()
  const plugins = [{ id: 'demo', name: 'Demo' }]
  const first = store.requestConfirmation(plugins)
  const second = store.requestConfirmation(plugins)
  expect(store.activeRequest?.plugins).toEqual(plugins)
  store.resolveConfirmation(true)
  await expect(first).resolves.toBe(true)
  await expect(second).resolves.toBe(true)
  expect(store.activeRequest).toBeNull()
})

it('the active request auto-resolves when every plugin leaves confirm', async () => {
  vi.mocked(approvePluginConsent).mockResolvedValue(response('approved', 'completed'))
  const store = usePluginConsentState()
  store.seedSummaries([
    { id: 'demo', version: '1', consent: { state: 'confirm', decision: null, upgraded: false } },
  ])
  await store.load('demo')
  const pending = store.requestConfirmation([{ id: 'demo', name: 'Demo' }])
  await store.approve('demo', { version: '1', unconfirmed: [], analysis_status: 'completed' })
  await expect(pending).resolves.toBe(true)
  expect(store.activeRequest).toBeNull()
})

it('cancels a pending start when its consent state is forgotten', async () => {
  const store = usePluginConsentState()
  store.details.demo = response(null, 'completed')
  const pending = store.requestConfirmation([{ id: 'demo', name: 'Demo' }])
  store.forget('demo')
  await expect(pending).resolves.toBe(false)
})
