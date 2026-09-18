/**
 * One in-flight Market install/upgrade task, shared by every UI surface.
 *
 * The Market panel dialog and the update float window drive the same backend
 * endpoints (``POST /market/install`` + ``GET /market/tasks/{id}``). Keeping the
 * polling here means the two entry points mutually exclude each other instead
 * of racing, and the progress UI is fed by one derivation rather than two.
 *
 * Everything the UI needs beyond the raw task is derived here, because the REST
 * API deliberately does not carry it:
 *
 *   * the plugin name / action / version transition — only the caller knows
 *     them, so they arrive as ``MarketInstallContext``;
 *   * download speed and ETA — sampled from ``downloaded_bytes`` every poll;
 *   * the step checklist — the backend keeps ``stage`` at the step where it
 *     failed, but overwrites it with ``"canceled"`` on cancel, so the last
 *     running step is remembered locally.
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { fetchBridge } from '@/api/marketBridge'
import { resolvePluginInstallErrorKey } from '@/utils/pluginInstallError'

const LOG_PREFIX = '[market-install]'
const POLL_INTERVAL_MS = 800
/** Consecutive 404s before we accept that the backend task is gone. */
const TASK_MISSING_TOLERANCE = 15
/** Past this only the copy changes — the task is still running server-side. */
const OVERTIME_MS = 3 * 60 * 1000
/** Sliding window for the speed estimate. */
const SPEED_WINDOW_MS = 4000

const log = {
  info: (...args: unknown[]) => console.info(LOG_PREFIX, ...args),
  warn: (...args: unknown[]) => console.warn(LOG_PREFIX, ...args),
}

export type MarketInstallMode = 'install' | 'upgrade' | 'reinstall' | 'override_builtin'

export interface MarketInstallContext {
  pluginId: string
  name: string
  mode: MarketInstallMode
  channel?: 'stable' | 'beta' | null
  /** Version currently on disk — absent for a fresh install. */
  fromVersion?: string | null
  /** Version being installed. */
  toVersion?: string | null
}

export interface MarketInstallTask {
  task_id: string
  status: string
  stage: string
  progress?: number
  message?: string
  downloaded_bytes?: number
  total_bytes?: number | null
  error?: string | null
  error_code?: string | null
  cancel_requested?: boolean
  rollback?: {
    prepared?: boolean
    restored?: boolean
    running?: boolean
    cause_code?: string
  } | null
}

export type InstallStepId = 'download' | 'verify' | 'install' | 'replace' | 'rollback' | 'completed'
export type InstallStepState = 'done' | 'active' | 'failed' | 'pending'

export interface InstallStep {
  id: InstallStepId
  labelKey: string
  state: InstallStepState
}

export interface TrackOutcome {
  ok: boolean
  errorKey?: string
  canceled?: boolean
  /** Tracking was dropped (``dismiss`` / a newer task). Not a failure. */
  aborted?: boolean
}

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'canceled'])

/** Canonical pipeline order — lets install and replace modes be compared. */
const CANONICAL_ORDER: InstallStepId[] = [
  'download',
  'verify',
  'install',
  'replace',
  'rollback',
  'completed',
]

const STEPS_BY_MODE: Record<MarketInstallMode, InstallStepId[]> = {
  install: ['download', 'verify', 'install'],
  override_builtin: ['download', 'verify', 'install'],
  upgrade: ['download', 'verify', 'replace'],
  reinstall: ['download', 'verify', 'replace'],
}

const STEP_LABEL_KEYS: Record<InstallStepId, string> = {
  download: 'market.installStep.download',
  verify: 'market.installStep.verify',
  install: 'market.installStep.install',
  replace: 'market.installStep.replace',
  rollback: 'market.installStep.rollback',
  completed: 'market.installStep.completed',
}

/** The backend ``stage`` only ever takes these values. */
function stageToStep(stage: string | null | undefined): InstallStepId | null {
  switch (stage) {
    case 'download': return 'download'
    case 'verify': return 'verify'
    case 'install': return 'install'
    case 'replace': return 'replace'
    case 'rollback': return 'rollback'
    case 'completed': return 'completed'
    default: return null
  }
}

