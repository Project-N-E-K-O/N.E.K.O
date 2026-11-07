import {
  pendingDisplayRequest as pending,
  displayApplying as applying,
  displayRevision as revision,
  type PendingDisplayRequest as Pending,
} from './usePluginDisplayDialog'
import type { PluginDisplayResult, PluginDisplayState } from '../../../../plugin/sdk/hosted-ui'

export type DisplayOwner = {
  target: () => HTMLElement | null
  active: () => boolean
  name: () => string
  focused: () => boolean
  setFocused: (value: boolean) => void
  notify: (state: PluginDisplayState) => void
}
type Session = {
  owner: DisplayOwner
  target: HTMLElement
  baselineFocus: boolean
  fullscreen: boolean
}
// One window owns one display session. A pending confirmation also remains
// pending during native entry so the dialog can show progress. Cancelling its
// caller resolves the request immediately, but transition/exiting keep native
// ownership locked until the captured target has finished its cleanup.
let session: Session | null = null
let transition: Pending | null = null
let exiting: { promise: Promise<void> } | null = null

export function getDisplayState(owner: DisplayOwner): PluginDisplayState {
  const target = owner.target()
  const supportedModes: PluginDisplayState['supportedModes'] = owner.active() ? ['focus'] : []
  if (supportedModes.length && target?.requestFullscreen && document.fullscreenEnabled)
    supportedModes.push('fullscreen')
  return {
    mode:
      target && document.fullscreenElement === target
        ? 'fullscreen'
        : owner.focused()
          ? 'focus'
          : 'normal',
    supportedModes,
  }
}
export function notifyDisplayOwner(owner: DisplayOwner) {
  revision.value++
  owner.notify(getDisplayState(owner))
}
export function displayResult(
  owner: DisplayOwner,
  status: PluginDisplayResult['status'],
  reason?: string
): PluginDisplayResult {
  return { status, state: getDisplayState(owner), ...(reason ? { reason } : {}) }
}
function restoreSession(previous = session, restoreFocus = true) {
  if (!previous || session !== previous) return
  session = null
  document.removeEventListener('fullscreenchange', fullscreenChanged)
  if (restoreFocus) previous.owner.setFocused(previous.baselineFocus)
  notifyDisplayOwner(previous.owner)
}
function fullscreenChanged() {
  if (!session) return
  if (document.fullscreenElement === session.target) {
    session.fullscreen = true
    notifyDisplayOwner(session.owner)
  } else if (session.fullscreen) {
    if (transition?.owner === session.owner && transition.mode === 'focus') {
      session.fullscreen = false
      return
    }
    if (transition?.owner === session.owner) {
      // A user/browser exit can arrive before requestFullscreen's native
      // promise settles. Treat that as cancellation, not a successful entry.
      cancelDisplayOwner(session.owner)
      return
    }
    restoreSession()
  }
}
function exitFullscreenTarget(target: HTMLElement | null): Promise<void> {
  if (exiting) return exiting.promise
  if (!target || document.fullscreenElement !== target) return Promise.resolve()
  let resolve!: () => void
  let reject!: (error: unknown) => void
  const completion = new Promise<void>((yes, no) => {
    resolve = yes
    reject = no
  })
  const operation = {
    promise: completion.finally(() => {
      if (exiting === operation) exiting = null
    }),
  }
  // Keep ownership locked even if fullscreenchange fires before the native
  // exit promise resolves. An old completion must not restore a new session.
  exiting = operation
  try {
    Promise.resolve(document.exitFullscreen()).then(resolve, reject)
  } catch (error) {
    reject(error)
  }
  return operation.promise
}
export function cancelDisplayOwner(owner: DisplayOwner, { restoreFocus = true } = {}) {
  if (pending.value?.owner === owner) {
    const request = pending.value
    pending.value = null
    request.resolve(displayResult(owner, 'cancelled'))
  }
  if (transition?.owner === owner) {
    const request = transition
    request.cancelled = true
    applying.value = false
    request.resolve(displayResult(owner, 'cancelled'))
  }
  if (session?.owner === owner) {
    void exitFullscreenTarget(session.target).then(
      () => notifyDisplayOwner(owner),
      () => notifyDisplayOwner(owner)
    )
    restoreSession(session, restoreFocus)
  }
}
export async function exitDisplay(owner: DisplayOwner): Promise<PluginDisplayResult> {
  if (session && session.owner !== owner) return displayResult(owner, 'busy')
  const previousSession = session
  const previousTransition = transition?.owner === owner ? transition : null
  if (pending.value?.owner === owner || transition?.owner === owner) cancelDisplayOwner(owner)
  try {
    await exitFullscreenTarget(previousSession?.target ?? owner.target())
    await previousTransition?.finished
    restoreSession(previousSession)
    return displayResult(owner, 'applied')
  } catch (error) {
    return displayResult(owner, 'failed', String(error))
  }
}
export function requestDisplay(owner: DisplayOwner, mode: unknown): Promise<PluginDisplayResult> {
  if (mode !== 'focus' && mode !== 'fullscreen')
    return Promise.resolve(displayResult(owner, 'unsupported', 'invalid_mode'))
  if (!getDisplayState(owner).supportedModes.includes(mode))
    return Promise.resolve(displayResult(owner, 'unsupported', 'inactive_or_unsupported'))
  if (pending.value || transition || exiting || (session && session.owner !== owner))
    return Promise.resolve(displayResult(owner, 'busy'))
  if (getDisplayState(owner).mode === mode) return Promise.resolve(displayResult(owner, 'applied'))
  if (document.fullscreenElement && document.fullscreenElement !== owner.target())
    return Promise.resolve(displayResult(owner, 'busy', 'another_fullscreen_owner'))
  return new Promise((resolve) => {
    pending.value = { owner, mode, resolve, confirm: confirmDisplay, decline: declineDisplay }
  })
}
export function declineDisplay() {
  const request = pending.value
  if (!request || applying.value) return
  pending.value = null
  request.resolve(displayResult(request.owner, 'declined'))
}
// Called directly by the host confirmation button. Do not insert an await,
// nextTick or dialog close animation before requestFullscreen: it consumes the
// browser's real transient activation, independent of iframe message flags.
export function confirmDisplay() {
  const request = pending.value
  if (!request || applying.value) return
  const { owner, mode } = request
  const target = owner.target()
  if (!owner.active() || !target?.isConnected) {
    cancelDisplayOwner(owner)
    return
  }
  if (document.fullscreenElement && document.fullscreenElement !== target) {
    pending.value = null
    request.resolve(displayResult(owner, 'busy', 'another_fullscreen_owner'))
    return
  }
  const previousSession = session
  if (!session) {
    session = { owner, target, baselineFocus: owner.focused(), fullscreen: false }
    document.addEventListener('fullscreenchange', fullscreenChanged)
  }
  const currentSession = session
  transition = request
  applying.value = true
  const settle = (status: PluginDisplayResult['status'], reason?: string) => {
    if (transition !== request) return
    transition = null
    pending.value = null
    applying.value = false
    request.resolve(displayResult(owner, status, reason))
  }
  let operation: Promise<void>
  try {
    operation = mode === 'fullscreen' ? target.requestFullscreen() : exitFullscreenTarget(target)
  } catch (error) {
    operation = Promise.reject(error)
  }
  request.finished = Promise.resolve(operation)
    .then(async () => {
      if (request.cancelled || !owner.active()) {
        await exitFullscreenTarget(target)
        restoreSession(currentSession)
        notifyDisplayOwner(owner)
        return
      }
      // A deliberate fullscreen -> focus transition retains this display session.
      session = currentSession
      document.addEventListener('fullscreenchange', fullscreenChanged)
      session.fullscreen = mode === 'fullscreen'
      owner.setFocused(true)
      notifyDisplayOwner(owner)
      settle('applied')
    })
    .catch((error) => {
      if (request.cancelled) return
      if (!previousSession) restoreSession(currentSession)
      settle('failed', error instanceof Error ? error.message : String(error))
    })
    .finally(() => {
      if (transition === request) {
        transition = null
        applying.value = false
      }
    })
}

export function ownsDisplaySession(owner: DisplayOwner) {
  void revision.value
  return session?.owner === owner
}
