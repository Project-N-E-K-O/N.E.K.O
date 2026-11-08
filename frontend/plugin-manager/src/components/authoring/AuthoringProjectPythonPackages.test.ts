// @vitest-environment happy-dom
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
const state = (overrides: Partial<PythonPackagesSnapshot> = {}): PythonPackagesSnapshot => ({
  revision: 'r1',
  requirements: [
    {
      requirement: 'httpx[socks]>=0.27; python_version >= "3.11"',
      name: 'httpx',
      extras: ['socks'],
      specifier: '>=0.27',
      marker: 'python_version >= "3.11"',
      editable: true,
      status: 'satisfied',
      installed_version: '0.28.1',
    },
  ],
  installed: [
    {
      name: 'httpx',
      version: '0.28.1',
      direct: true,
      requires_dist: ['anyio'],
      requires_python: '>=3.8',
      summary: 'HTTP client',
    },
    {
      name: 'anyio',
      version: '4.0',
      direct: false,
      requires_dist: [],
      requires_python: '>=3.8',
      summary: '',
    },
  ],
  issues: [],
  market_issues: [],
  vendor_path: '/demo/vendor',
  pyproject_path: '/demo/pyproject.toml',
  vendor_exists: true,
  running: false,
  environment: {
    python_version: '3.11.9',
    python_executable: '/python',
    platform: 'Linux',
    architecture: 'x64',
    installer: 'uv',
    installer_path: '/uv',
    packaged: false,
    can_sync: true,
    setup_required: false,
    reason: null,
  },
  task: null,
  operation: null,
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
  app = createApp({ render: () => h(Panel, props) })
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
  return { container, props }
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
  vi.clearAllMocks()
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
  it.each(['', '   '])(
    'returns to the basic form after clearing the full declaration to %j',
    async (value) => {
      await mount()
      await click(en.add)
      await fill('python-package-name', 'demo')
      await fill('python-package-version', '>=2')
      const checkbox = document.querySelector<HTMLInputElement>(
        '.el-dialog input[type="checkbox"]'
      )!
      checkbox.click()
      await flush()
      expect(document.querySelector<HTMLInputElement>('#python-package-requirement')?.value).toBe(
        'demo>=2'
      )
      await fill('python-package-requirement', value)
      checkbox.click()
      await flush()
      expect(checkbox.checked).toBe(false)
      expect(document.querySelector<HTMLInputElement>('#python-package-name')?.value).toBe('')
      expect(document.querySelector<HTMLInputElement>('#python-package-version')?.value).toBe('')
      expect(document.querySelector<HTMLInputElement>('#python-package-extras')?.value).toBe('')
      expect(document.querySelector<HTMLInputElement>('#python-package-marker')?.value).toBe('')
      await click('Cancel')
      expect(api.saveProjectPythonRequirements).not.toHaveBeenCalled()
    }
  )
  it('returns to the basic form with a populated full declaration', async () => {
    await mount()
    await click(en.add)
    const checkbox = document.querySelector<HTMLInputElement>('.el-dialog input[type="checkbox"]')!
    checkbox.click()
    await flush()
    await fill('python-package-requirement', 'demo[extra]>=2; sys_platform == "win32"')
    checkbox.click()
    await flush()
    expect(checkbox.checked).toBe(false)
    expect(document.querySelector<HTMLInputElement>('#python-package-name')?.value).toBe('demo')
    expect(document.querySelector<HTMLInputElement>('#python-package-version')?.value).toBe('>=2')
    expect(document.querySelector<HTMLInputElement>('#python-package-extras')?.value).toBe('extra')
    expect(document.querySelector<HTMLInputElement>('#python-package-marker')?.value).toBe(
      'sys_platform == "win32"'
    )
  })
  it('retains a newly added unsaved declaration when reopening its editor', async () => {
    await mount()
    await click(en.add)
    await fill('python-package-name', 'demo')
    await fill('python-package-version', '>=2')
    await click('Save')
    const edits = [...document.querySelectorAll<HTMLButtonElement>('button')].filter(
      (button) => button.textContent?.trim() === 'Edit'
    )
    edits[1]!.click()
    await flush()
    expect(document.querySelector<HTMLInputElement>('#python-package-requirement')?.value).toBe(
      'demo>=2'
    )
    await fill('python-package-requirement', 'demo==3')
    await click('Save')
    const changed = [...document.querySelectorAll<HTMLButtonElement>('button')].filter(
      (button) => button.textContent?.trim() === 'Edit'
    )
    changed[1]!.click()
    await flush()
    expect(document.querySelector<HTMLInputElement>('#python-package-requirement')?.value).toBe(
      'demo==3'
    )
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
  it('saves a named package with extras and an environment condition', async () => {
    await mount()
    await click(en.add)
    await fill('python-package-name', 'demo')
    await fill('python-package-version', '>=1,<2')
    await fill('python-package-extras', 'extra')
    await fill('python-package-marker', 'sys_platform == "win32"')
    await click('Save')
    await click(en.save)
    expect(api.saveProjectPythonRequirements).toHaveBeenCalledWith(
      'p1',
      [state().requirements[0]!.requirement, 'demo[extra]>=1,<2; sys_platform == "win32"'],
      'r1'
    )
    expect(api.startProjectPythonTask).not.toHaveBeenCalled()
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
    await click('Edit')
    await fill('python-package-version', '==0.27.0')
    await click('Save')
    api.getProjectPythonPackages.mockResolvedValue(state({ revision: 'external' }))
    await click('Refresh')
    await click(en.save)
    expect(api.saveProjectPythonRequirements).toHaveBeenCalledWith(
      'p1',
      ['httpx[socks]==0.27.0; python_version >= "3.11"'],
      'r1'
    )
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
