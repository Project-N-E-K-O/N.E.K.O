// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'
import ElementPlus from 'element-plus'
import PluginConfigEditor from './PluginConfigEditor.vue'

vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
vi.mock('@/utils/request', () => ({ isRequestTimeout: () => false }))
vi.mock('vue-router', () => ({ onBeforeRouteLeave: vi.fn(), onBeforeRouteUpdate: vi.fn() }))
vi.mock('@/stores/plugin', () => ({ usePluginStore: () => ({ reload: vi.fn() }) }))
vi.mock('@/api/config', () => ({
  getPluginEffectiveBaseConfig: async () => ({
    config: {
      plugin_runtime: { enabled: true },
      search: { max_results: 8 },
      cache: { ttl: 120 },
      plugin: { id: 'test' },
    },
  }),
  getPluginConfig: async () => ({ config: {} }),
  getPluginProfilesState: async () => ({ config_profiles: null }),
  hotUpdatePluginConfig: vi.fn(),
}))
const cleanups: (() => void)[] = []
afterEach(() => {
  cleanups.splice(0).forEach((cleanup) => cleanup())
  vi.restoreAllMocks()
})
async function mountEditor() {
  const host = document.createElement('main')
  host.dataset.yuiGuideId = 'plugin-main'
  Object.defineProperty(host, 'clientHeight', { value: 1000 })
  document.body.append(host)
  const app = createApp(PluginConfigEditor, { pluginId: 'test' })
  app.use(ElementPlus)
  app.mount(host)
  cleanups.push(() => {
    app.unmount()
    host.remove()
  })
  await vi.waitFor(() => expect(host.querySelectorAll('.config-nav button')).toHaveLength(3))
  return host
}
async function search(host: HTMLElement, value: string) {
  const input = host.querySelector<HTMLInputElement>('.config-toolbar input')!
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()
  await nextTick()
}

describe('configuration navigation boundaries', () => {
  it('matches field order, hides protected metadata, and keeps runtime last', async () => {
    const host = await mountEditor()
    expect([...host.querySelectorAll('.config-nav button span')].map((e) => e.textContent)).toEqual(
      ['search', 'cache', 'plugin_runtime']
    )
  })

  it('disables nonmatching sections and handles an empty search result', async () => {
    const host = await mountEditor()
    await search(host, 'max_results')
    const buttons = [...host.querySelectorAll<HTMLButtonElement>('.config-nav button')]
    expect(buttons.map((button) => button.disabled)).toEqual([false, true, true])
    await search(host, 'no-such-field')
    expect(buttons.every((button) => button.disabled)).toBe(true)
    expect(host.textContent).toContain('plugins.configUi.emptySearch')
  })

  it('jumps within the editor without clearing search or changing an unsaved draft', async () => {
    const host = await mountEditor()
    await search(host, 'max_results')
    const input = host.querySelector<HTMLInputElement>('input[aria-label="search.max_results"]')!
    input.value = '9'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    const pane = host.querySelector<HTMLElement>('.config-content')!
    const scroll = vi.spyOn(pane, 'scrollTo').mockImplementation(() => {})
    const row = host.querySelector<HTMLElement>('[data-config-path="search"]')!
    vi.spyOn(row, 'getClientRects').mockReturnValue([{}] as unknown as DOMRectList)
    vi.spyOn(row, 'getBoundingClientRect').mockReturnValue(new DOMRect(0, 120, 100, 50))
    host.querySelector<HTMLButtonElement>('.config-nav button')!.click()
    await nextTick()
    await nextTick()
    expect(scroll).toHaveBeenCalled()
    expect(host.querySelector<HTMLInputElement>('.config-toolbar input')!.value).toBe('max_results')
    expect(input.value).toBe('9')
    expect(host.querySelector('.config-footer')?.textContent).toContain(
      'plugins.configUi.unsavedCount'
    )
  })
})
