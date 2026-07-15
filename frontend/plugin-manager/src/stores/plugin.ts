/**
 * 插件状态管理
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  getPlugins,
  getPluginStatus,
  startPlugin,
  stopPlugin,
  reloadPlugin,
  refreshPluginsRegistry,
} from '@/api/plugins'
import { getLocale } from '@/i18n'
import type { PluginMeta, PluginStatusData } from '@/types/api'
import { PluginStatus as StatusEnum } from '@/utils/constants'

type RegistrySyncResult = {
  registryRefreshed: boolean
  warningMessage: string | null
}

type PluginMutationOptions = {
  refresh?: boolean
}

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
  let pendingPluginListRegistrySync: Promise<RegistrySyncResult> | null = null
  const pluginListRegistrySynced = ref(false)
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
      const enabled = plugin.runtime_enabled !== false
      const autoStart = plugin.runtime_auto_start !== false
      // 不再把 `runtime_enabled=false` 提升成 DISABLED 状态：
      // 历史上 stop 写 `runtime_overrides.json[pid]=false`，下次启动 plugin
      // 不被 import，前端拿到 status=stopped 但又被 enabled=false 覆盖成
      // disabled，按钮被 isDisabled 拦截 → 用户"停过就再也开不起来"。
      // 现在直接信任 runtime status（stopped / running / load_failed），
      // start API 仍会把 override 翻回 true，所以"停过下次还停"的持久化
      // 行为不变，只是不再用一个独立的灰色 disabled 态遮蔽 start 按钮。
      const displayStatus = typeof plugin.status === 'string' ? plugin.status : StatusEnum.STOPPED
      
      return {
        ...plugin,
        status: displayStatus,
        enabled,
        autoStart
      }
    })
  })

  const normalPlugins = computed(() => {
    return pluginsWithStatus.value
  })

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
        const response = await getPlugins(getLocale())
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

  async function syncRegistryAndFetch(): Promise<RegistrySyncResult> {
    let registryRefreshed = false
    let warningMessage: string | null = null

    try {
      const response = await refreshPluginsRegistry()
      registryRefreshed = true
      if (response.success === false) {
        const firstFailure = response.failed[0]
        if (firstFailure) {
          const failureTarget = firstFailure.plugin_id || firstFailure.config_path
          warningMessage = response.failed.length > 1
            ? `插件注册表刷新有 ${response.failed.length} 项失败，首项为 ${failureTarget}: ${firstFailure.error}`
            : `插件注册表刷新失败: ${failureTarget}: ${firstFailure.error}`
        } else {
          warningMessage = '插件注册表刷新未完全成功'
        }
      }
    } catch (err: any) {
      const status = err?.response?.status
      if (status !== 401 && status !== 403) {
        throw err
      }
      warningMessage = status === 403
        ? '当前账号无权限刷新插件注册表，已仅重新拉取插件列表'
        : '当前会话未认证，已仅重新拉取插件列表'
    }

    await fetchPlugins(true)
    pluginListRegistrySynced.value = true
    return {
      registryRefreshed,
      warningMessage,
    }
  }

  async function ensurePluginListRegistrySynced(): Promise<RegistrySyncResult | null> {
    if (pluginListRegistrySynced.value) {
      return null
    }
    if (pendingPluginListRegistrySync) {
      return pendingPluginListRegistrySync
    }
    pendingPluginListRegistrySync = syncRegistryAndFetch().finally(() => {
      pendingPluginListRegistrySync = null
    })
    return pendingPluginListRegistrySync
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

  async function start(pluginId: string, options: PluginMutationOptions = {}) {
    try {
      await startPlugin(pluginId)
      if (options.refresh !== false) {
        await fetchPluginStatus(pluginId)
        await fetchPlugins(true)
      }
    } catch (err: any) {
      throw err
    }
  }

  async function stop(pluginId: string, options: PluginMutationOptions = {}) {
    try {
      await stopPlugin(pluginId)
      if (options.refresh !== false) {
        await fetchPluginStatus(pluginId)
        await fetchPlugins(true)
      }
    } catch (err: any) {
      throw err
    }
  }

  async function reload(pluginId: string, options: PluginMutationOptions = {}) {
    try {
      await reloadPlugin(pluginId)
      if (options.refresh !== false) {
        await fetchPluginStatus(pluginId)
        await fetchPlugins(true)
      }
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
    pluginListRegistrySynced,
    loading,
    error,
    // 操作
    fetchPlugins,
    syncRegistryAndFetch,
    ensurePluginListRegistrySynced,
    fetchPluginStatus,
    start,
    stop,
    reload,
    setSelectedPlugin
  }
})
