import {
  getPendingPermissions,
  getPermissionPanel,
  type PendingPermissions,
  type PermissionPanel,
} from '@/api/pluginPermissions'
import { PermissionStartDialog } from '@/components/plugin/permissionStartDialog'
import { usePermissionStartDialog } from '@/stores/permissionStartDialog'
import { usePluginStore } from '@/stores/plugin'
import type { DialogRequest } from '@/utils/permissionDialogs'
import { validPanel, validPending } from '@/utils/permissionTab'

/** How long a manual start waits for the permission reads before it starts anyway. */
export const START_REVIEW_TIMEOUT_MS = 3000

/** Plugins whose manual start is in progress: a second click starts nothing. */
const starting = new Set<string>()

export interface StartReview {
  panel: PermissionPanel
  pending: PendingPermissions
  request: DialogRequest
}

function withTimeout<T>(work: Promise<T>, ms: number): Promise<T | null> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise<null>((resolve) => {
    timer = setTimeout(() => resolve(null), ms)
  })
  return Promise.race([work, timeout]).finally(() => clearTimeout(timer))
}

/**
 * What to show before starting: dialog ① while the plugin was never decided,
 * ③ when an update asks for more. Null (start at once) when nothing waits,
 * or when a read fails or is too slow.
 */
export async function readStartReview(
  pluginId: string,
  timeoutMs = START_REVIEW_TIMEOUT_MS,
): Promise<StartReview | null> {
  const read = async (): Promise<StartReview | null> => {
    const pending = await getPendingPermissions()
    if (!validPending(pending)) return null
    const listed = (entries: PendingPermissions['to_confirm']) =>
      (entries ?? []).find((entry) => entry.plugin_id === pluginId && entry.items.length > 0)
    const first = listed(pending.to_confirm)
    const upgrade = first ? undefined : listed(pending.upgraded)
    if (!first && !upgrade) return null
    const [panel] = await Promise.all([getPermissionPanel(pluginId), PermissionStartDialog.preload()])
    if (!validPanel(panel)) return null
    const request: DialogRequest = first
      ? { kind: 'first', items: [...first.items] }
      : { kind: 'upgrade', items: [...upgrade!.items] }
    return { panel, pending, request }
  }
  try {
    return await withTimeout(read(), timeoutMs)
  } catch {
    return null
  }
}

/**
 * A manual start from the plugin's own start button. The dialog never blocks
 * the start: "Start" is always there. Other starts call the store directly.
 */
export function usePluginStartGate() {
  const pluginStore = usePluginStore()
  const dialog = usePermissionStartDialog()

  /** True when the plugin was started; false when the user chose not to, or a start was already on its way. */
  async function startWithReview(pluginId: string): Promise<boolean> {
    if (starting.has(pluginId)) return false
    starting.add(pluginId)
    try {
      const review = await readStartReview(pluginId)
      if (review) {
        const pluginName = pluginStore.getPluginById(pluginId)?.name || pluginId
        if (!(await dialog.ask({ ...review, pluginName }))) return false
      }
      await pluginStore.start(pluginId)
      return true
    } finally {
      starting.delete(pluginId)
    }
  }

  return { startWithReview }
}
