// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, ref } from 'vue'
import PluginImportDialog from './PluginImportDialog.vue'
import { uploadPluginPackage, planPluginInstall, installPluginPackage, discardUploadedPluginPackage } from '@/api/pluginCli'

const notify = vi.hoisted(() => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() }))
const confirm = vi.hoisted(() => vi.fn())
vi.mock('element-plus', () => ({ ElMessage: notify, ElMessageBox: { confirm } }))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string, params?: unknown) => key + (params ? JSON.stringify(params) : '') }) }))
vi.mock('@/utils/request', () => ({ formatHttpError: (error: unknown) => String(error) }))
vi.mock('@/api/pluginCli', () => ({ uploadPluginPackage: vi.fn(), planPluginInstall: vi.fn(), installPluginPackage: vi.fn(), discardUploadedPluginPackage: vi.fn() }))

let cleanup = () => {}
async function flush() { for (let i = 0; i < 20; i++) { await Promise.resolve(); await nextTick() } }
function mount() {
  const visible = ref(true)
  const busy = vi.fn()
  const installed = vi.fn()
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp(defineComponent({ setup: () => () => h(PluginImportDialog, {
    visible: visible.value, 'onUpdate:visible': (value: boolean) => { visible.value = value },
    onBusyChange: busy, onInstalled: installed,
  }) }))
  app.component('ElDialog', defineComponent({
    props: ['modelValue', 'showClose', 'closeOnClickModal', 'closeOnPressEscape'], emits: ['update:modelValue', 'closed'],
    setup: (props, { slots, emit }) => () => props.modelValue ? h('div', { role: 'dialog' }, [
      h('button', { 'data-testid': 'request-close', onClick: () => emit('update:modelValue', false) }),
      slots.default?.(), slots.footer?.(),
    ]) : null,
  }))
  app.component('ElButton', defineComponent({
    props: { disabled: Boolean, loading: Boolean },
    setup: (props, { slots, attrs }) => () => h('button', { ...attrs, disabled: props.disabled || props.loading }, slots.default?.()),
  }))
  app.component('ElIcon', defineComponent({ setup: (_, { slots }) => () => h('span', slots.default?.()) }))
  app.mount(root)
  cleanup = () => { app.unmount(); root.remove() }
  const select = async (file: File) => {
    const input = root.querySelector('input')!
    Object.defineProperty(input, 'files', { value: [file], configurable: true })
    input.dispatchEvent(new Event('change', { bubbles: true }))
    await flush()
  }
  const submit = () => [...root.querySelectorAll('button')].find(button => button.textContent === 'plugins.import' || button.textContent === 'plugins.importing')!
  return { root, visible, busy, installed, select, submit }
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(uploadPluginPackage).mockResolvedValue({ path: '/uploads/demo.neko-plugin' } as never)
  vi.mocked(planPluginInstall).mockResolvedValue({ action: 'install' } as never)
  vi.mocked(installPluginPackage).mockResolvedValue({ installed_plugin_count: 1, rollback_status: 'not_needed' } as never)
  vi.mocked(discardUploadedPluginPackage).mockResolvedValue({ success: true } as never)
  vi.spyOn(console, 'error').mockImplementation(() => {})
})
afterEach(() => { cleanup(); cleanup = () => {}; vi.restoreAllMocks() })

