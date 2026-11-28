// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, markRaw, nextTick } from 'vue'
import type { Component } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import words from '@/i18n/power/en-US'
import type { PermissionsView, RuntimeView } from '@/api/console'
import { describeConsoleError, formatBytes, joinIdle, splitIdle } from './powerFormat'
import BuiltinTab from './BuiltinTab.vue'
import PermissionsTab from './PermissionsTab.vue'
import PlacesTab from './PlacesTab.vue'
import RuntimeTab from './RuntimeTab.vue'
import WindowTab from './WindowTab.vue'

const api = vi.hoisted(() => ({
  getRuntime: vi.fn(), setRuntimePreference: vi.fn(), installDsh: vi.fn(), uninstallDsh: vi.fn(),
  getPermissions: vi.fn(), setPermission: vi.fn(), getWindow: vi.fn(), setWindow: vi.fn(),
  getPlaces: vi.fn(), openPlace: vi.fn(),
  getStorage: vi.fn(), setStorageSettings: vi.fn(), cleanWorkDirs: vi.fn(), cleanWorktrees: vi.fn(),
  cleanTranscripts: vi.fn(), openStorageItem: vi.fn(), landWorktree: vi.fn(),
  getSwitches: vi.fn(), setDeviceSwitch: vi.fn(), setPower: vi.fn(), getBrowser: vi.fn(), setBrowser: vi.fn(),
  consoleErrorCode: (error: unknown) => (error as { code?: string })?.code ?? null,
}))
vi.mock('@/api/console', () => api)

const refusal = (code: string) => Object.assign(new Error(code), { code, response: { status: 409 } })

function runtime(dsh: Partial<RuntimeView['dsh']> = {}): RuntimeView {
  return {
    dsh: {
      version: '0.2.1rc2', supported: true, installed: false, downloading: false, download_bytes: 83860784,
      downloaded_bytes: null, last_error: null, last_error_code: null, preference: 'auto', sessions: 0, ...dsh,
    },
    plugins: [],
  }
}
function permissions(version = 3, mode: PermissionsView['categories'][number]['mode'] = 'auto'): PermissionsView {
  return {
    version, unreadable: false, modes: ['auto', 'first_ask', 'always_ask', 'allow', 'deny'],
    categories: [{ category: 'run_command', mode, auto: 'first_ask' }, { category: 'other', mode: 'auto', auto: 'allow' }],
  }
}
const modelWindow = {
  model: 'agent-model', id: 'a1b2c3d4e5f60718', window: 200000, default: 200000, set: null, compact_at: 170000, compact_at_set: null,
  idle_compact_after: 1440, idle_compact_after_set: null, idle_compact_on: true,
}

let cleanup: (() => void) | undefined
const NativeMutationObserver = globalThis.MutationObserver
async function flush() { for (let i = 0; i < 6; i++) { await Promise.resolve(); await nextTick() } }
async function mount(component: Component) {
  const container = document.createElement('div')
  document.body.append(container)
  const app = createApp(component)
  app.use(ElementPlus)
  app.use(createI18n({
    legacy: false, locale: 'en-US',
    messages: { 'en-US': { power: words, common: { cancel: 'Cancel', refresh: 'Refresh' } } },
  }))
  app.mount(container)
  cleanup = () => { app.unmount(); container.remove() }
  await flush()
  return container
}
const byTest = (id: string) => document.querySelector(`[data-testid="${id}"]`) as HTMLElement | null
async function click(element: Element | null | undefined) {
  expect(element).toBeTruthy()
  ;(element as HTMLElement).click()
  await flush()
}
async function type(testId: string, value: string) {
  const input = document.querySelector(`[data-testid="${testId}"] input`) as HTMLInputElement
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
  input.dispatchEvent(new Event('change', { bubbles: true }))
  await flush()
}

beforeEach(() => {
  // Element Plus stores observers in reactive state; happy-dom's private fields cannot be proxied.
  vi.stubGlobal('MutationObserver', class extends NativeMutationObserver {
    constructor(callback: MutationCallback) { super(callback); markRaw(this) }
  })
  vi.clearAllMocks()
})
afterEach(() => { cleanup?.(); cleanup = undefined; document.body.innerHTML = ''; vi.useRealTimers(); vi.unstubAllGlobals() })

