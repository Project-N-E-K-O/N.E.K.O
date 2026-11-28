import { inject, ref } from 'vue'
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

/** Stopping a plugin from the detail page: confirm, stop, refresh hosted panels. */
export function usePluginStopAction() {
  const { t } = useI18n()
  const store = usePluginStore()
  const refreshHostedPanels = inject<(() => Promise<void>) | null>(
    PLUGIN_DETAIL_REFRESH_HOSTED_PANELS_KEY,
    null,
  )
  const stopping = ref(false)

  async function stop(pluginId: string) {
    try {
      await ElMessageBox.confirm(t('messages.confirmStop'), t('common.confirm'), { type: 'warning' })
      stopping.value = true
      await store.stop(pluginId)
      void refreshHostedPanels?.().catch(() => {})
      ElMessage.success(t('messages.pluginStopped'))
    } catch (error) {
      if (error !== 'cancel') showActionError(error, t('messages.stopFailed'))
    } finally {
      stopping.value = false
    }
  }

  return { stopping, stop }
}
