// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { ElMessage } from 'element-plus'
import PluginList from './PluginList.vue'
import { usePluginStore } from '@/stores/plugin'
import { getPluginStatus, getPluginSummaries, refreshPluginsRegistry } from '@/api/plugins'
import { usePluginWorkbench } from '@/composables/usePluginWorkbench'

vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key, locale: ref('en-US') }) }))
vi.mock('@/i18n', () => ({ getLocale: () => 'en-US', i18n: { global: { t: (key: string) => key } } }))
vi.mock('vue-router', () => ({ useRoute: () => ({ path: '/plugins', query: {} }), useRouter: () => ({ replace: vi.fn() }) }))
vi.mock('@/api/plugins', () => ({ getPlugin: vi.fn(), getPlugins: vi.fn(), getPluginSummaries: vi.fn(), getPluginStatus: vi.fn(), refreshPluginsRegistry: vi.fn(), startPlugin: vi.fn(), stopPlugin: vi.fn(), reloadPlugin: vi.fn(), reloadAllPlugins: vi.fn(), setPluginAutoStart: vi.fn(), deletePlugin: vi.fn() }))
vi.mock('@/api/market', () => ({ getMarketUrl: async () => '' }))
vi.mock('@/stores/metrics', () => ({ useMetricsStore: () => ({ fetchAllMetrics: vi.fn() }) }))
vi.mock('@/stores/marketVersions', () => ({ useMarketVersionsStore: () => ({ ensureFresh: async () => {} }) }))
vi.mock('@/stores/pluginUpdates', () => ({ usePluginUpdatesStore: () => ({ checking: false, candidates: [], openFromButton: vi.fn() }) }))
vi.mock('@/composables/useMarketAuth', () => ({ useMarketAuth: () => ({ marketAuth: ref({ authenticated: false, auth_state: 'anonymous' }), marketAuthDisplayName: ref(''), loadMarketAuthStatus: async () => {} }) }))
vi.mock('@/composables/usePluginListContextActions', () => ({ usePluginListContextActions: () => ({ buildActions: vi.fn(), executeAction: vi.fn(), shouldUseHoldConfirm: vi.fn() }) }))
vi.mock('@/components/plugin/PluginGridSection.vue', () => ({ default: defineComponent({ props: ['items'], setup: props => () => h('div', { 'data-testid': 'visible-plugins' }, props.items.map((item: { name: string }) => item.name).join(',')) }) }))
vi.mock('@/components/plugin/PluginImportDialog.vue', () => ({ default: defineComponent({
  emits: ['installed'],
  setup: (_, { emit }) => () => h('button', { 'data-testid': 'installation-completed', onClick: () => emit('installed') }),
}) }))
vi.mock('@/components/plugin/PluginContextMenu.vue', () => ({ default: { render: () => null } }))
vi.mock('@/components/plugin/PluginDangerConfirmDialog.vue', () => ({ default: { render: () => null } }))
vi.mock('@/components/common/WorkbenchFilterBar.vue', () => ({ default: defineComponent({ props: ['filterText'], emits: ['update:filterText'], setup: (props, { emit }) => () => h('input', { 'data-testid': 'filter', value: props.filterText, onInput: (event: Event) => emit('update:filterText', (event.target as HTMLInputElement).value) }) }) }))
vi.mock('@/components/common/WorkbenchGroupFilter.vue', () => ({ default: { render: () => null } }))
vi.mock('@/components/common/WorkbenchLayoutSwitcher.vue', () => ({ default: { render: () => null } }))

let cleanup = () => {}
const demo = { id: 'demo', name: 'Demo', version: '1.0.0', type: 'plugin', status: 'running' }
async function flush() { for (let i = 0; i < 40; i++) { await Promise.resolve(); await nextTick() } }
async function mount() {
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp(PluginList)
  app.use(createPinia())
  app.config.globalProperties.$t = (key: string) => key
  app.directive('loading', () => {})
  const pass = defineComponent({ setup: (_, { slots, attrs }) => () => h('div', attrs, [slots.header?.(), slots.default?.()]) })
  for (const name of ['ElCard', 'ElIcon', 'ElTag']) app.component(name, pass)
  app.component('ElButton', defineComponent({ setup: (_, { slots, attrs }) => () => h('button', attrs, slots.default?.()) }))
  app.component('ElEmpty', defineComponent({ props: ['description'], setup: (props, { slots }) => () => h('div', [props.description, slots.default?.()]) }))
  app.component('ElAlert', defineComponent({ props: ['description'], setup: props => () => h('div', { role: 'alert' }, props.description) }))
  app.mount(root)
  cleanup = () => { app.unmount(); root.remove() }
  await flush()
  return { root, store: usePluginStore() }
}
beforeEach(() => {
  vi.clearAllMocks()
  setActivePinia(createPinia())
  const workbench = usePluginWorkbench(ref([]), { scope: 'plugin-workbench' })
  workbench.filterText.value = ''; workbench.selectedTypes.value = ['plugin', 'adapter']
  vi.mocked(getPluginSummaries).mockResolvedValue({ plugins: [demo], message: '' } as never)
  vi.mocked(getPluginStatus).mockResolvedValue({ plugins: { demo: { status: 'running' } } } as never)
  vi.mocked(refreshPluginsRegistry).mockResolvedValue({ success: true, failed: [] } as never)
  vi.spyOn(console, 'warn').mockImplementation(() => {})
  vi.spyOn(console, 'error').mockImplementation(() => {})
})
afterEach(() => { cleanup(); cleanup = () => {}; vi.restoreAllMocks() })

