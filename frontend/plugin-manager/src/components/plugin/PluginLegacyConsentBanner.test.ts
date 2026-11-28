// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import PluginLegacyConsentBanner from './PluginLegacyConsentBanner.vue'
import type { PluginConsent } from '@/api/pluginConsent'

const api = vi.hoisted(() => ({
  getPluginConsent: vi.fn(),
  revokePluginConsent: vi.fn(),
}))
vi.mock('@/api/pluginConsent', () => api)
vi.mock('@/i18n', () => ({
  i18n: { global: { t: (key: string) => key } },
  localeLoadState: { pending: null },
}))
vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}))

const apps: Array<ReturnType<typeof createApp>> = []
const flush = async () => {
  for (let n = 0; n < 10; n++) await nextTick()
}
async function mount() {
  const root = document.createElement('div')
  document.body.append(root)
  const changed = vi.fn()
  const app = createApp(PluginLegacyConsentBanner, { pluginId: 'demo', onChanged: changed })
  app.component(
    'el-button',
    defineComponent({
      props: { disabled: Boolean, loading: Boolean },
      emits: ['click'],
      setup: (props, { slots, emit, attrs }) => () =>
        h('button', { ...attrs, disabled: props.disabled || props.loading, onClick: () => emit('click') }, slots.default?.()),
    }),
  )
  app.component('el-icon', defineComponent({ setup: (_, { slots }) => () => h('i', slots.default?.()) }))
  app.mount(root)
  apps.push(app)
  await flush()
  return { root, changed }
}
const record = (decision: PluginConsent['decision']): PluginConsent => ({
  plugin_id: 'demo',
  version: '1.0.0',
  decision,
  decided_at: null,
  state: 'clear',
  analysis: { status: 'completed', capabilities: [], evidence: [], warnings: [] },
})

beforeEach(() => {
  vi.clearAllMocks()
  vi.spyOn(ElMessage, 'error').mockImplementation(() => ({ close: () => {} }) as never)
})
afterEach(() => {
  for (const app of apps.splice(0)) app.unmount()
  document.body.innerHTML = ''
})

describe('PluginLegacyConsentBanner', () => {
  it('shows an earlier plugin-wide denial with a way to withdraw it', async () => {
    api.getPluginConsent.mockResolvedValue(record('denied'))
    api.revokePluginConsent.mockResolvedValue(record(null))
    const { root, changed } = await mount()
    expect(root.textContent).toContain('pluginConsent.decision.denied')
    expect(root.querySelectorAll('button')).toHaveLength(1)
    ;(root.querySelector('[data-test="legacy-revoke"]') as HTMLElement).click()
    await flush()
    expect(api.revokePluginConsent).toHaveBeenCalledWith('demo')
    expect(root.querySelector('[data-test="legacy-consent"]')).toBeNull()
    expect(changed).toHaveBeenCalled()
  })

  it.each([['approved' as const], [null]])('stays hidden for decision %s', async (decision) => {
    api.getPluginConsent.mockResolvedValue(record(decision))
    const { root } = await mount()
    expect(root.querySelector('[data-test="legacy-consent"]')).toBeNull()
  })

  it('stays hidden when the record cannot be read', async () => {
    api.getPluginConsent.mockRejectedValue(new Error('offline'))
    const { root } = await mount()
    expect(root.querySelector('[data-test="legacy-consent"]')).toBeNull()
  })

  it('says why a withdrawal failed and reads the record again', async () => {
    api.getPluginConsent.mockResolvedValue(record('denied'))
    api.revokePluginConsent.mockRejectedValue(new Error('x'))
    const { root } = await mount()
    ;(root.querySelector('[data-test="legacy-revoke"]') as HTMLElement).click()
    await flush()
    expect(ElMessage.error).toHaveBeenCalledWith('pluginConsent.saveFailed')
    expect(api.getPluginConsent).toHaveBeenCalledTimes(2)
    expect(root.querySelector('[data-test="legacy-consent"]')).not.toBeNull()
  })
})
