import { describe, expect, it } from 'vitest'

import { createStaleResponseGuard } from './staleResponseGuard'

describe('createStaleResponseGuard', () => {
  it('drops an older response that lands after a newer one was applied', () => {
    const guard = createStaleResponseGuard<'installed'>()
    const older = guard.begin()
    const newer = guard.begin()

    expect(guard.accept('installed', newer)).toBe(true)
    expect(guard.accept('installed', older)).toBe(false)
  })

  it('still applies an older result when the newer refresh never applies', () => {
    const guard = createStaleResponseGuard<'installed'>()
    const older = guard.begin()
    guard.begin() // newer refresh fails and never calls accept

    // Regression guard: a "latest request wins" check discarded this too,
    // leaving the output stale until the next successful refresh.
    expect(guard.accept('installed', older)).toBe(true)
  })

  it('keeps a separate high-water mark per output', () => {
    const guard = createStaleResponseGuard<'installed' | 'yanked'>()
    const older = guard.begin()
    const newer = guard.begin()

    expect(guard.accept('installed', newer)).toBe(true)
    // The newer refresh has not written its second output yet.
    expect(guard.accept('yanked', older)).toBe(true)
    expect(guard.accept('yanked', newer)).toBe(true)
    expect(guard.accept('yanked', older)).toBe(false)
  })

  it('lets the same refresh write an output more than once', () => {
    const guard = createStaleResponseGuard<'installed'>()
    const ticket = guard.begin()
    expect(guard.accept('installed', ticket)).toBe(true)
    expect(guard.accept('installed', ticket)).toBe(true)
  })
})
