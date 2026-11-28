import { ElMessage } from 'element-plus'
import { i18n } from '@/i18n'
import {
  PluginConsentInvalidated,
  usePluginConsentState,
  type ConsentRequestPlugin,
} from '@/stores/pluginConsentState'
import { usePluginStore } from '@/stores/plugin'
import { usePluginDependencyInstall } from '@/stores/pluginDependencyInstall'
import { getPythonDependencies } from '@/api/plugins'

/**
 * Manual-start consent gate. Programmatic/internal starts and reloads call
 * the plugin store directly; every user-initiated start goes through here.
 */
export function usePluginStartGate() {
  const pluginStore = usePluginStore()
  const consentState = usePluginConsentState()
  /**
   * A plugin that ships uv.lock instead of vendor/ installs its Python
   * dependencies first, after the user confirms the list. False when the
   * dialog was closed before they were installed. A failed status read never
   * blocks: the start itself then reports what is wrong.
   */
  async function ensureDependencies(pluginId: string) {
    let pending = false
    try {
      // Silent: this probe runs before every manual start; the start reports real problems.
      pending = (await getPythonDependencies(pluginId, { suppressErrorMessage: true })).pending === true
    } catch {
      return true
    }
    if (!pending) return true
    const plugin = pluginStore.getPluginById(pluginId)
    // Resolved lazily: callers that never start a pending plugin need no store for it.
    return usePluginDependencyInstall().request(pluginId, plugin?.name || pluginId)
  }

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
    if (!(await ensureDependencies(pluginId))) return false
    await pluginStore.start(pluginId)
    return true
  }

  return { startWithConsent, prepareBatchStart }
}
