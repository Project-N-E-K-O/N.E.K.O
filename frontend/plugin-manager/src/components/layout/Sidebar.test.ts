// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick } from 'vue'
import { getByRole, queryByRole } from '@testing-library/dom'
import { createPinia, setActivePinia } from 'pinia'
import Sidebar from './Sidebar.vue'
import { usePluginStore } from '@/stores/plugin'

/**
 * 侧栏的适配器子项是进适配器界面页（`adapter/:id/ui`）的唯一入口，所以它的 `to` 必须
 * 把插件 id 编码后再放进路径段：`#` 不编码会被 URL 解析器当成片段、`?` 当成查询、
 * `/` 直接让 `:id` 匹配不上，点下去就是空白页（详情页那条路早就编码过了）。
 */

vi.mock('@/i18n', () => ({
  getLocale: () => 'en-US',
  i18n: { global: { t: (key: string) => key, locale: { value: 'en-US' } } },
}))
const routeState = vi.hoisted(() => ({ path: '/' }))
vi.mock('vue-router', () => ({ useRoute: () => routeState }))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))

/** 取某个标签对应那项路由链接的 `to`。 */
function linkTo(container: HTMLElement, label: string): string | null {
  const wrapper = Array.from(container.querySelectorAll<HTMLElement>('[data-to]')).find((element) =>
    element.textContent?.includes(label),
  )
  return wrapper?.getAttribute('data-to') ?? null
}

async function mountSidebar(): Promise<{ container: HTMLDivElement; unmount: () => void }> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = usePluginStore()
  store.pluginSummaries = [
    { id: 'adapter#demo', name: '哈希适配器', type: 'adapter', status: 'running', version: '1.0.0' },
    { id: 'adapter?demo/next', name: '特殊适配器', type: 'adapter', status: 'running', version: '1.0.0' },
    { id: 'adapter_demo', name: '普通适配器', type: 'adapter', status: 'running', version: '1.0.0' },
    { id: 'study_companion', name: '普通插件', type: 'plugin', status: 'running', version: '1.0.0' },
  ] as never

  const app = createApp(Sidebar)
  app.use(pinia)
  app.config.globalProperties.$t = (key: string) => key
  // router-link 的 `to` 不进 DOM（组件用了 custom + v-slot），所以换成把 to 暴露出来的桩。
  app.component(
    'router-link',
    defineComponent({
      props: { to: { type: [String, Object], required: true }, custom: Boolean },
      setup(props, { slots }) {
        return () =>
          h(
            'div',
            { 'data-to': String(props.to) },
            slots.default?.({ isActive: false, isExactActive: false, navigate: () => {} }),
          )
      },
    }),
  )
  app.component('el-icon', defineComponent({ setup(_props, { slots }) { return () => h('span', slots.default?.()) } }))
  app.mount(container)
  for (let index = 0; index < 3; index += 1) {
    await Promise.resolve()
    await nextTick()
  }
  return { container, unmount: () => { app.unmount(); container.remove() } }
}

