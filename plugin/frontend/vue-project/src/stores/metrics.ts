/**
 * 性能指标状态管理
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getAllMetrics, getPluginMetrics, getPluginMetricsHistory } from '@/api/metrics'
import { useAuthStore } from '@/stores/auth'
import type { PluginMetrics } from '@/types/api'

export const useMetricsStore = defineStore('metrics', () => {
  // 状态
  const allMetrics = ref<PluginMetrics[]>([])
  const currentMetrics = ref<Record<string, PluginMetrics>>({})
  const metricsHistory = ref<Record<string, PluginMetrics[]>>({})
  const loading = ref(false)
  const error = ref<string | null>(null)

  /**
   * Fetches all plugin metrics from the API and updates the store state.
   *
   * When the user is not authenticated, returns a default empty metrics structure without updating store state.
   * On success, stores the returned metrics list into `allMetrics` and updates `currentMetrics` with the latest metric per plugin.
   * Updates `loading` and `error` state during the request lifecycle and rethrows any error encountered.
   *
   * @returns The raw API response object containing `metrics`, `count`, `global`, and `time`.
   */
  async function fetchAllMetrics() {
    // 检查认证状态
    const authStore = useAuthStore()
    if (!authStore.isAuthenticated) {
      console.log('[Metrics] Not authenticated, skipping fetchAllMetrics')
      return {
        metrics: [],
        count: 0,
        global: {
          total_cpu_percent: 0.0,
          total_memory_mb: 0.0,
          total_memory_percent: 0.0,
          total_threads: 0,
          active_plugins: 0
        },
        time: new Date().toISOString()
      }
    }
    
    loading.value = true
    error.value = null
    try {
      const response = await getAllMetrics()
      const metricsList: PluginMetrics[] = Array.isArray((response as any)?.metrics)
        ? ((response as any).metrics as PluginMetrics[])
        : []
      allMetrics.value = metricsList
      
      // 更新当前指标
      metricsList.forEach((metric: PluginMetrics) => {
        currentMetrics.value[metric.plugin_id] = metric
      })
      
      // 返回响应以便提取全局指标
      return response
    } catch (err: any) {
      error.value = err?.message || 'FETCH_METRICS_FAILED'
      console.error('Failed to fetch metrics:', err)
      throw err
    } finally {
      loading.value = false
    }
  }

  /**
   * Fetches the latest metrics for a given plugin and updates the store's current metrics entry.
   *
   * If the plugin has no metrics or is not found, the corresponding entry is removed to reflect "no data".
   *
   * @param pluginId - The ID of the plugin whose metrics should be fetched
   */
  async function fetchPluginMetrics(pluginId: string) {
    if (!pluginId) {
      console.warn('[Metrics] fetchPluginMetrics called with empty pluginId')
      return
    }
    
    // 检查认证状态
    const authStore = useAuthStore()
    if (!authStore.isAuthenticated) {
      console.log(`[Metrics] Not authenticated, skipping fetch for ${pluginId}`)
      return
    }
    
    console.log(`[Metrics] Fetching metrics for plugin: ${pluginId}`)
    
    try {
      const response = await getPluginMetrics(pluginId)
      console.log(`[Metrics] Received response for ${pluginId}:`, response)
      
      // 检查响应格式
      if (!response || typeof response !== 'object') {
        console.warn(`[Metrics] Invalid response format for ${pluginId}:`, response)
        return
      }
      
      if (response.metrics && typeof response.metrics === 'object') {
        // 确保 metrics 包含必需的字段
        if (response.metrics.plugin_id && response.metrics.timestamp) {
          currentMetrics.value[pluginId] = response.metrics
          console.log(`[Metrics] Successfully stored metrics for ${pluginId}`)
        } else {
          console.warn(`[Metrics] Incomplete metrics data for ${pluginId}:`, response.metrics)
        }
      } else {
        // 插件正在运行但没有指标数据（可能正在收集）
        // 清除之前的指标数据，让组件显示"暂无数据"
        if (currentMetrics.value[pluginId]) {
          delete currentMetrics.value[pluginId]
        }
        // 记录消息（如果有）
        if (response.message) {
          console.log(`[Metrics] ${pluginId}: ${response.message}`)
        } else {
          console.log(`[Metrics] ${pluginId}: No metrics available (metrics is null)`)
        }
      }
    } catch (err: any) {
      // 404 表示插件不存在，这是正常的
      if (err.response?.status === 404) {
        console.log(`[Metrics] Plugin ${pluginId} not found (404)`)
        // 清除该插件的指标数据（如果存在）
        if (currentMetrics.value[pluginId]) {
          delete currentMetrics.value[pluginId]
        }
        return
      }
      // 其他错误才记录
      console.error(`[Metrics] Failed to fetch metrics for plugin ${pluginId}:`, err)
      // 即使失败也不抛出异常，让组件显示"暂无数据"
    }
  }

  /**
   * Fetches historical metrics for a plugin and stores the result in the store's `metricsHistory`.
   *
   * @param pluginId - Identifier of the plugin whose metrics history to fetch.
   * @param params - Optional query parameters to filter the history.
   * @param params.limit - Maximum number of history entries to retrieve.
   * @param params.start_time - ISO 8601 start timestamp to filter entries (inclusive).
   * @param params.end_time - ISO 8601 end timestamp to filter entries (inclusive).
   */
  async function fetchMetricsHistory(
    pluginId: string,
    params?: { limit?: number; start_time?: string; end_time?: string }
  ) {
    try {
      const response = await getPluginMetricsHistory(pluginId, params)
      metricsHistory.value[pluginId] = response.history || []
    } catch (err: any) {
      console.error(`Failed to fetch metrics history for plugin ${pluginId}:`, err)
    }
  }

  /**
   * Retrieve the latest metrics for the specified plugin.
   *
   * @param pluginId - The unique identifier of the plugin
   * @returns The latest `PluginMetrics` for `pluginId`, or `null` if no metrics are available
   */
  function getCurrentMetrics(pluginId: string): PluginMetrics | null {
    return currentMetrics.value[pluginId] || null
  }

  /**
   * Retrieve the stored metrics history for a plugin.
   *
   * @param pluginId - The plugin identifier to look up
   * @returns An array of `PluginMetrics` for the specified plugin; an empty array if no history exists
   */
  function getHistory(pluginId: string): PluginMetrics[] {
    return metricsHistory.value[pluginId] || []
  }

  return {
    // 状态
    allMetrics,
    currentMetrics,
    metricsHistory,
    loading,
    error,
    // 操作
    fetchAllMetrics,
    fetchPluginMetrics,
    fetchMetricsHistory,
    getCurrentMetrics,
    getHistory
  }
})
