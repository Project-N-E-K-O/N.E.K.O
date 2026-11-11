import { ElMessage } from 'element-plus'
import { i18n } from '@/i18n'
import { usePluginConsentState, type ConsentRequestPlugin } from '@/stores/pluginConsentState'
import { usePluginStore } from '@/stores/plugin'

// A click while capability analysis is still running is remembered for
// 30 seconds: a second click on the same plugin starts it directly.
const analysisBypass = new Map<string, number>()
const ANALYSIS_BYPASS_MS = 30_000

/**
 * Manual-start consent gate. Programmatic/internal starts and reloads call
 * the plugin store directly; every user-initiated start goes through here.
 */
export function usePluginStartGate() {
  const pluginStore = usePluginStore()
  const consentState = usePluginConsentState()

  /**
   * Returns the ids that may start now plus whether the confirmation dialog
   * was shown. Unfinished analysis warns on the first click and a second
   * click within 30 seconds bypasses; a failed consent read never blocks.
   */
  async function prepareBatchStart(pluginIds: string[]) {
    const loaded = await Promise.all(
      pluginIds.map((id) => consentState.load(id).catch(() => null)),
    )
    const allowed: string[] = []
    const pending: string[] = []
    const confirm: ConsentRequestPlugin[] = []
    pluginIds.forEach((id, index) => {
      const plugin = pluginStore.getPluginById(id)
      // A failed consent read must never block a start: this is a
      // notification, not a security boundary.
      const state = loaded[index]?.state ?? 'clear'
      if (state === 'clear') {
        allowed.push(id)
        return
      }
      if (state === 'analyzing') {
        const bypass = analysisBypass.get(id)
        if (bypass !== undefined && Date.now() - bypass < ANALYSIS_BYPASS_MS) {
          analysisBypass.delete(id)
          allowed.push(id)
        } else {
          analysisBypass.set(id, Date.now())
          pending.push(id)
        }
        return
      }
      confirm.push({ id, name: plugin?.name || id, version: plugin?.version })
    })
    if (pending.length) {
      const names = pending.map((id) => pluginStore.getPluginById(id)?.name || id).join('、')
      ElMessage.warning(i18n.global.t('pluginConsent.analysisPending', { names }))
    }
    let dialogShown = false
    if (confirm.length) {
      dialogShown = true
      if (await consentState.requestConfirmation(confirm)) {
        allowed.push(...confirm.map((plugin) => plugin.id))
      }
    }
    return { allowed, dialogShown }
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