describe('what the pages share', () => {
  it('writes sizes as a person reads them', () => {
    expect([formatBytes(0), formatBytes(2048), formatBytes(83860784), formatBytes(1395864371), formatBytes(null)])
      .toEqual(['0 B', '2.0 KB', '80.0 MB', '1.3 GB', ''])
  })
  it('splits an idle time into the largest unit that divides it, and back', () => {
    expect(splitIdle(1440)).toEqual({ amount: 24, unit: 'minutes' })
    expect(splitIdle(7200)).toEqual({ amount: 2, unit: 'hours' })
    expect(splitIdle(30 * 86400)).toEqual({ amount: 30, unit: 'days' })
    expect(splitIdle(5400)).toEqual({ amount: 90, unit: 'minutes' })
    expect([joinIdle(24, 'minutes'), joinIdle(2, 'hours'), joinIdle(1, 'days')]).toEqual([1440, 7200, 86400])
  })
  it('words a refusal by its code, and an unknown one or no answer in general words', () => {
    const t = (key: string) => key
    const te = (key: string) => key === 'power.errors.stale'
    const failed = (code: string | undefined, status?: number) => ({ response: status ? { status, data: { detail: { code } } } : undefined })
    api.consoleErrorCode = (error: unknown) =>
      (error as { response?: { data?: { detail?: { code?: string } } } })?.response?.data?.detail?.code ?? null
    expect(describeConsoleError(failed('stale', 409), t, te)).toBe('power.errors.stale')
    expect(describeConsoleError(failed('something_new', 400), t, te)).toBe('power.errors.generic')
    expect(describeConsoleError(failed(undefined, 502), t, te)).toBe('power.errors.agent_unreachable')
    expect(describeConsoleError(failed(undefined), t, te)).toBe('power.errors.agent_unreachable')
    api.consoleErrorCode = (error: unknown) => (error as { code?: string })?.code ?? null
  })
})

