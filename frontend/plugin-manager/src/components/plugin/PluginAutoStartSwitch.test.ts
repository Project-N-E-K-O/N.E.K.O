// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import PluginAutoStartSwitch from './PluginAutoStartSwitch.vue'
import { usePluginStore } from '@/stores/plugin'

const apiMocks = vi.hoisted(() => ({
  getPlugins: vi.fn(),
  getPlugin: vi.fn(),
  getPluginSummaries: vi.fn(),
  getPluginStatus: vi.fn(),
  startPlugin: vi.fn(),
  stopPlugin: vi.fn(),
  reloadPlugin: vi.fn(),
  refreshPluginsRegistry: vi.fn(),
  setPluginAutoStart: vi.fn(),
}))

vi.mock('@/api/plugins', () => apiMocks)
vi.mock('@/i18n', () => ({ getLocale: () => 'en-US' }))
vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}))

const elementPlusMocks = vi.hoisted(() => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))
vi.mock('element-plus', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  ...elementPlusMocks,
}))

function stubSwitch(app: ReturnType<typeof createApp>) {
  app.component('el-switch', defineComponent({
    props: { modelValue: Boolean, loading: Boolean, disabled: Boolean },
    emits: ['change'],
    setup(props, { emit }) {
      return () => h('button', {
        'data-checked': String(props.modelValue),
        'data-loading': String(props.loading),
        disabled: props.disabled,
        onClick: () => emit('change', !props.modelValue),
      })
    },
  }))
}

async function flushPromises() {
  for (let i = 0; i < 8; i += 1) await nextTick()
}

function mount(autoStart: boolean, extra: Record<string, unknown> = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = usePluginStore()
  store.pluginSummaries = [{
    id: 'demo',
    name: 'Demo',
    description: 'Demo',
    version: '1.0.0',
    status: 'running',
    runtime_auto_start: autoStart,
    ...extra,
  } as never]
  const root = document.createElement('div')
  const app = createApp(PluginAutoStartSwitch, { pluginId: 'demo' })
  app.use(pinia)
  stubSwitch(app)
  app.mount(root)
  const button = () => root.querySelector<HTMLButtonElement>('[data-testid="plugin-auto-start-switch"]')!
  return { app, button, root }
}

