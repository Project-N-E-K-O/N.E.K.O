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
 * 获取所有插件的性能指标
 */
export function getAllMetrics(): Promise<MetricsResponse> {
  return get('/plugin/metrics')
}

/**
 * 获取指定插件的性能指标
 */
export function getPluginMetrics(pluginId: string): Promise<PluginMetricsResult> {
  return get(`/plugin/metrics/${encodeURIComponent(pluginId)}`)
}

/**
 * 获取插件性能指标历史
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

