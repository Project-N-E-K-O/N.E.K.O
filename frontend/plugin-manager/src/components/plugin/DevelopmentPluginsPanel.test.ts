// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick } from 'vue'
import DevelopmentPluginsPanel from './DevelopmentPluginsPanel.vue'
import { usePluginStore } from '@/stores/plugin'
import { getDevelopment, registerDevelopment, removeDevelopment, runDevelopmentAction, setDevelopmentEnabled, downloadDevelopmentPackage } from '@/api/development'
import { buildPluginCli } from '@/api/pluginCli'
import { ElMessage } from 'element-plus'
const record = { registration_id: 'reg-1', revision: 7, plugin_id: 'demo', source_dir: 'C:/中文 folder/demo', name: 'Demo', version: '1', entry: 'plugins.demo:Demo', error: null }
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/utils/request', () => ({ formatHttpError: (error: unknown) => error instanceof Error ? error.message : String(error) }))
vi.mock('@/stores/plugin', async () => {
  const { reactive } = await import('vue')
  const store = reactive({ pluginsWithStatus: [{ id: 'demo', status: 'running', entries: [] }], fetchPlugins: vi.fn(), fetchPluginStatus: vi.fn() })
  return { usePluginStore: () => store }
})
vi.mock('@/api/development', () => ({ getDevelopment: vi.fn(), setDevelopmentEnabled: vi.fn(), registerDevelopment: vi.fn(), rebindDevelopment: vi.fn(), removeDevelopment: vi.fn(), runDevelopmentAction: vi.fn(), downloadDevelopmentPackage: vi.fn() }))
vi.mock('@/api/pluginCli', () => ({ buildPluginCli: vi.fn(), downloadPluginPackage: vi.fn() }))
vi.mock('element-plus', () => ({ ElMessage: { success: vi.fn() }, ElMessageBox: { confirm: vi.fn().mockResolvedValue(true) } }))
let teardown = () => {}
const settle = async () => { for (let i = 0; i < 8; i++) await nextTick() }
function mount() {
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp(DevelopmentPluginsPanel)
  const passthrough = defineComponent({ setup: (_, { slots }) => () => h('div', slots.default?.()) })
  app.component('ElButton', defineComponent({ inheritAttrs: false, props: ['disabled'], emits: ['click'], setup: (props, { slots, emit }) => () => h('button', { disabled: props.disabled, onClick: () => emit('click') }, slots.default?.()) }))
  app.component('ElSwitch', defineComponent({ props: ['modelValue', 'disabled'], emits: ['change'], setup: (props, { emit }) => () => h('input', { type: 'checkbox', checked: props.modelValue, disabled: props.disabled, onChange: (event: Event) => emit('change', (event.target as HTMLInputElement).checked) }) }))
  app.component('ElInput', defineComponent({ props: ['modelValue', 'disabled'], emits: ['update:modelValue', 'input'], setup: (props, { emit }) => () => h('input', { value: props.modelValue, disabled: props.disabled, onInput: (event: Event) => { emit('update:modelValue', (event.target as HTMLInputElement).value); emit('input') } }) }))
  app.component('ElDialog', defineComponent({ props: ['modelValue'], setup: (props, { slots }) => () => props.modelValue ? h('div', { role: 'dialog' }, [slots.default?.(), slots.footer?.()]) : null }))
  app.component('ElAlert', defineComponent({ props: ['title'], setup: (props) => () => h('p', { role: 'alert' }, props.title) }))
  app.component('ElTag', passthrough)
  app.mount(root)
  teardown = () => { app.unmount(); root.remove() }
  return root
}
function button(root: Element, key: string) { return [...root.querySelectorAll('button')].find((item) => item.textContent === key)! }
beforeEach(() => {
  vi.clearAllMocks()
  const store = usePluginStore()
  store.pluginsWithStatus[0]!.status = 'running'
  store.pluginsWithStatus[0]!.entries = []
  vi.mocked(store.fetchPlugins).mockReset()
  vi.mocked(store.fetchPluginStatus).mockReset()
  vi.mocked(getDevelopment).mockResolvedValue({ enabled: true, registrations: [{ ...record }] })
  vi.mocked(registerDevelopment).mockResolvedValue({ ...record })
})
afterEach(() => teardown())
describe('development plugin workflow', () => {
  it('awaits the protected download and displays a download failure', async () => {
    vi.mocked(buildPluginCli).mockResolvedValueOnce({
      ok: true, built_count: 1, failed_count: 0, failed: [],
      built: [{
        plugin_id: 'demo', package_type: 'plugin', plugin_ids: ['demo'],
        package_path: 'C:/packages-development/demo.neko-plugin', profile_files: [], staged_files: [],
        payload_hash: 'hash', package_size_bytes: 1, staged_file_count: 0, profile_file_count: 0,
      }],
    })
    let rejectDownload!: (error: Error) => void
    vi.mocked(downloadDevelopmentPackage).mockImplementationOnce(() => new Promise((_, reject) => { rejectDownload = reject }))
    const root = mount()
    await settle()
    button(root, 'development.build').click()
    await settle()
    expect(downloadDevelopmentPackage).toHaveBeenCalledWith('C:/packages-development/demo.neko-plugin')
    expect(button(root, 'development.build').disabled).toBe(true)
    expect(ElMessage.success).not.toHaveBeenCalled()
    rejectDownload(new Error('download denied'))
    await settle()
    expect(root.textContent).toContain('download denied')
    expect(button(root, 'development.build').disabled).toBe(false)
    expect(ElMessage.success).not.toHaveBeenCalled()
  })
  it('shows a degraded startup error while keeping Stop available', async () => {
    vi.mocked(getDevelopment).mockResolvedValueOnce({ enabled: true, registrations: [{ ...record, runtime_alive: true, error: 'startup hook failed' }] })
    const root = mount()
    await settle()
    expect(root.textContent).toContain('startup hook failed')
    expect(button(root, 'development.stop').disabled).toBe(false)
  })
  it('releases busy state and refreshes the actual setting after a shutdown timeout', async () => {
    let failRequest!: (reason: Error) => void
    vi.mocked(setDevelopmentEnabled).mockImplementationOnce(() => new Promise((_, reject) => { failRequest = reject }))
    const root = mount()
    await settle()
    const toggle = root.querySelector<HTMLInputElement>('input[type="checkbox"]')!
    toggle.checked = false
    toggle.dispatchEvent(new Event('change'))
    await settle()
    expect(toggle.disabled).toBe(true)
    expect(button(root, 'common.refresh').disabled).toBe(true)
    failRequest(new Error('request timeout'))
    await settle()
    expect(root.textContent).toContain('request timeout')
    expect(toggle.disabled).toBe(false)
    expect(getDevelopment).toHaveBeenCalledTimes(2)
    expect(button(root, 'development.reload').disabled).toBe(false)
    expect(button(root, 'common.refresh').disabled).toBe(false)
    expect(setDevelopmentEnabled).toHaveBeenCalledTimes(1)
    // The backend may finish later; manual refresh reconciles without retrying.
    vi.mocked(getDevelopment).mockResolvedValueOnce({ enabled: false, registrations: [record] })
    button(root, 'common.refresh').click()
    await settle()
    expect(toggle.checked).toBe(false)
    expect(button(root, 'development.reload').disabled).toBe(true)
    expect(setDevelopmentEnabled).toHaveBeenCalledTimes(1)
  })
  it('uses the translated source-missing label instead of the raw backend status', async () => {
    usePluginStore().pluginsWithStatus[0]!.status = 'source_missing'
    const root = mount()
    await settle()
    expect(root.querySelector('article')?.textContent).toContain('status.sourceMissing')
    expect(root.querySelector('article')?.textContent).not.toContain('status.source_missing')
    const locales = import.meta.glob('../../i18n/locales/*.ts', { eager: true, import: 'default' }) as Record<string, { status: Record<string, string> }>
    expect(Object.keys(locales)).toHaveLength(8)
    for (const locale of Object.values(locales)) {
      expect(locale.status.sourceMissing).toBeTruthy()
      expect(locale.status.sourceMissing).not.toContain('status.')
    }
  })
  it('can stop a live process after its source becomes unavailable', async () => {
    usePluginStore().pluginsWithStatus[0]!.status = 'source_missing'
    const missing = { ...record, runtime_alive: true, error: 'directory unavailable' }
    vi.mocked(getDevelopment).mockResolvedValue({ enabled: true, registrations: [missing] })
    vi.mocked(runDevelopmentAction).mockResolvedValue({ success: true })
    const root = mount()
    await settle()
    expect(root.textContent).toContain('directory unavailable')
    expect(button(root, 'development.stop').disabled).toBe(false)
    button(root, 'development.stop').click()
    await settle()
    expect(runDevelopmentAction).toHaveBeenCalledWith(missing, 'stop')
  })
  it('refreshes runtime state and entry metadata using the toolbar', async () => {
    const store = usePluginStore()
    store.pluginsWithStatus[0]!.entries = [{ id: 'hello', name: 'hello', description: 'Old entry' }]
    const root = mount()
    await settle()
    vi.mocked(store.fetchPlugins).mockImplementationOnce(async () => {
      store.pluginsWithStatus[0]!.status = 'stopped'
      store.pluginsWithStatus[0]!.entries = [{ id: 'hello', name: 'hello', description: 'Updated entry' }]
    })
    vi.mocked(getDevelopment).mockResolvedValue({ enabled: true, registrations: [{ ...record, runtime_alive: false }] })
    vi.mocked(store.fetchPluginStatus).mockClear()
    button(root, 'common.refresh').click()
    await settle()
    expect(store.fetchPluginStatus).toHaveBeenCalledOnce()
    expect(root.textContent).toContain('Updated entry')
    expect(root.textContent).not.toContain('Old entry')
    expect(button(root, 'development.start').disabled).toBe(false)
  })
  it('updates visible entry descriptions from refreshed metadata after reload', async () => {
    const store = usePluginStore()
    store.pluginsWithStatus[0]!.entries = [{ id: 'hello', name: 'hello', description: 'Hello v1' }]
    vi.mocked(runDevelopmentAction).mockResolvedValue({ success: true })
    const root = mount()
    await settle()
    expect(root.querySelector('.development-entries')?.textContent).toContain('hello')
    expect(root.querySelector('.development-entries')?.textContent).toContain('Hello v1')
    vi.mocked(store.fetchPlugins).mockImplementationOnce(async () => {
      store.pluginsWithStatus[0]!.entries = [{ id: 'hello', name: 'hello', description: 'Hello v2' }]
    })
    button(root, 'development.reload').click()
    await settle()
    expect(store.fetchPlugins).toHaveBeenCalledWith(true)
    expect(root.querySelector('.development-entries')?.textContent).toContain('Hello v2')
    expect(root.querySelector('.development-entries')?.textContent).not.toContain('Hello v1')
    store.pluginsWithStatus[0]!.entries = []
    await settle()
    expect(root.querySelector('.development-entries')?.textContent).toContain('common.noData')
    expect(root.querySelector('.development-entries')?.textContent).not.toContain('Hello v2')
  })
  it('shows errors and keeps the association when stop/removal fails', async () => {
    vi.mocked(removeDevelopment).mockRejectedValue(new Error('stop failed'))
    const root = mount()
    await settle()
    button(root, 'development.remove').click()
    await settle()
    expect(removeDevelopment).toHaveBeenCalledWith(record)
    expect(root.textContent).toContain('stop failed')
    expect(root.querySelectorAll('article')).toHaveLength(1)
  })
  it('reloads with the visible registration revision and retains startup errors', async () => {
    vi.mocked(runDevelopmentAction).mockResolvedValue({ success: false, message: 'syntax error' })
    const root = mount()
    await settle()
    button(root, 'development.reload').click()
    await settle()
    expect(runDevelopmentAction).toHaveBeenCalledWith(record, 'reload')
    expect(root.textContent).toContain('syntax error')
  })
  it('invalidates the preview after editing a path and explains backend-local paths', async () => {
    const root = mount()
    await settle()
    button(root, 'development.load').click()
    await settle()
    const dialog = root.querySelector('[role="dialog"]')!
    const input = dialog.querySelector('input')!
    input.value = record.source_dir
    input.dispatchEvent(new Event('input'))
    await settle()
    button(dialog, 'development.validate').click()
    await settle()
    expect(button(dialog, 'development.load').disabled).toBe(false)
    expect(dialog.textContent).toContain('development.pathHint')
    input.value = 'D:/other'
    input.dispatchEvent(new Event('input'))
    await settle()
    expect(button(dialog, 'development.load').disabled).toBe(true)
    expect(registerDevelopment).toHaveBeenCalledTimes(1)
  })
  it('preserves missing source registrations while developer mode is disabled', async () => {
    vi.mocked(getDevelopment).mockResolvedValue({ enabled: false, registrations: [{ ...record, error: 'directory unavailable' }] })
    const root = mount()
    await settle()
    expect(root.textContent).toContain('directory unavailable')
    expect(button(root, 'development.reload').disabled).toBe(true)
    expect(button(root, 'development.rebind').disabled).toBe(false)
    expect(button(root, 'development.remove').disabled).toBe(false)
  })
})