describe('the runtime tab', () => {
  it('starts the download and keeps asking how far it is until it is over', async () => {
    vi.useFakeTimers()
    api.getRuntime.mockResolvedValueOnce(runtime())
    api.installDsh.mockResolvedValue(runtime({ downloading: true, downloaded_bytes: 0 }))
    await mount(RuntimeTab)
    expect(byTest('dsh-state')?.textContent).toContain(words.runtime.dsh.notInstalled)

    api.getRuntime.mockResolvedValueOnce(runtime({ downloading: true, downloaded_bytes: 41930392 }))
    api.getRuntime.mockResolvedValueOnce(runtime({ installed: true, path: 'D:/data/dsh' }))
    await click(byTest('dsh-install'))
    expect(api.installDsh).toHaveBeenCalledTimes(1)
    expect(byTest('dsh-progress')).toBeTruthy()

    await vi.advanceTimersByTimeAsync(1000)
    await flush()
    expect(byTest('dsh-progress')?.textContent).toContain('40.0 MB / 80.0 MB')
    await vi.advanceTimersByTimeAsync(1000)
    await flush()
    expect(byTest('dsh-state')?.textContent).toContain(words.runtime.dsh.installed)
    expect(byTest('dsh-progress')).toBeNull()
    // Installed: nothing asks any more.
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.getRuntime).toHaveBeenCalledTimes(3)
  })

  it('says in the user\'s words why a download failed, with what was reported below', async () => {
    api.getRuntime.mockResolvedValue(runtime({ last_error: 'dsh could not be downloaded (pypi: ConnectTimeout)', last_error_code: 'dsh_download' }))
    await mount(RuntimeTab)
    const shown = byTest('dsh-error')?.textContent ?? ''
    expect(shown).toContain(words.errors.dsh_download)
    expect(shown).toContain('ConnectTimeout')
    expect(byTest('dsh-install')?.textContent).toContain(words.runtime.dsh.retry)
  })

  it('goes on asking about a download after a change of the runtime was not taken, and shows a change that was', async () => {
    vi.useFakeTimers()
    api.getRuntime.mockResolvedValueOnce(runtime({ downloading: true, downloaded_bytes: 1000 }))
    await mount(RuntimeTab)
    const builtin = () => document.querySelector('[data-testid="runtime-preference"] input[value="builtin"]') as HTMLInputElement

    // Not taken (the connection dropped): the download is still asked about.
    api.setRuntimePreference.mockRejectedValueOnce(refusal('agent_unreachable'))
    api.getRuntime.mockResolvedValueOnce(runtime({ downloading: true, downloaded_bytes: 41930392 }))
    await click(builtin())
    expect(document.body.textContent).toContain(words.errors.agent_unreachable)
    await vi.advanceTimersByTimeAsync(1000)
    await flush()
    expect(byTest('dsh-progress')?.textContent).toContain('40.0 MB / 80.0 MB')
    // Why it was not taken is still said after the download was asked about again.
    expect(document.body.textContent).toContain(words.errors.agent_unreachable)

    // Taken: what it answers is shown, and what was asked for before it came back is not shown over it.
    let late!: (value: unknown) => void
    api.getRuntime.mockReturnValueOnce(new Promise(resolve => { late = resolve }))
    await vi.advanceTimersByTimeAsync(1000)
    api.setRuntimePreference.mockResolvedValueOnce(runtime({ downloading: false, installed: true, preference: 'builtin' }))
    await click(builtin())
    late(runtime({ downloading: true, downloaded_bytes: 41930392, preference: 'auto' }))
    await flush()
    expect(builtin().checked).toBe(true)
    expect(byTest('dsh-state')?.textContent).toContain(words.runtime.dsh.installed)
    // Nothing asks any more: the answer that came late did not start the asking again.
    const asked = api.getRuntime.mock.calls.length
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.getRuntime).toHaveBeenCalledTimes(asked)
  })

  it('says nothing of a reading that failed after a change was taken', async () => {
    vi.useFakeTimers()
    api.getRuntime.mockResolvedValueOnce(runtime({ downloading: true, downloaded_bytes: 1000 }))
    await mount(RuntimeTab)
    const builtin = () => document.querySelector('[data-testid="runtime-preference"] input[value="builtin"]') as HTMLInputElement
    let fail!: (reason: unknown) => void
    api.getRuntime.mockReturnValueOnce(new Promise((_resolve, reject) => { fail = reject }))
    await vi.advanceTimersByTimeAsync(1000)
    api.setRuntimePreference.mockResolvedValueOnce(runtime({ downloading: false, installed: true, preference: 'builtin' }))
    await click(builtin())
    fail(refusal('agent_unreachable'))
    await flush()
    expect(document.body.textContent).not.toContain(words.errors.agent_unreachable)
    const asked = api.getRuntime.mock.calls.length
    await vi.advanceTimersByTimeAsync(10000)
    expect(api.getRuntime).toHaveBeenCalledTimes(asked)
  })

  it('does not offer to remove dsh while a task runs in it, and passes on what else goes', async () => {
    api.getRuntime.mockResolvedValue(runtime({ installed: true, sessions: 1 }))
    await mount(RuntimeTab)
    expect((byTest('dsh-uninstall') as HTMLButtonElement).disabled).toBe(true)
    cleanup?.()

    api.getRuntime.mockResolvedValue(runtime({ installed: true }))
    api.uninstallDsh.mockResolvedValue(runtime())
    await mount(RuntimeTab)
    await click(byTest('dsh-uninstall'))
    await click(byTest('dsh-purge')?.querySelector('input') ?? byTest('dsh-purge'))
    await click(byTest('dsh-uninstall-confirm'))
    expect(api.uninstallDsh).toHaveBeenCalledWith(true)
    expect(byTest('dsh-state')?.textContent).toContain(words.runtime.dsh.notInstalled)
  })
})

