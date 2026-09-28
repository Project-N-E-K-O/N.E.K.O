// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  hasPendingReload,
  pendingReloadRevision,
  setPendingReload,
  subscribePendingReload,
} from './pendingReload'

const keyFor = (pluginId: string) => `neko-plugin-config-pending-reload:${pluginId}`

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  // The flags live in the module, so clear the ones these tests touch.
  for (const pluginId of ['alpha', 'beta', '__proto__']) setPendingReload(pluginId, false)
  localStorage.clear()
})

describe('pending reload bookkeeping', () => {
  it('records and clears a plugin independently', () => {
    expect(hasPendingReload('alpha')).toBe(false)
    setPendingReload('alpha', true)
    expect(hasPendingReload('alpha')).toBe(true)
    expect(hasPendingReload('beta')).toBe(false)
    setPendingReload('alpha', false)
    expect(hasPendingReload('alpha')).toBe(false)
  })

  it('ignores an empty plugin id', () => {
    setPendingReload('', true)
    expect(hasPendingReload('')).toBe(false)
  })

  it('keeps a reserved plugin id usable', () => {
    setPendingReload('__proto__', true)
    expect(hasPendingReload('__proto__')).toBe(true)
    expect(Object.getPrototypeOf({})).toBe(Object.prototype)
    setPendingReload('__proto__', false)
    expect(hasPendingReload('__proto__')).toBe(false)
  })

  it('keeps concurrent plugins in separate flags', () => {
    setPendingReload('alpha', true)
    setPendingReload('beta', true)
    setPendingReload('beta', false)
    expect(hasPendingReload('alpha')).toBe(true)
    expect(hasPendingReload('beta')).toBe(false)
  })

  it('keeps the flag in memory only', () => {
    // A persisted flag outlived the events that make it wrong (a backend restart relaunches
    // auto-start plugins with the saved configuration), so storage is neither read nor
    // written. A key an older build left behind must not resurrect a hint either.
    localStorage.setItem(keyFor('restored'), '1')
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    const getItem = vi.spyOn(Storage.prototype, 'getItem')
    expect(hasPendingReload('restored')).toBe(false)
    setPendingReload('alpha', true)
    expect(hasPendingReload('alpha')).toBe(true)
    setPendingReload('alpha', false)
    expect(setItem).not.toHaveBeenCalled()
    expect(getItem).not.toHaveBeenCalled()
  })

  it('applies writes in arrival order', () => {
    // A save that lands after a reload still records the flag: the reload may have read the
    // configuration from before that save.
    setPendingReload('alpha', true)
    setPendingReload('alpha', false)
    setPendingReload('alpha', true)
    expect(hasPendingReload('alpha')).toBe(true)
  })

  it('notifies subscribers about every applied change', () => {
    const seen: Array<[string, boolean]> = []
    const release = subscribePendingReload((pluginId, pending) => seen.push([pluginId, pending]))
    setPendingReload('alpha', true)
    setPendingReload('beta', true)
    setPendingReload('alpha', false)
    release()
    setPendingReload('alpha', true)

    expect(seen).toEqual([
      ['alpha', true],
      ['beta', true],
      ['alpha', false],
    ])
  })

  it('refuses to clear a flag that a later write claimed', () => {
    setPendingReload('alpha', true)
    const captured = pendingReloadRevision('alpha')

    // A profile write lands while a start or reload is in flight: it describes a
    // configuration that host cannot have read, so the flag has to stay.
    setPendingReload('alpha', true)
    expect(setPendingReload('alpha', false, captured)).toBe(false)
    expect(hasPendingReload('alpha')).toBe(true)
  })

  it('clears a flag that no later write touched', () => {
    setPendingReload('alpha', true)
    const captured = pendingReloadRevision('alpha')
    expect(setPendingReload('alpha', false, captured)).toBe(true)
    expect(hasPendingReload('alpha')).toBe(false)
  })
})
