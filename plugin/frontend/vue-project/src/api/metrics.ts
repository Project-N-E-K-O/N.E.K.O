/**
 * 性能监控相关 API
 */
import { get } from './index'
import type {
  PluginMetrics,
  MetricsResponse,
  PluginMetricsResult,
  PluginMetricsHistoryResult,
} from '@/types/api'

/**
 * Fetches performance metrics for all plugins.
 *
 * @returns Aggregated performance metrics for all plugins as a `MetricsResponse`
 */
export function getAllMetrics(): Promise<MetricsResponse> {
  return get('/plugin/metrics')
}

/**
 * Fetches performance metrics for a specific plugin.
 *
 * @param pluginId - The plugin's unique identifier
 * @returns The performance metrics for the specified plugin as a `PluginMetricsResult`
 */
export function getPluginMetrics(pluginId: string): Promise<PluginMetricsResult> {
  return get(`/plugin/metrics/${encodeURIComponent(pluginId)}`)
}

/**
 * Fetches historical performance metrics for a plugin.
 *
 * @param pluginId - The plugin's unique identifier.
 * @param params - Optional query parameters:
 *   - `limit`: maximum number of records to return
 *   - `start_time`: ISO 8601 timestamp to start the range (inclusive)
 *   - `end_time`: ISO 8601 timestamp to end the range (inclusive)
 * @returns The plugin's metrics history as a `PluginMetricsHistoryResult`.
 */
export function getPluginMetricsHistory(
  pluginId: string,
  params?: {
    limit?: number
    start_time?: string
    end_time?: string
  }
): Promise<PluginMetricsHistoryResult> {
  return get(`/plugin/metrics/${encodeURIComponent(pluginId)}/history`, { params })
}