describe('package import through the upload and existing installer interfaces', () => {
  it('validates file suffix and size before uploading and accepts case-insensitive package names', async () => {
    const ui = mount()
    await ui.select(new File(['x'], 'demo.zip'))
    expect(notify.error).toHaveBeenLastCalledWith('plugins.importUnsupportedFile')
    expect(ui.submit().disabled).toBe(true)
    const huge = new File(['x'], 'large.neko-bundle')
    Object.defineProperty(huge, 'size', { value: 500 * 1024 * 1024 + 1 })
    await ui.select(huge)
    expect(notify.error).toHaveBeenLastCalledWith(expect.stringContaining('plugins.importFileTooLarge'))
    expect(uploadPluginPackage).not.toHaveBeenCalled()
    await ui.select(new File(['x'], 'Demo.NEKO-PLUGIN'))
    expect(ui.root.textContent).toContain('Demo.NEKO-PLUGIN')
    expect(ui.submit().disabled).toBe(false)
  })
  it('does not replace the selected file, duplicate upload, or close while busy', async () => {
    let finish!: (value: never) => void
    vi.mocked(uploadPluginPackage).mockReturnValue(new Promise(resolve => { finish = resolve }))
    const ui = mount()
    const file = new File(['x'], 'demo.neko-plugin')
    await ui.select(file)
    ui.submit().click()
    ui.submit().dispatchEvent(new Event('click', { bubbles: true }))
    await flush()
    ;(ui.root.querySelector('[data-testid="request-close"]') as HTMLButtonElement).click()
    await ui.select(new File(['other'], 'other.neko-plugin'))
    expect(ui.visible.value).toBe(true)
    expect(ui.root.textContent).toContain('demo.neko-plugin')
    expect(uploadPluginPackage).toHaveBeenCalledTimes(1)
    finish({ path: '/uploads/demo.neko-plugin' } as never)
    await flush()
    expect(installPluginPackage).toHaveBeenCalledWith(expect.objectContaining({ package: '/uploads/demo.neko-plugin', install_source: 'imported' }))
    expect(ui.installed).toHaveBeenCalledTimes(1)
    expect(ui.busy.mock.calls).toEqual([[true], [false]])
    expect(ui.visible.value).toBe(false)
  })
  it('keeps the chosen file and shows only the installer-owned error after a failed install', async () => {
    vi.mocked(installPluginPackage).mockRejectedValue(new Error('offline'))
    const ui = mount()
    await ui.select(new File(['x'], 'demo.neko-plugin'))
    ui.submit().click()
    await flush()
    expect(ui.visible.value).toBe(true)
    expect(ui.root.textContent).toContain('demo.neko-plugin')
    expect(notify.error).toHaveBeenCalledTimes(1)
    expect(notify.success).not.toHaveBeenCalled()
    expect(ui.installed).not.toHaveBeenCalled()
    expect(ui.submit().disabled).toBe(false)
  })
  it('keeps the file for retry when upload fails', async () => {
    vi.mocked(uploadPluginPackage).mockRejectedValue(new Error('offline'))
    const ui = mount()
    await ui.select(new File(['x'], 'demo.neko-plugin'))
    ui.submit().click()
    await flush()
    expect(ui.root.textContent).toContain('demo.neko-plugin')
    expect(notify.error).toHaveBeenCalledTimes(1)
    expect(planPluginInstall).not.toHaveBeenCalled()
    expect(ui.visible.value).toBe(true)
  })
  it('preserves the install warning flow instead of also reporting success', async () => {
    vi.mocked(installPluginPackage).mockResolvedValue({ installed_plugin_count: 1, install_source_warning: 'bookkeeping warning' } as never)
    const ui = mount()
    await ui.select(new File(['x'], 'demo.neko-bundle'))
    ui.submit().click()
    await flush()
    expect(notify.warning).toHaveBeenCalledTimes(1)
    expect(notify.success).not.toHaveBeenCalled()
    expect(ui.installed).toHaveBeenCalledTimes(1)
    expect(ui.visible.value).toBe(false)
  })
  it('keeps the file when a returned result reports that installation rolled back', async () => {
    vi.mocked(installPluginPackage).mockResolvedValue({ installed_plugin_count: 0, rollback_status: 'completed' } as never)
    const ui = mount()
    await ui.select(new File(['x'], 'demo.neko-plugin'))
    ui.submit().click()
    await flush()
    expect(notify.error).toHaveBeenCalledExactlyOnceWith('package.install.rollbackCompleted')
    expect(ui.visible.value).toBe(true)
    expect(ui.root.textContent).toContain('demo.neko-plugin')
    expect(ui.installed).not.toHaveBeenCalled()
  })
  it('leaves a cancelled upgrade retryable without duplicating the installer notification', async () => {
    vi.mocked(planPluginInstall).mockResolvedValue({ action: 'upgrade', confirmation_token: 'token' } as never)
    confirm.mockRejectedValue('cancel')
    const ui = mount()
    await ui.select(new File(['x'], 'demo.neko-plugin'))
    ui.submit().click()
    await flush()
    expect(notify.info).toHaveBeenCalledExactlyOnceWith('package.install.upgradeCancelled')
    expect(notify.error).not.toHaveBeenCalled()
    expect(ui.visible.value).toBe(true)
    expect(ui.root.textContent).toContain('demo.neko-plugin')
    expect(installPluginPackage).not.toHaveBeenCalled()
    expect(discardUploadedPluginPackage).toHaveBeenCalledWith('/uploads/demo.neko-plugin')
  })
})
