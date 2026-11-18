/**
 * Where a project stands on the way from local source to a listed Market
 * version, derived from facts only: the local repository and public GitHub
 * reads (`PublishStatus`), the Market submission the panel found, and the
 * latest publish task. The publish tab renders this and nothing else.
 *
 * Order matters: the Market approves a submission without a Release, refuses
 * a release notification before approval, and lists the first version at once
 * only when it is built from the approved commit; any other commit goes to
 * human review.
 */
import {
  RELEASE_WORKFLOW_PATH,
  type PublishMode,
  type MarketPublicationStatus,
  type PublishRemoteState,
  type PublishStatus,
  type PublishTask,
  type Toolchain,
} from '@/api/pluginProjects'
import type { SubmissionPanelState, SubmissionPhase } from '@/api/marketSubmissions'
import type { AuthoringMessageKey } from '@/i18n/authoring'

export type PublishStepId = 'repository' | 'verify' | 'submission' | 'release' | 'market'
/** Step 0 prepares the machine; it is a page of the publish tab, not a publish step. */
export type PublishPageId = 'toolchain' | PublishStepId
export type PublishStepState = 'done' | 'todo' | 'waiting' | 'unknown'

/** An i18n key under `authoring.` with its parameters. */
export interface FlowMessage {
  key: AuthoringMessageKey
  params?: Record<string, string | number>
}

export interface PublishStep {
  id: PublishStepId
  state: PublishStepState
  detail: FlowMessage | null
  warning: FlowMessage | null
}

export interface PublishFlowFacts {
  status: PublishStatus
  submission: SubmissionPanelState
  /** The latest publish task, or null when the project never published or the server forgot it. */
  task: PublishTask | null
  /** Whether the Market's public version list has this Release's tag; null when it was not read. */
  marketListed: boolean | null
  marketReceipt?: MarketPublicationStatus | null
  /** The publishing tools of this machine; null or absent when they were not read. */
  toolchain?: Toolchain | null
}

/**
 * Why a publish action is not the recommended move right now. The action stays
 * clickable; the panel explains the blocker first.
 */
export interface ActionBlocker {
  /** soft: it works, with a worse outcome; hard: the server refuses it or it does lasting harm. */
  severity: 'soft' | 'hard'
  /** The page to finish first, or null when the reason is about the action itself. */
  step: PublishPageId | null
  reason: FlowMessage
}

export const PUBLISH_STEP_ORDER: readonly PublishStepId[] = [
  'repository',
  'verify',
  'submission',
  'release',
  'market',
]
/** Every page of the publish tab; a page's number is its index (step 0 prepares the machine). */
export const PUBLISH_PAGE_ORDER: readonly PublishPageId[] = ['toolchain', ...PUBLISH_STEP_ORDER]

export type ToolId = 'git' | 'gitIdentity' | 'uv' | 'gh' | 'githubAccount'

export interface ToolCheck {
  id: ToolId
  /** ok: ready; missing: install or set it up; unknown: not read yet. */
  state: 'ok' | 'missing' | 'unknown'
  /** The version, account or name to show once it is ready. */
  value: string | null
  /** A missing tool keeps some action from working at all. */
  required: boolean
}

export interface ToolchainPage {
  state: PublishStepState
  tools: ToolCheck[]
}

export interface PublishFlow {
  steps: PublishStep[]
  /** Step 0: the tools this machine needs; `unknown` until they are read. */
  toolchain: ToolchainPage
  /** Per publish mode: hard blockers first, then soft ones, each in step order. */
  blockers: Record<PublishMode, ActionBlocker[]>
  /** For a later page that is not done: the current one, which is better finished first. */
  prerequisites: Partial<Record<PublishStepId, PublishPageId>>
  /** The first page to act on; an unknown one only when nothing is actionable. */
  currentStepId: PublishPageId | null
  /** HEAD once it is on its origin branch, so the Market can resolve it. */
  pushedHead: string | null
  /** No origin at all: the workbench can create the GitHub repository. */
  canCreateRepository: boolean
  remoteWarning: FlowMessage | null
  notes: FlowMessage[]
}

const VERIFY_DETAILS: Record<Exclude<PublishRemoteState['verify'], 'unknown'>, AuthoringMessageKey> = {
  success: 'publishVerifyPassed',
  failure: 'publishVerifyFailed',
  running: 'publishVerifyRunning',
  not_run: 'publishVerifyNotRun',
  missing: 'publishVerifyMissing',
}

