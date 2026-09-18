// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { fetchMarketLatestVersions, fetchMarketPlugin } from '@/api/market'
import { collectMarketUpdateTargets, usePluginUpdatesStore } from './pluginUpdates'
import type { MarketPlugin } from '@/api/market'
import type { PluginMeta } from '@/types/api'

const mocks = vi.hoisted(() => ({
  pluginStore: {
    plugins: [] as unknown[],
    pluginsWithStatus: [] as unknown[],
    error: null as string | null,
    fetchPlugins: vi.fn(async () => {}),
    syncRegistryAndFetch: vi.fn(async () => ({
      registryRefreshed: true,
      warningMessage: null,
    })),
  },
}))

vi.mock('@/stores/plugin', () => ({
  usePluginStore: () => mocks.pluginStore,
}))

vi.mock('@/api/market', () => ({
  fetchMarketLatestVersions: vi.fn(),
  fetchMarketPlugin: vi.fn(),
}))

// ─── helpers ────────────────────────────────────────────────────────────────

function plugin(id: string, installSource?: unknown): PluginMeta {
  return { id, name: id, install_source: installSource ?? null } as unknown as PluginMeta
}

function marketSource(marketId: string, version: string, channel = 'stable'): unknown {
  return {
    source: 'market',
    reason: 'user_requested',
    installed_at: null,
    source_detail: {
      plugin_market_id: marketId,
      version,
      channel,
      package_url: 'https://market.test/x.neko-plugin',
      package_sha256: 'a'.repeat(64),
      payload_hash: null,
      published_at: '2026-01-01T00:00:00Z',
      previous_version: null,
    },
  }
}

function setPlugins(list: PluginMeta[]): void {
  mocks.pluginStore.plugins = list
  mocks.pluginStore.pluginsWithStatus = list
}

function latestRows(rows: Array<[number, string, string?]>) {
  return rows.map(([pluginId, version, channel]) => ({
    plugin_id: pluginId,
    channel: (channel ?? 'stable') as 'stable' | 'beta',
    version,
    published_at: '2026-01-01T00:00:00Z',
  }))
}

function release(version: string, marketId = 15): MarketPlugin {
  return {
    id: marketId,
    rawId: marketId,
    name: 'alpha',
    version,
    download_url: 'https://market.test/alpha.neko-plugin',
    latest_package_sha256: 'b'.repeat(64),
    latest_payload_hash: 'payload-hash',
    latest_channel: 'stable',
    latest_published_at: '2026-01-02T00:00:00Z',
    has_release: true,
  } as unknown as MarketPlugin
}

type Route = { status: number; body: unknown }

function mockFetch(handler: (url: string, init?: RequestInit) => Route | undefined) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const route = handler(String(input), init) ?? { status: 404, body: {} }
    return new Response(JSON.stringify(route.body), { status: route.status })
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

