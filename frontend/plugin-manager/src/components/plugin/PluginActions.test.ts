// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import PluginActions from './PluginActions.vue'
import { usePluginStore } from '@/stores/plugin'
import { PLUGIN_DETAIL_REFRESH_HOSTED_PANELS_KEY } from '@/views/pluginDetailHostedPanelRefresh'

const apiMocks = vi.hoisted(() => ({
  getPlugins: vi.fn(),
  getPluginStatus: vi.fn(),
  startPlugin: vi.fn(),
  stopPlugin: vi.fn(),
  reloadPlugin: vi.fn(),
  refreshPluginsRegistry: vi.fn(),
}))

vi.mock('@/api/plugins', () => apiMocks)
vi.mock('@/i18n', () => ({ getLocale: () => 'en-US' }))
vi.mock('vue-i18n', () => ({
  useI18n: () => ({ locale: ref('en-US'), t: (key: string) => key }),
}))
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), resolve: vi.fn(() => ({ href: '/plugins/demo?tab=ui' })) }),
}))
vi.mock('@element-plus/icons-vue', () => ({
  Monitor: {},
  Refresh: {},
  VideoPause: {},
  VideoPlay: {},
}))
vi.mock('@/utils/openExternal', () => ({ openExternalUrl: vi.fn() }))

const elementPlusMocks = vi.hoisted(() => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn(() => Promise.resolve('confirm')) },
}))
// 部分 mock：只替换全局反馈组件。`unplugin-vue-components` 的 ElementPlusResolver
// 会把模板里的 <el-button> 编译成显式 `import { ElButton } from 'element-plus'`，
// 整模块 mock 会让这些自动导入变成 undefined 并让渲染直接抛错。
vi.mock('element-plus', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  ...elementPlusMocks,
}))

function stubButtons(app: ReturnType<typeof createApp>) {
  app.component('el-button', defineComponent({
    props: { disabled: Boolean },
    setup(props, { slots }) {
      return () => h('button', { disabled: props.disabled }, slots.default?.())
    },
  }))
  app.component('el-button-group', defineComponent({
    setup(_props, { slots }) {
      return () => h('div', slots.default?.())
    },
  }))
}

async function flushPromises() {
  for (let i = 0; i < 8; i += 1) await nextTick()
}

describe('PluginActions UI action', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('shows the detail-header button when the plugin declares an action with kind ui', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = usePluginStore()
    store.plugins = [{
      id: 'demo',
      name: 'Demo',
      description: 'Demo',
      version: '1.0.0',
      status: 'running',
      list_actions: [{ id: 'open_ui', kind: 'ui', label: 'Open learning UI' }],
    }]
    const container = document.createElement('div')
    document.body.appendChild(container)
    const app = createApp(PluginActions, { pluginId: 'demo' })
    app.use(pinia)
    app.component('el-button', defineComponent({
      props: { disabled: Boolean },
      setup(props, { slots }) {
        return () => h('button', { disabled: props.disabled }, slots.default?.())
      },
    }))
    app.component('el-button-group', defineComponent({
      setup(_props, { slots }) {
        return () => h('div', slots.default?.())
      },
    }))
    app.mount(container)
    await nextTick()

    const labels = Array.from(container.querySelectorAll('button')).map((button) => button.textContent?.trim())
    expect(labels).toContain('Open learning UI')

    app.unmount()
    container.remove()
  })

  it.each(['url', 'route'] as const)(
    'also shows the detail-header button when open_ui uses kind %s',
    async (kind) => {
      const pinia = createPinia()
      setActivePinia(pinia)
      const store = usePluginStore()
      store.plugins = [{
        id: 'demo',
        name: 'Demo',
        description: 'Demo',
        version: '1.0.0',
        status: 'running',
        list_actions: [{ id: 'open_ui', kind, label: 'Open learning UI' }],
      }]
      const container = document.createElement('div')
      document.body.appendChild(container)
      const app = createApp(PluginActions, { pluginId: 'demo' })
      app.use(pinia)
      app.component('el-button', defineComponent({
        props: { disabled: Boolean },
        setup(props, { slots }) {
          return () => h('button', { disabled: props.disabled }, slots.default?.())
        },
      }))
      app.component('el-button-group', defineComponent({
        setup(_props, { slots }) {
          return () => h('div', slots.default?.())
        },
      }))
      app.mount(container)
      await nextTick()

      expect(container.textContent).toContain('Open learning UI')

      app.unmount()
      container.remove()
    },
  )

  it('does not show the UI button for non-ui list actions', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = usePluginStore()
    store.plugins = [{
      id: 'demo',
      name: 'Demo',
      description: 'Demo',
      version: '1.0.0',
      status: 'running',
      list_actions: [{ id: 'open_docs', kind: 'route', label: 'Open docs' }],
    }]
    const container = document.createElement('div')
    document.body.appendChild(container)
    const app = createApp(PluginActions, { pluginId: 'demo' })
    app.use(pinia)
    app.component('el-button', defineComponent({
      setup(_props, { slots }) {
        return () => h('button', slots.default?.())
      },
    }))
    app.component('el-button-group', defineComponent({
      setup(_props, { slots }) {
        return () => h('div', slots.default?.())
      },
    }))
    app.mount(container)
    await nextTick()

    expect(container.textContent).not.toContain('Open docs')

    app.unmount()
    container.remove()
  })
})

describe('PluginActions runtime mutations', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    elementPlusMocks.ElMessage.success.mockClear()
    elementPlusMocks.ElMessageBox.confirm.mockClear()
  })

  it.each([
    ['start', 'plugins.start', 'messages.pluginStarted'],
    ['reload', 'plugins.reload', 'messages.pluginReloaded'],
  ] as const)(
    'reports %s without waiting for the hosted panel refresh chain',
    async (action, buttonLabel, successKey) => {
      const pinia = createPinia()
      setActivePinia(pinia)
      const store = usePluginStore()
      store.plugins = [{
        id: 'demo',
        name: 'Demo',
        description: 'Demo',
        version: '1.0.0',
        status: 'stopped',
      }]
      const mutationSpy = vi.spyOn(store, action).mockResolvedValue(undefined)
      // Stands in for a hosted panel whose context re-query never settles.
      // Awaiting it would hold both the spinner and the success toast, which is
      // what every plugin paid for before this was made fire-and-forget.
      const refreshHostedPanels = vi.fn(() => new Promise<void>(() => {}))

      const container = document.createElement('div')
      document.body.appendChild(container)
      const app = createApp(PluginActions, { pluginId: 'demo' })
      app.use(pinia)
      app.provide(PLUGIN_DETAIL_REFRESH_HOSTED_PANELS_KEY, refreshHostedPanels)
      stubButtons(app)
      app.mount(container)
      await nextTick()

      const button = Array.from(container.querySelectorAll('button'))
        .find((candidate) => candidate.textContent?.trim() === buttonLabel)
      expect(button).toBeTruthy()
      button!.dispatchEvent(new Event('click'))
      await flushPromises()

      expect(mutationSpy).toHaveBeenCalledWith('demo')
      expect(refreshHostedPanels).toHaveBeenCalledTimes(1)
      expect(elementPlusMocks.ElMessage.success).toHaveBeenCalledWith(successKey)

      app.unmount()
      container.remove()
    },
  )
})
