import { describe, expect, it } from 'vitest'
import {
  RELEASE_WORKFLOW_PATH,
  type PublishStatus,
  type PublishTask,
  type Toolchain,
} from '@/api/pluginProjects'
import type { SubmissionPanelState } from '@/api/marketSubmissions'
import { publishFlow, type PublishFlowFacts, type PublishStepId } from './publishFlow'

const HEAD = 'a'.repeat(40)
const APPROVED = 'b'.repeat(40)

/** The flow for these facts; the Market's version list is unread unless a test passes it. */
const flowOf = (facts: Omit<PublishFlowFacts, 'marketListed'> & Partial<PublishFlowFacts>) =>
  publishFlow({ marketListed: null, ...facts })

function status(
  overrides: {
    repository?: Partial<PublishStatus['repository']>
    remote?: Partial<NonNullable<PublishStatus['remote']>> | null
    actions?: PublishStatus['actions']
    workspace?: PublishStatus['workspace']
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
    workspace: overrides.workspace ?? null,
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
    const flow = flowOf({ status: status(), submission: submission(), task: null })
    expect(flow.steps.map((item) => item.id)).toEqual([
      'repository',
      'verify',
      'submission',
      'release',
      'market',
    ])
  })

  it('makes the submission the current step before approval; the Release waits for it', () => {
    const flow = flowOf({ status: status(), submission: submission(), task: null })
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
    const flow = flowOf({ status: status(), submission: submission({ phase }), task: null })
    expect(step(flow, 'submission').state).toBe(state)
  })

  it('offers the GitHub Release once the submission is approved', () => {
    const flow = flowOf({ status: status(), submission: approved(), task: null })
    expect(flow.currentStepId).toBe('release')
    expect(step(flow, 'release')).toMatchObject({
      state: 'todo',
      detail: { key: 'publishReleaseMissing', params: { tag: 'v0.2.0' } },
      warning: null,
    })
    expect(step(flow, 'market').detail).toEqual({ key: 'marketVersionAwaitingRelease' })
  })

  it('warns that a Release from another commit than the approved one goes to human review', () => {
    const flow = flowOf({ status: status(), submission: approved(APPROVED), task: null })
    expect(step(flow, 'release').warning).toEqual({
      key: 'publishReleaseCommitDiffers',
      params: { head: 'aaaaaaa', approved: 'bbbbbbb' },
    })
  })

  it('drops the commit warning once the Release exists', () => {
    const flow = flowOf({
      status: status({ remote: published }),
      submission: approved(APPROVED),
      task: null,
    })
    expect(step(flow, 'release')).toMatchObject({ state: 'done', warning: null })
  })

  it('asks to notify the Market for an approved plugin with a complete Release', () => {
    const flow = flowOf({ status: status({ remote: published }), submission: approved(), task: null })
    expect(flow.currentStepId).toBe('market')
    expect(step(flow, 'market').detail).toEqual({ key: 'marketVersionNotify', params: { tag: 'v0.2.0' } })
  })

  it('waits for approval in the Market step, even with a Release', () => {
    const flow = flowOf({
      status: status({ remote: published }),
      submission: submission({ phase: 'in_review' }),
      task: null,
    })
    expect(step(flow, 'release').state).toBe('done')
    expect(step(flow, 'market').state).toBe('waiting')
  })

  it.each([
    ['published', 'done', { key: 'marketVersionListed' }],
    ['already_published', 'done', { key: 'marketVersionListed' }],
    ['pending_review', 'waiting', { key: 'marketVersionPendingReview', params: { candidate: 12 } }],
    ['rejected', 'todo', { key: 'marketVersionRejected', params: { candidate: 12 } }],
    ['superseded', 'waiting', { key: 'marketVersionSuperseded' }],
  ] as const)('shows the Market answer %s', (marketStatus, state, detail) => {
    const flow = flowOf({
      status: status({ remote: published }),
      submission: approved(),
      task: task({ market_status: marketStatus, market_candidate_id: 12 }),
    })
    expect(step(flow, 'market')).toMatchObject({ state, detail })
  })

  it('reads a rejection from a failed full run, which keeps no release URL', () => {
    const flow = flowOf({
      status: status({ remote: published }),
      submission: approved(),
      task: task({ mode: 'github_and_market', status: 'failed', release_url: '', market_status: 'rejected' }),
    })
    expect(step(flow, 'market').state).toBe('todo')
  })

  it('ignores the Market answer of an earlier version', () => {
    const flow = flowOf({
      status: status({ remote: published }),
      submission: approved(),
      task: task({
        release_url: 'https://github.com/owner/n.e.k.o_plugin_hello_world/releases/tag/v0.1.0',
        market_status: 'published',
      }),
    })
    expect(step(flow, 'market').detail?.key).toBe('marketVersionNotify')
  })

  it('reads a listed version from the Market when no task survived', () => {
    const flow = flowOf({
      status: status({ remote: published }),
      submission: approved(),
      task: null,
      marketListed: true,
    })
    expect(step(flow, 'market')).toMatchObject({ state: 'done', detail: { key: 'marketVersionListed' } })
  })

  it('prefers the Market list over an older answer for the same Release', () => {
    const flow = flowOf({
      status: status({ remote: published }),
      submission: approved(),
      task: task({ market_status: 'pending_review', market_candidate_id: 12 }),
      marketListed: true,
    })
    expect(step(flow, 'market').state).toBe('done')
  })

  it('still offers the notification when the Market does not list the Release', () => {
    const flow = flowOf({
      status: status({ remote: published }),
      submission: approved(),
      task: null,
      marketListed: false,
    })
    expect(step(flow, 'market').detail?.key).toBe('marketVersionNotify')
  })

  it('reports local git problems first and withholds the submission commit', () => {
    const flow = flowOf({
      status: status({ repository: { head_pushed: false } }),
      submission: approved(),
      task: null,
    })
    expect(flow.currentStepId).toBe('repository')
    expect(step(flow, 'repository').detail).toEqual({ key: 'publishRepoUnpushed' })
    expect(flow.pushedHead).toBeNull()
  })

  it('passes the full pushed HEAD to the submission', () => {
    const flow = flowOf({ status: status(), submission: submission(), task: null })
    expect(flow.pushedHead).toBe(HEAD)
  })

  it('offers repository creation only without any origin', () => {
    const none = status({ repository: { origin_url: null, github_repository: null }, remote: null })
    expect(flowOf({ status: none, submission: submission(), task: null }).canCreateRepository).toBe(true)
    const foreign = status({
      repository: { origin_url: 'https://gitee.com/o/r.git', github_repository: null },
      remote: null,
    })
    const flow = flowOf({ status: foreign, submission: submission(), task: null })
    expect(flow.canCreateRepository).toBe(false)
    expect(step(flow, 'repository').detail).toEqual({
      key: 'publishRepoForeignOrigin',
      params: { url: 'https://gitee.com/o/r.git' },
    })
  })

  it('marks remote facts unknown when GitHub cannot be read', () => {
    const flow = flowOf({
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
    const flow = flowOf({
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

describe('publishFlow action blockers', () => {
  const severities = (flow: ReturnType<typeof publishFlow>, mode: keyof typeof flow.blockers) =>
    flow.blockers[mode].map((item) => [item.severity, item.step, item.reason.key])

  it('has no blocker for the recommended next action', () => {
    const release = flowOf({ status: status(), submission: approved(), task: null })
    expect(release.blockers.github_and_market).toEqual([])
    expect(release.blockers.github).toEqual([])
    const market = flowOf({ status: status({ remote: published }), submission: approved(), task: null })
    expect(market.blockers.market_notify).toEqual([])
  })

  it('lets a Release alone run before approval, but not the full run that notifies the Market', () => {
    const flow = flowOf({ status: status(), submission: submission(), task: null })
    expect(severities(flow, 'github')).toEqual([['soft', 'submission', 'publishSoftNotApproved']])
    expect(severities(flow, 'github_and_market')).toEqual([
      ['hard', 'submission', 'publishBlockedNotApproved'],
    ])
  })

  it('puts every local git problem the server gate refuses first, as hard', () => {
    const flow = flowOf({
      status: status({ repository: { head_pushed: false }, remote: { verify: 'failure' } }),
      submission: submission(),
      task: null,
    })
    expect(severities(flow, 'github')).toEqual([
      ['hard', 'repository', 'publishRepoUnpushed'],
      ['soft', 'submission', 'publishSoftNotApproved'],
      ['soft', 'verify', 'publishVerifyFailed'],
    ])
  })

  it('refuses a second Release for a version that already has one', () => {
    const flow = flowOf({ status: status({ remote: published }), submission: approved(), task: null })
    expect(severities(flow, 'github')).toEqual([['hard', 'market', 'publishBlockedReleaseExists']])
  })

  it('warns, without blocking, when HEAD is not the approved commit', () => {
    const flow = flowOf({ status: status(), submission: approved(APPROVED), task: null })
    expect(severities(flow, 'github')).toEqual([['soft', null, 'publishReleaseCommitDiffers']])
  })

  it('needs approval and a complete Release before notifying the Market', () => {
    const flow = flowOf({
      status: status({ remote: { release: { state: 'published', missing_assets: ['a.zip'] } } }),
      submission: submission({ phase: 'in_review' }),
      task: null,
    })
    expect(severities(flow, 'market_notify')).toEqual([
      ['hard', 'submission', 'publishBlockedNotApproved'],
      ['hard', 'release', 'publishReleaseAssets'],
    ])
  })

  it('only asks before notifying again once the Market listed the version', () => {
    const flow = flowOf({
      status: status({ remote: published }),
      submission: approved(),
      task: null,
      marketListed: true,
    })
    expect(severities(flow, 'market_notify')).toEqual([['soft', null, 'publishSoftAlreadyAnswered']])
  })

  it('points later unfinished steps at the current one', () => {
    const flow = flowOf({ status: status(), submission: submission(), task: null })
    expect(flow.currentStepId).toBe('submission')
    expect(flow.prerequisites).toEqual({ release: 'submission', market: 'submission' })
    const done = flowOf({
      status: status({ remote: published }),
      submission: approved(),
      task: null,
      marketListed: true,
    })
    expect(done.prerequisites).toEqual({})
  })
})

describe('publishFlow step 0 (toolchain)', () => {
  const ready = (): Toolchain => ({
    git: { installed: true, version: '2.55.0', user_name: 'Dev', user_email: 'dev@example.com' },
    uv: { installed: true, version: '0.12.18' },
    gh: { installed: true, version: '2.101.0', authenticated: true, login: 'octocat' },
    source_checkout: true,
  })
  const tool = (flow: ReturnType<typeof publishFlow>, id: string) =>
    flow.toolchain.tools.find((item) => item.id === id)!

  it('is unknown until read, and does not take the current page', () => {
    const flow = flowOf({ status: status(), submission: submission(), task: null })
    expect(flow.toolchain.state).toBe('unknown')
    expect(flow.currentStepId).toBe('submission')
    expect(flow.blockers.github).toEqual([
      { severity: 'soft', step: 'submission', reason: { key: 'publishSoftNotApproved' } },
    ])
  })

  it('is done with every required tool, showing versions and the account', () => {
    const flow = flowOf({ status: status(), submission: approved(), task: null, toolchain: ready() })
    expect(flow.toolchain.state).toBe('done')
    expect(tool(flow, 'git').value).toBe('2.55.0')
    expect(tool(flow, 'gitIdentity').value).toBe('Dev <dev@example.com>')
    expect(tool(flow, 'githubAccount').value).toBe('octocat')
    expect(flow.blockers.github).toEqual([])
  })

  it('becomes the current page and a hard blocker when a required tool is missing', () => {
    const flow = flowOf({
      status: status(),
      submission: approved(),
      task: null,
      toolchain: { ...ready(), uv: { installed: false, version: null } },
    })
    expect(flow.toolchain.state).toBe('todo')
    expect(flow.currentStepId).toBe('toolchain')
    // Later unfinished steps point back at step 0; finished ones do not.
    expect(flow.prerequisites).toEqual({ release: 'toolchain', market: 'toolchain' })
    expect(flow.blockers.github[0]).toEqual({
      severity: 'hard',
      step: 'toolchain',
      reason: { key: 'publishToolMissingBlocker', params: { tool: 'uv' } },
    })
    // Notifying the Market runs no ruff, so it does not need uv.
    const market = flowOf({
      status: status({ remote: published }),
      submission: approved(),
      task: null,
      toolchain: { ...ready(), uv: { installed: false, version: null } },
    })
    expect(market.blockers.market_notify).toEqual([])
  })

  it('treats a missing git identity as required, and gh as recommended only', () => {
    const noIdentity = flowOf({
      status: status(),
      submission: approved(),
      task: null,
      toolchain: { ...ready(), git: { ...ready().git, user_email: null } },
    })
    expect(tool(noIdentity, 'gitIdentity').state).toBe('missing')
    expect(noIdentity.toolchain.state).toBe('todo')
    const noGh = flowOf({
      status: status(),
      submission: approved(),
      task: null,
      toolchain: { ...ready(), gh: { installed: false, version: null, authenticated: false, login: null } },
    })
    expect(tool(noGh, 'gh')).toMatchObject({ state: 'missing', required: false })
    expect(noGh.toolchain.state).toBe('done')
    expect(noGh.blockers.github).toEqual([])
  })
})
