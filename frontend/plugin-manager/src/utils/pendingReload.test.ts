// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { hasPendingReload, setPendingReload, subscribePendingReload } from './pendingReload'

afterEach(() => {
  setPendingReload('alpha', false)
  setPendingReload('__proto__', false)
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  localStorage.clear()
})

const STORAGE_KEY = 'neko-plugin-config-pending-reload'

/**
 * The event another renderer window receives. A plain Event carries the two fields
 * the handler reads, which avoids `StorageEvent`'s init dictionary altogether.
 */
function crossWindowChange(record: Record<string, true> | null): Event {
  const event = new Event('storage')
  Object.defineProperties(event, {
    key: { value: STORAGE_KEY },
    newValue: { value: record === null ? null : JSON.stringify(record) },
  })
  return event
}

describe('pending reload storage', () => {
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

  it('stores a reserved plugin id as an own key', () => {
    setPendingReload('__proto__', true)
    expect(hasPendingReload('__proto__')).toBe(true)
    expect(Object.getPrototypeOf({})).toBe(Object.prototype)
    // Survives a round trip through storage, where a lost key would stay invisible.
    expect(JSON.parse(localStorage.getItem('neko-plugin-config-pending-reload')!)).toHaveProperty(
      '__proto__'
    )
    setPendingReload('__proto__', false)
    expect(hasPendingReload('__proto__')).toBe(false)
  })

  it('survives a fresh read and treats corrupt storage as nothing pending', () => {
    setPendingReload('alpha', true)
    expect(hasPendingReload('alpha')).toBe(true)
    localStorage.setItem('neko-plugin-config-pending-reload', 'not json')
    expect(hasPendingReload('alpha')).toBe(false)
    expect(() => setPendingReload('alpha', true)).not.toThrow()
    expect(hasPendingReload('alpha')).toBe(true)
  })

  it('falls back to memory when storage is unavailable', () => {
    setPendingReload('alpha', false)
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('storage denied')
      },
      setItem: () => {
        throw new Error('storage denied')
      },
    })
    expect(hasPendingReload('alpha')).toBe(false)
    expect(() => setPendingReload('alpha', true)).not.toThrow()
    expect(hasPendingReload('alpha')).toBe(true)
    setPendingReload('alpha', false)
    expect(hasPendingReload('alpha')).toBe(false)
  })

  it('keeps the flag when only the write fails', () => {
    setPendingReload('alpha', false)
    const backing = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => backing.get(key) ?? null,
      setItem: () => {
        throw new Error('quota exceeded')
      },
    })
    setPendingReload('alpha', true)
    // Reads must not fall back to the storage record that never got written.
    expect(hasPendingReload('alpha')).toBe(true)
    expect(backing.size).toBe(0)
  })

  it('applies writes in arrival order', () => {
    // A save that lands after a reload still records the flag: the reload may have
    // read the configuration from before that save.
    setPendingReload('alpha', true)
    expect(hasPendingReload('alpha')).toBe(true)
    setPendingReload('alpha', false)
    expect(hasPendingReload('alpha')).toBe(false)
  })

  it('propagates a change made by another renderer window', () => {
    const seen: Array<[string, boolean]> = []
    const release = subscribePendingReload((pluginId, pending) => seen.push([pluginId, pending]))

    // Another window saved a profile; only other windows receive the event.
    window.dispatchEvent(crossWindowChange({ alpha: true }))
    expect(seen).toEqual([['alpha', true]])
    expect(hasPendingReload('alpha')).toBe(true)

    // And another window reloaded the plugin, clearing it.
    window.dispatchEvent(crossWindowChange({}))
    expect(seen.at(-1)).toEqual(['alpha', false])
    expect(hasPendingReload('alpha')).toBe(false)
    release()
  })

  it('ignores storage events for unrelated keys', () => {
    const seen: Array<[string, boolean]> = []
    const release = subscribePendingReload((pluginId, pending) => seen.push([pluginId, pending]))
    const unrelated = new Event('storage')
    Object.defineProperty(unrelated, 'key', { value: 'neko-dark-mode' })
    window.dispatchEvent(unrelated)
    expect(seen).toEqual([])
    release()
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
    setPendingReload('beta', false)
  })
})