describe('the permissions tab', () => {
  it('shows each category with what automatic comes to', async () => {
    api.getPermissions.mockResolvedValue(permissions())
    const page = await mount(PermissionsTab)
    const row = page.querySelector('[data-category="other"]')?.textContent ?? ''
    expect(row).toContain(words.runtime.permissions.categories.other)
    expect(row).toContain(words.runtime.permissions.modes.allow)
    expect(row).toContain(words.runtime.permissions.notes.other)
  })

  it('names a boss summoning a familiar that changes files, with when that is asked about', async () => {
    api.getPermissions.mockResolvedValue({ ...permissions(), categories: [{ category: 'open_session', mode: 'auto', auto: 'first_ask' }] })
    const page = await mount(PermissionsTab)
    const row = page.querySelector('[data-category="open_session"]')?.textContent ?? ''
    expect(row).toContain(words.runtime.permissions.categories.open_session)
    expect(row).toContain(words.runtime.permissions.notes.open_session)
    expect(row).not.toContain('open_session')
  })

  it('reloads what is there when the setting was changed elsewhere meanwhile', async () => {
    api.getPermissions.mockResolvedValueOnce(permissions(3))
    const page = await mount(PermissionsTab)
    api.setPermission.mockRejectedValue(refusal('stale'))
    api.getPermissions.mockResolvedValueOnce(permissions(4, 'deny'))
    const select = page.querySelector('[data-category="run_command"] .el-select') as HTMLElement & { __vueParentComponent?: { emit: (name: string, value: string) => void } }
    select.__vueParentComponent?.emit('change', 'allow')
    await flush()
    expect(api.setPermission).toHaveBeenCalledWith('run_command', 'allow', 3)
    expect(api.getPermissions).toHaveBeenCalledTimes(2)
    expect(page.textContent).toContain(words.errors.stale)
  })

  it('says so when the file cannot be read', async () => {
    api.getPermissions.mockResolvedValue({ ...permissions(), unreadable: true })
    await mount(PermissionsTab)
    expect(byTest('permissions-unreadable')).toBeTruthy()
  })
})

describe('the context tab', () => {
  it('sends only what was changed', async () => {
    api.getWindow.mockResolvedValue({ window: { ...modelWindow } })
    api.setWindow.mockResolvedValue({ window: { ...modelWindow, compact_at: 90000, compact_at_set: 90000 } })
    await mount(WindowTab)
    expect((byTest('window-save') as HTMLButtonElement).disabled).toBe(true)
    await type('window-compact-at', '90000')
    await click(byTest('window-save'))
    // With the model the settings were read for: they are not to be written to another.
    expect(api.setWindow).toHaveBeenCalledWith({ model_id: 'a1b2c3d4e5f60718', compact_at: 90000 })
    expect((byTest('window-save') as HTMLButtonElement).disabled).toBe(true)
  })

  it('switches summing up idle sessions off, and the time with it cannot be set then', async () => {
    api.getWindow.mockResolvedValue({ window: { ...modelWindow } })
    api.setWindow.mockResolvedValue({ window: { ...modelWindow, idle_compact_on: false } })
    await mount(WindowTab)
    await click(byTest('window-idle-on')?.querySelector('input'))
    expect((document.querySelector('[data-testid="window-idle"] input') as HTMLInputElement).disabled).toBe(true)
    await click(byTest('window-save'))
    expect(api.setWindow).toHaveBeenCalledWith({ model_id: 'a1b2c3d4e5f60718', idle_compact_on: false })
    expect((byTest('window-idle-on')?.querySelector('input') as HTMLInputElement).checked).toBe(false)
    expect((byTest('window-save') as HTMLButtonElement).disabled).toBe(true)
  })

  it('shows the settings of the model configured now when they were typed for another', async () => {
    api.getWindow.mockResolvedValueOnce({ window: { ...modelWindow } })
    api.setWindow.mockRejectedValue(refusal('stale'))
    const page = await mount(WindowTab)
    await type('window-context', '120000')
    api.getWindow.mockResolvedValueOnce({ window: { ...modelWindow, model: 'another-model', set: 64000, window: 64000 } })
    await click(byTest('window-save'))
    expect(api.getWindow).toHaveBeenCalledTimes(2)
    expect(page.textContent).toContain(words.errors.stale)
    expect(page.textContent).toContain('another-model')
    expect((document.querySelector('[data-testid="window-context"] input') as HTMLInputElement).value).toBe('64000')
  })

  it('saves an idle time it showed rounded as the seconds it was, unless the user changed it', async () => {
    // 1441 seconds is shown as 24.02 minutes; written back as shown it would be another time.
    api.getWindow.mockResolvedValue({ window: { ...modelWindow, idle_compact_after: 1441, idle_compact_after_set: 1441 } })
    api.setWindow.mockResolvedValue({ window: { ...modelWindow, idle_compact_after: 1441, idle_compact_after_set: 1441, compact_at: 90000, compact_at_set: 90000 } })
    await mount(WindowTab)
    expect((byTest('window-save') as HTMLButtonElement).disabled).toBe(true)
    await type('window-compact-at', '90000')
    await click(byTest('window-save'))
    expect(api.setWindow).toHaveBeenCalledWith({ model_id: 'a1b2c3d4e5f60718', compact_at: 90000 })
  })

  it('shows that there is no model to set anything for', async () => {
    api.getWindow.mockResolvedValue({ window: null })
    await mount(WindowTab)
    expect(byTest('window-no-model')).toBeTruthy()
    expect(byTest('window-save')).toBeNull()
  })

  it('keeps what was typed and says why when a value is refused', async () => {
    api.getWindow.mockResolvedValue({ window: { ...modelWindow } })
    api.setWindow.mockRejectedValue(refusal('out_of_range'))
    const page = await mount(WindowTab)
    await type('window-context', '5')
    await click(byTest('window-save'))
    expect(page.textContent).toContain(words.errors.out_of_range)
    expect((document.querySelector('[data-testid="window-context"] input') as HTMLInputElement).value).toBe('5')
  })
})

