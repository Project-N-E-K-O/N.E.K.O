// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { createApp, defineComponent, h, nextTick } from 'vue'
import { usePluginConsentNotices } from './pluginConsentNotices'
import { localeLoadState } from '@/i18n'
import { usePluginConsentState } from '@/stores/pluginConsentState'
import { usePluginStore } from '@/stores/plugin'
import type { PluginConsentNotice } from '@/api/pluginConsent'

const api = vi.hoisted(() => ({
  getPluginConsentNotices: vi.fn(),
  getPluginConsent: vi.fn(),
  approvePluginConsent: vi.fn(),
  rejectPluginConsent: vi.fn(),
  revokePluginConsent: vi.fn(),
  analyzePluginCapabilities: vi.fn(),
}))
const notify = vi.hoisted(() => ({
  ElNotification: vi.fn(() => ({ close: vi.fn() })),
}))
vi.mock('@/api/pluginConsent', () => api)
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
  setPluginAutoStart: vi.fn(),
}))
vi.mock('@/api/config', () => ({
  getPluginConfigApplicationState: vi.fn().mockResolvedValue({ config_state: 'matched' }),
}))
vi.mock('@/i18n', async () => {
  const { reactive } = await import('vue')
  return {
    getLocale: () => 'en-US',
    i18n: { global: { t: (key: string) => key } },
    localeLoadState: reactive({ pending: null as string | null }),
  }
})
vi.mock('element-plus', async (importOriginal) => {
  const actual = await importOriginal<typeof import('element-plus')>()
  return { ...actual, ElNotification: notify.ElNotification }
})
vi.mock('@/components/plugin/PluginConsentNotice.vue', () => ({
  default: { name: 'PluginConsentNotice' },
}))

const apps: Array<ReturnType<typeof createApp>> = []
const flush = async () => {
  for (let n = 0; n < 10; n++) await nextTick()
  await vi.dynamicImportSettled()
}
function mount() {
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp(
    defineComponent({
      setup() {
        usePluginConsentNotices()
        return () => h('div')
      },
    }),
  )
  app.mount(root)
  apps.push(app)
  return app
}
function notice(id: string, extras: Partial<PluginConsentNotice> = {}): PluginConsentNotice {
  return {
    plugin_id: id,
    name: id,
    version: '1',
    unconfirmed: ['memory.read'],
    upgraded: false,
    analysis_status: 'completed',
    ...extras,
  }
}
function runningAnalyzing(id: string, state: 'analyzing' | 'confirm' = 'analyzing') {
  usePluginStore().pluginSummaries = [
    { id, name: id, description: '', version: '1', status: 'running' },
  ]
  usePluginConsentState().seedSummaries([
    { id, version: '1', consent: { state, decision: null, upgraded: false } },
  ])
}
beforeEach(() => {
  setActivePinia(createPinia())
  for (const mock of Object.values(api)) mock.mockReset()
  api.getPluginConsentNotices.mockResolvedValue([])
  notify.ElNotification.mockClear()
  localeLoadState.pending = null
})
afterEach(() => {
  apps.splice(0).forEach((app) => app.unmount())
  document.body.innerHTML = ''
})

describe('usePluginConsentNotices', () => {
  it('fetches notices on mount and opens one notification', async () => {
    api.getPluginConsentNotices.mockResolvedValue([notice('a')])
    mount()
    await flush()
    expect(api.getPluginConsentNotices).toHaveBeenCalledTimes(1)
    expect(notify.ElNotification).toHaveBeenCalledTimes(1)
  })

  it('waits for the locale before fetching', async () => {
    localeLoadState.pending = 'en-US'
    mount()
    await flush()
    expect(api.getPluginConsentNotices).not.toHaveBeenCalled()
    localeLoadState.pending = null
    await flush()
    expect(api.getPluginConsentNotices).toHaveBeenCalledTimes(1)
  })

  it('watches a running analyzing plugin and opens once it becomes confirm', async () => {
    const consentState = usePluginConsentState()
    const watchSpy = vi.spyOn(consentState, 'watchPlugin')
    runningAnalyzing('a')
    mount()
    await flush()
    expect(api.getPluginConsentNotices).toHaveBeenCalledTimes(1)
    expect(watchSpy).toHaveBeenCalledWith('a')
    expect(notify.ElNotification).not.toHaveBeenCalled()
    api.getPluginConsentNotices.mockResolvedValue([notice('a')])
    consentState.seedSummaries([
      { id: 'a', version: '1', consent: { state: 'confirm', decision: null, upgraded: false } },
    ])
    await flush()
    expect(api.getPluginConsentNotices).toHaveBeenCalledTimes(2)
    expect(notify.ElNotification).toHaveBeenCalledTimes(1)
  })

  it('unwatches plugins when the page is left', async () => {
    const consentState = usePluginConsentState()
    const unwatchSpy = vi.spyOn(consentState, 'unwatchPlugin')
    runningAnalyzing('a')
    const app = mount()
    await flush()
    app.unmount()
    expect(unwatchSpy).toHaveBeenCalledWith('a')
  })

  it('reopens only when the unhandled set changes', async () => {
    api.getPluginConsentNotices.mockResolvedValue([notice('a')])
    runningAnalyzing('w')
    mount()
    await flush()
    expect(notify.ElNotification).toHaveBeenCalledTimes(1)
    api.getPluginConsentNotices.mockResolvedValue([notice('a'), notice('b')])
    usePluginConsentState().seedSummaries([
      { id: 'w', version: '1', consent: { state: 'confirm', decision: null, upgraded: false } },
    ])
    await flush()
    expect(notify.ElNotification).toHaveBeenCalledTimes(2)
  })

  it('discards older responses and closes when the latest response is empty', async () => {
    let finishFirst!: (notices: PluginConsentNotice[]) => void
    api.getPluginConsentNotices.mockReturnValueOnce(
      new Promise((resolve) => { finishFirst = resolve })
    )
    runningAnalyzing('w')
    mount()
    await flush()
    api.getPluginConsentNotices.mockResolvedValueOnce([notice('a')])
    runningAnalyzing('w', 'confirm')
    await flush()
    const notification = notify.ElNotification.mock.results[0]?.value
    expect(notification).toBeDefined()
    finishFirst([notice('old')])
    await flush()
    expect(notify.ElNotification).toHaveBeenCalledTimes(1)
    expect(notification?.close).not.toHaveBeenCalled()
    runningAnalyzing('w')
    await flush()
    runningAnalyzing('w', 'confirm')
    await flush()
    expect(notification?.close).toHaveBeenCalledOnce()
  })

  it('fetches again on a new visit', async () => {
    api.getPluginConsentNotices.mockResolvedValue([notice('a')])
    const app = mount()
    await flush()
    expect(notify.ElNotification).toHaveBeenCalledTimes(1)
    app.unmount()
    mount()
    await flush()
    expect(api.getPluginConsentNotices).toHaveBeenCalledTimes(2)
    expect(notify.ElNotification).toHaveBeenCalledTimes(2)
  })

  it('warns instead of failing silently when the fetch fails', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    api.getPluginConsentNotices.mockRejectedValue(new Error('down'))
    mount()
    await flush()
    expect(warn).toHaveBeenCalledOnce()
    warn.mockRestore()
  })
})
