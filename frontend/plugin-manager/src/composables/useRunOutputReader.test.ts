// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick } from 'vue'
import { createRunOutputReader, type RunOutputReader } from './useRunOutputReader'
import type { ExportItem } from '@/api/runs'

const sessions: RunOutputReader[] = []
function create(value: unknown) {
  const scope = effectScope()
  const item: ExportItem = {
    export_item_id: 'one',
    run_id: 'r',
    type: 'json',
    created_at: 1,
    json: value,
  }
  const session = scope.run(() => createRunOutputReader(item))!
  sessions.push(session)
  return { session, item, scope }
}
afterEach(() => {
  for (const session of sessions.splice(0)) session.dispose()
  vi.useRealTimers()
})

describe('per-export reading session', () => {
  it('keeps the same outline, folded branches, search hit and page for an equal fresh snapshot', async () => {
    vi.useFakeTimers()
    const { session, item } = create({
      nested: { list: Array.from({ length: 1200 }, (_, i) => `value-${i}`) },
    })
    const outline = session.lines.value
    session.setCollapsed(false)
    session.query.value = 'value-'
    await nextTick()
    await vi.advanceTimersByTimeAsync(150)
    session.moveMatch(3)
    session.page.value = 2
    const folded = new Set(session.collapsed.value)
    session.update({ ...item, json: JSON.parse(JSON.stringify(item.json)) })
    await nextTick()
    expect(session.lines.value).toBe(outline)
    expect(session.page.value).toBe(2)
    expect(session.query.value).toBe('value-')
    expect(session.matchIndex.value).toBe(3)
    expect(session.collapsed.value).toEqual(folded)
  })

  it('debounces searches and clears scheduled work on disposal', async () => {
    vi.useFakeTimers()
    const { session } = create({ needle: true })
    session.query.value = 'needle'
    await nextTick()
    expect(session.matches.value).toHaveLength(0)
    await vi.advanceTimersByTimeAsync(150)
    expect(session.matches.value).toHaveLength(1)
    session.query.value = 'another'
    await nextTick()
    session.dispose()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('defers large structures, preserves the source and constructs only after explicit loading', () => {
    const value = Array.from({ length: 20000 }, (_, i) => i)
    const { session } = create(value)
    expect(session.needsLoad.value).toBe(true)
    expect(session.lines.value).toHaveLength(0)
    expect(session.jsonValue.value).toBe(value)
    session.loadPreview()
    expect(session.needsLoad.value).toBe(false)
    expect(session.lines.value).toHaveLength(20002)
    expect(session.pageLines.value).toHaveLength(400)
  })

  it('rebuilds when the payload actually changes and clamps an invalid old page', async () => {
    const { session, item } = create(Array.from({ length: 1500 }, (_, i) => i))
    session.page.value = 3
    session.update({ ...item, json: { fresh: true } })
    await nextTick()
    expect(session.page.value).toBe(0)
    expect(session.lines.value.some((line) => line.key === 'fresh')).toBe(true)
  })
})
