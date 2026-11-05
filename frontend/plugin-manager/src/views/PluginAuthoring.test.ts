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
} from '@/api/pluginProjects'
import { RELEASE_WORKFLOW_PATH } from '@/api/pluginProjects'

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
// The real submission panel is mounted, so the state it reports reaches the
// publish steps; only its Market login and network calls are replaced.
const market = vi.hoisted(() => ({
  listMarketSubmissions: vi.fn(),
  authenticated: false,
}))
vi.mock('@/api/marketSubmissions', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/marketSubmissions')>()),
  listMarketSubmissions: market.listMarketSubmissions,
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
}
/** A project whose local and remote state passes every publish gate, without a release. */
function publishStatus(overrides: {
  repository?: Partial<PublishStatus['repository']>
  remote?: Partial<NonNullable<PublishStatus['remote']>> | null
  actions?: PublishStatus['actions']
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
            release: { state: 'missing', missing_assets: [] },
            tag: 'missing',
            ...overrides.remote,
          },
  }
}
const publishTask = (overrides: Partial<PublishTask> = {}): PublishTask => ({
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
  ...overrides,
})
const taskNotFound = { response: { data: { detail: { code: 'PUBLISH_TASK_NOT_FOUND' } } } }
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

async function openTab(container: HTMLElement, index: number) {
  const tabs = [...container.querySelectorAll('.authoring-tabs > .el-tabs__header [role="tab"]')]
  ;(tabs[index] as HTMLElement).click()
  await flush()
}

async function openPublish(container: HTMLElement) {
  await openTab(container, 2)
  return container.querySelector('.authoring-publish')!
}

function steps() {
  return [...document.querySelectorAll('.authoring-publish-step')]
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
  market.authenticated = false
  market.listMarketSubmissions.mockResolvedValue({ items: [] })
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
    const [repository, verify, release, submission, marketVersion] = steps()
    expect(repository!.textContent).toContain('owner/hello_world is public and HEAD is pushed.')
    expect(verify!.textContent).toContain(en.publishVerifyPassed)
    expect(release!.textContent).toContain('Release v0.2.0 does not exist yet.')
    // The first step with something to do is highlighted; the panel with controls is never dimmed.
    expect(release!.className).toContain('authoring-publish-step--current')
    expect(submission!.className).not.toContain('authoring-publish-step--pending')
    // Signed out and no release yet: both Market steps wait, with their explanation.
    expect(submission!.textContent).toContain(en.publishStatusWaiting)
    expect(submission!.textContent).toContain(en.submissionStepDetailLater)
    expect(submission!.textContent).toContain(en.submissionLogin)
    expect(marketVersion!.textContent).toContain(en.marketVersionStepDetailLater)
    expect(pane.textContent).toContain('.github/workflows/verify.yml')
    expect(pane.textContent).toContain('n.e.k.o_plugin_hello_world')
  })

  it('carries the real submission panel state into the submission and Market steps', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(
      publishStatus({ remote: { release: { state: 'published', missing_assets: [] } } })
    )
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
            repo_url: 'https://github.com/Owner/hello_world',
            repo_owner: 'Owner',
            repo_name: 'hello_world',
            plugin_name: 'Hello',
            plugin_slug: 'hello_world',
            submitted_ref: 'main',
            resolved_commit: 'abc',
          },
        },
      ],
      total: 1,
      page: 1,
      page_size: 100,
      total_pages: 1,
    })
    const container = await mount()
    await openPublish(container)
    expect(market.listMarketSubmissions).toHaveBeenCalledWith('owner/hello_world')
    const [, , , submission, marketVersion] = steps()
    expect(submission!.textContent).toContain(en.publishStatusDone)
    expect(submission!.textContent).toContain(en.submissionStateApproved)
    expect(marketVersion!.textContent).toContain(en.publishStatusDone)
    expect(marketVersion!.textContent).toContain('Listed on the Market as #7.')
  })

  it('offers the full publish only after the second confirmation', async () => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(publishStatus())
    api.startPluginProjectPublish.mockResolvedValue(publishTask())
    const container = await mount()
    await openPublish(container)
    // No release yet, so the full flow is offered, not the Market-only one.
    expect(buttons(en.publishRunMarket)).toHaveLength(0)
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
    const container = await mount()
    await openPublish(container)
    await click(en.publishRunGithub)
    await settle()
    await click(en.publishConfirmTitle, { last: true })
    await settle()
    const text = container.querySelector('.authoring-publish')!.textContent
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
    expect(buttons(en.publishRunGithubMarket)).toHaveLength(0)
    expect(buttons(en.publishRunMarket)).toHaveLength(0)
    expect(buttons(en.publishCreateRepo)).toHaveLength(1)
    expect(steps()[0]!.textContent).toContain(en.publishRepoNoGithub)
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
  ])('offers no GitHub publish when %s', async (_, status, detail) => {
    api.route.query = { project: 'p1' }
    api.listPluginProjects.mockResolvedValue([listed])
    api.getPluginProjectPublishStatus.mockResolvedValue(status)
    const container = await mount()
    await openPublish(container)
    expect(buttons(en.publishRunGithubMarket)).toHaveLength(0)
    expect(buttons(en.publishRunGithub)).toHaveLength(0)
    expect(steps()[0]!.textContent).toContain(en.publishStatusTodo)
    expect(steps()[0]!.textContent).toContain(detail)
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
          release: { state: 'unknown', missing_assets: [] },
          tag: 'unknown',
        },
      })
    )
    const container = await mount()
    const pane = await openPublish(container)
    const [repository, verify, release] = steps()
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
    const container = await mount()
    await openPublish(container)
    expect(steps()[2]!.textContent).toContain(
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
