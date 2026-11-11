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
  let openIds = ''
  const handledIds = new Set<string>()
  const watchedIds = new Set<string>()
  let stopped = false

  function close() {
    handle?.close()
    handle = null
    openIds = ''
  }

  function open(notices: PluginConsentNotice[]) {
    close()
    handle = ElNotification(
      {
        title: i18n.global.t('pluginConsent.noticesTitle'),
        message: h(PluginConsentNoticeView, {
          notices,
          onHandled: (pluginId: string) => handledIds.add(pluginId),
          onDone: () => close(),
        }),
        duration: 0,
        type: 'warning',
      },
      appContext,
    )
    openIds = notices.map((notice) => notice.plugin_id).join(',')
  }

  async function refresh() {
    try {
      const fetched = await getPluginConsentNotices()
      if (stopped) return
      const notices = fetched.filter((notice) => !handledIds.has(notice.plugin_id))
      const ids = notices.map((notice) => notice.plugin_id).join(',')
      // Reopen only when the unhandled set changed; an open notification is
      // left alone so in-flight clicks are not dropped.
      if (ids && ids !== openIds) open(notices)
    } catch (error) {
      console.warn('Failed to load plugin consent notices:', error)
    }
  }

  // Running plugins still being analyzed are followed via the consent store;
  // when one turns "confirm" the notice list is refetched.
  const analyzingIds = computed(() =>
    pluginStore.pluginSummariesWithStatus
      .filter(
        (plugin) =>
          plugin.status === 'running' && consentState.stateOf(plugin.id)?.state === 'analyzing',
      )
      .map((plugin) => plugin.id),
  )
  watch(
    analyzingIds,
    (ids, previous) => {
      const next = new Set(ids)
      for (const id of previous ?? []) {
        if (!next.has(id)) {
          watchedIds.delete(id)
          consentState.unwatchPlugin(id)
          // The transition happened here, while the id is still tracked:
          // a watched plugin that just turned "confirm" may need a notice.
          if (consentState.stateOf(id)?.state === 'confirm') void refresh()
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
