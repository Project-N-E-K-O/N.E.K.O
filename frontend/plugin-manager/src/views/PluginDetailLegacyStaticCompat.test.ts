// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import PluginDetail from './PluginDetail.vue'
import type { PluginUiSurface } from '@/types/api'
import { usePluginStore } from '@/stores/plugin'

const apiMocks = vi.hoisted(() => ({
  getPluginUiSurfaceInfo: vi.fn(),
  get: vi.fn(),
  getPlugins: vi.fn(),
  getPluginStatus: vi.fn(),
}))
const routerMocks = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  route: { params: { id: 'study_companion' }, query: {} as Record<string, string> },
}))

vi.mock('@/api/plugins', () => ({
  getPluginUiSurfaceInfo: apiMocks.getPluginUiSurfaceInfo,
  getPlugins: apiMocks.getPlugins,
  getPluginStatus: apiMocks.getPluginStatus,
}))
vi.mock('@/api', () => ({ get: apiMocks.get }))
vi.mock('@/i18n', () => ({ getLocale: () => 'en-US' }))
vi.mock('vue-router', () => ({
  useRoute: () => routerMocks.route,
  useRouter: () => ({ push: routerMocks.push, replace: routerMocks.replace }),
}))
vi.mock('vue-i18n', () => ({
  useI18n: () => ({ locale: ref('en-US'), t: (key: string) => key }),
}))
vi.mock('@/components/plugin/PluginActions.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return { default: defineComponent(() => () => h('div', { 'data-testid': 'plugin-actions' })) }
})
vi.mock('@/components/plugin/HostedSurfaceFrame.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      props: { surface: Object },
      setup(props) {
        return () => h('div', { 'data-surface-id': (props.surface as PluginUiSurface)?.id })
      },
    }),
  }
})
vi.mock('@/components/plugin/PluginUIFrame.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return { default: defineComponent(() => () => h('div', { 'data-testid': 'legacy-ui' })) }
})
vi.mock('@/components/common/StatusIndicator.vue', () => ({ default: defineComponent(() => () => h('div')) }))
vi.mock('@/components/plugin/EntryList.vue', () => ({ default: defineComponent(() => () => h('div')) }))
vi.mock('@/components/metrics/MetricsCard.vue', () => ({ default: defineComponent(() => () => h('div')) }))
vi.mock('@/components/plugin/PluginConfigEditor.vue', () => ({ default: defineComponent(() => () => h('div')) }))
vi.mock('@/components/logs/LogViewer.vue', () => ({ default: defineComponent(() => () => h('div')) }))
vi.mock('@/components/common/EmptyState.vue', () => ({ default: defineComponent(() => () => h('div')) }))

function surface(overrides: Partial<PluginUiSurface>): PluginUiSurface {
  return {
    id: 'main',
    kind: 'panel',
    mode: 'hosted-tsx',
    title: 'Hosted panel',
    available: true,
    ...overrides,
  }
}

async function mountDetail(surfaces: PluginUiSurface[]) {
  apiMocks.getPluginUiSurfaceInfo.mockResolvedValue({ surfaces, warnings: [] })
  apiMocks.get.mockResolvedValue({ has_ui: true })
  const plugin = {
    id: 'study_companion',
    name: 'Study Companion',
    description: 'Study Companion',
    version: '1.0.0',
    status: 'running',
  }
  apiMocks.getPlugins.mockResolvedValue({ plugins: [plugin] })
  apiMocks.getPluginStatus.mockResolvedValue({ plugin_id: plugin.id, status: { status: 'running' } })

  const container = document.createElement('div')
  document.body.appendChild(container)
  const pinia = createPinia()
  setActivePinia(pinia)
  usePluginStore().plugins = [plugin]
  const app = createApp(PluginDetail)
  app.use(pinia)
  app.config.globalProperties.$t = (key: string) => key
  const passthrough = defineComponent({
    setup(_props, { slots }) {
      return () => h('div', slots.default?.())
    },
  })
  const card = defineComponent({
    setup(_props, { slots }) {
      return () => h('div', [slots.header?.(), slots.default?.()])
    },
  })
  const tabPane = defineComponent({
    props: { label: String, name: String },
    setup(props, { slots }) {
      return () => h('section', { 'data-tab-name': props.name }, slots.default?.())
    },
  })
  app.component('el-card', card)
  app.component('el-tabs', passthrough)
  app.component('el-tab-pane', tabPane)
  app.component('el-alert', passthrough)
  app.component('el-descriptions', passthrough)
  app.component('el-descriptions-item', passthrough)
  app.component('el-tag', passthrough)
  app.component('el-icon', passthrough)
  app.component('el-button', passthrough)
  app.mount(container)
  for (let index = 0; index < 10; index += 1) {
    await Promise.resolve()
    await nextTick()
  }
  return {
    container,
    unmount: () => {
      app.unmount()
      container.remove()
    },
  }
}

describe('PluginDetail legacy static compatibility', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('keeps the legacy main without adding a duplicate static UI tab when hosted panels exist', async () => {
    const mounted = await mountDetail([
      surface({ id: 'study-panel' }),
      surface({ id: 'main', mode: 'static', legacy_static_compat: true }),
    ])

    expect(mounted.container.querySelector('[data-surface-id="study-panel"]')).not.toBeNull()
    expect(mounted.container.querySelector('[data-surface-id="main"]')).not.toBeNull()
    expect(mounted.container.querySelector('[data-tab-name="ui"]')).toBeNull()
    mounted.unmount()
  })

  it('keeps a legacy static surface as the sole panel when no declared panel exists', async () => {
    const mounted = await mountDetail([
      surface({ id: 'main', mode: 'static', legacy_static_compat: true }),
    ])

    expect(mounted.container.querySelector('[data-surface-id="main"]')).not.toBeNull()
    expect(mounted.container.querySelectorAll('[data-tab-name="panel"]')).toHaveLength(1)
    expect(mounted.container.querySelector('[data-tab-name="ui"]')).toBeNull()
    mounted.unmount()
  })
})
