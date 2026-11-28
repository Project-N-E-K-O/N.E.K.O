import { inject, reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { usePluginStore } from '@/stores/plugin'
import { formatHttpError } from '@/utils/request'
import { PLUGIN_DETAIL_REFRESH_HOSTED_PANELS_KEY } from '@/views/pluginDetailHostedPanelRefresh'

/**
 * Show an action error only when the request interceptor did not already:
 * no message for a request without a response or for statuses it reports.
 */
export function showActionError(error: unknown, fallbackMessage: string) {
  const failure = error as { request?: unknown; response?: { status?: unknown } } | null
  const status = failure?.response?.status
  if (failure?.request && !failure?.response) return
  if (typeof status === 'number' && ![401, 403, 404].includes(status)) return
  ElMessage.error(formatHttpError(error) || fallbackMessage)
}

// Shared by every stop button of the page: one confirmation and one request
// per plugin, whichever button was clicked first.
const inFlight = new Map<string, Promise<void>>()
const stoppingIds = reactive(new Set<string>())

/** Stopping a plugin from the detail page: confirm, stop, refresh hosted panels. */
export function usePluginStopAction() {
  const { t } = useI18n()
  const store = usePluginStore()
  const refreshHostedPanels = inject<(() => Promise<void>) | null>(
    PLUGIN_DETAIL_REFRESH_HOSTED_PANELS_KEY,
    null,
  )

  async function run(pluginId: string) {
    try {
      await ElMessageBox.confirm(t('messages.confirmStop'), t('common.confirm'), { type: 'warning' })
      stoppingIds.add(pluginId)
      await store.stop(pluginId)
      void refreshHostedPanels?.().catch(() => {})
      ElMessage.success(t('messages.pluginStopped'))
    } catch (error) {
      if (error !== 'cancel') showActionError(error, t('messages.stopFailed'))
    } finally {
      stoppingIds.delete(pluginId)
      inFlight.delete(pluginId)
    }
  }

  function stop(pluginId: string): Promise<void> {
    const current = inFlight.get(pluginId)
    if (current) return current
    const next = run(pluginId)
    inFlight.set(pluginId, next)
    return next
  }

  /** True while the stop request runs (not while the confirmation is open). */
  function isStopping(pluginId: string) {
    return stoppingIds.has(pluginId)
  }

  return { stop, isStopping }
}
