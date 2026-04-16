/**
 * 插件相关 API
 */
import { get, post } from './index'
import type {
  PluginMeta,
  PluginStatusData,
  PluginHealth,
  PluginMessage,
} from '@/types/api'

/**
 * 获取插件列表
 */
export function getPlugins(): Promise<{ plugins: PluginMeta[]; message: string }> {
  return get('/plugins')
}

/**
 * 获取插件状态
 */
export function getPluginStatus(pluginId?: string): Promise<PluginStatusData | { plugins: Record<string, PluginStatusData> }> {
  const url = pluginId ? `/plugin/status?plugin_id=${encodeURIComponent(pluginId)}` : '/plugin/status'
  return get(url)
}

/**
 * 获取插件健康状态
 */
export function getPluginHealth(pluginId: string): Promise<PluginHealth> {
  const safeId = encodeURIComponent(pluginId)
  return get(`/plugin/${safeId}/health`)
}

/**
 * 启动插件
 */
export function startPlugin(pluginId: string): Promise<{ success: boolean; plugin_id: string; message: string }> {
  const safeId = encodeURIComponent(pluginId)
  return post(`/plugin/${safeId}/start`)
}

/**
 * 停止插件
 */
export function stopPlugin(pluginId: string): Promise<{ success: boolean; plugin_id: string; message: string }> {
  const safeId = encodeURIComponent(pluginId)
  return post(`/plugin/${safeId}/stop`)
}

/**
 * 重载插件
 */
export function reloadPlugin(pluginId: string): Promise<{ success: boolean; plugin_id: string; message: string }> {
  const safeId = encodeURIComponent(pluginId)
  return post(`/plugin/${safeId}/reload`)
}

/**
 * 重载所有插件（批量 API，后端并行处理）
 */
export function reloadAllPlugins(): Promise<{
  success: boolean
  reloaded: string[]
  failed: { plugin_id: string; error: string }[]
  skipped: string[]
  message: string
}> {
  return post('/plugins/reload')
}

/**
 * 获取插件消息
 */
export function getPluginMessages(params?: {
  plugin_id?: string
  max_count?: number
  priority_min?: number
}): Promise<{ messages: PluginMessage[]; count: number; time: string }> {
  return get('/plugin/messages', { params })
}

/**
 * 禁用 Extension（热切换）
 */
export function disableExtension(extId: string): Promise<{ success: boolean; ext_id: string; host_plugin_id: string; data?: any; message?: string }> {
  const safeId = encodeURIComponent(extId)
  return post(`/plugin/${safeId}/extension/disable`)
}

/**
 * 启用 Extension（热切换）
 */
export function enableExtension(extId: string): Promise<{ success: boolean; ext_id: string; host_plugin_id: string; data?: any; message?: string }> {
  const safeId = encodeURIComponent(extId)
  return post(`/plugin/${safeId}/extension/enable`)
}

/**
 * 获取服务器信息（包括SDK版本）
 */
export function getServerInfo(): Promise<{
  sdk_version: string
  plugins_count: number
  time: string
}> {
  return get('/server/info')
}

