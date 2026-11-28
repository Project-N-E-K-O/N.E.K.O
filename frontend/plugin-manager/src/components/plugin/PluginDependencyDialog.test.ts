// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { createApp, defineComponent, h, nextTick } from 'vue'
import PluginDependencyDialog from './PluginDependencyDialog.vue'
import { usePluginDependencyInstall } from '@/stores/pluginDependencyInstall'
import type { PythonDependencyStatus } from '@/api/plugins'

const api = vi.hoisted(() => ({
  getPythonDependencies: vi.fn(),
  requestPythonDependencyInstall: vi.fn(),
}))
vi.mock('@/api/plugins', () => api)
vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string, values?: Record<string, unknown>) => (values ? `${key} ${JSON.stringify(values)}` : key) }),
}))
vi.mock('@element-plus/icons-vue', () => ({ Loading: defineComponent({ setup: () => () => h('i') }) }))

const apps: Array<ReturnType<typeof createApp>> = []
const flush = async () => {
  for (let n = 0; n < 10; n++) await nextTick()
}

const awaiting: PythonDependencyStatus = {
  plugin_id: 'demo',
  state: 'awaiting_confirmation',
  pending: true,
  lock_sha256: 'a'.repeat(64),
  packages: [
    { name: 'orjson', version: '3.13.0', size: 130518 },
    { name: 'idna', version: '3.20', size: null },
  ],
  total_size: 130518,
  size_complete: false,
}

function mount() {
  const root = document.createElement('div')
  document.body.append(root)
  const store = usePluginDependencyInstall()
  void store.request('demo', 'Demo Plugin')
  const app = createApp(PluginDependencyDialog, { request: store.active })
  app.use(pinia)
  const passthrough = (tag: string) =>
    defineComponent({ setup: (_, { slots }) => () => h(tag, [slots.default?.(), slots.footer?.()]) })
  app.component('el-dialog', passthrough('div'))
  app.component(
    'el-button',
    defineComponent({
      props: { loading: Boolean, type: String },
      setup: (props, { slots }) => () => h('button', { 'data-type': props.type || 'default' }, slots.default?.()),
    }),
  )
  app.component(
    'el-alert',
    defineComponent({
      props: ['title', 'description'],
      setup: (props) => () => h('aside', `${props.title} ${props.description}`),
    }),
  )
  app.component(
    'el-table',
    defineComponent({
      props: ['data'],
      setup: (props) => () =>
        h('table', (props.data as Array<{ name: string; version: string; size: number | null }>).map((row) =>
          h('tr', { 'data-testid': 'row' }, `${row.name} ${row.version}`))),
    }),
  )
  app.component('el-table-column', defineComponent({ setup: () => () => null }))
  app.component('el-icon', passthrough('span'))
  app.mount(root)
  apps.push(app)
  return { root, store }
}

function buttons(root: HTMLElement) {
  return [...root.querySelectorAll('button')]
}

let pinia: ReturnType<typeof createPinia>

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  vi.clearAllMocks()
  api.getPythonDependencies.mockResolvedValue({ plugin_id: 'demo', state: null, pending: true })
})
afterEach(() => {
  apps.splice(0).forEach((app) => app.unmount())
  document.body.innerHTML = ''
  vi.useRealTimers()
})

