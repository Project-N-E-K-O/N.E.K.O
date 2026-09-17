// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref } from 'vue'
import ElementPlus, {
  ElMessage,
  ElMessageBox,
  type Action,
  type MessageBoxData,
} from 'element-plus'
import * as configApi from '@/api/config'
import { setPendingReload } from '@/utils/pendingReload'
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
  upsertPluginProfileConfig: vi.fn(),
  hotUpdatePluginConfig: vi.fn(),
}))
const cleanups: (() => void)[] = []
afterEach(() => {
  cleanups.splice(0).forEach((cleanup) => cleanup())
  vi.restoreAllMocks()
  vi.clearAllMocks()
  localStorage.clear()
})
async function mountEditor({ expectNav = true }: { expectNav?: boolean } = {}) {
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
  // Plugins without configurable sections render no navigation at all.
  await vi.waitFor(() =>
    expect(host.querySelectorAll(expectNav ? '.config-nav button' : '.config-footer')).toHaveLength(
      expectNav ? 3 : 1
    )
  )
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
  const confirmSpy = vi
    .spyOn(ElMessageBox, 'confirm')
    .mockReturnValue(confirmation.promise as Promise<MessageBoxData>)
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

  it('clears the base-config reload path when the deleted profile was active', async () => {
    // After deleting the active profile the server clears `active` while another
    // saved profile remains, so the editor must still offer a working reload.
    let state: configApi.PluginProfilesState = {
      plugin_id: 'test',
      profiles_path: 'profiles',
      profiles_exists: true,
      config_profiles: {
        active: 'saved',
        files: {
          saved: { path: 'saved.toml', resolved_path: null, exists: true },
          other: { path: 'other.toml', resolved_path: null, exists: true },
        },
      },
    }
    vi.spyOn(configApi, 'getPluginProfilesState').mockImplementation(async () => state)
    vi.spyOn(configApi, 'getPluginProfileConfig').mockResolvedValue({
      plugin_id: 'test',
      profile: { name: 'saved', path: 'saved.toml', resolved_path: null, exists: true },
      config: {},
    })
    vi.spyOn(configApi, 'deletePluginProfileConfig').mockImplementation(async () => {
      state = {
        ...state,
        config_profiles: {
          active: null,
          files: { other: { path: 'other.toml', resolved_path: null, exists: true } },
        },
      }
      return { plugin_id: 'test', profile: 'saved', removed: true }
    })
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as unknown as MessageBoxData)
    const { host, unmount } = await mountEditor()
    host.querySelector<HTMLButtonElement>('.profile-entry')!.click()
    await vi.waitFor(() =>
      expect(document.querySelector('.profile-current-row button')).not.toBeNull()
    )
    document.querySelector<HTMLButtonElement>('.profile-current-row button')!.click()
    await vi.waitFor(() =>
      expect(host.querySelector('.apply-status')?.textContent).toContain(
        'plugins.configUi.pendingApply'
      )
    )
    const reload = [...host.querySelectorAll<HTMLButtonElement>('.config-footer button')].find(
      (button) => button.textContent?.trim() === 'plugins.reloadPlugin'
    )
    expect(reload).toBeDefined()
    expect(reload?.disabled).toBe(false)
    unmount()
  })

  it('keeps a reload hint after deleting the active profile, including after remount', async () => {
    const { deletion, host, unmount } = await startProfileDeletion()
    deletion.resolve({ plugin_id: 'test', profile: 'saved', removed: true })
    await vi.waitFor(() =>
      expect(host.querySelector('.apply-status')?.textContent).toContain(
        'plugins.configUi.pendingApply'
      )
    )
    // The hint is derived from persisted state, so it survives a remount.
    unmount()
    const { host: remounted } = await mountEditor()
    expect(remounted.querySelector('.apply-status')?.textContent).toContain(
      'plugins.configUi.pendingApply'
    )
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

describe('saved profile hot updates', () => {
  async function savedEditor() {
    vi.spyOn(configApi, 'upsertPluginProfileConfig').mockResolvedValue({
      plugin_id: 'test',
      profile: { name: 'default', path: 'default.toml', resolved_path: null, exists: true },
      config: { search: { max_results: 9 } },
    })
    vi.spyOn(configApi, 'getPluginProfilesState').mockResolvedValue({
      plugin_id: 'test',
      profiles_path: 'profiles',
      profiles_exists: true,
      config_profiles: {
        active: 'default',
        files: { default: { path: 'default.toml', resolved_path: null, exists: true } },
      },
    })
    const editor = await mountEditor()
    const input = editor.host.querySelector<HTMLInputElement>(
      'input[aria-label="search.max_results"]'
    )!
    input.value = '9'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    const save = [...editor.host.querySelectorAll<HTMLButtonElement>('.config-footer button')].find(
      (button) => button.textContent?.trim() === 'plugins.configUi.saveProfile'
    )!
    save.click()
    await vi.waitFor(() => expect(hotButton(editor.host)?.disabled).toBe(false))
    return editor
  }

  function hotButton(host: HTMLElement) {
    return [...host.querySelectorAll<HTMLButtonElement>('.config-footer button')].find(
      (button) => button.textContent?.trim() === 'plugins.hotUpdate'
    )
  }

  it('sends fresh resolved config temporarily, excluding protected metadata', async () => {
    const { host } = await savedEditor()
    const resolved = {
      plugin: { id: 'test', entry: 'main' },
      search: { max_results: 9, inherited: true },
      cache: {},
    }
    const read = vi
      .spyOn(configApi, 'getPluginConfig')
      .mockResolvedValue({ plugin_id: 'test', config: resolved, last_modified: '' })
    const hot = vi.spyOn(configApi, 'hotUpdatePluginConfig').mockResolvedValue({
      success: true,
      plugin_id: 'test',
      mode: 'temporary',
      hot_reloaded: true,
      requires_reload: false,
      message: 'Config update sent (response timeout, may have been applied)',
    })
    hotButton(host)!.click()
    await vi.waitFor(() =>
      expect(hot).toHaveBeenCalledExactlyOnceWith(
        'test',
        {
          search: { max_results: 9, inherited: true },
          cache: {},
        },
        'temporary',
        'default'
      )
    )
    expect(read).toHaveBeenCalledWith('test')
    expect(resolved.plugin.id).toBe('test')
    await vi.waitFor(() =>
      expect(host.querySelector('.apply-status')?.textContent).toContain(
        'plugins.configUi.hotRequested'
      )
    )
    // Hot updates are merged into the live config, so the pending state and the
    // reload affordance stay available.
    expect(hotButton(host)).toBeDefined()
    expect(hotButton(host)?.disabled).toBe(false)
    expect(configApi.upsertPluginProfileConfig).toHaveBeenCalledTimes(1)
  })

  it('keeps the pending state after leaving and returning to the page', async () => {
    const { unmount } = await savedEditor()
    unmount()
    const { host } = await mountEditor()
    expect(hotButton(host)).toBeDefined()
    expect(hotButton(host)?.disabled).toBe(false)
  })

  it.each(['unmount', 'switch'] as const)('does not apply a response after %s', async (action) => {
    const { host, pluginId, unmount } = await savedEditor()
    const response = deferred<Awaited<ReturnType<typeof configApi.getPluginConfig>>>()
    const read = vi.spyOn(configApi, 'getPluginConfig').mockReturnValueOnce(response.promise)
    const hot = vi.spyOn(configApi, 'hotUpdatePluginConfig')
    hotButton(host)!.click()
    await vi.waitFor(() => expect(read).toHaveBeenCalled())
    if (action === 'unmount') unmount()
    else {
      pluginId.value = 'next'
      await nextTick()
    }
    response.resolve({
      plugin_id: 'test',
      config: { search: { max_results: 9 } },
      last_modified: '',
    })
    await flushDeletion()
    expect(hot).not.toHaveBeenCalled()
  })

  it('does not apply when reading the saved config fails', async () => {
    const { host } = await savedEditor()
    vi.spyOn(configApi, 'getPluginConfig').mockRejectedValue(new Error('Cannot read saved config'))
    const hot = vi.spyOn(configApi, 'hotUpdatePluginConfig')
    hotButton(host)!.click()
    await vi.waitFor(() =>
      expect(host.querySelector('.config-error')?.textContent).toContain('Cannot read saved config')
    )
    expect(hot).not.toHaveBeenCalled()
    expect(hotButton(host)?.disabled).toBe(false)
  })
})

describe('virtual default materialization', () => {
  it('persists the current draft instead of writing an empty profile', async () => {
    const upsert = vi.spyOn(configApi, 'upsertPluginProfileConfig').mockResolvedValue({
      plugin_id: 'test',
      profile: { name: 'default', path: 'default.toml', resolved_path: null, exists: true },
      config: { search: { max_results: 9 } },
    })
    vi.spyOn(configApi, 'getPluginProfileConfig').mockResolvedValue({
      plugin_id: 'test',
      profile: { name: 'default', path: 'default.toml', resolved_path: null, exists: true },
      config: { search: { max_results: 9 } },
    })
    vi.spyOn(ElMessageBox, 'prompt').mockResolvedValue({
      value: 'default',
      action: 'confirm',
    } as MessageBoxData)
    const { host, unmount } = await mountEditor()
    const input = host.querySelector<HTMLInputElement>('input[aria-label="search.max_results"]')!
    input.value = '9'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    host.querySelector<HTMLButtonElement>('button.profile-entry')!.click()
    await nextTick()
    await vi.waitFor(() =>
      expect(document.querySelector('.profile-picker-row button')).not.toBeNull()
    )
    const addBtn = [
      ...document.querySelectorAll<HTMLButtonElement>('.profile-picker-row button'),
    ].find((button) => button.querySelector('svg'))!
    addBtn.click()
    await vi.waitFor(() => expect(upsert).toHaveBeenCalled())
    // The draft must be persisted, not replaced by an empty profile object.
    expect(upsert).toHaveBeenCalledWith('test', 'default', { search: { max_results: 9 } }, true)
    unmount()
  })
})

describe('pending state synchronisation', () => {
  it('clears the warning when the plugin is reloaded elsewhere', async () => {
    setPendingReload('test', true)
    const { host, unmount } = await mountEditor()
    expect(host.querySelector('.apply-status')?.textContent).toContain(
      'plugins.configUi.pendingApply'
    )

    // What pluginStore.reload() does for any entry point outside the editor.
    setPendingReload('test', false)
    await nextTick()

    expect(host.querySelector('.apply-status')).toBeNull()
    unmount()
  })

  it('keeps the form usable when only protected metadata is configured', async () => {
    vi.spyOn(configApi, 'getPluginEffectiveBaseConfig').mockResolvedValue({
      plugin_id: 'test',
      config: { plugin: { id: 'test', entry: 'main' } },
    } as never)
    const { host, unmount } = await mountEditor({ expectNav: false })
    expect(host.querySelector('.el-empty')).toBeNull()
    expect(host.querySelectorAll('.config-nav button')).toHaveLength(0)
    unmount()
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

describe('async operation lifecycle isolation', () => {
  it('does not show stale errors after switching plugins', async () => {
    vi.spyOn(configApi, 'getPluginProfilesState').mockResolvedValue({
      plugin_id: 'test',
      profiles_path: 'profiles',
      profiles_exists: true,
      config_profiles: {
        active: 'default',
        files: { default: { path: 'default.toml', resolved_path: null, exists: true } },
      },
    })
    vi.spyOn(configApi, 'upsertPluginProfileConfig').mockRejectedValue(new Error('Create failed'))
    vi.spyOn(ElMessageBox, 'prompt').mockResolvedValue({
      value: 'prod',
      action: 'confirm',
    } as MessageBoxData)
    const { host, pluginId, unmount } = await mountEditor()
    // 打开配置文件管理器
    const profileBtn = [...host.querySelectorAll<HTMLButtonElement>('button')].find((btn) =>
      btn.classList.contains('profile-entry')
    )!
    profileBtn.click()
    await nextTick()
    await vi.waitFor(() => expect(document.querySelector('.profile-manager-dialog')).not.toBeNull())
    // 点击新建配置文件按钮
    const addBtn = [
      ...document.querySelectorAll<HTMLButtonElement>('.profile-picker-row button'),
    ].find(
      (btn) => btn.querySelector('svg') // Plus icon
    )!
    addBtn.click()
    await vi.waitFor(() => expect(configApi.upsertPluginProfileConfig).toHaveBeenCalled())
    pluginId.value = 'other'
    await nextTick()
    await nextTick()
    expect(host.querySelector('.config-error')).toBeNull()
    unmount()
  })

  it('does not show stale activation errors after plugin change', async () => {
    vi.useFakeTimers()
    vi.spyOn(configApi, 'getPluginProfilesState').mockResolvedValue({
      plugin_id: 'test',
      profiles_path: 'profiles',
      profiles_exists: true,
      config_profiles: {
        active: 'prod',
        files: { prod: { path: 'prod.toml', resolved_path: null, exists: true } },
      },
    })
    vi.spyOn(configApi, 'getPluginProfileConfig').mockResolvedValue({
      plugin_id: 'test',
      profile: { name: 'prod', path: 'prod.toml', resolved_path: null, exists: true },
      config: { cache: { ttl: 60 } },
    })

    const { host, pluginId, unmount } = await mountEditor()
    await vi.waitFor(() => expect(configApi.getPluginProfilesState).toHaveBeenCalled())
    await nextTick()

    // 模拟保存操作失败（延迟 100ms）
    const saveSpy = vi.spyOn(configApi, 'upsertPluginProfileConfig').mockImplementation(
      () =>
        new Promise((_, reject) => {
          setTimeout(() => reject(new Error('Network error')), 100)
        })
    )

    // 修改配置以触发草稿状态
    const input = host.querySelector<HTMLInputElement>('input[type="number"]')
    expect(input).toBeDefined()
    input!.value = '120'
    input!.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()

    // 点击保存按钮
    await vi.waitFor(() => {
      const saveBtn = [...host.querySelectorAll<HTMLButtonElement>('button')].find(
        (btn) => btn.textContent?.includes('save') || btn.classList.contains('save-button')
      )
      expect(saveBtn).toBeDefined()
      return saveBtn !== undefined
    })
    const saveBtn = [...host.querySelectorAll<HTMLButtonElement>('button')].find(
      (btn) => btn.textContent?.includes('save') || btn.classList.contains('save-button')
    )!
    saveBtn.click()
    await nextTick()

    // 等待 50ms，然后切换插件
    await vi.advanceTimersByTimeAsync(50)
    pluginId.value = 'another-plugin'
    await nextTick()

    // 等待保存操作完成（失败）
    await vi.advanceTimersByTimeAsync(100)
    await nextTick()

    expect(saveSpy).toHaveBeenCalledOnce()
    // 不应该显示错误（因为插件 ID 已经改变）
    expect(host.querySelector('.config-error')).toBeNull()
    vi.useRealTimers()
    unmount()
  })
})
