// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, markRaw, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import PluginAuthoring from './PluginAuthoring.vue'
import { authoringMessages } from '@/i18n/authoring'
import type { AuthoringProject, CreatedPluginProject } from '@/api/pluginProjects'

const api = vi.hoisted(() => ({
  listPluginProjects: vi.fn(),
  previewPluginProject: vi.fn(),
  createPluginProject: vi.fn(),
  registerPluginProject: vi.fn(),
  removePluginProject: vi.fn(),
  pickProjectDirectory: vi.fn(),
  openProjectDirectory: vi.fn(),
  mountDevelopment: vi.fn(),
  push: vi.fn(),
  route: { query: {} as Record<string, string> },
}))
vi.mock('@/api/pluginProjects', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/pluginProjects')>()),
  listPluginProjects: api.listPluginProjects,
  previewPluginProject: api.previewPluginProject,
  createPluginProject: api.createPluginProject,
  registerPluginProject: api.registerPluginProject,
  removePluginProject: api.removePluginProject,
  pickProjectDirectory: api.pickProjectDirectory,
  openProjectDirectory: api.openProjectDirectory,
}))
vi.mock('@/api/development', () => ({ mountDevelopment: api.mountDevelopment }))
vi.mock('vue-router', () => ({
  useRoute: () => api.route,
  useRouter: () => ({ push: api.push }),
}))

const en = authoringMessages['en-US']
const created: CreatedPluginProject = {
  project_id: 'p1',
  plugin_id: 'hello_world',
  name: 'Real project',
  description: 'Created on disk',
  author: 'Developer',
  plugin_type: 'plugin',
  version: '0.1.0',
  entry: 'plugin.plugins.hello_world:HelloWorldPlugin',
  project_dir: 'E:/Projects/hello_world',
  files: ['plugin.toml', '__init__.py', '.git/'],
}
const listed: AuthoringProject = {
  project_id: 'p1',
  source_dir: created.project_dir,
  added_at: '2026-11-03T00:00:00+00:00',
  status: 'ok',
  error: null,
  plugin_id: created.plugin_id,
  name: created.name,
  description: created.description,
  author: created.author,
  plugin_type: 'plugin',
  version: '0.2.0',
  entry: created.entry,
  development: 'unmounted',
}
const NativeMutationObserver = globalThis.MutationObserver
let cleanup: (() => void) | undefined

async function flush() {
  for (let i = 0; i < 10; i++) {
    await Promise.resolve()
    await nextTick()
  }
}

async function mount() {
  const container = document.createElement('div')
  document.body.append(container)
  const app = createApp(PluginAuthoring)
  app.use(ElementPlus)
  app.use(
    createI18n({
      legacy: false,
      locale: 'en-US',
      messages: {
        'en-US': {
          authoring: en,
          common: { cancel: 'Cancel', close: 'Close', noData: 'No data', loading: 'Loading' },
        },
      },
    })
  )
  app.mount(container)
  cleanup = () => {
    app.unmount()
    container.remove()
  }
  await flush()
  return container
}

function buttons(text: string) {
  return [...document.querySelectorAll('button')].filter(
    (element) => element.textContent?.trim() === text
  )
}

async function click(text: string, { last = false } = {}) {
  const found = buttons(text)
  const button = last ? found.at(-1) : found[0]
  expect(button, `Missing button: ${text}`).toBeTruthy()
  button!.click()
  await flush()
}

async function fill(id: string, value: string) {
  const input = document.getElementById(id) as HTMLInputElement
  expect(input).toBeTruthy()
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await flush()
}

async function settle() {
  // Element Plus message boxes open and close through transitions and timers.
  for (let i = 0; i < 5; i++) {
    await new Promise((resolve) => setTimeout(resolve, 20))
    await flush()
  }
}

beforeEach(() => {
  vi.resetAllMocks()
  api.route.query = {}
  vi.stubGlobal(
    'MutationObserver',
    class extends NativeMutationObserver {
      constructor(callback: MutationCallback) {
        super(callback)
        markRaw(this)
      }
    }
  )
  localStorage.clear()
  api.listPluginProjects.mockResolvedValue([])
  api.previewPluginProject.mockResolvedValue({ ...created, project_id: undefined })
  api.createPluginProject.mockResolvedValue(created)
  api.pickProjectDirectory.mockResolvedValue('E:/Projects')
  api.mountDevelopment.mockResolvedValue({})
})