describe('the folders tab', () => {
  it('tells a size that was not added up to the end from one that was, and opens a folder by its key', async () => {
    api.getStorage.mockRejectedValueOnce(Object.assign(new Error('not found'), { response: { status: 404 } }))
    api.getPlaces.mockResolvedValue({ places: [
      { key: 'task_agents', path: 'D:/data/task_agents', exists: true, bytes: 1395864371, bytes_whole: false },
      { key: 'space', path: 'D:/data/space', exists: true, bytes: 2097152, bytes_whole: true },
      { key: 'dsh', path: 'D:/data/dsh', exists: false, bytes: null, bytes_whole: true },
    ] })
    api.openPlace.mockResolvedValue({ success: true })
    const page = await mount(PlacesTab)
    expect(page.querySelector('[data-place="task_agents"]')?.textContent).toContain('at least 1.3 GB')
    expect(page.querySelector('[data-place="space"]')?.textContent).toContain('2.0 MB')
    const missing = page.querySelector('[data-place="dsh"]')
    expect(missing?.textContent).toContain(words.runtime.places.missing)
    expect(missing?.querySelector('button')).toBeNull()
    await click(page.querySelector('[data-place="space"] button'))
    expect(api.openPlace).toHaveBeenCalledWith('space')
  })

  function storageSample(over: Record<string, unknown> = {}) {
    return {
      disk: { total: 500 * 1024 * 1024 * 1024, used: 200 * 1024 * 1024 * 1024, free: 300 * 1024 * 1024 * 1024 },
      total_bytes: 520 * 1024 * 1024,
      total_whole: true,
      reclaimable_bytes: 420 * 1024 * 1024,
      categories: [
        { key: 'worktrees', bytes: 300 * 1024 * 1024, bytes_whole: true, count: 2, reclaimable_bytes: 250 * 1024 * 1024, cleanable: true },
        { key: 'work_dirs', bytes: 150 * 1024 * 1024, bytes_whole: true, count: 2, reclaimable_bytes: 120 * 1024 * 1024, cleanable: true },
        { key: 'transcripts', bytes: 50 * 1024 * 1024, bytes_whole: true, count: 3, reclaimable_bytes: 50 * 1024 * 1024, cleanable: true },
        { key: 'space', bytes: 20 * 1024 * 1024, bytes_whole: true, count: 0, reclaimable_bytes: 0, cleanable: false },
      ],
      places: [
        { key: 'task_agents', path: 'D:/data/task_agents', exists: true, bytes: 200 * 1024 * 1024, bytes_whole: true },
        { key: 'worktrees', path: 'D:/data/wt', exists: true, bytes: 300 * 1024 * 1024, bytes_whole: true },
        { key: 'space', path: 'D:/data/space', exists: true, bytes: 20 * 1024 * 1024, bytes_whole: true },
      ],
      worktrees: [
        {
          id: 'wt-stale', kind: 'tracked', project: 'alpha', title: 'Old refactor', session_name: 'worker',
          session_id: 's1', commission_id: 'c1', state: 'kept', reason: 'conflict', conflicts: ['a.ts'],
          path: 'D:/data/wt/wt-stale', workdir: 'D:/data/wt/wt-stale', exists: true,
          bytes: 250 * 1024 * 1024, bytes_whole: true, created_at: 1700000000, updated_at: 1700000000,
          can_clean: true, can_land: true, stale: true,
        },
        {
          id: 'wt-live', kind: 'tracked', project: 'alpha', title: 'Live task', session_name: 'worker-2',
          session_id: 's2', commission_id: 'c2', state: 'active', reason: '', conflicts: [],
          path: 'D:/data/wt/wt-live', workdir: 'D:/data/wt/wt-live', exists: true,
          bytes: 50 * 1024 * 1024, bytes_whole: true, created_at: 1790000000, updated_at: 1790000000,
          can_clean: false, can_land: false, stale: false,
        },
      ],
      worktrees_more: 0,
      work_dirs: [
        {
          id: 'session:_daily/s-old', scope: 'session', project: '_daily', session_id: 's-old',
          session_name: 'one-off', title: 'Old script', session_status: 'idle',
          path: 'D:/data/task_agents/projects/_daily/sessions/s-old/work',
          bytes: 120 * 1024 * 1024, bytes_whole: true, file_count: 14, empty: false,
          last_active_at: 1700000000, in_use: false, can_clean: true, stale: true, referenced_by_project: false,
        },
        {
          id: 'session:_daily/s-live', scope: 'session', project: '_daily', session_id: 's-live',
          session_name: 'busy', title: 'Running job', session_status: 'running',
          path: 'D:/data/task_agents/projects/_daily/sessions/s-live/work',
          bytes: 30 * 1024 * 1024, bytes_whole: true, file_count: 3, empty: false,
          last_active_at: 1790000000, in_use: true, can_clean: false, stale: false, referenced_by_project: false,
        },
      ],
      work_dirs_more: 0,
      transcripts: {
        bytes: 50 * 1024 * 1024, bytes_whole: true, sessions: 3,
        expired_count: 2, expired_bytes: 40 * 1024 * 1024,
        outputs_count: 1, outputs_bytes: 10 * 1024 * 1024,
      },
      settings: {
        auto_clean_work_dirs: true, work_dir_kept_days: 30,
        auto_clean_worktrees: true, worktree_kept_days: 14,
        transcript_kept_days: 90, min_days: 1, max_days: 365,
        defaults: {
          auto_clean_work_dirs: true, work_dir_kept_days: 30,
          auto_clean_worktrees: true, worktree_kept_days: 14,
        },
      },
      ...over,
    }
  }

  it('renders the disk space breakdown, cleans a worktree with confirmation, and opens items by id', async () => {
    api.getStorage.mockResolvedValue(storageSample())
    api.openStorageItem.mockResolvedValue({ success: true })
    api.cleanWorktrees.mockResolvedValue({ cleaned: ['wt-stale'], deferred: [], freed_bytes: 250 * 1024 * 1024, failed: [] })

    const page = await mount(PlacesTab)
    expect(byTest('kpi-total')?.textContent).toContain('520 MB')
    expect(byTest('kpi-reclaimable')?.textContent).toContain('420 MB')
    expect(byTest('kpi-disk')?.textContent).toContain('300 GB')

    // Active worktree cannot be cleaned; stale kept worktree can be opened and cleaned.
    expect(byTest('worktree-clean-wt-live')).toBeNull()
    await click(byTest('worktree-open-wt-stale'))
    expect(api.openStorageItem).toHaveBeenCalledWith('worktree', 'wt-stale')

    await click(byTest('worktree-clean-wt-stale'))
    expect(byTest('storage-confirm-warn')?.textContent).toContain(words.runtime.storage.confirm.worktreesUnmergedWarn)
    await click(byTest('storage-confirm-submit'))
    expect(api.cleanWorktrees).toHaveBeenCalledWith(['wt-stale'])
    expect(byTest('storage-banner')?.textContent).toContain('250 MB')
    expect(api.getStorage).toHaveBeenCalledTimes(2)
  })

  it('switches to working folders via breakdown legend, disables in-use folders, and cleans idle ones', async () => {
    api.getStorage.mockResolvedValue(storageSample())
    api.cleanWorkDirs.mockResolvedValue({ cleaned: ['session:_daily/s-old'], freed_bytes: 120 * 1024 * 1024, failed: [] })

    const page = await mount(PlacesTab)
    await click(page.querySelector('[data-category="work_dirs"]'))
    expect(byTest('section-workdirs')).toBeTruthy()
    expect((byTest('workdir-clean-session:_daily/s-live') as HTMLButtonElement).disabled).toBe(true)

    await click(byTest('workdir-clean-session:_daily/s-old'))
    await click(byTest('storage-confirm-submit'))
    expect(api.cleanWorkDirs).toHaveBeenCalledWith(['session:_daily/s-old'])
    expect(byTest('storage-banner')?.textContent).toContain('120 MB')
  })

  it('cleans transcripts and saves updated automatic cleanup policy', async () => {
    const sample = storageSample()
    api.getStorage.mockResolvedValue(sample)
    api.cleanTranscripts.mockResolvedValue({ cleaned: 1, freed_bytes: 10 * 1024 * 1024, skipped: 0 })
    api.setStorageSettings.mockResolvedValue({
      settings: { ...sample.settings, auto_clean_work_dirs: false },
    })

    await mount(PlacesTab)
    await click(byTest('storage-tab-transcripts'))
    await click(byTest('clean-output-caches'))
    await click(byTest('storage-confirm-submit'))
    expect(api.cleanTranscripts).toHaveBeenCalledWith('outputs')

    // Toggle auto_clean_work_dirs off and save policy.
    expect((byTest('policy-save') as HTMLButtonElement).disabled).toBe(true)
    await click(byTest('policy-workdirs-enabled')?.querySelector('input'))
    expect((byTest('policy-save') as HTMLButtonElement).disabled).toBe(false)
    await click(byTest('policy-save'))
    expect(api.setStorageSettings).toHaveBeenCalledWith({ auto_clean_work_dirs: false })
    expect(byTest('policy-note')?.textContent).toContain(words.runtime.storage.policy.saved)
  })

  it('supports one-click cleaning of all stale items across worktrees, work dirs, and expired transcripts', async () => {
    api.getStorage.mockResolvedValue(storageSample())
    api.cleanWorktrees.mockResolvedValue({ cleaned: ['wt-stale'], deferred: [], freed_bytes: 250 * 1024 * 1024, failed: [] })
    api.cleanWorkDirs.mockResolvedValue({ cleaned: ['session:_daily/s-old'], freed_bytes: 120 * 1024 * 1024, failed: [] })
    api.cleanTranscripts.mockResolvedValue({ cleaned: 2, freed_bytes: 40 * 1024 * 1024, skipped: 0 })

    await mount(PlacesTab)
    await click(byTest('clean-all-stale'))
    await click(byTest('storage-confirm-submit'))
    expect(api.cleanWorktrees).toHaveBeenCalledWith(['wt-stale'])
    expect(api.cleanWorkDirs).toHaveBeenCalledWith(['session:_daily/s-old'])
    expect(api.cleanTranscripts).toHaveBeenCalledWith('expired')
    expect(byTest('storage-banner')?.textContent).toContain('410 MB')
  })
})

