// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { usePluginStore } from './plugin'
import {
  getPlugins,
  getPluginStatus,
  reloadAllPlugins,
  reloadPlugin,
  startPlugin,
} from '@/api/plugins'
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
  reloadAllPlugins: vi.fn(),
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

  it('clears the flag when the plugin actually starts', async () => {
    setPendingReload('demo', true)
    vi.mocked(startPlugin).mockResolvedValue({
      success: true,
      plugin_id: 'demo',
      message: 'Plugin started successfully',
    })
    const store = usePluginStore()

    await store.start('demo')

    expect(hasPendingReload('demo')).toBe(false)
  })

  it('keeps a flag that a profile write claimed while the plugin was starting', async () => {
    setPendingReload('demo', true)
    let releaseStart!: () => void
    vi.mocked(startPlugin).mockImplementation(
      () =>
        new Promise((resolve) => {
          releaseStart = () =>
            resolve({ success: true, plugin_id: 'demo', message: 'Plugin started successfully' })
        })
    )
    const store = usePluginStore()

    const starting = store.start('demo')
    // The new host reads its saved configuration while starting, so a save that lands in
    // the meantime describes a configuration it cannot have read yet.
    setPendingReload('demo', true)
    releaseStart()
    await starting

    expect(hasPendingReload('demo')).toBe(true)
  })

  it('keeps a flag that a profile write claimed while the plugin was reloading', async () => {
    setPendingReload('demo', true)
    let releaseReload!: () => void
    vi.mocked(reloadPlugin).mockImplementation(
      () =>
        new Promise((resolve) => {
          releaseReload = () => resolve({ success: true, plugin_id: 'demo', message: '' })
        })
    )
    const store = usePluginStore()

    const reloading = store.reload('demo')
    setPendingReload('demo', true)
    releaseReload()
    await reloading

    expect(hasPendingReload('demo')).toBe(true)
  })

  it('clears the flag of every plugin a bulk reload restarted', async () => {
    setPendingReload('demo', true)
    setPendingReload('other', true)
    vi.mocked(getPlugins).mockResolvedValue({
      plugins: [{ id: 'demo' }, { id: 'other' }] as never,
      message: '',
    })
    vi.mocked(reloadAllPlugins).mockResolvedValue({
      success: true,
      reloaded: ['demo'],
      failed: [],
      skipped: ['other'],
      message: '',
    })
    const store = usePluginStore()
    await store.fetchPlugins()

    await store.reloadAll({ refresh: false })

    // Only the plugin the server actually restarted matches its saved configuration again.
    expect(hasPendingReload('demo')).toBe(false)
    expect(hasPendingReload('other')).toBe(true)
  })

  it('keeps a flag that a profile write claimed during a bulk reload', async () => {
    setPendingReload('demo', true)
    vi.mocked(getPlugins).mockResolvedValue({ plugins: [{ id: 'demo' }] as never, message: '' })
    let releaseReload!: () => void
    vi.mocked(reloadAllPlugins).mockImplementation(
      () =>
        new Promise((resolve) => {
          releaseReload = () =>
            resolve({ success: true, reloaded: ['demo'], failed: [], skipped: [], message: '' })
        })
    )
    const store = usePluginStore()
    await store.fetchPlugins()

    const reloading = store.reloadAll({ refresh: false })
    // A save lands while the bulk reload is in flight, so the restarted host may have read
    // the configuration from before it.
    setPendingReload('demo', true)
    releaseReload()
    await reloading

    expect(hasPendingReload('demo')).toBe(true)
  })

  it('keeps the flag when the server reports the plugin was already running', async () => {
    // That response does not restart the host or re-read the saved configuration,
    // so the new configuration is still not applied.
    setPendingReload('demo', true)
    vi.mocked(startPlugin).mockResolvedValue({
      success: true,
      plugin_id: 'demo',
      already_running: true,
      message: 'Plugin is already running',
    })
    const store = usePluginStore()

    await store.start('demo')

    expect(hasPendingReload('demo')).toBe(true)
  })
})
