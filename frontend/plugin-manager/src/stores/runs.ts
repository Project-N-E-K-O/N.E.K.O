import { defineStore } from 'pinia'
import { computed, onScopeDispose, ref } from 'vue'
import { createRunsClient, type RunRecord, type ExportItem } from '@/api/runs'
import { RunsRequestError } from '@/utils/runsError'

export type { RunStatus, RunError, RunRecord, ExportType, ExportItem } from '@/api/runs'

export const useRunsStore = defineStore('runs', () => {
  let stateVersion = 0
  const connected = ref(false)
  const connecting = ref(false)
  const lastError = ref<string | null>(null)

  const runsById = ref<Record<string, RunRecord>>({})
  const runOrder = ref<string[]>([])

  const selectedRunId = ref<string | null>(null)
  const exportsByRunId = ref<Record<string, ExportItem[]>>({})

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

  function setRun(rec: RunRecord) {
    const id = rec.run_id
    runsById.value[id] = rec
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
        const prev = runsById.value[id]
        const merged = { ...(prev || {}), ...payload } as RunRecord
        setRun(merged)
      }
    }
    if (bus === 'export' && payload && typeof payload === 'object') {
      const runId = (payload as Record<string, unknown>).run_id
      if (typeof runId === 'string' && runId) {
        // Just mark that exports changed; caller can call export.list.
        // We keep this lightweight to avoid big payloads over ws.
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
    const version = stateVersion
    const items = await pending
    assertCurrentVersion(version)
    if (Array.isArray(items)) {
      // newest first: sort by updated_at desc
      const sorted = [...items].sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0))
      const nextOrder: string[] = []
      const nextMap: Record<string, RunRecord> = { ...runsById.value }
      for (const r of sorted) {
        if (!r || typeof r !== 'object') continue
        nextOrder.push(r.run_id)
        nextMap[r.run_id] = r
      }
      runOrder.value = nextOrder
      runsById.value = nextMap
    }
  }

  async function loadRun(runId: string) {
    const pending = client.getRun(runId)
    const version = stateVersion
    const rec = await pending
    assertCurrentVersion(version)
    if (rec && typeof rec === 'object') {
      setRun(rec)
    }
    return rec
  }

  async function loadExports(runId: string) {
    const pending = client.listExports(runId)
    const version = stateVersion
    const out = await pending
    assertCurrentVersion(version)
    const items = Array.isArray(out?.items) ? out.items : []
    exportsByRunId.value[runId] = items
    return items
  }

  function selectRun(runId: string | null) {
    selectedRunId.value = runId
  }

  async function cancelRun(runId: string, reason?: string) {
    const pending = client.cancelRun(runId, reason)
    const version = stateVersion
    const rec = await pending
    assertCurrentVersion(version)
    if (rec && typeof rec === 'object') {
      setRun(rec)
    }
    return rec
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
    connect,
    disconnect,
    refreshRuns,
    loadRun,
    loadExports,
    selectRun,
    cancelRun
  }
})
