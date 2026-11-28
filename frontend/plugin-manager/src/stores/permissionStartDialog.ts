import { defineStore } from 'pinia'
import { ref, shallowRef } from 'vue'
import type { PendingPermissions, PermissionPanel } from '@/api/pluginPermissions'
import type { DialogRequest } from '@/utils/permissionDialogs'

export interface StartDialogRequest {
  panel: PermissionPanel
  pending: PendingPermissions
  request: DialogRequest
  pluginName: string
}

interface Active extends StartDialogRequest {
  key: number
  resolve: (start: boolean) => void
}

/**
 * The permission dialog shown before a manual start (① or ③). One at a
 * time: a new request answers the one still open with "don't start".
 */
export const usePermissionStartDialog = defineStore('permissionStartDialog', () => {
  const active = shallowRef<Active | null>(null)
  /** Moves when a start dialog saved a decision, so open views read again. */
  const revision = ref(0)
  let sequence = 0

  /** Resolves true when the user chose to start. */
  function ask(request: StartDialogRequest): Promise<boolean> {
    answer(false)
    return new Promise<boolean>((resolve) => {
      active.value = { ...request, key: ++sequence, resolve }
    })
  }
  /** Answers the open dialog; an answer for a dialog already replaced is dropped. */
  function answer(start: boolean, key?: number, changed = false) {
    if (changed) revision.value++
    const current = active.value
    if (!current || (key !== undefined && key !== current.key)) return
    active.value = null
    current.resolve(start)
  }
  return { active, revision, ask, answer }
})
