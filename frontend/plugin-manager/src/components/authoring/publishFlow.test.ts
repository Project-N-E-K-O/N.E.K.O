import { describe, expect, it } from 'vitest'
import { RELEASE_WORKFLOW_PATH, type PublishStatus, type PublishTask } from '@/api/pluginProjects'
import type { SubmissionPanelState } from '@/api/marketSubmissions'
import { publishFlow, type PublishStepId } from './publishFlow'

const HEAD = 'a'.repeat(40)
const APPROVED = 'b'.repeat(40)

function status(
  overrides: {
    repository?: Partial<PublishStatus['repository']>
    remote?: Partial<NonNullable<PublishStatus['remote']>> | null
    actions?: PublishStatus['actions']
  } = {}
): PublishStatus {
  return {
    plugin_id: 'hello_world',
    version: '0.2.0',
    repository: {
      git_available: true,
      is_repository: true,
      tag: 'v0.2.0',
      origin_url: 'https://github.com/owner/n.e.k.o_plugin_hello_world.git',
      github_repository: 'owner/n.e.k.o_plugin_hello_world',
      branch: 'main',
      upstream: 'origin/main',
      head: HEAD,
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

const submission = (overrides: Partial<SubmissionPanelState> = {}): SubmissionPanelState => ({
  phase: null,
  marketPluginId: null,
  resolvedCommit: null,
  ...overrides,
})
const approved = (resolvedCommit = HEAD) =>
  submission({ phase: 'approved', marketPluginId: 7, resolvedCommit })

const task = (overrides: Partial<PublishTask> = {}): PublishTask => ({
  task_id: 't1',
  project_id: 'p1',
  mode: 'market_notify',
  status: 'succeeded',
  message: '',
  release_url: 'https://github.com/owner/n.e.k.o_plugin_hello_world/releases/tag/v0.2.0',
  market_status: null,
  market_candidate_id: null,
  log: [],
  started_at: 1,
  updated_at: 2,
  finished_at: 2,
  ...overrides,
})

const published = { release: { state: 'published' as const, missing_assets: [] } }

function step(flow: ReturnType<typeof publishFlow>, id: PublishStepId) {
  return flow.steps.find((item) => item.id === id)!
}

describe('publishFlow', () => {
  it('orders the steps repository, verify, submission, release, market', () => {
    const flow = publishFlow({ status: status(), submission: submission(), task: null })
    expect(flow.steps.map((item) => item.id)).toEqual([
      'repository',
      'verify',
      'submission',
      'release',
      'market',
    ])
  })

  it('makes the submission the current step before approval; the Release waits for it', () => {
    const flow = publishFlow({ status: status(), submission: submission(), task: null })
    expect(flow.currentStepId).toBe('submission')
    expect(step(flow, 'release')).toMatchObject({
      state: 'waiting',
      detail: { key: 'publishReleaseAfterApproval' },
      warning: null,
    })
    expect(step(flow, 'market')).toMatchObject({
      state: 'waiting',
      detail: { key: 'marketVersionStepDetailLater' },
    })
    expect(flow.canPublishGithub).toBe(false)
    expect(flow.canNotifyMarket).toBe(false)
  })

  it.each([
    ['draft', 'todo'],
    ['submitted', 'waiting'],
    ['in_review', 'waiting'],
    ['author_action', 'todo'],
    ['approved', 'done'],
    ['rejected', 'todo'],
    ['withdrawn', 'todo'],
  ] as const)('shows a %s submission as %s', (phase, state) => {
    const flow = publishFlow({ status: status(), submission: submission({ phase }), task: null })
    expect(step(flow, 'submission').state).toBe(state)
  })

  it('offers the GitHub Release once the submission is approved', () => {
    const flow = publishFlow({ status: status(), submission: approved(), task: null })
    expect(flow.currentStepId).toBe('release')
    expect(step(flow, 'release')).toMatchObject({
      state: 'todo',
      detail: { key: 'publishReleaseMissing', params: { tag: 'v0.2.0' } },
      warning: null,
    })
    expect(step(flow, 'market').detail).toEqual({ key: 'marketVersionAwaitingRelease' })
    expect(flow.canPublishGithub).toBe(true)
  })

  it('warns that a Release from another commit than the approved one goes to human review', () => {
    const flow = publishFlow({ status: status(), submission: approved(APPROVED), task: null })
    expect(step(flow, 'release').warning).toEqual({
      key: 'publishReleaseCommitDiffers',
      params: { head: 'aaaaaaa', approved: 'bbbbbbb' },
    })
  })

  it('drops the commit warning once the Release exists', () => {
    const flow = publishFlow({
      status: status({ remote: published }),
      submission: approved(APPROVED),
      task: null,
    })
    expect(step(flow, 'release')).toMatchObject({ state: 'done', warning: null })
  })

  it('asks to notify the Market for an approved plugin with a complete Release', () => {
    const flow = publishFlow({ status: status({ remote: published }), submission: approved(), task: null })
    expect(flow.currentStepId).toBe('market')
    expect(step(flow, 'market').detail).toEqual({ key: 'marketVersionNotify', params: { tag: 'v0.2.0' } })
    expect(flow.canNotifyMarket).toBe(true)
    expect(flow.canPublishGithub).toBe(false)
  })

  it('never offers the Market notification before approval, even with a Release', () => {
    const flow = publishFlow({
      status: status({ remote: published }),
      submission: submission({ phase: 'in_review' }),
      task: null,
    })
    expect(step(flow, 'release').state).toBe('done')
    expect(flow.canNotifyMarket).toBe(false)
  })

  it.each([
    ['published', 'done', { key: 'marketVersionListed' }],
    ['already_published', 'done', { key: 'marketVersionListed' }],
    ['pending_review', 'waiting', { key: 'marketVersionPendingReview', params: { candidate: 12 } }],
    ['rejected', 'todo', { key: 'marketVersionRejected', params: { candidate: 12 } }],
    ['superseded', 'waiting', { key: 'marketVersionSuperseded' }],
  ] as const)('shows the Market answer %s', (marketStatus, state, detail) => {
    const flow = publishFlow({
      status: status({ remote: published }),
      submission: approved(),
      task: task({ market_status: marketStatus, market_candidate_id: 12 }),
    })
    expect(step(flow, 'market')).toMatchObject({ state, detail })
    expect(flow.canNotifyMarket).toBe(false)
  })

  it('reads a rejection from a failed full run, which keeps no release URL', () => {
    const flow = publishFlow({
      status: status({ remote: published }),
      submission: approved(),
      task: task({ mode: 'github_and_market', status: 'failed', release_url: '', market_status: 'rejected' }),
    })
    expect(step(flow, 'market').state).toBe('todo')
  })

  it('ignores the Market answer of an earlier version', () => {
    const flow = publishFlow({
      status: status({ remote: published }),
      submission: approved(),
      task: task({
        release_url: 'https://github.com/owner/n.e.k.o_plugin_hello_world/releases/tag/v0.1.0',
        market_status: 'published',
      }),
    })
    expect(step(flow, 'market').detail?.key).toBe('marketVersionNotify')
  })

  it('reports local git problems first and gates the GitHub publish on them', () => {
    const flow = publishFlow({
      status: status({ repository: { head_pushed: false } }),
      submission: approved(),
      task: null,
    })
    expect(flow.currentStepId).toBe('repository')
    expect(step(flow, 'repository').detail).toEqual({ key: 'publishRepoUnpushed' })
    expect(flow.pushedHead).toBeNull()
    expect(flow.canPublishGithub).toBe(false)
  })

  it('passes the full pushed HEAD to the submission', () => {
    const flow = publishFlow({ status: status(), submission: submission(), task: null })
    expect(flow.pushedHead).toBe(HEAD)
  })

  it('offers repository creation only without any origin', () => {
    const none = status({ repository: { origin_url: null, github_repository: null }, remote: null })
    expect(publishFlow({ status: none, submission: submission(), task: null }).canCreateRepository).toBe(true)
    const foreign = status({
      repository: { origin_url: 'https://gitee.com/o/r.git', github_repository: null },
      remote: null,
    })
    const flow = publishFlow({ status: foreign, submission: submission(), task: null })
    expect(flow.canCreateRepository).toBe(false)
    expect(step(flow, 'repository').detail).toEqual({
      key: 'publishRepoForeignOrigin',
      params: { url: 'https://gitee.com/o/r.git' },
    })
  })

  it('marks remote facts unknown when GitHub cannot be read', () => {
    const flow = publishFlow({
      status: status({
        remote: {
          reachable: false,
          visibility: 'unknown',
          verify: 'unknown',
          release: { state: 'unknown', missing_assets: [] },
          tag: 'unknown',
        },
      }),
      submission: approved(),
      task: null,
    })
    expect(flow.remoteWarning).toEqual({ key: 'publishRemoteUnreachable' })
    expect(step(flow, 'verify')).toMatchObject({ state: 'unknown', detail: { key: 'publishRemoteUnknown' } })
    expect(step(flow, 'release')).toMatchObject({ state: 'unknown', detail: { key: 'publishRemoteUnknown' } })
  })

  it('notes outdated workflows and the repository name the CLI expects', () => {
    const flow = publishFlow({
      status: status({
        repository: { github_repository: 'owner/hello' },
        actions: { [RELEASE_WORKFLOW_PATH]: 'CURRENT', '.github/workflows/verify.yml': 'UPGRADE' },
      }),
      submission: submission(),
      task: null,
    })
    expect(flow.notes).toEqual([
      { key: 'publishActionsOutdated', params: { files: '.github/workflows/verify.yml' } },
      { key: 'publishNameNote', params: { id: 'hello_world' } },
    ])
  })
})
