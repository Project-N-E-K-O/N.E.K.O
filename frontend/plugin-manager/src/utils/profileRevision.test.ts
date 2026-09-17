// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { bumpProfileRevision, subscribeProfileRevision } from './profileRevision'

const KEY_PREFIX = 'neko-plugin-config-profile-revision:'
const keyFor = (pluginId: string) => KEY_PREFIX + pluginId
const storedKeys = () =>
  Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index) ?? '')

/** The event another window receives for one of its storage keys. */
function crossWindowEvent(key: string): Event {
  const event = new Event('storage')
  Object.defineProperty(event, 'key', { value: key })
  return event
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('profile revision broadcast', () => {
  it('stores a token that differs on every write, even within one millisecond', () => {
    // Browsers only fire `storage` when the stored value actually changes, so a
    // token that repeats would silently drop the second write of that millisecond
    // and leave the other windows' drafts stale.
    vi.spyOn(Date, 'now').mockReturnValue(1_700_000_000_000_000)
    bumpProfileRevision('alpha')
    const first = localStorage.getItem(keyFor('alpha'))
    bumpProfileRevision('alpha')
    const second = localStorage.getItem(keyFor('alpha'))

    expect(first).not.toBeNull()
    expect(second).not.toBeNull()
    expect(second).not.toBe(first)
  })

  it('keeps one key per plugin', () => {
    bumpProfileRevision('alpha')
    bumpProfileRevision('beta')
    expect(storedKeys()).toEqual([keyFor('alpha'), keyFor('beta')])
  })

  it('ignores an empty plugin id', () => {
    bumpProfileRevision('')
    expect(storedKeys()).toEqual([])
  })

  it('reports only its own plugin keys to subscribers', () => {
    const seen: string[] = []
    const release = subscribeProfileRevision((pluginId) => seen.push(pluginId))
    try {
      window.dispatchEvent(crossWindowEvent(keyFor('alpha')))
      // A key without the prefix, and a `storage.clear()` event, are not profile writes.
      window.dispatchEvent(crossWindowEvent('neko-plugin-config-pending-reload:alpha'))
      window.dispatchEvent(new Event('storage'))
      expect(seen).toEqual(['alpha'])
    } finally {
      release()
    }
  })
})
