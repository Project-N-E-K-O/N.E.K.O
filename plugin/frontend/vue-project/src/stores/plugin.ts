/**
 * 插件状态管理
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getPlugins, getPluginStatus, startPlugin, stopPlugin, reloadPlugin } from '@/api/plugins'
import type { PluginMeta, PluginStatusData } from '@/types/api'
import { PluginStatus as StatusEnum } from '@/utils/constants'

export const usePluginStore = defineStore('plugin', () => {
  // 状态
  const plugins = ref<PluginMeta[]>([])
  const pluginStatuses = ref<Record<string, PluginStatusData>>({})
  const selectedPluginId = ref<string | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  // 计算属性
  const selectedPlugin = computed(() => {
    if (!selectedPluginId.value) return null
    return plugins.value.find(p => p.id === selectedPluginId.value) || null
  })

  const pluginsWithStatus = computed(() => {
    return plugins.value.map(plugin => {
      const statusData = pluginStatuses.value[plugin.id]
      // statusData 的结构: { plugin_id, status: { status: "running", ... }, updated_at, source }
      // 需要从 statusData.status.status 中提取状态字符串
      let statusValue: string = StatusEnum.STOPPED
      
      if (statusData) {
        const statusObj = statusData.status
        if (statusObj) {
          if (typeof statusObj === 'string') {
            // 如果 status 直接是字符串
            statusValue = statusObj
          } else if (typeof statusObj === 'object' && statusObj !== null) {
            // 如果 status 是对象，尝试提取 status 字段
            const nestedStatus = (statusObj as any).status
            if (typeof nestedStatus === 'string') {
              statusValue = nestedStatus
            } else {
              // 如果嵌套的 status 也不是字符串，使用默认值
              statusValue = StatusEnum.STOPPED
            }
          }
        }
      }
      
      // 确保返回的 status 始终是字符串
      const finalStatus = typeof statusValue === 'string' ? statusValue : StatusEnum.STOPPED
      
      return {
        ...plugin,
        status: finalStatus
      }
    })
  })

  /**
   * Loads the plugin list from the backend and updates the store state.
   *
   * Sets `loading` while the request is in progress, replaces `plugins` with the fetched list (or an empty array if none), and stores any error message in `error`. Always clears the `loading` flag when finished.
   */
  async function fetchPlugins() {
    loading.value = true
    error.value = null
    try {
      const response = await getPlugins()
      plugins.value = response.plugins || []
    } catch (err: any) {
      error.value = err.message || '获取插件列表失败'
      console.error('Failed to fetch plugins:', err)
    } finally {
      loading.value = false
    }
  }

  /**
   * Fetches status information for a specific plugin or for all plugins and updates the store state.
   *
   * @param pluginId - If provided, fetches and stores the status for that plugin; if omitted, fetches statuses for all plugins and replaces the store's status map
   */
  async function fetchPluginStatus(pluginId?: string) {
    try {
      const response = await getPluginStatus(pluginId)
      if (pluginId) {
        // 单个插件状态
        pluginStatuses.value[pluginId] = response as PluginStatusData
      } else {
        // 所有插件状态
        const statuses = response as { plugins: Record<string, PluginStatusData> }
        pluginStatuses.value = statuses.plugins || {}
      }
    } catch (err: any) {
      console.error('Failed to fetch plugin status:', err)
    }
  }

  /**
   * Start the specified plugin then refresh its status and the overall plugin list.
   *
   * @param pluginId - The identifier of the plugin to start
   * @throws Any error raised by the start, status fetch, or plugin list fetch operations
   */
  async function start(pluginId: string) {
    try {
      await startPlugin(pluginId)
      // 刷新状态
      await fetchPluginStatus(pluginId)
      await fetchPlugins()
    } catch (err: any) {
      throw err
    }
  }

  /**
   * Stops the specified plugin, then refreshes that plugin's status and the overall plugin list.
   *
   * @param pluginId - The identifier of the plugin to stop
   * @throws The error thrown by the stop operation or by subsequent status/list refreshes
   */
  async function stop(pluginId: string) {
    try {
      await stopPlugin(pluginId)
      // 刷新状态
      await fetchPluginStatus(pluginId)
      await fetchPlugins()
    } catch (err: any) {
      throw err
    }
  }

  /**
   * Reloads the specified plugin and refreshes its status and the plugin list.
   *
   * @param pluginId - The identifier of the plugin to reload
   * @throws Propagates any error encountered while reloading the plugin or refreshing statuses
   */
  async function reload(pluginId: string) {
    try {
      await reloadPlugin(pluginId)
      // 刷新状态
      await fetchPluginStatus(pluginId)
      await fetchPlugins()
    } catch (err: any) {
      throw err
    }
  }

  /**
   * Selects a plugin by its id or clears the current selection.
   *
   * @param pluginId - The id of the plugin to select; pass `null` to clear the selection
   */
  function setSelectedPlugin(pluginId: string | null) {
    selectedPluginId.value = pluginId
  }

  return {
    // 状态
    plugins,
    pluginStatuses,
    selectedPluginId,
    selectedPlugin,
    pluginsWithStatus,
    loading,
    error,
    // 操作
    fetchPlugins,
    fetchPluginStatus,
    start,
    stop,
    reload,
    setSelectedPlugin
  }
})
