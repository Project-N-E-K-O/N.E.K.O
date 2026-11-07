// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  cancelDisplayOwner,
  confirmDisplay,
  declineDisplay,
  exitDisplay,
  getDisplayState,
  requestDisplay,
  type DisplayOwner,
} from './pluginDisplaySession'
import { usePluginDisplayDialog } from './usePluginDisplayDialog'

describe('plugin display sessions', () => {
  const owners: DisplayOwner[] = []
  let fullscreen: Element | null
  function owner(initialFocus = false) {
    const target = document.createElement('div')
    document.body.appendChild(target)
    let focused = initialFocus
    const value: DisplayOwner = {
      target: () => target,
      active: () => true,
      name: () => 'Example',
      focused: () => focused,
      setFocused: (next) => {
        focused = next
      },
      notify: vi.fn(),
    }
    target.requestFullscreen = vi.fn(async () => {
      fullscreen = target
      document.dispatchEvent(new Event('fullscreenchange'))
    })
    owners.push(value)
    return value
  }
  beforeEach(() => {
    fullscreen = null
    Object.defineProperty(document, 'fullscreenElement', {
      configurable: true,
      get: () => fullscreen,
    })
    Object.defineProperty(document, 'fullscreenEnabled', { configurable: true, value: true })
    document.exitFullscreen = vi.fn(async () => {
      fullscreen = null
      document.dispatchEvent(new Event('fullscreenchange'))
    })
  })
  afterEach(async () => {
    owners.splice(0).forEach((owner) => cancelDisplayOwner(owner))
    await Promise.resolve()
    document.body.innerHTML = ''
  })
  it('waits for host consent and restores a focus session', async () => {
    const value = owner()
    const promise = requestDisplay(value, 'focus')
    expect(value.focused()).toBe(false)
    expect(usePluginDisplayDialog().confirmation.value).toEqual({
      mode: 'focus',
      pluginName: 'Example',
    })
    confirmDisplay()
    expect((await promise).status).toBe('applied')
    expect(value.focused()).toBe(true)
    await exitDisplay(value)
    expect(value.focused()).toBe(false)
  })
  it('declines without applying, and never queues a second prompt', async () => {
    const value = owner()
    const promise = requestDisplay(value, 'focus')
    expect((await requestDisplay(owner(), 'fullscreen')).status).toBe('busy')
    declineDisplay()
    expect((await promise).status).toBe('declined')
    expect(value.focused()).toBe(false)
  })
  it('releases a failed dialog load and ignores an old failure after a new request starts', async () => {
    const value = owner()
    const request = requestDisplay(value, 'focus')
    const fail = usePluginDisplayDialog().captureLoadFailure()!
    expect(fail('chunk load failed')).toBe(true)
    expect(await request).toMatchObject({ status: 'failed', reason: 'chunk load failed' })
    const retry = requestDisplay(value, 'focus')
    expect(fail('late failure')).toBe(false)
    expect(usePluginDisplayDialog().visible.value).toBe(true)
    confirmDisplay()
    expect((await retry).status).toBe('applied')
  })
  it('starts fullscreen synchronously on confirmation and preserves prior manual focus', async () => {
    const value = owner(true)
    const promise = requestDisplay(value, 'fullscreen')
    confirmDisplay()
    expect(value.target()!.requestFullscreen).toHaveBeenCalledOnce()
    expect((await promise).state.mode).toBe('fullscreen')
    fullscreen = null
    document.dispatchEvent(new Event('fullscreenchange'))
    expect(getDisplayState(value).mode).toBe('focus')
  })
  it('returns native failure without leaving focus or a session behind', async () => {
    const value = owner()
    value.target()!.requestFullscreen = vi.fn(() => Promise.reject(new Error('Denied')))
    const promise = requestDisplay(value, 'fullscreen')
    confirmDisplay()
    expect(await promise).toMatchObject({
      status: 'failed',
      reason: 'Denied',
      state: { mode: 'normal' },
    })
    expect(value.focused()).toBe(false)
  })
  it('keeps an existing focus session when its fullscreen upgrade fails', async () => {
    const value = owner()
    const focus = requestDisplay(value, 'focus')
    confirmDisplay()
    await focus
    value.target()!.requestFullscreen = vi.fn(() => Promise.reject(new Error('Denied')))
    const upgrade = requestDisplay(value, 'fullscreen')
    confirmDisplay()
    expect(await upgrade).toMatchObject({ status: 'failed', state: { mode: 'focus' } })
    await exitDisplay(value)
    expect(value.focused()).toBe(false)
  })
  it('keeps the fullscreen session and exit control available when native exit fails', async () => {
    const value = owner()
    const full = requestDisplay(value, 'fullscreen')
    confirmDisplay()
    await full
    document.exitFullscreen = vi.fn(() => Promise.reject(new Error('Exit denied')))
    expect(await exitDisplay(value)).toMatchObject({
      status: 'failed',
      state: { mode: 'fullscreen' },
    })
    expect(value.focused()).toBe(true)
    // Teardown can now exit normally; do not leak the rejected stub to cleanup.
    document.exitFullscreen = vi.fn(async () => {
      fullscreen = null
      document.dispatchEvent(new Event('fullscreenchange'))
    })
    await exitDisplay(value)
    expect(getDisplayState(value).mode).toBe('normal')
  })
  it('upgrades focus to fullscreen and exits to the original layout', async () => {
    const value = owner()
    let promise = requestDisplay(value, 'focus')
    confirmDisplay()
    await promise
    await Promise.resolve()
    promise = requestDisplay(value, 'fullscreen')
    confirmDisplay()
    expect((await promise).state.mode).toBe('fullscreen')
    await exitDisplay(value)
    expect(getDisplayState(value).mode).toBe('normal')
  })
  it('downgrades fullscreen to focus, then restores the baseline on exit', async () => {
    const value = owner()
    let promise = requestDisplay(value, 'fullscreen')
    confirmDisplay()
    await promise
    await Promise.resolve()
    promise = requestDisplay(value, 'focus')
    confirmDisplay()
    expect((await promise).state.mode).toBe('focus')
    await exitDisplay(value)
    expect(value.focused()).toBe(false)
  })
  it('keeps an explicit host focus exit instead of restoring the manual focus baseline', async () => {
    const value = owner(true)
    const full = requestDisplay(value, 'fullscreen')
    confirmDisplay()
    await full
    const focus = requestDisplay(value, 'focus')
    confirmDisplay()
    await focus

    value.setFocused(false)
    cancelDisplayOwner(value, { restoreFocus: false })

    expect(value.focused()).toBe(false)
    expect(getDisplayState(value).mode).toBe('normal')
  })
  it('cancels an open prompt when its frame disappears', async () => {
    const value = owner()
    const promise = requestDisplay(value, 'fullscreen')
    cancelDisplayOwner(value)
    expect((await promise).status).toBe('cancelled')
    expect(usePluginDisplayDialog().visible.value).toBe(false)
  })
  it('cleans up a late fullscreen completion after cancellation', async () => {
    const value = owner()
    let finish!: () => void
    value.target()!.requestFullscreen = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve
        })
    )
    const promise = requestDisplay(value, 'fullscreen')
    confirmDisplay()
    cancelDisplayOwner(value)
    expect((await promise).status).toBe('cancelled')
    fullscreen = value.target()
    finish()
    await Promise.resolve()
    await Promise.resolve()
    expect(fullscreen).toBeNull()
    expect(value.focused()).toBe(false)
  })
  it('does not resurrect a session if the user exits before native entry resolves', async () => {
    const value = owner()
    let finish!: () => void
    value.target()!.requestFullscreen = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve
        })
    )
    const request = requestDisplay(value, 'fullscreen')
    confirmDisplay()
    fullscreen = value.target()
    document.dispatchEvent(new Event('fullscreenchange'))
    fullscreen = null
    document.dispatchEvent(new Event('fullscreenchange'))
    finish()
    expect((await request).status).toBe('cancelled')
    await Promise.resolve()
    expect(getDisplayState(value).mode).toBe('normal')
    expect(value.focused()).toBe(false)
  })
  it('rejects inactive panels, invalid modes and unrelated fullscreen ownership', async () => {
    const value = owner()
    expect((await requestDisplay(value, 'windowed')).status).toBe('unsupported')
    value.active = () => false
    expect((await requestDisplay(value, 'focus')).status).toBe('unsupported')
    value.active = () => true
    fullscreen = document.body
    expect((await requestDisplay(value, 'fullscreen')).status).toBe('busy')
    await exitDisplay(value)
    expect(fullscreen).toBe(document.body)
  })
  it('keeps new requests busy until a native fullscreen exit has finished', async () => {
    const value = owner()
    const entered = requestDisplay(value, 'fullscreen')
    confirmDisplay()
    await entered
    let finish!: () => void
    document.exitFullscreen = vi.fn(() => {
      fullscreen = null
      document.dispatchEvent(new Event('fullscreenchange'))
      return new Promise<void>((resolve) => {
        finish = resolve
      })
    })
    const exiting = exitDisplay(value)
    const next = requestDisplay(value, 'focus')
    if (usePluginDisplayDialog().visible.value) declineDisplay()
    finish()
    await exiting
    expect((await next).status).toBe('busy')
  })
  it('notifies the surviving document after a cancelled session finishes native exit', async () => {
    const value = owner()
    const entered = requestDisplay(value, 'fullscreen')
    confirmDisplay()
    await entered
    let finish!: () => void
    document.exitFullscreen = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finish = () => {
            fullscreen = null
            document.dispatchEvent(new Event('fullscreenchange'))
            resolve()
          }
        })
    )
    cancelDisplayOwner(value)
    finish()
    await vi.waitFor(() =>
      expect(value.notify).toHaveBeenLastCalledWith(expect.objectContaining({ mode: 'normal' }))
    )
  })
  it('does not release a cancelled native transition before cleaning up its captured target', async () => {
    const value = owner()
    const originalTarget = value.target()!
    let finish!: () => void
    originalTarget.requestFullscreen = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve
        })
    )
    const entered = requestDisplay(value, 'fullscreen')
    confirmDisplay()
    cancelDisplayOwner(value)
    expect((await entered).status).toBe('cancelled')
    const next = requestDisplay(owner(), 'focus')
    if (usePluginDisplayDialog().visible.value) declineDisplay()
    const replacement = document.createElement('div')
    value.target = () => replacement
    fullscreen = originalTarget
    finish()
    await Promise.resolve()
    await Promise.resolve()
    expect(fullscreen).toBeNull()
    expect((await next).status).toBe('busy')
  })
  it('rechecks fullscreen ownership at the actual confirmation click', async () => {
    const value = owner()
    const request = requestDisplay(value, 'fullscreen')
    fullscreen = document.body
    confirmDisplay()
    expect((await request).status).toBe('busy')
    expect(value.target()!.requestFullscreen).not.toHaveBeenCalled()
    expect(fullscreen).toBe(document.body)
  })
})
