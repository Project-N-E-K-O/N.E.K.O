// @vitest-environment happy-dom
import {
  pythonPackagesSnapshot,
  pythonRequirement,
  pythonDependencyGraph,
  pythonDependencyNode,
} from '@/test/fixtures/pythonPackages'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, reactive } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import Panel from './AuthoringProjectPythonPackages.vue'
import { pythonPackageMessages } from '@/i18n/python-packages'
import type { AuthoringProject } from '@/api/pluginProjects'
import type { PythonPackagesSnapshot, PythonPackagesTask } from '@/api/projectPythonPackages'
const api = vi.hoisted(() => ({
  getProjectPythonPackages: vi.fn(),
  saveProjectPythonRequirements: vi.fn(),
  startProjectPythonTask: vi.fn(),
  getProjectPythonTask: vi.fn(),
  cancelProjectPythonTask: vi.fn(),
  stopPlugin: vi.fn(),
}))
vi.mock('@/api/projectPythonPackages', async (original) => ({
  ...(await original<typeof import('@/api/projectPythonPackages')>()),
  ...api,
}))
vi.mock('@/api/plugins', () => ({ stopPlugin: api.stopPlugin }))
vi.mock('vue-router', () => ({ onBeforeRouteLeave: vi.fn(), onBeforeRouteUpdate: vi.fn() }))
const en = pythonPackageMessages['en-US']
const project = { project_id: 'p1', plugin_id: 'demo' } as AuthoringProject
const state = (overrides: Partial<PythonPackagesSnapshot> = {}) =>
  pythonPackagesSnapshot({
    requirements: [
      pythonRequirement('httpx', {
        requirement: 'httpx[socks]>=0.27; python_version >= "3.11"',
        extras: ['socks'],
        specifier: '>=0.27',
        marker: 'python_version >= "3.11"',
        installed_version: '0.28.1',
      }),
    ],
    dependency_graph: pythonDependencyGraph(
      ['httpx'],
      [
        pythonDependencyNode('httpx', ['anyio'], { version: '0.28.1' }),
        pythonDependencyNode('anyio', [], { version: '4.0' }),
      ]
    ),
    ...overrides,
  })
