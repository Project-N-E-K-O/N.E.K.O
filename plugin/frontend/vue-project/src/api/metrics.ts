/**
 * 性能监控相关 API
 */
import { get } from './index'
import type { PluginMetrics } from '@/types/api'

/**
 * 获取所有插件的性能指标
 */
export function getAllMetrics(): Promise<{
  metrics: PluginMetrics[]
  count: number
  global?: {
    total_cpu_percent: number
    total_memory_mb: number
    total_memory_percent: number
    total_threads: number
    active_plugins: number
  }
  time: string
}> {
  return get('/plugin/metrics')
}

/**
 * 获取指定插件的性能指标
 */
export function getPluginMetrics(pluginId: string): Promise<{ plugin_id: string; metrics: PluginMetrics; time: string }> {
  return get(`/plugin/metrics/${pluginId}`)
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
): Promise<{ plugin_id: string; history: PluginMetrics[]; count: number; time: string }> {
  return get(`/plugin/metrics/${pluginId}/history`, { params })
}

