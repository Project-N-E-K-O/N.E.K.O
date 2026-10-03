// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { fetchMarketLatestVersions, fetchMarketPluginVersions } from '@/api/market'
import { useGithubMirrorSource } from '@/composables/useGithubMirrorSource'
import { useMarketInstallTaskStore } from './marketInstallTask'
import { collectMarketUpdateTargets, usePluginUpdatesStore } from './pluginUpdates'
import type { MarketPluginVersion } from '@/api/market'
import type { PluginMeta } from '@/types/api'

const mocks = vi.hoisted(() => ({
  pluginStore: {
    pluginSummaries: [] as unknown[],
    fetchPluginSummaries: vi.fn(async () => {}),
    syncRegistryAndFetchSummaries: vi.fn(async () => ({
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
  fetchMarketPluginVersions: vi.fn(),
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
  mocks.pluginStore.pluginSummaries = list
}

function latestRows(rows: Array<[number, string, string?]>) {
  return rows.map(([pluginId, version, channel]) => ({
    plugin_id: pluginId,
    channel: (channel ?? 'stable') as 'stable' | 'beta',
    version,
    published_at: '2026-01-01T00:00:00Z',
  }))
}

/** One row of the channel's version table, as the release lookup sees it. */
function release(version: string, marketId = 15): MarketPluginVersion {
  return {
    id: 1,
    plugin_id: marketId,
    version,
    channel: 'stable',
    package_url: 'https://market.test/alpha.neko-plugin',
    package_sha256: 'b'.repeat(64),
    payload_hash: 'payload-hash',
    is_latest: true,
    yanked_at: null,
    yanked_reason: null,
    created_at: '2026-01-02T00:00:00Z',
  } as unknown as MarketPluginVersion
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
  mocks.pluginStore.fetchPluginSummaries.mockImplementation(async () => {})
  mocks.pluginStore.syncRegistryAndFetchSummaries.mockImplementation(async () => ({
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
    setPlugins(mocks.pluginStore.pluginSummaries as PluginMeta[])

    expect(collectMarketUpdateTargets(mocks.pluginStore.pluginSummaries as PluginMeta[])).toEqual([
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
    const targets = collectMarketUpdateTargets(mocks.pluginStore.pluginSummaries as PluginMeta[])
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

  it('re-runs a forced check that arrived while an older check was in flight', async () => {
    setPlugins([plugin('alpha', marketSource('15', '1.0.0'))])
    let releaseFirst: (rows: ReturnType<typeof latestRows>) => void = () => {}
    vi.mocked(fetchMarketLatestVersions)
      .mockImplementationOnce(() => new Promise((resolve) => { releaseFirst = resolve }))
      .mockResolvedValueOnce(latestRows([[15, '1.1.0']]))

    const store = usePluginUpdatesStore()
    const first = store.check()
    await vi.waitFor(() => expect(fetchMarketLatestVersions).toHaveBeenCalledTimes(1))

    // The Market page upgrades alpha meanwhile and asks for a forced re-check.
    setPlugins([plugin('alpha', marketSource('15', '1.1.0'))])
    await store.check({ force: true })

    releaseFirst(latestRows([[15, '1.1.0']]))
    await first
    // Regression guard: the forced request used to be dropped, so the check
    // that read the pre-upgrade version kept offering the installed release.
    // The first call resolves only after the re-check, so a caller acting on
    // its result (the boot popup) never sees the stale list.
    expect(fetchMarketLatestVersions).toHaveBeenCalledTimes(2)
    expect(store.checking).toBe(false)
    expect(store.candidates).toEqual([])
  })

  it('does not reopen a popup the user closed while the boot check ran', async () => {
    setPlugins([plugin('alpha', marketSource('15', '1.0.0'))])
    let release: (rows: ReturnType<typeof latestRows>) => void = () => {}
    vi.mocked(fetchMarketLatestVersions)
      .mockImplementationOnce(() => new Promise((resolve) => { release = resolve }))
    const store = usePluginUpdatesStore()
    const boot = store.checkOnBoot()
    await vi.waitFor(() => expect(fetchMarketLatestVersions).toHaveBeenCalledTimes(1))

    // Opened from the toolbar mid-check (its own forced check queues), then closed.
    void store.openFromButton()
    store.closePopup()
    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(latestRows([[15, '1.1.0']]))
    release(latestRows([[15, '1.1.0']]))
    await boot

    // Regression guard: the boot check used to pop the window back open.
    expect(store.candidates).toHaveLength(1)
    expect(store.popupOpen).toBe(false)
  })

  it('decides the boot popup only after a queued re-check has run', async () => {
    setPlugins([plugin('alpha', marketSource('15', '1.0.0'))])
    let releaseFirst: (rows: ReturnType<typeof latestRows>) => void = () => {}
    vi.mocked(fetchMarketLatestVersions)
      .mockImplementationOnce(() => new Promise((resolve) => { releaseFirst = resolve }))
      .mockResolvedValueOnce(latestRows([[15, '1.1.0']]))

    const store = usePluginUpdatesStore()
    const boot = store.checkOnBoot()
    await vi.waitFor(() => expect(fetchMarketLatestVersions).toHaveBeenCalledTimes(1))

    setPlugins([plugin('alpha', marketSource('15', '1.1.0'))])
    await store.check({ force: true })
    releaseFirst(latestRows([[15, '1.1.0']]))
    await boot

    expect(store.candidates).toEqual([])
    expect(store.popupOpen).toBe(false)
  })

  it('keeps a known candidate when a later lookup omits that plugin', async () => {
    setPlugins([
      plugin('alpha', marketSource('15', '1.0.0')),
      plugin('beta', marketSource('18', '1.0.0')),
    ])
    vi.mocked(fetchMarketLatestVersions)
      .mockResolvedValueOnce(latestRows([[15, '1.1.0'], [18, '1.1.0']]))
      .mockResolvedValueOnce(latestRows([[18, '1.1.0']])) // alpha omitted
    const store = usePluginUpdatesStore()
    await store.check()
    await store.check({ force: true })

    // Regression guard: a transient partial response used to make alpha's
    // confirmed update vanish from the list.
    expect(store.candidates.map((c) => c.pluginId).sort()).toEqual(['alpha', 'beta'])
    expect(store.unresolved).toBe(1)
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

  it('clears stale candidates when the list is successfully empty', async () => {
    setPlugins([plugin('alpha', marketSource('15', '1.0.0'))])
    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(latestRows([[15, '1.1.0']]))
    const store = usePluginUpdatesStore()
    await store.check()
    expect(store.candidates).toHaveLength(1)

    // Everything got uninstalled. `error` stays null, so this is a real
    // no-target result rather than a failed fetch.
    setPlugins([])
    await store.check({ force: true })

    expect(store.candidates).toEqual([])
    expect(store.unresolved).toBe(0)
    expect(store.checkFailed).toBe(false)
  })

  it('keeps the previous snapshot when the plugin summaries cannot be fetched', async () => {
    setPlugins([plugin('alpha', marketSource('15', '1.0.0'))])
    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(latestRows([[15, '1.1.0']]))
    const store = usePluginUpdatesStore()
    await store.check()
    expect(store.candidates).toHaveLength(1)

    setPlugins([])
    mocks.pluginStore.fetchPluginSummaries.mockRejectedValueOnce(new Error('offline'))
    await store.check({ force: true })

    expect(store.candidates.map((candidate) => candidate.pluginId)).toEqual(['alpha'])
    expect(store.checkFailed).toBe(true)
  })

  it('loads only plugin summaries when the boot check finds no snapshot', async () => {
    mocks.pluginStore.fetchPluginSummaries.mockImplementationOnce(async () => {
      setPlugins([plugin('alpha', marketSource('15', '1.0.0'))])
    })
    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(latestRows([[15, '1.1.0']]))

    const store = usePluginUpdatesStore()
    await store.check()

    expect(mocks.pluginStore.fetchPluginSummaries).toHaveBeenCalledTimes(1)
    expect(store.candidates.map((candidate) => candidate.pluginId)).toEqual(['alpha'])
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

  it('drops a candidate whose plugin was removed since the check, without posting', async () => {
    const fetchMock = mockFetch((url) => {
      if (url.startsWith('/market/bridge-token')) return { status: 200, body: { bridge_token: 'tok' } }
      if (url.startsWith('/market/install')) return { status: 200, body: {} }
      return undefined
    })
    const store = await seedOneCandidate()
    // Deleted from the plugin list, which refreshes the plugin store only.
    setPlugins([plugin('beta', marketSource('18', '1.0.0'))])

    await expect(store.updateOne('alpha')).resolves.toBe(false)
    // Regression guard: the stale row used to post an upgrade for a plugin that
    // is gone, then linger as "update in the Market".
    expect(installBodies(fetchMock)).toEqual([])
    expect(store.candidates).toEqual([])
    expect(fetchMarketPluginVersions).not.toHaveBeenCalled()
  })

  it('installs the newest usable release, not a withdrawn or superseded one', async () => {
    vi.mocked(fetchMarketPluginVersions).mockResolvedValue([
      { ...release('1.1.0'), yanked_at: '2026-01-03T00:00:00Z' },
      { ...release('1.2.0'), package_sha256: 'c'.repeat(64) },
    ])
    const fetchMock = mockFetch((url) => {
      if (url.startsWith('/market/bridge-token')) return { status: 200, body: { bridge_token: 'tok' } }
      if (url.startsWith('/market/install')) return { status: 200, body: {} }
      return undefined
    })
    const store = await seedOneCandidate() // detected 1.1.0

    await expect(store.updateOne('alpha')).resolves.toBe(true)
    // Regression guard: the checked 1.1.0 was installed although it had been
    // withdrawn and 1.2.0 was available.
    expect(installBodies(fetchMock)).toEqual([
      expect.objectContaining({ version: '1.2.0', package_sha256: 'c'.repeat(64) }),
    ])
  })

  it('never downgrades a plugin that was upgraded elsewhere during the lookup', async () => {
    const fetchMock = mockFetch(() => undefined)
    const store = await seedOneCandidate() // 1.0.0 → 1.1.0
    vi.mocked(fetchMarketPluginVersions).mockImplementation(async () => {
      // Meanwhile upgraded to 1.0.5 from the Market page; 1.1.0 got withdrawn.
      setPlugins([plugin('alpha', marketSource('15', '1.0.5'))])
      return [{ ...release('1.1.0'), yanked_at: '2026-01-03T00:00:00Z' }, release('1.0.1')]
    })

    await expect(store.updateOne('alpha')).resolves.toBe(false)
    // Regression guard: 1.0.1 was measured against the checked 1.0.0 and would
    // have been installed over 1.0.5.
    expect(installBodies(fetchMock)).toEqual([])
    expect(store.candidates).toEqual([])
  })

  it('drops the row when the channel no longer offers anything newer', async () => {
    vi.mocked(fetchMarketPluginVersions).mockResolvedValue([
      { ...release('1.1.0'), yanked_at: '2026-01-03T00:00:00Z' },
      release('1.0.0'),
    ])
    const fetchMock = mockFetch(() => undefined)
    const store = await seedOneCandidate()

    await expect(store.updateOne('alpha')).resolves.toBe(false)
    expect(installBodies(fetchMock)).toEqual([])
    expect(store.candidates).toEqual([])
  })

  it('does not carry a manual-only verdict onto a different install', async () => {
    vi.mocked(fetchMarketPluginVersions).mockResolvedValue([release('1.1.0')])
    mockFetch((url) => {
      if (url.startsWith('/market/bridge-token')) return { status: 200, body: { bridge_token: 'tok' } }
      if (url.startsWith('/market/install')) {
        return { status: 409, body: { detail: { code: 'override_confirmation_required' } } }
      }
      return undefined
    })
    const store = await seedOneCandidate()
    await store.updateOne('alpha')
    expect(store.candidates[0]!.needsManualUpgrade).toBe(true)

    // Same local id, now installed from beta.
    setPlugins([plugin('alpha', marketSource('15', '1.0.0', 'beta'))])
    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(latestRows([[15, '1.1.0', 'beta']]))
    await store.check({ force: true })
    // Regression guard: the stable install's verdict disabled the beta row.
    expect(store.candidates[0]!.channel).toBe('beta')
    expect(store.candidates[0]!.needsManualUpgrade).toBe(false)
  })

  it('drops a candidate whose plugin switched channel since the check', async () => {
    const fetchMock = mockFetch(() => undefined)
    const store = await seedOneCandidate()
    // Same local and Market id, now installed from beta.
    setPlugins([plugin('alpha', marketSource('15', '1.0.0', 'beta'))])

    await expect(store.updateOne('alpha')).resolves.toBe(false)
    // Regression guard: the stale stable candidate used to pass the preflight
    // and pull the plugin back onto the stable channel.
    expect(installBodies(fetchMock)).toEqual([])
    expect(store.candidates).toEqual([])
  })

  it('drops a candidate that was already upgraded elsewhere', async () => {
    const fetchMock = mockFetch(() => undefined)
    const store = await seedOneCandidate()
    setPlugins([plugin('alpha', marketSource('15', '1.1.0'))])

    await expect(store.updateOne('alpha')).resolves.toBe(false)
    expect(installBodies(fetchMock)).toEqual([])
    expect(store.candidates).toEqual([])
  })

  it('does not re-offer an installed release when the registry sync failed', async () => {
    vi.mocked(fetchMarketPluginVersions).mockResolvedValue([release('1.1.0')])
    mockFetch((url) => {
      if (url.startsWith('/market/bridge-token')) return { status: 200, body: { bridge_token: 'tok' } }
      if (url.startsWith('/market/install')) return { status: 200, body: {} }
      return undefined
    })
    const store = await seedOneCandidate()
    // The backend upgrade succeeds, but the refresh throws before the plugin
    // list is refetched: it still says alpha is on 1.0.0.
    mocks.pluginStore.syncRegistryAndFetchSummaries.mockRejectedValueOnce(new Error('registry down'))

    // The upgrade itself did happen, so it is still reported as a success.
    await expect(store.updateOne('alpha')).resolves.toBe(true)

    await store.check({ force: true })
    // Regression guard: the stale list used to put 1.1.0 straight back.
    expect(store.candidates).toEqual([])

    // A genuinely newer release is still offered, measured from 1.1.0.
    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(latestRows([[15, '1.2.0']]))
    await store.check({ force: true })
    expect(store.candidates.map((c) => `${c.currentVersion}->${c.latestVersion}`)).toEqual(['1.1.0->1.2.0'])
  })

  it('trusts the plugin list again once it shows anything but the replaced version', async () => {
    vi.mocked(fetchMarketPluginVersions).mockResolvedValue([release('1.1.0')])
    mockFetch((url) => {
      if (url.startsWith('/market/bridge-token')) return { status: 200, body: { bridge_token: 'tok' } }
      if (url.startsWith('/market/install')) return { status: 200, body: {} }
      return undefined
    })
    const store = await seedOneCandidate()
    mocks.pluginStore.syncRegistryAndFetchSummaries.mockRejectedValueOnce(new Error('registry down'))
    await expect(store.updateOne('alpha')).resolves.toBe(true)

    // Later reinstalled lower from the Market page; a 1.0.5 release appears.
    setPlugins([plugin('alpha', marketSource('15', '0.9.0'))])
    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(latestRows([[15, '1.0.5']]))
    await store.check({ force: true })
    // Regression guard: the remembered 1.1.0 used to mask the real 0.9.0 and
    // hide this update for the rest of the session.
    expect(store.candidates.map((c) => `${c.currentVersion}->${c.latestVersion}`)).toEqual(['0.9.0->1.0.5'])
  })

  it('ignores the remembered install once the plugin comes from another channel', async () => {
    vi.mocked(fetchMarketPluginVersions).mockResolvedValue([release('1.1.0')])
    mockFetch((url) => {
      if (url.startsWith('/market/bridge-token')) return { status: 200, body: { bridge_token: 'tok' } }
      if (url.startsWith('/market/install')) return { status: 200, body: {} }
      return undefined
    })
    const store = await seedOneCandidate()
    mocks.pluginStore.syncRegistryAndFetchSummaries.mockRejectedValueOnce(new Error('registry down'))
    await expect(store.updateOne('alpha')).resolves.toBe(true)

    // Reinstalled from beta at the very version the stable upgrade replaced.
    setPlugins([plugin('alpha', marketSource('15', '1.0.0', 'beta'))])
    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(latestRows([[15, '1.0.5', 'beta']]))
    await store.check({ force: true })
    // Regression guard: matching on the version string alone reused the stable
    // record and hid this beta update.
    expect(store.candidates.map((c) => `${c.channel}:${c.currentVersion}->${c.latestVersion}`))
      .toEqual(['beta:1.0.0->1.0.5'])
  })

  it('keeps masking a still-stale list across chained upgrades', async () => {
    vi.mocked(fetchMarketPluginVersions)
      .mockResolvedValueOnce([release('1.1.0')])
      .mockResolvedValueOnce([release('1.2.0')])
    mockFetch((url) => {
      if (url.startsWith('/market/bridge-token')) return { status: 200, body: { bridge_token: 'tok' } }
      if (url.startsWith('/market/install')) return { status: 200, body: {} }
      return undefined
    })
    const store = await seedOneCandidate()
    mocks.pluginStore.syncRegistryAndFetchSummaries.mockRejectedValue(new Error('registry down'))
    await expect(store.updateOne('alpha')).resolves.toBe(true)

    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(latestRows([[15, '1.2.0']]))
    await store.check({ force: true })
    await expect(store.updateOne('alpha')).resolves.toBe(true)

    // The list still says 1.0.0 after both failed syncs.
    await store.check({ force: true })
    expect(store.candidates).toEqual([])
  })

  it('stays busy until the registry refresh after an upgrade has finished', async () => {
    vi.mocked(fetchMarketPluginVersions).mockResolvedValue([release('1.1.0')])
    mockFetch((url) => {
      if (url.startsWith('/market/bridge-token')) return { status: 200, body: { bridge_token: 'tok' } }
      if (url.startsWith('/market/install')) return { status: 200, body: {} }
      return undefined
    })
    const store = await seedOneCandidate()
    let busyDuringSync: boolean | null = null
    mocks.pluginStore.syncRegistryAndFetchSummaries.mockImplementation(async () => {
      busyDuringSync = store.busy
      return { registryRefreshed: true, warningMessage: null }
    })

    await expect(store.updateOne('alpha')).resolves.toBe(true)
    // Regression guard: the row used to be dropped before the sync, so a
    // refresh during it could re-read the old list and re-offer the version.
    expect(busyDuringSync).toBe(true)
    expect(store.candidates).toEqual([])
    expect(store.busy).toBe(false)
  })

  it('upgrades through the bridge, then drops the candidate', async () => {
    vi.mocked(fetchMarketPluginVersions).mockResolvedValue([release('1.1.0')])
    const fetchMock = mockFetch((url) => {
      if (url.startsWith('/market/bridge-token')) return { status: 200, body: { bridge_token: 'tok' } }
      if (url.startsWith('/market/install')) return { status: 200, body: { task_id: 'task-1' } }
      if (url.startsWith('/market/tasks/task-1')) {
        return { status: 200, body: { status: 'completed', stage: 'completed', progress: 1 } }
      }
      return undefined
    })

    const store = await seedOneCandidate()
    expect(store.completedUpgrades).toBe(0)
    await expect(store.updateOne('alpha')).resolves.toBe(true)

    expect(store.candidates).toEqual([])
    // The Market page watches this to refresh its own installed snapshot.
    expect(store.completedUpgrades).toBe(1)
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
    expect(mocks.pluginStore.syncRegistryAndFetchSummaries).toHaveBeenCalled()
  })

  it('reports a rollback code when the task fails', async () => {
    vi.mocked(fetchMarketPluginVersions).mockResolvedValue([release('1.1.0')])
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
    vi.mocked(fetchMarketPluginVersions).mockResolvedValue([release('1.1.0')])
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

  it('routes a GitHub release package through the configured mirror', async () => {
    const githubUrl = 'https://github.com/neko/alpha/releases/download/v1.1.0/alpha.neko-plugin'
    vi.mocked(fetchMarketPluginVersions).mockResolvedValue([
      { ...release('1.1.0'), package_url: githubUrl },
    ])
    const fetchMock = mockFetch((url) => {
      if (url.startsWith('/market/bridge-token')) return { status: 200, body: { bridge_token: 'tok' } }
      if (url.startsWith('/market/install')) return { status: 200, body: {} }
      return undefined
    })
    const mirror = useGithubMirrorSource()
    const previousMode = mirror.mode.value
    const previousSource = mirror.specifiedSourceId.value
    mirror.setMode('specified')
    mirror.setSpecifiedSourceId('gh-proxy-com')

    try {
      const store = await seedOneCandidate()
      await expect(store.updateOne('alpha')).resolves.toBe(true)

      // Regression guard: the popup used to submit the canonical GitHub URL,
      // which fails wherever GitHub itself is unreachable.
      expect(installBodies(fetchMock)).toEqual([
        expect.objectContaining({
          package_url: `https://gh-proxy.com/${githubUrl}`,
          canonical_package_url: githubUrl,
        }),
      ])
    } finally {
      mirror.setMode(previousMode)
      mirror.setSpecifiedSourceId(previousSource)
    }
  })

  it('reports a bridge transport failure as an install failure, not as pairing', async () => {
    vi.mocked(fetchMarketPluginVersions).mockResolvedValue([release('1.1.0')])
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/market/bridge-token')) {
        return new Response(JSON.stringify({ bridge_token: 'tok' }), { status: 200 })
      }
      throw new TypeError('Failed to fetch')
    }))

    const store = await seedOneCandidate()
    await expect(store.updateOne('alpha')).resolves.toBe(false)

    expect(store.candidates[0]!.status).toBe('failed')
    expect(store.candidates[0]!.errorKey).toBe('market.installFailed')
  })

  it('clears its own finished task before the next upgrade starts', async () => {
    setPlugins([
      plugin('alpha', marketSource('15', '1.0.0')),
      plugin('beta', marketSource('18', '1.0.0')),
    ])
    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(latestRows([[15, '1.1.0'], [18, '1.1.0']]))
    vi.mocked(fetchMarketPluginVersions)
      .mockResolvedValueOnce([release('1.1.0')])
      .mockResolvedValue(null)
    mockFetch((url) => {
      if (url.startsWith('/market/bridge-token')) return { status: 200, body: { bridge_token: 'tok' } }
      if (url.startsWith('/market/install')) return { status: 200, body: { task_id: 'task-a' } }
      if (url.startsWith('/market/tasks/task-a')) {
        return { status: 200, body: { status: 'completed', stage: 'completed', progress: 1 } }
      }
      return undefined
    })
    const store = usePluginUpdatesStore()
    await store.check()
    const installTask = useMarketInstallTaskStore()

    await expect(store.updateOne('alpha')).resolves.toBe(true)
    expect(installTask.done).toBe(true)
    expect(installTask.owner).toBe('float')

    // beta fails in its preflight, before any task is tracked.
    await expect(store.updateOne('beta')).resolves.toBe(false)
    // Regression guard: alpha's "completed" panel used to stay up beside
    // beta's failure until the popup was closed.
    expect(installTask.task).toBeNull()
    expect(installTask.reservation).toBeNull()
  })

  it('keeps a failed upgrade flagged across a later re-check', async () => {
    vi.mocked(fetchMarketPluginVersions).mockResolvedValue(null)
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
    vi.mocked(fetchMarketPluginVersions).mockImplementation(async (pluginId) => (
      String(pluginId) === '18' ? null : [release('1.1.0', Number(pluginId))]
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

  it('refuses to POST while an update check is in flight', async () => {
    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(latestRows([[15, '1.1.0']]))
    const fetchMock = mockFetch((url) => {
      if (url.startsWith('/market/bridge-token')) return { status: 200, body: { bridge_token: 'tok' } }
      return undefined
    })

    const store = await seedOneCandidate()
    // A check in flight will rebuild `candidates` when it finishes, orphaning
    // whatever object updateOne is mutating.
    store.$patch({ checking: true })

    await expect(store.updateOne('alpha')).resolves.toBe(false)
    expect(installBodies(fetchMock)).toEqual([])
    expect(store.candidates[0]!.status).toBe('idle')
  })

  it('refuses to POST while another surface owns an install task', async () => {
    vi.mocked(fetchMarketLatestVersions).mockResolvedValue(latestRows([[15, '1.1.0']]))
    const fetchMock = mockFetch((url) => {
      if (url.startsWith('/market/bridge-token')) return { status: 200, body: { bridge_token: 'tok' } }
      return undefined
    })

    const store = await seedOneCandidate()
    // A Market-panel install is in flight: the shared store says so.
    useMarketInstallTaskStore().$patch({
      task: { task_id: 'panel-task', status: 'downloading', stage: 'download', progress: 0.3 },
    })

    await expect(store.updateOne('alpha')).resolves.toBe(false)
    expect(installBodies(fetchMock)).toEqual([])
    expect(store.candidates[0]!.status).toBe('idle')
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
    vi.mocked(fetchMarketPluginVersions).mockResolvedValue([release('1.1.0')])
    // First run moves the candidate to the manual path.
    await store.updateOne('alpha')
    expect(store.candidates[0]!.needsManualUpgrade).toBe(true)

    const callsBefore = installBodies(fetchMock).length
    await store.updateAll()
    expect(installBodies(fetchMock)).toHaveLength(callsBefore)
  })
})
