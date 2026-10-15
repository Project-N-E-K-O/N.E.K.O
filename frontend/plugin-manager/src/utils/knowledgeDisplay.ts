import {
  isActiveKnowledgeJob,
  type KnowledgeDiagnosticQuery,
  type KnowledgePackJob,
  type KnowledgeVectorState,
} from '@/api/knowledge'

export type KnowledgeTagType = 'success' | 'info' | 'warning' | 'danger' | 'primary'

/** Tag color for a lookup outcome; every failure kind gets a visible color. */
export function diagnosticResultTagType(result: string): KnowledgeTagType {
  switch (result) {
    case 'matched':
      return 'success'
    case 'timeout':
    case 'busy':
      return 'warning'
    case 'error':
    case 'unavailable':
      return 'danger'
    default:
      // miss, disabled and anything unrecognized
      return 'info'
  }
}

/**
 * Records shown in the diagnostics table. Only plain misses can be hidden;
 * timeouts, busy, errors, disabled and unavailable records always stay
 * visible so failures are never silently dropped.
 */
export function visibleDiagnosticQueries(
  queries: readonly KnowledgeDiagnosticQuery[],
  showMisses: boolean,
): KnowledgeDiagnosticQuery[] {
  return showMisses ? [...queries] : queries.filter((query) => query.result !== 'miss')
}

export function vectorStateTagType(state: KnowledgeVectorState | string): KnowledgeTagType {
  switch (state) {
    case 'complete':
      return 'success'
    case 'building':
      return 'primary'
    case 'partial':
      return 'warning'
    default:
      // none, waiting, paused, off
      return 'info'
  }
}

export function vectorProgressPercent(ready: unknown, total: unknown): number {
  const totalCount = Math.max(0, Number(total) || 0)
  if (totalCount <= 0) return 0
  const readyCount = Math.min(totalCount, Math.max(0, Number(ready) || 0))
  return Math.round((readyCount * 1000) / totalCount) / 10
}

export interface KnowledgeJobTransition {
  job: KnowledgePackJob
  outcome: 'active' | 'failed' | 'cancelled'
}

/**
 * Jobs that were queued/building in the previous snapshot and have now
 * finished. Jobs never seen running (already finished on first load) are not
 * reported, so reopening the page does not replay old toasts.
 */
export function finishedJobTransitions(
  previousStates: ReadonlyMap<string, string>,
  jobs: readonly KnowledgePackJob[],
): KnowledgeJobTransition[] {
  const transitions: KnowledgeJobTransition[] = []
  for (const job of jobs) {
    const before = previousStates.get(job.job_id)
    if (before !== 'queued' && before !== 'building') continue
    if (job.state === 'active' || job.state === 'failed' || job.state === 'cancelled') {
      transitions.push({ job, outcome: job.state })
    }
  }
  return transitions
}

export function jobStateSnapshot(jobs: readonly KnowledgePackJob[]): Map<string, string> {
  return new Map(jobs.map((job) => [job.job_id, job.state]))
}

export const KNOWLEDGE_JOB_POLL_MS = 2_000
export const KNOWLEDGE_VECTOR_POLL_MS = 10_000
export const KNOWLEDGE_STARTING_POLL_MS = 3_000

/**
 * Delay before the next background refresh, or null to stop polling.
 * Import jobs poll fast. A service that is still starting is polled on every
 * tab, since every tab's data waits for it. Other background progress
 * (embedding model loading, vectors building) polls slowly and only while a
 * tab that shows that progress is open.
 */
export function nextKnowledgePollDelay(options: {
  jobs: readonly Pick<KnowledgePackJob, 'state'>[]
  backgroundProgress: boolean
  showsVectorProgress: boolean
  serviceStarting?: boolean
}): number | null {
  if (options.jobs.some(isActiveKnowledgeJob)) return KNOWLEDGE_JOB_POLL_MS
  if (options.serviceStarting) return KNOWLEDGE_STARTING_POLL_MS
  if (options.backgroundProgress && options.showsVectorProgress) return KNOWLEDGE_VECTOR_POLL_MS
  return null
}
