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
  updatePluginProjectMetadata: vi.fn(),
  precheckPluginProject: vi.fn(),
  getPluginProjectPublishStatus: vi.fn(),
  getPluginProjectPublishTask: vi.fn(),
  startPluginProjectPublish: vi.fn(),
  cancelPluginProjectPublishTask: vi.fn(),
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
  updatePluginProjectMetadata: api.updatePluginProjectMetadata,
  precheckPluginProject: api.precheckPluginProject,
  getPluginProjectPublishStatus: api.getPluginProjectPublishStatus,
  getPluginProjectPublishTask: api.getPluginProjectPublishTask,
  startPluginProjectPublish: api.startPluginProjectPublish,
  cancelPluginProjectPublishTask: api.cancelPluginProjectPublishTask,
}))
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
  loaded: true,
  running: false,
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
          common: {
            cancel: 'Cancel',
            close: 'Close',
            noData: 'No data',
            loading: 'Loading',
            edit: 'Edit',
            save: 'Save',
          },
          status: { running: 'Running', stopped: 'Stopped' },
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
  // Never having published is the normal state the publish tab expects.
  api.getPluginProjectPublishTask.mockRejectedValue({
    response: { data: { detail: { code: 'PUBLISH_TASK_NOT_FOUND' } } },
  })
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

  it('renders the server list with live metadata and plugin status', async () => {
    api.listPluginProjects.mockResolvedValue([
      { ...listed, running: true },
      {
        ...listed,
        project_id: 'p2',
        source_dir: 'F:/Gone/old_plugin',
        status: 'missing',
        plugin_id: null,
        name: null,
        version: null,
        loaded: false,
      },
      { ...listed, project_id: 'p3', source_dir: 'G:/Stopped/plugin' },
      { ...listed, project_id: 'p4', source_dir: 'G:/Conflict/plugin', loaded: false },
    ])
    const container = await mount()
    const cards = container.querySelectorAll('.authoring-project-card')
    expect(cards).toHaveLength(4)
    expect(cards[0]!.textContent).toContain('v0.2.0')
    expect(cards[0]!.textContent).toContain('Running')
    expect(cards[1]!.textContent).toContain('old_plugin')
    expect(cards[1]!.textContent).toContain(en.statusMissing)
    expect(cards[2]!.textContent).toContain('Stopped')
    expect(cards[3]!.textContent).toContain(en.notLoaded)
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
      path: '/workbench',
      query: { project: created.project_id },
    })
    expect(localStorage.length).toBe(0)
  })

  it('opens the folder and the loaded plugin from the project details', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    await mount()
    expect(document.body.textContent).toContain('Stopped')
    await click('Open folder')
    expect(api.openProjectDirectory).toHaveBeenCalledWith(listed.source_dir)
    await click(en.openPluginPage)
    expect(api.push).toHaveBeenCalledWith('/plugins/hello_world')
  })

  it('shows every project field on one page with the status beside the title', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    const container = await mount()
    const tabs = [...container.querySelectorAll('.authoring-tabs > .el-tabs__header [role="tab"]')]
    expect(tabs.map((tab) => tab.textContent)).toEqual([en.tabOverview, en.precheckTab, en.publishTab])
    expect(tabs[0]?.getAttribute('aria-selected')).toBe('true')
    const header = container.querySelector('.authoring-header')!
    expect(header.textContent).toContain(listed.name)
    expect(header.textContent).toContain('Stopped')
    expect(header.textContent).toContain(listed.plugin_type)
    const panel = container.querySelector('.authoring-details-panel')!
    expect(panel.textContent).toContain(listed.version)
    expect(panel.textContent).toContain(listed.source_dir)
    expect(panel.textContent).toContain(listed.entry)
    expect(api.listPluginProjects).toHaveBeenCalledOnce()
  })

  it('reads the publish status only when its tab opens and lists the steps', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue({
      plugin_id: listed.plugin_id,
      version: listed.version,
      repository: {
        git_available: true,
        is_repository: true,
        tag: 'v0.2.0',
        origin_url: 'https://github.com/owner/hello_world.git',
        github_repository: 'owner/hello_world',
        branch: 'main',
        upstream: 'origin/main',
        head: 'abc',
        head_pushed: true,
        worktree_clean: true,
        tag_exists: false,
        tag_at_head: null,
      },
      actions: { 'ruff.toml': 'CURRENT', '.github/workflows/release.yml': 'UPGRADE' },
      remote: {
        rate_limited: false,
        visibility: 'public',
        verify: 'success',
        release: { state: 'missing', missing_assets: [] },
      },
    })
    const container = await mount()
    // The tab is lazy: opening a project must not call GitHub.
    expect(api.getPluginProjectPublishStatus).not.toHaveBeenCalled()
    const tabs = [...container.querySelectorAll('.authoring-tabs > .el-tabs__header [role="tab"]')]
    ;(tabs[2] as HTMLElement).click()
    await flush()
    expect(api.getPluginProjectPublishStatus).toHaveBeenCalledWith('p1')
    const pane = container.querySelector('.authoring-publish')!
    expect(pane.textContent).toContain(en.publishStepRepository)
    expect(pane.textContent).toContain('owner/hello_world is public and HEAD is pushed.')
    expect(pane.textContent).toContain(en.publishVerifyPassed)
    expect(pane.textContent).toContain('Release v0.2.0 does not exist yet.')
    // Submission and Market version are not read in this phase.
    expect(pane.textContent).toContain(en.publishStatusLater)
    expect(pane.textContent).toContain('.github/workflows/release.yml')
    expect(pane.textContent).toContain('n.e.k.o_plugin_hello_world')
  })

  it('offers the full publish only after the second confirmation', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue({
      plugin_id: listed.plugin_id,
      version: listed.version,
      repository: {
        git_available: true,
        is_repository: true,
        tag: 'v0.2.0',
        origin_url: 'https://github.com/owner/hello_world.git',
        github_repository: 'owner/hello_world',
        branch: 'main',
        upstream: 'origin/main',
        head: 'abc',
        head_pushed: true,
        worktree_clean: true,
        tag_exists: false,
        tag_at_head: null,
      },
      actions: {},
      remote: {
        rate_limited: false,
        visibility: 'public',
        verify: 'success',
        release: { state: 'missing', missing_assets: [] },
      },
    })
    api.startPluginProjectPublish.mockResolvedValue({
      task_id: 't1',
      project_id: 'p1',
      mode: 'github_and_market',
      status: 'queued',
      message: '',
      release_url: '',
      log: [],
      started_at: null,
      updated_at: 1,
      finished_at: null,
    })
    const container = await mount()
    const tabs = [...container.querySelectorAll('.authoring-tabs > .el-tabs__header [role="tab"]')]
    ;(tabs[2] as HTMLElement).click()
    await flush()
    // No release yet, so the full flow is offered, not the Market-only one.
    await click(en.publishRunGithubMarket)
    await settle()
    expect(api.startPluginProjectPublish).not.toHaveBeenCalled()
    await click(en.publishConfirmTitle, { last: true })
    await settle()
    expect(api.startPluginProjectPublish).toHaveBeenCalledWith('p1', 'github_and_market')
    expect(container.querySelector('.authoring-publish-task')?.textContent).toContain(
      en.publishTaskQueued
    )
  })

  it('keeps the publish buttons hidden until the repository is ready', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue({
      plugin_id: listed.plugin_id,
      version: listed.version,
      repository: {
        git_available: true,
        is_repository: true,
        tag: 'v0.2.0',
        origin_url: null,
        github_repository: null,
        branch: 'main',
        upstream: null,
        head: 'abc',
        head_pushed: false,
        worktree_clean: true,
        tag_exists: false,
        tag_at_head: null,
      },
      actions: {},
      remote: null,
    })
    await mount()
    const tabs = [
      ...document.querySelectorAll('.authoring-tabs > .el-tabs__header [role="tab"]'),
    ]
    ;(tabs[2] as HTMLElement).click()
    await flush()
    expect(buttons(en.publishRunGithubMarket)).toHaveLength(0)
    expect(buttons(en.publishRunMarket)).toHaveLength(0)
  })

  it('runs the pre-release check and renders the CI gate report', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.precheckPluginProject.mockResolvedValue({
      plugin_id: listed.plugin_id,
      version: listed.version,
      plugin_dir: listed.source_dir,
      passed: false,
      ruff_version: '0.12.4',
      environment_issues: [['warning', 'git working tree has uncommitted changes']],
      stages: [
        { name: 'lint', passed: true, output: '' },
        { name: 'sync', passed: false, output: 'vendor drift' },
      ],
    })
    const container = await mount()
    const tabs = [...container.querySelectorAll('.authoring-tabs > .el-tabs__header [role="tab"]')]
    ;(tabs[1] as HTMLElement).click()
    await flush()
    await click(en.precheckRun)
    expect(api.precheckPluginProject).toHaveBeenCalledWith('p1')
    const pane = container.querySelector('.authoring-precheck')!
    expect(pane.textContent).toContain(en.precheckFailed)
    expect(pane.textContent).toContain(en.precheckStageSync)
    expect(pane.textContent).toContain('vendor drift')
    // The gate after the failing one is reported as skipped, not run.
    expect(pane.textContent).toContain(en.precheckSkipped)
    expect(pane.textContent).toContain(en.precheckEnv)
    expect(pane.textContent).toContain('git working tree has uncommitted changes')
  })

  it('saves editable metadata and refreshes the project details', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    const updated = {
      ...listed,
      name: 'Renamed plugin',
      version: '0.3.0',
      author: 'New author',
      description: 'New description',
    }
    api.updatePluginProjectMetadata.mockResolvedValue(updated)
    const container = await mount()
    await click('Edit')
    await fill('project-metadata-name', updated.name)
    await fill('project-metadata-version', updated.version)
    await fill('project-metadata-author', updated.author)
    await fill('project-metadata-description', updated.description)
    await click('Save')
    expect(api.updatePluginProjectMetadata).toHaveBeenCalledExactlyOnceWith('p1', {
      name: updated.name,
      version: updated.version,
      author: updated.author,
      description: updated.description,
    })
    expect(container.querySelector('.authoring-header h2')?.textContent).toBe(updated.name)
    expect(container.querySelector('.authoring-details-panel')?.textContent).toContain(
      updated.description
    )
    expect(container.querySelector('#project-metadata-name')).toBeNull()
  })

  it('discards edits on cancel and retains them after a failed save', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.updatePluginProjectMetadata.mockRejectedValue(new Error('Write failed'))
    const container = await mount()
    await click('Edit')
    await fill('project-metadata-name', 'Discarded name')
    await click('Cancel')
    expect(api.updatePluginProjectMetadata).not.toHaveBeenCalled()
    await click('Edit')
    expect((container.querySelector('#project-metadata-name') as HTMLInputElement).value).toBe(
      listed.name
    )
    await fill('project-metadata-name', 'Retained draft')
    await click('Save')
    expect(container.querySelector('.authoring-details-panel')?.textContent).toContain(
      'Write failed'
    )
    expect((container.querySelector('#project-metadata-name') as HTMLInputElement).value).toBe(
      'Retained draft'
    )
    expect(container.querySelector('.authoring-header h2')?.textContent).toBe(listed.name)
  })

  it('offers no plugin page for a project the runtime did not load', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([{ ...listed, loaded: false }])
    await mount()
    expect(document.body.textContent).toContain(en.notLoaded)
    expect(buttons(en.openPluginPage)).toHaveLength(0)
  })

  it('localizes plugin ID conflicts when adding a project', async () => {
    api.pickProjectDirectory.mockResolvedValueOnce('D:/Existing/plugin')
    api.registerPluginProject.mockRejectedValue({
      response: { data: { detail: { code: 'PROJECT_PLUGIN_ID_CONFLICT', message: 'raw' } } },
    })
    await mount()
    await click(en.addExisting)
    await settle()
    expect(document.body.textContent).toContain(en.projectPluginIdConflict)
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
    expect(api.push).toHaveBeenCalledWith({ path: '/workbench' })
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

  it('removes a project only after confirming that it stops and files are kept', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([{ ...listed, running: true }])
    await mount()
    expect(document.body.textContent).toContain('Running')
    await click(en.removeProject)
    await settle()
    const confirmation = document.querySelector('.el-message-box')?.textContent
    expect(confirmation).toContain('A running plugin is stopped')
    expect(confirmation).toContain('are not deleted')
    expect(api.removePluginProject).not.toHaveBeenCalled()
    api.listPluginProjects.mockResolvedValue([])
    await click(en.removeProject, { last: true })
    await settle()
    expect(api.removePluginProject).toHaveBeenCalledWith('p1')
    expect(api.push).toHaveBeenCalledWith({ path: '/workbench' })
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