const SUBMISSION_STATES: Record<SubmissionPhase, PublishStepState> = {
  draft: 'todo',
  submitted: 'waiting',
  in_review: 'waiting',
  author_action: 'todo',
  approved: 'done',
  // Closed without approval: a new draft is the way on.
  rejected: 'todo',
  withdrawn: 'todo',
  superseded: 'todo',
  closed: 'todo',
}

const message = (key: AuthoringMessageKey, params?: FlowMessage['params']): FlowMessage =>
  params ? { key, params } : { key }

const shortCommit = (sha: string) => sha.slice(0, 7)

/** Why the remote read gave no answer, for a step whose remote fact is unknown. */
const remoteUnknown = (remote: PublishRemoteState | null) =>
  remote ? message('publishRemoteUnknown') : null

/** Local git facts first: they need no network and decide what the user does next. */
function localRepositoryProblem(status: PublishStatus): FlowMessage | null {
  const repo = status.repository
  if (!repo.is_repository) return message('publishRepoNoGit')
  if (!repo.github_repository) {
    return repo.origin_url
      ? message('publishRepoForeignOrigin', { url: repo.origin_url })
      : message('publishRepoNoGithub')
  }
  if (repo.worktree_clean !== true) {
    return repo.dirty_paths.length
      ? message('publishRepoUncommittedFiles', { files: repo.dirty_paths.join(', ') })
      : message('publishRepoUncommitted')
  }
  if (!repo.head) return message('publishRepoNoCommits')
  if (repo.detached) return message('publishRepoDetached')
  if (repo.head_pushed !== true) {
    const branch = repo.branch ?? ''
    if (!repo.upstream) return message('publishRepoNoUpstream', { branch })
    if (!repo.upstream.startsWith('origin/')) {
      return message('publishRepoUpstreamNotOrigin', { upstream: repo.upstream, branch })
    }
    return message('publishRepoUnpushed')
  }
  if (repo.tag_exists && repo.tag_at_head === false) {
    return message('publishRepoTagElsewhere', { tag: repo.tag })
  }
  if (status.actions[RELEASE_WORKFLOW_PATH] !== 'CURRENT') return message('publishRepoReleaseWorkflow')
  return null
}

function repositoryStep(status: PublishStatus): Omit<PublishStep, 'id' | 'warning'> {
  const problem = localRepositoryProblem(status)
  if (problem) return { state: 'todo', detail: problem }
  const remote = status.remote
  if (!remote || remote.visibility === 'unknown') {
    return { state: 'unknown', detail: remoteUnknown(remote) }
  }
  if (remote.visibility !== 'public') return { state: 'todo', detail: message('publishRepoNotPublic') }
  return {
    state: 'done',
    detail: message('publishRepoReady', { repo: status.repository.github_repository ?? '' }),
  }
}

function verifyStep(remote: PublishRemoteState | null): Omit<PublishStep, 'id' | 'warning'> {
  if (!remote || remote.verify === 'unknown') return { state: 'unknown', detail: remoteUnknown(remote) }
  const state = remote.verify === 'success' ? 'done' : remote.verify === 'running' ? 'waiting' : 'todo'
  return { state, detail: message(VERIFY_DETAILS[remote.verify]) }
}

/** The Release as GitHub reports it, for an approved plugin. */
function releaseState(remote: PublishRemoteState | null, tag: string): Omit<PublishStep, 'id' | 'warning'> {
  if (!remote || remote.release.state === 'unknown') {
    return { state: 'unknown', detail: remoteUnknown(remote) }
  }
  if (remote.release.state === 'missing') {
    if (remote.tag === 'elsewhere') return { state: 'todo', detail: message('publishRemoteTagElsewhere', { tag }) }
    if (remote.tag === 'at_head') return { state: 'waiting', detail: message('publishReleaseBuilding', { tag }) }
    return { state: 'todo', detail: message('publishReleaseMissing', { tag }) }
  }
  const missing = remote.release.missing_assets
  if (missing.length) {
    return { state: 'todo', detail: message('publishReleaseAssets', { tag, assets: missing.join(', ') }) }
  }
  if (!remote.release.ready) {
    if (remote.release.workflow === 'failure' || remote.release.workflow === 'missing') {
      return { state: 'todo', detail: message('publishReleaseWorkflowFailed', { tag }) }
    }
    if (remote.release.workflow === 'unknown') {
      return { state: 'unknown', detail: remoteUnknown(remote) }
    }
    return { state: 'waiting', detail: message('publishReleaseBuilding', { tag }) }
  }
  return { state: 'done', detail: message('publishReleaseReady', { tag }) }
}

