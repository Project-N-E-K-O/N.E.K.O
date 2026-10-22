import { describe, expect, it } from 'vitest'
import { buildJsonLines, jsonAncestorIds, visibleJsonLines } from './runJson'

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
