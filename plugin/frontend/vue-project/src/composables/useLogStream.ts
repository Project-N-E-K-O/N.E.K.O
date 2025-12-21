/**
 * 日志流 WebSocket 连接管理
 */
import { ref, onMounted, onUnmounted, watch, type Ref, type MaybeRef, toRef, isRef } from 'vue'
import { useLogsStore } from '@/stores/logs'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { API_BASE_URL } from '@/utils/constants'

export function useLogStream(pluginIdInput: MaybeRef<string>) {
  // 将输入转换为响应式引用
  const pluginId = isRef(pluginIdInput) ? pluginIdInput : toRef(() => pluginIdInput)
  const { t } = useI18n()
  const logsStore = useLogsStore()
  const ws = ref<WebSocket | null>(null)
  const isConnected = ref(false)
  const reconnectTimer = ref<number | null>(null)
  const reconnectAttempts = ref(0)
  const maxReconnectAttempts = 5
  const reconnectDelay = 3000 // 3秒

  // 获取 WebSocket URL
  function getWebSocketUrl(): string {
    const id = pluginId.value
    // 在开发环境中，如果使用代理（API_BASE_URL 为空），使用当前窗口的 host
    // Vite 代理会自动转发 WebSocket 请求
    if (!API_BASE_URL || API_BASE_URL === '') {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const host = window.location.host
      return `${protocol}//${host}/ws/logs/${id}`
    }
    
    // 生产环境：使用与 HTTP API 相同的基础 URL
    try {
      const apiUrl = new URL(API_BASE_URL)
      const protocol = apiUrl.protocol === 'https:' ? 'wss:' : 'ws:'
      const host = apiUrl.host
      return `${protocol}//${host}/ws/logs/${id}`
    } catch (e) {
      // 如果 URL 解析失败，回退到当前窗口的 host
      console.warn('[LogStream] Failed to parse API_BASE_URL, using current host:', e)
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const host = window.location.host
      return `${protocol}//${host}/ws/logs/${id}`
    }
  }

  // 连接 WebSocket
  function connect() {
    if (ws.value?.readyState === WebSocket.OPEN || ws.value?.readyState === WebSocket.CONNECTING) {
      return
    }

    try {
      const url = getWebSocketUrl()
      ws.value = new WebSocket(url)

      ws.value.onopen = () => {
        isConnected.value = true
        reconnectAttempts.value = 0
        console.log(`[LogStream] Connected to ${pluginId.value}`)
      }

      ws.value.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          const id = pluginId.value
          
          if (data.type === 'initial') {
            // 初始日志：替换所有日志
            logsStore.logs[id] = data.logs || []
            logsStore.logFileInfo[id] = {
              log_file: data.log_file,
              total_lines: data.total_lines || 0,
              returned_lines: data.logs?.length || 0
            }
            console.log(`[LogStream] Received initial logs for ${id}:`, data.logs?.length || 0)
          } else if (data.type === 'append') {
            // 追加新日志
            const currentLogs = logsStore.logs[id] || []
            logsStore.logs[id] = [...currentLogs, ...(data.logs || [])]
            console.log(`[LogStream] Appended ${data.logs?.length || 0} new logs for ${id}`)
          } else if (data.type === 'ping') {
            // 心跳消息，可以回复 pong（可选）
            // 目前不需要回复
          }
        } catch (error) {
          console.error('[LogStream] Failed to parse message:', error)
        }
      }

      ws.value.onerror = (error) => {
        console.error(`[LogStream] WebSocket error for ${pluginId.value}:`, error)
        isConnected.value = false
      }

      ws.value.onclose = (event) => {
        isConnected.value = false
        console.log(`[LogStream] Disconnected from ${pluginId.value}`, event.code, event.reason)
        
        // 如果不是正常关闭，尝试重连
        if (event.code !== 1000 && reconnectAttempts.value < maxReconnectAttempts) {
          reconnectAttempts.value++
          console.log(`[LogStream] Attempting to reconnect (${reconnectAttempts.value}/${maxReconnectAttempts})...`)
          reconnectTimer.value = window.setTimeout(() => {
            connect()
          }, reconnectDelay)
        } else if (reconnectAttempts.value >= maxReconnectAttempts) {
          console.error(`[LogStream] Max reconnection attempts reached for ${pluginId.value}`)
          ElMessage.error(t('logs.connectionFailed'))
        }
      }
    } catch (error) {
      console.error(`[LogStream] Failed to create WebSocket connection:`, error)
      isConnected.value = false
    }
  }

  // 断开连接
  function disconnect() {
    if (reconnectTimer.value) {
      clearTimeout(reconnectTimer.value)
      reconnectTimer.value = null
    }
    
    if (ws.value) {
      try {
        // 正常关闭连接（code 1000 表示正常关闭）
        if (ws.value.readyState === WebSocket.OPEN || ws.value.readyState === WebSocket.CONNECTING) {
          ws.value.close(1000, 'Client disconnect')
        }
      } catch (error) {
        // 忽略关闭时的错误
        console.debug('[LogStream] Error closing WebSocket:', error)
      } finally {
        ws.value = null
      }
    }
    
    isConnected.value = false
    reconnectAttempts.value = 0
  }

  // 监听 pluginId 变化，重新连接
  watch(pluginId, (newId, oldId) => {
    if (oldId && oldId !== newId) {
      disconnect()
    }
    if (newId) {
      connect()
    }
  })

  // 组件挂载时连接
  onMounted(() => {
    if (pluginId.value) {
      connect()
    }
  })

  // 组件卸载时断开
  onUnmounted(() => {
    disconnect()
  })

  return {
    isConnected,
    connect,
    disconnect
  }
}

