// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { usePluginStore } from './plugin'
import { getPlugins, getPluginStatus, reloadPlugin } from '@/api/plugins'
import { hasPendingReload, setPendingReload } from '@/utils/pendingReload'

vi.mock('@/i18n', () => ({
  getLocale: () => 'zh-CN',
  i18n: { global: { t: (key: string) => key } },
}))

vi.mock('@/api/plugins', () => ({
  getPlugins: vi.fn(),
  getPluginStatus: vi.fn(),
  startPlugin: vi.fn(),
  stopPlugin: vi.fn(),
  reloadPlugin: vi.fn(),
  refreshPluginsRegistry: vi.fn(),
}))

describe('plugin store reload bookkeeping', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    localStorage.clear()
    vi.mocked(getPlugins).mockResolvedValue({ plugins: [], message: '' })
    vi.mocked(getPluginStatus).mockResolvedValue({} as never)
    vi.mocked(reloadPlugin).mockResolvedValue({ success: true, plugin_id: 'demo', message: '' })
  })

  afterEach(() => localStorage.clear())

  it('clears the pending reload flag for the reloaded plugin only', async () => {
    setPendingReload('demo', true)
    setPendingReload('other', true)
    const store = usePluginStore()

    await store.reload('demo')

    expect(hasPendingReload('demo')).toBe(false)
    expect(hasPendingReload('other')).toBe(true)
  })

  it('keeps the flag when the reload fails', async () => {
    setPendingReload('demo', true)
    vi.mocked(reloadPlugin).mockRejectedValue(new Error('reload failed'))
    const store = usePluginStore()

    await expect(store.reload('demo')).rejects.toThrow('reload failed')

    expect(hasPendingReload('demo')).toBe(true)
  })
})
