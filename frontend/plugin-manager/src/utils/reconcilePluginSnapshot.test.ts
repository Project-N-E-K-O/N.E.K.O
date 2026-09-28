import { describe, expect, it } from 'vitest'

import { reconcilePluginSnapshot } from './reconcilePluginSnapshot'
import type { PluginMeta } from '@/types/api'

const plugin = (id: string, version = '1.0.0'): PluginMeta => ({
  id,
  name: id,
  description: '',
  version,
  type: 'plugin',
})

describe('reconcilePluginSnapshot', () => {
  it('returns the previous array when the snapshot is unchanged', () => {
    const previous = [plugin('demo')]
    const next = reconcilePluginSnapshot(previous, [plugin('demo')])

    expect(next).toBe(previous)
    expect(next[0]).toBe(previous[0])
  })

  it('preserves unchanged plugin objects around a changed plugin', () => {
    const stable = plugin('stable')
    const previous = [stable, plugin('changed')]
    const next = reconcilePluginSnapshot(previous, [plugin('stable'), plugin('changed', '2.0.0')])

    expect(next).not.toBe(previous)
    expect(next[0]).toBe(stable)
    expect(next[1]).not.toBe(previous[1])
  })

  it('follows incoming order and removes plugins absent from the snapshot', () => {
    const previous = [plugin('removed'), plugin('kept')]
    const next = reconcilePluginSnapshot(previous, [plugin('kept'), plugin('added')])

    expect(next.map(item => item.id)).toEqual(['kept', 'added'])
    expect(next[0]).toBe(previous[1])
  })

  it('detects nested metadata changes', () => {
    const previous = [{ ...plugin('demo'), author: { name: 'A' } }]
    const incoming = [{ ...plugin('demo'), author: { name: 'B' } }]

    const next = reconcilePluginSnapshot(previous, incoming)

    expect(next[0]).not.toBe(previous[0])
    expect(next[0]?.author?.name).toBe('B')
  })
})
