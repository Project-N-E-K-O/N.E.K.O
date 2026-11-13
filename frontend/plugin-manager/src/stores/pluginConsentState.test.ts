// @vitest-environment happy-dom
import { beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { PluginConsentInvalidated, usePluginConsentState } from './pluginConsentState'
import {
  analyzePluginCapabilities,
  approvePluginConsent,
  getPluginConsent,
  revokePluginConsent,
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

it('polls a completed report while its revision check is pending', async () => {
  vi.useFakeTimers()
  const store = usePluginConsentState()
  const completed = response(null, 'completed')
  store.details.demo = {
    ...completed,
    analysis: { ...completed.analysis, analysis_id: 'pending-check' },
  }
  vi.mocked(getPluginConsent).mockResolvedValue(completed)
  store.watchPlugin('demo')
  try {
    expect(store.stateOf('demo')?.state).toBe('confirm')
    expect(store.isAnalysisPending('demo')).toBe(true)
    await vi.advanceTimersByTimeAsync(4100)
    expect(getPluginConsent).toHaveBeenCalledOnce()
    expect(store.isAnalysisPending('demo')).toBe(false)
  } finally {
    store.unwatchPlugin('demo')
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

it('a new same-version summary fences a pending detail read and triggers a fresh read', async () => {
  let finishOld!: (value: PluginConsent) => void
  const old = response('approved', 'completed')
  const current: PluginConsent = { ...old, state: 'confirm', unconfirmed: ['messages.push'] }
  vi.mocked(getPluginConsent)
    .mockReturnValueOnce(new Promise((resolve) => { finishOld = resolve }))
    .mockResolvedValueOnce(current)
  const store = usePluginConsentState()
  store.details.demo = old
  const previous = store.load('demo')
  store.seedSummaries([
    { id: 'demo', version: '1', consent: current },
  ])
  expect(store.stateOf('demo')?.state).toBe('confirm')
  expect(store.details.demo).toBeUndefined()
  const concurrent = store.load('demo')
  expect(getPluginConsent).toHaveBeenCalledTimes(1)

  finishOld(old)
  await expect(previous).resolves.toEqual(current)
  await expect(concurrent).resolves.toEqual(current)
  expect(store.details.demo).toEqual(current)
  expect(getPluginConsent).toHaveBeenCalledTimes(2)
})

it('rechecks a saved approval when a newer summary still requires confirmation', async () => {
  let finishApproval!: (value: PluginConsent) => void
  const old = response('approved', 'completed')
  const current: PluginConsent = { ...old, state: 'confirm', unconfirmed: ['messages.push'] }
  vi.mocked(approvePluginConsent).mockReturnValue(
    new Promise((resolve) => { finishApproval = resolve }),
  )
  vi.mocked(getPluginConsent).mockResolvedValue(current)
  const store = usePluginConsentState()
  store.details.demo = response(null, 'completed')
  const confirmation = store.requestConfirmation([{ id: 'demo', name: 'Demo' }])
  const approval = store.approve('demo', {
    version: '1',
    unconfirmed: ['memory.read'],
    analysis_status: 'completed',
  })
  await vi.waitFor(() => expect(approvePluginConsent).toHaveBeenCalledOnce())
  store.seedSummaries([{ id: 'demo', version: '1', consent: current }])
  const read = store.load('demo')

  finishApproval(old)
  await expect(approval).resolves.toEqual(current)
  await expect(read).resolves.toEqual(current)
  expect(store.stateOf('demo')?.state).toBe('confirm')
  expect(store.activeRequest?.plugins).toHaveLength(1)
  expect(getPluginConsent).toHaveBeenCalledOnce()
  store.resolveConfirmation(false)
  await expect(confirmation).resolves.toBe(false)
})

it('rechecks an old GET that finishes after a saved decision and a newer summary', async () => {
  let finishOld!: (value: PluginConsent) => void
  const old = response('approved', 'completed')
  const current: PluginConsent = { ...old, state: 'confirm', unconfirmed: ['messages.push'] }
  vi.mocked(getPluginConsent)
    .mockReturnValueOnce(new Promise((resolve) => { finishOld = resolve }))
    .mockResolvedValueOnce(current)
  vi.mocked(approvePluginConsent).mockResolvedValue(old)
  const store = usePluginConsentState()
  const reading = store.load('demo')
  await store.approve('demo', {
    version: '1',
    unconfirmed: ['memory.read'],
    analysis_status: 'completed',
  })
  store.seedSummaries([{ id: 'demo', version: '1', consent: current }])

  finishOld(old)
  await expect(reading).resolves.toEqual(current)
  expect(store.stateOf('demo')?.state).toBe('confirm')
})

it('lets a slow detail read finish across several poll ticks', async () => {
  vi.useFakeTimers()
  const store = usePluginConsentState()
  let finish!: (value: PluginConsent) => void
  vi.mocked(getPluginConsent).mockReturnValue(
    new Promise((resolve) => { finish = resolve }),
  )
  store.watchPlugin('demo')
  try {
    await vi.advanceTimersByTimeAsync(6100)
    expect(getPluginConsent).toHaveBeenCalledOnce()
    finish(response(null, 'completed'))
    await vi.advanceTimersByTimeAsync(2100)
    expect(store.stateOf('demo')?.state).toBe('confirm')
    expect(getPluginConsent).toHaveBeenCalledOnce()
  } finally {
    store.unwatchPlugin('demo')
    vi.useRealTimers()
  }
})

it('reloads details invalidated by a summary while a confirmation is open', async () => {
  const store = usePluginConsentState()
  store.details.demo = response(null, 'completed')
  const current: PluginConsent = {
    ...response('approved', 'completed'),
    state: 'confirm',
    unconfirmed: ['messages.push'],
  }
  vi.mocked(getPluginConsent).mockResolvedValue(current)
  const pending = store.requestConfirmation([{ id: 'demo', name: 'Demo' }])

  store.seedSummaries([{ id: 'demo', version: '1', consent: current }])
  await vi.waitFor(() => expect(store.details.demo).toEqual(current))
  store.resolveConfirmation(false)
  await expect(pending).resolves.toBe(false)
})

it('ignores a summary requested before a newer detail', async () => {
  const store = usePluginConsentState()
  const ticket = store.nextRequestTicket()
  const current = response('approved', 'completed')
  vi.mocked(getPluginConsent).mockResolvedValue(current)
  await store.load('demo')
  store.seedSummaries([
    { id: 'demo', version: '1', consent: { state: 'confirm', decision: null, upgraded: true } },
  ], ticket)
  expect(store.stateOf('demo')?.state).toBe('clear')
  expect(store.details.demo).toEqual(current)
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

it('discards an in-flight mutation and skips queued writes when a plugin is forgotten', async () => {
  let finishApproval!: (value: PluginConsent) => void
  vi.mocked(approvePluginConsent).mockReturnValue(
    new Promise((resolve) => { finishApproval = resolve }),
  )
  const store = usePluginConsentState()
  const approval = store.approve('demo', { version: '1', unconfirmed: [], analysis_status: 'completed' })
  const revocation = store.revoke('demo')
  const invalidated = Promise.all([
    expect(approval).rejects.toBeInstanceOf(PluginConsentInvalidated),
    expect(revocation).rejects.toBeInstanceOf(PluginConsentInvalidated),
  ])
  await vi.waitFor(() => expect(approvePluginConsent).toHaveBeenCalledOnce())
  store.forget('demo')
  const replacement = { ...response(null, 'completed'), version: '2' }
  vi.mocked(getPluginConsent).mockResolvedValue(replacement)
  await expect(store.load('demo')).resolves.toEqual(replacement)
  finishApproval(response('approved', 'completed'))

  await invalidated
  expect(revokePluginConsent).not.toHaveBeenCalled()
  expect(store.details.demo).toEqual(replacement)
})

it('cancels a pending start when its consent state is forgotten', async () => {
  const store = usePluginConsentState()
  store.details.demo = response(null, 'completed')
  const pending = store.requestConfirmation([{ id: 'demo', name: 'Demo' }])
  store.forget('demo')
  await expect(pending).resolves.toBe(false)
})