describe('PluginDependencyDialog', () => {
  it('plans on open and lists packages with versions and a total', async () => {
    api.requestPythonDependencyInstall.mockResolvedValue(awaiting)
    const { root } = mount()
    await flush()

    expect(api.requestPythonDependencyInstall).toHaveBeenCalledWith('demo', null)
    expect([...root.querySelectorAll('[data-testid="row"]')].map((row) => row.textContent)).toEqual([
      'orjson 3.13.0', 'idna 3.20',
    ])
    expect(root.textContent).toContain('pythonDependencies.introduction {"name":"Demo Plugin"}')
    // One size is unknown: the total says it covers only the known part.
    expect(root.textContent).toContain('pythonDependencies.totalPartial {"count":2,"size":"127 KB"}')
    // Two buttons: cancel and install.
    expect(buttons(root).map((button) => button.textContent)).toEqual([
      'common.cancel', 'pythonDependencies.installAndStart',
    ])
  })

  it('installs the confirmed lock and reports success to the start gate', async () => {
    vi.useFakeTimers()
    api.requestPythonDependencyInstall
      .mockResolvedValueOnce(awaiting)
      .mockResolvedValueOnce({ ...awaiting, state: 'installing', phase: 'installing' })
    const { root, store } = mount()
    const result = store.request('demo', 'Demo Plugin')
    await flush()

    buttons(root)[1]!.click()
    await flush()
    expect(api.requestPythonDependencyInstall).toHaveBeenLastCalledWith('demo', 'a'.repeat(64))
    expect(root.textContent).toContain('pythonDependencies.phases.installing')

    api.getPythonDependencies.mockResolvedValue({ ...awaiting, state: 'completed', pending: false })
    await vi.advanceTimersByTimeAsync(1100)
    await flush()

    await expect(result).resolves.toBe(true)
    expect(store.active).toBeNull()
  })

  it('a failure explains itself and offers retry and close; retry reuses the confirmed lock', async () => {
    api.getPythonDependencies.mockResolvedValue({
      ...awaiting, state: 'failed', error_code: 'network', error_detail: 'Failed to fetch',
    })
    api.requestPythonDependencyInstall
      .mockResolvedValueOnce({ ...awaiting, state: 'failed', error_code: 'network', error_detail: 'Failed to fetch' })
      .mockResolvedValueOnce({ ...awaiting, state: 'installing' })
    const { root } = mount()
    await flush()

    expect(root.textContent).toContain('pythonDependencies.failedTitle pythonDependencies.errors.network')
    expect(buttons(root).map((button) => button.textContent)).toEqual(['common.close', 'common.retry'])

    buttons(root)[1]!.click()
    await flush()
    expect(api.requestPythonDependencyInstall).toHaveBeenLastCalledWith('demo', 'a'.repeat(64))
  })

  it('closing resolves false without stopping anything', async () => {
    api.requestPythonDependencyInstall.mockResolvedValue(awaiting)
    const { root, store } = mount()
    const result = store.request('demo', 'Demo Plugin')
    await flush()

    buttons(root)[0]!.click()
    await expect(result).resolves.toBe(false)
    expect(api.requestPythonDependencyInstall).toHaveBeenCalledTimes(1)
  })

  it('an unknown error code falls back to the generic message', async () => {
    api.requestPythonDependencyInstall.mockResolvedValue({ ...awaiting, state: 'failed', error_code: 'weird' })
    const { root } = mount()
    await flush()

    expect(root.textContent).toContain('pythonDependencies.errors.install_failed')
  })

  it('a complete size list shows the plain total', async () => {
    api.requestPythonDependencyInstall.mockResolvedValue({ ...awaiting, size_complete: true })
    const { root } = mount()
    await flush()

    expect(root.textContent).toContain('pythonDependencies.total {"count":2,"size":"127 KB"}')
  })

  it('a task lost on the server (state null, still pending) becomes retryable', async () => {
    vi.useFakeTimers()
    api.requestPythonDependencyInstall.mockResolvedValue({ ...awaiting, state: 'installing' })
    const { root } = mount()
    await flush()

    api.getPythonDependencies.mockResolvedValue({ plugin_id: 'demo', state: null, pending: true })
    await vi.advanceTimersByTimeAsync(1100)
    await flush()

    expect(buttons(root).map((button) => button.textContent)).toEqual(['common.close', 'common.retry'])
    api.requestPythonDependencyInstall.mockResolvedValue(awaiting)
    buttons(root)[1]!.click()
    await flush()
    expect(api.requestPythonDependencyInstall).toHaveBeenLastCalledWith('demo', null)
  })

  it('a failed planning request asks the server again, then offers retry', async () => {
    api.requestPythonDependencyInstall.mockRejectedValue(new Error('offline'))
    api.getPythonDependencies
      .mockResolvedValueOnce({ plugin_id: 'demo', state: null, pending: true })
      .mockRejectedValueOnce(new Error('offline'))
    const { root } = mount()
    await flush()

    expect(api.getPythonDependencies).toHaveBeenCalledTimes(2)
    expect(root.textContent).toContain('pythonDependencies.errors.install_failed')
    expect(buttons(root).map((button) => button.textContent)).toEqual(['common.close', 'common.retry'])
  })

  it('a lost response is recovered from the server state', async () => {
    api.requestPythonDependencyInstall.mockRejectedValue(new Error('timeout'))
    api.getPythonDependencies
      .mockResolvedValueOnce({ plugin_id: 'demo', state: null, pending: true })
      .mockResolvedValueOnce(awaiting)
    const { root } = mount()
    await flush()

    expect([...root.querySelectorAll('[data-testid="row"]')]).toHaveLength(2)
  })
})
