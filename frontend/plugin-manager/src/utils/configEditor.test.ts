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

describe('profile preview boundaries', () => {
  it('keeps inheritance for an empty root overlay and protects plugin metadata', () => {
    const base = { cache: { ttl: 120 }, plugin: { id: 'test' } }
    expect(applyProfileOverlay(base, {})).toEqual(base)
    // A profile may not modify the 'plugin' section, so its overlay is ignored.
    expect(applyProfileOverlay(base, { plugin: {}, cache: {} })).toEqual(base)
  })
})

describe('server merge markers and empty tables', () => {
  // Expectations below are the real output of the server merge
  // (plugin/server/infrastructure/config_profiles.py + config_merge.py), captured
  // by running apply_user_config_profiles over the same base and overlay pairs.
  const cases: Array<[string, any, any, any]> = [
    [
      'keeps base fields for a top-level empty table',
      { cache: { ttl: 120 } },
      { cache: {} },
      { cache: { ttl: 120 } },
    ],
    [
      'keeps a top-level __DELETE__ as data',
      { keep: 1, drop: 2 },
      { drop: '__DELETE__' },
      { drop: '__DELETE__', keep: 1 },
    ],
    [
      'keeps a top-level __replace__ marker for a new table',
      { other: 1 },
      { t: { __replace__: true, y: 3 } },
      { other: 1, t: { __replace__: true, y: 3 } },
    ],
    [
      'keeps a top-level __replace__ marker beside base fields',
      { t: { x: 1 } },
      { t: { __replace__: true, y: 3 } },
      { t: { __replace__: true, x: 1, y: 3 } },
    ],
    [
      'replaces a nested empty table',
      { a: { cache: { ttl: 120 }, keep: 1 } },
      { a: { cache: {} } },
      { a: { cache: {}, keep: 1 } },
    ],
    [
      'honours a nested __replace__ marker',
      { a: { keep: 1, t: { x: 1, y: 2 } } },
      { a: { t: { __replace__: true, y: 3 } } },
      { a: { keep: 1, t: { y: 3 } } },
    ],
    [
      'honours __replace__ for a table missing from the base',
      { a: { keep: 1 } },
      { a: { t: { __replace__: true, y: 3 } } },
      { a: { keep: 1, t: { y: 3 } } },
    ],
    [
      'honours a nested __DELETE__ marker',
      { a: { keep: 1, drop: 2 } },
      { a: { drop: '__DELETE__' } },
      { a: { keep: 1 } },
    ],
    [
      'replaces nested scalars and arrays',
      { a: { n: 1, arr: [1, 2] } },
      { a: { n: 5, arr: [3] } },
      { a: { n: 5, arr: [3] } },
    ],
    [
      'keeps markers below a table missing from the base',
      { other: 1 },
      { t: { u: { __replace__: true, y: 3 } } },
      { other: 1, t: { u: { __replace__: true, y: 3 } } },
    ],
  ]

  it.each(cases)('%s', (_name, base, overlay, expected) => {
    expect(applyProfileOverlay(base, overlay)).toEqual(expected)
  })

  it('never mutates the base or the overlay', () => {
    const base = { a: { keep: 1, t: { x: 1 } }, cache: { ttl: 120 } }
    const overlay = { a: { t: { __replace__: true, y: 3 } }, cache: {} }
    applyProfileOverlay(base, overlay)
    expect(base).toEqual({ a: { keep: 1, t: { x: 1 } }, cache: { ttl: 120 } })
    expect(overlay).toEqual({ a: { t: { __replace__: true, y: 3 } }, cache: {} })
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

  it('does not match the root as configured when the overlay is empty', () => {
    const overlay = {}
    const baseline = { cache: { ttl: 120 }, nested: { value: 1 } }
    const changes: any[] = []
    // Root node with empty overlay should not match 'configured' filter
    expect(configNodeMatches(overlay, baseline, [], '', 'configured', changes, false)).toBe(false)
    // Non-root paths can still match their own state
    const overlayWithData = { cache: { ttl: 60 } }
    expect(configNodeMatches(overlayWithData, baseline, [], '', 'configured', changes, false)).toBe(
      true
    )
  })
})
