import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useConnectionStore } from '@/stores/connection'
import { API_BASE_URL } from '@/utils/constants'

export type RunStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'canceled'
  | 'timeout'
  | 'cancel_requested'

export interface RunError {
  code: string
  message: string
  details?: Record<string, any>
}

export interface RunRecord {
  run_id: string
  plugin_id: string
  entry_id: string
  status: RunStatus
  created_at: number
  updated_at: number
  task_id?: string | null
  trace_id?: string | null
  idempotency_key?: string | null
  started_at?: number | null
  finished_at?: number | null
  progress?: number | null
  stage?: string | null
  message?: string | null
  step?: number | null
  step_total?: number | null
  eta_seconds?: number | null
  metrics?: Record<string, any>
  cancel_requested?: boolean
  cancel_reason?: string | null
  cancel_requested_at?: number | null
  error?: RunError | null
  result_refs?: string[]
}

export type ExportType = 'text' | 'url' | 'binary_url' | 'binary' | 'json'

export interface ExportItem {
  export_item_id: string
  run_id: string
  type: ExportType
  created_at: number
  description?: string | null
  label?: string | null
  category?: string | null
  text?: string | null
  url?: string | null
  binary_url?: string | null
  binary?: string | null
  json?: any
  mime?: string | null
  metadata?: Record<string, any>
}

interface WsMsgBase {
  type: string
  [k: string]: any
}

