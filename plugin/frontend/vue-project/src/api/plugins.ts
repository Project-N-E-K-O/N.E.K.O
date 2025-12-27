/**
 * 插件相关 API
 */
import { get, post } from './index'
import type {
  PluginMeta,
  PluginStatusData,
  PluginHealth,
  PluginTriggerRequest,
  PluginTriggerResponse
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
  const url = pluginId ? `/plugin/status?plugin_id=${pluginId}` : '/plugin/status'
  return get(url)
}

/**
 * 获取插件健康状态
 */
export function getPluginHealth(pluginId: string): Promise<PluginHealth> {
  return get(`/plugin/${pluginId}/health`)
}

/**
 * 启动插件
 */
export function startPlugin(pluginId: string): Promise<{ success: boolean; plugin_id: string; message: string }> {
  return post(`/plugin/${pluginId}/start`)
}

/**
 * 停止插件
 */
export function stopPlugin(pluginId: string): Promise<{ success: boolean; plugin_id: string; message: string }> {
  return post(`/plugin/${pluginId}/stop`)
}

/**
 * 重载插件
 */
export function reloadPlugin(pluginId: string): Promise<{ success: boolean; plugin_id: string; message: string }> {
  return post(`/plugin/${pluginId}/reload`)
}

/**
 * 触发插件执行
 */
export function triggerPlugin(payload: PluginTriggerRequest): Promise<PluginTriggerResponse> {
  return post('/plugin/trigger', payload)
}

/**
 * 获取插件消息
 */
export function getPluginMessages(params?: {
  plugin_id?: string
  max_count?: number
  priority_min?: number
}): Promise<{ messages: any[]; count: number; time: string }> {
  return get('/plugin/messages', { params })
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

