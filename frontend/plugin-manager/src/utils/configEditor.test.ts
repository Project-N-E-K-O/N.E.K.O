import { describe, expect, it } from 'vitest'
import { applyProfileOverlay, configNodeMatches, restoreConfigPath } from './configEditor'

const owns = (value: object, key: string) => Object.prototype.hasOwnProperty.call(value, key)

describe('literal configuration keys', () => {
  it('restores a nested __proto__ path without modifying Object.prototype', () => {
    const original = JSON.parse('{"settings":{"__proto__":{"configEditorProbe":42}}}')
    try {
      const result = restoreConfigPath({}, original, ['settings', '__proto__', 'configEditorProbe'])
      expect(owns(Object.prototype, 'configEditorProbe')).toBe(false)
      expect(owns(result.settings, '__proto__')).toBe(true)
      expect(JSON.stringify(result)).toBe(JSON.stringify(original))
    } finally {
      delete (Object.prototype as Record<string, unknown>).configEditorProbe
    }
  })

  it('restores a whole literal __proto__ key as an own property', () => {
    const original = JSON.parse('{"__proto__":{"value":7}}')
    const result = restoreConfigPath({}, original, ['__proto__'])
    expect(Object.getPrototypeOf(result)).toBe(Object.prototype)
    expect(owns(result, '__proto__')).toBe(true)
    expect(JSON.stringify(result)).toBe(JSON.stringify(original))
  })

  it('does not restore inherited source properties', () => {
    const original = Object.create({ settings: { value: 99 } })
    const result = restoreConfigPath({ settings: { value: 1 } }, original, ['settings', 'value'])
    expect(result).toEqual({})
  })

  it('preserves siblings, literal dotted keys, and input objects during undo', () => {
    const draft = { 'section.name': { value: 9, sibling: 2 }, extra: true }
    const original = { 'section.name': { value: 1, sibling: 2 } }
    expect(restoreConfigPath(draft, original, ['section.name', 'value'])).toEqual({
      'section.name': { value: 1, sibling: 2 },
      extra: true,
    })
    expect(draft['section.name'].value).toBe(9)
    expect(restoreConfigPath(draft, original, ['extra'])).toEqual({
      'section.name': draft['section.name'],
    })
  })

  it.each([false, true])('merges __proto__ as data (nested: %s)', (nested) => {
    const branch = JSON.parse('{"__proto__":{"value":42}}')
    const base = nested ? { section: {} } : {}
    const overlay = nested ? { section: branch } : branch
    const result = applyProfileOverlay(base, overlay)
    const target = nested ? result.section : result
    expect(Object.getPrototypeOf(target)).toBe(Object.prototype)
    expect(owns(target, '__proto__')).toBe(true)
    expect(JSON.stringify(result)).toBe(JSON.stringify(overlay))
    expect(JSON.stringify(base)).toBe(nested ? '{"section":{}}' : '{}')
  })

  it('merges existing reserved keys without changing replacement semantics', () => {
    const base = JSON.parse(
      '{"section":{"__proto__":{"a":1},"constructor":{"a":1},"items":[1,2],"keep":true},"plugin":{"id":"original"}}'
    )
    const overlay = JSON.parse(
      '{"section":{"__proto__":{"b":2},"constructor":{"b":2},"items":[3]},"plugin":{"id":"ignored"}}'
    )
    const result = applyProfileOverlay(base, overlay)
    expect(JSON.stringify(result)).toBe(
      '{"section":{"__proto__":{"a":1,"b":2},"constructor":{"a":1,"b":2},"items":[3],"keep":true},"plugin":{"id":"original"}}'
    )
    expect(Object.getPrototypeOf(result.section)).toBe(Object.prototype)
    expect(base.section.items).toEqual([1, 2])
  })
})

describe('profile preview empty tables', () => {
  it('replaces explicit empty tables at every depth without changing inputs', () => {
    const base = { cache: { ttl: 120 }, nested: { cache: { ttl: 60 }, keep: true } }
    const overlay = { cache: {}, nested: { cache: {} } }
    expect(applyProfileOverlay(base, overlay)).toEqual({
      cache: {},
      nested: { cache: {}, keep: true },
    })
    expect(base.cache).toEqual({ ttl: 120 })
    expect(base.nested.cache).toEqual({ ttl: 60 })
    expect(overlay).toEqual({ cache: {}, nested: { cache: {} } })
  })

  it('keeps inheritance for an empty root overlay and protects plugin metadata', () => {
    const base = { cache: { ttl: 120 }, plugin: { id: 'test' } }
    expect(applyProfileOverlay(base, {})).toEqual(base)
    expect(applyProfileOverlay(base, { plugin: {}, cache: {} })).toEqual({
      plugin: { id: 'test' },
      cache: {},
    })
  })
})

describe('configNodeMatches', () => {
  it('returns true when searching for a top-level section name that has children', () => {
    const overlay = {}
    const baseline = {
      llm: { model: 'gpt-4', temperature: 0.7 },
      network: { timeout: 30 },
    }
    const changes: any[] = []

    // Search for 'llm' should match the section itself before recursing into children
    expect(configNodeMatches(overlay, baseline, [], 'llm', 'all', changes, false)).toBe(true)

    // Search for 'network' should match the section
    expect(configNodeMatches(overlay, baseline, [], 'network', 'all', changes, false)).toBe(true)

    // Search for nested path component should also match
    expect(configNodeMatches(overlay, baseline, [], 'model', 'all', changes, false)).toBe(true)

    // Search for non-existent key should not match
    expect(configNodeMatches(overlay, baseline, [], 'nonexistent', 'all', changes, false)).toBe(
      false
    )
  })
})