function releaseStep(facts: PublishFlowFacts, releaseReady: boolean): Omit<PublishStep, 'id'> {
  const { status, submission } = facts
  const tag = status.repository.tag
  if (submission.phase !== 'approved') {
    return releaseReady
      ? { state: 'done', detail: message('publishReleaseReady', { tag }), warning: null }
      : { state: 'waiting', detail: message('publishReleaseAfterApproval'), warning: null }
  }
  const step = releaseState(status.remote, tag)
  const head = status.repository.head
  const approved = submission.resolvedCommit
  const differs =
    step.state !== 'done' && !!head && !!approved && head.toLowerCase() !== approved.toLowerCase()
  return {
    ...step,
    warning: differs
      ? message('publishReleaseCommitDiffers', { head: shortCommit(head), approved: shortCommit(approved) })
      : null,
  }
}

function decodeTag(value: string): string | null {
  try {
    return decodeURIComponent(value)
  } catch {
    return null
  }
}

/**
 * Whether `url` is the GitHub Release page of `tag` in `repository`.
 *
 * Only `https://github.com/<owner>/<repo>/releases/tag/<tag>` counts; query,
 * fragment and a trailing slash are ignored. GitHub owner and repository names
 * ignore case; tags do not, so the URL's tag is percent-decoded (the Market
 * re-encodes tags, and not always the same way: `v1%2Fpreview`, `v2%2E0%2E0`)
 * and compared exactly with `tag`. `tag` is the literal Git tag, never URL
 * text, so it is not decoded: a tag may itself contain `%`. Old receipts may
 * name a Release by its numeric ID (`/releases/<id>`), which says nothing
 * about the tag, so they never match.
 */
export function isReleaseUrlOf(url: string, repository: string, tag: string): boolean {
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return false
  }
  if (parsed.protocol !== 'https:' || parsed.host.toLowerCase() !== 'github.com') return false
  const match = /^\/([^/]+\/[^/]+)\/releases\/tag\/(.+?)\/?$/.exec(parsed.pathname)
  if (!match || match[1]!.toLowerCase() !== repository.toLowerCase()) return false
  return decodeTag(match[2]!) === tag
}

/** The Market's answer to the latest task's release notification, when it carries one. */
function marketAnswer(task: PublishTask | null, repository: string | null, tag: string): Omit<PublishStep, 'id' | 'warning'> | null {
  if (!task?.market_status || !repository) return null
  if (!isReleaseUrlOf(task.release_url, repository, tag)) return null
  return marketPublicationAnswer(task.market_status, task.market_candidate_id)
}

function marketPublicationAnswer(status: MarketPublicationStatus, candidate?: number | null): Omit<PublishStep, 'id' | 'warning'> {
  switch (status) {
    case 'published':
    case 'already_published':
    case 'approved':
      return { state: 'done', detail: message('marketVersionListed') }
    case 'pending_review':
      return { state: 'waiting', detail: candidate
        ? message('marketVersionPendingReview', { candidate })
        : message('marketVersionAwaitingReview') }
    case 'accepted':
    case 'waiting_build':
    case 'verifying':
    case 'retry_wait':
      return { state: 'waiting', detail: message('marketVersionVerifying') }
    case 'platform_attention':
      return { state: 'waiting', detail: message('marketVersionPlatformAttention') }
    case 'superseded':
      return { state: 'waiting', detail: message('marketVersionSuperseded') }
    case 'rejected':
      return { state: 'todo', detail: candidate
        ? message('marketVersionRejected', { candidate })
        : message('marketVersionRejectedReceipt') }
  }
}

function marketStep(
  listed: boolean | null,
  answer: Omit<PublishStep, 'id' | 'warning'> | null,
  approved: boolean,
  releaseReady: boolean,
  tag: string
): Omit<PublishStep, 'id' | 'warning'> {
  // The Market's own list outlives the task the server keeps in memory.
  if (listed) return { state: 'done', detail: message('marketVersionListed') }
  if (answer) return answer
  if (!approved) return { state: 'waiting', detail: message('marketVersionStepDetailLater') }
  return releaseReady
    ? { state: 'todo', detail: message('marketVersionNotify', { tag }) }
    : { state: 'waiting', detail: message('marketVersionAwaitingRelease') }
}

