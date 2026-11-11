// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, getActivePinia, setActivePinia } from 'pinia'
import { createApp, defineComponent, h, nextTick } from 'vue'
import PluginPermissions from './PluginPermissions.vue'
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
const pluginStore = vi.hoisted(() => ({
  plugin: { id: 'demo', status: 'running' } as { id: string; status: string } | null,
  getPluginById: vi.fn((id: string) =>
    pluginStore.plugin?.id === id ? pluginStore.plugin : null,
  ),
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
    t: (key: string, params?: Record<string, unknown>) =>
      params ? `${key} ${JSON.stringify(params)}` : key,
  }),
}))
const apps: Array<ReturnType<typeof createApp>> = []
const flush = async () => {
  for (let n = 0; n < 10; n++) await nextTick()
}
function mount() {
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp(PluginPermissions, { pluginId: 'demo' })
  app.use(getActivePinia() ?? createPinia())
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
    'el-tag',
    defineComponent({ setup: (_, { slots }) => () => h('i', slots.default?.()) })
  )
  app.component(
    'el-icon',
    defineComponent({ setup: (_, { slots }) => () => h('i', slots.default?.()) })
  )
  app.component(
    'el-alert',
    defineComponent({ props: ['title'], setup: (props) => () => h('aside', String(props.title)) })
  )
  app.mount(root)
  apps.push(app)
  return root
}
function buttons(root: HTMLElement, text: string) {
  return [...root.querySelectorAll('button')].filter(
    (button) => button.textContent?.trim() === text,
  )
}
function buttonsContaining(root: HTMLElement, text: string) {
  return [...root.querySelectorAll('button')].filter((button) =>
    button.textContent?.includes(text),
  )
}
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
    analysis: {
      status: 'completed',
      version: '1.0.0',
      analyzed_at: '2026-11-10T09:00:00',
      capabilities: ['memory.read', 'messages.push'],
      evidence: [
        { capability: 'memory.read', method: 'm', file: 'main.py', line: 12 },
        { capability: 'memory.read', method: 'm', file: 'main.py', line: 40 },
      ],
      warnings: [],
      files_analyzed: 3,
    },
    ...extras,
  }
}
function seed(extras: Partial<PluginConsent> = {}) {
  const value = record(extras)
  api.getPluginConsent.mockResolvedValue(value)
  usePluginConsentState().details.demo = value
  return value
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

describe('PluginPermissions', () => {
  it('running + confirm shows approve and deny actions', async () => {
    seed()
    const root = mount()
    await flush()
    expect(buttons(root, 'pluginConsent.approve')).toHaveLength(1)
    expect(buttons(root, 'pluginConsent.reject')).toHaveLength(1)
    expect(buttons(root, 'pluginConsent.approveAndStart')).toHaveLength(0)
  })

  it('stopped + confirm shows approve-and-start which approves then starts', async () => {
    seed()
    pluginStore.plugin = { id: 'demo', status: 'stopped' }
    const root = mount()
    await flush()
    const button = buttons(root, 'pluginConsent.approveAndStart')[0]
    expect(button).toBeTruthy()
    const approved = record({ decision: 'approved', unconfirmed: [], state: 'clear' })
    api.approvePluginConsent.mockImplementation(async () => {
      api.getPluginConsent.mockResolvedValue(approved)
      return approved
    })
    button.click()
    await vi.waitFor(() => {
      expect(api.approvePluginConsent).toHaveBeenCalledWith('demo', {
        version: '1.0.0',
        unconfirmed: ['memory.read', 'messages.push'],
        analysis_status: 'completed',
      })
      expect(pluginStore.start).toHaveBeenCalledWith('demo')
    })
  })

  it('denied shows the denied banner with approve and withdraw actions', async () => {
    seed({ decision: 'denied', decided_at: '2026-11-10T10:00:00Z' })
    const root = mount()
    await flush()
    expect(root.textContent).toContain('pluginConsent.decision.denied')
    expect(root.textContent).toContain('pluginConsent.banner.deniedDesc')
    expect(buttons(root, 'pluginConsent.revokeDecision')).toHaveLength(1)
    expect(buttons(root, 'pluginConsent.reject')).toHaveLength(0)
  })

  it('approved + clear shows the success banner with revoke', async () => {
    seed({
      decision: 'approved',
      decided_at: '2026-11-10T10:00:00Z',
      state: 'clear',
      unconfirmed: [],
      approved_capabilities: ['memory.read', 'messages.push'],
    })
    const root = mount()
    await flush()
    expect(root.textContent).toContain('pluginConsent.decision.approved')
    expect(buttons(root, 'pluginConsent.revoke')).toHaveLength(1)
    expect(buttons(root, 'pluginConsent.approve')).toHaveLength(0)
  })

  it('analyzing shows the analyzing banner without actions', async () => {
    seed({
      state: 'analyzing',
      unconfirmed: [],
      analysis: {
        status: 'running',
        capabilities: [],
        evidence: [],
        warnings: [],
      },
    })
    const root = mount()
    await flush()
    expect(root.textContent).toContain('pluginConsent.banner.analyzingTitle')
    expect(buttons(root, 'pluginConsent.approve')).toHaveLength(0)
  })

  it('renders a status tag per capability', async () => {
    seed({
      decision: 'approved',
      decided_at: '2026-11-10T10:00:00Z',
      approved_capabilities: ['memory.read'],
      unconfirmed: ['messages.push'],
      upgraded: true,
    })
    const root = mount()
    await flush()
    expect(root.textContent).toContain('pluginConsent.decision.approved')
    expect(root.textContent).toContain('pluginConsent.upgraded')
    expect(root.textContent).toContain('pluginConsent.capabilities.memory_read.title')
    expect(root.textContent).toContain('pluginConsent.capabilities.messages_push.title')
  })

  it('keeps evidence collapsed until the toggle is clicked', async () => {
    seed()
    const root = mount()
    await flush()
    expect(root.textContent).not.toContain('main.py:12')
    const toggle = buttonsContaining(root, 'pluginConsent.evidenceToggle')[0]
    expect(toggle).toBeTruthy()
    toggle.click()
    await flush()
    expect(root.textContent).toContain('main.py:12')
    expect(root.textContent).toContain('main.py:40')
  })

  it('formats the footer analysis time as YYYY-MM-DD HH:mm', async () => {
    seed()
    const root = mount()
    await flush()
    expect(root.textContent).toContain('2026-11-10 09:00')
  })
})
