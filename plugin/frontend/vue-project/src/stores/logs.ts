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

  /**
   * Fetches logs for a plugin and updates the store's log state and metadata.
   *
   * Updates `loading`, `error`, `logs[pluginId]`, and `logFileInfo[pluginId]` based on the API response or any error encountered.
   *
   * @param pluginId - The identifier of the plugin whose logs should be fetched
   * @param params - Optional fetch filters
   * @param params.lines - Maximum number of log lines to return
   * @param params.level - Log level filter (e.g., "info", "error")
   * @param params.start_time - ISO timestamp to filter logs from this time (inclusive)
   * @param params.end_time - ISO timestamp to filter logs up to this time (inclusive)
   * @param params.search - Text to search for within log entries
   */
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
        error: response.error
      }
      
      // 如果有错误信息，记录到 error 状态
      if (response.error) {
        error.value = response.error
        console.warn(`Log fetch warning for plugin ${pluginId}:`, response.error)
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

  /**
   * Fetches available log files for the given plugin and stores them in the store's `logFiles` by `pluginId`.
   *
   * @param pluginId - The plugin identifier whose log files will be fetched and stored
   */
  async function fetchLogFiles(pluginId: string) {
    try {
      const response = await getPluginLogFiles(pluginId)
      logFiles.value[pluginId] = response.log_files || []
    } catch (err: any) {
      console.error(`Failed to fetch log files for plugin ${pluginId}:`, err)
    }
  }

  /**
   * Retrieve the list of log entries for a given plugin.
   *
   * @param pluginId - The identifier of the plugin whose logs to return
   * @returns The array of `LogEntry` objects for `pluginId`, or an empty array if none exist
   */
  function getLogs(pluginId: string): LogEntry[] {
    return logs.value[pluginId] || []
  }

  /**
   * Retrieve stored log files for a plugin.
   *
   * @param pluginId - The plugin identifier whose log files to return
   * @returns The array of `LogFile` objects for the given plugin, or an empty array if none are stored
   */
  function getFiles(pluginId: string): LogFile[] {
    return logFiles.value[pluginId] || []
  }

  /**
   * Retrieve stored metadata about a plugin's current log file.
   *
   * @param pluginId - The plugin identifier whose log file info to retrieve
   * @returns The log file info for `pluginId`, or `null` if none is available. May include `log_file`, `total_lines`, `returned_lines`, and `error` fields.
   */
  function getLogFileInfo(pluginId: string) {
    return logFileInfo.value[pluginId] || null
  }

  /**
   * Initialize a plugin's logs and associated log file metadata from an initial (WebSocket) payload.
   *
   * @param pluginId - The plugin identifier whose state will be initialized
   * @param data - Initial payload containing `logs` and optional `log_file` and `total_lines`. The store's `returned_lines` will be set to the number of items in `logs`; `total_lines` defaults to 0 when absent.
   */
  function setInitialLogs(
    pluginId: string,
    data: {
      logs: LogEntry[]
      log_file?: string
      total_lines?: number
    }
  ) {
    logs.value[pluginId] = data.logs || []
    logFileInfo.value[pluginId] = {
      log_file: data.log_file,
      total_lines: data.total_lines || 0,
      returned_lines: data.logs?.length || 0
    }
  }

  /**
   * Append new log entries for the given plugin, used for incremental WebSocket updates.
   *
   * @param pluginId - The plugin's unique identifier
   * @param newLogs - Log entries to append; entries are added after existing logs in the given order
   */
  function appendLogs(pluginId: string, newLogs: LogEntry[]) {
    const currentLogs = logs.value[pluginId] || []
    logs.value[pluginId] = [...currentLogs, ...newLogs]
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
    getLogFileInfo,
    setInitialLogs,
    appendLogs
  }
})
