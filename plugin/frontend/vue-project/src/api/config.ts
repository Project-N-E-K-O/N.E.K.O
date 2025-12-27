/**
 * 配置管理相关 API
 */
import { get, put, post } from './index'
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

/**
 * 解析 TOML 为配置对象（不落盘，用于表单/源码同步）
 */
export function parsePluginConfigToml(
  pluginId: string,
  toml: string
): Promise<{
  plugin_id: string
  config: Record<string, any>
}> {
  return post(`/plugin/${pluginId}/config/parse_toml`, { toml })
}

/**
 * 渲染配置对象为 TOML（不落盘，用于表单/源码同步）
 */
export function renderPluginConfigToml(
  pluginId: string,
  config: Record<string, any>
): Promise<{
  plugin_id: string
  toml: string
}> {
  return post(`/plugin/${pluginId}/config/render_toml`, { config })
}

