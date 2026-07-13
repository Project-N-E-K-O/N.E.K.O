import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { usePluginStore } from './plugin'
import { getPlugins, getPluginStatus, refreshPluginsRegistry, startPlugin } from '@/api/plugins'

vi.mock('@/i18n', () => ({
  getLocale: () => 'zh-CN',
}))

vi.mock('@/api/plugins', () => ({
  getPlugins: vi.fn(),
  getPluginStatus: vi.fn(),
  startPlugin: vi.fn(),
  stopPlugin: vi.fn(),
  reloadPlugin: vi.fn(),
  refreshPluginsRegistry: vi.fn(),
}))

function registryRefreshResult() {
  return {
    success: true,
    added: [],
    updated: [],
    removed: [],
    removed_running: [],
    unchanged: [],
    failed: [],
    scanned_count: 0,
  }
}

describe('plugin store registry refresh policy', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(getPlugins).mockResolvedValue({ plugins: [], message: '' })
    vi.mocked(getPluginStatus).mockResolvedValue({} as any)
    vi.mocked(startPlugin).mockResolvedValue({ success: true, plugin_id: 'demo', message: '' })
    vi.mocked(refreshPluginsRegistry).mockResolvedValue(registryRefreshResult())
  })

  it('runs the plugin list registry sync only once per manager window', async () => {
    const store = usePluginStore()

    const first = await store.ensurePluginListRegistrySynced()
    const second = await store.ensurePluginListRegistrySynced()

    expect(first?.registryRefreshed).toBe(true)
    expect(second).toBeNull()
    expect(store.pluginListRegistrySynced).toBe(true)
    expect(refreshPluginsRegistry).toHaveBeenCalledTimes(1)
    expect(getPlugins).toHaveBeenCalledTimes(1)
  })

  it('marks explicit registry syncs as satisfying the first plugin list open', async () => {
    const store = usePluginStore()

    await store.syncRegistryAndFetch()
    const initialOpenResult = await store.ensurePluginListRegistrySynced()

    expect(initialOpenResult).toBeNull()
    expect(store.pluginListRegistrySynced).toBe(true)
    expect(refreshPluginsRegistry).toHaveBeenCalledTimes(1)
    expect(getPlugins).toHaveBeenCalledTimes(1)
  })

  it('can defer lifecycle refreshes so batch operations refresh once afterward', async () => {
    const store = usePluginStore()

    await store.start('demo', { refresh: false })

    expect(startPlugin).toHaveBeenCalledWith('demo')
    expect(getPluginStatus).not.toHaveBeenCalled()
    expect(getPlugins).not.toHaveBeenCalled()
  })
})
