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

  it('reads the flag an earlier session left in storage', () => {
    // Mirrored so the hint survives a page reload: a fresh document has no memory of it.
    localStorage.setItem(keyFor('restored'), '1')
    expect(hasPendingReload('restored')).toBe(true)
    expect(hasPendingReload('untouched')).toBe(false)
  })

  it('ignores unrelated storage content', () => {
    // An earlier revision of this feature kept every plugin in one JSON record under the
    // prefix without a trailing colon. That format only ever existed on an unmerged branch,
    // so it is deliberately not migrated: migrating it could revive a cleared flag.
    localStorage.setItem('neko-plugin-config-pending-reload', '{"alpha":true,"__proto__":true}')
    localStorage.setItem('neko-dark-mode', 'true')
    expect(hasPendingReload('untouched')).toBe(false)
  })

  it('answers from memory when storage is unavailable', () => {
    vi.stubGlobal('localStorage', undefined)
    expect(hasPendingReload('alpha')).toBe(false)
    expect(() => setPendingReload('alpha', true)).not.toThrow()
    expect(hasPendingReload('alpha')).toBe(true)
    setPendingReload('alpha', false)
    expect(hasPendingReload('alpha')).toBe(false)
  })

  it('answers from memory when storage refuses the write', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('quota exceeded')
      },
      setItem: () => {
        throw new Error('quota exceeded')
      },
      removeItem: () => {
        throw new Error('quota exceeded')
      },
    })
    setPendingReload('alpha', true)
    expect(hasPendingReload('alpha')).toBe(true)
    // The removal it refused cannot bring the flag back either.
    setPendingReload('alpha', false)
    expect(hasPendingReload('alpha')).toBe(false)
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