const task = (overrides: Partial<PythonPackagesTask> = {}): PythonPackagesTask => ({
  task_id: 't1',
  project_id: 'p1',
  operation: 'sync',
  status: 'running',
  phase: 'installing',
  message: '',
  log: ['Installing httpx'],
  started_at: Date.now() / 1000,
  updated_at: Date.now() / 1000,
  finished_at: null,
  result: null,
  ...overrides,
})
let app: ReturnType<typeof createApp> | null = null
async function flush() {
  for (let n = 0; n < 12; n++) {
    await Promise.resolve()
    await nextTick()
  }
}
async function mount() {
  const container = document.createElement('div')
  document.body.append(container)
  const props = reactive({ project })
  const changed = vi.fn()
  app = createApp({ render: () => h(Panel, { ...props, onChanged: changed }) })
  app
    .use(ElementPlus)
    .use(
      createI18n({
        legacy: false,
        locale: 'en-US',
        messages: {
          'en-US': {
            pythonPackages: en,
            common: {
              edit: 'Edit',
              save: 'Save',
              refresh: 'Refresh',
              cancel: 'Cancel',
              confirm: 'Confirm',
            },
          },
        },
      })
    )
    .mount(container)
  await flush()
  return { container, props, changed }
}
async function click(text: string) {
  const button = [...document.querySelectorAll<HTMLButtonElement>('button')].find(
    (el) => el.textContent?.trim() === text
  )
  expect(button, text).toBeTruthy()
  button!.click()
  await flush()
}
async function fill(id: string, value: string) {
  const input = document.querySelector<HTMLInputElement>(`#${id}`)!
  input.value = value
  input.dispatchEvent(new Event('input'))
  await flush()
}
beforeEach(() => {
  vi.resetAllMocks()
  api.getProjectPythonPackages.mockResolvedValue(state())
  api.saveProjectPythonRequirements.mockImplementation(async (_id, requirements) =>
    state({
      revision: 'r2',
      requirements: requirements.map((requirement: string) => ({
        ...state().requirements[0],
        requirement,
      })),
    })
  )
  api.startProjectPythonTask.mockResolvedValue(task())
  api.getProjectPythonTask.mockResolvedValue(task())
  api.cancelProjectPythonTask.mockResolvedValue(task({ status: 'canceled', phase: 'finished' }))
})
afterEach(() => {
  app?.unmount()
  app = null
  document.body.innerHTML = ''
  vi.useRealTimers()
})
describe('project dependency management', () => {
  it('adds and installs a named package with automatic version selection', async () => {
    await mount()
    await click(en.add)
    const dialog = document.querySelector('.el-dialog')!
    expect(dialog.querySelector('details')?.open).toBe(false)
    expect(dialog.textContent).not.toContain('PEP 508')
    expect(dialog.textContent).not.toContain('Declaration preview')
    await fill('python-package-name', 'demo')
    await click(en.addInstall)
    expect(api.saveProjectPythonRequirements).toHaveBeenCalledWith(
      'p1',
      [state().requirements[0]!.requirement, 'demo'],
      'r1'
    )
    expect(api.startProjectPythonTask).toHaveBeenCalledWith('p1', 'sync', 'r2')
  })
  it('keeps a failed save editable and does not leave an extra draft entry', async () => {
    api.saveProjectPythonRequirements.mockRejectedValue(new Error('Unable to save'))
    await mount()
    await click(en.add)
    await fill('python-package-name', 'demo')
    await click(en.saveOnly)
    expect(document.querySelector<HTMLInputElement>('#python-package-name')?.value).toBe('demo')
    expect(document.querySelectorAll('.python-package-declaration')).toHaveLength(1)
    expect(api.startProjectPythonTask).not.toHaveBeenCalled()
  })
  it('shows the backend duplicate error without starting an installation', async () => {
    api.saveProjectPythonRequirements.mockRejectedValue({
      response: { data: { detail: { code: 'PYTHON_PACKAGES_REQUIREMENTS_DUPLICATE' } } },
    })
    await mount()
    await click(en.add)
    await fill('python-package-name', 'HTTPX[socks] >=0.27; python_version >= "3.11"')
    await click(en.addInstall)
    expect(api.saveProjectPythonRequirements).toHaveBeenCalledWith(
      'p1',
      [state().requirements[0]!.requirement, 'HTTPX[socks]>=0.27; python_version >= "3.11"'],
      'r1'
    )
    expect(document.querySelector('.el-dialog')?.textContent).toContain(en.alreadyAdded)
    expect(document.querySelectorAll('.python-package-declaration')).toHaveLength(1)
    expect(api.startProjectPythonTask).not.toHaveBeenCalled()
  })
  it('retries the initial snapshot after a load failure', async () => {
    api.getProjectPythonPackages.mockRejectedValueOnce(new Error('Read failed'))
    await mount()
    expect(document.body.textContent).toContain(en.actionFailed)
    await click('Refresh')
    expect(api.getProjectPythonPackages).toHaveBeenCalledTimes(2)
    expect(document.querySelector('.python-package-declaration code')?.textContent).toBe(
      state().requirements[0]!.requirement
    )
  })
  it('edits one of two same-package declarations without rejecting the other', async () => {
    const other = 'httpx[http2]<1; python_version >= "3.11"'
    api.getProjectPythonPackages.mockResolvedValue(
      state({
        requirements: [
          state().requirements[0]!,
          { ...state().requirements[0]!, requirement: other },
        ],
      })
    )
    await mount()
    await click('Edit')
    await fill('python-package-version', '>=0.28')
    await click(en.saveOnly)
    expect(api.saveProjectPythonRequirements).toHaveBeenCalledWith(
      'p1',
      ['httpx[socks]>=0.28; python_version >= "3.11"', other],
      'r1'
    )
  })
  it('allows saving for later while a plugin is running', async () => {
    api.getProjectPythonPackages.mockResolvedValue(state({ running: true }))
    await mount()
    await click(en.add)
    await fill('python-package-name', 'demo')
    const install = [...document.querySelectorAll<HTMLButtonElement>('button')].find(
      (item) => item.textContent?.trim() === en.addInstall
    )!
    expect(install.disabled).toBe(true)
    await click(en.saveOnly)
    expect(api.saveProjectPythonRequirements).toHaveBeenCalled()
    expect(api.startProjectPythonTask).not.toHaveBeenCalled()
  })
  it.each(['failed', 'canceled'] as const)(
    'does not treat a %s task as a successful full check',
    async (status) => {
      api.getProjectPythonPackages.mockResolvedValue(
        state({ task: task({ status, result: { after: state(), changes: [] } }) })
      )
      await mount()
      expect(document.body.textContent).toContain(en.notChecked)
      expect(document.body.textContent).not.toContain(en.noFindings)
    }
  )
  it('releases the UI after the server forgets an active task', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    api.getProjectPythonPackages
      .mockResolvedValueOnce(state({ task: task(), operation: 'python-packages' }))
      .mockResolvedValue(state())
    api.getProjectPythonTask.mockRejectedValue({
      response: { data: { detail: { code: 'PYTHON_PACKAGES_TASK_NOT_FOUND' } } },
    })
    await mount()
    await vi.advanceTimersByTimeAsync(1100)
    await flush()
    const sync = [...document.querySelectorAll<HTMLButtonElement>('button')].find(
      (el) => el.textContent?.trim() === en.sync
    )!
    expect(sync.disabled).toBe(false)
    expect(api.getProjectPythonTask).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(2500)
    expect(api.getProjectPythonTask).toHaveBeenCalledTimes(1)
  })
  it('pins a version while preserving extras and the environment marker', async () => {
    await mount()
    await click(en.pin)
    await click(en.saveSync)
    expect(api.saveProjectPythonRequirements).toHaveBeenCalledWith(
      'p1',
      ['httpx[socks]==0.28.1; python_version >= "3.11"'],
      'r1'
    )
    expect(api.startProjectPythonTask).toHaveBeenCalledWith('p1', 'sync', 'r2')
  })
  it('keeps the original revision and draft across a refresh after an external edit', async () => {
    await mount()
    await click(en.pin)
    api.getProjectPythonPackages.mockResolvedValue(state({ revision: 'external' }))
    await click('Refresh')
    await click(en.save)
    expect(api.saveProjectPythonRequirements).toHaveBeenCalledWith(
      'p1',
      ['httpx[socks]==0.28.1; python_version >= "3.11"'],
      'r1'
    )
  })
  it('preserves the editor target and original revision when an earlier refresh completes', async () => {
    await mount()
    let resolve!: (value: PythonPackagesSnapshot) => void
    api.getProjectPythonPackages.mockReturnValueOnce(
      new Promise((done) => {
        resolve = done
      })
    )
    await click('Refresh')
    await click('Edit')
    await fill('python-package-version', '>=1,<2')
    resolve(
      state({
        revision: 'external',
        requirements: [{ ...state().requirements[0]!, requirement: 'anyio>=4', name: 'anyio' }],
      })
    )
    await flush()
    await click(en.saveOnly)
    expect(api.saveProjectPythonRequirements).toHaveBeenCalledWith(
      'p1',
      ['httpx[socks]>=1,<2; python_version >= "3.11"'],
      'r1'
    )
  })
  it('aligns an unchanged draft with the latest refresh after canceling the editor', async () => {
    await mount()
    let resolve!: (value: PythonPackagesSnapshot) => void
    api.getProjectPythonPackages.mockReturnValueOnce(
      new Promise((done) => {
        resolve = done
      })
    )
    await click('Refresh')
    await click('Edit')
    await fill('python-package-version', '>=1,<2')
    resolve(
      state({
        revision: 'external',
        requirements: [{ ...state().requirements[0]!, requirement: 'anyio>=4', name: 'anyio' }],
      })
    )
    await flush()
    await click('Cancel')
    expect(document.querySelector('.python-package-declaration code')?.textContent).toBe('anyio>=4')
    await click('Edit')
    expect(document.querySelector<HTMLInputElement>('#python-package-name')?.value).toBe('anyio')
    await click(en.saveOnly)
    expect(api.saveProjectPythonRequirements).toHaveBeenCalledWith('p1', ['anyio>=4'], 'external')
  })
  it('retains unsaved draft changes and their revision when canceling after a refresh', async () => {
    await mount()
    await click(en.pin)
    let resolve!: (value: PythonPackagesSnapshot) => void
    api.getProjectPythonPackages.mockReturnValueOnce(
      new Promise((done) => {
        resolve = done
      })
    )
    await click('Refresh')
    await click('Edit')
    resolve(
      state({
        revision: 'external',
        requirements: [{ ...state().requirements[0]!, requirement: 'anyio>=4', name: 'anyio' }],
      })
    )
    await flush()
    await click('Cancel')
    expect(document.querySelector('.python-package-declaration code')?.textContent).toBe(
      'httpx[socks]==0.28.1; python_version >= "3.11"'
    )
    await click(en.save)
    expect(api.saveProjectPythonRequirements).toHaveBeenCalledWith(
      'p1',
      ['httpx[socks]==0.28.1; python_version >= "3.11"'],
      'r1'
    )
  })
  it('does not replace a saved declaration with an older in-flight refresh', async () => {
    await mount()
    let resolve!: (value: PythonPackagesSnapshot) => void
    api.getProjectPythonPackages.mockReturnValueOnce(
      new Promise((done) => {
        resolve = done
      })
    )
    await click('Refresh')
    await click('Edit')
    await fill('python-package-version', '>=1,<2')
    await click(en.saveOnly)
    resolve(state())
    await flush()
    expect(document.querySelector('.python-package-declaration code')?.textContent).toBe(
      'httpx[socks]>=1,<2; python_version >= "3.11"'
    )
    await click('Edit')
    await fill('python-package-version', '>=2,<3')
    await click(en.saveOnly)
    expect(api.saveProjectPythonRequirements).toHaveBeenLastCalledWith(
      'p1',
      ['httpx[socks]>=2,<3; python_version >= "3.11"'],
      'r2'
    )
  })
  it('keeps a newly started task when an older refresh completes', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    await mount()
    let resolve!: (value: PythonPackagesSnapshot) => void
    api.getProjectPythonPackages.mockReturnValueOnce(
      new Promise((done) => {
        resolve = done
      })
    )
    await click('Refresh')
    const button = (label: string) =>
      [...document.querySelectorAll<HTMLButtonElement>('button')].find(
        (item) => item.textContent?.trim() === label
      )!
    await click(en.sync)
    resolve(state())
    await flush()
    expect(button(en.sync).disabled).toBe(true)
    expect(button('Refresh').classList.contains('is-loading')).toBe(false)
    expect(document.body.textContent).toContain('Installing httpx')
    await vi.advanceTimersByTimeAsync(1000)
    await flush()
    expect(api.getProjectPythonTask).toHaveBeenCalledWith('p1', 't1')
  })
  it('saves and syncs an empty declaration list after removing the final dependency', async () => {
    await mount()
    await click(en.remove)
    await click(en.saveSync)
    expect(api.saveProjectPythonRequirements).toHaveBeenCalledWith('p1', [], 'r1')
    expect(api.startProjectPythonTask).toHaveBeenCalledWith('p1', 'sync', 'r2')
  })
  it('requires an explicit plugin stop before syncing', async () => {
    api.getProjectPythonPackages.mockResolvedValue(state({ running: true }))
    await mount()
    const sync = [...document.querySelectorAll<HTMLButtonElement>('button')].find(
      (el) => el.textContent?.trim() === en.sync
    )!
    expect(sync.disabled).toBe(true)
    api.getProjectPythonPackages.mockResolvedValue(state())
    await click(en.stop)
    expect(api.stopPlugin).toHaveBeenCalledWith('demo')
    expect(sync.disabled).toBe(false)
  })
  it('reconnects to an active task and cancels it', async () => {
    api.getProjectPythonPackages.mockResolvedValue(state({ task: task() }))
    await mount()
    expect(document.body.textContent).toContain('Installing httpx')
    await click(en.cancel)
    expect(api.cancelProjectPythonTask).toHaveBeenCalledWith('p1', 't1')
    expect(api.startProjectPythonTask).not.toHaveBeenCalled()
  })
  it.each(['succeeded', 'failed', 'canceled'] as const)(
    'notifies once when refresh observes a %s task before polling',
    async (status) => {
      vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
      const finished = task({ status, phase: 'finished' })
      api.getProjectPythonPackages
        .mockResolvedValueOnce(state({ task: task() }))
        .mockResolvedValue(state({ task: finished }))
      const { changed } = await mount()
      await click('Refresh')
      expect(changed).toHaveBeenCalledTimes(1)
      await vi.advanceTimersByTimeAsync(2500)
      expect(api.getProjectPythonTask).not.toHaveBeenCalled()
      await click('Refresh')
      expect(changed).toHaveBeenCalledTimes(1)
      api.getProjectPythonPackages.mockResolvedValue(
        state({ task: { ...finished, task_id: 'another-task' } })
      )
      await click('Refresh')
      expect(changed).toHaveBeenCalledTimes(2)
    }
  )
  it.each(['poll', 'cancel'] as const)(
    'retries failed terminal snapshot reads after %s until controls are released',
    async (completion) => {
      vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
      const finished = task({
        status: completion === 'cancel' ? 'canceled' : 'failed',
        phase: 'finished',
      })
      api.getProjectPythonPackages
        .mockResolvedValueOnce(state({ task: task(), operation: 'python-packages-sync' }))
        .mockRejectedValueOnce(new Error('Read failed'))
        .mockRejectedValueOnce(new Error('Read failed again'))
        .mockResolvedValue(state({ task: finished }))
      api.getProjectPythonTask.mockResolvedValue(finished)
      api.cancelProjectPythonTask.mockResolvedValue(finished)
      const { changed } = await mount()
      if (completion === 'cancel') await click(en.cancel)
      else await vi.advanceTimersByTimeAsync(1000)
      await flush()
      expect(api.getProjectPythonPackages).toHaveBeenCalledTimes(2)
      expect(changed).toHaveBeenCalledTimes(1)
      const sync = [...document.querySelectorAll<HTMLButtonElement>('button')].find(
        (button) => button.textContent?.trim() === en.sync
      )!
      expect(sync.disabled).toBe(true)
      await vi.advanceTimersByTimeAsync(1000)
      await flush()
      expect(api.getProjectPythonPackages).toHaveBeenCalledTimes(3)
      await vi.advanceTimersByTimeAsync(1000)
      await flush()
      expect(api.getProjectPythonPackages).toHaveBeenCalledTimes(4)
      expect(sync.disabled).toBe(false)
      expect(changed).toHaveBeenCalledTimes(1)
      await vi.advanceTimersByTimeAsync(2500)
      expect(api.getProjectPythonPackages).toHaveBeenCalledTimes(4)
      expect(api.getProjectPythonTask).toHaveBeenCalledTimes(completion === 'cancel' ? 0 : 1)
    }
  )
  it('refreshes a terminal snapshot until its project operation has released', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    const finished = task({ status: 'succeeded', phase: 'finished' })
    api.getProjectPythonPackages
      .mockResolvedValueOnce(state({ task: task(), operation: 'python-packages-sync' }))
      .mockResolvedValueOnce(state({ task: finished, operation: 'python-packages-sync' }))
      .mockResolvedValue(state({ task: finished }))
    api.getProjectPythonTask.mockResolvedValue(finished)
    const { changed } = await mount()
    await vi.advanceTimersByTimeAsync(2000)
    await flush()
    expect(api.getProjectPythonPackages).toHaveBeenCalledTimes(3)
    const sync = [...document.querySelectorAll<HTMLButtonElement>('button')].find(
      (button) => button.textContent?.trim() === en.sync
    )!
    expect(sync.disabled).toBe(false)
    expect(changed).toHaveBeenCalledTimes(1)
    expect(api.getProjectPythonTask).toHaveBeenCalledTimes(1)
  })
  it.each(['initial', 'after-terminal'] as const)(
    'keeps refreshing a busy project without a task on %s snapshot',
    async (timing) => {
      vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
      const busyWithoutTask = state({ task: null, operation: 'python-packages-sync' })
      if (timing === 'after-terminal') {
        api.getProjectPythonPackages.mockResolvedValueOnce(
          state({ task: task(), operation: 'python-packages-sync' })
        )
        api.getProjectPythonTask.mockResolvedValue(task({ status: 'failed', phase: 'finished' }))
      }
      api.getProjectPythonPackages.mockResolvedValueOnce(busyWithoutTask).mockResolvedValue(state())
      const { changed } = await mount()
      if (timing === 'after-terminal') {
        await vi.advanceTimersByTimeAsync(1000)
        await flush()
      }
      const sync = [...document.querySelectorAll<HTMLButtonElement>('button')].find(
        (button) => button.textContent?.trim() === en.sync
      )!
      expect(sync.disabled).toBe(true)
      await vi.advanceTimersByTimeAsync(1000)
      await flush()
      expect(sync.disabled).toBe(false)
      const reads = timing === 'after-terminal' ? 3 : 2
      expect(api.getProjectPythonPackages).toHaveBeenCalledTimes(reads)
      expect(changed).toHaveBeenCalledTimes(timing === 'after-terminal' ? 1 : 0)
      await vi.advanceTimersByTimeAsync(2500)
      expect(api.getProjectPythonPackages).toHaveBeenCalledTimes(reads)
    }
  )
  it('retries a failed snapshot after the server forgets the task', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    api.getProjectPythonPackages
      .mockResolvedValueOnce(state({ task: task(), operation: 'python-packages-sync' }))
      .mockRejectedValueOnce(new Error('Read failed'))
      .mockResolvedValueOnce(state({ task: null, operation: 'python-packages-sync' }))
      .mockResolvedValue(state())
    api.getProjectPythonTask.mockRejectedValue({
      response: { data: { detail: { code: 'PYTHON_PACKAGES_TASK_NOT_FOUND' } } },
    })
    const { changed } = await mount()
    await vi.advanceTimersByTimeAsync(3000)
    await flush()
    const sync = [...document.querySelectorAll<HTMLButtonElement>('button')].find(
      (button) => button.textContent?.trim() === en.sync
    )!
    expect(sync.disabled).toBe(false)
    expect(api.getProjectPythonPackages).toHaveBeenCalledTimes(4)
    expect(api.getProjectPythonTask).toHaveBeenCalledTimes(1)
    expect(changed).not.toHaveBeenCalled()
  })
  it('does not carry terminal snapshot retries into the next project', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    api.getProjectPythonPackages
      .mockResolvedValueOnce(state({ task: task(), operation: 'python-packages-sync' }))
      .mockRejectedValueOnce(new Error('Read failed'))
      .mockResolvedValue(state())
    api.getProjectPythonTask.mockResolvedValue(task({ status: 'failed', phase: 'finished' }))
    const { props, changed } = await mount()
    await vi.advanceTimersByTimeAsync(1000)
    await flush()
    expect(changed).toHaveBeenCalledTimes(1)
    props.project = { ...project, project_id: 'p2' }
    await flush()
    expect(api.getProjectPythonPackages).toHaveBeenLastCalledWith('p2')
    await vi.advanceTimersByTimeAsync(2500)
    expect(api.getProjectPythonPackages).toHaveBeenCalledTimes(3)
    expect(changed).toHaveBeenCalledTimes(1)
  })
  it('cleans up a pending terminal snapshot retry when the panel unmounts', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    api.getProjectPythonPackages
      .mockResolvedValueOnce(state({ task: task(), operation: 'python-packages-sync' }))
      .mockRejectedValue(new Error('Read failed'))
    api.getProjectPythonTask.mockResolvedValue(task({ status: 'failed', phase: 'finished' }))
    await mount()
    await vi.advanceTimersByTimeAsync(1000)
    await flush()
    app!.unmount()
    app = null
    await vi.advanceTimersByTimeAsync(2500)
    expect(api.getProjectPythonPackages).toHaveBeenCalledTimes(2)
  })
  it('does not repeat the terminal notification when an earlier poll finishes after refresh', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    const finished = task({ status: 'failed', phase: 'finished' })
    api.getProjectPythonPackages
      .mockResolvedValueOnce(state({ task: task() }))
      .mockResolvedValue(state({ task: finished }))
    let resolve!: (value: PythonPackagesTask) => void
    api.getProjectPythonTask.mockReturnValueOnce(
      new Promise((done) => {
        resolve = done
      })
    )
    const { changed } = await mount()
    await vi.advanceTimersByTimeAsync(1000)
    await flush()
    expect(api.getProjectPythonTask).toHaveBeenCalledTimes(1)
    await click('Refresh')
    expect(changed).toHaveBeenCalledTimes(1)
    resolve(finished)
    await flush()
    expect(changed).toHaveBeenCalledTimes(1)
  })
  it('does not repeat the notification for a canceled task returned by refresh', async () => {
    const finished = task({ status: 'canceled', phase: 'finished' })
    api.getProjectPythonPackages
      .mockResolvedValueOnce(state({ task: task() }))
      .mockResolvedValue(state({ task: finished }))
    const { changed } = await mount()
    await click(en.cancel)
    expect(changed).toHaveBeenCalledTimes(1)
    await click('Refresh')
    expect(changed).toHaveBeenCalledTimes(1)
  })
  it('combines save and terminal snapshot changes into one notification', async () => {
    const finished = task({ status: 'failed', phase: 'finished' })
    const saved = state({ revision: 'r2', task: finished })
    api.saveProjectPythonRequirements.mockResolvedValue(saved)
    const { changed } = await mount()
    await click(en.pin)
    await click(en.save)
    expect(changed).toHaveBeenCalledTimes(1)
    api.getProjectPythonPackages.mockResolvedValue(saved)
    await click('Refresh')
    expect(changed).toHaveBeenCalledTimes(1)
  })
  it('distinguishes unchecked state from full check results and rejects stale findings', async () => {
    await mount()
    expect(document.body.textContent).toContain(en.notChecked)
    api.getProjectPythonPackages.mockResolvedValue(
      state({
        task: task({
          status: 'succeeded',
          result: {
            after: {
              ...state(),
              issues: [
                {
                  code: 'dependency_missing',
                  package: 'httpx',
                  requirement: 'missing-lib>=1',
                  message: 'Required package is missing',
                },
              ],
            },
            changes: [],
          },
        }),
      })
    )
    await click('Refresh')
    expect(document.body.textContent).toContain('Required package is missing')
    api.getProjectPythonPackages.mockResolvedValue(
      state({
        revision: 'r2',
        task: task({ status: 'succeeded', result: { after: state(), changes: [] } }),
      })
    )
    await click('Refresh')
    expect(document.body.textContent).toContain(en.notChecked)
  })
  it('ends the previous project editing session before loading new declarations', async () => {
    const { props } = await mount()
    await click('Edit')
    await fill('python-package-version', '>=1,<2')
    api.getProjectPythonPackages.mockResolvedValue(
      state({
        revision: 'p2-revision',
        requirements: [{ ...state().requirements[0]!, requirement: 'anyio>=4', name: 'anyio' }],
      })
    )
    props.project = { ...project, project_id: 'p2' }
    await flush()
    expect(document.querySelector('.python-package-declaration code')?.textContent).toBe('anyio>=4')
    expect(api.saveProjectPythonRequirements).not.toHaveBeenCalled()
    await click('Edit')
    expect(document.querySelector<HTMLInputElement>('#python-package-name')?.value).toBe('anyio')
    await click(en.saveOnly)
    expect(api.saveProjectPythonRequirements).toHaveBeenCalledWith(
      'p2',
      ['anyio>=4'],
      'p2-revision'
    )
  })
  it('ignores the previous project response after switching projects', async () => {
    let resolve!: (value: PythonPackagesSnapshot) => void
    api.getProjectPythonPackages.mockReturnValueOnce(
      new Promise((done) => {
        resolve = done
      })
    )
    const { props } = await mount()
    props.project = { ...project, project_id: 'p2' }
    await flush()
    resolve(state({ vendor_path: '/old/vendor' }))
    await flush()
    expect(document.body.textContent).not.toContain('/old/vendor')
    expect(api.getProjectPythonPackages).toHaveBeenCalledWith('p2')
  })
})