describe('plugin list states through its real store and filter', () => {
  it('suppresses an older refresh warning after the newest refresh succeeds', async () => {
    const warning = vi.spyOn(ElMessage, 'warning').mockImplementation(() => ({ close: vi.fn() }))
    const { root } = await mount()
    let release!: (value: never) => void
    vi.mocked(refreshPluginsRegistry)
      .mockReturnValueOnce(new Promise(resolve => { release = resolve }))
      .mockResolvedValueOnce({ success: true, failed: [] } as never)
    const refresh = root.querySelector('[data-yui-guide-id="plugin-list-refresh"]') as HTMLButtonElement
    refresh.click()
    await flush()
    ;(root.querySelector('[data-testid="installation-completed"]') as HTMLButtonElement).click()
    await flush()
    warning.mockClear()
    release({ success: false, failed: [{ plugin_id: 'old', error: 'stale failure' }] } as never)
    await flush()
    expect(warning).not.toHaveBeenCalled()
  })
  it('shows a retryable load error instead of claiming there are no plugins', async () => {
    vi.mocked(getPluginSummaries).mockRejectedValue(new Error('offline'))
    const { root } = await mount()
    expect(root.textContent).toContain('plugins.listLoadFailed')
    expect(root.textContent).not.toContain('plugins.noPlugins')
    vi.mocked(getPluginSummaries).mockResolvedValue({ plugins: [demo], message: '' } as never)
    ;([...root.querySelectorAll('button')].find(button => button.textContent === 'market.retry')!).click()
    await flush()
    expect(root.textContent).toContain('Demo')
    expect(root.textContent).not.toContain('plugins.listLoadFailed')
  })
  it('preserves loaded data and exposes an update error with retry', async () => {
    const { root, store } = await mount()
    vi.mocked(getPluginSummaries).mockRejectedValue(new Error('offline'))
    ;(root.querySelector('[data-yui-guide-id="plugin-list-refresh"]') as HTMLButtonElement).click()
    await flush()
    expect(store.pluginSummaries).toHaveLength(1)
    expect(root.textContent).toContain('Demo')
    expect(root.textContent).toContain('messages.refreshPartialFailure')
  })
  it('distinguishes a successful empty registry from an active filter with zero matches', async () => {
    const { root } = await mount()
    const filter = root.querySelector('[data-testid="filter"]') as HTMLInputElement
    filter.value = 'no-such-plugin'
    filter.dispatchEvent(new Event('input', { bubbles: true }))
    await flush()
    expect(root.textContent).toContain('plugins.noMatches')
    ;([...root.querySelectorAll('button')].find(button => button.textContent === 'plugins.clearFilters')!).click()
    await flush()
    expect(root.textContent).toContain('Demo')
    expect(filter.value).toBe('')
  })
  it('only reports no plugins after a successful empty snapshot', async () => {
    vi.mocked(getPluginSummaries).mockResolvedValue({ plugins: [], message: '' })
    const { root } = await mount()
    expect(root.textContent).toContain('plugins.noPlugins')
    expect(root.textContent).not.toContain('plugins.listLoadFailed')
  })
  it('renders loading rather than empty while the first list request is pending', async () => {
    let finish!: (value: never) => void
    vi.mocked(getPluginSummaries).mockReturnValue(new Promise(resolve => { finish = resolve }))
    const { root } = await mount()
    expect(root.textContent).toContain('common.loading')
    expect(root.textContent).not.toContain('plugins.noPlugins')
    finish({ plugins: [], message: '' } as never)
    await flush()
    expect(root.textContent).toContain('plugins.noPlugins')
  })
  it('keeps a successful summary visible when its accompanying status refresh fails', async () => {
    const { root } = await mount()
    vi.mocked(getPluginStatus).mockRejectedValue(new Error('status offline'))
    ;(root.querySelector('[data-yui-guide-id="plugin-list-refresh"]') as HTMLButtonElement).click()
    await flush()
    expect(root.textContent).toContain('Demo')
    expect(root.textContent).toContain('messages.refreshPartialFailure')
    expect(root.textContent).not.toContain('plugins.listLoadFailed')
  })
})
