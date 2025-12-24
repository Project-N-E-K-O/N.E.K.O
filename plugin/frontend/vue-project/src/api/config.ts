/**
 * 配置管理相关 API
 */
import { get, put } from './index'
import type { PluginConfig } from '@/types/api'

export interface PluginConfigToml {
  plugin_id: string
  toml: string
  last_modified: string
  config_path?: string
}

/**
 * 获取插件配置
 */
export function getPluginConfig(pluginId: string): Promise<PluginConfig> {
  return get(`/plugin/${pluginId}/config`)
}

/**
 * 获取插件配置（TOML 原文）
 */
export function getPluginConfigToml(pluginId: string): Promise<PluginConfigToml> {
  return get(`/plugin/${pluginId}/config/toml`)
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

/**
 * 更新插件配置（TOML 原文覆盖写入）
 */
export function updatePluginConfigToml(
  pluginId: string,
  toml: string
): Promise<{
  success: boolean
  plugin_id: string
  config: Record<string, any>
  requires_reload: boolean
  message: string
}> {
  return put(`/plugin/${pluginId}/config/toml`, { toml })
}

