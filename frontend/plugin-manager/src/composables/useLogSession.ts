/** One viewer owns its query, snapshot, stream and connection lifecycle. */
import { onScopeDispose, ref, shallowRef, toValue, watch, type MaybeRefOrGetter } from 'vue'
import { getPluginLogStreamUrl } from '@/api/logs'
import type { LogEntry } from '@/types/api'

const MAX_LOGS = 5000
const MAX_RECONNECT_ATTEMPTS = 5

export function useLogSession(pluginId: MaybeRefOrGetter<string>) {
  const logs = shallowRef<LogEntry[]>([])
  const logFile = ref<string>()
  const totalLines = ref(0)
  const lines = ref(500)
  const level = ref('')
  const search = ref('')
  const loading = ref(false)
  const isConnected = ref(false)
  const error = ref<string | null>(null)
  const errorCode = ref<string | null>(null)
  const connectionFailed = ref(false)

  let socket: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined
  let generation = 0
  let attempts = 0

  function close() {
    generation++
    clearTimeout(reconnectTimer)
    reconnectTimer = undefined
    const previous = socket
    socket = null
    if (previous) {
      previous.onopen = previous.onmessage = previous.onerror = previous.onclose = null
      previous.close(1000, 'Log session closed')
    }
    isConnected.value = false
  }

  function connect(currentGeneration: number, url: string) {
    if (currentGeneration !== generation) return
    const retry = () => {
      if (currentGeneration !== generation) return
      isConnected.value = false
      loading.value = false
      if (attempts >= MAX_RECONNECT_ATTEMPTS) {
        connectionFailed.value = true
        return
      }
      const delay = Math.min(1200 * 2 ** attempts++, 8000)
      reconnectTimer = setTimeout(() => {
        reconnectTimer = undefined
        loading.value = true
        connect(currentGeneration, url)
      }, delay)
    }
    let current: WebSocket
    try {
      current = new WebSocket(url)
    } catch {
      retry()
      return
    }
    socket = current
    const isCurrent = () => currentGeneration === generation && socket === current
    current.onopen = () => {
      if (!isCurrent()) return
      isConnected.value = true
      connectionFailed.value = false
    }
    current.onmessage = (event) => {
      if (!isCurrent()) return
      try {
        const message = JSON.parse(event.data)
        if (message.type === 'ping') return
        if (message.type === 'error') {
          errorCode.value = typeof message.code === 'string' ? message.code : null
          error.value = String(message.error || errorCode.value || 'LOG_READ_FAILED')
          loading.value = false
          return
        }
        if (!['initial', 'append'].includes(message.type) || !Array.isArray(message.logs)) return
        const incoming = message.logs as LogEntry[]
        if (message.type === 'initial') {
          logs.value = incoming.slice(-MAX_LOGS)
          totalLines.value = typeof message.total_lines === 'number' ? message.total_lines : incoming.length
          logFile.value = message.log_file
          attempts = 0
        } else {
          // IDs are optional for old servers. Never deduplicate equal text: two
          // identical messages can be separate real log records.
          const ids = new Set(logs.value.flatMap(log => log.id ? [log.id] : []))
          const added = incoming.filter(log => {
            if (!log.id) return true
            if (ids.has(log.id)) return false
            ids.add(log.id)
            return true
          })
          logs.value = [...logs.value, ...added].slice(-MAX_LOGS)
          totalLines.value = typeof message.total_lines === 'number' ? message.total_lines : totalLines.value + added.length
          if (message.log_file) logFile.value = message.log_file
        }
        error.value = message.error || null
        errorCode.value = error.value && typeof message.code === 'string' ? message.code : null
        connectionFailed.value = false
        loading.value = false
      } catch {
        connectionFailed.value = true
        loading.value = false
      }
    }
    current.onerror = () => {
      if (!isCurrent()) return
      isConnected.value = false
    }
    current.onclose = () => {
      if (!isCurrent()) return
      socket = null
      retry()
    }
  }

  /** Apply the current query atomically; only this stream can replace the list. */
  function refresh() {
    close()
    attempts = 0
    logs.value = []
    logFile.value = undefined
    totalLines.value = 0
    error.value = null
    errorCode.value = null
    connectionFailed.value = false
    const id = toValue(pluginId)
    loading.value = !!id
    if (!id) return
    connect(generation, getPluginLogStreamUrl(id, {
      lines: lines.value,
      level: level.value || undefined,
      search: search.value.trim() || undefined,
    }))
  }

  watch(() => toValue(pluginId), refresh, { immediate: true })
  onScopeDispose(close)

  return { logs, logFile, totalLines, lines, level, search, loading, isConnected, error, errorCode, connectionFailed, refresh }
}
