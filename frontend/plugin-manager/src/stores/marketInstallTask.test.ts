// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { fetchBridge } from '@/api/marketBridge'
import { useMarketInstallTaskStore, type MarketInstallContext } from './marketInstallTask'

vi.mock('@/api/marketBridge', () => ({
  fetchBridge: vi.fn(),
}))

const POLL_MS = 800

function context(overrides: Partial<MarketInstallContext> = {}): MarketInstallContext {
  return {
    pluginId: 'neko_live',
    name: 'NEKO Live',
    mode: 'upgrade',
    channel: 'stable',
    fromVersion: '0.1.6',
    toVersion: '0.1.9',
    ...overrides,
  }
}

function task(body: Record<string, unknown>) {
  return { ok: true, status: 200, json: async () => body }
}

/** One poll: advance the fake clock past the poll interval and let the loop run. */
async function tick(): Promise<void> {
  await vi.advanceTimersByTimeAsync(POLL_MS)
}

beforeEach(() => {
  setActivePinia(createPinia())
  // reset, not clear: an unconsumed ``mockResolvedValueOnce`` would otherwise
  // bleed into the next test's first poll.
  vi.resetAllMocks()
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] })
})

async function drainPollLoop(): Promise<void> {
  useMarketInstallTaskStore().dismiss()
  // Let the parked ``sleep`` wake up once so it can observe the generation bump.
  await vi.advanceTimersByTimeAsync(POLL_MS * 2)
}

afterEach(async () => {
  await drainPollLoop()
  vi.useRealTimers()
})

