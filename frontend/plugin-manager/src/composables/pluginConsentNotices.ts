import { computed, getCurrentInstance, h, onMounted, onUnmounted, watch } from 'vue'
import { ElNotification, type NotificationHandle } from 'element-plus'
import { i18n, localeLoadState } from '@/i18n'
import { getPluginConsentNotices, type PluginConsentNotice } from '@/api/pluginConsent'
import { usePluginConsentState } from '@/stores/pluginConsentState'
import { usePluginStore } from '@/stores/plugin'
import PluginConsentNoticeView from '@/components/plugin/PluginConsentNotice.vue'

/**
 * Owns the "unconfirmed capabilities" notice for one visit of the plugin
 * list page: fetch on entry, then follow running plugins whose consent state
 * is "analyzing" through the consent store (the single polling source) and
 * refetch when one settles. Shows at most one notification; rows the user
 * already handled in this visit are not re-added; the next visit starts fresh.
 */
export function usePluginConsentNotices() {
  // Service-rendered notifications mount outside the app: capture the app
  // context in setup so the notice can inject pinia and i18n.
  const appContext = getCurrentInstance()?.appContext
  const pluginStore = usePluginStore()
  const consentState = usePluginConsentState()
  let handle: NotificationHandle | null = null
  let openSnapshot = ''
  const handledIds = new Set<string>()
  const watchedIds = new Set<string>()
  let stopped = false
  let refreshSequence = 0

  function close() {
    handle?.close()
    handle = null
    openSnapshot = ''
  }

  function open(notices: PluginConsentNotice[], snapshot: string) {
    close()
    handle = ElNotification(
      {
        title: i18n.global.t('pluginConsent.noticesTitle'),
        message: h(PluginConsentNoticeView, {
          notices,
          onHandled: (pluginId: string) => handledIds.add(pluginId),
          onDone: () => {
            if (openSnapshot === snapshot) close()
          },
          onStale: () => void refresh(),
        }),
        duration: 0,
        type: 'warning',
      },
      appContext,
    )
    openSnapshot = snapshot
  }

  async function refresh() {
    const sequence = ++refreshSequence
    try {
      const fetched = await getPluginConsentNotices()
      if (stopped || sequence !== refreshSequence) return
      if (fetched.some((notice) => !handledIds.has(notice.plugin_id))) {
        await import('element-plus/theme-chalk/el-notification.css')
        if (stopped || sequence !== refreshSequence) return
      }
      const notices = fetched.filter((notice) => !handledIds.has(notice.plugin_id))
      const snapshot = JSON.stringify(notices)
      if (!notices.length) close()
      else if (snapshot !== openSnapshot) open(notices, snapshot)
    } catch (error) {
      console.warn('Failed to load plugin consent notices:', error)
    }
  }

  const runningConsent = computed(() =>
    pluginStore.pluginSummariesWithStatus
      .filter((plugin) => plugin.status === 'running')
      .map((plugin) => ({ id: plugin.id, state: consentState.stateOf(plugin.id)?.state })),
  )
  const analyzingIds = computed(() =>
    runningConsent.value.filter((plugin) => plugin.state === 'analyzing').map(({ id }) => id),
  )
  const confirmingIds = computed(() =>
    runningConsent.value.filter((plugin) => plugin.state === 'confirm').map(({ id }) => id),
  )
  watch(
    analyzingIds,
    (ids, previous) => {
      const next = new Set(ids)
      for (const id of previous ?? []) {
        if (!next.has(id)) {
          watchedIds.delete(id)
          consentState.unwatchPlugin(id)
        }
      }
      for (const id of ids) {
        if (!watchedIds.has(id)) {
          watchedIds.add(id)
          consentState.watchPlugin(id)
        }
      }
    },
    { immediate: true },
  )

  watch(confirmingIds, (ids, previous) => {
    if (localeLoadState.pending !== null) return
    const previousIds = new Set(previous)
    if (ids.length !== previousIds.size || ids.some((id) => !previousIds.has(id))) {
      void refresh()
    }
  })

  onMounted(() => {
    // The notice title is baked into the notification: wait for the user's
    // locale before composing it.
    if (localeLoadState.pending === null) {
      void refresh()
      return
    }
    const stop = watch(
      () => localeLoadState.pending,
      (pending) => {
        if (pending === null) {
          stop()
          void refresh()
        }
      },
    )
  })
  onUnmounted(() => {
    stopped = true
    for (const id of watchedIds) consentState.unwatchPlugin(id)
    watchedIds.clear()
    close()
  })
}