function orderOf(step: InstallStepId): number {
  return CANONICAL_ORDER.indexOf(step)
}

function formatByteCount(value: number): string {
  if (value >= 1024 * 1024 * 1024) return `${(value / (1024 * 1024 * 1024)).toFixed(1)} GB`
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${value} B`
}

interface ByteSample {
  at: number
  bytes: number
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

export const useMarketInstallTaskStore = defineStore('marketInstallTask', () => {
  const task = ref<MarketInstallTask | null>(null)
  const context = ref<MarketInstallContext | null>(null)
  const taskId = ref<string | null>(null)
  const cancelling = ref(false)
  const overtime = ref(false)
  const detailsExpanded = ref(false)
  /** Bytes/s over the last few samples; null while unknown or not downloading. */
  const speed = ref<number | null>(null)
  /** Seconds remaining; only when the server sent a content length. */
  const eta = ref<number | null>(null)

  // Sticky per-task state. Plain locals on purpose: they must not re-render.
  let samples: ByteSample[] = []
  let peakProgress = 0
  /**
   * Highest step reached. Sticky so a mirror-fallback retry, which re-downloads
   * from the beginning, cannot walk the checklist backwards.
   */
  let furthestStep: InstallStepId | null = null
  /** Last step seen while running — cancel overwrites ``stage``. */
  let lastRunningStep: InstallStepId | null = null
  let failedStep: InstallStepId | null = null
  let missingPolls = 0
  let overtimeTimer: ReturnType<typeof setTimeout> | null = null
  /** Bumped per tracking run so an abandoned poll loop cannot touch fresh state. */
  let generation = 0

  const running = computed(() => {
    const status = task.value?.status
    return !!status && !TERMINAL_STATUSES.has(status)
  })

  const done = computed(() => !!task.value && TERMINAL_STATUSES.has(task.value.status))

  /** Monotonic: a retry must not drag the bar backwards. */
  const percent = computed(() => (
    Math.round(Math.max(peakProgress, task.value?.progress ?? 0) * 100)
  ))

  const barStatus = computed<'success' | 'exception' | undefined>(() => {
    const status = task.value?.status
    if (status === 'failed') return 'exception'
    if (status === 'completed') return 'success'
    return undefined
  })

  const stageLabelKey = computed(() => {
    // Progressive copy ("Downloading") for the running state; the checklist
    // below uses the noun form ("Download") because a finished row must not
    // read as still-in-progress.
    const step = stageToStep(task.value?.stage) ?? lastRunningStep
    return step ? `market.installStage.${step}` : 'market.installStage.pending'
  })

  const errorKey = computed(() => (
    task.value?.status === 'failed'
      ? resolvePluginInstallErrorKey(task.value.error_code)
      : null
  ))

  const rollback = computed(() => task.value?.rollback ?? null)

  /** ``8.5 MB / 25.0 MB · 3.2 MB/s · 5s`` — the trailing parts appear as soon
   *  as they become knowable. */
  const transferText = computed(() => {
    const current = task.value
    if (!current || current.stage !== 'download') return ''
    const downloaded = current.downloaded_bytes ?? 0
    const parts = [
      current.total_bytes
        ? `${formatByteCount(downloaded)} / ${formatByteCount(current.total_bytes)}`
        : formatByteCount(downloaded),
    ]
    if (speed.value) parts.push(`${formatByteCount(speed.value)}/s`)
    if (eta.value !== null && eta.value > 0) parts.push(`${eta.value}s`)
    return parts.join(' · ')
  })

  const steps = computed<InstallStep[]>(() => {
    const current = task.value
    const ctx = context.value
    if (!current || !ctx) return []

    // Union of the mode's canonical pipeline and whatever the backend actually
    // reported, sorted back into pipeline order. Takes care of the optional
    // rollback step without a special case, and keeps the checklist honest if a
    // stage outside the mode's usual order ever shows up.
    const ids = new Set<InstallStepId>(STEPS_BY_MODE[ctx.mode] ?? STEPS_BY_MODE.install)
    const observed = stageToStep(current.stage)
    if (observed && observed !== 'completed') ids.add(observed)
    if (furthestStep && furthestStep !== 'completed') ids.add(furthestStep)
    // The backend marks the replacement transaction as rollback-capable before
    // it flips ``stage``, so a prepared rollback belongs in the list already.
    if (current.rollback?.prepared) ids.add('rollback')

    const ordered = [...ids].sort((a, b) => orderOf(a) - orderOf(b))
    ordered.push('completed')

    // The active step is the furthest one the pipeline actually contains —
    // comparing by order rather than index keeps this correct when the list
    // grows a step mid-flight.
    const furthest = furthestStep ? orderOf(furthestStep) : -1
    const failed = failedStep ? orderOf(failedStep) : -1
    let active = -1
    for (const id of ordered) {
      const order = orderOf(id)
      if (order <= furthest && order > active) active = order
    }

    return ordered.map((id) => {
      const order = orderOf(id)
      let state: InstallStepState
      if (current.status === 'failed' && failed >= 0) {
        state = order === failed ? 'failed' : order < failed ? 'done' : 'pending'
      } else if (current.status === 'completed') {
        state = 'done'
      } else if (order < active) {
        state = 'done'
      } else if (order === active) {
        state = 'active'
      } else {
        state = 'pending'
      }
      return { id, labelKey: STEP_LABEL_KEYS[id], state }
    })
  })

  // ─── internals ───────────────────────────────────────────────────────────

  function clearOvertimeTimer(): void {
    if (overtimeTimer !== null) {
      clearTimeout(overtimeTimer)
      overtimeTimer = null
    }
  }

  function beginTracking(id: string, ctx: MarketInstallContext): number {
    generation += 1
    taskId.value = id
    task.value = { task_id: id, status: 'pending', stage: 'pending', progress: 0 }
    samples = []
    peakProgress = 0
    furthestStep = null
    lastRunningStep = null
    failedStep = null
    missingPolls = 0
    speed.value = null
    eta.value = null
    overtime.value = false
    cancelling.value = false
    clearOvertimeTimer()
    const myGeneration = generation
    overtimeTimer = setTimeout(() => {
      overtimeTimer = null
      if (myGeneration === generation) overtime.value = true
    }, OVERTIME_MS)
    context.value = ctx
    log.info('tracking', { taskId: id, pluginId: ctx.pluginId, mode: ctx.mode })
    return myGeneration
  }

  function observe(now: number, current: MarketInstallTask): void {
    if ((current.progress ?? 0) > peakProgress) peakProgress = current.progress ?? 0

    const step = stageToStep(current.stage)
    if (step && orderOf(step) > (furthestStep ? orderOf(furthestStep) : -1)) {
      furthestStep = step
    }

    if (current.stage !== 'download') {
      // The byte counter stops being meaningful outside the download phase.
      samples = []
      speed.value = null
      eta.value = null
      return
    }

    const bytes = current.downloaded_bytes ?? 0
    const previous = samples[samples.length - 1]
    if (previous && bytes < previous.bytes) {
      // The backend zeroes the counter when it retries through GitHub direct.
      samples = []
      speed.value = null
      eta.value = null
    }

    samples.push({ at: now, bytes })
    while (samples.length > 1) {
      const oldest = samples[0]
      if (!oldest || now - oldest.at <= SPEED_WINDOW_MS) break
      samples.shift()
    }

    const first = samples[0]
    if (samples.length < 2 || !first) return
    const elapsedSeconds = (now - first.at) / 1000
    if (elapsedSeconds <= 0) return

    const bytesPerSecond = (bytes - first.bytes) / elapsedSeconds
    speed.value = bytesPerSecond > 0 ? bytesPerSecond : null
    eta.value = speed.value && current.total_bytes
      ? Math.max(0, Math.round((current.total_bytes - bytes) / speed.value))
      : null
  }

  // ─── public API ──────────────────────────────────────────────────────────

  /** Poll ``id`` to a terminal state. Resolves with the outcome; the reactive
   *  state stays populated afterwards so the caller can report success/failure
   *  until it calls :func:`dismiss`. */
  async function track(id: string, ctx: MarketInstallContext): Promise<TrackOutcome> {
    if (running.value) {
      log.warn('refused: a task is already being tracked', {
        tracked: taskId.value,
        incoming: id,
      })
      return { ok: false, errorKey: 'market.installAlreadyRunning' }
    }

    const myGeneration = beginTracking(id, ctx)

    for (;;) {
      await sleep(POLL_INTERVAL_MS)
      if (myGeneration !== generation) {
        return { ok: false, aborted: true }
      }

      let res: Response | null = null
      try {
        res = await fetchBridge(`/market/tasks/${id}`)
      } catch (err) {
        log.warn('task poll threw', { taskId: id, err })
      }
      if (!res) {
        // Bridge temporarily unreachable: keep polling, it usually comes back.
        log.warn('task poll has no bridge', { taskId: id })
        continue
      }
      if (res.status === 401 || res.status === 403) {
        log.warn('task poll rejected', { taskId: id, status: res.status })
        return { ok: false, errorKey: 'market.pairRequired' }
      }
      if (res.status === 404) {
        missingPolls += 1
        if (missingPolls >= TASK_MISSING_TOLERANCE) {
          log.warn('task disappeared before reaching a terminal state', { taskId: id })
          return { ok: false, errorKey: 'market.installTaskLost' }
        }
        continue
      }
      if (!res.ok) {
        log.warn('task poll error', { taskId: id, status: res.status })
        continue
      }

      missingPolls = 0
      const current = (await res.json().catch(() => null)) as MarketInstallTask | null
      if (!current?.status) {
        log.warn('task poll returned no status', { taskId: id })
        continue
      }

      task.value = current
      observe(performance.now(), current)

      if (current.status === 'completed') {
        log.info('task completed', { taskId: id, pluginId: ctx.pluginId })
        return { ok: true }
      }
      if (current.status === 'failed') {
        failedStep = stageToStep(current.stage) ?? lastRunningStep
        log.warn('task failed', {
          taskId: id,
          pluginId: ctx.pluginId,
          stage: current.stage,
          errorCode: current.error_code,
          detail: current.error,
        })
        return { ok: false, errorKey: resolvePluginInstallErrorKey(current.error_code) }
      }
      if (current.status === 'canceled') {
        log.info('task canceled', { taskId: id, pluginId: ctx.pluginId })
        return { ok: false, errorKey: 'market.installCancelled', canceled: true }
      }

      const step = stageToStep(current.stage)
      if (step) lastRunningStep = step
    }
  }

  async function cancel(): Promise<'ok' | 'unavailable' | 'failed'> {
    const id = taskId.value
    if (!id || done.value || cancelling.value) return 'unavailable'

    cancelling.value = true
    try {
      const res = await fetchBridge(`/market/tasks/${id}/cancel`, { method: 'POST' })
      if (!res) return 'failed'
      if (res.ok) {
        task.value = (await res.json()) as MarketInstallTask
        return 'ok'
      }
      // 409 = already inside a stage that cannot be torn down safely.
      if (res.status === 409) return 'unavailable'
      log.warn('cancel rejected', { taskId: id, status: res.status })
      return 'failed'
    } catch (err) {
      log.warn('cancel failed', { taskId: id, err })
      return 'failed'
    } finally {
      cancelling.value = false
    }
  }

  function dismiss(): void {
    generation += 1
    clearOvertimeTimer()
    taskId.value = null
    task.value = null
    context.value = null
    samples = []
    peakProgress = 0
    furthestStep = null
    lastRunningStep = null
    failedStep = null
    missingPolls = 0
    speed.value = null
    eta.value = null
    overtime.value = false
    cancelling.value = false
  }

  function toggleDetails(): void {
    detailsExpanded.value = !detailsExpanded.value
  }

  return {
    task,
    context,
    taskId,
    cancelling,
    overtime,
    detailsExpanded,
    // `speed` / `eta` are the raw derivations; `transferText` only formats them.
    // Kept public so the sampling maths can be asserted without string parsing.
    speed,
    eta,
    running,
    done,
    percent,
    barStatus,
    stageLabelKey,
    errorKey,
    rollback,
    transferText,
    steps,
    track,
    cancel,
    dismiss,
    toggleDetails,
  }
})