describe('market install task store — tracking', () => {
  it('polls to completion and exposes the final state', async () => {
    vi.mocked(fetchBridge)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'downloading', stage: 'download', progress: 0.2, downloaded_bytes: 100, total_bytes: 1000 }) as never)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'completed', stage: 'completed', progress: 1 }) as never)

    const store = useMarketInstallTaskStore()
    const pending = store.track('t', context(), 'panel')

    expect(store.running).toBe(true)
    await tick()
    expect(store.task?.stage).toBe('download')
    await tick()
    await expect(pending).resolves.toEqual({ ok: true })

    expect(store.running).toBe(false)
    expect(store.done).toBe(true)
    expect(store.percent).toBe(100)
    expect(store.barStatus).toBe('success')
  })

  it('maps a failed task to its preserved stage and error key', async () => {
    vi.mocked(fetchBridge).mockResolvedValue(
      task({
        task_id: 't',
        status: 'failed',
        stage: 'replace',
        progress: 0.8,
        error_code: 'upgrade_rollback_completed',
      }) as never,
    )

    const store = useMarketInstallTaskStore()
    const outcome = await (async () => {
      const p = store.track('t', context(), 'panel')
      await tick()
      return p
    })()

    expect(outcome.ok).toBe(false)
    expect(outcome.errorKey).toBe('market.upgradeRollback')
    expect(store.barStatus).toBe('exception')
    expect(store.steps.map((step) => step.state)).toEqual(['done', 'done', 'failed', 'pending'])
  })

  it('refuses to track a second task while one is running', async () => {
    vi.mocked(fetchBridge).mockResolvedValue(
      task({ task_id: 't', status: 'downloading', stage: 'download', progress: 0.2 }) as never,
    )

    const store = useMarketInstallTaskStore()
    const first = store.track('t', context(), 'panel')
    await tick()

    await expect(store.track('other', context(), 'panel')).resolves.toEqual({
      ok: false,
      errorKey: 'market.installAlreadyRunning',
      refused: true,
    })
    expect(store.taskId).toBe('t')

    store.dismiss()
    await tick()
    await expect(first).resolves.toEqual({ ok: false, aborted: true })
  })

  it('reports a dropped track as aborted, not as a failure', async () => {
    vi.mocked(fetchBridge).mockResolvedValue(
      task({ task_id: 't', status: 'downloading', stage: 'download', progress: 0.2 }) as never,
    )

    const store = useMarketInstallTaskStore()
    const p = store.track('t', context(), 'panel')
    await tick()

    store.dismiss()
    await tick()

    await expect(p).resolves.toEqual({ ok: false, aborted: true })
  })

  it('leaves the task terminal after the 404 lockout so later installs still work', async () => {
    vi.mocked(fetchBridge).mockResolvedValue({ status: 404 } as never)

    const store = useMarketInstallTaskStore()
    const p = store.track('t', context(), 'panel')
    for (let i = 0; i < 15; i += 1) await tick()
    await expect(p).resolves.toEqual({ ok: false, errorKey: 'market.installTaskLost' })

    // Regression guard: a non-terminal placeholder used to leave `running` true
    // forever, which disabled every later install and could never be dismissed.
    expect(store.running).toBe(false)
    expect(store.done).toBe(true)
    expect(store.errorKey).toBe('market.installTaskLost')

    vi.mocked(fetchBridge).mockResolvedValue(
      task({ task_id: 'next', status: 'completed', stage: 'completed', progress: 1 }) as never,
    )
    const next = store.track('next', context(), 'panel')
    await tick()
    await expect(next).resolves.toEqual({ ok: true })
  })

  it('leaves the task terminal after a rejected token', async () => {
    vi.mocked(fetchBridge).mockResolvedValue({ status: 403 } as never)

    const store = useMarketInstallTaskStore()
    const p = store.track('t', context(), 'panel')
    await tick()
    await expect(p).resolves.toEqual({ ok: false, errorKey: 'market.pairRequired' })

    expect(store.running).toBe(false)
    expect(store.done).toBe(true)
    expect(store.errorKey).toBe('market.pairRequired')
    expect(store.barStatus).toBe('exception')
  })

  it('gives up after a run of 404s', async () => {
    vi.mocked(fetchBridge).mockResolvedValue({ status: 404 } as never)

    const store = useMarketInstallTaskStore()
    const p = store.track('t', context(), 'panel')
    for (let i = 0; i < 15; i += 1) await tick()

    await expect(p).resolves.toEqual({ ok: false, errorKey: 'market.installTaskLost' })
  })

  it('bails out immediately when the bridge rejects the token', async () => {
    vi.mocked(fetchBridge).mockResolvedValue({ status: 403 } as never)

    const store = useMarketInstallTaskStore()
    const p = store.track('t', context(), 'panel')
    await tick()

    await expect(p).resolves.toEqual({ ok: false, errorKey: 'market.pairRequired' })
  })

  it('keeps polling when the bridge is temporarily unreachable', async () => {
    vi.mocked(fetchBridge)
      .mockResolvedValueOnce(null as never)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'completed', stage: 'completed', progress: 1 }) as never)

    const store = useMarketInstallTaskStore()
    const p = store.track('t', context(), 'panel')
    await tick()
    await tick()

    await expect(p).resolves.toEqual({ ok: true })
  })

  it('flags overtime without faking a failure', async () => {
    vi.mocked(fetchBridge).mockResolvedValue(
      task({ task_id: 't', status: 'downloading', stage: 'download', progress: 0.2 }) as never,
    )

    const store = useMarketInstallTaskStore()
    void store.track('t', context(), 'panel')
    await tick()
    expect(store.overtime).toBe(false)

    await vi.advanceTimersByTimeAsync(3 * 60 * 1000)
    expect(store.overtime).toBe(true)
    expect(store.running).toBe(true)
    store.dismiss()
  })
})

