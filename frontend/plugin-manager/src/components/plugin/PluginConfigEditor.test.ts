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

describe('PluginConfigEditor confidential values', () => {
  it.each([
    { overridden: false, dynamic: false }, { overridden: true, dynamic: false },
    { overridden: false, dynamic: true }, { overridden: true, dynamic: true },
  ])('masks secrets and saves real edits (override=$overridden, dynamic=$dynamic)', async ({ overridden, dynamic }) => {
    const baseline = { auth: { token: 'fixture-baseline-token' }, servers: [{ token: 'fixture-array-token' }] }
    api.getPluginConfig.mockResolvedValue({ config: baseline })
    api.getPluginEffectiveBaseConfig.mockResolvedValue({
      config: baseline,
      config_schema: { type: 'object', properties: {
        auth: { type: 'object', ...(dynamic ? {
          additionalProperties: { type: 'string', title: 'Access token', writeOnly: true },
        } : { properties: {
          token: { type: 'string', title: 'Access token', writeOnly: true },
        } }) },
        servers: { type: 'array', items: { type: 'object', ...(dynamic ? {
          additionalProperties: { type: 'string', writeOnly: true },
        } : { properties: {
          token: { type: 'string', writeOnly: true },
        } }) } },
      } },
    })
    api.getPluginProfileConfig.mockResolvedValue({
      config: overridden ? { auth: { token: 'fixture-profile-token' } } : {},
    })
    const host = await mount()
    const inputs = host.querySelectorAll<HTMLInputElement>('input[type="password"]')
    expect(inputs).toHaveLength(2)
    expect(inputs[0]!.value).toBe(overridden ? 'fixture-profile-token' : 'fixture-baseline-token')
    expect(inputs[1]!.value).toBe('fixture-array-token')
    expect(host.querySelector('.diff-body')!.textContent).toContain('********')
    expect(host.textContent).not.toContain('fixture-')
    inputs[0]!.value = 'fixture-edited-token'
    inputs[0]!.dispatchEvent(new Event('input'))
    inputs[0]!.dispatchEvent(new Event('change'))
    await nextTick()
    expect(host.textContent).not.toContain('fixture-')
    const save = Array.from(host.querySelectorAll('button')).find((node) => node.textContent?.trim() === 'common.save')!
    save.click()
    await vi.waitFor(() => expect(api.upsertPluginProfileConfig).toHaveBeenCalledWith(
      'demo', 'draft', { auth: { token: 'fixture-edited-token' } }, false,
    ))
    expect(baseline.auth.token).toBe('fixture-baseline-token')
    expect(baseline.servers[0]!.token).toBe('fixture-array-token')
  })
})
