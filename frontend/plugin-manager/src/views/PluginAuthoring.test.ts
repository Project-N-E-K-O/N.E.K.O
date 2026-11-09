// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, markRaw, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import PluginAuthoring from './PluginAuthoring.vue'
import { authoringMessages } from '@/i18n/authoring'
import type {
  AuthoringProject,
  CreatedPluginProject,
  PublishStatus,
  PublishTask,
  Toolchain,
} from '@/api/pluginProjects'
import { RELEASE_WORKFLOW_PATH } from '@/api/pluginProjects'

const api = vi.hoisted(() => ({
  listPluginProjects: vi.fn(),
  previewPluginProject: vi.fn(),
  createPluginProject: vi.fn(),
  createPluginProjectRepo: vi.fn(),
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
  getPluginToolchain: vi.fn(),
  buildPluginCli: vi.fn(),
  downloadPluginPackage: vi.fn(),
  fetchMarketPluginVersions: vi.fn(),
  push: vi.fn(),
  route: { query: {} as Record<string, string> },
}))
vi.mock('@/api/pluginCli', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/pluginCli')>()),
  buildPluginCli: api.buildPluginCli,
  downloadPluginPackage: api.downloadPluginPackage,
}))
vi.mock('@/api/market', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/market')>()),
  fetchMarketPluginVersions: api.fetchMarketPluginVersions,
}))
vi.mock('@/api/pluginProjects', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/pluginProjects')>()),
  listPluginProjects: api.listPluginProjects,
  previewPluginProject: api.previewPluginProject,
  createPluginProject: api.createPluginProject,
  createPluginProjectRepo: api.createPluginProjectRepo,
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
  getPluginToolchain: api.getPluginToolchain,
}))
vi.mock('vue-router', () => ({
  useRoute: () => api.route,
  useRouter: () => ({ push: api.push }),
}))
// The real submission panel is mounted, so the state it reports reaches the
// publish steps; only its Market login and network calls are replaced.
const market = vi.hoisted(() => ({
  listMarketSubmissions: vi.fn(),
  listMarketZones: vi.fn(),
  authenticated: false,
}))
vi.mock('@/api/marketSubmissions', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/marketSubmissions')>()),
  listMarketSubmissions: market.listMarketSubmissions,
  listMarketZones: market.listMarketZones,
}))
vi.mock('@/composables/useMarketAuth', async () => {
  const { computed, ref } = await import('vue')
  return {
    useMarketAuth: () => {
      const marketAuth = ref<{ authenticated: boolean; market_web_url?: string }>({
        authenticated: false,
      })
      return {
        marketAuth,
        marketAuthBusy: ref(false),
        marketAuthDisplayName: computed(() => 'Author'),
        marketAuthStateMessageKey: computed(() => ''),
        loadMarketAuthStatus: async () => {
          marketAuth.value = {
            authenticated: market.authenticated,
            market_web_url: 'https://market.example',
          }
        },
        startMarketLogin: vi.fn(),
        stopMarketAuthPolling: vi.fn(),
      }
    },
  }
})

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
  problem: null,
}
/** A project whose local and remote state passes every publish gate, without a release. */
function publishStatus(overrides: {
  repository?: Partial<PublishStatus['repository']>
  remote?: Partial<NonNullable<PublishStatus['remote']>> | null
  actions?: PublishStatus['actions']
  workspace?: PublishStatus['workspace']
} = {}): PublishStatus {
  return {
    plugin_id: listed.plugin_id!,
    version: listed.version!,
    repository: {
      git_available: true,
      is_repository: true,
      tag: 'v0.2.0',
      origin_url: 'https://github.com/owner/hello_world.git',
      github_repository: 'owner/hello_world',
      branch: 'main',
      upstream: 'origin/main',
      head: 'abc',
      detached: false,
      head_pushed: true,
      worktree_clean: true,
      dirty_paths: [],
      tag_exists: false,
      tag_at_head: null,
      ...overrides.repository,
    },
    actions: overrides.actions ?? { [RELEASE_WORKFLOW_PATH]: 'CURRENT' },
    remote:
      overrides.remote === null
        ? null
        : {
            rate_limited: false,
            reachable: true,
            visibility: 'public',
            verify: 'success',
            release: { state: 'missing', missing_assets: [], workflow: 'unknown' as const, ready: false },
            tag: 'missing',
            ...overrides.remote,
          },
    workspace: overrides.workspace ?? null,
  }
}
const publishTask = (overrides: Partial<PublishTask> = {}): PublishTask => ({
  task_id: 't1',
  project_id: 'p1',
  mode: 'github_and_market',
  status: 'queued',
  message: '',
  release_url: '',
  market_status: null,
  market_candidate_id: null,
  log: [],
  started_at: null,
  updated_at: 1,
  finished_at: null,
  ...overrides,
})
/** Sign in to a Market account whose submission of this repository was approved at `commit`. */
function approvedSubmission(commit = 'abc') {
  market.authenticated = true
  market.listMarketSubmissions.mockResolvedValue({
    items: [
      {
        id: 152,
        status: 'closed',
        decision: 'approved',
        terminal_kind: 'approved',
        plugin_id: 7,
        current_snapshot: {
          id: 31,
          repo_url: 'https://github.com/Owner/hello_world',
          repo_owner: 'Owner',
          repo_name: 'hello_world',
          plugin_name: 'Hello',
          plugin_slug: 'hello_world',
          zone_slug: 'game',
          tags: ['工具'],
          submitted_ref: commit,
          resolved_commit: commit,
        },
      },
    ],
    total: 1,
    page: 1,
    page_size: 100,
    total_pages: 1,
  })
}
const taskNotFound = { response: { data: { detail: { code: 'PUBLISH_TASK_NOT_FOUND' } } } }
const readyToolchain = (overrides: Partial<Toolchain> = {}): Toolchain => ({
  git: { installed: true, version: '2.55.0', user_name: 'Dev', user_email: 'dev@example.com' },
  uv: { installed: true, version: '0.12.18' },
  gh: { installed: true, version: '2.101.0', authenticated: true, login: 'octocat' },
  source_checkout: true,
  ...overrides,
})
const NativeMutationObserver = globalThis.MutationObserver
let cleanup: (() => void) | undefined