function buildWsUrl(path: string): string {
  const base = API_BASE_URL || ''
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  if (!base || base.startsWith('/')) {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const prefix = base ? base.replace(/\/$/, '') : ''
    return `${proto}//${window.location.host}${prefix}${normalizedPath}`
  }
  if (base.startsWith('https://')) return base.replace('https://', 'wss://') + normalizedPath
  if (base.startsWith('http://')) return base.replace('http://', 'ws://') + normalizedPath
  if (base.startsWith('ws://') || base.startsWith('wss://')) return base + normalizedPath
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${base}${normalizedPath}`
}

export const useRunsStore = defineStore('runs', () => {
  const authStore = useAuthStore()
  const connectionStore = useConnectionStore()

  const ws = ref<WebSocket | null>(null)
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

  let rpcSeq = 0
  const inflight = new Map<string, { resolve: (v: any) => void; reject: (e: any) => void }>()

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

  function applyBusChange(evt: any) {
    if (!evt || typeof evt !== 'object') return
    const bus = evt.bus
    const payload = evt.payload
    if (bus === 'runs' && payload && typeof payload === 'object') {
      const id = payload.run_id
      if (typeof id === 'string' && id) {
        const prev = runsById.value[id]
        const merged = { ...(prev || {}), ...payload } as RunRecord
        setRun(merged)
      }
    }
    if (bus === 'export' && payload && typeof payload === 'object') {
      const runId = payload.run_id
      if (typeof runId === 'string' && runId) {
        // Just mark that exports changed; caller can call export.list.
        // We keep this lightweight to avoid big payloads over ws.
      }
    }
  }

  function ensureAuthCode(): string {
    const code = authStore.authCode
    if (!code || !/^[A-Z]{4}$/.test(code)) {
      throw new Error('auth code missing')
    }
    return code
  }

  async function connect(): Promise<void> {
    if (connected.value || connecting.value) return
    connecting.value = true
    lastError.value = null

    try {
      const code = ensureAuthCode()
      const url = buildWsUrl('/ws/admin')
      const sock = new WebSocket(url)
      ws.value = sock

      sock.onopen = () => {
        connected.value = true
        connecting.value = false
        try {
          connectionStore.markConnected()
        } catch (_) {
          // ignore
        }
        sock.send(JSON.stringify({ type: 'auth', code }))
      }

      sock.onmessage = (ev: MessageEvent) => {
        try {
          const msg = JSON.parse(String(ev.data || '{}')) as WsMsgBase
          if (!msg || typeof msg !== 'object') return

          if (msg.type === 'ping') {
            sock.send(JSON.stringify({ type: 'pong' }))
            return
          }

          if (msg.type === 'resp') {
            const id = msg.id
            if (typeof id === 'string' && inflight.has(id)) {
              const h = inflight.get(id)!
              inflight.delete(id)
              if (msg.ok) h.resolve(msg.result)
              else h.reject(new Error(String(msg.error || 'rpc error')))
            }
            return
          }

          if (msg.type === 'event' && msg.event === 'bus.change') {
            const data = msg.data
            if (!data || typeof data !== 'object') return
            applyBusChange(data)
            return
          }
        } catch (e) {
          // ignore
        }
      }

      sock.onerror = () => {
        lastError.value = 'ws error'
      }

      sock.onclose = (ev: CloseEvent) => {
        connected.value = false
        connecting.value = false
        ws.value = null

        for (const [id, h] of inflight.entries()) {
          inflight.delete(id)
          h.reject(new Error('ws closed'))
        }

        if (ev.code === 1008) {
          try {
            authStore.clearAuthCode()
          } catch (_) {
            // ignore
          }
          try {
            connectionStore.requireAuth(ev.reason || 'forbidden')
          } catch (_) {
            // ignore
          }
        }
        try {
          connectionStore.markDisconnected()
        } catch (_) {
          // ignore
        }
      }

      await new Promise<void>((resolve, reject) => {
        const t = window.setTimeout(() => {
          reject(new Error('ws connect timeout'))
        }, 5000)
        const check = () => {
          if (connected.value) {
            clearTimeout(t)
            resolve()
          } else if (!connecting.value && !connected.value) {
            clearTimeout(t)
            reject(new Error('ws connect failed'))
          } else {
            window.setTimeout(check, 50)
          }
        }
        check()
      })
    } catch (e: any) {
      connecting.value = false
      connected.value = false
      lastError.value = String(e?.message || e || 'connect failed')
      try {
        connectionStore.requireAuth(lastError.value)
      } catch (_) {
        // ignore
      }
      throw e
    }
  }

  function disconnect() {
    const sock = ws.value
    ws.value = null
    connected.value = false
    connecting.value = false
    if (sock) {
      try {
        sock.close(1000, '')
      } catch (_) {
        // ignore
      }
    }
  }

  async function rpc<T = any>(method: string, params: Record<string, any>): Promise<T> {
    if (!connected.value) {
      await connect()
    }
    const sock = ws.value
    if (!sock || sock.readyState !== WebSocket.OPEN) {
      throw new Error('ws not ready')
    }

    rpcSeq += 1
    const id = `rpc-${Date.now()}-${rpcSeq}`
    const payload = { type: 'req', id, method, params }

    const p = new Promise<T>((resolve, reject) => {
      inflight.set(id, { resolve, reject })
      window.setTimeout(() => {
        if (inflight.has(id)) {
          inflight.delete(id)
          reject(new Error('rpc timeout'))
        }
      }, 5000)
    })

    try {
      sock.send(JSON.stringify(payload))
    } catch (e) {
      inflight.delete(id)
      throw e
    }
    return p
  }

  async function refreshRuns(pluginId?: string) {
    const params: Record<string, any> = {}
    if (pluginId) params.plugin_id = pluginId
    const items = await rpc<RunRecord[]>('runs.list', params)
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
    const rec = await rpc<RunRecord>('run.get', { run_id: runId })
    if (rec && typeof rec === 'object') {
      setRun(rec)
    }
    return rec
  }

  async function loadExports(runId: string) {
    const out = await rpc<{ items: ExportItem[]; next_after?: string | null }>('export.list', {
      run_id: runId,
      after: null,
      limit: 500
    })
    const items = Array.isArray(out?.items) ? out.items : []
    exportsByRunId.value[runId] = items
    return items
  }

  function selectRun(runId: string | null) {
    selectedRunId.value = runId
  }

  async function cancelRun(runId: string, reason?: string) {
    const params: Record<string, any> = { run_id: runId }
    if (reason) params.reason = reason
    const rec = await rpc<RunRecord>('run.cancel', params)
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
