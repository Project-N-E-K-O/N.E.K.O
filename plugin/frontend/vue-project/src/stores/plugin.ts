/**
 * 插件状态管理
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getPlugins, getPluginStatus, startPlugin, stopPlugin, reloadPlugin, disableExtension, enableExtension } from '@/api/plugins'
import type { PluginMeta, PluginStatusData } from '@/types/api'
import { PluginStatus as StatusEnum } from '@/utils/constants'

export const usePluginStore = defineStore('plugin', () => {
  // 状态
  const plugins = ref<PluginMeta[]>([])
  const pluginStatuses = ref<Record<string, PluginStatusData>>({})
  const selectedPluginId = ref<string | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  
  // 防止请求堆积：正在进行的请求
  let pendingFetchPlugins: Promise<void> | null = null
  let pendingFetchStatus: Promise<void> | null = null
  // 请求超时自动清理（防止请求堆积）
  const REQUEST_TIMEOUT = 15000 // 15秒
  // 请求序列号，用于忽略过期响应
  let fetchPluginsSeq = 0
  let fetchStatusSeq = 0

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
            statusValue = statusObj
          } else if (typeof statusObj === 'object' && statusObj !== null) {
            const nestedStatus = (statusObj as any).status
            if (typeof nestedStatus === 'string') {
              statusValue = nestedStatus
            } else {
              statusValue = StatusEnum.STOPPED
            }
          }
        }
      }
      
      const finalStatus = typeof statusValue === 'string' ? statusValue : StatusEnum.STOPPED

      const enabled = plugin.runtime_enabled !== false
      const autoStart = plugin.runtime_auto_start !== false
      const isExtension = plugin.type === 'extension'

      // Extension 状态由后端 build_plugin_list 推导（injected/pending/disabled），
      // 直接使用 GET /plugins 返回的 status 字段，因为 Extension 不是独立进程，
      // pluginStatuses（GET /plugin/status）中不会有它的数据。
      let displayStatus: string
      if (isExtension) {
        displayStatus = typeof plugin.status === 'string' ? plugin.status : StatusEnum.PENDING
      } else {
        displayStatus = enabled ? finalStatus : StatusEnum.DISABLED
      }
      
      return {
        ...plugin,
        status: displayStatus,
        enabled,
        autoStart
      }
    })
  })

  const normalPlugins = computed(() => {
    return pluginsWithStatus.value.filter(p => p.type !== 'extension')
  })

  const extensions = computed(() => {
    return pluginsWithStatus.value.filter(p => p.type === 'extension')
  })

  function getExtensionsForHost(hostPluginId: string) {
    return extensions.value.filter(e => e.host_plugin_id === hostPluginId)
  }

  // 操作
  async function fetchPlugins(force = false) {
    // 防止请求堆积
    if (!force && pendingFetchPlugins) {
      return pendingFetchPlugins
    }
    
    loading.value = true
    error.value = null
    
    // 设置超时自动清理，防止请求堆积
    const timeoutId = setTimeout(() => {
      if (pendingFetchPlugins) {
        console.warn('[Plugin Store] fetchPlugins timeout, clearing pending request')
        pendingFetchPlugins = null
        loading.value = false
      }
    }, REQUEST_TIMEOUT)
    
    const seq = ++fetchPluginsSeq
    pendingFetchPlugins = (async () => {
      try {
        const response = await getPlugins()
        // 忽略过期响应，防止旧数据覆盖新数据
        if (seq !== fetchPluginsSeq) return
        plugins.value = response.plugins || []
      } catch (err: any) {
        if (seq !== fetchPluginsSeq) return
        error.value = err.message || '获取插件列表失败'
        console.error('Failed to fetch plugins:', err)
      } finally {
        clearTimeout(timeoutId)
        if (seq === fetchPluginsSeq) {
          loading.value = false
          pendingFetchPlugins = null
        }
      }
    })()
    
    return pendingFetchPlugins
  }

  async function fetchPluginStatus(pluginId?: string) {
    // 只对全量状态请求做防抖（单个插件状态请求不做限制）
    if (!pluginId && pendingFetchStatus) {
      return pendingFetchStatus
    }
    
    // 设置超时自动清理（仅对全量请求）
    let timeoutId: ReturnType<typeof setTimeout> | null = null
    if (!pluginId) {
      timeoutId = setTimeout(() => {
        if (pendingFetchStatus) {
          console.warn('[Plugin Store] fetchPluginStatus timeout, clearing pending request')
          pendingFetchStatus = null
        }
      }, REQUEST_TIMEOUT)
    }
    
    // 仅对全量请求使用序列号
    const seq = !pluginId ? ++fetchStatusSeq : 0
    
    const doFetch = async () => {
      try {
        const response = await getPluginStatus(pluginId)
        // 忽略过期响应（仅对全量请求）
        if (!pluginId && seq !== fetchStatusSeq) return
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
      } finally {
        if (timeoutId) clearTimeout(timeoutId)
        if (!pluginId && seq === fetchStatusSeq) {
          pendingFetchStatus = null
        }
      }
    }
    
    if (!pluginId) {
      pendingFetchStatus = doFetch()
      return pendingFetchStatus
    } else {
      return doFetch()
    }
  }

  async function start(pluginId: string) {
    try {
      await startPlugin(pluginId)
      await fetchPluginStatus(pluginId)
      await fetchPlugins(true)
    } catch (err: any) {
      throw err
    }
  }

  async function stop(pluginId: string) {
    try {
      await stopPlugin(pluginId)
      await fetchPluginStatus(pluginId)
      await fetchPlugins(true)
    } catch (err: any) {
      throw err
    }
  }

  async function reload(pluginId: string) {
    try {
      await reloadPlugin(pluginId)
      await fetchPluginStatus(pluginId)
      await fetchPlugins(true)
    } catch (err: any) {
      throw err
    }
  }

  async function disableExt(extId: string) {
    try {
      await disableExtension(extId)
      await fetchPlugins()
    } catch (err: any) {
      throw err
    }
  }

  async function enableExt(extId: string) {
    try {
      await enableExtension(extId)
      await fetchPlugins()
    } catch (err: any) {
      throw err
    }
  }

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
    normalPlugins,
    extensions,
    loading,
    error,
    // 操作
    fetchPlugins,
    fetchPluginStatus,
    start,
    stop,
    reload,
    disableExt,
    enableExt,
    getExtensionsForHost,
    setSelectedPlugin
  }
})

