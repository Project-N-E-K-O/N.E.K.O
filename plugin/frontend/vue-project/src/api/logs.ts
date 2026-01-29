/**
 * 日志相关 API
 */
import { get } from './index'
import type { LogEntry, LogFile } from '@/types/api'

/**
 * 获取插件日志
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
  return get(`/plugin/${encodeURIComponent(pluginId)}/logs`, { params })
}

/**
 * 获取插件日志文件列表
 */
export function getPluginLogFiles(pluginId: string): Promise<{
  plugin_id: string
  log_files: LogFile[]
  count: number
  time: string
}> {
  return get(`/plugin/${encodeURIComponent(pluginId)}/logs/files`)
}

