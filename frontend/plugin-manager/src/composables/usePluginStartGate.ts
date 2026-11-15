import { ElMessage } from 'element-plus'
import { i18n } from '@/i18n'
import {
  PluginConsentInvalidated,
  usePluginConsentState,
  type ConsentRequestPlugin,
} from '@/stores/pluginConsentState'
import { usePluginStore } from '@/stores/plugin'

/**
 * Manual-start consent gate. Programmatic/internal starts and reloads call
 * the plugin store directly; every user-initiated start goes through here.
 */
export function usePluginStartGate() {
  const pluginStore = usePluginStore()
  const consentState = usePluginConsentState()

  /**
   * Returns the ids that may start now plus whether the confirmation dialog
   * was accepted. Unfinished analysis warns on the first click and a second
   * click within 30 seconds bypasses; a failed consent read never blocks.
   */
  async function prepareBatchStart(pluginIds: string[]) {
    const loaded = await Promise.allSettled(pluginIds.map((id) => consentState.load(id)))
    const allowed: string[] = []
    const pending: string[] = []
    const confirm: ConsentRequestPlugin[] = []
    pluginIds.forEach((id, index) => {
      const result = loaded[index]
      if (result?.status === 'rejected' && result.reason instanceof PluginConsentInvalidated) return
      const consent = result?.status === 'fulfilled' ? result.value : null
      if (!consent || consent.state === 'clear') {
        allowed.push(id)
        return
      }
      if (consent.state === 'analyzing') {
        if (consentState.allowStartDuringAnalysis(consent)) allowed.push(id)
        else pending.push(id)
        return
      }
      const plugin = pluginStore.getPluginById(id)
      confirm.push({ id, name: plugin?.name || id, version: plugin?.version })
    })
    if (pending.length) {
      const names = pending.map((id) => pluginStore.getPluginById(id)?.name || id).join('、')
      ElMessage.warning(i18n.global.t('pluginConsent.analysisPending', { names }))
    }
    const confirmed = confirm.length > 0 && (await consentState.requestConfirmation(confirm))
    if (confirmed) allowed.push(...confirm.map((plugin) => plugin.id))
    return { allowed, confirmed }
  }

  /** Returns true when the plugin actually started (false on cancel/bypass). */
  async function startWithConsent(pluginId: string) {
    const { allowed } = await prepareBatchStart([pluginId])
    if (!allowed.includes(pluginId)) return false
    await pluginStore.start(pluginId)
    return true
  }

  return { startWithConsent, prepareBatchStart }
}
