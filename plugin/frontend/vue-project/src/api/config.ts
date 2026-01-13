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
 * Fetches the configuration for a plugin.
 *
 * @param pluginId - The identifier of the plugin to retrieve configuration for
 * @returns The plugin's configuration as a `PluginConfig` object
 */
export function getPluginConfig(pluginId: string): Promise<PluginConfig> {
  return get(`/plugin/${pluginId}/config`)
}

/**
 * Retrieve a plugin's configuration as raw TOML.
 *
 * @returns The plugin's TOML payload (`PluginConfigToml`) containing `plugin_id`, `toml`, `last_modified`, and optional `config_path`.
 */
export function getPluginConfigToml(pluginId: string): Promise<PluginConfigToml> {
  return get(`/plugin/${pluginId}/config/toml`)
}

/**
 * Fetches the plugin's base configuration directly from `plugin.toml` without applying profiles.
 *
 * @param pluginId - The plugin identifier
 * @returns The plugin's base configuration as a `PluginBaseConfig`
 */
export function getPluginBaseConfig(pluginId: string): Promise<PluginBaseConfig> {
  return get(`/plugin/${pluginId}/config/base`)
}

/**
 * Update a plugin's configuration on the server.
 *
 * @param pluginId - The unique identifier of the plugin to update
 * @param config - The configuration object to persist for the plugin
 * @returns An object with `success` indicating operation result, `plugin_id` of the updated plugin, the persisted `config` map, `requires_reload` indicating whether the plugin must be reloaded for changes to take effect, and a human-readable `message`
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
 * Overwrites a plugin's configuration using the provided TOML content.
 *
 * @param pluginId - Identifier of the plugin to update
 * @param toml - TOML-formatted configuration text to be written for the plugin
 * @returns An object with `success` indicating operation result, `plugin_id`, the resulting `config` map, `requires_reload` indicating whether the plugin must be reloaded for changes to take effect, and a human-readable `message`
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
 * Parse a TOML string into a plugin configuration object without persisting it.
 *
 * @param pluginId - The plugin identifier
 * @param toml - The TOML content to parse
 * @returns An object containing `plugin_id` and the parsed `config` map
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
 * Render a plugin configuration object to TOML without saving it to disk.
 *
 * @param pluginId - The identifier of the plugin whose configuration will be rendered
 * @param config - The configuration object to convert to TOML
 * @returns An object with `plugin_id` and `toml`, where `toml` is the rendered TOML string
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

/**
 * Retrieve the overall profiles state for a plugin.
 *
 * @param pluginId - The plugin identifier
 * @returns The plugin's profiles state as a `PluginProfilesState` object
 */
export function getPluginProfilesState(pluginId: string): Promise<PluginProfilesState> {
  return get(`/plugin/${pluginId}/config/profiles`)
}

/**
 * Retrieve the configuration for a single plugin profile.
 *
 * @param pluginId - The identifier of the plugin
 * @param profileName - The name of the profile to retrieve (will be URL-encoded)
 * @returns The profile configuration payload for the specified plugin and profile
 */
export function getPluginProfileConfig(
  pluginId: string,
  profileName: string
): Promise<PluginProfileConfig> {
  return get(`/plugin/${pluginId}/config/profiles/${encodeURIComponent(profileName)}`)
}

/**
 * Create or update a plugin profile configuration.
 *
 * @param pluginId - The plugin identifier
 * @param profileName - The profile name (will be URL-encoded in the request)
 * @param config - Configuration key/value map for the profile
 * @param makeActive - If `true`, set the profile as the active profile after upsert
 * @returns The persisted PluginProfileConfig for the profile
 */
export function upsertPluginProfileConfig(
  pluginId: string,
  profileName: string,
  config: Record<string, any>,
  makeActive?: boolean
): Promise<PluginProfileConfig> {
  return put(`/plugin/${pluginId}/config/profiles/${encodeURIComponent(profileName)}`, {
    config,
    make_active: makeActive
  })
}

/**
 * Delete a plugin profile configuration.
 *
 * @returns An object with `plugin_id`, `profile` (the profile name), and `removed` — `true` if the profile was deleted, `false` otherwise.
 */
export function deletePluginProfileConfig(
  pluginId: string,
  profileName: string
): Promise<{
  plugin_id: string
  profile: string
  removed: boolean
}> {
  return del(`/plugin/${pluginId}/config/profiles/${encodeURIComponent(profileName)}`)
}

/**
 * Activate the specified profile for a plugin.
 *
 * @param pluginId - The plugin identifier
 * @param profileName - The name of the profile to activate
 * @returns The updated PluginProfilesState reflecting the active profile and profiles metadata
 */
export function setPluginActiveProfile(
  pluginId: string,
  profileName: string
): Promise<PluginProfilesState> {
  return post(`/plugin/${pluginId}/config/profiles/${encodeURIComponent(profileName)}/activate`, {})
}
