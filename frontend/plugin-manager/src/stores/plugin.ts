/**
 * 插件状态管理
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { AxiosError } from 'axios'
import { readErrorCode } from '@/utils/request'
import {
  getPlugins,
  getPlugin,
  getPluginSummaries,
  getPluginStatus,
  startPlugin,
  stopPlugin,
  reloadPlugin,
  reloadAllPlugins,
  refreshPluginsRegistry,
} from '@/api/plugins'
import { getPluginConfigApplicationState } from '@/api/config'
import type { PluginListSummary } from '@/api/plugins'
import { getLocale, i18n } from '@/i18n'
import type { PluginMeta, PluginStatusData } from '@/types/api'
import { PluginStatus as StatusEnum } from '@/utils/constants'
import { reconcilePluginSnapshot } from '@/utils/reconcilePluginSnapshot'
import {
  pendingReloadPlugins,
  pendingReloadRevision,
  pendingReloadRevisionSnapshot,
  setPendingReload,
  hasPendingReload,
} from '@/utils/pendingReload'

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
  const pluginSummaries = ref<PluginListSummary[]>([])
  const pluginDetails = ref<Record<string, PluginMeta>>({})
  const pluginStatuses = ref<Record<string, PluginStatusData>>({})
  const pluginStatusSnapshotLoaded = ref(false)
  const pluginStatusFetchedAt = ref(0)
  const PLUGIN_SNAPSHOT_MAX_AGE = 10_000
  const pluginSummarySnapshotLoaded = ref(false)
  const pluginSummaryFetchedAt = ref(0)
  const pluginSummaryFetchedLocale = ref<string | null>(null)
  
  // 防止请求堆积：正在进行的请求
  let pendingFetchStatus: Promise<void> | null = null
  let pendingFetchSummaries: Promise<void> | null = null
  let pendingFetchSummariesLocale: string | null = null
  const pendingFetchDetails = new Map<string, Promise<void>>()
  let pendingPluginListRegistrySync: Promise<RegistrySyncResult> | null = null
  const pluginListRegistrySynced = ref(false)
  // 请求超时自动清理（防止请求堆积）
  const REQUEST_TIMEOUT = 15000 // 15秒
  // 请求序列号，用于忽略过期响应
  let fetchStatusSeq = 0
  let fetchSummariesSeq = 0
  const fetchDetailSeq = new Map<string, number>()
  const applicationStateQuerySeq = new Map<string, number>()
  const applicationStateAppliedSeq = new Map<string, number>()

  // 不再把 `runtime_enabled=false` 提升成 DISABLED 状态：
  // 历史上 stop 写 `runtime_overrides.json[pid]=false`，下次启动 plugin
  // 不被 import，前端拿到 status=stopped 但又被 enabled=false 覆盖成
  // disabled，按钮被 isDisabled 拦截 → 用户"停过就再也开不起来"。
  // 现在直接信任 runtime status（stopped / running / load_failed），
  // start API 仍会把 override 翻回 true，所以"停过下次还停"的持久化
  // 行为不变，只是不再用一个独立的灰色 disabled 态遮蔽 start 按钮。
  function withDisplayState<P extends PluginListSummary>(plugin: P) {
    return {
      ...plugin,
      status: typeof plugin.status === 'string' ? plugin.status : StatusEnum.STOPPED,
      enabled: plugin.runtime_enabled !== false,
      autoStart: plugin.runtime_auto_start !== false,
    }
  }

  // Read precedence: detail > summary.
  function resolvePluginById(pluginId: string) {
    const plugin = pluginDetails.value[pluginId]
      || pluginSummaries.value.find(item => item.id === pluginId)
    return plugin ? withDisplayState(plugin) : null
  }

  const pluginSummariesWithStatus = computed(() => pluginSummaries.value.map(withDisplayState))

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
        const nextSummaries = response.plugins || []
        pruneDetails(new Set(nextSummaries.map(plugin => plugin.id)))
        pluginSummaries.value = reconcilePluginSnapshot(pluginSummaries.value, nextSummaries)
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
    const requestLocale = getLocale()
    const seq = (fetchDetailSeq.get(pluginId) || 0) + 1
    fetchDetailSeq.set(pluginId, seq)
    let request!: Promise<void>
    // A locale switch or a summary that dropped this plugin bumps the fence.
    // The late response must not republish the detail it fetched.
    const stillCurrent = () => fetchDetailSeq.get(pluginId) === seq && getLocale() === requestLocale
    request = (async () => {
      try {
        const detail = await getPlugin(pluginId, requestLocale)
        if (!stillCurrent()) return
        pluginDetails.value = { ...pluginDetails.value, [pluginId]: detail }
      } catch (error: any) {
        const status = error?.response?.status
        if (status !== 404 && status !== 405) throw error
        // Compatibility with older plugin servers: the old full list endpoint
        // remains a safe fallback when the single-plugin route is unavailable.
        const response = await getPlugins(requestLocale)
        const detail = response.plugins?.find((plugin) => plugin.id === pluginId)
        if (!stillCurrent()) return
        if (detail) {
          pluginDetails.value = { ...pluginDetails.value, [pluginId]: detail }
        } else if (pluginId in pluginDetails.value) {
          const rest = { ...pluginDetails.value }
          delete rest[pluginId]
          pluginDetails.value = rest
        }
      } finally {
        if (pendingFetchDetails.get(pluginId) === request) pendingFetchDetails.delete(pluginId)
      }
    })()
    pendingFetchDetails.set(pluginId, request)
    return request
  }

  // Installs and upgrades only refresh summaries, so a cached copy is served
  // immediately and revalidated in the background.
  async function ensurePlugin(pluginId: string) {
    const cached = pluginDetails.value[pluginId]
    if (cached) {
      fetchPluginDetail(pluginId).catch(err => console.warn(`Failed to revalidate plugin ${pluginId}:`, err))
      return cached
    }
    let current = fetchPluginDetail(pluginId)
    await current
    // A locale refresh may have replaced the request while this one was in flight.
    for (;;) {
      const pending = pendingFetchDetails.get(pluginId)
      if (!pending || pending === current) break
      current = pending
      await current
    }
    return pluginDetails.value[pluginId] || null
  }

  function invalidateDetail(pluginId: string) {
    fetchDetailSeq.set(pluginId, (fetchDetailSeq.get(pluginId) || 0) + 1)
  }

  function pruneDetails(liveIds: ReadonlySet<string>) {
    const ids = new Set([...Object.keys(pluginDetails.value), ...pendingFetchDetails.keys()])
    let dropped = false
    for (const id of ids) {
      if (liveIds.has(id)) continue
      invalidateDetail(id)
      dropped = true
    }
    if (!dropped) return
    pluginDetails.value = Object.fromEntries(
      Object.entries(pluginDetails.value).filter(([id]) => liveIds.has(id)),
    )
  }

  function getPluginById(pluginId: string) {
    return resolvePluginById(pluginId)
  }

  async function refreshLoadedPluginData(options: RegistrySyncOptions = {}) {
    const tasks: Promise<unknown>[] = []
    if (pluginSummarySnapshotLoaded.value) {
      tasks.push(fetchPluginSummaries(true, options))
    }
    // Include requests that have not landed yet. A locale switch otherwise
    // leaves that response free to publish the previous language.
    const detailIds = new Set([
      ...Object.keys(pluginDetails.value),
      ...pendingFetchDetails.keys(),
    ])
    for (const id of detailIds) {
      tasks.push(fetchPluginDetail(id, true))
    }
    if (tasks.length === 0) {
      tasks.push(fetchPluginSummaries(true, options))
    }
    await Promise.all(tasks)
  }

  async function syncRegistryAndFetchSummaries(options: RegistrySyncOptions = {}): Promise<RegistrySyncResult> {
    let result: RegistrySyncResult
    try {
      const response = await refreshPluginsRegistry(
        options.preserveMessagesOn404 ? { preserveMessagesOn404: true } : undefined,
      )
      result = { registryRefreshed: true, warningMessage: null }
      if (response.success === false) {
        const firstFailure = response.failed[0]
        const target = firstFailure?.plugin_id || firstFailure?.config_path
        result.warningMessage = !target
          ? i18n.global.t('messages.pluginListRefreshPartialUnknown')
          : response.failed.length > 1
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

  /**
   * Refresh the server-owned config application state. A missing endpoint or
   * malformed response leaves the window-local hint untouched for compatibility
   * with older plugin servers. Matched/not-running are the only states that clear
   * a hint; pending/unknown keep it visible.
   */
  async function syncPluginApplicationState(
    pluginId: string,
    expectedRevision = pendingReloadRevision(pluginId),
    legacyLifecycleApplied = false,
  ): Promise<boolean> {
    const requestSeq = (applicationStateQuerySeq.get(pluginId) ?? 0) + 1
    applicationStateQuerySeq.set(pluginId, requestSeq)
    const canApplyResponse = () => {
      const appliedSeq = applicationStateAppliedSeq.get(pluginId) ?? 0
      if (requestSeq < appliedSeq) return false
      applicationStateAppliedSeq.set(pluginId, requestSeq)
      return true
    }
    let raw: unknown
    try {
      raw = await getPluginConfigApplicationState(pluginId)
    } catch (error) {
      const status = (error as { response?: { status?: unknown } } | null)?.response?.status
      // Older plugin servers do not expose application-state. A successful
      // lifecycle operation is the only compatibility evidence available there;
      // network errors remain conservative and keep the hint visible.
      if (
        legacyLifecycleApplied &&
        (status === 404 || status === 405) &&
        !readErrorCode(error as AxiosError)
      ) {
        if (!canApplyResponse()) return false
        return setPendingReload(pluginId, false, expectedRevision)
      }
      return false
    }
    if (!raw || typeof raw !== 'object') return false
    const state = raw as { plugin_id?: unknown; config_state?: unknown }
    if (
      state.plugin_id !== pluginId ||
      state.config_state !== 'matched' &&
        state.config_state !== 'pending' &&
        state.config_state !== 'not_running' &&
        state.config_state !== 'unknown'
    ) {
      return false
    }
    if (!canApplyResponse()) return false
    const pending = state.config_state === 'pending' || state.config_state === 'unknown'
    if (pending) {
      if (!hasPendingReload(pluginId)) setPendingReload(pluginId, true, expectedRevision)
    } else {
      // A save that landed while this query was in flight wins over an older
      // matched response, just like the previous local revision guard.
      setPendingReload(pluginId, false, expectedRevision)
    }
    return true
  }

  async function start(pluginId: string, options: PluginMutationOptions = {}) {
    const pendingRevision = pendingReloadRevision(pluginId)
    const result = await startPlugin(pluginId)
    // The server knows which effective config the host actually loaded. Keep the
    // in-memory flag only as a compatibility fallback for older servers.
    await syncPluginApplicationState(
      pluginId,
      pendingRevision,
      result.success === true && result.already_running !== true,
    )
    if (options.refresh !== false) await refreshAfterMutation(pluginId)
  }

  async function stop(pluginId: string, options: PluginMutationOptions = {}) {
    const pendingRevision = pendingReloadRevision(pluginId)
    await stopPlugin(pluginId)
    await syncPluginApplicationState(pluginId, pendingRevision)
    if (options.refresh !== false) await refreshAfterMutation(pluginId)
  }

  async function reload(pluginId: string, options: PluginMutationOptions = {}) {
    const pendingRevision = pendingReloadRevision(pluginId)
    const result = await reloadPlugin(pluginId)
    await syncPluginApplicationState(pluginId, pendingRevision, result.success === true)
    if (options.refresh !== false) await refreshAfterMutation(pluginId)
  }

  async function reloadAll(options: PluginMutationOptions = {}) {
    // The bulk endpoint restarts every plugin it reports back, so those hosts match their
    // saved configuration again and the flags have to go with it. Capture the revisions
    // first for the same reason as the single-plugin path: a profile write that lands during
    // the request describes a configuration the restarted host cannot have read. The flagged
    // plugins are included because the server restarts hosts from its own running set, which
    // can be ahead of (or behind) the list this window last loaded.
    const baseline = [
      ...new Set([...pluginSummaries.value.map((p) => p.id), ...pendingReloadPlugins()]),
    ]
    const baselineIds = new Set(baseline)
    const revisions = pendingReloadRevisionSnapshot()
    for (const id of baseline) revisions.set(id, pendingReloadRevision(id))
    const result = await reloadAllPlugins()
    await Promise.all(result.reloaded.map(async (pluginId) => {
      // Unknown plugins still receive a revision fence. A save during the bulk
      // request must not be hidden by a matched response based on old config.
      const revision = revisions.get(pluginId) ?? 0
      await syncPluginApplicationState(pluginId, revision, baselineIds.has(pluginId))
    }))
    // The reload already happened; a follow-up refresh that fails or times out must not
    // turn its result into a failure for the caller.
    if (options.refresh !== false) {
      try {
        await fetchPluginStatus()
        await refreshLoadedPluginData()
      } catch (err) {
        console.warn('Failed to refresh plugin data after reloading all plugins:', err)
      }
    }
    return result
  }

  // The mutation already succeeded; a failed follow-up refresh must not be
  // reported to the caller as a failed start/stop/reload.
  async function refreshAfterMutation(pluginId: string) {
    await fetchPluginStatus(pluginId)
    try {
      await refreshLoadedPluginData()
    } catch (err) {
      console.warn('Failed to refresh plugin data after mutation:', err)
    }
  }

  return {
    // 状态
    pluginSummaries,
    pluginDetails,
    pluginStatuses,
    pluginSummariesWithStatus,
    getPluginById,
    pluginListRegistrySynced,
    pluginStatusSnapshotLoaded,
    // 操作
    fetchPluginSummaries,
    ensurePluginSummaries,
    fetchPluginDetail,
    ensurePlugin,
    refreshLoadedPluginData,
    syncRegistryAndFetchSummaries,
    ensurePluginListRegistrySynced,
    fetchPluginStatus,
    ensurePluginStatus,
    start,
    stop,
    reload,
    reloadAll,
  }
})
