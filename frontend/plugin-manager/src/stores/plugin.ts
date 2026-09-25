/**
 * 插件状态管理
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  getPlugins,
  getPlugin,
  getPluginSummaries,
  getPluginStatus,
  startPlugin,
  stopPlugin,
  reloadPlugin,
  refreshPluginsRegistry,
} from '@/api/plugins'
import type { PluginListSummary } from '@/api/plugins'
import { getLocale, i18n } from '@/i18n'
import type { PluginMeta, PluginStatusData } from '@/types/api'
import { PluginStatus as StatusEnum } from '@/utils/constants'
import { reconcilePluginSnapshot } from '@/utils/reconcilePluginSnapshot'

type RegistrySyncResult = {
  registryRefreshed: boolean
  warningMessage: string | null
}

type RegistrySyncOptions = {
  preserveMessagesOn404?: boolean
}

type PluginMutationOptions = {
  refresh?: boolean
}

export const usePluginStore = defineStore('plugin', () => {
  // 状态
  const plugins = ref<PluginMeta[]>([])
  const pluginSummaries = ref<PluginListSummary[]>([])
  const pluginDetails = ref<Record<string, PluginMeta>>({})
  const pluginStatuses = ref<Record<string, PluginStatusData>>({})
  const selectedPluginId = ref<string | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const pluginsSnapshotLoaded = ref(false)
  const pluginsFetchedAt = ref(0)
  const pluginsFetchedLocale = ref<string | null>(null)
  const pluginStatusSnapshotLoaded = ref(false)
  const pluginStatusFetchedAt = ref(0)
  const PLUGIN_SNAPSHOT_MAX_AGE = 10_000
  const pluginSummarySnapshotLoaded = ref(false)
  const pluginSummaryFetchedAt = ref(0)
  const pluginSummaryFetchedLocale = ref<string | null>(null)
  
  // 防止请求堆积：正在进行的请求
  let pendingFetchPlugins: Promise<void> | null = null
  let pendingFetchPluginsLocale: string | null = null
  let pendingFetchStatus: Promise<void> | null = null
  let pendingFetchSummaries: Promise<void> | null = null
  let pendingFetchSummariesLocale: string | null = null
  const pendingFetchDetails = new Map<string, Promise<void>>()
  let pendingPluginListRegistrySync: Promise<RegistrySyncResult> | null = null
  const pluginListRegistrySynced = ref(false)
  // 请求超时自动清理（防止请求堆积）
  const REQUEST_TIMEOUT = 15000 // 15秒
  // 请求序列号，用于忽略过期响应
  let fetchPluginsSeq = 0
  let fetchStatusSeq = 0
  let fetchSummariesSeq = 0
  const fetchDetailSeq = new Map<string, number>()

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

  const pluginSummariesWithStatus = computed(() => pluginSummaries.value.map(plugin => {
    const status = pluginStatuses.value[plugin.id]
    const statusValue = status?.status
    return {
      ...plugin,
      status: typeof statusValue === 'string' ? statusValue : (plugin.status || StatusEnum.STOPPED),
      enabled: plugin.runtime_enabled !== false,
      autoStart: plugin.runtime_auto_start !== false,
    }
  }))

  const normalPlugins = computed(() => {
    return pluginsWithStatus.value
  })

  // 操作
  async function fetchPlugins(force = false, options: RegistrySyncOptions = {}) {
    const requestLocale = getLocale()
    // 防止请求堆积
    if (!force && pendingFetchPlugins && pendingFetchPluginsLocale === requestLocale) {
      return pendingFetchPlugins
    }
    
    loading.value = true
    error.value = null
    
    // 设置超时自动清理，防止请求堆积
    const seq = ++fetchPluginsSeq
    let timeoutId: ReturnType<typeof setTimeout> | null = null
    let timeoutReject: ((reason?: unknown) => void) | null = null
    timeoutId = setTimeout(() => {
      if (seq === fetchPluginsSeq && pendingFetchPlugins) {
        console.warn('[Plugin Store] fetchPlugins timeout, clearing pending request')
        fetchPluginsSeq += 1
        pendingFetchPlugins = null
        pendingFetchPluginsLocale = null
        loading.value = false
        timeoutReject?.(new Error('获取插件列表超时'))
      }
    }, REQUEST_TIMEOUT)
    pendingFetchPluginsLocale = requestLocale
    pendingFetchPlugins = (async () => {
      try {
        const response = await getPlugins(
          requestLocale,
          options.preserveMessagesOn404 ? { preserveMessagesOn404: true } : undefined,
        )
        // 忽略过期响应，防止旧数据覆盖新数据
        if (seq !== fetchPluginsSeq) return
        plugins.value = reconcilePluginSnapshot(plugins.value, response.plugins || [])
        pluginsSnapshotLoaded.value = true
        pluginsFetchedAt.value = Date.now()
        pluginsFetchedLocale.value = requestLocale
      } catch (err: any) {
        if (seq !== fetchPluginsSeq) return
        error.value = err.message || '获取插件列表失败'
        console.error('Failed to fetch plugins:', err)
      } finally {
        if (timeoutId) clearTimeout(timeoutId)
        if (seq === fetchPluginsSeq) {
          loading.value = false
          pendingFetchPlugins = null
          pendingFetchPluginsLocale = null
        }
      }
    })()
    const timeout = new Promise<void>((_, reject) => { timeoutReject = reject })
    pendingFetchPlugins = Promise.race([pendingFetchPlugins, timeout]).finally(() => {
      if (timeoutId) clearTimeout(timeoutId)
    })
    
    return pendingFetchPlugins
  }

  async function fetchPluginSummaries(force = false, options: RegistrySyncOptions = {}) {
    const requestLocale = getLocale()
    if (!force && pendingFetchSummaries && pendingFetchSummariesLocale === requestLocale) {
      return pendingFetchSummaries
    }
    const seq = ++fetchSummariesSeq
    pendingFetchSummariesLocale = requestLocale
    pendingFetchSummaries = (async () => {
      try {
        const response = await getPluginSummaries(requestLocale, options.preserveMessagesOn404
          ? { preserveMessagesOn404: true }
          : undefined)
        if (seq !== fetchSummariesSeq) return
        pluginSummaries.value = reconcilePluginSnapshot(pluginSummaries.value, response.plugins || [])
        pluginSummarySnapshotLoaded.value = true
        pluginSummaryFetchedAt.value = Date.now()
        pluginSummaryFetchedLocale.value = requestLocale
      } finally {
        if (seq === fetchSummariesSeq) {
          pendingFetchSummaries = null
          pendingFetchSummariesLocale = null
        }
      }
    })()
    return pendingFetchSummaries
  }

  async function ensurePluginSummaries(maxAgeMs = PLUGIN_SNAPSHOT_MAX_AGE) {
    const locale = getLocale()
    const fresh = pluginSummarySnapshotLoaded.value
      && pluginSummaryFetchedLocale.value === locale
      && Date.now() - pluginSummaryFetchedAt.value < maxAgeMs
    if (fresh) return
    await fetchPluginSummaries()
  }

  async function fetchPluginDetail(pluginId: string, force = false) {
    const existing = pendingFetchDetails.get(pluginId)
    if (existing && !force) return existing
    const seq = (fetchDetailSeq.get(pluginId) || 0) + 1
    fetchDetailSeq.set(pluginId, seq)
    let request!: Promise<void>
    request = (async () => {
      try {
        const detail = await getPlugin(pluginId, getLocale())
        if (fetchDetailSeq.get(pluginId) !== seq) return
        pluginDetails.value = { ...pluginDetails.value, [pluginId]: detail }
      } catch (error: any) {
        const status = error?.response?.status
        if (status !== 404 && status !== 405) throw error
        // Compatibility with older plugin servers: the old full list endpoint
        // remains a safe fallback when the single-plugin route is unavailable.
        const response = await getPlugins(getLocale())
        const detail = response.plugins?.find((plugin) => plugin.id === pluginId)
        if (detail && fetchDetailSeq.get(pluginId) === seq) {
          pluginDetails.value = { ...pluginDetails.value, [pluginId]: detail }
        }
      } finally {
        if (pendingFetchDetails.get(pluginId) === request) pendingFetchDetails.delete(pluginId)
      }
    })()
    pendingFetchDetails.set(pluginId, request)
    return request
  }

  async function ensurePlugin(pluginId: string) {
    if (pluginDetails.value[pluginId]) return pluginDetails.value[pluginId]
    const full = plugins.value.find(plugin => plugin.id === pluginId)
    if (full) {
      pluginDetails.value = { ...pluginDetails.value, [pluginId]: full }
      return full
    }
    await fetchPluginDetail(pluginId)
    return pluginDetails.value[pluginId] || null
  }

  function getPluginById(pluginId: string) {
    const plugin = pluginDetails.value[pluginId]
      || plugins.value.find(plugin => plugin.id === pluginId)
      || pluginSummaries.value.find(plugin => plugin.id === pluginId)
    if (!plugin) return null
    const status = pluginStatuses.value[pluginId]?.status
    return {
      ...plugin,
      status: typeof status === 'string' ? status : (plugin.status || StatusEnum.STOPPED),
      enabled: plugin.runtime_enabled !== false,
      autoStart: plugin.runtime_auto_start !== false,
    }
  }

  async function ensurePlugins(maxAgeMs = PLUGIN_SNAPSHOT_MAX_AGE) {
    const locale = getLocale()
    const fresh = pluginsSnapshotLoaded.value
      && pluginsFetchedLocale.value === locale
      && Date.now() - pluginsFetchedAt.value < maxAgeMs
    if (fresh) return
    await fetchPlugins()
  }

  async function syncRegistryAndFetch(options: RegistrySyncOptions = {}): Promise<RegistrySyncResult> {
    let registryRefreshed = false
    let warningMessage: string | null = null

    try {
      const response = await refreshPluginsRegistry(
        options.preserveMessagesOn404 ? { preserveMessagesOn404: true } : undefined,
      )
      registryRefreshed = true
      if (response.success === false) {
        const firstFailure = response.failed[0]
        if (firstFailure) {
          const failureTarget = firstFailure.plugin_id || firstFailure.config_path
          if (!failureTarget) {
            warningMessage = i18n.global.t('messages.pluginListRefreshPartialUnknown')
          } else {
            warningMessage = response.failed.length > 1
              ? i18n.global.t('messages.pluginListRefreshPartialMultiple', {
                  count: response.failed.length,
                  target: failureTarget,
                  error: firstFailure.error,
                })
              : i18n.global.t('messages.pluginListRefreshPartial', {
                  target: failureTarget,
                  error: firstFailure.error,
                })
          }
        } else {
          warningMessage = i18n.global.t('messages.pluginListRefreshPartialUnknown')
        }
      }
    } catch (err: any) {
      const status = err?.response?.status
      if (status !== 401 && status !== 403 && status !== 404) {
        throw err
      }
      warningMessage = status === 403
        ? i18n.global.t('messages.pluginListRefreshForbidden')
        : status === 404
          ? i18n.global.t('messages.resourceNotFound')
          : i18n.global.t('messages.pluginListRefreshUnauthenticated')
    }

    await fetchPlugins(true, options)
    pluginListRegistrySynced.value = true
    return {
      registryRefreshed,
      warningMessage,
    }
  }

  async function syncRegistryAndFetchSummaries(options: RegistrySyncOptions = {}): Promise<RegistrySyncResult> {
    let result: RegistrySyncResult
    try {
      const response = await refreshPluginsRegistry(
        options.preserveMessagesOn404 ? { preserveMessagesOn404: true } : undefined,
      )
      result = { registryRefreshed: true, warningMessage: null }
      if (response.success === false && response.failed[0]) {
        const firstFailure = response.failed[0]
        const target = firstFailure.plugin_id || firstFailure.config_path
        result.warningMessage = response.failed.length > 1
          ? i18n.global.t('messages.pluginListRefreshPartialMultiple', { count: response.failed.length, target, error: firstFailure.error })
          : i18n.global.t('messages.pluginListRefreshPartial', { target, error: firstFailure.error })
      }
    } catch (err: any) {
      const status = err?.response?.status
      if (status !== 401 && status !== 403 && status !== 404) throw err
      result = {
        registryRefreshed: false,
        warningMessage: status === 403
          ? i18n.global.t('messages.pluginListRefreshForbidden')
          : status === 404 ? i18n.global.t('messages.resourceNotFound') : i18n.global.t('messages.pluginListRefreshUnauthenticated'),
      }
    }
    await fetchPluginSummaries(true, options)
    pluginListRegistrySynced.value = true
    return result
  }

  async function ensurePluginListRegistrySynced(): Promise<RegistrySyncResult | null> {
    if (pluginListRegistrySynced.value) {
      return null
    }
    if (pendingPluginListRegistrySync) {
      return pendingPluginListRegistrySync
    }
    pendingPluginListRegistrySync = syncRegistryAndFetchSummaries().finally(() => {
      pendingPluginListRegistrySync = null
    })
    return pendingPluginListRegistrySync
  }

  async function fetchPluginStatus(pluginId?: string, force = false) {
    if (pluginId) {
      // A single-plugin mutation makes any in-flight full snapshot stale.
      fetchStatusSeq += 1
      pendingFetchStatus = null
      pluginStatusSnapshotLoaded.value = false
    }
    // 只对全量状态请求做防抖（单个插件状态请求不做限制）
    if (!pluginId && pendingFetchStatus && !force) {
      return pendingFetchStatus
    }
    
    // 设置超时自动清理（仅对全量请求）
    let timeoutId: ReturnType<typeof setTimeout> | null = null
    let timeoutReject: ((reason?: unknown) => void) | null = null
    const seq = !pluginId ? ++fetchStatusSeq : 0
    if (!pluginId) {
      timeoutId = setTimeout(() => {
        if (seq === fetchStatusSeq && pendingFetchStatus) {
          console.warn('[Plugin Store] fetchPluginStatus timeout, clearing pending request')
          fetchStatusSeq += 1
          pendingFetchStatus = null
          timeoutReject?.(new Error('获取插件状态超时'))
        }
      }, REQUEST_TIMEOUT)
    }
    
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
          pluginStatusSnapshotLoaded.value = true
          pluginStatusFetchedAt.value = Date.now()
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
      const timeout = new Promise<void>((_, reject) => { timeoutReject = reject })
      pendingFetchStatus = Promise.race([doFetch(), timeout])
      return pendingFetchStatus
    } else {
      return doFetch()
    }
  }

  async function ensurePluginStatus(maxAgeMs = PLUGIN_SNAPSHOT_MAX_AGE) {
    if (pluginStatusSnapshotLoaded.value && Date.now() - pluginStatusFetchedAt.value < maxAgeMs) return
    await fetchPluginStatus()
  }

  async function start(pluginId: string, options: PluginMutationOptions = {}) {
    try {
      await startPlugin(pluginId)
      if (options.refresh !== false) {
        await fetchPluginStatus(pluginId)
        await fetchPluginsAfterMutation()
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
        await fetchPluginsAfterMutation()
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
        await fetchPluginsAfterMutation()
      }
    } catch (err: any) {
      throw err
    }
  }

  async function fetchPluginsAfterMutation() {
    if (pluginSummarySnapshotLoaded.value) await fetchPluginSummaries(true)
    if (pluginsSnapshotLoaded.value) await fetchPlugins(true)
    for (const id of Object.keys(pluginDetails.value)) {
      await fetchPluginDetail(id, true)
    }
  }

  function setSelectedPlugin(pluginId: string | null) {
    selectedPluginId.value = pluginId
  }

  return {
    // 状态
    plugins,
    pluginSummaries,
    pluginDetails,
    pluginStatuses,
    selectedPluginId,
    selectedPlugin,
    pluginsWithStatus,
    pluginSummariesWithStatus,
    getPluginById,
    normalPlugins,
    pluginListRegistrySynced,
    loading,
    error,
    pluginsSnapshotLoaded,
    pluginStatusSnapshotLoaded,
    // 操作
    fetchPlugins,
    fetchPluginSummaries,
    ensurePluginSummaries,
    fetchPluginDetail,
    ensurePlugin,
    ensurePlugins,
    syncRegistryAndFetch,
    syncRegistryAndFetchSummaries,
    ensurePluginListRegistrySynced,
    fetchPluginStatus,
    ensurePluginStatus,
    start,
    stop,
    reload,
    setSelectedPlugin
  }
})