describe('market install task store — download speed', () => {
  it('derives speed and ETA from successive byte counters', async () => {
    vi.mocked(fetchBridge)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'downloading', stage: 'download', progress: 0.2, downloaded_bytes: 0, total_bytes: 8_000_000 }) as never)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'downloading', stage: 'download', progress: 0.4, downloaded_bytes: 800_000, total_bytes: 8_000_000 }) as never)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'downloading', stage: 'download', progress: 0.6, downloaded_bytes: 1_600_000, total_bytes: 8_000_000 }) as never)

    const store = useMarketInstallTaskStore()
    void store.track('t', context(), 'panel')

    await tick()
    expect(store.speed).toBeNull() // one sample is not a rate

    await tick()
    expect(store.speed).toBeCloseTo(1_000_000, -3) // 800KB per 800ms
    await tick()
    expect(store.speed).toBeCloseTo(1_000_000, -3)
    expect(store.eta).toBe(6) // (8MB - 1.6MB) / 1MB/s

    expect(store.transferText).toContain('1.5 MB / 7.6 MB')
    expect(store.transferText).toContain('976.6 KB/s')
    expect(store.transferText).toContain('6s')
    store.dismiss()
  })

  it('drops the rate instead of going negative when the backend resets the counter', async () => {
    vi.mocked(fetchBridge)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'downloading', stage: 'verify', progress: 0.7, downloaded_bytes: 5_000_000, total_bytes: 5_000_000 }) as never)
      // Mirror fallback: progress is dragged back to the download range and the
      // byte counter restarts from zero while the stage stays "verify".
      .mockResolvedValueOnce(task({ task_id: 't', status: 'downloading', stage: 'verify', progress: 0.1, downloaded_bytes: 0, total_bytes: null }) as never)

    const store = useMarketInstallTaskStore()
    void store.track('t', context(), 'panel')

    await tick()
    await tick()

    expect(store.speed).toBeNull()
    expect(store.eta).toBeNull()
    expect(store.transferText).toBe('') // not in the download stage any more
    store.dismiss()
  })

  it('never lets the bar move backwards', async () => {
    vi.mocked(fetchBridge)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'verifying', stage: 'verify', progress: 0.7 }) as never)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'downloading', stage: 'download', progress: 0.1 }) as never)

    const store = useMarketInstallTaskStore()
    void store.track('t', context(), 'panel')

    await tick()
    expect(store.percent).toBe(70)
    await tick()
    expect(store.percent).toBe(70)
    store.dismiss()
  })

  it('reports speed without an ETA when the server sent no content length', async () => {
    vi.mocked(fetchBridge)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'downloading', stage: 'download', progress: 0.2, downloaded_bytes: 0, total_bytes: null }) as never)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'downloading', stage: 'download', progress: 0.3, downloaded_bytes: 400_000, total_bytes: null }) as never)

    const store = useMarketInstallTaskStore()
    void store.track('t', context(), 'panel')
    await tick()
    await tick()

    expect(store.speed).toBeCloseTo(500_000, -3)
    expect(store.eta).toBeNull()
    expect(store.transferText).toBe('390.6 KB · 488.3 KB/s')
    store.dismiss()
  })
})

