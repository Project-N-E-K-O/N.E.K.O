/**
 * 日志相关 API
 */
import { get } from './index'
import type { LogEntry, LogFile } from '@/types/api'

/**
 * Retrieve logs for a specific plugin with optional filters.
 *
 * @param pluginId - Identifier of the plugin whose logs to fetch
 * @param params - Optional query filters
 * @param params.lines - Maximum number of log lines to return
 * @param params.level - Log level to filter by (e.g., "info", "error")
 * @param params.start_time - Start time (inclusive) for the log range, as an ISO timestamp
 * @param params.end_time - End time (inclusive) for the log range, as an ISO timestamp
 * @param params.search - Substring to search for within log entries
 * @returns An object containing `plugin_id`, an array of `logs`, `total_lines`, `returned_lines`, and optionally `log_file` and `error`
 */
export function getPluginLogs(
  pluginId: string,
  params?: {
    lines?: number
    level?: string
    start_time?: string
    end_time?: string
    search?: string
  }
): Promise<{
  plugin_id: string
  logs: LogEntry[]
  total_lines: number
  returned_lines: number
  log_file?: string
  error?: string
}> {
  return get(`/plugin/${pluginId}/logs`, { params })
}

/**
 * Retrieve the list of log files for a plugin.
 *
 * @param pluginId - The plugin identifier to fetch log files for
 * @returns An object containing `plugin_id`, `log_files` (array of `LogFile`), `count` (number of files), and `time` (server timestamp for the listing)
 */
export function getPluginLogFiles(pluginId: string): Promise<{
  plugin_id: string
  log_files: LogFile[]
  count: number
  time: string
}> {
  return get(`/plugin/${pluginId}/logs/files`)
}
