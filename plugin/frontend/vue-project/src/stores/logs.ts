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
  const logFileInfo = ref<Record<string, { log_file?: string; total_lines?: number; returned_lines?: number; error?: string }>>({})

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
      
      // 保存日志文件信息，包括错误信息
      logFileInfo.value[pluginId] = {
        log_file: response.log_file,
        total_lines: response.total_lines,
        returned_lines: response.returned_lines,
        error: (response as any).error
      }
      
      // 如果有错误信息，记录到 error 状态
      if ((response as any).error) {
        error.value = (response as any).error
        console.warn(`Log fetch warning for plugin ${pluginId}:`, (response as any).error)
      } else {
        error.value = null
      }
      
      // 调试信息
      console.log(`Fetched logs for plugin ${pluginId}:`, {
        logFile: response.log_file,
        totalLines: response.total_lines,
        returnedLines: response.returned_lines,
        logsCount: (response.logs || []).length
      })
    } catch (err: any) {
      error.value = err.message || '获取日志失败'
      console.error(`Failed to fetch logs for plugin ${pluginId}:`, err)
      logs.value[pluginId] = []
      logFileInfo.value[pluginId] = {
        error: err.message || '获取日志失败'
      }
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

  function getLogFileInfo(pluginId: string) {
    return logFileInfo.value[pluginId] || null
  }

  return {
    // 状态
    logs,
    logFiles,
    loading,
    error,
    logFileInfo,
    // 操作
    fetchLogs,
    fetchLogFiles,
    getLogs,
    getFiles,
    getLogFileInfo
  }
})

