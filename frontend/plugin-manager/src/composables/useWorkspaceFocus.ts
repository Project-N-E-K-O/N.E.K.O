import { computed, onBeforeUnmount, ref, watch, type Ref } from 'vue'

/**
 * Focus mode hands the whole workspace to one view (a plugin panel, typically a
 * web mini-game): the shell drops its sidebar and padding while the view hides
 * its own chrome. Layout changes are CSS-only so iframes are never re-parented,
 * which would reload them.
 *
 * Ownership is tracked by token because page transitions mount the next view
 * before the previous one unmounts; a leaving view must not release focus the
 * entering view has just claimed.
 */
const owner = ref<symbol | null>(null)

const focused = computed(() => owner.value !== null)

/** Read side, for the app shell. */
export function useWorkspaceFocus() {
  return { focused }
}

/** Write side, for the view that fills the workspace while `active` is true. */
export function useWorkspaceFocusOwner(active: Ref<boolean>) {
  const token = Symbol('workspace-focus')
  function release() {
    if (owner.value === token) owner.value = null
  }
  watch(active, (value) => {
    if (value) owner.value = token
    else release()
  }, { immediate: true })
  onBeforeUnmount(release)
}