function notes(status: PublishStatus): FlowMessage[] {
  const result: FlowMessage[] = []
  // release.yml is a publish blocker and is reported on the repository step.
  const outdated = Object.entries(status.actions)
    .filter(([file, state]) => file !== RELEASE_WORKFLOW_PATH && state !== 'CURRENT')
    .map(([file]) => file)
  if (outdated.length) result.push(message('publishActionsOutdated', { files: outdated.join(', ') }))
  const repoName = status.repository.github_repository?.split('/')[1]
  if (repoName && repoName.toLowerCase() !== `n.e.k.o_plugin_${status.plugin_id}`.toLowerCase()) {
    result.push(message('publishNameNote', { id: status.plugin_id }))
  }
  return result
}

function remoteWarning(remote: PublishRemoteState | null): FlowMessage | null {
  if (!remote) return null
  if (remote.rate_limited) return message('publishRateLimited')
  return remote.reachable ? null : message('publishRemoteUnreachable')
}

const hard = (step: PublishPageId | null, reason: FlowMessage): ActionBlocker => ({
  severity: 'hard',
  step,
  reason,
})
const soft = (step: PublishPageId | null, reason: FlowMessage): ActionBlocker => ({
  severity: 'soft',
  step,
  reason,
})

interface BlockerFacts {
  status: PublishStatus
  submission: PublishFlowFacts['submission']
  approved: boolean
  releaseReady: boolean
  /** The Market already answered for this tag, or lists it. */
  marketDone: boolean
}

/**
 * The tag-and-Release modes. Hard: the server gate (`_local_gate`,
 * `_remote_release_blockers`) refuses, the Release already exists, or the
 * Market would refuse the notification after the tag is pushed. Soft: the
 * Release works but is unlikely to be listed at once.
 */
function githubBlockers(facts: BlockerFacts, notify: boolean): ActionBlocker[] {
  const { status, submission, approved, releaseReady } = facts
  const repo = status.repository
  const remote = status.remote
  const result: ActionBlocker[] = []
  const local = localRepositoryProblem(status)
  if (local) result.push(hard('repository', local))
  else if (remote?.visibility === 'private' || remote?.visibility === 'not_found') {
    result.push(hard('repository', message('publishRepoNotPublic')))
  }
  if (remote?.tag === 'elsewhere') {
    result.push(hard('release', message('publishRemoteTagElsewhere', { tag: repo.tag })))
  }
  if (releaseReady) result.push(hard('market', message('publishBlockedReleaseExists', { tag: repo.tag })))
  if (!approved) {
    result.push(
      notify
        ? hard('submission', message('publishBlockedNotApproved'))
        : soft('submission', message('publishSoftNotApproved'))
    )
  }
  if (remote && remote.verify !== 'success' && remote.verify !== 'unknown') {
    result.push(soft('verify', message(VERIFY_DETAILS[remote.verify])))
  }
  const head = repo.head
  const resolved = submission.resolvedCommit
  if (approved && head && resolved && head.toLowerCase() !== resolved.toLowerCase()) {
    result.push(
      soft(null, message('publishReleaseCommitDiffers', {
        head: shortCommit(head),
        approved: shortCommit(resolved),
      }))
    )
  }
  return result
}

/** The Market-only mode: the server needs a GitHub origin and a complete Release; the Market an approval. */
function marketBlockers(facts: BlockerFacts): ActionBlocker[] {
  const { status, approved, releaseReady, marketDone } = facts
  const repo = status.repository
  const result: ActionBlocker[] = []
  if (!repo.is_repository || !repo.github_repository) {
    result.push(hard('repository', localRepositoryProblem(status) ?? message('publishRepoNoGithub')))
  }
  if (!approved) result.push(hard('submission', message('publishBlockedNotApproved')))
  if (!releaseReady) {
    const release = releaseState(status.remote, repo.tag)
    result.push(hard('release', release.detail ?? message('publishRemoteUnknown')))
  }
  if (marketDone) result.push(soft(null, message('publishSoftAlreadyAnswered', { tag: repo.tag })))
  return result
}

const bySeverity = (blockers: ActionBlocker[]) => [
  ...blockers.filter((item) => item.severity === 'hard'),
  ...blockers.filter((item) => item.severity === 'soft'),
]

/**
 * Step 0. Required: git (the plugin's own repository, commits need an author),
 * uv (`uvx` runs the pinned ruff in checks and `publish`). gh is recommended:
 * it creates the repository and signs GitHub reads in; without it those are done by hand.
 */