describe('Sidebar navigation', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    routeState.path = '/'
    const saved = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => saved.get(key) ?? null,
      setItem: (key: string, value: string) => saved.set(key, value),
    })
  })

  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

  it('桌面侧栏可收起并恢复偏好，图标入口仍有名称', async () => {
    let mounted = await mountSidebar()
    const toggle = getByRole(mounted.container, 'button', { name: 'nav.collapseSidebar' })
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    toggle.click()
    await nextTick()
    expect(getByRole(mounted.container, 'button', { name: 'nav.expandSidebar' }).getAttribute('aria-expanded')).toBe('false')
    expect(getByRole(mounted.container, 'button', { name: 'nav.plugins' }).getAttribute('title')).toBe('nav.plugins')
    mounted.unmount()

    mounted = await mountSidebar()
    expect(getByRole(mounted.container, 'button', { name: 'nav.expandSidebar' }).getAttribute('aria-expanded')).toBe('false')
    mounted.unmount()
  })

  it('适配器分组可独立收起，隐藏的入口不在可访问树中', async () => {
    const mounted = await mountSidebar()
    const toggle = getByRole(mounted.container, 'button', { name: 'nav.adapters' })
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    getByRole(mounted.container, 'button', { name: '哈希适配器' }).focus()
    toggle.click()
    await nextTick()
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(queryByRole(mounted.container, 'button', { name: '哈希适配器' })).toBeNull()
    expect(document.activeElement).toBe(toggle)
    toggle.click()
    await nextTick()
    expect(getByRole(mounted.container, 'button', { name: '哈希适配器' })).toBeTruthy()
    mounted.unmount()
  })

  it('窄屏自动使用图标导航，返回桌面保留原展开偏好并安置焦点', async () => {
    const media = new EventTarget() as MediaQueryList
    Object.defineProperty(media, 'matches', { value: false, writable: true })
    vi.spyOn(window, 'matchMedia').mockReturnValue(media)
    const mounted = await mountSidebar()
    getByRole(mounted.container, 'button', { name: 'nav.collapseSidebar' }).focus()
    Object.assign(media, { matches: true })
    media.dispatchEvent(Object.assign(new Event('change'), { matches: true }))
    await nextTick()
    expect(queryByRole(mounted.container, 'button', { name: 'nav.expandSidebar' })).toBeNull()
    expect(queryByRole(mounted.container, 'button', { name: 'nav.collapseSidebar' })).toBeNull()
    expect(getByRole(mounted.container, 'button', { name: 'nav.dashboard' }).getAttribute('title')).toBe('nav.dashboard')
    expect(document.activeElement).toBe(getByRole(mounted.container, 'button', { name: 'nav.dashboard' }))
    Object.assign(media, { matches: false })
    media.dispatchEvent(Object.assign(new Event('change'), { matches: false }))
    await nextTick()
    expect(getByRole(mounted.container, 'button', { name: 'nav.collapseSidebar' }).getAttribute('aria-expanded')).toBe('true')
    mounted.unmount()
  })

  it('存储被拒绝时仍可展开收起和访问原导航', async () => {
    vi.stubGlobal('localStorage', {
      getItem: () => { throw new DOMException('Access denied', 'SecurityError') },
      setItem: () => { throw new DOMException('Access denied', 'SecurityError') },
    })
    const mounted = await mountSidebar()
    getByRole(mounted.container, 'button', { name: 'nav.collapseSidebar' }).click()
    await nextTick()
    expect(getByRole(mounted.container, 'button', { name: 'nav.expandSidebar' }).getAttribute('aria-expanded')).toBe('false')
    getByRole(mounted.container, 'button', { name: 'nav.expandSidebar' }).click()
    await nextTick()
    expect(getByRole(mounted.container, 'button', { name: 'nav.collapseSidebar' })).toBeTruthy()
    mounted.unmount()
  })

  it('保留七个主入口顺序与路径，折叠后详情页仍标记插件管理为当前页', async () => {
    routeState.path = '/plugins/demo'
    const mounted = await mountSidebar()
    expect(Array.from(mounted.container.querySelectorAll('[data-to]')).slice(0, 7).map((element) => element.getAttribute('data-to'))).toEqual([
      '/', '/plugins', '/knowledge', '/model-api', '/runs', '/logs/_server', '/development',
    ])
    expect(getByRole(mounted.container, 'button', { name: 'nav.plugins' }).getAttribute('aria-current')).toBe('page')
    getByRole(mounted.container, 'button', { name: 'nav.collapseSidebar' }).click()
    await nextTick()
    expect(getByRole(mounted.container, 'button', { name: 'nav.plugins' }).getAttribute('aria-current')).toBe('page')
    expect(getByRole(mounted.container, 'button', { name: 'nav.plugins' }).getAttribute('data-yui-guide-id')).toBe('sidebar-plugins')
    mounted.unmount()
  })

  it('适配器子项的 id 编码后再进路径段，普通 id 不受影响', async () => {
    const mounted = await mountSidebar()

    expect(linkTo(mounted.container, '哈希适配器'), '带 # 的 id 没编码，点下去会被当成 URL 片段').toBe(
      '/adapter/adapter%23demo/ui',
    )
    expect(linkTo(mounted.container, '普通适配器')).toBe('/adapter/adapter_demo/ui')
    expect(linkTo(mounted.container, '特殊适配器')).toBe('/adapter/adapter%3Fdemo%2Fnext/ui')
    mounted.unmount()
  })

  it('非 adapter 类型的插件不进适配器分组', async () => {
    const mounted = await mountSidebar()

    expect(linkTo(mounted.container, '普通插件')).toBeNull()
    mounted.unmount()
  })
})
