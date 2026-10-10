import { describe, expect, it } from 'vitest'

import { createOverviewRequestGate, type OverviewResource } from './overviewRequestGate'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

describe('overview request gate', () => {
  it('keeps status and packs requests current within the same epoch', () => {
    const gate = createOverviewRequestGate()
    const status = gate.begin('status')!
    const packs = gate.begin('packs')!

    expect(gate.isCurrent('status', status)).toBe(true)
    expect(gate.isCurrent('packs', packs)).toBe(true)
  })

  it('only supersedes an older request for the same resource', () => {
    const gate = createOverviewRequestGate()
    const status = gate.begin('status')!
    const olderPacks = gate.begin('packs')!
    const newerPacks = gate.begin('packs')!

    expect(gate.isCurrent('status', status)).toBe(true)
    expect(gate.isCurrent('packs', olderPacks)).toBe(false)
    expect(gate.isCurrent('packs', newerPacks)).toBe(true)
  })

  it('rejects delayed responses and queued work from an invalidated epoch', async () => {
    const gate = createOverviewRequestGate()
    const delayed = deferred<string>()
    const oldEpoch = gate.invalidate()
    const oldTicket = gate.begin('status', oldEpoch)!
    const commit = delayed.promise.then((value) =>
      gate.isCurrent('status', oldTicket) ? value : null
    )

    const newEpoch = gate.invalidate()
    const currentTicket = gate.begin('status', newEpoch)!
    delayed.resolve('stale')

    expect(await commit).toBeNull()
    expect(gate.begin('packs', oldEpoch)).toBeNull()
    expect(gate.isCurrent('status', currentTicket)).toBe(true)
  })

  it('keeps current loading ownership when an older request finishes first', async () => {
    const gate = createOverviewRequestGate()
    const older = deferred<void>()
    const newer = deferred<void>()
    const olderTicket = gate.begin('packs')!
    let loading = true
    const finishOlder = older.promise.then(() => {
      if (gate.isCurrent('packs', olderTicket)) loading = false
    })
    const newerTicket = gate.begin('packs')!
    const finishNewer = newer.promise.then(() => {
      if (gate.isCurrent('packs', newerTicket)) loading = false
    })

    older.resolve()
    await finishOlder
    expect(loading).toBe(true)

    newer.resolve()
    await finishNewer
    expect(loading).toBe(false)
  })

  it.each<OverviewResource>(['status', 'packs'])(
    'invalidates an in-flight %s request',
    (resource) => {
      const gate = createOverviewRequestGate()
      const ticket = gate.begin(resource)!

      gate.invalidate()

      expect(gate.isCurrent(resource, ticket)).toBe(false)
    }
  )
})
