// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { usePluginStore } from './plugin'
import {
  getPluginSummaries,
  getPluginStatus,
  reloadAllPlugins,
  reloadPlugin,
  startPlugin,
} from '@/api/plugins'
import { getPluginConfigApplicationState } from '@/api/config'
import type { PluginConfigApplicationState } from '@/api/config'
import { hasPendingReload, setPendingReload } from '@/utils/pendingReload'

vi.mock('@/i18n', () => ({
  getLocale: () => 'zh-CN',
  i18n: { global: { t: (key: string) => key } },
}))

vi.mock('@/api/plugins', () => ({
  getPlugins: vi.fn(),
  getPlugin: vi.fn(),
  getPluginSummaries: vi.fn(),
  getPluginStatus: vi.fn(),
  startPlugin: vi.fn(),
  stopPlugin: vi.fn(),
  reloadPlugin: vi.fn(),
  reloadAllPlugins: vi.fn(),
  refreshPluginsRegistry: vi.fn(),
}))

vi.mock('@/api/config', () => ({
  getPluginConfigApplicationState: vi.fn(),
}))

describe('plugin store reload bookkeeping', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    localStorage.clear()
    vi.mocked(getPluginSummaries).mockResolvedValue({ plugins: [], message: '' })
    vi.mocked(getPluginStatus).mockResolvedValue({} as never)
    vi.mocked(getPluginConfigApplicationState).mockImplementation(async (pluginId: string) => ({
      plugin_id: pluginId,
      config_state: 'matched',
    }))
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
    vi.mocked(getPluginSummaries).mockResolvedValue({
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
    await store.fetchPluginSummaries()

    await store.reloadAll({ refresh: false })

    // Only the plugin the server actually restarted matches its saved configuration again.
    expect(hasPendingReload('demo')).toBe(false)
    expect(hasPendingReload('other')).toBe(true)
  })

  it('clears a flagged plugin that the list snapshot does not know about', async () => {
    setPendingReload('demo', true)
    // The server restarts hosts from its own running set, so its answer can name a plugin
    // this window's list has not loaded (or has fallen behind on).
    vi.mocked(getPluginSummaries).mockResolvedValue({ plugins: [], message: '' })
    vi.mocked(reloadAllPlugins).mockResolvedValue({
      success: true,
      reloaded: ['demo'],
      failed: [],
      skipped: [],
      message: '',
    })
    const store = usePluginStore()
    await store.fetchPluginSummaries()

    await store.reloadAll({ refresh: false })

    expect(hasPendingReload('demo')).toBe(false)
  })

  it('reports a bulk reload as done even when the status refresh times out', async () => {
    vi.useFakeTimers()
    try {
      const result = { success: true, reloaded: ['demo'], failed: [], skipped: [], message: '' }
      vi.mocked(reloadAllPlugins).mockResolvedValue(result)
      // The full status request never answers, so the store's own timeout rejects it.
      vi.mocked(getPluginStatus).mockReturnValue(new Promise(() => {}) as never)
      const store = usePluginStore()

      const reloading = store.reloadAll()
      await vi.advanceTimersByTimeAsync(20_000)

      await expect(reloading).resolves.toEqual(result)
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps a flag that a profile write claimed during a bulk reload', async () => {
    setPendingReload('demo', true)
    vi.mocked(getPluginSummaries).mockResolvedValue({
      plugins: [{ id: 'demo' }] as never,
      message: '',
    })
    let releaseReload!: () => void
    vi.mocked(reloadAllPlugins).mockImplementation(
      () =>
        new Promise((resolve) => {
          releaseReload = () =>
            resolve({ success: true, reloaded: ['demo'], failed: [], skipped: [], message: '' })
        })
    )
    const store = usePluginStore()
    await store.fetchPluginSummaries()

    const reloading = store.reloadAll({ refresh: false })
    // A save lands while the bulk reload is in flight, so the restarted host may have read
    // the configuration from before it.
    setPendingReload('demo', true)
    releaseReload()
    await reloading

    expect(hasPendingReload('demo')).toBe(true)
  })

  it('does not let a legacy bulk reload clear a flag created after its baseline', async () => {
    let releaseReload!: (result: {
      success: boolean
      reloaded: string[]
      failed: { plugin_id: string; error: string }[]
      skipped: string[]
      message: string
    }) => void
    vi.mocked(reloadAllPlugins).mockImplementation(
      () =>
        new Promise((resolve) => {
          releaseReload = resolve
        })
    )
    vi.mocked(getPluginConfigApplicationState).mockRejectedValue({ response: { status: 404 } })
    const store = usePluginStore()

    const reloading = store.reloadAll({ refresh: false })
    // This plugin was absent from the bulk baseline. A save that lands while the
    // request is in flight must not be cleared by an old server's 404 fallback.
    setPendingReload('demo', true)
    releaseReload({ success: true, reloaded: ['demo'], failed: [], skipped: [], message: '' })
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
    vi.mocked(getPluginConfigApplicationState).mockResolvedValue({
      plugin_id: 'demo',
      config_state: 'pending',
    })
    const store = usePluginStore()

    await store.start('demo')

    expect(hasPendingReload('demo')).toBe(true)
  })

  it('clears the flag after a successful reload on an older server', async () => {
    setPendingReload('demo', true)
    vi.mocked(getPluginConfigApplicationState).mockRejectedValue({ response: { status: 404 } })
    const store = usePluginStore()

    await store.reload('demo')

    expect(hasPendingReload('demo')).toBe(false)
  })

  it('keeps the flag when application-state fails for a non-compatibility reason', async () => {
    setPendingReload('demo', true)
    vi.mocked(getPluginConfigApplicationState).mockRejectedValue(new Error('network failure'))
    const store = usePluginStore()

    await store.reload('demo')

    expect(hasPendingReload('demo')).toBe(true)
  })

  it('does not recreate a cleared flag from a stale pending response', async () => {
    setPendingReload('demo', true)
    let releaseReload!: () => void
    vi.mocked(reloadPlugin).mockImplementation(
      () =>
        new Promise((resolve) => {
          releaseReload = () => resolve({ success: true, plugin_id: 'demo', message: '' })
        })
    )
    vi.mocked(getPluginConfigApplicationState).mockResolvedValue({
      plugin_id: 'demo',
      config_state: 'pending',
    })
    const store = usePluginStore()

    const reloading = store.reload('demo', { refresh: false })
    setPendingReload('demo', false)
    releaseReload()
    await reloading

    expect(hasPendingReload('demo')).toBe(false)
  })

  it('ignores an older matched response after a newer pending response', async () => {
    setPendingReload('demo', true)
    let resolveFirst!: (value: PluginConfigApplicationState) => void
    let resolveSecond!: (value: PluginConfigApplicationState) => void
    vi.mocked(getPluginConfigApplicationState)
      .mockImplementationOnce(
        () => new Promise<PluginConfigApplicationState>((resolve) => {
          resolveFirst = resolve
        })
      )
      .mockImplementationOnce(
        () => new Promise<PluginConfigApplicationState>((resolve) => {
          resolveSecond = resolve
        })
      )
    const store = usePluginStore()

    const first = store.reload('demo', { refresh: false })
    await vi.waitFor(() => expect(getPluginConfigApplicationState).toHaveBeenCalledTimes(1))
    const second = store.reload('demo', { refresh: false })
    await vi.waitFor(() => expect(getPluginConfigApplicationState).toHaveBeenCalledTimes(2))

    resolveSecond({ plugin_id: 'demo', config_state: 'pending' })
    await second
    resolveFirst({ plugin_id: 'demo', config_state: 'matched' })
    await first

    expect(hasPendingReload('demo')).toBe(true)
  })
})
