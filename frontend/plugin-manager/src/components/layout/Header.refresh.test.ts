// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import Header from './Header.vue'
import { usePluginStore, type PluginStatusRefreshResult } from '@/stores/plugin'
import { getPlugin, getPluginStatus, getPluginSummaries } from '@/api/plugins'

const messages = vi.hoisted(() => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn() }))
vi.mock('element-plus', () => ({ ElMessage: messages }))
vi.mock('@/i18n', () => ({ getLocale: () => 'en-US', i18n: { global: { t: (key: string) => key } } }))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
vi.mock('@/composables/useDarkMode', async () => {
  const { ref } = await import('vue')
  return {
    useDarkMode: () => ({
      isDark: ref(false),
      lightPalette: ref('neko'),
      darkPalette: ref('neko'),
      setDarkMode: vi.fn(),
      setLightPalette: vi.fn(),
      setDarkPalette: vi.fn(),
    }),
  }
})
vi.mock('@/components/common/LanguageSwitcher.vue', () => ({ default: { render: () => null } }))
vi.mock('@/api/plugins', () => ({
  getPlugins: vi.fn(), getPlugin: vi.fn(), getPluginSummaries: vi.fn(), getPluginStatus: vi.fn(),
  startPlugin: vi.fn(), stopPlugin: vi.fn(), reloadPlugin: vi.fn(), reloadAllPlugins: vi.fn(),
  refreshPluginsRegistry: vi.fn(), setPluginAutoStart: vi.fn(),
}))

let cleanup = () => {}
async function flushRefresh() {
  for (let i = 0; i < 30; i++) { await Promise.resolve(); await nextTick() }
}
async function refreshFromTitlebar(prepare?: (store: ReturnType<typeof usePluginStore>) => void, complete = true) {
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp(Header)
  app.use(createPinia())
  app.config.globalProperties.$t = (key: string) => key
  app.component('ElIcon', defineComponent({ setup: (_, { slots }) => () => h('span', slots.default?.()) }))
  app.mount(root)
  const store = usePluginStore()
  store.pluginStatuses = { demo: { status: 'running' } } as never
  prepare?.(store)
  cleanup = () => { app.unmount(); root.remove() }
  const button = root.querySelector('[aria-label="common.refresh"]') as HTMLButtonElement
  button.click()
  await flushRefresh()
  expect(button.disabled).toBe(!complete)
  return store
}

beforeEach(() => {
  vi.clearAllMocks()
  setActivePinia(createPinia())
  vi.mocked(getPluginSummaries).mockResolvedValue({ plugins: [], message: '' })
  vi.mocked(getPluginStatus).mockResolvedValue({ plugins: {} } as never)
  vi.spyOn(console, 'error').mockImplementation(() => {})
  vi.spyOn(console, 'warn').mockImplementation(() => {})
})
afterEach(() => { cleanup(); cleanup = () => {}; vi.restoreAllMocks(); vi.useRealTimers() })