describe('the built-in abilities tab', () => {
  const switches = (over: Record<string, unknown> = {}) => ({ switches: {
    enabled: true, flags: { computer_use_enabled: false, browser_use_enabled: true }, gate: { ready: true },
    capabilities: { browser_use: { ready: true, reason: '' } }, model_images: { sees: true }, ...over,
  } })
  const browser = (settings: Record<string, unknown> = {}) => ({
    settings: { mode: 'own_profile', executable: null, headless: false, ...settings },
    modes: ['own_profile', 'attach'],
    browsers: [{ kind: 'chrome', name: 'Chrome', executable: 'C:/chrome.exe' }, { kind: 'edge', name: 'Edge', executable: 'C:/edge.exe' }],
  })

  it('shows what the agent process says after a switch, whether it took or not', async () => {
    api.getSwitches.mockResolvedValueOnce(switches())
    api.getBrowser.mockResolvedValue(browser())
    const page = await mount(BuiltinTab)
    expect(page.textContent).toContain('Found on this computer: Chrome, Edge')
    expect(byTest('builtin-needs-power')).toBeNull()

    // Switched on while there is no input to send: it cannot be used, and says why.
    api.setDeviceSwitch.mockResolvedValue({ success: true })
    api.getSwitches.mockResolvedValueOnce(switches({
      flags: { computer_use_enabled: true, browser_use_enabled: true },
      capabilities: { computer_use: { ready: false, reason: 'AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE' }, browser_use: { ready: true } },
    }))
    await click(byTest('switch-keyboard')?.querySelector('input'))
    expect(api.setDeviceSwitch).toHaveBeenCalledWith('computer_use_enabled', true)
    expect(byTest('keyboard-reason')?.textContent).toContain('AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE')

    api.setDeviceSwitch.mockRejectedValue(refusal('switch_failed'))
    api.getSwitches.mockResolvedValueOnce(switches())
    await click(byTest('switch-browser')?.querySelector('input'))
    expect(api.setDeviceSwitch).toHaveBeenLastCalledWith('browser_use_enabled', false)
    expect(page.textContent).toContain(words.errors.switch_failed)
    // The agent process still has it on: so does the page.
    expect((byTest('switch-browser')?.querySelector('input') as HTMLInputElement).checked).toBe(true)
  })

  it('says that nothing here takes effect while it is all switched off', async () => {
    api.getSwitches.mockResolvedValue(switches({
      enabled: false,
      capabilities: { computer_use: { ready: false, reason: '' }, browser_use: { ready: true } },
    }))
    api.getBrowser.mockResolvedValue(browser())
    await mount(BuiltinTab)
    expect(byTest('builtin-needs-power')).toBeTruthy()
    // The keyboard switch is off: why it could not be used is of no interest.
    expect(byTest('keyboard-reason')).toBeNull()
  })

  it('tells why the model is not sent pictures', async () => {
    api.getSwitches.mockResolvedValue(switches({ model_images: { sees: false, source: 'refused', reason: 'image input is not supported', lapses_at: 1797552000 } }))
    api.getBrowser.mockResolvedValue(browser())
    await mount(BuiltinTab)
    const shown = byTest('model-images')?.textContent ?? ''
    expect(shown).toContain('the model provider refused pictures')
    expect(document.body.textContent).toContain('image input is not supported')
    cleanup?.()
    api.getSwitches.mockResolvedValue(switches({ model_images: { sees: false, source: 'set by hand' } }))
    await mount(BuiltinTab)
    expect(byTest('model-images')?.textContent).toContain(words.runtime.builtin.imagesByHand)
  })

  it('says that the service did not answer when what came back holds no switches', async () => {
    api.getSwitches.mockResolvedValue({})
    api.getBrowser.mockResolvedValue({})
    const page = await mount(BuiltinTab)
    expect(page.textContent).toContain(words.errors.agent_unreachable)
  })

  it('saves one browser setting at a time and shows what came back', async () => {
    api.getSwitches.mockResolvedValue(switches())
    api.getBrowser.mockResolvedValue(browser())
    api.setBrowser.mockResolvedValue(browser({ headless: true }))
    await mount(BuiltinTab)
    await click(byTest('browser-headless')?.querySelector('input'))
    expect(api.setBrowser).toHaveBeenCalledWith({ headless: true })
    expect((byTest('browser-headless')?.querySelector('input') as HTMLInputElement).checked).toBe(true)
  })

  it('switches the task HUD of the main page, even while it is all switched off', async () => {
    api.getSwitches.mockResolvedValueOnce(switches({ enabled: false, task_hud_visible: true }))
    api.getBrowser.mockResolvedValue(browser())
    const page = await mount(BuiltinTab)
    expect(page.textContent).toContain(words.runtime.builtin.taskHudSwitch)
    const box = () => byTest('switch-task-hud')?.querySelector('input') as HTMLInputElement
    expect(box().checked).toBe(true)
    expect(box().disabled).toBe(false)

    api.setDeviceSwitch.mockResolvedValue({ success: true })
    api.getSwitches.mockResolvedValueOnce(switches({ enabled: false, task_hud_visible: false }))
    await click(box())
    expect(api.setDeviceSwitch).toHaveBeenCalledWith('task_hud_visible', false)
    expect(box().checked).toBe(false)
  })

  it('has no task HUD switch when an older agent process does not tell it', async () => {
    api.getSwitches.mockResolvedValue(switches())
    api.getBrowser.mockResolvedValue(browser())
    await mount(BuiltinTab)
    expect(byTest('switch-task-hud')).toBeNull()
  })
})