describe('market install task store — step checklist', () => {
  it('lists replace for upgrades and install for fresh installs', async () => {
    vi.mocked(fetchBridge).mockResolvedValue(
      task({ task_id: 't', status: 'downloading', stage: 'download', progress: 0.2 }) as never,
    )

    const store = useMarketInstallTaskStore()
    void store.track('t', context({ mode: 'upgrade' }), 'panel')
    await tick()
    expect(store.steps.map((step) => step.id)).toEqual(['download', 'verify', 'replace', 'completed'])
    store.dismiss()

    void store.track('t2', context({ mode: 'install', fromVersion: null }), 'panel')
    await tick()
    expect(store.steps.map((step) => step.id)).toEqual(['download', 'verify', 'install', 'completed'])
    store.dismiss()
  })

  it('advances the checklist and marks the active step', async () => {
    vi.mocked(fetchBridge)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'downloading', stage: 'download', progress: 0.3 }) as never)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'verifying', stage: 'verify', progress: 0.7 }) as never)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'installing', stage: 'replace', progress: 0.8 }) as never)

    const store = useMarketInstallTaskStore()
    void store.track('t', context(), 'panel')

    await tick()
    expect(store.steps.map((step) => step.state)).toEqual(['active', 'pending', 'pending', 'pending'])
    await tick()
    expect(store.steps.map((step) => step.state)).toEqual(['done', 'active', 'pending', 'pending'])
    await tick()
    expect(store.steps.map((step) => step.state)).toEqual(['done', 'done', 'active', 'pending'])
    store.dismiss()
  })

  it('inserts a rollback step once the replacement transaction starts one', async () => {
    vi.mocked(fetchBridge)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'installing', stage: 'replace', progress: 0.8, rollback: { prepared: true, restored: false } }) as never)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'installing', stage: 'rollback', progress: 0.9, rollback: { prepared: true, restored: false, running: true } }) as never)

    const store = useMarketInstallTaskStore()
    void store.track('t', context(), 'panel')

    await tick()
    expect(store.steps.map((step) => step.id)).toEqual(['download', 'verify', 'replace', 'rollback', 'completed'])
    await tick()
    expect(store.steps.map((step) => step.state)).toEqual(['done', 'done', 'done', 'active', 'pending'])
    expect(store.rollback?.running).toBe(true)
    store.dismiss()
  })

  it('does not walk the checklist backwards when a retry re-enters download', async () => {
    vi.mocked(fetchBridge)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'verifying', stage: 'verify', progress: 0.7 }) as never)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'downloading', stage: 'download', progress: 0.1 }) as never)

    const store = useMarketInstallTaskStore()
    void store.track('t', context(), 'panel')

    await tick()
    await tick()
    expect(store.steps.map((step) => step.state)).toEqual(['done', 'active', 'pending', 'pending'])
    store.dismiss()
  })

  it('remembers the running step when the backend wipes it on cancel', async () => {
    vi.mocked(fetchBridge)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'verifying', stage: 'verify', progress: 0.7 }) as never)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'canceled', stage: 'canceled', progress: 0.7, error_code: 'install_cancelled' }) as never)

    const store = useMarketInstallTaskStore()
    const p = store.track('t', context(), 'panel')

    await tick()
    await tick()
    await expect(p).resolves.toEqual({
      ok: false,
      errorKey: 'market.installCancelled',
      canceled: true,
    })

    expect(store.steps.map((step) => step.state)).toEqual(['done', 'active', 'pending', 'pending'])
    store.dismiss()
  })
})

