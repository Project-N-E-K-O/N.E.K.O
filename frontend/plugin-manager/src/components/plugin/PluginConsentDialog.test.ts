// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, getActivePinia, setActivePinia } from 'pinia'
import { createApp, defineComponent, h, nextTick } from 'vue'
import PluginConsentDialog from './PluginConsentDialog.vue'
import { usePluginConsentState } from '@/stores/pluginConsentState'
import type { PluginConsent } from '@/api/pluginConsent'

const api = vi.hoisted(() => ({
  getPluginConsent: vi.fn(),
  approvePluginConsent: vi.fn(),
  rejectPluginConsent: vi.fn(),
  revokePluginConsent: vi.fn(),
  getPluginConsentNotices: vi.fn(),
  analyzePluginCapabilities: vi.fn(),
}))
vi.mock('@/api/pluginConsent', () => api)
vi.mock('@/utils/request', () => ({ readErrorCode: vi.fn() }))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
const apps: Array<ReturnType<typeof createApp>> = []
const flush = async () => {
  for (let n = 0; n < 10; n++) await nextTick()
}
function mount() {
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp(PluginConsentDialog)
  app.use(getActivePinia() ?? createPinia())
  app.component(
    'el-dialog',
    defineComponent({
      setup:
        (_, { slots }) =>
        () =>
          h('div', [slots.default?.(), slots.footer?.()]),
    })
  )
  app.component(
    'el-button',
    defineComponent({
      props: { disabled: Boolean, loading: Boolean },
      setup:
        (props, { slots }) =>
        () =>
          h('button', { disabled: props.disabled || props.loading }, slots.default?.()),
    })
  )
  app.component(
    'el-alert',
    defineComponent({ props: ['title'], setup: (props) => () => h('aside', String(props.title)) })
  )
  app.component(
    'el-tag',
    defineComponent({ setup: (_, { slots }) => () => h('i', slots.default?.()) })
  )
  app.mount(root)
  apps.push(app)
  return root
}
function agree(root: HTMLElement) {
  return [...root.querySelectorAll('button')].find(
    (button) => button.textContent === 'pluginConsent.approve'
  )!
}
function record(id: string, extras: Partial<PluginConsent> = {}): PluginConsent {
  return {
    plugin_id: id,
    version: '1',
    decision: null,
    decided_at: null,
    approved_capabilities: [],
    unconfirmed: ['memory.read'],
    state: 'confirm',
    upgraded: false,
    analysis: { status: 'completed', capabilities: ['memory.read'], evidence: [], warnings: [] },
    ...extras,
  }
}
beforeEach(() => {
  setActivePinia(createPinia())
  for (const mock of Object.values(api)) mock.mockReset()
  api.approvePluginConsent.mockImplementation(async (id: string) => record(id, {
    decision: 'approved',
    state: 'clear',
    unconfirmed: [],
  }))
})
afterEach(() => {
  apps.splice(0).forEach((app) => app.unmount())

  document.body.innerHTML = ''
})

describe('plugin consent dialog', () => {
  it('keeps concurrent requests separate and only merges identical requests', async () => {
    const resolved: string[] = []
    const store = usePluginConsentState()
    const requestA = store.requestConfirmation([{ id: 'a', name: 'A' }])
    let duplicateResolved: boolean | null = null
    void store
      .requestConfirmation([{ id: 'a', name: 'A' }])
      .then((value) => (duplicateResolved = value))
    const a = requestA.then(() => resolved.push('a'))
    const b = store.requestConfirmation([{ id: 'b', name: 'B' }]).then(() => resolved.push('b'))
    const c = store.requestConfirmation([{ id: 'c', name: 'C' }]).then(() => resolved.push('c'))
    store.resolveConfirmation(true)
    await flush()
    expect((store.activeRequest?.plugins ?? []).map((plugin) => plugin.id)).toEqual(['b'])
    store.resolveConfirmation(true)
    await flush()
    expect(resolved).toEqual(['a', 'b'])
    expect((store.activeRequest?.plugins ?? []).map((plugin) => plugin.id)).toEqual(['c'])
    store.resolveConfirmation(true)
    await Promise.all([a, b, c])
    expect(resolved).toEqual(['a', 'b', 'c'])
    expect(duplicateResolved).toBe(true)
  })

  it('lists unconfirmed capabilities and approves each plugin', async () => {
    const store = usePluginConsentState()
    store.details.demo = record('demo')
    const request = store.requestConfirmation([{ id: 'demo', name: 'Demo' }])
    const root = mount()
    await flush()
    expect(root.textContent).toContain('pluginConsent.capabilities.memory_read.title')
    expect(agree(root).disabled).toBe(false)
    agree(root).click()
    await request
    expect(api.approvePluginConsent).toHaveBeenCalledWith('demo', expect.objectContaining({ unconfirmed: ['memory.read'] }))
    expect(store.activeRequest).toBeNull()
  })

  it('labels capabilities added after upgrade', async () => {
    const store = usePluginConsentState()
    store.details.demo = record('demo', { upgraded: true, decision: 'approved' })
    const request = store.requestConfirmation([{ id: 'demo', name: 'Demo' }])
    const root = mount()
    await flush()
    expect(root.textContent).toContain('pluginConsent.upgraded')
    agree(root).click()
    await request
  })

  it('warns when the analysis is incomplete', async () => {
    const store = usePluginConsentState()
    store.details.demo = record('demo', {
      analysis: { status: 'partial', capabilities: [], evidence: [], warnings: [] },
    })
    const request = store.requestConfirmation([{ id: 'demo', name: 'Demo' }])
    const root = mount()
    await flush()
    expect(root.textContent).toContain('pluginConsent.incomplete')
    agree(root).click()
    await request
  })

  it('drops a plugin that turns clear and resolves once none remain', async () => {
    const store = usePluginConsentState()
    store.details.demo = record('demo')
    const request = store.requestConfirmation([{ id: 'demo', name: 'Demo' }])
    mount()
    await flush()
    store.details.demo = { ...store.details.demo, state: 'clear', unconfirmed: [] }
    await flush()
    await request
    expect(api.approvePluginConsent).not.toHaveBeenCalled()
  })

  it('keeps the dialog open when consent cannot be saved', async () => {
    api.approvePluginConsent.mockRejectedValue(new Error('disk full'))
    const store = usePluginConsentState()
    store.details.demo = record('demo')
    const request = store.requestConfirmation([{ id: 'demo', name: 'Demo' }])
    const root = mount()
    await flush()
    agree(root).click()
    await flush()
    expect(root.textContent).toContain('pluginConsent.saveFailed')
    expect(store.activeRequest?.plugins).toHaveLength(1)
    store.resolveConfirmation(false)
    expect(await request).toBe(false)
  })

  it('handles a batch in one dialog and does not grant on cancel', async () => {
    const store = usePluginConsentState()
    store.details.one = record('one')
    store.details.two = record('two')
    const request = store.requestConfirmation([
      { id: 'one', name: 'One' },
      { id: 'two', name: 'Two' },
    ]).catch((error) => error)
    const root = mount()
    await flush()
    expect(root.querySelectorAll('h3')).toHaveLength(2)
    root.querySelector<HTMLButtonElement>('button')!.click()
    expect(await request).toBe(false)
    expect(api.approvePluginConsent).not.toHaveBeenCalled()
  })
})
