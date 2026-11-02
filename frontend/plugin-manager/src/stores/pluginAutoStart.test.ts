import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { usePluginStore } from './plugin'
import { getPlugin, getPluginSummaries } from '@/api/plugins'
import type { PluginMeta } from '@/types/api'

vi.mock('@/i18n', () => ({
  getLocale: () => 'en-US',
  i18n: { global: { t: (key: string) => key } },
}))

vi.mock('@/api/plugins', () => ({
  getPlugin: vi.fn(),
  getPlugins: vi.fn(),
  getPluginSummaries: vi.fn(),
  getPluginStatus: vi.fn(),
  startPlugin: vi.fn(),
  stopPlugin: vi.fn(),
  reloadPlugin: vi.fn(),
  reloadAllPlugins: vi.fn(),
  refreshPluginsRegistry: vi.fn(),
  setPluginAutoStart: vi.fn(),
}))

const preferences: { name: string; runtime: Partial<PluginMeta>; expected: boolean }[] = [
  { name: 'omitted', runtime: {}, expected: false },
  { name: 'disabled', runtime: { runtime_auto_start: false }, expected: false },
  { name: 'enabled', runtime: { runtime_auto_start: true }, expected: true },
]

function plugin(runtime: Partial<PluginMeta>): PluginMeta {
  return { id: 'demo', name: 'Demo', description: '', version: '1.0.0', ...runtime }
}

describe('plugin store auto-start display state', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it.each(preferences)('shows an $name preference consistently in the list and lookup', async ({ runtime, expected }) => {
    vi.mocked(getPluginSummaries).mockResolvedValue({ plugins: [plugin(runtime)], message: '' })
    const store = usePluginStore()

    await store.fetchPluginSummaries()

    expect(store.pluginSummariesWithStatus[0]?.autoStart).toBe(expected)
    expect(store.getPluginById('demo')?.autoStart).toBe(expected)
  })

  it.each(preferences)('shows an $name preference when opening a detail directly', async ({ runtime, expected }) => {
    vi.mocked(getPlugin).mockResolvedValue(plugin(runtime))
    const store = usePluginStore()

    await store.fetchPluginDetail('demo')

    expect(store.getPluginById('demo')?.autoStart).toBe(expected)
  })
})