function toolchainPage(toolchain: Toolchain | null | undefined): ToolchainPage {
  const check = (
    id: ToolId,
    ready: boolean | undefined,
    value: string | null | undefined,
    required: boolean
  ): ToolCheck => ({
    id,
    state: ready === undefined ? 'unknown' : ready ? 'ok' : 'missing',
    value: ready ? (value ?? null) : null,
    required,
  })
  const git = toolchain?.git
  const gh = toolchain?.gh
  const identityReady = git ? git.installed && !!git.user_name && !!git.user_email : undefined
  const tools = [
    check('git', git?.installed, git?.version, true),
    check('gitIdentity', identityReady, `${git?.user_name} <${git?.user_email}>`, true),
    check('uv', toolchain?.uv.installed, toolchain?.uv.version, true),
    check('gh', gh?.installed, gh?.version, false),
    check('githubAccount', gh ? gh.authenticated : undefined, gh?.login, false),
  ]
  const state: PublishStepState = !toolchain
    ? 'unknown'
    : tools.some((tool) => tool.required && tool.state === 'missing')
      ? 'todo'
      : 'done'
  return { state, tools }
}

/** A required tool that is missing makes every publish mode fail on the server or in the CLI. */
function toolchainBlockers(page: ToolchainPage, mode: PublishMode): ActionBlocker[] {
  // Notifying the Market runs no ruff, so it does not need uv.
  const needed: ToolId[] = mode === 'market_notify' ? ['git'] : ['git', 'uv']
  return page.tools
    .filter((tool) => needed.includes(tool.id) && tool.state === 'missing')
    .map((tool) => hard('toolchain', message('publishToolMissingBlocker', { tool: TOOL_NAMES[tool.id] })))
}

/** Product names, not translated. */
export const TOOL_NAMES: Record<ToolId, string> = {
  git: 'Git',
  gitIdentity: 'Git user.name / user.email',
  uv: 'uv',
  gh: 'GitHub CLI (gh)',
  githubAccount: 'GitHub',
}

/** Each later step that is not done yet points at the current page. */
function prerequisites(
  steps: PublishStep[],
  current: PublishPageId | null
): Partial<Record<PublishStepId, PublishPageId>> {
  const result: Partial<Record<PublishStepId, PublishPageId>> = {}
  if (!current) return result
  const from = PUBLISH_PAGE_ORDER.indexOf(current)
  for (const step of steps) {
    if (PUBLISH_PAGE_ORDER.indexOf(step.id) > from && step.state !== 'done') result[step.id] = current
  }
  return result
}

export function publishFlow(facts: PublishFlowFacts): PublishFlow {
  const { status, submission } = facts
  const repo = status.repository
  const remote = status.remote
  const releaseReady = remote?.release.ready === true
  const approved = submission.phase === 'approved'
  const submissionState = submission.phase ? SUBMISSION_STATES[submission.phase] : 'todo'
  const answer = facts.marketReceipt
    ? marketPublicationAnswer(facts.marketReceipt)
    : marketAnswer(facts.task, repo.github_repository, repo.tag)
  const steps: PublishStep[] = [
    { id: 'repository', warning: null, ...repositoryStep(status) },
    { id: 'verify', warning: null, ...verifyStep(remote) },
    // The submission panel inside this step explains its own state.
    { id: 'submission', state: submissionState, detail: null, warning: null },
    { id: 'release', ...releaseStep(facts, releaseReady) },
    { id: 'market', warning: null, ...marketStep(facts.marketListed, answer, approved, releaseReady, repo.tag) },
  ]
  const tools = toolchainPage(facts.toolchain)
  // A missing required tool comes before every step; an unread toolchain does not.
  const current: { id: PublishPageId } | undefined =
    tools.state === 'todo'
      ? { id: 'toolchain' }
      : (steps.find((step) => step.state === 'todo') ?? steps.find((step) => step.state === 'unknown'))
  const blockerFacts: BlockerFacts = {
    status,
    submission,
    approved,
    releaseReady,
    marketDone: !!answer || facts.marketListed === true,
  }
  const blockers = (mode: PublishMode, own: ActionBlocker[]) =>
    bySeverity([...toolchainBlockers(tools, mode), ...own])
  return {
    steps,
    toolchain: tools,
    blockers: {
      github_and_market: blockers('github_and_market', githubBlockers(blockerFacts, true)),
      github: blockers('github', githubBlockers(blockerFacts, false)),
      market_notify: blockers('market_notify', marketBlockers(blockerFacts)),
    },
    prerequisites: prerequisites(steps, current?.id ?? null),
    currentStepId: current?.id ?? null,
    pushedHead: repo.head_pushed === true ? repo.head : null,
    canCreateRepository: repo.is_repository && !repo.github_repository && repo.origin_url === null,
    remoteWarning: remoteWarning(remote),
    notes: notes(status),
  }
}