afterEach(() => {
  cleanup?.()
  cleanup = undefined
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('authoring workbench backed by the server project list', () => {
  it('keeps all locale keys consistent and free of encoding damage', () => {
    const keys = Object.keys(en).sort()
    for (const messages of Object.values(authoringMessages)) {
      expect(Object.keys(messages).sort()).toEqual(keys)
      // A lossy pipe once turned every non-ASCII character into "?".
      expect(Object.values(messages).filter((value) => /\?\?|\uFFFD/.test(value))).toEqual([])
    }
  })

  it('renders the server list with live metadata and status', async () => {
    api.listPluginProjects.mockResolvedValue([
      { ...listed, development: 'mounted' },
      {
        ...listed,
        project_id: 'p2',
        source_dir: 'F:/Gone/old_plugin',
        status: 'missing',
        plugin_id: null,
        name: null,
        version: null,
        development: 'unmounted',
      },
      { ...listed, project_id: 'p3', source_dir: 'G:/Off/plugin', development: 'mode_disabled' },
    ])
    const container = await mount()
    const cards = container.querySelectorAll('.authoring-project-card')
    expect(cards).toHaveLength(3)
    expect(cards[0]!.textContent).toContain('v0.2.0')
    expect(cards[0]!.textContent).toContain(en.mounted)
    expect(cards[1]!.textContent).toContain('old_plugin')
    expect(cards[1]!.textContent).toContain(en.statusMissing)
    expect(cards[2]!.textContent).toContain(en.mountedModeDisabled)
    expect(localStorage.length).toBe(0)
  })

  it('creates through the API, closes the dialog and opens the new project', async () => {
    await mount()
    api.listPluginProjects.mockResolvedValue([listed])
    await click('New plugin')
    await click('Choose folder')
    await fill('authoring-name', created.name)
    await fill('authoring-id', created.plugin_id)
    await click('Preview project')
    expect(api.previewPluginProject).toHaveBeenCalledWith(
      expect.objectContaining({ parent_dir: 'E:/Projects', plugin_id: created.plugin_id })
    )
    await click('Create plugin')
    expect(api.createPluginProject).toHaveBeenCalledOnce()
    expect(api.listPluginProjects).toHaveBeenCalledTimes(2)
    expect(api.push).toHaveBeenCalledWith({
      path: '/development/workbench',
      query: { project: created.project_id },
    })
    expect(buttons('Mount in development mode')).toHaveLength(0)
    expect(localStorage.length).toBe(0)
  })

  it('opens and mounts from the project details, then reloads the state', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    await mount()
    expect(document.body.textContent).toContain(en.notMounted)
    await click('Open folder')
    expect(api.openProjectDirectory).toHaveBeenCalledWith(listed.source_dir)
    api.listPluginProjects.mockResolvedValue([{ ...listed, development: 'mounted' }])
    await click('Mount in development mode')
    expect(api.mountDevelopment).toHaveBeenCalledWith(listed.source_dir)
    expect(document.body.textContent).toContain(en.mounted)
    expect(buttons('Mount in development mode')).toHaveLength(0)
    expect(buttons(en.goDevelopment)).toHaveLength(1)
  })

  it('localizes development-mode mount errors carried in the response header', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.mountDevelopment.mockRejectedValue({
      response: {
        data: { detail: 'Plugin ID already registered in runtime' },
        headers: { 'x-error-code': 'DEVELOPMENT_CONFLICT' },
      },
    })
    await mount()
    await click('Mount in development mode')
    await settle()
    expect(document.body.textContent).toContain(en.developmentConflict)
  })

  it('offers development mode, not mounting, when registered while the mode is off', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([{ ...listed, development: 'mode_disabled' }])
    await mount()
    expect(document.body.textContent).toContain(en.mountedModeDisabled)
    expect(buttons('Mount in development mode')).toHaveLength(0)
    expect(buttons(en.goDevelopment)).toHaveLength(1)
  })

  it.each([
    ['GIT_INIT_FAILED', en.gitInitFailed],
    ['PROJECT_REGISTER_FAILED', en.projectRegisterFailed],
  ])('shows a localized message for %s', async (code, message) => {
    api.createPluginProject.mockRejectedValue({
      response: { data: { detail: { code, message: 'raw diagnostic' } } },
    })
    await mount()
    await click('New plugin')
    await fill('authoring-parent', 'E:/Projects')
    await fill('authoring-name', created.name)
    await fill('authoring-id', created.plugin_id)
    await click('Preview project')
    await click('Create plugin')
    expect(document.body.textContent).toContain(message)
    expect(document.body.textContent).not.toContain('raw diagnostic')
  })

  it('adds an existing folder, and does nothing when the picker is cancelled', async () => {
    await mount()
    api.pickProjectDirectory.mockResolvedValueOnce(null)
    await click(en.addExisting)
    expect(api.registerPluginProject).not.toHaveBeenCalled()
    api.pickProjectDirectory.mockResolvedValueOnce('D:/Existing/plugin')
    api.registerPluginProject.mockResolvedValue(listed)
    api.listPluginProjects.mockResolvedValue([listed])
    await click(en.addExisting)
    expect(api.registerPluginProject).toHaveBeenCalledWith('D:/Existing/plugin')
    expect(document.querySelectorAll('.authoring-project-card')).toHaveLength(1)
  })

  it('reports list loading failures without claiming there are no projects', async () => {
    api.listPluginProjects.mockRejectedValue({
      response: { data: { detail: { code: 'PROJECT_STORE_UNREADABLE' } } },
    })
    await mount()
    expect(document.body.textContent).toContain(en.projectStoreUnreadable)
    expect(document.body.textContent).not.toContain(en.noProjects)
  })

  it('shows a loading state, not an empty list, until the first load finishes', async () => {
    let resolve!: (value: AuthoringProject[]) => void
    api.listPluginProjects.mockReturnValue(new Promise((done) => (resolve = done)))
    const container = await mount()
    expect(container.querySelector('[role="status"]')).toBeTruthy()
    expect(document.body.textContent).not.toContain(en.noProjects)
    resolve([])
    await flush()
    expect(container.querySelector('[role="status"]')).toBeNull()
    expect(document.body.textContent).toContain(en.noProjects)
  })

  it('explains when the requested project is not in the workbench', async () => {
    api.route.query = { project: 'gone' }
    api.listPluginProjects.mockResolvedValue([listed])
    const container = await mount()
    expect(document.body.textContent).toContain(en.projectNotFound)
    expect(container.querySelectorAll('.authoring-project-card')).toHaveLength(0)
    await click(en.back)
    expect(api.push).toHaveBeenCalledWith({ path: '/development/workbench' })
  })

  it('keeps the raw diagnostic of an invalid project behind technical details', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([
      { ...listed, status: 'invalid', error: 'Invalid TOML at line 3', name: null },
    ])
    const container = await mount()
    const details = container.querySelector('details.authoring-diagnostic')!
    expect(details.querySelector('summary')!.textContent).toBe(en.technicalDetails)
    expect(details.textContent).toContain('Invalid TOML at line 3')
    expect(document.body.textContent).toContain(en.statusInvalidHint)
  })

  it('removes a mounted project only after confirming that files and mount are kept', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([{ ...listed, development: 'mounted' }])
    await mount()
    expect(document.body.textContent).toContain(en.mounted)
    expect(buttons(en.mountDevelopment)).toHaveLength(0)
    await click(en.removeProject)
    await settle()
    expect(document.querySelector('.el-message-box')?.textContent).toContain(
      'development mode registration is kept'
    )
    expect(api.removePluginProject).not.toHaveBeenCalled()
    api.listPluginProjects.mockResolvedValue([])
    await click(en.removeProject, { last: true })
    await settle()
    expect(api.removePluginProject).toHaveBeenCalledWith('p1')
    expect(api.mountDevelopment).not.toHaveBeenCalled()
    expect(api.push).toHaveBeenCalledWith({ path: '/development/workbench' })
  })

  it('keeps the project when removal is cancelled', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    await mount()
    await click(en.removeProject)
    await settle()
    await click('Cancel', { last: true })
    await settle()
    expect(api.removePluginProject).not.toHaveBeenCalled()
  })
})
