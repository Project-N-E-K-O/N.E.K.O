/**
 * 配置管理相关 API
 */
import { get, put, post, del } from './index'
import type { PluginConfig } from '@/types/api'

export interface PluginConfigToml {
  plugin_id: string
  toml: string
  last_modified: string
  config_path?: string
}

export interface PluginBaseConfig extends PluginConfig {}

export interface PluginProfilesState {
  plugin_id: string
  profiles_path: string
  profiles_exists: boolean
  config_profiles: null | {
    active: string | null
    files: Record<
      string,
      {
        path: string
        resolved_path: string | null
        exists: boolean
      }
    >
  }
}

export interface PluginProfileConfig {
  plugin_id: string
  profile: {
    name: string
    path: string
    resolved_path: string | null
    exists: boolean
  }
  config: Record<string, any>
}

/**
 * 获取插件配置
 */
export function getPluginConfig(pluginId: string): Promise<PluginConfig> {
  return get(`/plugin/${encodeURIComponent(pluginId)}/config`)
}

/**
 * 获取插件配置（TOML 原文）
 */
export function getPluginConfigToml(pluginId: string): Promise<PluginConfigToml> {
  return get(`/plugin/${encodeURIComponent(pluginId)}/config/toml`)
}

/**
 * 获取插件基础配置（直接来自 plugin.toml，不包含 profile 叠加）
 */
export function getPluginBaseConfig(pluginId: string): Promise<PluginBaseConfig> {
  return get(`/plugin/${encodeURIComponent(pluginId)}/config/base`)
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
  return put(`/plugin/${encodeURIComponent(pluginId)}/config`, { config })
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
  return put(`/plugin/${encodeURIComponent(pluginId)}/config/toml`, { toml })
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
  return post(`/plugin/${encodeURIComponent(pluginId)}/config/parse_toml`, { toml })
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
  return post(`/plugin/${encodeURIComponent(pluginId)}/config/render_toml`, { config })
}

/**
 * 获取 profile 配置总体状态
 */
export function getPluginProfilesState(pluginId: string): Promise<PluginProfilesState> {
  return get(`/plugin/${encodeURIComponent(pluginId)}/config/profiles`)
}

/**
 * 获取单个 profile 的配置
 */
export function getPluginProfileConfig(
  pluginId: string,
  profileName: string
): Promise<PluginProfileConfig> {
  return get(`/plugin/${encodeURIComponent(pluginId)}/config/profiles/${encodeURIComponent(profileName)}`)
}

/**
 * 创建或更新 profile 配置
 */
export function upsertPluginProfileConfig(
  pluginId: string,
  profileName: string,
  config: Record<string, any>,
  makeActive?: boolean
): Promise<PluginProfileConfig> {
  return put(`/plugin/${encodeURIComponent(pluginId)}/config/profiles/${encodeURIComponent(profileName)}`, {
    config,
    make_active: makeActive
  })
}

/**
 * 删除 profile 配置映射
 */
export function deletePluginProfileConfig(
  pluginId: string,
  profileName: string
): Promise<{
  plugin_id: string
  profile: string
  removed: boolean
}> {
  return del(`/plugin/${encodeURIComponent(pluginId)}/config/profiles/${encodeURIComponent(profileName)}`)
}

/**
 * 设置当前激活的 profile
 */
export function setPluginActiveProfile(
  pluginId: string,
  profileName: string
): Promise<PluginProfilesState> {
  return post(`/plugin/${encodeURIComponent(pluginId)}/config/profiles/${encodeURIComponent(profileName)}/activate`, {})
}

/**
 * 热更新配置响应
 */
export interface HotUpdateConfigResponse {
  success: boolean
  plugin_id: string
  mode: 'temporary' | 'permanent'
  hot_reloaded: boolean
  requires_reload: boolean
  handler_called?: boolean
  message: string
}

/**
 * 热更新插件配置（不需要重启插件）
 * 
 * @param pluginId 插件ID
 * @param config 要更新的配置部分（会与现有配置深度合并）
 * @param mode 更新模式：
 *   - 'temporary': 临时更新，只修改插件进程内缓存，不写入文件。插件重启后配置会恢复。
 *   - 'permanent': 永久更新，写入 profile 文件，并通知插件进程更新缓存。
 * @param profile profile 名称（permanent 模式时使用，null 表示使用当前激活的 profile）
 */
export function hotUpdatePluginConfig(
  pluginId: string,
  config: Record<string, any>,
  mode: 'temporary' | 'permanent' = 'temporary',
  profile?: string | null
): Promise<HotUpdateConfigResponse> {
  return post(`/plugin/${encodeURIComponent(pluginId)}/config/hot-update`, {
    config,
    mode,
    profile: profile ?? null
  })
}