describe('titlebar refresh through the real plugin store', () => {
  it('does not announce success when the status endpoint fails; retains the old snapshot', async () => {
    vi.mocked(getPluginStatus).mockRejectedValue(new Error('offline'))
    // The plugin still exists. A successful empty list instead confirms removal
    // and must prune its cached runtime state even when the status fetch fails.
    vi.mocked(getPluginSummaries).mockResolvedValueOnce({ plugins: [{ id: 'demo' }], message: '' } as never)
    const store = await refreshFromTitlebar()
    expect(messages.success).not.toHaveBeenCalled()
    expect(messages.warning).toHaveBeenCalledWith('messages.refreshPartialFailure')
    expect(store.pluginStatuses.demo?.status).toBe('running')
  })
  it('reports failure when neither data source refreshed', async () => {
    vi.mocked(getPluginStatus).mockRejectedValue(new Error('offline'))
    vi.mocked(getPluginSummaries).mockRejectedValue(new Error('offline'))
    await refreshFromTitlebar()
    expect(messages.error).toHaveBeenCalledWith('messages.operationFailed')
    expect(messages.success).not.toHaveBeenCalled()
  })
  it('announces success only after both data sources refresh', async () => {
    await refreshFromTitlebar()
    expect(messages.success).toHaveBeenCalledWith('messages.operationSuccess')
    expect(messages.warning).not.toHaveBeenCalled()
  })
  it('does not misreport failure when a newer status refresh has already succeeded', async () => {
    let release!: (value: never) => void
    vi.mocked(getPluginStatus).mockReturnValueOnce(new Promise(resolve => { release = resolve }))
      .mockResolvedValueOnce({ plugins: {} } as never)
    const store = await refreshFromTitlebar(undefined, false)
    await store.fetchPluginStatus(undefined, true)
    release({ plugins: {} } as never)
    await flushRefresh()
    expect(messages.error).not.toHaveBeenCalled()
    expect(messages.warning).not.toHaveBeenCalled()
    expect(messages.success).not.toHaveBeenCalled()
  })
  it.each(['single', 'full'] as const)('reports a partial failure when the replacing %s status query fails', async (scope) => {
    let release!: (value: never) => void
    vi.mocked(getPluginStatus).mockReturnValueOnce(new Promise(resolve => { release = resolve }))
      .mockRejectedValueOnce(new Error('replacement unavailable'))
    const store = await refreshFromTitlebar(undefined, false)
    // Lifecycle callers intentionally keep mutation success separate from the
    // follow-up query; they do not necessarily announce that query's failure.
    await store.fetchPluginStatus(scope === 'single' ? 'demo' : undefined, true)
    release({ plugins: {} } as never)
    await flushRefresh()
    expect(messages.warning).toHaveBeenCalledExactlyOnceWith('messages.refreshPartialFailure')
    expect(messages.success).not.toHaveBeenCalled()
  })
  it('does not assume an unfinished replacing query will succeed', async () => {
    let releaseOld!: (value: never) => void
    let releaseNew!: (value: never) => void
    vi.mocked(getPluginStatus)
      .mockReturnValueOnce(new Promise(resolve => { releaseOld = resolve }))
      .mockReturnValueOnce(new Promise(resolve => { releaseNew = resolve }))
    const store = await refreshFromTitlebar(undefined, false)
    const replacing = store.fetchPluginStatus('demo')
    releaseOld({ plugins: {} } as never)
    await flushRefresh()
    try {
      expect(messages.warning).toHaveBeenCalledExactlyOnceWith('messages.refreshPartialFailure')
      expect(messages.success).not.toHaveBeenCalled()
    } finally {
      releaseNew({ plugin_id: 'demo', source: 'main_process_synthetic', status: { status: 'running' } } as never)
      await replacing
    }
  })
  it('does not let an earlier replacement success hide the newest query failure', async () => {
    let release!: (value: never) => void
    vi.mocked(getPluginStatus)
      .mockReturnValueOnce(new Promise(resolve => { release = resolve }))
      .mockResolvedValueOnce({ plugins: {} } as never)
      .mockRejectedValueOnce(new Error('latest query failed'))
    const store = await refreshFromTitlebar(undefined, false)
    await store.fetchPluginStatus(undefined, true)
    await store.fetchPluginStatus('demo')
    release({ plugins: {} } as never)
    await flushRefresh()
    expect(messages.warning).toHaveBeenCalledExactlyOnceWith('messages.refreshPartialFailure')
  })
  it('keeps bounded failure feedback when the old query hangs and its replacement fails', async () => {
    vi.useFakeTimers()
    vi.mocked(getPluginStatus)
      .mockReturnValueOnce(new Promise(() => {}))
      .mockRejectedValueOnce(new Error('replacement unavailable'))
    const store = await refreshFromTitlebar(undefined, false)
    await store.fetchPluginStatus('demo')
    await vi.advanceTimersByTimeAsync(15_000)
    await flushRefresh()
    expect(messages.warning).toHaveBeenCalledExactlyOnceWith('messages.refreshPartialFailure')
    expect(messages.success).not.toHaveBeenCalled()
  })
  it('waits for every loaded detail request before reporting a partial failure', async () => {
    let release!: (value: never) => void
    vi.mocked(getPlugin).mockRejectedValueOnce(new Error('offline'))
      .mockReturnValueOnce(new Promise(resolve => { release = resolve }))
    await refreshFromTitlebar(store => {
      store.pluginDetails = { first: { id: 'first' }, second: { id: 'second' } } as never
    }, false)
    expect(messages.warning).not.toHaveBeenCalled()
    release({ id: 'second' } as never)
    await flushRefresh()
    expect(messages.warning).toHaveBeenCalledWith('messages.refreshPartialFailure')
  })
  it('reports partial refresh when a detail succeeds but another detail and status both fail', async () => {
    vi.mocked(getPlugin).mockResolvedValueOnce({ id: 'first', name: 'Updated' } as never)
      .mockRejectedValueOnce(new Error('second detail offline'))
    vi.mocked(getPluginStatus).mockRejectedValue(new Error('status offline'))
    const store = await refreshFromTitlebar(store => {
      store.pluginDetails = { first: { id: 'first', name: 'Old' }, second: { id: 'second', name: 'Kept' } } as never
    })
    expect(store.pluginDetails.first?.name).toBe('Updated')
    expect(store.pluginDetails.second?.name).toBe('Kept')
    expect(messages.warning).toHaveBeenCalledExactlyOnceWith('messages.refreshPartialFailure')
    expect(messages.error).not.toHaveBeenCalled()
    expect(messages.success).not.toHaveBeenCalled()
  })
  it('still rejects metadata refresh for callers that need to handle a partial failure', async () => {
    const store = usePluginStore()
    store.pluginDetails = { first: { id: 'first' }, second: { id: 'second' } } as never
    vi.mocked(getPlugin).mockResolvedValueOnce({ id: 'first' } as never)
      .mockRejectedValueOnce(new Error('second detail offline'))
    await expect(store.refreshLoadedPluginData()).rejects.toMatchObject({
      successfulRequests: 1, message: 'second detail offline',
    })
  })
  it('keeps background failures and timeouts resolved with an explicit failed result', async () => {
    const store = usePluginStore()
    vi.mocked(getPluginStatus).mockRejectedValueOnce(new Error('offline'))
    await expect(store.fetchPluginStatus()).resolves.toBe('failed')
    vi.useFakeTimers()
    vi.mocked(getPluginStatus).mockReturnValue(new Promise(() => {}))
    const pending = store.fetchPluginStatus()
    await vi.advanceTimersByTimeAsync(15_000)
    await expect(pending).resolves.toBe('failed')
  })
  it('settles a superseded request at its own timeout without invalidating the fresh snapshot', async () => {
    vi.useFakeTimers()
    const store = usePluginStore()
    vi.mocked(getPluginStatus).mockReturnValueOnce(new Promise(() => {}))
      .mockResolvedValueOnce({ plugins: { demo: { status: 'running' } } } as never)
    const old = store.fetchPluginStatus()
    await store.fetchPluginStatus(undefined, true)
    let oldResult: PluginStatusRefreshResult | undefined
    void old.then(result => { oldResult = result })
    await vi.advanceTimersByTimeAsync(15_000)
    expect(oldResult).toBe('superseded')
    expect(store.pluginStatuses.demo?.status).toBe('running')
    expect(store.pluginStatusSnapshotLoaded).toBe(true)
  })
})
