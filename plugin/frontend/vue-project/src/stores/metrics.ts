/**
 * 性能指标状态管理
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getAllMetrics, getPluginMetrics, getPluginMetricsHistory } from '@/api/metrics'
import type { PluginMetrics } from '@/types/api'

export const useMetricsStore = defineStore('metrics', () => {
  // 状态
  const allMetrics = ref<PluginMetrics[]>([])
  const currentMetrics = ref<Record<string, PluginMetrics>>({})
  const metricsHistory = ref<Record<string, PluginMetrics[]>>({})
  const loading = ref(false)
  const error = ref<string | null>(null)

  // 操作
  async function fetchAllMetrics() {
    loading.value = true
    error.value = null
    try {
      const response = await getAllMetrics()
      allMetrics.value = response.metrics || []
      
      // 更新当前指标
      (response.metrics || []).forEach(metric => {
        currentMetrics.value[metric.plugin_id] = metric
      })
      
      // 返回响应以便提取全局指标
      return response
    } catch (err: any) {
      error.value = err.message || '获取性能指标失败'
      console.error('Failed to fetch metrics:', err)
      throw err
    } finally {
      loading.value = false
    }
  }

  async function fetchPluginMetrics(pluginId: string) {
    try {
      const response = await getPluginMetrics(pluginId)
      if (response.metrics) {
        currentMetrics.value[pluginId] = response.metrics
      }
    } catch (err: any) {
      console.error(`Failed to fetch metrics for plugin ${pluginId}:`, err)
      // 即使失败也不抛出异常，让组件显示"暂无数据"
    }
  }

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

  function getCurrentMetrics(pluginId: string): PluginMetrics | null {
    return currentMetrics.value[pluginId] || null
  }

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

