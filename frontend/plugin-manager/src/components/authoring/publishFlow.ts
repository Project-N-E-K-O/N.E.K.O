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
  type PublishRemoteState,
  type PublishStatus,
  type PublishTask,
} from '@/api/pluginProjects'
import type { SubmissionPanelState, SubmissionPhase } from '@/api/marketSubmissions'
import type { AuthoringMessageKey } from '@/i18n/authoring'

export type PublishStepId = 'repository' | 'verify' | 'submission' | 'release' | 'market'
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
  /** The latest publish task, or null when the project never published. */
  task: PublishTask | null
}

export interface PublishFlow {
  steps: PublishStep[]
  /** The first step to act on; an unknown one only when nothing is actionable. */
  currentStepId: PublishStepId | null
  /** HEAD once it is on its origin branch, so the Market can resolve it. */
  pushedHead: string | null
  /** No origin at all: the workbench can create the GitHub repository. */
  canCreateRepository: boolean
  /** The tag-and-Release modes (with or without the Market notification). */
  canPublishGithub: boolean
  canNotifyMarket: boolean
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

/** The Market's answer to the latest task's release notification, when it carries one. */
function marketAnswer(task: PublishTask | null, tag: string): Omit<PublishStep, 'id' | 'warning'> | null {
  if (!task?.market_status) return null
  // A run for an earlier version says nothing about this one. A failed full run keeps no URL.
  if (task.release_url && !task.release_url.endsWith(`/tag/${tag}`)) return null
  const candidate = task.market_candidate_id ?? ''
  switch (task.market_status) {
    case 'published':
    case 'already_published':
    case 'approved':
      return { state: 'done', detail: message('marketVersionListed') }
    case 'pending_review':
      return { state: 'waiting', detail: message('marketVersionPendingReview', { candidate }) }
    case 'superseded':
      return { state: 'waiting', detail: message('marketVersionSuperseded') }
    case 'rejected':
      return { state: 'todo', detail: message('marketVersionRejected', { candidate }) }
  }
}

function marketStep(
  answer: Omit<PublishStep, 'id' | 'warning'> | null,
  approved: boolean,
  releaseReady: boolean,
  tag: string
): Omit<PublishStep, 'id' | 'warning'> {
  if (answer) return answer
  if (!approved) return { state: 'waiting', detail: message('marketVersionStepDetailLater') }
  return releaseReady
    ? { state: 'todo', detail: message('marketVersionNotify', { tag }) }
    : { state: 'waiting', detail: message('marketVersionAwaitingRelease') }
}

/** The server gate for the github modes (`plugin_publish_tasks._local_gate`), mirrored. */
function localPublishGate(status: PublishStatus): boolean {
  const repo = status.repository
  return (
    repo.is_repository &&
    !!repo.github_repository &&
    repo.worktree_clean === true &&
    repo.head_pushed === true &&
    status.actions[RELEASE_WORKFLOW_PATH] === 'CURRENT' &&
    !(repo.tag_exists && repo.tag_at_head === false) &&
    status.remote?.visibility !== 'private'
  )
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

export function publishFlow(facts: PublishFlowFacts): PublishFlow {
  const { status, submission } = facts
  const repo = status.repository
  const remote = status.remote
  const releaseReady = remote?.release.state === 'published' && !remote.release.missing_assets.length
  const approved = submission.phase === 'approved'
  const submissionState = submission.phase ? SUBMISSION_STATES[submission.phase] : 'todo'
  const answer = marketAnswer(facts.task, repo.tag)
  const steps: PublishStep[] = [
    { id: 'repository', warning: null, ...repositoryStep(status) },
    { id: 'verify', warning: null, ...verifyStep(remote) },
    // The submission panel inside this step explains its own state.
    { id: 'submission', state: submissionState, detail: null, warning: null },
    { id: 'release', ...releaseStep(facts, releaseReady) },
    { id: 'market', warning: null, ...marketStep(answer, approved, releaseReady, repo.tag) },
  ]
  const current =
    steps.find((step) => step.state === 'todo') ?? steps.find((step) => step.state === 'unknown')
  return {
    steps,
    currentStepId: current?.id ?? null,
    pushedHead: repo.head_pushed === true ? repo.head : null,
    canCreateRepository: repo.is_repository && !repo.github_repository && repo.origin_url === null,
    canPublishGithub: approved && !releaseReady && localPublishGate(status),
    // Notifying again after an answer gets the same answer back.
    canNotifyMarket: approved && releaseReady && !answer && !!repo.github_repository,
    remoteWarning: remoteWarning(remote),
    notes: notes(status),
  }
}
