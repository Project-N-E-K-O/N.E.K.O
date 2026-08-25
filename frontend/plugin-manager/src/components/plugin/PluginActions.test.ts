// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import PluginActions from './PluginActions.vue'
import { usePluginStore } from '@/stores/plugin'

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
