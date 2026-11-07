import { computed, inject, onBeforeUnmount, provide, watch, type InjectionKey, type Ref } from 'vue'
import type { PluginDisplayState } from '../../../../plugin/sdk/hosted-ui'
import {
  cancelDisplayOwner,
  displayResult,
  exitDisplay,
  getDisplayState,
  notifyDisplayOwner,
  ownsDisplaySession,
  requestDisplay,
  type DisplayOwner,
} from './pluginDisplaySession'

type DisplayPage = { focused: Ref<boolean>; name: () => string }
const displayPageKey: InjectionKey<DisplayPage> = Symbol('plugin-display-page')
export function providePluginDisplayPage(focused: Ref<boolean>, name: () => string) {
  provide(displayPageKey, { focused, name })
}

type DisplayMessage = {
  type?: unknown
  documentId?: unknown
  requestId?: unknown
  method?: unknown
  payload?: { mode?: unknown }
}
export function usePluginDisplayFrame(options: {
  target: Ref<HTMLElement | null>
  active: () => boolean
  identity: () => string
  name: () => string
  notify: (state: PluginDisplayState, documentId: string) => void
}) {
  const page = inject(displayPageKey, null)
  let documentId: string | null = null
  let epoch = 0
  let interactiveRequest: DisplayMessage | null = null
  const owner: DisplayOwner = {
    target: () => options.target.value,
    active: () => !!page && options.active(),
    name: () => page?.name() || options.name(),
    focused: () => page?.focused.value ?? false,
    setFocused: (value) => {
      if (page) page.focused.value = value
    },
    notify: (state) => {
      if (documentId) options.notify(state, documentId)
    },
  }
  const owned = computed(() => ownsDisplaySession(owner))
  function invalidateDocument() {
    epoch++
    documentId = null
    interactiveRequest = null
    cancelDisplayOwner(owner)
  }
  // Host identity changes invalidate before replacement; iframe load/pageshow
  // separately cover navigation within a surviving WindowProxy.
  watch(
    () => [options.identity(), options.active()],
    (current, previous) => {
      if (current[0] !== previous[0]) invalidateDocument()
      else if (current[1] !== previous[1]) cancelDisplayOwner(owner)
    },
    { flush: 'sync' }
  )
  if (page)
    watch(page.focused, () => {
      // A host exit already chose the layout; teardown must not restore the
      // manual-focus baseline captured before this display session.
      if (!page.focused.value && ownsDisplaySession(owner))
        cancelDisplayOwner(owner, { restoreFocus: false })
      notifyDisplayOwner(owner)
    })
  onBeforeUnmount(invalidateDocument)
  async function handle(data: { method?: unknown; payload?: { mode?: unknown } }) {
    if (data.method === 'display.getState') return getDisplayState(owner)
    if (data.method === 'display.requestMode') return requestDisplay(owner, data.payload?.mode)
    if (data.method === 'display.exit') return exitDisplay(owner)
    return displayResult(owner, 'unsupported', 'unknown_method')
  }
  return {
    owned,
    invalidateDocument,
    // Call only after the frame's existing source + origin checks. A document
    // announces itself after pageshow (after the parent's iframe load reset).
    // WindowProxy is reused by same-frame reloads, so source alone is not a
    // document identity. Replies and disposal also carry the client document id.
    handleMessage(data: DisplayMessage, reply: (message: object) => void): boolean {
      if (
        data.type === 'neko-plugin-display-ready' &&
        typeof data.documentId === 'string' &&
        data.documentId
      ) {
        if (documentId !== data.documentId) {
          invalidateDocument()
          documentId = data.documentId
        }
        return true
      }
      if (data.type === 'neko-plugin-display-dispose') {
        if (data.documentId === documentId) invalidateDocument()
        return true
      }
      if (data.type === 'neko-hosted-surface-cancel' && data.documentId) {
        if (data.documentId === documentId && data.requestId === interactiveRequest?.requestId)
          cancelDisplayOwner(owner)
        return true
      }
      if (
        data.type !== 'neko-hosted-surface-request' ||
        typeof data.method !== 'string' ||
        !data.method.startsWith('display.')
      )
        return false
      if (
        !documentId ||
        data.documentId !== documentId ||
        typeof data.requestId !== 'string' ||
        !data.requestId
      )
        return true
      const requestEpoch = epoch
      const requestDocument = documentId
      const respond = (value: unknown) => {
        if (epoch === requestEpoch && documentId === requestDocument)
          reply({
            type: 'neko-hosted-surface-response',
            requestId: data.requestId,
            documentId: requestDocument,
            ok: true,
            result: value,
          })
      }
      if (data.method === 'display.requestMode' && interactiveRequest) {
        respond(displayResult(owner, 'busy'))
        return true
      }
      if (data.method === 'display.requestMode') interactiveRequest = data
      void handle(data)
        .then(respond)
        .finally(() => {
          if (interactiveRequest === data) interactiveRequest = null
        })
      return true
    },
    exit: () => exitDisplay(owner),
  }
}
