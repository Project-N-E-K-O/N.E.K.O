import { post } from './index'
import { API_BASE_URL } from '@/utils/constants'
import { RunsRequestError } from '@/utils/runsError'

export function createRun(payload: {
  plugin_id: string
  entry_id: string
  args: Record<string, unknown>
  task_id?: string
}): Promise<{ run_id: string; status: string; run_token?: string; expires_at?: number }> {
  return post('/runs', payload)
}

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
  details?: Record<string, unknown>
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
  metrics?: Record<string, unknown>
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
  json?: unknown
  mime?: string | null
  metadata?: Record<string, unknown>
}

interface WsMsgBase {
  type: string
  [k: string]: unknown
}

interface PendingRequest {
  method: string
  sent: boolean
  timer: number
  resolve: (value: unknown) => void
  reject: (error: RunsRequestError) => void
}

interface RunsConnection {
  socket: WebSocket
  ready: Promise<void>
  resolve: () => void
  reject: (error: RunsRequestError) => void
  opened: boolean
  terminalError: RunsRequestError | null
  timer: number | null
  pending: Map<string, PendingRequest>
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

export interface RunsConnectionState {
  connected: boolean
  connecting: boolean
  lastError: string | null
}

/** One run-history connection per consumer; no shared transport or auto-replayed commands. */
export function createRunsClient(handlers: {
  onConnectionState: (state: RunsConnectionState) => void
  onBusChange: (event: unknown) => void
}) {
  let activeConnection: RunsConnection | null = null
  let rpcSeq = 0

  function diagnose(connection: RunsConnection, error: RunsRequestError, details: Record<string, unknown> = {}) {
    const url = new URL(connection.socket.url, window.location.href)
    console.warn('[runs-ws]', {
      code: error.code, endpoint: `${url.origin}${url.pathname}`, ...details,
    })
  }

  function takeRequest(connection: RunsConnection, id: string): PendingRequest | undefined {
    const request = connection.pending.get(id)
    if (request) {
      connection.pending.delete(id)
      window.clearTimeout(request.timer)
    }
    return request
  }

  function endConnection(connection: RunsConnection, error: RunsRequestError) {
    if (activeConnection !== connection) return
    connection.terminalError = error
    activeConnection = null
    handlers.onConnectionState({ connected: false, connecting: false, lastError: error.code === 'cancelled' ? null : error.code })
    if (connection.timer !== null) window.clearTimeout(connection.timer)
    connection.timer = null
    const sock = connection.socket
    sock.onopen = sock.onmessage = sock.onclose = sock.onerror = null
    if (!connection.opened) connection.reject(error)
    for (const id of connection.pending.keys()) {
      const request = takeRequest(connection, id)!
      request.reject(request.sent && request.method === 'run.cancel' && error.code !== 'cancelled'
        ? new RunsRequestError('outcome_unknown') : error)
    }
    if (sock.readyState === WebSocket.CONNECTING || sock.readyState === WebSocket.OPEN) {
      try { sock.close(1000, '') } catch { /* already closed */ }
    }
  }

  function getConnection(): RunsConnection {
    if (activeConnection) {
      const current = activeConnection
      if (current.socket.readyState === WebSocket.CONNECTING || current.socket.readyState === WebSocket.OPEN) {
        return current
      }
      endConnection(current, new RunsRequestError('connection_closed'))
    }

    let sock: WebSocket
    try {
      sock = new WebSocket(buildWsUrl('/ws/admin'))
    } catch {
      handlers.onConnectionState({ connected: false, connecting: false, lastError: 'connection_failed' })
      throw new RunsRequestError('connection_failed')
    }
    let resolve!: () => void
    let reject!: (error: RunsRequestError) => void
    const ready = new Promise<void>((done, fail) => { resolve = done; reject = fail })
    const connection: RunsConnection = {
      socket: sock, ready, resolve, reject, opened: false, terminalError: null, timer: null, pending: new Map(),
    }
    activeConnection = connection
    handlers.onConnectionState({ connected: false, connecting: true, lastError: null })

    connection.timer = window.setTimeout(() => {
      if (activeConnection !== connection) return
      const error = new RunsRequestError('connection_timeout')
      diagnose(connection, error)
      endConnection(connection, error)
    }, 5000)

    sock.onopen = () => {
      if (activeConnection !== connection) return
      if (connection.timer !== null) window.clearTimeout(connection.timer)
      connection.timer = null
      connection.opened = true
      handlers.onConnectionState({ connected: true, connecting: false, lastError: null })
      connection.resolve()
    }
    sock.onmessage = (event: MessageEvent) => {
      if (activeConnection !== connection) return
      let msg: WsMsgBase
      try { msg = JSON.parse(String(event.data || '{}')) as WsMsgBase } catch { return }
      if (!msg || typeof msg !== 'object') return
      if (msg.type === 'ping') {
        try { sock.send(JSON.stringify({ type: 'pong' })) } catch {
          endConnection(connection, new RunsRequestError('connection_closed'))
        }
      } else if (msg.type === 'resp' && typeof msg.id === 'string') {
        const request = takeRequest(connection, msg.id)
        if (!request) return
        if (msg.ok) request.resolve(msg.result)
        else {
          const error = new RunsRequestError('request_failed')
          diagnose(connection, error, {
            method: request.method, requestId: msg.id,
            serverError: typeof msg.error === 'string' ? msg.error.slice(0, 200) : 'invalid response',
          })
          request.reject(error)
        }
      } else if (msg.type === 'event' && msg.event === 'bus.change') {
        handlers.onBusChange(msg.data)
      }
    }
    // Browsers report handshake failures through error + close. Keep the close
    // metadata; its callback (or the connect deadline) owns terminal cleanup.
    sock.onerror = () => {}
    sock.onclose = (event: CloseEvent) => {
      if (activeConnection !== connection) return
      const error = new RunsRequestError(connection.opened ? 'connection_closed' : 'connection_failed')
      diagnose(connection, error, { closeCode: event.code, reason: event.reason.slice(0, 200), wasClean: event.wasClean })
      endConnection(connection, error)
    }
    return connection
  }

  function connect(): Promise<void> {
    try { return getConnection().ready } catch (error) { return Promise.reject(error) }
  }

  function disconnect() {
    if (activeConnection) endConnection(activeConnection, new RunsRequestError('cancelled'))
    else handlers.onConnectionState({ connected: false, connecting: false, lastError: null })
  }

  async function rpc<T = unknown>(method: string, params: Record<string, unknown>): Promise<T> {
    const connection = getConnection()
    await connection.ready
    if (activeConnection !== connection) throw connection.terminalError ?? new RunsRequestError('cancelled')
    if (connection.socket.readyState !== WebSocket.OPEN) {
      endConnection(connection, new RunsRequestError('connection_closed'))
      throw new RunsRequestError('connection_closed')
    }

    const id = `rpc-${++rpcSeq}`
    const result = await new Promise<T>((resolve, reject) => {
      const request: PendingRequest = {
        method, sent: false, resolve: value => resolve(value as T), reject,
        timer: window.setTimeout(() => {
          const pending = takeRequest(connection, id)
          if (!pending) return
          const error = new RunsRequestError(pending.sent && method === 'run.cancel' ? 'outcome_unknown' : 'request_timeout')
          diagnose(connection, error, { method, requestId: id })
          pending.reject(error)
        }, 5000),
      }
      connection.pending.set(id, request)
      try {
        connection.socket.send(JSON.stringify({ type: 'req', id, method, params }))
        request.sent = true
      } catch {
        const error = new RunsRequestError('request_failed')
        diagnose(connection, error, { method, requestId: id, phase: 'send' })
        takeRequest(connection, id)?.reject(error)
      }
    })
    if (activeConnection !== connection) throw new RunsRequestError('cancelled')
    return result
  }

  return {
    connect,
    disconnect,
    listRuns(pluginId?: string) {
      return rpc<RunRecord[]>('runs.list', pluginId ? { plugin_id: pluginId } : {})
    },
    getRun(runId: string) {
      return rpc<RunRecord>('run.get', { run_id: runId })
    },
    listExports(runId: string) {
      return rpc<{ items: ExportItem[]; next_after?: string | null }>('export.list', {
        run_id: runId, after: null, limit: 500,
      })
    },
    cancelRun(runId: string, reason?: string) {
      const params: Record<string, unknown> = { run_id: runId }
      if (reason) params.reason = reason
      return rpc<RunRecord>('run.cancel', params)
    },
  }
}
