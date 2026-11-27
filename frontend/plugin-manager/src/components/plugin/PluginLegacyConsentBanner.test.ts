// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { createApp, defineComponent, h, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import PluginLegacyConsentBanner from './PluginLegacyConsentBanner.vue'
import type { PluginConsent } from '@/api/pluginConsent'

const api = vi.hoisted(() => ({
  getPluginConsent: vi.fn(),
  approvePluginConsent: vi.fn(),
  rejectPluginConsent: vi.fn(),
  revokePluginConsent: vi.fn(),
  getPluginConsentNotices: vi.fn(),
  analyzePluginCapabilities: vi.fn(),
}))
const pluginStore = vi.hoisted(() => ({
  plugin: { id: 'demo', status: 'running' } as { id: string; status: string } | null,
  getPluginById: vi.fn((id: string) => (pluginStore.plugin?.id === id ? pluginStore.plugin : null)),
  start: vi.fn().mockResolvedValue(undefined),
  fetchPluginStatus: vi.fn().mockResolvedValue(undefined),
  refreshLoadedPluginData: vi.fn().mockResolvedValue(undefined),
}))
vi.mock('@/api/pluginConsent', () => api)
vi.mock('@/stores/plugin', () => ({ usePluginStore: () => pluginStore }))
vi.mock('@/i18n', () => ({
  i18n: { global: { t: (key: string) => key } },
  localeLoadState: { pending: null },
}))
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => (params ? `${key} ${JSON.stringify(params)}` : key),
  }),
}))

const apps: Array<ReturnType<typeof createApp>> = []
const flush = async () => {
  for (let n = 0; n < 10; n++) await nextTick()
}
async function mount() {
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp(PluginLegacyConsentBanner, { pluginId: 'demo' })
  app.use(createPinia())
  app.component(
    'el-button',
    defineComponent({
      props: { disabled: Boolean, loading: Boolean },
      setup: (props, { slots }) => () => h('button', { disabled: props.disabled || props.loading }, slots.default?.()),
    }),
  )
  app.component('el-icon', defineComponent({ setup: (_, { slots }) => () => h('i', slots.default?.()) }))
  app.mount(root)
  apps.push(app)
  await flush()
  return root
}
const buttons = (root: HTMLElement, text: string) =>
  [...root.querySelectorAll('button')].filter((button) => button.textContent?.trim() === text)

function record(extras: Partial<PluginConsent> = {}): PluginConsent {
  return {
    plugin_id: 'demo',
    version: '1.0.0',
    decision: null,
    decided_at: null,
    approved_capabilities: [],
    unconfirmed: ['memory.read', 'messages.push'],
    state: 'confirm',
    upgraded: false,
    analysis: { status: 'completed', capabilities: [], evidence: [], warnings: [] },
    ...extras,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  for (const mock of Object.values(api)) mock.mockReset()
  pluginStore.plugin = { id: 'demo', status: 'running' }
  pluginStore.start.mockClear()
})
afterEach(() => {
  apps.splice(0).forEach((app) => app.unmount())
  document.body.innerHTML = ''
})

