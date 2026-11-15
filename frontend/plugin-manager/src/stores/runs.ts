import { defineStore } from 'pinia'
import { computed, onScopeDispose, ref } from 'vue'
import { createRunsClient, type RunRecord, type ExportItem } from '@/api/runs'
import { RunsRequestError } from '@/utils/runsError'
import { createStaleResponseGuard } from '@/utils/staleResponseGuard'

export type { RunStatus, RunError, RunRecord, ExportType, ExportItem } from '@/api/runs'

export const useRunsStore = defineStore('runs', () => {
  let stateVersion = 0
  const results = createStaleResponseGuard<string>()
  // Live events and commands arbitrate independently of read responses.
  const liveResults = createStaleResponseGuard<string>()
  const connected = ref(false)
  const connecting = ref(false)
  const lastError = ref<string | null>(null)

  const runsById = ref<Record<string, RunRecord>>(Object.create(null))
  const runOrder = ref<string[]>([])

  const selectedRunId = ref<string | null>(null)
  const exportsByRunId = ref<Record<string, ExportItem[]>>(Object.create(null))
  const exportRevisions = ref<Record<string, number>>(Object.create(null))
  const runRevisions = ref<Record<string, number>>(Object.create(null))
  const terminalStatuses = new Set(['succeeded', 'failed', 'canceled', 'timeout'])

  const runs = computed(() => {
    const ids = runOrder.value
    const all = runsById.value
    return ids
      .map(id => all[id])
      .filter(Boolean)
  })

  const selectedRun = computed(() => {
    if (!selectedRunId.value) return null
    return runsById.value[selectedRunId.value] || null
  })

  const selectedExports = computed(() => {
    if (!selectedRunId.value) return []
    return exportsByRunId.value[selectedRunId.value] || []
  })

  function preserveAcknowledgedState(rec: RunRecord, current?: RunRecord): RunRecord {
    if (!current || terminalStatuses.has(rec.status)) return rec
    // A run cannot leave a terminal state or resume after cancellation was
    // acknowledged. Delayed progress/snapshots can still fill other fields.
    if (terminalStatuses.has(current.status)) return { ...rec, ...current }
    if (current.status !== 'cancel_requested' || rec.status === 'cancel_requested') return rec
    return {
      ...rec,
      status: current.status,
      cancel_requested: current.cancel_requested ?? true,
      cancel_reason: current.cancel_reason,
      cancel_requested_at: current.cancel_requested_at,
    }
  }

  function setRun(rec: RunRecord, ticket = results.begin()) {
    const id = rec.run_id
    if (!results.accept(`run:${id}`, ticket)) {
      // A partial live event may arrive before its older full read. Fill the
      // missing fields without replacing anything the newer event published.
      if (runsById.value[id]) runsById.value[id] = { ...rec, ...runsById.value[id] }
      return
    }
    runsById.value[id] = preserveAcknowledgedState(rec, runsById.value[id])
    if (!runOrder.value.includes(id)) {
      runOrder.value = [id, ...runOrder.value]
    }
    runOrder.value = [...runOrder.value].sort(
      (a, b) => (runsById.value[b]?.updated_at || 0) - (runsById.value[a]?.updated_at || 0)
    )
  }

  function applyBusChange(evt: unknown) {
    if (!evt || typeof evt !== 'object') return
    const { bus, payload } = evt as Record<string, unknown>
    if (bus === 'runs' && payload && typeof payload === 'object') {
      const id = (payload as Record<string, unknown>).run_id
      if (typeof id === 'string' && id) {
        runRevisions.value[id] = (runRevisions.value[id] ?? 0) + 1
        liveResults.accept(id, liveResults.begin())
        const prev = runsById.value[id]
        const merged = { ...(prev || {}), ...payload } as RunRecord
        setRun(merged)
      }
    }
    if (bus === 'export' && payload && typeof payload === 'object') {
      const runId = (payload as Record<string, unknown>).run_id
      if (typeof runId === 'string' && runId) {
        results.accept(`output:${runId}`, results.begin())
        exportRevisions.value[runId] = (exportRevisions.value[runId] ?? 0) + 1
      }
    }
  }

  const client = createRunsClient({
    onConnectionState(state) {
      // Invalidate cached writes from requests that finished as this page disconnected.
      if (!state.connected && !state.connecting) stateVersion++
      connected.value = state.connected
      connecting.value = state.connecting
      lastError.value = state.lastError
    },
    onBusChange: applyBusChange,
  })
  const { connect, disconnect } = client
  onScopeDispose(disconnect)

  function assertCurrentVersion(version: number) {
    if (version !== stateVersion) throw new RunsRequestError('cancelled')
  }

  async function refreshRuns(pluginId?: string) {
    const pending = client.listRuns(pluginId)
    const ticket = results.begin()
    const version = stateVersion
    const items = await pending
    assertCurrentVersion(version)
    if (!Array.isArray(items)) throw new RunsRequestError('request_failed')
    if (results.accept('list', ticket)) {
      const ids = new Set<string>()
      const nextMap: Record<string, RunRecord> = Object.assign(Object.create(null), runsById.value)
      for (const rec of items) {
        if (!rec || typeof rec.run_id !== 'string' || !rec.run_id) continue
        ids.add(rec.run_id)
        nextMap[rec.run_id] = results.accept(`run:${rec.run_id}`, ticket)
          ? preserveAcknowledgedState(rec, nextMap[rec.run_id]) : { ...rec, ...nextMap[rec.run_id] }
      }
      // A late list cannot hide records published while that list was pending.
      for (const id of runOrder.value) {
        if (!ids.has(id) && !results.accept(`run:${id}`, ticket)) ids.add(id)
      }
      runsById.value = nextMap
      runOrder.value = [...ids].sort((a, b) => (nextMap[b]?.updated_at || 0) - (nextMap[a]?.updated_at || 0))
    }
  }

  async function loadRun(runId: string) {
    const pending = client.getRun(runId)
    const ticket = results.begin()
    const version = stateVersion
    const rec = await pending
    assertCurrentVersion(version)
    if (!rec || typeof rec !== 'object' || rec.run_id !== runId) throw new RunsRequestError('request_failed')
    setRun(rec, ticket)
    return runsById.value[runId] ?? rec
  }

  async function loadExports(runId: string) {
    const pending = client.listExports(runId)
    const ticket = results.begin()
    const version = stateVersion
    const out = await pending
    assertCurrentVersion(version)
    if (!out || !Array.isArray(out.items)) throw new RunsRequestError('request_failed')
    const items = out.items
    if (results.accept(`output:${runId}`, ticket)) {
      exportsByRunId.value[runId] = items
      return items
    }
    // Undefined means no accepted snapshot exists yet, not an empty result.
    return exportsByRunId.value[runId]
  }

  function selectRun(runId: string | null) {
    selectedRunId.value = runId
  }

  async function cancelRun(runId: string, reason?: string) {
    const pending = client.cancelRun(runId, reason)
    const ticket = liveResults.begin()
    const version = stateVersion
    const rec = await pending
    assertCurrentVersion(version)
    if (!rec || typeof rec !== 'object' || rec.run_id !== runId) throw new RunsRequestError('outcome_unknown')
    const current = runsById.value[runId]
    const hasNewerLiveState = !liveResults.accept(runId, ticket) && !!current
    const result = hasNewerLiveState ? { ...rec, ...current } : rec
    if (hasNewerLiveState && !terminalStatuses.has(current.status)) {
      // Progress published while the command was pending is newer metadata,
      // but cannot negate the command's acknowledged lifecycle transition.
      result.status = rec.status
      result.cancel_requested = rec.cancel_requested
      result.cancel_reason = rec.cancel_reason
      result.cancel_requested_at = rec.cancel_requested_at
    }
    // Publish at acknowledgement time so reads begun during the command
    // cannot subsequently undo its result.
    setRun(result)
    return runsById.value[runId] ?? rec
  }

  return {
    connected,
    connecting,
    lastError,
    runs,
    runsById,
    selectedRunId,
    selectedRun,
    selectedExports,
    exportRevisions,
    runRevisions,
    connect,
    disconnect,
    refreshRuns,
    loadRun,
    loadExports,
    selectRun,
    cancelRun
  }
})
