// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, markRaw, nextTick } from 'vue'
import type { Component } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import { powerMessages } from '@/i18n/power'
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
  getSwitches: vi.fn(), setDeviceSwitch: vi.fn(), setPower: vi.fn(), getBrowser: vi.fn(), setBrowser: vi.fn(),
  consoleErrorCode: (error: unknown) => (error as { code?: string })?.code ?? null,
}))
vi.mock('@/api/console', () => api)

const words = powerMessages['en-US']
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
  model: 'agent-model', window: 200000, default: 200000, set: null, compact_at: 170000, compact_at_set: null,
  idle_compact_after: 1440, idle_compact_after_set: null,
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
    expect(api.setWindow).toHaveBeenCalledWith({ compact_at: 90000 })
    expect((byTest('window-save') as HTMLButtonElement).disabled).toBe(true)
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

    // Switched on, and put off again at once by a model that cannot be reached.
    api.setDeviceSwitch.mockResolvedValue({ success: true })
    api.getSwitches.mockResolvedValueOnce(switches({
      flags: { computer_use_enabled: true, browser_use_enabled: true },
      capabilities: { computer_use: { ready: false, reason: 'AGENT_API_KEY_INVALID' }, browser_use: { ready: true } },
    }))
    await click(byTest('switch-keyboard')?.querySelector('input'))
    expect(api.setDeviceSwitch).toHaveBeenCalledWith('computer_use_enabled', true)
    expect(byTest('keyboard-reason')?.textContent).toContain('AGENT_API_KEY_INVALID')

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
      capabilities: { computer_use: { ready: false, reason: 'AGENT_PRECHECK_PENDING' }, browser_use: { ready: true } },
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

  it('saves one browser setting at a time and shows what came back', async () => {
    api.getSwitches.mockResolvedValue(switches())
    api.getBrowser.mockResolvedValue(browser())
    api.setBrowser.mockResolvedValue(browser({ headless: true }))
    await mount(BuiltinTab)
    await click(byTest('browser-headless')?.querySelector('input'))
    expect(api.setBrowser).toHaveBeenCalledWith({ headless: true })
    expect((byTest('browser-headless')?.querySelector('input') as HTMLInputElement).checked).toBe(true)
  })
})
