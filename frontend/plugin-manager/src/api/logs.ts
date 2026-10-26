/** Log-management transport helpers used by the viewer. */
import { get } from './index'
import { API_BASE_URL } from '@/utils/constants'

/**
 * 获取插件日志目录路径
 */
export function getPluginLogDirectory(pluginId: string): Promise<{
  plugin_id: string
  directory: string
  time: string
}> {
  return get(`/plugin/${encodeURIComponent(pluginId)}/logs/directory`)
}

/**
 * 导出插件日志文件（返回下载 URL）
 */
export function getPluginLogExportUrl(pluginId: string): string {
  // 移除尾部斜杠避免双斜杠路径
  const baseUrl = API_BASE_URL.replace(/\/$/, '')
  return `${baseUrl}/plugin/${encodeURIComponent(pluginId)}/logs/export`
}


/** A log viewer starts and refreshes its snapshot through this one stream. */
export function getPluginLogStreamUrl(
  pluginId: string,
  query: { lines: number; level?: string; search?: string }
): string {
  const url = new URL(`/ws/logs/${encodeURIComponent(pluginId)}`, API_BASE_URL || window.location.origin)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.searchParams.set('lines', String(query.lines))
  if (query.level) url.searchParams.set('level', query.level)
  if (query.search) url.searchParams.set('search', query.search)
  return url.toString()
}
