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
  
  // 防止请求堆积：正在进行的请求
  let pendingFetchAll: Promise<any> | null = null
  // 请求超时自动清理（防止请求堆积）
  const REQUEST_TIMEOUT = 15000 // 15秒

  // 操作
  async function fetchAllMetrics() {
    // 如果已有请求正在进行，直接返回该请求的结果（防止请求堆积）
    if (pendingFetchAll) {
      return pendingFetchAll
    }
    
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
    
    // 设置超时自动清理，防止请求堆积
    const timeoutId = setTimeout(() => {
      if (pendingFetchAll) {
        console.warn('[Metrics Store] fetchAllMetrics timeout, clearing pending request')
        pendingFetchAll = null
        loading.value = false
      }
    }, REQUEST_TIMEOUT)
    
    // 创建请求并保存引用（防止请求堆积）
    pendingFetchAll = (async () => {
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
        clearTimeout(timeoutId)
        loading.value = false
        pendingFetchAll = null  // 请求完成后清除引用
      }
    })()
    
    return pendingFetchAll
  }

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

