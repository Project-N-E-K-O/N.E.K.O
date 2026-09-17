// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, type Ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import AdapterUI from './AdapterUI.vue'
import type { PluginUiSurface } from '@/types/api'
import { usePluginStore } from '@/stores/plugin'

/**
 * 适配器界面页的两条回归：
 *
 *   1. 语言切换后必须按新 locale 重取 surface 列表。面板正文由 HostedSurfaceFrame 自己
 *      负责（它 watch locale 去 /hosted-ui/source 取文档），但 surface 的 `title` 是
 *      /surfaces 按 locale 给的，而 markdown 的 title 会被写进文档的 <h1> —— 不重取就会
 *      出现"新语言的正文 + 旧语言的标题"。
 *   2. 面板要求打开日志页时，插件 id 必须编码后再进路径段。`#` 不编码会被路由拆成 hash，
 *      于是日志页开到另一个插件上。
 */

const apiMocks = vi.hoisted(() => ({ getPluginUiSurfaceInfo: vi.fn() }))
const routerMocks = vi.hoisted(() => ({
  push: vi.fn(),
  route: {
    params: { id: 'adapter_demo' } as Record<string, string>,
    query: {} as Record<string, string>,
  },
}))
// watch(locale) 必须挂在真的 ref 上才会触发，而 vi.hoisted 里拿不到 vue 的 ref，
// 于是由 mock 工厂在求值时把同一个 ref 挂出来给用例用。
const i18nHolder = vi.hoisted(() => ({ locale: null as unknown as Ref<string> }))

vi.mock('@/api/plugins', () => ({ getPluginUiSurfaceInfo: apiMocks.getPluginUiSurfaceInfo }))
// store / request 会从 '@/i18n' 取真实的 i18n 实例，而那边模块体里就 createI18n()；
// 这里与 PluginDetail.test.ts 一样把它挡掉，只留用例真正需要的那两个面。
vi.mock('@/i18n', () => ({
  getLocale: () => 'en-US',
  i18n: { global: { t: (key: string) => key, locale: { value: 'en-US' } } },
}))
vi.mock('vue-router', () => ({
  useRoute: () => routerMocks.route,
  useRouter: () => ({ push: routerMocks.push, replace: vi.fn() }),
}))
vi.mock('vue-i18n', async () => {
  const { ref } = await import('vue')
  const locale = ref('en-US')
  i18nHolder.locale = locale as Ref<string>
  return { useI18n: () => ({ locale, t: (key: string) => key }) }
})
vi.mock('@/components/plugin/HostedSurfaceFrame.vue', async () => {
  const { defineComponent: define, h: render } = await import('vue')
  return {
    default: define({
      props: { pluginId: String, surface: Object },
      emits: ['open-logs', 'message'],
      setup(props, { emit, expose }) {
        expose({ refreshContext: vi.fn(), sendSurfaceMessage: vi.fn() })
        return () =>
          render('div', {
            'data-testid': 'hosted-surface-frame',
            'data-surface-title': (props.surface as PluginUiSurface | null)?.title ?? '',
            onClick: () => emit('open-logs'),
          })
      },
    }),
  }
})
vi.mock('@/components/plugin/PluginUIFrame.vue', async () => {
  const { defineComponent: define, h: render } = await import('vue')
  return { default: define(() => () => render('div', { 'data-testid': 'legacy-ui' })) }
})
vi.mock('@/components/common/StatusIndicator.vue', async () => {
  const { defineComponent: define, h: render } = await import('vue')
  return { default: define(() => () => render('div')) }
})
vi.mock('@/components/common/EmptyState.vue', async () => {
  const { defineComponent: define, h: render } = await import('vue')
  return { default: define(() => () => render('div', { 'data-testid': 'empty-state' })) }
})

const ADAPTER_ID = 'adapter_demo'

function panel(overrides: Partial<PluginUiSurface> = {}): PluginUiSurface {
  return { id: 'main', kind: 'panel', mode: 'markdown', title: 'Adapter Panel', available: true, ...overrides }
}

async function flush(rounds = 10) {
  for (let index = 0; index < rounds; index += 1) {
    await Promise.resolve()
    await nextTick()
  }
}

async function mountAdapter(id = ADAPTER_ID): Promise<{ container: HTMLDivElement; unmount: () => void }> {
  routerMocks.route.params.id = id
  const container = document.createElement('div')
  document.body.appendChild(container)
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = usePluginStore()
  store.plugins = [{ id, name: 'Adapter Demo', description: '', version: '1.0.0', status: 'running' } as never]
  const app = createApp(AdapterUI)
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
  app.component('el-card', card)
  app.component('el-alert', passthrough)
  app.component('el-tag', passthrough)
  app.component('el-icon', passthrough)
  app.component('el-button', passthrough)
  app.mount(container)
  await flush()
  return { container, unmount: () => { app.unmount(); container.remove() } }
}