describe('market install task store — install slot reservation', () => {
  it('only lets one surface claim the slot at a time', () => {
    const store = useMarketInstallTaskStore()

    expect(store.reserve('panel')).toBe(true)
    expect(store.reserve('float')).toBe(false)
    expect(store.reservation).toBe('panel')

    // Releasing the wrong surface must not free someone else's claim.
    store.release('float')
    expect(store.reservation).toBe('panel')

    store.release('panel')
    expect(store.reservation).toBeNull()
    expect(store.reserve('float')).toBe(true)
  })

  it('refuses a claim while a task is already being tracked', async () => {
    vi.mocked(fetchBridge).mockResolvedValue(
      task({ task_id: 't', status: 'downloading', stage: 'download', progress: 0.2 }) as never,
    )
    const store = useMarketInstallTaskStore()
    void store.track('t', context(), 'panel')
    await tick()

    expect(store.reserve('float')).toBe(false)
    store.dismiss()
    await tick()
    expect(store.reserve('float')).toBe(true)
  })

  it('does not clear a reservation held by the other surface', async () => {
    vi.mocked(fetchBridge).mockResolvedValue(
      task({ task_id: 't', status: 'completed', stage: 'completed', progress: 1 }) as never,
    )
    const store = useMarketInstallTaskStore()
    const p = store.track('t', context(), 'float')
    await tick()
    await p

    // The Market panel claims the slot for its own preflight while the finished
    // float task is still displayed.
    expect(store.reserve('panel')).toBe(true)
    store.dismiss('float')

    // Regression guard: this used to wipe the panel's claim, letting a third
    // request start a concurrent backend install.
    expect(store.reservation).toBe('panel')
    expect(store.reserve('float')).toBe(false)
  })

  it('clears the claim when the task is dismissed', async () => {
    vi.mocked(fetchBridge).mockResolvedValue(
      task({ task_id: 't', status: 'completed', stage: 'completed', progress: 1 }) as never,
    )
    const store = useMarketInstallTaskStore()
    expect(store.reserve('panel')).toBe(true)
    const p = store.track('t', context(), 'panel')
    await tick()
    await p

    store.dismiss('panel')
    expect(store.reservation).toBeNull()
  })

  it('does not write state when the task is dismissed mid-request', async () => {
    let releaseFetch: (value: unknown) => void = () => {}
    vi.mocked(fetchBridge).mockImplementation(
      () => new Promise((resolve) => { releaseFetch = resolve as (value: unknown) => void }) as never,
    )

    const store = useMarketInstallTaskStore()
    const p = store.track('t', context(), 'panel')
    await tick() // the loop is now parked inside `await fetchBridge(...)`

    store.dismiss()
    releaseFetch(task({ task_id: 't', status: 'downloading', stage: 'download', progress: 0.5 }))
    await tick()

    await expect(p).resolves.toEqual({ ok: false, aborted: true })
    // Regression guard: the stale poll used to repopulate the store, which made
    // every later install refuse to start.
    expect(store.task).toBeNull()
    expect(store.running).toBe(false)
  })
})

describe('market install task store — ownership', () => {
  it('refuses to let the other surface dismiss a live task', async () => {
    vi.mocked(fetchBridge).mockResolvedValue(
      task({ task_id: 't', status: 'completed', stage: 'completed', progress: 1 }) as never,
    )

    const store = useMarketInstallTaskStore()
    const p = store.track('t', context(), 'panel')
    await tick()
    await p

    store.dismiss('float')
    expect(store.task).not.toBeNull()

    store.dismiss('panel')
    expect(store.task).toBeNull()
  })

  it('dismisses without an owner for a forced clear', async () => {
    vi.mocked(fetchBridge).mockResolvedValue(
      task({ task_id: 't', status: 'completed', stage: 'completed', progress: 1 }) as never,
    )
    const store = useMarketInstallTaskStore()
    const p = store.track('t', context(), 'float')
    await tick()
    await p

    store.dismiss()
    expect(store.task).toBeNull()
    expect(store.owner).toBeNull()
  })
})

describe('market install task store — cancel', () => {
  it('posts the cancel request and adopts the returned task', async () => {
    vi.mocked(fetchBridge)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'downloading', stage: 'download', progress: 0.2 }) as never)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'downloading', stage: 'download', progress: 0.2, cancel_requested: true }) as never)

    const store = useMarketInstallTaskStore()
    const p = store.track('t', context(), 'panel')
    await tick()

    await expect(store.cancel()).resolves.toBe('ok')
    expect(store.task?.cancel_requested).toBe(true)

    store.dismiss()
    await tick()
    await p
  })

  it('reports an unavailable cancel as such', async () => {
    vi.mocked(fetchBridge)
      .mockResolvedValueOnce(task({ task_id: 't', status: 'installing', stage: 'replace', progress: 0.8 }) as never)
      .mockResolvedValueOnce({ status: 409, json: async () => ({ detail: '安装已进入写入阶段，无法安全取消' }) } as never)

    const store = useMarketInstallTaskStore()
    const p = store.track('t', context(), 'panel')
    await tick()

    await expect(store.cancel()).resolves.toBe('unavailable')

    store.dismiss()
    await tick()
    await p
  })
})
