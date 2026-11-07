import { computed, readonly, ref, shallowRef } from 'vue'
import type { PluginDisplayResult } from '../../../../plugin/sdk/hosted-ui'
import type { DisplayOwner } from './pluginDisplaySession'

export type PendingDisplayRequest = {
  owner: DisplayOwner
  mode: 'focus' | 'fullscreen'
  resolve: (result: PluginDisplayResult) => void
  confirm: () => void
  decline: () => void
  fail: (reason: string) => boolean
  cancelled?: boolean
  finished?: Promise<void>
}

// Shared state is lightweight: importing the dialog host does not load the
// session executor or iframe protocol. Only the executor writes these refs.
export const pendingDisplayRequest = shallowRef<PendingDisplayRequest | null>(null)
export const displayApplying = ref(false)
export const displayRevision = ref(0)

export function usePluginDisplayDialog() {
  const visible = computed(() => pendingDisplayRequest.value !== null)
  const confirmation = computed(() => {
    const request = pendingDisplayRequest.value
    return request ? { mode: request.mode, pluginName: request.owner.name() } : null
  })
  const appendTarget = computed(() => {
    void displayRevision.value
    const target = pendingDisplayRequest.value?.owner.target()
    return target && document.fullscreenElement === target ? target : document.body
  })
  return {
    visible,
    confirmation,
    appendTarget,
    applying: readonly(displayApplying),
    confirm: () => pendingDisplayRequest.value?.confirm(),
    decline: () => pendingDisplayRequest.value?.decline(),
    captureLoadFailure: () => pendingDisplayRequest.value?.fail,
  }
}
