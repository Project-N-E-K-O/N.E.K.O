// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, reactive, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import MarketPanel from './MarketPanel.vue'
import { fetchBridge } from '@/api/marketBridge'
import { fetchMarketPlugins } from '@/api/market'

const pluginState = reactive({
  registryRevision: 0,
  pluginSummariesWithStatus: [] as { id: string }[],
  fetchPluginSummaries: vi.fn(async () => undefined),
  syncRegistryAndFetchSummaries: vi.fn(async () => undefined),
})
vi.mock('@/stores/plugin', () => ({ usePluginStore: () => pluginState }))
vi.mock('@/stores/pluginUpdates', () => ({
  usePluginUpdatesStore: () => ({ completedUpgrades: 0, check: vi.fn() }),
}))
vi.mock('@/stores/marketInstallTask', () => ({
  useMarketInstallTaskStore: () => ({ running: false, owner: null, done: false }),
}))
vi.mock('@/api/marketBridge', () => ({
  fetchBridge: vi.fn(),
  ensureBridgeToken: vi.fn(),
  readErrorCode: vi.fn(),
}))
vi.mock('@/api/market', () => ({
  fetchMarketPlugins: vi.fn(),
  fetchMarketPluginVersions: vi.fn(async () => []),
  getMarketUrl: vi.fn(async () => 'https://market.example'),
  isMarketAvailable: vi.fn(async () => true),
}))
vi.mock('@/composables/useGithubMirrorSource', () => ({
  isGithubReleaseDownloadUrl: vi.fn(),
  useGithubMirrorSource: () => ({ resolveGithubDownloadUrl: vi.fn(), ensureAutoSource: vi.fn() }),
}))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
vi.mock('@/composables/useMarketWorkbench', async () => {
  const { ref, computed } = await import('vue')
  return {
    useMarketWorkbench: (plugins: { value: unknown[] }) => ({
      filterText: ref(''),
      selectedGroupIds: ref(['all']),
      layoutMode: ref('compact'),
      groupCounts: ref({}),
      filteredItems: computed(() => plugins.value),
    }),
  }
})
vi.mock('@/components/common/GridSection.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      props: ['items'],
      setup:
        (props, { slots }) =>
        () =>
          h(
            'div',
            props.items.map((item: unknown) => slots.item?.({ item }))
          ),
    }),
  }
})
vi.mock('@/components/plugin/MarketPluginCard.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      props: ['installed', 'action'],
      setup: (props) => () =>
        h('span', { 'data-testid': 'market-action' }, `${props.installed}:${props.action.kind}`),
    }),
  }
})
vi.mock('@/components/common/WorkbenchFilterBar.vue', () => ({ default: { render: () => null } }))
vi.mock('@/components/common/WorkbenchGroupFilter.vue', () => ({ default: { render: () => null } }))
vi.mock('@/components/common/WorkbenchLayoutSwitcher.vue', () => ({
  default: { render: () => null },
}))

const installed = {
  plugin_id: 'demo',
  effective_source: 'market',
  effective_version: '1.0.0',
  market_installed: true,
  latest_install_source: {
    plugin_market_id: '1',
    channel: 'stable',
    version: '1.0.0',
    package_url: 'https://example/demo.zip',
  },
}
const response = (entries: unknown[]) =>
  new Response(JSON.stringify({ installed: entries }), { status: 200 })
let cleanup = () => {}
async function flush() {
  for (let i = 0; i < 30; i++) {
    await Promise.resolve()
    await nextTick()
  }
}
function mount() {
  const active = ref(true)
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp(
    defineComponent({ setup: () => () => h(MarketPanel, { active: active.value }) })
  )
  const pinia = createPinia()
  setActivePinia(pinia)
  app.use(pinia)
  app.config.warnHandler = () => {}
  app.component('ElDialog', { render: () => null })
  app.mount(root)
  cleanup = () => {
    app.unmount()
    root.remove()
  }
  return { active, action: () => root.querySelector('[data-testid="market-action"]')?.textContent }
}
beforeEach(() => {
  vi.clearAllMocks()
  pluginState.registryRevision = 0
  pluginState.pluginSummariesWithStatus = []
  vi.mocked(fetchMarketPlugins).mockResolvedValue({
    items: [{ id: '1', slug: 'demo', name: 'Demo', version: '2.0.0', has_release: true }],
    total: 1,
  } as never)
  vi.mocked(fetchBridge).mockResolvedValue(response([installed]))
})
afterEach(() => {
  cleanup()
  cleanup = () => {}
})

describe('market installed state across a retained drawer', () => {
  it('revalidates an invalidated installed projection when the populated drawer reopens', async () => {
    const ui = mount()
    await flush()
    expect(ui.action()).toBe('true:upgrade')
    ui.active.value = false
    await flush()
    vi.mocked(fetchBridge).mockResolvedValue(response([]))
    pluginState.registryRevision += 1
    await flush()
    expect(fetchBridge).toHaveBeenCalledTimes(1)
    ui.active.value = true
    await flush()
    expect(ui.action()).toBe('false:install')
    expect(fetchBridge).toHaveBeenCalledTimes(2)
    expect(fetchMarketPlugins).toHaveBeenCalledTimes(1)
  })

  it('retains its successful projection on a failed reopen and retries on the next reopen', async () => {
    const ui = mount()
    await flush()
    ui.active.value = false
    await flush()
    pluginState.registryRevision += 1
    vi.mocked(fetchBridge).mockResolvedValueOnce(null)
    ui.active.value = true
    await flush()
    expect(ui.action()).toBe('true:upgrade')
    ui.active.value = false
    await flush()
    vi.mocked(fetchBridge).mockResolvedValueOnce(response([]))
    ui.active.value = true
    await flush()
    expect(ui.action()).toBe('false:install')
    expect(fetchBridge).toHaveBeenCalledTimes(3)
    expect(fetchMarketPlugins).toHaveBeenCalledTimes(1)
  })

  it('refreshes the active market after registry reconciliation', async () => {
    const ui = mount()
    await flush()
    vi.mocked(fetchBridge).mockResolvedValue(response([]))
    pluginState.registryRevision += 1
    await flush()
    expect(ui.action()).toBe('false:install')
    expect(fetchBridge).toHaveBeenCalledTimes(2)
  })
})
