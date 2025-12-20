/**
 * 日志状态管理
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getPluginLogs, getPluginLogFiles } from '@/api/logs'
import type { LogEntry, LogFile } from '@/types/api'

export const useLogsStore = defineStore('logs', () => {
  // 状态
  const logs = ref<Record<string, LogEntry[]>>({})
  const logFiles = ref<Record<string, LogFile[]>>({})
  const loading = ref(false)
  const error = ref<string | null>(null)

  // 操作
  async function fetchLogs(
    pluginId: string,
    params?: {
      lines?: number
      level?: string
      start_time?: string
      end_time?: string
      search?: string
    }
  ) {
    loading.value = true
    error.value = null
    try {
      const response = await getPluginLogs(pluginId, params)
      logs.value[pluginId] = response.logs || []
    } catch (err: any) {
      error.value = err.message || '获取日志失败'
      console.error(`Failed to fetch logs for plugin ${pluginId}:`, err)
    } finally {
      loading.value = false
    }
  }

  async function fetchLogFiles(pluginId: string) {
    try {
      const response = await getPluginLogFiles(pluginId)
      logFiles.value[pluginId] = response.log_files || []
    } catch (err: any) {
      console.error(`Failed to fetch log files for plugin ${pluginId}:`, err)
    }
  }

  function getLogs(pluginId: string): LogEntry[] {
    return logs.value[pluginId] || []
  }

  function getFiles(pluginId: string): LogFile[] {
    return logFiles.value[pluginId] || []
  }

  return {
    // 状态
    logs,
    logFiles,
    loading,
    error,
    // 操作
    fetchLogs,
    fetchLogFiles,
    getLogs,
    getFiles
  }
})

