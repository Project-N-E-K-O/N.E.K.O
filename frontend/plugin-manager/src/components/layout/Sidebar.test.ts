// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick } from 'vue'
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
vi.mock('vue-router', () => ({ useRoute: () => ({ path: '/' }) }))
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
  store.plugins = [
    { id: 'adapter#demo', name: '哈希适配器', type: 'adapter', status: 'running', version: '1.0.0' },
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

describe('Sidebar adapter links', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('适配器子项的 id 编码后再进路径段，普通 id 不受影响', async () => {
    const mounted = await mountSidebar()

    expect(linkTo(mounted.container, '哈希适配器'), '带 # 的 id 没编码，点下去会被当成 URL 片段').toBe(
      '/adapter/adapter%23demo/ui',
    )
    expect(linkTo(mounted.container, '普通适配器')).toBe('/adapter/adapter_demo/ui')
    mounted.unmount()
  })

  it('非 adapter 类型的插件不进适配器分组', async () => {
    const mounted = await mountSidebar()

    expect(linkTo(mounted.container, '普通插件')).toBeNull()
    mounted.unmount()
  })
})
