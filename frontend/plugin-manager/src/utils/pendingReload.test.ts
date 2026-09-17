// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { hasPendingReload, setPendingReload, subscribePendingReload } from './pendingReload'

const keyFor = (pluginId: string) => `neko-plugin-config-pending-reload:${pluginId}`

/**
 * The event another renderer window receives. The write that triggered it is applied
 * here too, because in a browser the shared storage already holds the new value.
 */
function crossWindowChange(pluginId: string | null, pending: boolean): Event {
  if (pluginId === null) {
    localStorage.clear()
  } else if (pending) {
    localStorage.setItem(keyFor(pluginId), '1')
  } else {
    localStorage.removeItem(keyFor(pluginId))
  }
  const event = new Event('storage')
  Object.defineProperties(event, {
    key: { value: pluginId === null ? null : keyFor(pluginId) },
    newValue: { value: pluginId === null || !pending ? null : '1' },
  })
  return event
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  // Clears both the storage keys and the module-level mirrors.
  for (const pluginId of ['alpha', 'beta', '__proto__']) setPendingReload(pluginId, false)
  localStorage.clear()
})

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

  it('keeps a reserved plugin id usable', () => {
    setPendingReload('__proto__', true)
    expect(hasPendingReload('__proto__')).toBe(true)
    expect(Object.getPrototypeOf({})).toBe(Object.prototype)
    setPendingReload('__proto__', false)
    expect(hasPendingReload('__proto__')).toBe(false)
  })

  it('keeps concurrent plugins in separate keys', () => {
    setPendingReload('alpha', true)
    setPendingReload('beta', true)
    // Neither write replaces the other, which a shared record would have done.
    expect(hasPendingReload('alpha')).toBe(true)
    expect(hasPendingReload('beta')).toBe(true)
    setPendingReload('beta', false)
    expect(hasPendingReload('alpha')).toBe(true)
    expect(hasPendingReload('beta')).toBe(false)
  })

  it('survives a fresh read and ignores unrelated storage content', () => {
    setPendingReload('alpha', true)
    expect(hasPendingReload('alpha')).toBe(true)
    localStorage.setItem('neko-plugin-config-pending-reload', 'legacy aggregate record')
    expect(hasPendingReload('alpha')).toBe(true)
    localStorage.setItem('neko-dark-mode', 'true')
    expect(hasPendingReload('alpha')).toBe(true)
  })

  it('falls back to memory when storage is unavailable', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('storage denied')
      },
      setItem: () => {
        throw new Error('storage denied')
      },
      removeItem: () => {
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
    const backing = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => backing.get(key) ?? null,
      setItem: () => {
        throw new Error('quota exceeded')
      },
      removeItem: (key: string) => backing.delete(key),
    })
    setPendingReload('alpha', true)
    // Reads must not fall back to the storage record that never got written.
    expect(hasPendingReload('alpha')).toBe(true)
    expect(backing.size).toBe(0)
  })

  it('propagates a change made by another renderer window', () => {
    const seen: Array<[string, boolean, string]> = []
    const release = subscribePendingReload((pluginId, pending, source) =>
      seen.push([pluginId, pending, source])
    )

    // Another window saved a profile; only other windows receive the event.
    window.dispatchEvent(crossWindowChange('alpha', true))
    expect(seen).toEqual([['alpha', true, 'external']])
    expect(hasPendingReload('alpha')).toBe(true)

    // And another window reloaded the plugin, clearing it.
    window.dispatchEvent(crossWindowChange('alpha', false))
    expect(seen.at(-1)).toEqual(['alpha', false, 'external'])
    expect(hasPendingReload('alpha')).toBe(false)
    release()
  })

  it('reports every known plugin when another window clears storage', () => {
    setPendingReload('alpha', true)
    const seen: Array<[string, boolean, string]> = []
    const release = subscribePendingReload((pluginId, pending, source) =>
      seen.push([pluginId, pending, source])
    )
    window.dispatchEvent(crossWindowChange(null, false))
    expect(seen).toEqual([['alpha', false, 'external']])
    expect(hasPendingReload('alpha')).toBe(false)
    release()
  })

  it('ignores storage events for unrelated keys', () => {
    const seen: Array<[string, boolean, string]> = []
    const release = subscribePendingReload((pluginId, pending, source) =>
      seen.push([pluginId, pending, source])
    )
    const unrelated = new Event('storage')
    Object.defineProperty(unrelated, 'key', { value: 'neko-dark-mode' })
    window.dispatchEvent(unrelated)
    expect(seen).toEqual([])
    release()
  })

  it('applies writes in arrival order', () => {
    // A save that lands after a reload still records the flag: the reload may have
    // read the configuration from before that save.
    setPendingReload('alpha', true)
    expect(hasPendingReload('alpha')).toBe(true)
    setPendingReload('alpha', false)
    expect(hasPendingReload('alpha')).toBe(false)
  })

  it('notifies subscribers about every applied change', () => {
    const seen: Array<[string, boolean, string]> = []
    const release = subscribePendingReload((pluginId, pending, source) =>
      seen.push([pluginId, pending, source])
    )
    setPendingReload('alpha', true)
    setPendingReload('beta', true)
    setPendingReload('alpha', false)
    release()
    setPendingReload('alpha', true)
    expect(seen).toEqual([
      ['alpha', true, 'local'],
      ['beta', true, 'local'],
      ['alpha', false, 'local'],
    ])
  })
})