function installBodies(fetchMock: ReturnType<typeof mockFetch>): Array<Record<string, unknown>> {
  return fetchMock.mock.calls
    .filter(([url]) => String(url).startsWith('/market/install'))
    .map(([, init]) => JSON.parse(String((init as RequestInit).body)))
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  sessionStorage.clear()
  setPlugins([])
  mocks.pluginStore.error = null
  mocks.pluginStore.syncRegistryAndFetch.mockImplementation(async () => ({
    registryRefreshed: true,
    warningMessage: null,
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

// ─── target collection ──────────────────────────────────────────────────────

describe('collectMarketUpdateTargets', () => {
  it('keeps only market installs with a numeric Market id', () => {
    setPlugins([
      plugin('alpha', marketSource('15', '1.0.0')),
      plugin('beta', marketSource('not-numeric', '1.0.0')),
      plugin('gamma', { source: 'manual', source_detail: null }),
      plugin('delta', { source: 'builtin', source_detail: null }),
      plugin('epsilon', {
        source: 'imported',
        source_detail: { package_filename: 'x.neko-plugin', package_sha256: 'c'.repeat(64) },
      }),
      plugin('zeta'),
    ])
    setPlugins(mocks.pluginStore.plugins as PluginMeta[])

    expect(collectMarketUpdateTargets(mocks.pluginStore.plugins as PluginMeta[])).toEqual([
      {
        pluginId: 'alpha',
        marketId: '15',
        name: 'alpha',
        channel: 'stable',
        currentVersion: '1.0.0',
      },
    ])
  })

  it('treats a non-stable/beta channel as stable and drops duplicates', () => {
    setPlugins([
      plugin('alpha', marketSource('15', '1.0.0', 'nightly')),
      plugin('alpha', marketSource('15', '1.0.0', 'beta')),
    ])
    const targets = collectMarketUpdateTargets(mocks.pluginStore.plugins as PluginMeta[])
    expect(targets).toHaveLength(1)
    expect(targets[0]!.channel).toBe('stable')
  })
})

// ─── check ──────────────────────────────────────────────────────────────────

describe('plugin updates store — check', () => {
  it('lists only plugins whose latest release is strictly newer', async () => {
    setPlugins([
      plugin('alpha', marketSource('15', '1.0.0')),
      plugin('beta', marketSource('18', '1.0.0')),
    ])
    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(
      latestRows([[15, '1.1.0'], [18, '1.0.0']]),
    )

    const store = usePluginUpdatesStore()
    await store.check()

    expect(store.candidates.map((candidate) => candidate.pluginId)).toEqual(['alpha'])
    expect(store.candidates[0]!.latestVersion).toBe('1.1.0')
    expect(store.unresolved).toBe(0)
    expect(store.checkFailed).toBe(false)
  })

  it('counts plugins the market did not report instead of calling them up to date', async () => {
    setPlugins([
      plugin('alpha', marketSource('15', '1.0.0')),
      plugin('beta', marketSource('18', '1.0.0')),
    ])
    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(latestRows([[15, '1.1.0']]))

    const store = usePluginUpdatesStore()
    await store.check()

    expect(store.candidates.map((candidate) => candidate.pluginId)).toEqual(['alpha'])
    expect(store.unresolved).toBe(1)
  })

  it('never touches the market when nothing was installed from it', async () => {
    setPlugins([plugin('delta', { source: 'builtin', source_detail: null })])

    const store = usePluginUpdatesStore()
    await store.check()

    expect(fetchMarketLatestVersions).not.toHaveBeenCalled()
    expect(store.candidates).toEqual([])
    expect(store.unresolved).toBe(0)
  })

  it('keeps the previous snapshot and flags the check when the lookup fails', async () => {
    setPlugins([plugin('alpha', marketSource('15', '1.0.0'))])
    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(latestRows([[15, '1.1.0']]))

    const store = usePluginUpdatesStore()
    await store.check()
    expect(store.candidates).toHaveLength(1)

    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(null)
    await store.check({ force: true })

    expect(store.checkFailed).toBe(true)
    expect(store.candidates.map((candidate) => candidate.pluginId)).toEqual(['alpha'])
  })

  it('does not re-fetch within the freshness window unless forced', async () => {
    setPlugins([plugin('alpha', marketSource('15', '1.0.0'))])
    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(latestRows([[15, '1.1.0']]))

    const store = usePluginUpdatesStore()
    await store.check()
    await store.check()
    expect(fetchMarketLatestVersions).toHaveBeenCalledTimes(1)

    await store.check({ force: true })
    expect(fetchMarketLatestVersions).toHaveBeenCalledTimes(1 + 1)
  })

  it('refuses to rebuild the list while an upgrade is in flight', async () => {
    setPlugins([plugin('alpha', marketSource('15', '1.0.0'))])
    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(latestRows([[15, '1.1.0']]))

    const store = usePluginUpdatesStore()
    await store.check()
    expect(store.candidates).toHaveLength(1)
    const fetches = vi.mocked(fetchMarketLatestVersions).mock.calls.length

    // Rebuilding here would replace the object `updateOne` is mutating, so its
    // failure state would be written to an orphan and never reach the UI.
    store.candidates[0]!.status = 'updating'
    await store.check({ force: true })

    expect(vi.mocked(fetchMarketLatestVersions).mock.calls).toHaveLength(fetches)
    expect(store.candidates[0]!.status).toBe('updating')
    expect(store.checking).toBe(false)
  })
})

// ─── boot popup ─────────────────────────────────────────────────────────────

describe('plugin updates store — boot popup', () => {
  it('pops up only when something is outdated, and only once per window', async () => {
    setPlugins([plugin('alpha', marketSource('15', '1.0.0'))])
    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(latestRows([[15, '1.1.0']]))

    const store = usePluginUpdatesStore()
    await store.checkOnBoot()
    expect(store.popupOpen).toBe(true)

    store.closePopup()
    await store.checkOnBoot()
    expect(store.popupOpen).toBe(false)
  })

  it('stays hidden when everything is up to date', async () => {
    setPlugins([plugin('alpha', marketSource('15', '1.0.0'))])
    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(latestRows([[15, '1.0.0']]))

    const store = usePluginUpdatesStore()
    await store.checkOnBoot()
    expect(store.popupOpen).toBe(false)
  })

  it('stays hidden and silent when the market cannot be reached', async () => {
    setPlugins([plugin('alpha', marketSource('15', '1.0.0'))])
    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(null)

    const store = usePluginUpdatesStore()
    await expect(store.checkOnBoot()).resolves.toBeUndefined()
    expect(store.popupOpen).toBe(false)
    expect(store.candidates).toEqual([])
  })
})

// ─── upgrade ────────────────────────────────────────────────────────────────

describe('plugin updates store — upgrade', () => {
  async function seedOneCandidate(): Promise<ReturnType<typeof usePluginUpdatesStore>> {
    setPlugins([plugin('alpha', marketSource('15', '1.0.0'))])
    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(latestRows([[15, '1.1.0']]))
    const store = usePluginUpdatesStore()
    await store.check()
    expect(store.candidates).toHaveLength(1)
    return store
  }

  it('upgrades through the bridge, then drops the candidate', async () => {
    vi.mocked(fetchMarketPlugin).mockResolvedValue(release('1.1.0'))
    const fetchMock = mockFetch((url) => {
      if (url.startsWith('/market/bridge-token')) return { status: 200, body: { bridge_token: 'tok' } }
      if (url.startsWith('/market/install')) return { status: 200, body: { task_id: 'task-1' } }
      if (url.startsWith('/market/tasks/task-1')) {
        return { status: 200, body: { status: 'completed', stage: 'completed', progress: 1 } }
      }
      return undefined
    })

    const store = await seedOneCandidate()
    await expect(store.updateOne('alpha')).resolves.toBe(true)

    expect(store.candidates).toEqual([])
    expect(installBodies(fetchMock)).toEqual([
      expect.objectContaining({
        mode: 'upgrade',
        on_conflict: 'fail',
        plugin_id: '15',
        // Matched against the active lock entry's plugin.toml id.
        expected_plugin_toml_id: 'alpha',
        package_sha256: 'b'.repeat(64),
        version: '1.1.0',
      }),
    ])
    expect(mocks.pluginStore.syncRegistryAndFetch).toHaveBeenCalled()
  })

  it('reports a rollback code when the task fails', async () => {
    vi.mocked(fetchMarketPlugin).mockResolvedValue(release('1.1.0'))
    mockFetch((url) => {
      if (url.startsWith('/market/bridge-token')) return { status: 200, body: { bridge_token: 'tok' } }
      if (url.startsWith('/market/install')) return { status: 200, body: { task_id: 'task-2' } }
      if (url.startsWith('/market/tasks/task-2')) {
        return {
          status: 200,
          body: { status: 'failed', error_code: 'upgrade_rollback_completed' },
        }
      }
      return undefined
    })

    const store = await seedOneCandidate()
    await expect(store.updateOne('alpha')).resolves.toBe(false)

    expect(store.candidates[0]!.status).toBe('failed')
    expect(store.candidates[0]!.errorKey).toBe('market.upgradeRollback')
  })

  it('sends builtin overrides to the Market page instead of failing them', async () => {
    vi.mocked(fetchMarketPlugin).mockResolvedValue(release('1.1.0'))
    mockFetch((url) => {
      if (url.startsWith('/market/bridge-token')) return { status: 200, body: { bridge_token: 'tok' } }
      if (url.startsWith('/market/install')) {
        return { status: 409, body: { detail: { code: 'override_confirmation_required' } } }
      }
      return undefined
    })

    const store = await seedOneCandidate()
    await expect(store.updateOne('alpha')).resolves.toBe(false)

    expect(store.candidates[0]!.needsManualUpgrade).toBe(true)
    expect(store.candidates[0]!.status).toBe('idle')
    expect(store.candidates[0]!.errorKey).toBeNull()
  })

  it('keeps a failed upgrade flagged across a later re-check', async () => {
    vi.mocked(fetchMarketPlugin).mockResolvedValue(null)
    mockFetch((url) => {
      if (url.startsWith('/market/bridge-token')) return { status: 200, body: { bridge_token: 'tok' } }
      return undefined
    })

    const store = await seedOneCandidate()
    await store.updateOne('alpha')
    expect(store.candidates[0]!.status).toBe('failed')

    await store.check({ force: true })
    const candidate = store.candidates.find((entry) => entry.pluginId === 'alpha')
    expect(candidate?.status).toBe('failed')
    expect(candidate?.errorKey).toBe('market.marketListFetchFailed')
  })

  it('updates serially in order and keeps going after one failure', async () => {
    setPlugins([
      plugin('alpha', marketSource('15', '1.0.0')),
      plugin('beta', marketSource('18', '1.0.0')),
      plugin('gamma', marketSource('19', '1.0.0')),
    ])
    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(
      latestRows([[15, '1.1.0'], [18, '1.1.0'], [19, '1.1.0']]),
    )
    // beta's release cannot be resolved, so its update must fail on its own.
    vi.mocked(fetchMarketPlugin).mockImplementation(async (pluginId) => (
      String(pluginId) === '18' ? null : release('1.1.0', Number(pluginId))
    ))
    const fetchMock = mockFetch((url) => {
      if (url.startsWith('/market/bridge-token')) return { status: 200, body: { bridge_token: 'tok' } }
      if (url.startsWith('/market/install')) return { status: 200, body: { task_id: 'batch-task' } }
      if (url.startsWith('/market/tasks/batch-task')) {
        return { status: 200, body: { status: 'completed', stage: 'completed', progress: 1 } }
      }
      return undefined
    })

    const store = usePluginUpdatesStore()
    await store.check()
    expect(store.candidates).toHaveLength(3)

    await store.updateAll()

    expect(installBodies(fetchMock).map((body) => body.plugin_id)).toEqual([
      '15',
      '19',
    ])
    expect(store.batchRunning).toBe(false)
    expect(store.batchTotal).toBe(3)
    expect(store.batchDone).toBe(3)
    expect(store.candidates.find((entry) => entry.pluginId === 'beta')?.status).toBe('failed')
  })

  it('ignores manual-only candidates when updating everything', async () => {
    setPlugins([plugin('alpha', marketSource('15', '1.0.0'))])
    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(latestRows([[15, '1.1.0']]))
    const fetchMock = mockFetch((url) => {
      if (url.startsWith('/market/bridge-token')) return { status: 200, body: { bridge_token: 'tok' } }
      if (url.startsWith('/market/install')) {
        return { status: 409, body: { detail: { code: 'plugin_replacement_source_unsupported' } } }
      }
      return undefined
    })

    const store = await seedOneCandidate()
    vi.mocked(fetchMarketPlugin).mockResolvedValue(release('1.1.0'))
    // First run moves the candidate to the manual path.
    await store.updateOne('alpha')
    expect(store.candidates[0]!.needsManualUpgrade).toBe(true)

    const callsBefore = installBodies(fetchMock).length
    await store.updateAll()
    expect(installBodies(fetchMock)).toHaveLength(callsBefore)
  })
})
