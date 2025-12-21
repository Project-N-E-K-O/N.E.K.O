/**
 * 配置管理相关 API
 */
import { get, put } from './index'
import type { PluginConfig } from '@/types/api'

/**
 * 获取插件配置
 */
export function getPluginConfig(pluginId: string): Promise<PluginConfig> {
  return get(`/plugin/${pluginId}/config`)
}

/**
 * 更新插件配置
 */
export function updatePluginConfig(
  pluginId: string,
  config: Record<string, any>
): Promise<{
  success: boolean
  plugin_id: string
  config: Record<string, any>
  requires_reload: boolean
  message: string
}> {
  return put(`/plugin/${pluginId}/config`, { config })
}

