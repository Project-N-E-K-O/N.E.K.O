// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, getActivePinia, setActivePinia } from 'pinia'
import { createApp, defineComponent, h, nextTick } from 'vue'
import PluginConsentNotice from './PluginConsentNotice.vue'
import type { PluginConsentNotice as Notice } from '@/api/pluginConsent'

const api = vi.hoisted(() => ({
  getPluginConsent: vi.fn(),
  approvePluginConsent: vi.fn(),
  rejectPluginConsent: vi.fn(),
  revokePluginConsent: vi.fn(),
  getPluginConsentNotices: vi.fn(),
  analyzePluginCapabilities: vi.fn(),
}))
const pluginStore = vi.hoisted(() => ({
  fetchPluginStatus: vi.fn().mockResolvedValue(undefined),
  refreshLoadedPluginData: vi.fn().mockResolvedValue(undefined),
}))
vi.mock('@/api/pluginConsent', () => api)
vi.mock('@/utils/request', () => ({ readErrorCode: vi.fn() }))
vi.mock('@/stores/plugin', () => ({ usePluginStore: () => pluginStore }))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
const apps: Array<ReturnType<typeof createApp>> = []
const flush = async () => {
  for (let n = 0; n < 10; n++) await nextTick()
}
function notice(id: string, extras: Partial<Notice> = {}): Notice {
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
function mount(notices: Notice[], onDone: () => void) {
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp(PluginConsentNotice, { notices, onDone })
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
  app.mount(root)
  apps.push(app)
  return root
}
const buttons = (root: HTMLElement, label: string) =>
  [...root.querySelectorAll('button')].filter((button) => button.textContent === label)

beforeEach(() => {
  setActivePinia(createPinia())
  for (const mock of Object.values(api)) mock.mockReset()
  pluginStore.fetchPluginStatus.mockClear()
  pluginStore.refreshLoadedPluginData.mockClear()
})
afterEach(() => {
  apps.splice(0).forEach((app) => app.unmount())
  document.body.innerHTML = ''
})

describe('running-plugin consent notice', () => {
  it('renders a row per plugin with its unconfirmed capabilities', () => {
    const root = mount([notice('one'), notice('two', { upgraded: true })], () => {})
    expect(root.querySelectorAll('li')).toHaveLength(2)
    expect(root.textContent).toContain('pluginConsent.capabilities.memory_read.title')
    expect(root.textContent).toContain('pluginConsent.upgraded')
  })

  it('reject records the denial, refreshes plugins, and closes when finished', async () => {
    api.rejectPluginConsent.mockResolvedValue({})
    let done = false
    const root = mount([notice('demo')], () => {
      done = true
    })
    await flush()
    buttons(root, 'pluginConsent.reject')[0].click()
    await flush()
    expect(api.rejectPluginConsent).toHaveBeenCalledWith('demo')
    expect(pluginStore.fetchPluginStatus).toHaveBeenCalled()
    expect(pluginStore.refreshLoadedPluginData).toHaveBeenCalled()
    expect(done).toBe(true)
    expect(root.querySelectorAll('li')).toHaveLength(0)
  })

  it('approve records the decision and keeps other rows open', async () => {
    api.approvePluginConsent.mockResolvedValue({})
    let done = false
    const root = mount([notice('one'), notice('two')], () => {
      done = true
    })
    await flush()
    buttons(root, 'pluginConsent.approve')[0].click()
    await flush()
    expect(api.approvePluginConsent).toHaveBeenCalledWith('one', notice('one'))
    expect(done).toBe(false)
    expect(root.querySelectorAll('li')).toHaveLength(1)
  })
})
