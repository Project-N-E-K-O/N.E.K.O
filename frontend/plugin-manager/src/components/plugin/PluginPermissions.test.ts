// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, getActivePinia, setActivePinia } from 'pinia'
import { createApp, defineComponent, h, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
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
    // Every key exists except the labels of a permission id this build does not know.
    te: (key: string) => !key.includes('system_example_future'),
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

  it.each([
    { saved: false, message: 'pluginConsent.saveFailed' },
    { saved: true, message: 'messages.startFailed' },
  ])('reports the failure at the approve-and-start step that failed (saved=$saved)', async ({
    saved,
    message,
  }) => {
    const toast = vi.spyOn(ElMessage, 'error').mockImplementation(() => ({ close: () => {} }))
    pluginStore.plugin = { id: 'demo', status: 'stopped' }
    seed()
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
      const root = mount()
      await flush()
      buttons(root, 'pluginConsent.approveAndStart')[0].click()
      await vi.waitFor(() => expect(toast).toHaveBeenCalledWith(message))
      expect(pluginStore.start).toHaveBeenCalledTimes(saved ? 1 : 0)
    } finally {
      toast.mockRestore()
    }
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

  describe('what the analysis found', () => {
    const clearRecord = {
      state: 'clear' as const,
      unconfirmed: [],
    }
    function analysis(extras: Partial<PluginConsent['analysis']> = {}): PluginConsent['analysis'] {
      return {
        status: 'completed',
        capabilities: [],
        evidence: [],
        warnings: [],
        files_analyzed: 2,
        permissions: {},
        blind_spots: [],
        ...extras,
      }
    }

    it('lists used permissions instead of reporting nothing found', async () => {
      seed({
        ...clearRecord,
        analysis: analysis({
          permissions: {
            'system.network:connect': {
              declared: null,
              findings: [{ file: 'main.py', line: 7 }],
            },
            'plugin_sdk.models:invoke': { declared: 'required', findings: [] },
          },
        }),
      })
      const root = mount()
      await flush()
      expect(root.textContent).not.toContain('pluginConsent.noneFound')
      expect(root.textContent).toContain('pluginConsent.findings.usedBanner {"n":2}')
      const listed = root.querySelector('[data-testid="used-permissions"]')!
      expect(listed.textContent).toContain('pluginConsent.capabilities.system_network_connect.title')
      expect(listed.textContent).toContain('pluginConsent.findings.detected')
      expect(listed.textContent).toContain('pluginConsent.capabilities.plugin_sdk_models_invoke.title')
      expect(listed.textContent).toContain('pluginConsent.findings.declared')
      expect(root.querySelector('[data-testid="blind-spots"]')).toBeNull()
    })

    it('shows the origin of a permission listed under its earlier capability name', async () => {
      seed({
        ...clearRecord,
        analysis: analysis({
          capabilities: ['memory.read'],
          permissions: {
            'plugin_sdk.main_agent.memory:read': {
              declared: 'require',
              findings: [{ file: 'main.py', line: 900 }],
            },
          },
        }),
      })
      const root = mount()
      await flush()
      expect(root.querySelector('[data-testid="used-permissions"]')).toBeNull()
      const origin = root.querySelector('[data-testid="capability-origin"]')!
      expect(origin.textContent).toContain('pluginConsent.findings.declared')
      const toggle = root.querySelector<HTMLButtonElement>('.capability__toggle')!
      expect(toggle.textContent).toContain('pluginConsent.evidenceToggle {"n":1}')
      toggle.click()
      await flush()
      expect(root.querySelector('.evidence code')?.textContent).toBe('main.py:900')
    })

    it('counts blind spots instead of reporting nothing found', async () => {
      seed({
        ...clearRecord,
        analysis: analysis({
          blind_spots: [
            { type: 'dynamic_import', file: 'main.py', line: 4 },
            { type: 'not_analyzed', file: 'vendor/big.py', line: null },
            { type: 'not_analyzed', file: null, line: null },
          ],
        }),
      })
      const root = mount()
      await flush()
      expect(root.textContent).not.toContain('pluginConsent.noneFound')
      expect(root.querySelector('.consent-banner')?.getAttribute('data-tone')).toBe('warning')
      expect(root.textContent).toContain('pluginConsent.findings.blindSpotsTitle {"n":3}')
      const spots = root.querySelector('[data-testid="blind-spots"]')!
      expect(spots.querySelector('code')).toBeNull()
      const toggle = buttonsContaining(spots as HTMLElement, 'pluginConsent.evidenceToggle')[0]!
      toggle.click()
      await flush()
      expect([...spots.querySelectorAll('code')].map((node) => node.textContent)).toEqual([
        'main.py:4',
        'vendor/big.py',
      ])
      expect(spots.textContent).toContain('pluginConsent.findings.unlocated')
      expect(spots.textContent).not.toContain('not_analyzed')
    })

    it('reports nothing found only without permissions and blind spots', async () => {
      seed({ ...clearRecord, analysis: analysis() })
      const root = mount()
      await flush()
      expect(root.textContent).toContain('pluginConsent.noneFound')
      expect(root.querySelector('.consent-banner')?.getAttribute('data-tone')).toBe('success')
      expect(root.querySelector('[data-testid="used-permissions"]')).toBeNull()
      expect(root.querySelector('[data-testid="blind-spots"]')).toBeNull()
    })

    it('shows an unlabelled permission id as it is', async () => {
      seed({
        ...clearRecord,
        analysis: analysis({
          permissions: { 'system.example:future': { declared: 'optional', findings: [] } },
        }),
      })
      const root = mount()
      await flush()
      const listed = root.querySelector('[data-testid="used-permissions"]')!
      expect(listed.querySelector('strong')?.textContent).toBe('system.example:future')
      expect(listed.textContent).not.toContain('system_example_future')
    })
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