async function flush() {
  for (let i = 0; i < 10; i++) {
    await Promise.resolve()
    await nextTick()
  }
}

let switchLocale: (language: 'en-US' | 'zh-CN') => void

async function mount() {
  const container = document.createElement('div')
  document.body.append(container)
  const app = createApp(PluginAuthoring)
  app.use(ElementPlus)
  const i18n = createI18n({
      legacy: false,
      locale: 'en-US',
      messages: {
        'en-US': {
          authoring: en,
          plugins: { typePlugin: 'Plugin', typeAdapter: 'Adapter' },
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
        'zh-CN': {
          authoring: authoringMessages['zh-CN'],
          plugins: { typePlugin: '插件', typeAdapter: '适配器' },
          common: { cancel: '取消', close: '关闭', edit: '编辑', save: '保存' },
          status: { running: '运行中', stopped: '已停止' },
        },
      },
    })
  switchLocale = (language) => { i18n.global.locale.value = language }
  app.use(i18n)
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

async function openTab(container: HTMLElement, index: number) {
  const tabs = [...container.querySelectorAll('.authoring-tabs > .el-tabs__header [role="tab"]')]
  ;(tabs[index] as HTMLElement).click()
  await flush()
}

async function openPublish(container: HTMLElement) {
  await openTab(container, 1)
  return container.querySelector('.authoring-publish')!
}

function messageBox() {
  return document.querySelector('.el-message-box')
}

/** The five publish steps, without the step-0 toolchain page. */
function steps() {
  return [...document.querySelectorAll('.authoring-publish-step:not([data-step="toolchain"])')]
}

function page(id: string) {
  return document.querySelector(`.authoring-publish-step[data-step="${id}"]`) as HTMLElement
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
  api.getPluginProjectPublishTask.mockRejectedValue(taskNotFound)
  // The Market's version list is unread unless a test answers it.
  api.fetchMarketPluginVersions.mockResolvedValue(null)
  // A machine with every publishing tool, unless a test says otherwise.
  api.getPluginToolchain.mockResolvedValue(readyToolchain())
  market.authenticated = false
  market.listMarketSubmissions.mockResolvedValue({ items: [] })
  market.listMarketZones.mockResolvedValue([{ id: 'game', name: 'Game' }])
})

afterEach(() => {
  vi.useRealTimers()
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
    await click(en.viewLogs)
    expect(api.push).toHaveBeenCalledWith('/logs/hello_world')
  })

  it.each([
    ['load_failed', "No module named 'x'", en.statusLoadFailed, en.loadFailedHint],
    ['reload_failed', null, en.statusReloadFailed, en.reloadFailedHint],
    ['startup_degraded', 'startup failed', en.statusStartupDegraded, en.startupDegradedHint],
  ] as const)('shows the runtime problem %s instead of a plain status', async (problem, error, tag, hint) => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([
      { ...listed, problem, error, running: problem === 'startup_degraded' },
    ])
    const container = await mount()
    expect(container.querySelector('.authoring-header .el-tag')!.textContent).toContain(tag)
    expect(document.body.textContent).toContain(hint)
    const details = container.querySelector('details.authoring-diagnostic')
    if (error) expect(details!.textContent).toContain(error)
    else expect(details).toBeNull()
    expect(buttons(en.viewLogs)).toHaveLength(1)
  })

  it('shows every project field on one page with the status beside the title', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    const container = await mount()
    const tabs = [...container.querySelectorAll('.authoring-tabs > .el-tabs__header [role="tab"]')]
    expect(tabs.map((tab) => tab.textContent)).toEqual([en.tabOverview, en.publishTab])
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
    api.getPluginProjectPublishStatus.mockResolvedValue(
      publishStatus({
        actions: { [RELEASE_WORKFLOW_PATH]: 'CURRENT', '.github/workflows/verify.yml': 'UPGRADE' },
      })
    )
    const container = await mount()
    // The tab is lazy: opening a project must not call GitHub.
    expect(api.getPluginProjectPublishStatus).not.toHaveBeenCalled()
    const pane = await openPublish(container)
    expect(api.getPluginProjectPublishStatus).toHaveBeenCalledExactlyOnceWith('p1', false)
    const [repository, verify, submission, release, marketVersion] = steps()
    expect(repository!.textContent).toContain('owner/hello_world is public and HEAD is pushed.')
    expect(verify!.textContent).toContain(en.publishVerifyPassed)
    // The submission comes before the Release: the Market approves without one.
    expect(submission!.className).toContain('authoring-publish-step--current')
    expect(submission!.textContent).toContain(en.publishStatusTodo)
    expect(submission!.textContent).toContain(en.submissionLogin)
    expect(release!.textContent).toContain(en.publishStatusWaiting)
    expect(release!.textContent).toContain(en.publishReleaseAfterApproval)
    expect(marketVersion!.textContent).toContain(en.marketVersionStepDetailLater)
    // Later steps can be previewed; they point back at the step to finish first.
    expect(release!.textContent).toContain(
      'Step 3 (Market submission) comes first. You can look around here now and come back later.'
    )
    expect(repository!.textContent).not.toContain('comes first')
    // The buttons stay clickable; the page explains what is missing on click.
    expect(buttons(en.publishRunGithubMarket)).toHaveLength(1)
    expect(buttons(en.publishRunGithub)).toHaveLength(1)
    expect(buttons(en.publishRefresh)).toHaveLength(1)
    expect(pane.textContent).toContain('.github/workflows/verify.yml')
    expect(pane.textContent).toContain('n.e.k.o_plugin_hello_world')
    // The local check now lives in the verify step, with its CI note up front.
    expect(verify!.textContent).toContain(en.precheckRun)
    expect(verify!.textContent).toContain(en.precheckCiNote)
  })

  it('carries the real submission panel state into the submission and Market steps', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(
      publishStatus({ remote: { release: { state: 'published', missing_assets: [], workflow: 'success' as const, ready: true } } })
    )
    approvedSubmission()
    const container = await mount()
    await openPublish(container)
    expect(market.listMarketSubmissions).toHaveBeenCalledWith('owner/hello_world')
    const [, , submission, release, marketVersion] = steps()
    expect(submission!.textContent).toContain(en.publishStatusDone)
    expect(submission!.textContent).toContain(en.submissionStateApproved)
    expect(release!.textContent).toContain(en.publishStatusDone)
    expect(marketVersion!.textContent).toContain(en.publishStatusTodo)
    expect(marketVersion!.textContent).toContain('Release v0.2.0 is ready; notify the Market to list it.')
    expect(buttons(en.publishRunMarket)).toHaveLength(1)
  })

  it('warns before a Release from another commit than the approved one', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(publishStatus())
    approvedSubmission('def0000')
    const container = await mount()
    await openPublish(container)
    const release = steps()[3]!
    expect(release.className).toContain('authoring-publish-step--current')
    expect(release.textContent).toContain('Release v0.2.0 does not exist yet.')
    expect(release.textContent).toContain('HEAD abc is not the approved commit def0000.')
  })

  it.each([
    ['pending_review', 'Sent to Market human review (candidate #12).'],
    ['rejected', 'The Market rejected this release (candidate #12); publish a new version.'],
    ['published', en.marketVersionListed],
  ] as const)('shows the Market answer %s from the last publish task', async (marketStatus, detail) => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(
      publishStatus({ remote: { release: { state: 'published', missing_assets: [], workflow: 'success' as const, ready: true } } })
    )
    api.getPluginProjectPublishTask.mockResolvedValue(
      publishTask({
        mode: 'market_notify',
        status: marketStatus === 'rejected' ? 'failed' : 'succeeded',
        release_url: 'https://github.com/owner/hello_world/releases/tag/v0.2.0',
        market_status: marketStatus,
        market_candidate_id: 12,
        finished_at: 1_700_000_000,
      })
    )
    approvedSubmission()
    const container = await mount()
    await openPublish(container)
    expect(steps()[4]!.textContent).toContain(detail)
    // The Market already answered: notifying again is a soft blocker that asks first.
    await click(en.publishRunMarket)
    await settle()
    expect(messageBox()?.textContent).toContain(en.publishSoftTitle)
    expect(messageBox()?.textContent).toContain(en.publishContinueAnyway)
    expect(api.startPluginProjectPublish).not.toHaveBeenCalled()
  })

  it('reads a listed version from the Market after the server forgot the task', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(
      publishStatus({ remote: { release: { state: 'published', missing_assets: [], workflow: 'success' as const, ready: true } } })
    )
    api.fetchMarketPluginVersions.mockResolvedValue([
      { id: 3, plugin_id: 7, version: 'v0.1.0' },
      { id: 4, plugin_id: 7, version: 'v0.2.0' },
    ])
    approvedSubmission()
    const container = await mount()
    await openPublish(container)
    expect(api.fetchMarketPluginVersions).toHaveBeenCalledWith(7)
    const marketVersion = steps()[4]!
    expect(marketVersion.textContent).toContain(en.publishStatusDone)
    expect(marketVersion.textContent).toContain(en.marketVersionListed)
    expect(marketVersion.textContent).not.toContain('comes first')
    let finish!: (versions: { version: string }[]) => void
    api.fetchMarketPluginVersions.mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
    api.getPluginProjectPublishStatus.mockResolvedValue(publishStatus({ repository: { tag: 'v0.3.0' } }))
    await click(en.publishRefresh)
    expect(steps()[4]!.textContent).not.toContain(en.marketVersionListed)
    finish([{ version: 'v0.2.0' }])
    await settle()
  })

  it('starts the full publish after the second confirmation, with its task in the Release step', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(publishStatus())
    api.startPluginProjectPublish.mockResolvedValue(publishTask())
    approvedSubmission()
    const container = await mount()
    await openPublish(container)
    // Each step carries its own actions.
    expect(steps()[3]!.textContent).toContain(en.publishRunGithubMarket)
    expect(steps()[4]!.textContent).toContain(en.publishRunMarket)
    await click(en.publishRunGithubMarket)
    await settle()
    expect(api.startPluginProjectPublish).not.toHaveBeenCalled()
    await click(en.publishConfirmTitle, { last: true })
    await settle()
    expect(api.startPluginProjectPublish).toHaveBeenCalledWith('p1', 'github_and_market')
    expect(steps()[3]!.querySelector('.authoring-publish-task')?.textContent).toContain(
      en.publishTaskQueued
    )
    expect(steps()[4]!.querySelector('.authoring-publish-task')).toBeNull()
  })

  it('explains a hard blocker and runs nothing: Market notify before a Release', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(publishStatus())
    approvedSubmission()
    const container = await mount()
    await openPublish(container)
    await click(en.publishRunMarket)
    await settle()
    const box = messageBox()!
    expect(box.textContent).toContain(en.publishBlockedTitle)
    expect(box.textContent).toContain('Release v0.2.0 does not exist yet.')
    // No way to continue: only going to the step or closing.
    expect(box.textContent).not.toContain(en.publishContinueAnyway)
    expect(box.textContent).not.toContain(en.publishConfirmTitle)
    const focus = vi.fn()
    const heading = steps()[3]!.querySelector('h3') as HTMLElement
    heading.focus = focus
    await click('Go to step 4: GitHub Release', { last: true })
    await settle()
    expect(focus).toHaveBeenCalled()
    expect(api.startPluginProjectPublish).not.toHaveBeenCalled()
  })

  it('suggests the earlier step on a soft blocker, and continuing still runs', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(publishStatus())
    api.startPluginProjectPublish.mockResolvedValue(publishTask({ mode: 'github' }))
    const container = await mount()
    await openPublish(container)
    // Not approved: a Release alone works, so it is soft.
    await click(en.publishRunGithub)
    await settle()
    expect(messageBox()?.textContent).toContain(en.publishSoftNotApproved)
    // Going back runs nothing.
    await click('Go to step 3: Market submission', { last: true })
    await settle()
    expect(api.startPluginProjectPublish).not.toHaveBeenCalled()
    // Continuing reaches the usual publish confirmation.
    await click(en.publishRunGithub)
    await settle()
    await click(en.publishContinueAnyway, { last: true })
    await settle()
    expect(api.startPluginProjectPublish).not.toHaveBeenCalled()
    await click(en.publishConfirmTitle, { last: true })
    await settle()
    expect(api.startPluginProjectPublish).toHaveBeenCalledWith('p1', 'github')
  })

  it('treats the full publish before approval as a hard blocker', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(publishStatus())
    const container = await mount()
    await openPublish(container)
    await click(en.publishRunGithubMarket)
    await settle()
    expect(messageBox()?.textContent).toContain(en.publishBlockedNotApproved)
    expect(messageBox()?.textContent).not.toContain(en.publishContinueAnyway)
    expect(api.startPluginProjectPublish).not.toHaveBeenCalled()
  })

  it('moves focus to a section when its step node is clicked', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(publishStatus())
    const container = await mount()
    await openPublish(container)
    const nodes = [...container.querySelectorAll('.authoring-stepper__node')] as HTMLElement[]
    // Step 0 (tools) and the five publish steps.
    expect(nodes.map((node) => node.getAttribute('data-step'))).toEqual([
      'toolchain',
      'repository',
      'verify',
      'submission',
      'release',
      'market',
    ])
    expect(nodes[3]!.getAttribute('aria-current')).toBe('step')
    nodes[5]!.click()
    await flush()
    expect(document.activeElement).toBe(steps()[4]!.querySelector('h3'))
  })

  it('shows one step per page, opening on the step to act on', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(publishStatus())
    const container = await mount()
    await openPublish(container)
    const visible = () => steps().filter((item) => (item as HTMLElement).style.display !== 'none')
    expect(visible().map((item) => item.getAttribute('data-step'))).toEqual(['submission'])
    expect(container.querySelector('.authoring-publish-pager')!.textContent).toContain('Step 3 of 5')
    await click('Next: GitHub Release')
    expect(visible().map((item) => item.getAttribute('data-step'))).toEqual(['release'])
    const nodes = [...container.querySelectorAll('.authoring-stepper__node')] as HTMLElement[]
    expect(nodes[4]!.getAttribute('aria-current')).toBe('step')
    // The step to act on keeps its own marker while another page is shown.
    expect(nodes[3]!.className).toContain('authoring-stepper__node--current')
    await click('Previous: Market submission')
    await click('Previous: Verification workflow')
    await click('Previous: Public repository')
    await click('Previous: Prepare tools')
    const allPages = [...document.querySelectorAll('.authoring-publish-step')] as HTMLElement[]
    const shown = allPages.filter((item) => item.style.display !== 'none')
    expect(shown.map((item) => item.getAttribute('data-step'))).toEqual(['toolchain'])
    expect(container.querySelector('.authoring-publish-pager')!.textContent).toContain('Step 0 of 5')
    expect(buttons('Previous: Prepare tools')).toHaveLength(0)
    // A page the user picked stays put through a refresh.
    await click(en.publishRefresh)
    expect(page('toolchain').style.display).not.toBe('none')
  })

  it('opens on step 0 while a required tool is missing, with how to install it', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(publishStatus())
    api.getPluginToolchain.mockResolvedValue(
      readyToolchain({
        uv: { installed: false, version: null },
        gh: { installed: true, version: '2.101.0', authenticated: false, login: null },
      })
    )
    const container = await mount()
    await openPublish(container)
    const tools = page('toolchain')
    expect(tools.style.display).not.toBe('none')
    expect(tools.querySelector('h3')!.textContent).toContain('0. Prepare tools')
    const uv = tools.querySelector('[data-tool="uv"]')!
    expect(uv.textContent).toContain(en.toolMissing)
    expect(uv.textContent).toContain(en.toolRequired)
    // A missing tool opens with its install commands.
    expect(uv.querySelector('details')!.open).toBe(true)
    expect(uv.textContent).toContain('astral.sh/uv/install.ps1')
    const account = tools.querySelector('[data-tool="githubAccount"]')!
    expect(account.textContent).toContain(en.toolSignedOut)
    expect(account.textContent).toContain('gh auth login')
    const git = tools.querySelector('[data-tool="git"]')!
    expect(git.textContent).toContain('Ready · 2.55.0')
    expect(git.querySelector('details')!.open).toBe(false)
    // Publishing without uv is refused and points back to step 0.
    await click('Next: Public repository')
    await click('Next: Verification workflow')
    await click('Next: Market submission')
    await click('Next: GitHub Release')
    await click(en.publishRunGithub)
    await settle()
    expect(messageBox()?.textContent).toContain('uv is not set up on this computer (step 0).')
    await click('Go to step 0: Prepare tools', { last: true })
    await settle()
    expect(page('toolchain').style.display).not.toBe('none')
    expect(api.startPluginProjectPublish).not.toHaveBeenCalled()
  })

  it('lists uncommitted files and recent commits on the repository page', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(
      publishStatus({
        repository: { worktree_clean: false, dirty_paths: ['main.py', 'notes.txt'], head: 'abc1234def' },
        workspace: {
          changes: [
            { path: 'main.py', kind: 'modified' },
            { path: 'notes.txt', kind: 'untracked' },
          ],
          changes_total: 2,
          commits: [
            { sha: 'abc1234def', author: 'Dev', timestamp: 1_700_000_000, subject: 'local work', pushed: false },
            { sha: 'fff0000aaa', author: 'Dev', timestamp: 1_699_000_000, subject: 'first', pushed: true },
          ],
          ahead: 1,
          behind: 0,
        },
      })
    )
    const container = await mount()
    await openPublish(container)
    const workspace = page('repository').querySelector('.authoring-workspace')!
    expect(workspace.textContent).toContain(en.workspaceTitle)
    expect(workspace.textContent).toContain('main.py')
    expect(workspace.textContent).toContain(en.workspaceChangeModified)
    expect(workspace.textContent).toContain(en.workspaceChangeUntracked)
    expect(workspace.textContent).toContain('1 commit(s) not pushed')
    const commits = [...workspace.querySelectorAll('.authoring-workspace__commits li')]
    expect(commits.map((item) => item.querySelector('.authoring-workspace__subject')!.textContent)).toEqual([
      'local work',
      'first',
    ])
    expect(commits[0]!.textContent).toContain('abc1234')
    expect(commits[0]!.textContent).toContain('HEAD')
    expect(commits[0]!.textContent).toContain(en.workspaceNotPushed)
    expect(commits[1]!.textContent).not.toContain(en.workspaceNotPushed)
  })

  it('explains every page for beginners, with the project filled into its commands', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(publishStatus())
    const container = await mount()
    await openPublish(container)
    for (const id of ['toolchain', 'repository', 'verify', 'submission', 'release', 'market']) {
      const guide = page(id).querySelector('.authoring-guide')
      expect(guide, `guide on ${id}`).toBeTruthy()
      expect(guide!.querySelector('.authoring-guide__summary')!.textContent!.length).toBeGreaterThan(40)
      expect(guide!.querySelectorAll('.authoring-guide__section').length).toBeGreaterThan(1)
    }
    const repository = page('repository').querySelector('.authoring-guide')!.textContent
    expect(repository).toContain('git push -u origin main')
    expect(repository).toContain('n.e.k.o_plugin_hello_world')
    expect(page('release').querySelector('.authoring-guide')!.textContent).toContain('v0.2.0')
    expect(page('verify').querySelector('.authoring-guide')!.textContent).toContain(
      'https://github.com/owner/hello_world/actions'
    )
    // Unfilled placeholders never leak into the text.
    expect(container.querySelector('.authoring-publish')!.textContent).not.toMatch(/\{(repo|tag|version|branch|pluginId|dir)\}/)
  })

  it('opens the page a blocker dialog points to', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(publishStatus())
    approvedSubmission()
    const container = await mount()
    await openPublish(container)
    await click('Next: Market version')
    await click(en.publishRunMarket)
    await settle()
    await click('Go to step 4: GitHub Release', { last: true })
    await settle()
    const shown = steps().filter((item) => (item as HTMLElement).style.display !== 'none')
    expect(shown.map((item) => item.getAttribute('data-step'))).toEqual(['release'])
  })

  it('shows the server reason after a refused publish', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(publishStatus())
    api.startPluginProjectPublish.mockRejectedValue({
      response: {
        data: {
          detail: { code: 'PUBLISH_NOT_READY', message: 'release assets are incomplete: a.zip' },
        },
      },
    })
    approvedSubmission()
    const container = await mount()
    await openPublish(container)
    await click(en.publishRunGithub)
    await settle()
    await click(en.publishConfirmTitle, { last: true })
    await settle()
    // The refusal shows in the step whose button was pressed.
    const text = steps()[3]!.textContent
    expect(text).toContain(en.publishNotReady)
    expect(text).toContain('release assets are incomplete: a.zip')
  })

  it('offers repository creation only when there is no origin at all', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(
      publishStatus({
        repository: { origin_url: null, github_repository: null, upstream: null, head_pushed: null },
        remote: null,
      })
    )
    const container = await mount()
    await openPublish(container)
    expect(buttons(en.publishCreateRepo)).toHaveLength(1)
    expect(steps()[0]!.textContent).toContain(en.publishRepoNoGithub)
    await click(en.publishRunGithubMarket)
    await settle()
    expect(messageBox()?.textContent).toContain(en.publishRepoNoGithub)
    expect(api.startPluginProjectPublish).not.toHaveBeenCalled()
  })

  it('names a non-GitHub origin instead of offering to create a repository', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(
      publishStatus({
        repository: { origin_url: 'https://gitee.com/owner/hello_world.git', github_repository: null },
        remote: null,
      })
    )
    const container = await mount()
    await openPublish(container)
    expect(buttons(en.publishCreateRepo)).toHaveLength(0)
    expect(steps()[0]!.textContent).toContain(
      'origin points to https://gitee.com/owner/hello_world.git, which is not a GitHub repository.'
    )
  })

  it.each([
    [
      'release.yml is outdated',
      publishStatus({ actions: { [RELEASE_WORKFLOW_PATH]: 'UPGRADE' } }),
      en.publishRepoReleaseWorkflow,
    ],
    [
      'the local tag is on another commit',
      publishStatus({ repository: { tag_exists: true, tag_at_head: false } }),
      'Local tag v0.2.0 already points at another commit; bump the version.',
    ],
    [
      'the worktree is dirty',
      publishStatus({ repository: { worktree_clean: false, dirty_paths: ['main.py', 'a.txt'] } }),
      'Uncommitted changes: main.py, a.txt. Commit them first.',
    ],
    [
      'the branch has no upstream',
      publishStatus({ repository: { upstream: null, head_pushed: null } }),
      'Branch main has no upstream; run git push -u origin main.',
    ],
    [
      'the upstream is not on origin',
      publishStatus({ repository: { upstream: 'fork/main', head_pushed: false } }),
      'Branch main tracks fork/main, not origin; run git push -u origin main.',
    ],
    [
      'HEAD is detached',
      publishStatus({ repository: { detached: true, branch: null, head_pushed: null } }),
      en.publishRepoDetached,
    ],
    [
      'the repository is private',
      publishStatus({ remote: { visibility: 'private' } }),
      en.publishRepoNotPublic,
    ],
  ])('refuses the GitHub publish with the reason when %s', async (_, status, detail) => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(status)
    approvedSubmission()
    const container = await mount()
    await openPublish(container)
    expect(steps()[0]!.textContent).toContain(en.publishStatusTodo)
    expect(steps()[0]!.textContent).toContain(detail)
    await click(en.publishRunGithub)
    await settle()
    expect(messageBox()?.textContent).toContain(en.publishBlockedTitle)
    expect(messageBox()?.textContent).toContain(detail)
    expect(messageBox()?.textContent).not.toContain(en.publishContinueAnyway)
    expect(api.startPluginProjectPublish).not.toHaveBeenCalled()
  })

  it('reports local git problems even when GitHub cannot be read', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(
      publishStatus({
        repository: { worktree_clean: false, dirty_paths: ['main.py'] },
        remote: {
          reachable: false,
          visibility: 'unknown',
          verify: 'unknown',
          release: { state: 'unknown', missing_assets: [], workflow: 'unknown' as const, ready: false },
          tag: 'unknown',
        },
      })
    )
    approvedSubmission()
    const container = await mount()
    const pane = await openPublish(container)
    const [repository, verify, , release] = steps()
    expect(pane.textContent).toContain(en.publishRemoteUnreachable)
    expect(repository!.textContent).toContain('Uncommitted changes: main.py. Commit them first.')
    expect(repository!.className).toContain('authoring-publish-step--current')
    expect(verify!.textContent).toContain(en.publishRemoteUnknown)
    expect(release!.textContent).toContain(en.publishRemoteUnknown)
  })

  it.each([
    ['failure', en.publishVerifyFailed, en.publishStatusTodo],
    ['running', en.publishVerifyRunning, en.publishStatusWaiting],
    ['not_run', en.publishVerifyNotRun, en.publishStatusTodo],
    ['missing', en.publishVerifyMissing, en.publishStatusTodo],
  ] as const)('explains a verify state of %s', async (verify, detail, label) => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(publishStatus({ remote: { verify } }))
    const container = await mount()
    await openPublish(container)
    expect(steps()[1]!.textContent).toContain(detail)
    expect(steps()[1]!.textContent).toContain(label)
  })

  it('explains a missing release whose tag is already on GitHub', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(publishStatus({ remote: { tag: 'elsewhere' } }))
    approvedSubmission()
    const container = await mount()
    await openPublish(container)
    expect(steps()[3]!.textContent).toContain(
      'Tag v0.2.0 on GitHub points at another commit; bump the version.'
    )
  })

  it('refresh bypasses the cache, re-reads the task and keeps the last status on error', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(publishStatus())
    const container = await mount()
    await openPublish(container)
    expect(api.getPluginProjectPublishTask).toHaveBeenCalledOnce()
    await click(en.publishRefresh)
    expect(api.getPluginProjectPublishStatus).toHaveBeenLastCalledWith('p1', true)
    expect(api.getPluginProjectPublishTask).toHaveBeenCalledTimes(2)
    api.getPluginProjectPublishStatus.mockRejectedValue(new Error('server down'))
    await click(en.publishRefresh)
    expect(container.querySelector('.authoring-publish')!.textContent).toContain('server down')
    expect(steps()).toHaveLength(5)
  })

  it('reloads the status when the publish tab is opened again', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(publishStatus())
    const container = await mount()
    await openPublish(container)
    expect(api.getPluginProjectPublishStatus).toHaveBeenCalledOnce()
    await openTab(container, 0)
    await openPublish(container)
    expect(api.getPluginProjectPublishStatus).toHaveBeenCalledTimes(2)
    expect(api.getPluginProjectPublishStatus).toHaveBeenLastCalledWith('p1', false)
  })

  it('runs a reload requested during a read once that read ends', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    let resolve!: (value: PublishStatus) => void
    api.getPluginProjectPublishStatus
      .mockReturnValueOnce(new Promise((done) => (resolve = done)))
      .mockResolvedValue(publishStatus({ remote: { verify: 'failure' } }))
    const container = await mount()
    await openPublish(container)
    await openTab(container, 0)
    await openPublish(container)
    // The second read waits for the first instead of being dropped.
    expect(api.getPluginProjectPublishStatus).toHaveBeenCalledOnce()
    resolve(publishStatus())
    await flush()
    expect(api.getPluginProjectPublishStatus).toHaveBeenCalledTimes(2)
    expect(steps()[1]!.textContent).toContain(en.publishVerifyFailed)
  })

  it('shows an earlier failed run collapsed, with when it ran', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(publishStatus())
    api.getPluginProjectPublishTask.mockResolvedValue(
      publishTask({ status: 'failed', message: '[FAIL] old', log: ['[FAIL] old'], finished_at: 1_700_000_000 })
    )
    const container = await mount()
    await openPublish(container)
    const section = container.querySelector('.authoring-publish-task')!
    expect(section.textContent).toContain(en.publishTaskFailed)
    expect(section.textContent).toContain('Last run ·')
    expect(section.querySelector('details')!.open).toBe(false)
  })

  it('keeps polling through a transient error and drops a task the server forgot', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(publishStatus())
    api.getPluginProjectPublishTask
      .mockResolvedValueOnce(publishTask({ status: 'running' }))
      .mockRejectedValueOnce(new Error('network'))
      .mockRejectedValueOnce(taskNotFound)
    const container = await mount()
    await openPublish(container)
    expect(buttons(en.publishCancelTask)).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(3000)
    await flush()
    // A failed poll is retried, and the running task stays on screen meanwhile.
    expect(api.getPluginProjectPublishTask).toHaveBeenCalledTimes(2)
    expect(container.querySelector('.authoring-publish-task')?.textContent).toContain(
      en.publishTaskRunning
    )
    await vi.advanceTimersByTimeAsync(3000)
    await flush()
    expect(api.getPluginProjectPublishTask).toHaveBeenCalledTimes(3)
    expect(container.querySelector('.authoring-publish-task')).toBeNull()
    expect(buttons(en.publishCancelTask)).toHaveLength(0)
    expect(api.getPluginProjectPublishStatus).toHaveBeenLastCalledWith('p1', true)
  })

  it('runs the pre-release check in the verify step and renders the CI gate report', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(publishStatus())
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
    const publish = await openPublish(container)
    await click(en.precheckRun)
    expect(api.precheckPluginProject).toHaveBeenCalledWith('p1')
    expect(publish.querySelector('[data-step="verify"] .authoring-precheck')).toBeTruthy()
    const precheck = container.querySelector('.authoring-precheck')!
    expect(precheck.textContent).toContain(en.precheckStageFailed)
    expect(precheck.textContent).toContain(en.precheckSeverityWarning)
    const pane = precheck
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

  it('says why the runtime did not load a readable project', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([
      {
        ...listed,
        loaded: false,
        problem: 'id_taken',
        error: "Plugin ID 'hello_world' is provided by D:/plugins/hello_world/plugin.toml",
      },
    ])
    const container = await mount()
    expect(document.body.textContent).toContain(en.unloadedIdTaken.replace('{id}', 'hello_world'))
    expect(container.querySelector('details.authoring-diagnostic')!.textContent).toContain(
      'D:/plugins/hello_world/plugin.toml'
    )
  })

  it('does not blame a valid project when the plugin system is off', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([
      { ...listed, loaded: false, problem: 'runtime_inactive', error: null },
    ])
    const container = await mount()
    const notice = container.querySelector('.authoring-details .el-alert')!
    expect(notice.textContent).toContain(en.unloadedRuntimeInactive)
    expect(notice.textContent).not.toContain('plugin.toml')
    // Informational, not a warning about the project.
    expect(notice.className).toContain('el-alert--info')
    expect(container.querySelector('.authoring-header')!.textContent).toContain(
      en.statusRuntimeInactive
    )
    expect(container.querySelector('details.authoring-diagnostic')).toBeNull()
  })

  it('builds the project package from its source and downloads it', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([{ ...listed, loaded: false }])
    api.buildPluginCli.mockResolvedValue({
      built: [{ package_path: 'D:/packages/hello_world.neko-plugin' }],
      built_count: 1,
      failed: [],
      failed_count: 0,
      ok: true,
    })
    await mount()
    await click(en.buildPackage)
    expect(api.buildPluginCli).toHaveBeenCalledWith({
      mode: 'single',
      plugin_ref: { root_id: 'project', directory_name: 'hello_world' },
    }, { timeout: 300_000 })
    expect(api.downloadPluginPackage).toHaveBeenCalledWith('D:/packages/hello_world.neko-plugin')
    await settle()
    expect(document.body.textContent).toContain(en.buildPackageDone)
  })

  it('reports a failed build and downloads nothing', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.buildPluginCli.mockResolvedValue({
      built: [],
      built_count: 0,
      failed: [{ plugin: 'hello_world', error: 'entry module is missing' }],
      failed_count: 1,
      ok: false,
    })
    await mount()
    await click(en.buildPackage)
    await settle()
    expect(api.downloadPluginPackage).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain(`${en.buildPackageFailed} entry module is missing`)
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


it('retranslates a persistent creation error and type labels, and limits the author length', async () => {
  await mount()
  await click(en.newProject)
  expect((document.getElementById('authoring-author') as HTMLInputElement).maxLength).toBe(80)
  await click(en.chooseFolder)
  await fill('authoring-name', created.name)
  await fill('authoring-id', created.plugin_id)
  api.previewPluginProject.mockRejectedValue({ response: { data: { code: 'GIT_NOT_FOUND' } } })
  await click(en.next)
  expect(document.body.textContent).toContain(en.gitMissing)
  switchLocale('zh-CN')
  await flush()
  expect(document.body.textContent).toContain(authoringMessages['zh-CN'].gitMissing)
  expect(document.body.textContent).not.toContain(en.gitMissing)
  expect([...document.querySelectorAll('.el-radio__label')].map((el) => el.textContent)).toEqual(['插件', '适配器'])
})

it('translates the current project type using the shared plugin labels', async () => {
  api.route.query = { project: 'p1' }
  api.listPluginProjects.mockResolvedValue([{ ...listed, plugin_type: 'adapter' }])
  const container = await mount()
  switchLocale('zh-CN')
  await flush()
  expect(container.querySelector('.authoring-header__title')!.textContent).toContain('适配器')
})

it('explains an unavailable existing-project picker without offering a manual path', async () => {
  await mount()
  api.pickProjectDirectory.mockRejectedValue({ response: { data: { code: 'DIRECTORY_PICKER_UNAVAILABLE' } } })
  await click(en.addExisting)
  expect(document.body.textContent).toContain(en.projectPickerUnavailable)
  expect(document.body.textContent).not.toContain(en.pickerUnavailable)
  expect(api.registerPluginProject).not.toHaveBeenCalled()
  expect(document.querySelectorAll('.el-message-box__input').length).toBe(0)
})

it('documents the explicit external project path for each follow-up CLI command', async () => {
  await mount()
  await click(en.help)
  const commands = [...document.querySelectorAll('.authoring-help-drawer pre')].map((el) => el.textContent).join('\n')
  for (const command of ['check', 'check-release', 'build', 'publish']) {
    const expected = command === 'check-release'
      ? 'check "<parent-directory>/<plugin_id>" --release'
      : `${command} "<parent-directory>/<plugin_id>"`
    expect(commands).toContain(expected)
  }
})


it('shows and retranslates the repository failure fallback when the CLI returns no log', async () => {
  api.route.query = { project: 'p1' }
  api.listPluginProjects.mockResolvedValue([listed])
  api.getPluginProjectPublishStatus.mockResolvedValue(publishStatus({
    repository: { origin_url: null, github_repository: null }, remote: null,
  }))
  api.createPluginProjectRepo.mockResolvedValue({ status: 'failed', repository_url: '', log: [], finished_at: 1 })
  const container = await mount()
  await openPublish(container)
  await settle()
  await click(en.publishCreateRepo)
  expect(document.body.textContent).toContain(en.repoCreateFailed)
  switchLocale('zh-CN')
  await flush()
  expect(document.body.textContent).toContain(authoringMessages['zh-CN'].repoCreateFailed)
  expect(document.body.textContent).not.toContain(en.repoCreateFailed)
})
