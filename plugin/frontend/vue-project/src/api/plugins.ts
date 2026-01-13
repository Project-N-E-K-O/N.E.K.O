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
 * Retrieve the list of available plugins.
 *
 * @returns An object containing `plugins` (an array of PluginMeta) and `message` (string)
 */
export function getPlugins(): Promise<{ plugins: PluginMeta[]; message: string }> {
  return get('/plugins')
}

/**
 * Retrieve status information for a specific plugin or for all plugins.
 *
 * @param pluginId - If provided, return status for the plugin with this ID; otherwise return statuses for all plugins
 * @returns `PluginStatusData` for the specified plugin, or an object with a `plugins` map from plugin IDs to `PluginStatusData` for all plugins
 */
export function getPluginStatus(pluginId?: string): Promise<PluginStatusData | { plugins: Record<string, PluginStatusData> }> {
  const url = pluginId ? `/plugin/status?plugin_id=${pluginId}` : '/plugin/status'
  return get(url)
}

/**
 * Retrieve health information for a specific plugin.
 *
 * @param pluginId - The ID of the plugin to query
 * @returns The plugin's health information
 */
export function getPluginHealth(pluginId: string): Promise<PluginHealth> {
  return get(`/plugin/${pluginId}/health`)
}

/**
 * Start the specified plugin.
 *
 * @param pluginId - The identifier of the plugin to start
 * @returns An object containing `success` (`true` if the plugin was started, `false` otherwise), `plugin_id` (the plugin's identifier), and `message` (a server-provided message)
 */
export function startPlugin(pluginId: string): Promise<{ success: boolean; plugin_id: string; message: string }> {
  return post(`/plugin/${pluginId}/start`)
}

/**
 * Stop a plugin by its ID.
 *
 * @param pluginId - The ID of the plugin to stop.
 * @returns An object with `success` indicating whether the stop operation succeeded (`true` if succeeded, `false` otherwise), `plugin_id` echoing the target plugin's ID, and `message` with additional information.
 */
export function stopPlugin(pluginId: string): Promise<{ success: boolean; plugin_id: string; message: string }> {
  return post(`/plugin/${pluginId}/stop`)
}

/**
 * Reloads the specified plugin.
 *
 * @param pluginId - The identifier of the plugin to reload
 * @returns An object with `success` indicating whether the reload succeeded, `plugin_id` of the plugin, and a server `message`
 */
export function reloadPlugin(pluginId: string): Promise<{ success: boolean; plugin_id: string; message: string }> {
  return post(`/plugin/${pluginId}/reload`)
}

/**
 * Trigger execution of a plugin using the provided request payload.
 *
 * @param payload - Request describing which plugin to trigger and any execution parameters
 * @returns The `PluginTriggerResponse` containing the execution result and any returned data
 */
export function triggerPlugin(payload: PluginTriggerRequest): Promise<PluginTriggerResponse> {
  return post('/plugin/trigger', payload)
}

/**
 * Retrieve plugin-related messages with optional filters.
 *
 * @param params - Optional query parameters to filter results:
 *   - `plugin_id`: ID of the plugin to fetch messages for
 *   - `max_count`: Maximum number of messages to return
 *   - `priority_min`: Minimum message priority to include
 * @returns An object with `messages` (array of message entries), `count` (total number of matching messages), and `time` (server timestamp)
 */
export function getPluginMessages(params?: {
  plugin_id?: string
  max_count?: number
  priority_min?: number
}): Promise<{ messages: any[]; count: number; time: string }> {
  return get('/plugin/messages', { params })
}

/**
 * Retrieve server information including the SDK version and plugin count.
 *
 * @returns An object with `sdk_version` (SDK version string), `plugins_count` (number of plugins), and `time` (timestamp string)
 */
export function getServerInfo(): Promise<{
  sdk_version: string
  plugins_count: number
  time: string
}> {
  return get('/server/info')
}
