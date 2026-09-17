// @vitest-environment happy-dom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { hasPendingReload, setPendingReload } from './pendingReload'

afterEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
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

  it('survives a fresh read and recovers from corrupt storage', () => {
    setPendingReload('alpha', true)
    expect(hasPendingReload('alpha')).toBe(true)
    localStorage.setItem('neko-plugin-config-pending-reload', 'not json')
    expect(hasPendingReload('alpha')).toBe(false)
    expect(() => setPendingReload('alpha', true)).not.toThrow()
  })
})
