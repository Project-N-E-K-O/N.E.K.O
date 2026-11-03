// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, reactive, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import MarketPanel from './MarketPanel.vue'
import { fetchBridge } from '@/api/marketBridge'
import { fetchMarketCatalog } from '@/api/market'

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
const userPref = reactive({ channel: 'stable' as 'stable' | 'beta' })
vi.mock('@/stores/userPreference', () => ({ useUserPreferenceStore: () => userPref }))
vi.mock('@/stores/marketInstallTask', () => ({
  useMarketInstallTaskStore: () => ({ running: false, owner: null, done: false }),
}))
vi.mock('@/api/marketBridge', () => ({
  fetchBridge: vi.fn(),
  ensureBridgeToken: vi.fn(),
  readErrorCode: vi.fn(),
}))
vi.mock('@/api/market', () => ({
  fetchMarketCatalog: vi.fn(),
  fetchMarketPluginVersions: vi.fn(async () => []),
  getMarketUrl: vi.fn(async () => 'https://market.example'),
  isMarketAvailable: vi.fn(async () => true),
}))
vi.mock('@/composables/useGithubMirrorSource', () => ({
  useGithubMirrorSource: () => ({ ensureDownloadPreferences: vi.fn() }),
}))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
const workbench = vi.hoisted(() => ({ filterText: null as unknown as { value: string } }))
vi.mock('@/composables/useMarketWorkbench', async () => {
  const { ref, computed } = await import('vue')
  return {
    useMarketWorkbench: (plugins: { value: { name: string }[] }) => {
      const filterText = ref('')
      workbench.filterText = filterText
      return {
        filterText,
        selectedGroupIds: ref(['all']),
        layoutMode: ref('compact'),
        groupCounts: ref({}),
        filteredItems: computed(() =>
          plugins.value.filter((plugin) => plugin.name.includes(filterText.value))
        ),
      }
    },
  }
})
vi.mock('@/components/common/GridSection.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      props: ['items', 'count'],
      setup:
        (props, { slots }) =>
        () =>
          h(
            'div',
            { 'data-testid': 'market-section', 'data-count': props.count },
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
vi.mock('@/components/common/EmptyState.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      props: ['description'],
      setup: (props) => () =>
        h('div', { 'data-testid': 'market-empty', 'data-description': props.description }),
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
  return {
    active,
    action: () => root.querySelector('[data-testid="market-action"]')?.textContent,
    cardCount: () => root.querySelectorAll('[data-testid="market-action"]').length,
    sectionCount: () =>
      root.querySelector('[data-testid="market-section"]')?.getAttribute('data-count'),
    emptyDescription: () =>
      root.querySelector('[data-testid="market-empty"]')?.getAttribute('data-description'),
  }
}
beforeEach(() => {
  vi.clearAllMocks()
  pluginState.registryRevision = 0
  pluginState.pluginSummariesWithStatus = []
  userPref.channel = 'stable'
  vi.mocked(fetchMarketCatalog).mockResolvedValue([
    { id: '1', slug: 'demo', name: 'Demo', version: '2.0.0', has_release: true },
  ] as never)
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
    expect(fetchMarketCatalog).toHaveBeenCalledTimes(1)
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
    expect(fetchMarketCatalog).toHaveBeenCalledTimes(1)
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

describe('market pagination', () => {
  it('pages the filtered catalog instead of filtering each catalog page', async () => {
    // 匹配项全部排在目录末尾：按页过滤时第一页会是空的
    const catalog = Array.from({ length: 30 }, (_, i) => ({
      id: String(i),
      slug: `plugin-${i}`,
      name: i >= 20 ? `match-${i}` : `other-${i}`,
      version: '1.0.0',
      has_release: true,
    }))
    vi.mocked(fetchMarketCatalog).mockResolvedValue(catalog as never)
    const ui = mount()
    await flush()
    expect(ui.cardCount()).toBe(12)
    expect(ui.sectionCount()).toBe('30')

    workbench.filterText.value = 'match-'
    await flush()
    expect(ui.cardCount()).toBe(10)
    expect(ui.sectionCount()).toBe('10')
    expect(fetchMarketCatalog).toHaveBeenCalledTimes(1)
  })

  it('drops the old channel catalog while the new one loads', async () => {
    const ui = mount()
    await flush()
    expect(ui.cardCount()).toBe(1)

    let resolveCatalog: (value: unknown) => void = () => {}
    vi.mocked(fetchMarketCatalog).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveCatalog = resolve
      }) as never
    )
    userPref.channel = 'beta'
    await flush()
    expect(fetchMarketCatalog).toHaveBeenLastCalledWith(expect.objectContaining({ channel: 'beta' }))
    expect(ui.cardCount()).toBe(0)

    resolveCatalog([{ id: '2', slug: 'beta-demo', name: 'Beta', version: '2.0.0', has_release: true }])
    await flush()
    expect(ui.cardCount()).toBe(1)
  })

  it('shows the retry state when a channel refresh fails', async () => {
    const ui = mount()
    await flush()
    vi.mocked(fetchMarketCatalog).mockResolvedValueOnce(null)
    userPref.channel = 'beta'
    await flush()
    expect(ui.cardCount()).toBe(0)
    expect(ui.emptyDescription()).toBe('market.loadFailed')
  })
})