describe('PluginLegacyConsentBanner', () => {
  it('running + confirm shows approve and deny actions', async () => {
    api.getPluginConsent.mockResolvedValue(record())
    const root = await mount()
    expect(root.textContent).toContain('pluginConsent.banner.confirm {"n":2}')
    expect(buttons(root, 'pluginConsent.approve')).toHaveLength(1)
    expect(buttons(root, 'pluginConsent.reject')).toHaveLength(1)
    expect(buttons(root, 'pluginConsent.approveAndStart')).toHaveLength(0)
  })

  it('running + confirm approves without starting again', async () => {
    api.getPluginConsent.mockResolvedValue(record())
    const approved = record({ decision: 'approved', unconfirmed: [], state: 'clear' })
    api.approvePluginConsent.mockImplementation(async () => {
      api.getPluginConsent.mockResolvedValue(approved)
      return approved
    })
    const root = await mount()
    buttons(root, 'pluginConsent.approve')[0]!.click()
    await vi.waitFor(() => expect(api.approvePluginConsent).toHaveBeenCalled())
    await flush()
    expect(pluginStore.start).not.toHaveBeenCalled()
  })

  it('stopped + confirm approves, then starts', async () => {
    api.getPluginConsent.mockResolvedValue(record())
    pluginStore.plugin = { id: 'demo', status: 'stopped' }
    const root = await mount()
    const approved = record({ decision: 'approved', unconfirmed: [], state: 'clear' })
    api.approvePluginConsent.mockImplementation(async () => {
      api.getPluginConsent.mockResolvedValue(approved)
      return approved
    })
    buttons(root, 'pluginConsent.approveAndStart')[0]!.click()
    await vi.waitFor(() => {
      expect(api.approvePluginConsent).toHaveBeenCalledWith('demo', {
        version: '1.0.0',
        unconfirmed: ['memory.read', 'messages.push'],
        analysis_status: 'completed',
      })
      expect(pluginStore.start).toHaveBeenCalledWith('demo')
    })
  })

  it.each([
    { saved: false, message: 'pluginConsent.saveFailed' },
    { saved: true, message: 'messages.startFailed' },
  ])('reports the step that failed (saved=$saved)', async ({ saved, message }) => {
    const toast = vi.spyOn(ElMessage, 'error').mockImplementation(() => ({ close: () => {} }) as never)
    pluginStore.plugin = { id: 'demo', status: 'stopped' }
    api.getPluginConsent.mockResolvedValue(record())
    if (saved) {
      const approved = record({ decision: 'approved', unconfirmed: [], state: 'clear' })
      api.approvePluginConsent.mockImplementation(async () => {
        api.getPluginConsent.mockResolvedValue(approved)
        return approved
      })
      pluginStore.start.mockRejectedValueOnce(new Error('start failed'))
    } else {
      api.approvePluginConsent.mockRejectedValue(new Error('save failed'))
    }
    try {
      const root = await mount()
      buttons(root, 'pluginConsent.approveAndStart')[0]!.click()
      await vi.waitFor(() => expect(toast).toHaveBeenCalledWith(message))
      expect(pluginStore.start).toHaveBeenCalledTimes(saved ? 1 : 0)
    } finally {
      toast.mockRestore()
    }
  })

  it('denied offers approve and withdraw', async () => {
    api.getPluginConsent.mockResolvedValue(record({ decision: 'denied', decided_at: '2026-11-10T10:00:00Z' }))
    const root = await mount()
    expect(root.textContent).toContain('pluginConsent.decision.denied')
    expect(buttons(root, 'pluginConsent.revokeDecision')).toHaveLength(1)
    expect(buttons(root, 'pluginConsent.reject')).toHaveLength(0)
    api.revokePluginConsent.mockResolvedValue(record())
    buttons(root, 'pluginConsent.revokeDecision')[0]!.click()
    await vi.waitFor(() => expect(api.revokePluginConsent).toHaveBeenCalledWith('demo'))
  })

  it('approved offers revoke', async () => {
    api.getPluginConsent.mockResolvedValue(
      record({ decision: 'approved', decided_at: '2026-11-10T10:00:00Z', state: 'clear', unconfirmed: [] }),
    )
    const root = await mount()
    expect(root.textContent).toContain('pluginConsent.decision.approved')
    expect(buttons(root, 'pluginConsent.revoke')).toHaveLength(1)
    expect(buttons(root, 'pluginConsent.approve')).toHaveLength(0)
  })

  it('stays hidden while analyzing, with nothing to decide, or when loading fails', async () => {
    api.getPluginConsent.mockResolvedValue(record({ state: 'analyzing' }))
    expect((await mount()).querySelector('[data-test="legacy-consent"]')).toBeNull()
    api.getPluginConsent.mockResolvedValue(record({ state: 'analyzing', decision: 'approved' }))
    expect((await mount()).querySelector('[data-test="legacy-consent"]')).toBeNull()
    api.getPluginConsent.mockResolvedValue(record({ state: 'clear', unconfirmed: [] }))
    expect((await mount()).querySelector('[data-test="legacy-consent"]')).toBeNull()
    api.getPluginConsent.mockRejectedValue(new Error('x'))
    expect((await mount()).querySelector('[data-test="legacy-consent"]')).toBeNull()
  })
})
