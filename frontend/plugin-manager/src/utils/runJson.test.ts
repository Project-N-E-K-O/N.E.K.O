import { describe, expect, it } from 'vitest'
import {
  buildJsonLines,
  jsonAncestorIds,
  visibleJsonLines,
  isAutoPreviewJson,
  sameJsonValue,
} from './runJson'

describe('run JSON outline', () => {
  it('keeps falsy values and empty containers with correct closing ranges', () => {
    const lines = buildJsonLines({
      ok: false,
      zero: 0,
      empty: '',
      nothing: null,
      list: [],
      nested: { value: 42 },
    })
    expect(lines.filter((line) => line.kind !== 'close').map((line) => line.text)).toEqual([
      '{',
      'false',
      '0',
      '""',
      'null',
      '[]',
      '{',
      '42',
    ])
    expect(lines[0]!.end).toBe(lines.length)
    const nested = lines.find((line) => line.key === 'nested')!
    expect(lines[nested.end - 1]!.kind).toBe('close')
    const collapsed = new Set([nested.id])
    expect(visibleJsonLines(lines, collapsed).some((line) => line.key === 'value')).toBe(false)
    const match = lines.find((line) => line.key === 'value')!
    expect(visibleJsonLines(lines, collapsed, jsonAncestorIds(lines, match.id))).toContain(match)
  })

  it('uses numeric identities even for arbitrary keys and keeps malicious text inert', () => {
    const value = JSON.parse(
      '{"__proto__":{"a/b~c":"<img src=x onerror=alert(1)>"},"array":[true,false]}'
    )
    const lines = buildJsonLines(value)
    const match = lines.find((line) => line.key === 'a/b~c')!
    expect(match.text).toBe('"<img src=x onerror=alert(1)>"')
    expect(jsonAncestorIds(lines, match.id).size).toBe(2)
    expect(
      lines.filter((line) => line.indexKey && line.kind !== 'close').map((line) => line.key)
    ).toEqual(['0', '1'])
    expect(new Set(lines.map((line) => line.id)).size).toBe(lines.length)
  })

  it('builds deeply nested outlines without using the JavaScript call stack', () => {
    let value: unknown = 'deep'
    for (let i = 0; i < 2500; i++) value = { child: value }
    const lines = buildJsonLines(value)
    expect(lines).toHaveLength(5001)
    expect(visibleJsonLines(lines, new Set([0]))).toHaveLength(1)
    expect(lines.find((line) => line.text === '"deep"')?.depth).toBe(2500)
  })
})

it('budgets huge strings and containers before outline allocation', () => {
  expect(isAutoPreviewJson('x'.repeat(500001))).toBe(false)
  expect(isAutoPreviewJson(Array.from({ length: 20000 }, (_, i) => i))).toBe(false)
  expect(isAutoPreviewJson({ a: [false, 0, null] })).toBe(true)
})
it('compares fresh JSON snapshots without conflating changed values', () => {
  expect(sameJsonValue({ a: [0, false, null] }, { a: [0, false, null] })).toBe(true)
  expect(sameJsonValue({ a: [0] }, { a: [false] })).toBe(false)
  expect(sameJsonValue([], {})).toBe(false)
})