describe('PluginAutoStartSwitch', () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach(mock => mock.mockReset())
    Object.values(elementPlusMocks.ElMessage).forEach(mock => mock.mockReset())
  })

  it('writes the auto-start preference without starting or stopping the plugin', async () => {
    let resolveRequest: (value: unknown) => void = () => {}
    apiMocks.setPluginAutoStart.mockImplementation(() => new Promise((resolve) => { resolveRequest = resolve }))
    const { app, button } = mount(true)
    apiMocks.getPluginSummaries.mockResolvedValue({ plugins: [{ id: 'demo', name: 'Demo', status: 'running', runtime_auto_start: false }] })

    expect(button().dataset.checked).toBe('true')
    button().click()
    await flushPromises()

    expect(apiMocks.setPluginAutoStart).toHaveBeenCalledWith('demo', false)
    expect(button().dataset.loading).toBe('true')
    expect(button().disabled).toBe(true)

    resolveRequest({ success: true, plugin_id: 'demo', auto_start: false })
    await vi.waitFor(() => expect(button().dataset.loading).toBe('false'))

    expect(button().dataset.checked).toBe('false')
    expect(elementPlusMocks.ElMessage.success).toHaveBeenCalledWith('messages.autoStartDisabled')
    expect(apiMocks.getPluginSummaries).toHaveBeenCalled()
    expect(apiMocks.getPluginStatus).not.toHaveBeenCalled()
    expect(apiMocks.startPlugin).not.toHaveBeenCalled()
    expect(apiMocks.stopPlugin).not.toHaveBeenCalled()
    app.unmount()
  })

  it('reports errors the interceptor does not surface and clears loading', async () => {
    apiMocks.setPluginAutoStart.mockRejectedValue({ response: { status: 404, data: {} } })
    const { app, button } = mount(false)

    button().click()
    await flushPromises()

    expect(apiMocks.setPluginAutoStart).toHaveBeenCalledWith('demo', true)
    expect(elementPlusMocks.ElMessage.error).toHaveBeenCalledTimes(1)
    expect(elementPlusMocks.ElMessage.success).not.toHaveBeenCalled()
    expect(button().dataset.loading).toBe('false')
    expect(button().dataset.checked).toBe('false')
    app.unmount()
  })

  it('keeps the confirmed preference when the follow-up refresh fails', async () => {
    apiMocks.setPluginAutoStart.mockResolvedValue({ success: true, plugin_id: 'demo', auto_start: false })
    apiMocks.getPluginStatus.mockResolvedValue({})
    apiMocks.getPluginSummaries.mockRejectedValue(new Error('offline'))
    const { app, button } = mount(true)
    usePluginStore().pluginDetails = {
      demo: { id: 'demo', name: 'Demo', description: 'Demo', version: '1.0.0', runtime_auto_start: true },
    }
    apiMocks.getPlugin.mockRejectedValue(new Error('offline'))
    await flushPromises()
    expect(button().dataset.checked).toBe('true')

    button().click()
    await vi.waitFor(() => expect(elementPlusMocks.ElMessage.success).toHaveBeenCalledWith('messages.autoStartDisabled'))
    await flushPromises()

    expect(button().dataset.loading).toBe('false')
    expect(button().dataset.checked).toBe('false')
    app.unmount()
  })

  it('ignores a detail request that was already in flight before the save', async () => {
    let resolveStale: (value: unknown) => void = () => {}
    apiMocks.getPlugin.mockImplementationOnce(() => new Promise((resolve) => { resolveStale = resolve }))
    apiMocks.setPluginAutoStart.mockResolvedValue({ success: true, plugin_id: 'demo', auto_start: false })
    apiMocks.getPluginSummaries.mockRejectedValue(new Error('offline'))
    const { app, button } = mount(true)
    const store = usePluginStore()
    store.pluginDetails = {
      demo: { id: 'demo', name: 'Demo', description: 'Demo', version: '1.0.0', runtime_auto_start: true },
    }
    const stale = store.fetchPluginDetail('demo')
    apiMocks.getPlugin.mockRejectedValue(new Error('offline'))
    await flushPromises()

    button().click()
    await vi.waitFor(() => expect(apiMocks.getPluginSummaries).toHaveBeenCalled())
    resolveStale({ id: 'demo', name: 'Demo', description: 'Demo', version: '1.0.0', runtime_auto_start: true })
    await stale
    await vi.waitFor(() => expect(elementPlusMocks.ElMessage.success).toHaveBeenCalledWith('messages.autoStartDisabled'))
    await flushPromises()

    expect(button().dataset.checked).toBe('false')
    app.unmount()
  })

  it('ignores a summary request that was already in flight before the save', async () => {
    let resolveStale: (value: unknown) => void = () => {}
    apiMocks.getPluginSummaries.mockImplementationOnce(() => new Promise((resolve) => { resolveStale = resolve }))
    apiMocks.setPluginAutoStart.mockResolvedValue({ success: true, plugin_id: 'demo', auto_start: false })
    const { app } = mount(true)
    const store = usePluginStore()
    const stale = store.fetchPluginSummaries(true)

    await store.setAutoStart('demo', false, { refresh: false })
    resolveStale({ plugins: [
      { id: 'demo', name: 'Demo', description: 'Demo', version: '1.0.0', runtime_auto_start: true },
      { id: 'other', name: 'Other', description: 'Other', version: '1.0.0', runtime_auto_start: true },
    ] })
    await stale

    // The response still publishes (other plugins appear) with the saved value kept.
    expect(store.getPluginById('demo')?.autoStart).toBe(false)
    expect(store.getPluginById('other')?.autoStart).toBe(true)
    app.unmount()
  })

  it('shows a read-only switch for development plugins', async () => {
    const { app, button } = mount(false, { source: 'development' })
    await flushPromises()

    expect(button().disabled).toBe(true)
    button().click()
    await flushPromises()
    expect(apiMocks.setPluginAutoStart).not.toHaveBeenCalled()
    app.unmount()
  })

  it('explains that enabling autostart also enables a disabled plugin', async () => {
    apiMocks.setPluginAutoStart.mockResolvedValue({ success: true, plugin_id: 'demo', auto_start: true })
    apiMocks.getPluginSummaries.mockRejectedValue(new Error('offline'))
    const { app, button, root } = mount(false, { runtime_enabled: false })
    expect(button().dataset.checked).toBe('false')
    expect(root.textContent).toContain('plugins.autoStartDisabledHint')
    button().click()
    await vi.waitFor(() => expect(elementPlusMocks.ElMessage.success).toHaveBeenCalled())
    expect(apiMocks.setPluginAutoStart).toHaveBeenCalledWith('demo', true)
    expect(button().dataset.checked).toBe('true')
    expect(usePluginStore().getPluginById('demo')?.runtime_enabled).toBe(true)
    expect(root.textContent).not.toContain('plugins.autoStartDisabledHint')
    expect(apiMocks.startPlugin).not.toHaveBeenCalled()
    app.unmount()
  })

  it.each([true, false])('preserves an initial detail load after saving (refresh=%s)', async (refresh) => {
    let resolveDetail: (value: unknown) => void = () => {}
    apiMocks.getPlugin.mockImplementationOnce(() => new Promise((resolve) => { resolveDetail = resolve }))
    apiMocks.getPlugin.mockRejectedValue(new Error('offline'))
    apiMocks.getPluginSummaries.mockResolvedValue({ plugins: [
      { id: 'demo', name: 'Demo', description: 'Demo', version: '1.0.0', runtime_auto_start: false },
    ] })
    apiMocks.setPluginAutoStart.mockResolvedValue({ success: true, plugin_id: 'demo', auto_start: false })
    const { app } = mount(true)
    const store = usePluginStore()
    const initial = store.ensurePlugin('demo')
    const save = store.setAutoStart('demo', false, { refresh })
    await flushPromises()
    expect(apiMocks.getPlugin).toHaveBeenCalledTimes(1)
    resolveDetail({ id: 'demo', name: 'Demo', description: 'Loaded detail', version: '1.0.0', runtime_auto_start: true })
    const [detail] = await Promise.all([initial, save])
    expect(detail?.description).toBe('Loaded detail')
    expect(detail?.runtime_auto_start).toBe(false)
    expect(store.getPluginById('demo')?.autoStart).toBe(false)
    app.unmount()
  })

  it('reuses the first in-flight summary during default save revalidation', async () => {
    let resolveSummary: (value: unknown) => void = () => {}
    apiMocks.getPluginSummaries.mockImplementationOnce(() => new Promise((resolve) => { resolveSummary = resolve }))
    // A superseding request would fail and discard the successful first load.
    apiMocks.getPluginSummaries.mockRejectedValue(new Error('offline'))
    apiMocks.getPlugin.mockRejectedValue(new Error('offline'))
    apiMocks.setPluginAutoStart.mockResolvedValue({ success: true, plugin_id: 'demo', auto_start: false })
    const { app } = mount(true)
    const store = usePluginStore()
    store.pluginDetails = {
      demo: { id: 'demo', name: 'Demo', description: 'Demo', version: '1.0.0', runtime_auto_start: true },
    }
    store.pluginSummaries = []
    const firstLoad = store.fetchPluginSummaries()
    const save = store.setAutoStart('demo', false)
    await vi.waitFor(() => expect(apiMocks.getPlugin).toHaveBeenCalled())
    expect(apiMocks.getPluginSummaries).toHaveBeenCalledTimes(1)
    resolveSummary({ plugins: [
      { id: 'demo', name: 'Demo', description: 'Demo', version: '1.0.0', runtime_auto_start: true },
      { id: 'other', name: 'Other', description: 'Other', version: '1.0.0', runtime_auto_start: true },
    ] })
    await Promise.all([firstLoad, save])
    expect(store.pluginSummaries.map(plugin => plugin.id)).toEqual(['demo', 'other'])
    expect(store.pluginSummaries[0]?.runtime_auto_start).toBe(false)
    expect(store.getPluginById('demo')?.autoStart).toBe(false)
    expect(apiMocks.getPluginStatus).not.toHaveBeenCalled()
    app.unmount()
  })

  it.each([
    { runtime_enabled: false },
    { autostart_pending: true },
    { runtime_enabled: false, autostart_pending: true },
  ])('keeps the saved preference on with a blocked hint and publishes cleared gates after enabling: %j', async (gates) => {
    apiMocks.setPluginAutoStart.mockResolvedValue({ success: true, plugin_id: 'demo', auto_start: true })
    const { app, button, root } = mount(true, gates)
    const store = usePluginStore()
    store.pluginDetails = {
      demo: { id: 'demo', name: 'Demo', description: 'Demo', version: '1.0.0', runtime_auto_start: true, ...gates },
    }
    let resolveStale: (value: unknown) => void = () => {}
    apiMocks.getPluginSummaries.mockImplementationOnce(() => new Promise((resolve) => { resolveStale = resolve }))
    const stale = store.fetchPluginSummaries(true)
    expect(button().dataset.checked).toBe('true')
    expect(root.textContent).toContain('plugins.autoStartBlockedHint')

    await store.setAutoStart('demo', true, { refresh: false })
    resolveStale({ plugins: [{ id: 'demo', name: 'Demo', description: 'Demo', version: '1.0.0', runtime_auto_start: true, ...gates }] })
    await stale
    await flushPromises()
    expect(button().dataset.checked).toBe('true')
    expect(root.textContent).not.toContain('plugins.autoStartBlockedHint')
    expect(store.pluginSummaries[0]?.runtime_enabled).toBe(true)
    expect(store.pluginSummaries[0]?.autostart_pending).toBe(false)
    expect(apiMocks.startPlugin).not.toHaveBeenCalled()
    expect(apiMocks.stopPlugin).not.toHaveBeenCalled()
    app.unmount()
  })

  it('clicking an on but blocked preference disables it without approving autostart', async () => {
    apiMocks.setPluginAutoStart.mockResolvedValue({ success: true, plugin_id: 'demo', auto_start: false })
    apiMocks.getPluginSummaries.mockRejectedValue(new Error('offline'))
    const { app, button, root } = mount(true, { autostart_pending: true })
    expect(button().dataset.checked).toBe('true')
    expect(root.textContent).toContain('plugins.autoStartBlockedHint')
    button().click()
    await vi.waitFor(() => expect(elementPlusMocks.ElMessage.success).toHaveBeenCalled())
    expect(apiMocks.setPluginAutoStart).toHaveBeenCalledWith('demo', false)
    expect(button().dataset.checked).toBe('false')
    expect(usePluginStore().getPluginById('demo')?.autostart_pending).toBe(true)
    app.unmount()
  })
})
