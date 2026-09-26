import { describe, expect, it } from 'vitest'

import { createLatestRequestGate } from './latestRequest'

describe('latest request gate', () => {
  it('rejects an older response after a newer request starts', () => {
    const gate = createLatestRequestGate()
    const older = gate.begin()
    const newer = gate.begin()

    expect(gate.isLatest(older)).toBe(false)
    expect(gate.isLatest(newer)).toBe(true)
  })

  it('invalidates an in-flight response after a local mutation commits', () => {
    const gate = createLatestRequestGate()
    const request = gate.begin()

    gate.invalidate()

    expect(gate.isLatest(request)).toBe(false)
  })
})
