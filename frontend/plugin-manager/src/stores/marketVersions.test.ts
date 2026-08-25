import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { fetchMarketLatestVersions } from '@/api/market'
import { useMarketVersionsStore } from './marketVersions'

vi.mock('@/api/market', () => ({
  fetchMarketLatestVersions: vi.fn(),
}))

describe('market versions store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('checks only installed ids, grouped by their installed channel', async () => {
    vi.mocked(fetchMarketLatestVersions).mockImplementation(async (ids, channel) =>
      ids.map((pluginId) => ({
        plugin_id: Number(pluginId),
        channel,
        version: channel === 'beta' ? '2.0.0-beta.1' : '1.2.0',
        published_at: '2026-01-01T00:00:00Z',
      })),
    )
    const store = useMarketVersionsStore()

    await store.ensureFresh([
      { pluginId: '15', channel: 'stable' },
      { pluginId: '18', channel: 'beta' },
      { pluginId: '15', channel: 'stable' },
    ])

    expect(fetchMarketLatestVersions).toHaveBeenCalledTimes(2)
    expect(fetchMarketLatestVersions).toHaveBeenCalledWith(['15'], 'stable')
    expect(fetchMarketLatestVersions).toHaveBeenCalledWith(['18'], 'beta')
    expect(store.latest('15', 'stable')).toBe('1.2.0')
    expect(store.latest('18', 'beta')).toBe('2.0.0-beta.1')
  })

  it('keeps the previous full snapshot when a later lookup fails', async () => {
    vi.mocked(fetchMarketLatestVersions).mockResolvedValueOnce([
      { plugin_id: 15, channel: 'stable', version: '1.2.0', published_at: '2026-01-01T00:00:00Z' },
    ])
    const store = useMarketVersionsStore()
    await store.ensureFresh([{ pluginId: '15', channel: 'stable' }])

    vi.mocked(fetchMarketLatestVersions).mockResolvedValueOnce(null)
    await store.ensureFresh([
      { pluginId: '15', channel: 'stable' },
      { pluginId: '18', channel: 'stable' },
    ])

    expect(fetchMarketLatestVersions).toHaveBeenLastCalledWith(['15', '18'], 'stable')
    expect(store.latest('15', 'stable')).toBe('1.2.0')
    expect(store.latest('18', 'stable')).toBeNull()
    expect(store.loadError).toContain('latest-version lookup failed')
  })
})
