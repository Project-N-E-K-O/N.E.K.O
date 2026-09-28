// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, ref } from 'vue'
import ElementPlus from 'element-plus'
import PluginConfigEditor from './PluginConfigEditor.vue'

const api = vi.hoisted(() => ({
  getPluginConfig: vi.fn(), getPluginEffectiveBaseConfig: vi.fn(),
  getPluginProfilesState: vi.fn(), getPluginProfileConfig: vi.fn(),
  upsertPluginProfileConfig: vi.fn(), deletePluginProfileConfig: vi.fn(),
}))
vi.mock('@/api/config', () => api)
vi.mock('@/utils/request', () => ({ isRequestTimeout: () => false }))
vi.mock('@/stores/plugin', () => ({ usePluginStore: () => ({ reload: vi.fn() }) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ replace: vi.fn() }) }))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ locale: ref('en-US'), t: (key: string) => key }) }))

let dispose: (() => void) | undefined
beforeEach(() => {
  vi.clearAllMocks()
  api.getPluginConfig.mockResolvedValue({ config: { search: { query: 'old' } } })
  api.getPluginEffectiveBaseConfig.mockResolvedValue({
    config: { search: { query: 'old', count: 8 } },
    config_schema: { type: 'object', properties: {
      search: { type: 'object', properties: {
        query: { type: 'string', title: 'Search query', description: 'Words to look up' },
      } },
    } },
  })
  api.getPluginProfilesState.mockResolvedValue({ config_profiles: { active: 'prod', files: { draft: {} } } })
  api.getPluginProfileConfig.mockResolvedValue({ config: {} })
  api.upsertPluginProfileConfig.mockResolvedValue({})
})
afterEach(() => { dispose?.(); dispose = undefined })

async function mount() {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp(PluginConfigEditor, { pluginId: 'demo' })
  app.use(ElementPlus)
  app.mount(host)
  dispose = () => { app.unmount(); host.remove() }
  await vi.waitFor(() => expect(host.querySelector('.pcf input')).not.toBeNull())
  return host
}

describe('PluginConfigEditor schema integration', () => {
  it('loads field metadata from the API and saves only edited profile values', async () => {
    const host = await mount()
    expect(api.getPluginEffectiveBaseConfig).toHaveBeenCalledWith('demo')
    expect(host.textContent).toContain('Words to look up')
    const title = Array.from(host.querySelectorAll('.field-title')).find((node) => node.textContent === 'Search query')!
    const input = title.closest('.row')!.querySelector('input')!
    input.value = 'new'
    input.dispatchEvent(new Event('input'))
    input.dispatchEvent(new Event('change'))
    await nextTick()
    const save = Array.from(host.querySelectorAll('button')).find((node) => node.textContent?.trim() === 'common.save')!
    save.click()
    await vi.waitFor(() => expect(api.upsertPluginProfileConfig).toHaveBeenCalledWith(
      'demo', 'draft', { search: { query: 'new' } }, false,
    ))
  })

  it('shows a translated warning and keeps generic editing available for invalid schemas', async () => {
    api.getPluginEffectiveBaseConfig.mockResolvedValue({
      config: { query: 'old' }, config_schema: null,
      warnings: [{ code: 'PLUGIN_CONFIG_EDITOR_SCHEMA_INVALID' }],
    })
    const host = await mount()
    expect(host.textContent).toContain('plugins.configSchemaInvalid')
    expect(host.querySelector('.pcf input')).not.toBeNull()
  })
})