function panelTitle(container: HTMLElement): string | null {
  return container.querySelector('[data-testid="hosted-surface-frame"]')?.getAttribute('data-surface-title') ?? null
}

describe('AdapterUI surface loading', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    apiMocks.getPluginUiSurfaceInfo.mockReset()
    routerMocks.push.mockReset()
    routerMocks.route.params.id = ADAPTER_ID
    i18nHolder.locale.value = 'en-US'
  })

  it('语言切换后按新 locale 重取 surface 列表，面板标题跟着换', async () => {
    apiMocks.getPluginUiSurfaceInfo.mockResolvedValue({
      surfaces: [panel({ title: 'Adapter Panel' })],
      warnings: [],
    })
    const mounted = await mountAdapter()

    expect(apiMocks.getPluginUiSurfaceInfo).toHaveBeenCalledWith(ADAPTER_ID, 'en-US')
    expect(panelTitle(mounted.container)).toBe('Adapter Panel')

    apiMocks.getPluginUiSurfaceInfo.mockResolvedValue({
      surfaces: [panel({ title: '适配器面板' })],
      warnings: [],
    })
    i18nHolder.locale.value = 'zh-CN'
    await flush()

    expect(apiMocks.getPluginUiSurfaceInfo).toHaveBeenLastCalledWith(ADAPTER_ID, 'zh-CN')
    expect(panelTitle(mounted.container), '切语言后没有把新的本地化标题交给面板').toBe('适配器面板')
    mounted.unmount()
  })

  it('慢的先发请求后回来时不会盖掉后一次的结果', async () => {
    let resolveSlow: ((value: unknown) => void) | undefined
    apiMocks.getPluginUiSurfaceInfo
      .mockResolvedValueOnce({ surfaces: [panel({ title: 'EN' })], warnings: [] })
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSlow = resolve }))
      .mockResolvedValueOnce({ surfaces: [panel({ title: 'JA' })], warnings: [] })
    const mounted = await mountAdapter()
    expect(panelTitle(mounted.container)).toBe('EN')

    i18nHolder.locale.value = 'zh-CN'
    await flush()
    i18nHolder.locale.value = 'ja-JP'
    await flush()
    expect(panelTitle(mounted.container)).toBe('JA')

    resolveSlow?.({ surfaces: [panel({ title: 'ZH' })], warnings: [] })
    await flush()
    expect(panelTitle(mounted.container), 'zh-CN 那次迟到的回包盖掉了 ja-JP 的结果').toBe('JA')
    mounted.unmount()
  })

  it('首次加载就失败时仍然退回旧式静态 UI', async () => {
    apiMocks.getPluginUiSurfaceInfo.mockRejectedValueOnce(new Error('boom'))
    const mounted = await mountAdapter()

    expect(panelTitle(mounted.container)).toBeNull()
    expect(
      mounted.container.querySelector('[data-testid="legacy-ui"]'),
      '首次就取不到 surface 时应该交给 PluginUIFrame',
    ).not.toBeNull()
    mounted.unmount()
  })

  it('切语言的这次重取失败时保留已经拿到的 surface，不把能用的面板拆掉', async () => {
    apiMocks.getPluginUiSurfaceInfo
      .mockResolvedValueOnce({ surfaces: [panel({ title: 'Adapter Panel' })], warnings: [] })
      .mockRejectedValueOnce(new Error('boom'))
    const mounted = await mountAdapter()
    expect(panelTitle(mounted.container)).toBe('Adapter Panel')

    i18nHolder.locale.value = 'zh-CN'
    await flush()

    expect(apiMocks.getPluginUiSurfaceInfo).toHaveBeenCalledTimes(2)
    expect(panelTitle(mounted.container), '重取失败把已经能用的面板清掉了').toBe('Adapter Panel')
    expect(
      mounted.container.querySelector('[data-testid="legacy-ui"]'),
      '重取失败时不该降级回旧式 UI',
    ).toBeNull()
    mounted.unmount()
  })

  it('面板要求打日志页时，插件 id 会被编码后再进路径段', async () => {
    apiMocks.getPluginUiSurfaceInfo.mockResolvedValue({ surfaces: [panel()], warnings: [] })
    const mounted = await mountAdapter('adapter#demo')

    mounted.container.querySelector<HTMLElement>('[data-testid="hosted-surface-frame"]')?.click()
    await flush()

    expect(routerMocks.push).toHaveBeenCalledWith({
      path: '/plugins/adapter%23demo',
      query: { tab: 'logs' },
    })
    mounted.unmount()
  })
})
