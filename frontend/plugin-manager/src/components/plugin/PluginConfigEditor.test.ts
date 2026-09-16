// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref } from 'vue'
import ElementPlus, { ElMessage, ElMessageBox, type Action, type MessageBoxData } from 'element-plus'
import * as configApi from '@/api/config'
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
  getPluginProfileConfig: vi.fn(),
  deletePluginProfileConfig: vi.fn(),
  hotUpdatePluginConfig: vi.fn(),
}))
const cleanups: (() => void)[] = []
afterEach(() => {
  cleanups.splice(0).forEach((cleanup) => cleanup())
  vi.restoreAllMocks()
  vi.clearAllMocks()
})
async function mountEditor() {
  const host = document.createElement('main')
  host.dataset.yuiGuideId = 'plugin-main'
  Object.defineProperty(host, 'clientHeight', { value: 1000 })
  document.body.append(host)
  const pluginId = ref('test')
  const app = createApp({ render: () => h(PluginConfigEditor, { pluginId: pluginId.value }) })
  app.use(ElementPlus)
  app.mount(host)
  let mounted = true
  const unmount = () => {
    if (mounted) app.unmount()
    mounted = false
    host.remove()
  }
  cleanups.push(unmount)
  await vi.waitFor(() => expect(host.querySelectorAll('.config-nav button')).toHaveLength(3))
  return { host, pluginId, unmount }
}
async function search(host: HTMLElement, value: string) {
  const input = host.querySelector<HTMLInputElement>('.config-toolbar input')!
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()
  await nextTick()
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

async function startProfileDeletion() {
  vi.spyOn(configApi, 'getPluginProfilesState').mockImplementation(async (pluginId) => ({
    plugin_id: pluginId,
    profiles_path: '',
    profiles_exists: true,
    config_profiles: {
      active: 'saved',
      files: { saved: { path: 'saved.toml', resolved_path: null, exists: true } },
    },
  }))
  vi.spyOn(configApi, 'getPluginProfileConfig').mockImplementation(async (pluginId, name) => ({
    plugin_id: pluginId,
    profile: { name, path: 'saved.toml', resolved_path: null, exists: true },
    config: {},
  }))
  const deletion = deferred<Awaited<ReturnType<typeof configApi.deletePluginProfileConfig>>>()
  const deleteSpy = vi
    .spyOn(configApi, 'deletePluginProfileConfig')
    .mockReturnValue(deletion.promise)
  const confirmation = deferred<Action>()
  const confirmSpy = vi.spyOn(ElMessageBox, 'confirm').mockReturnValue(
    confirmation.promise as Promise<MessageBoxData>
  )
  const success = vi.spyOn(ElMessage, 'success').mockReturnValue({ close: vi.fn() })
  const editor = await mountEditor()
  editor.host.querySelector<HTMLButtonElement>('.profile-entry')!.click()
  await vi.waitFor(() =>
    expect(document.querySelector('.profile-current-row button')).not.toBeNull()
  )
  document.querySelector<HTMLButtonElement>('.profile-current-row button')!.click()
  expect(confirmSpy).toHaveBeenCalledOnce()
  expect(deleteSpy).not.toHaveBeenCalled()
  confirmation.resolve('confirm')
  await vi.waitFor(() => expect(deleteSpy).toHaveBeenCalledWith('test', 'saved'))
  return { ...editor, deletion, success }
}

async function flushDeletion() {
  // Drain the API, composable, component, and rendering continuations.
  await new Promise((resolve) => setTimeout(resolve, 0))
  await nextTick()
}

describe('profile deletion lifecycle', () => {
  it('shows success for the current mounted plugin', async () => {
    const { deletion, success } = await startProfileDeletion()
    deletion.resolve({ plugin_id: 'test', profile: 'saved', removed: true })
    await vi.waitFor(() => expect(success).toHaveBeenCalledExactlyOnceWith('common.success'))
  })

  it('does not show success after unmount while deletion is pending', async () => {
    const { deletion, success, unmount } = await startProfileDeletion()
    unmount()
    deletion.resolve({ plugin_id: 'test', profile: 'saved', removed: true })
    await flushDeletion()
    expect(success).not.toHaveBeenCalled()
  })

  it('does not show success after switching plugins while deletion is pending', async () => {
    const { deletion, success, pluginId, host } = await startProfileDeletion()
    pluginId.value = 'next'
    await nextTick()
    await vi.waitFor(() => expect(host.querySelectorAll('.config-nav button')).toHaveLength(3))
    deletion.resolve({ plugin_id: 'test', profile: 'saved', removed: true })
    await flushDeletion()
    expect(success).not.toHaveBeenCalled()
  })

  it('does not display a stale deletion error on the next plugin', async () => {
    const { deletion, success, pluginId, host } = await startProfileDeletion()
    pluginId.value = 'next'
    await nextTick()
    await vi.waitFor(() => expect(host.querySelectorAll('.config-nav button')).toHaveLength(3))
    deletion.reject(new Error('Previous plugin deletion failed'))
    await flushDeletion()
    expect(host.querySelector('.config-error')).toBeNull()
    expect(host.textContent).not.toContain('Previous plugin deletion failed')
    expect(success).not.toHaveBeenCalled()
  })

  it('still displays a deletion error for the current plugin', async () => {
    const { deletion, host } = await startProfileDeletion()
    deletion.reject(new Error('Current plugin deletion failed'))
    await vi.waitFor(() =>
      expect(host.querySelector('.config-error')?.textContent).toContain(
        'Current plugin deletion failed'
      )
    )
  })
})

describe('configuration navigation boundaries', () => {
  it('matches field order, hides protected metadata, and keeps runtime last', async () => {
    const { host } = await mountEditor()
    expect([...host.querySelectorAll('.config-nav button span')].map((e) => e.textContent)).toEqual(
      ['search', 'cache', 'plugin_runtime']
    )
  })

  it('disables nonmatching sections and handles an empty search result', async () => {
    const { host } = await mountEditor()
    await search(host, 'max_results')
    const buttons = [...host.querySelectorAll<HTMLButtonElement>('.config-nav button')]
    expect(buttons.map((button) => button.disabled)).toEqual([false, true, true])
    await search(host, 'no-such-field')
    expect(buttons.every((button) => button.disabled)).toBe(true)
    expect(host.textContent).toContain('plugins.configUi.emptySearch')
  })

  it('jumps within the editor without clearing search or changing an unsaved draft', async () => {
    const { host } = await mountEditor()
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
