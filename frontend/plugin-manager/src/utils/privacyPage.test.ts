import { describe, expect, it } from 'vitest'
import {
  itemUsage,
  overviewSections,
  pendingParts,
  pluginLines,
  recentUses,
  selectedGroup,
  switchRequest,
  validGroupDetail,
  validOverview,
  weekUses,
} from './privacyPage'
import { covered, detail, ENV, item, MEMORY, NET, overview, plugin, PUSH, SPAWN } from './privacyPage.fixtures'
import { row } from './permissionTab.fixtures'

const NOW = new Date('2026-11-28T12:00:00Z')

describe('overviewSections', () => {
  it('groups by tier in the server order, low starts folded, empty tiers are left out', () => {
    const sections = overviewSections(overview())
    expect(sections.map((s) => [s.tier, s.groups.map((g) => g.group), s.collapsed])).toEqual([
      ['high', ['memory', 'conversations', 'network'], false],
      ['medium', ['fs_read'], false],
      ['low', ['listen', 'models'], true],
    ])
    const noMedium = overview({ groups: overview().groups.filter((g) => g.tier !== 'medium') })
    expect(overviewSections(noMedium).map((s) => s.tier)).toEqual(['high', 'low'])
  })
})

describe('pendingParts', () => {
  it('lists only non-empty parts, in a fixed order', () => {
    expect(pendingParts(overview())).toEqual([])
    expect(
      pendingParts(overview({ pending: { first_use: 4, inferred: 3, upgraded: 0, to_confirm: 2 } })),
    ).toEqual([
      { key: 'to_confirm', n: 2 },
      { key: 'inferred', n: 3 },
      { key: 'first_use', n: 4 },
    ])
  })
})

describe('selectedGroup', () => {
  it('keeps a choice that still exists, else the first group', () => {
    expect(selectedGroup(overview(), null)).toBe('memory')
    expect(selectedGroup(overview(), 'fs_read')).toBe('fs_read')
    expect(selectedGroup(overview(), 'gone')).toBe('memory')
    expect(selectedGroup(overview({ groups: [] }), null)).toBeNull()
  })
})

describe('weekUses', () => {
  it('shows nothing when uses are not recorded or there were none', () => {
    expect(weekUses(item({ uses_7d: 0 }), covered, NOW)).toBeNull()
    expect(weekUses(item({ uses_7d: 5, usage_tracked: false }), covered, NOW)).toBeNull()
  })
  it('is always a lower bound for system uses, which the plugin process may lose', () => {
    expect(weekUses(item({ permission: NET, uses_7d: 12 }), covered, NOW)).toEqual({ count: 12, atLeast: true })
  })
  it('is exact with 7 days of history, a lower bound with less or none', () => {
    expect(weekUses(item({ uses_7d: 12 }), covered, NOW)).toEqual({ count: 12, atLeast: false })
    const short = { ...covered, since: '2026-11-25T00:00:00Z' }
    expect(weekUses(item({ uses_7d: 12 }), short, NOW)).toEqual({ count: 12, atLeast: true })
    expect(weekUses(item({ uses_7d: 12 }), { ...covered, since: null }, NOW)).toEqual({ count: 12, atLeast: true })
    expect(weekUses(item({ uses_7d: 12 }), null, NOW)).toEqual({ count: 12, atLeast: true })
  })
})

describe('itemUsage', () => {
  it('says nothing for items whose uses are not recorded', () => {
    expect(itemUsage(item({ usage_tracked: false, state: 'denied' }), covered)).toBeNull()
  })
  it('shows the last use and whether it would have been blocked', () => {
    const last = { at: '2026-11-28T06:00:00Z', origin_kind: 'chat_tool', outcome: 'would_block' as const }
    expect(itemUsage(item({ last_used: last }), covered)).toEqual({
      kind: 'last', at: last.at, blocked: true, unused30d: false,
    })
  })
  it('claims "unused for 30 days" only with 30 days of history', () => {
    expect(itemUsage(item({ state: 'denied' }), covered)).toEqual({ kind: 'unused30d' })
    const short = { ...covered, last_use_covers_30d: false, last_use_since: '2026-11-18T00:00:00Z' }
    expect(itemUsage(item({ state: 'denied' }), short)).toEqual({ kind: 'unusedSince', since: '2026-11-18T00:00:00Z' })
    expect(itemUsage(item({ state: 'granted', unused_30d: true }), short)).toEqual({ kind: 'unused30d' })
  })
  it('claims nothing about an unused system permission beyond the server answer', () => {
    // No per-plugin loss time here: the plugin process may have lost a use.
    expect(itemUsage(item({ permission: NET, state: 'denied' }), covered)).toBeNull()
    expect(itemUsage(item({ permission: NET, state: 'granted', unused_30d: true }), covered)).toEqual({ kind: 'unused30d' })
  })
})

describe('pluginLines', () => {
  it('one line per plugin and permission, with what the row shows', () => {
    const lines = pluginLines(
      detail({
        group: 'process_env',
        plugins: [
          plugin({ items: [item({ permission: SPAWN, condition: 'anytime' }), item({ permission: ENV, state: 'undecided', condition: null, enforced: false, usage_tracked: false })] }),
        ],
      }),
      NOW,
    )
    expect(lines.map((l) => [l.permission, l.state, l.condition, l.switchable])).toEqual([
      [SPAWN, 'granted', 'anytime', true],
      [ENV, 'undecided', null, true],
    ])
  })
  it('shows "when it may" and the earlier approval only for a granted item', () => {
    const [denied] = pluginLines(detail({ plugins: [plugin({ items: [item({ state: 'denied', legacy_any: true })] })] }), NOW)
    expect(denied!.condition).toBeNull()
    expect(denied!.legacyAny).toBe(false)
    const [legacy] = pluginLines(detail({ plugins: [plugin({ items: [item({ legacy_any: true })] })] }), NOW)
    expect(legacy!.legacyAny).toBe(true)
  })
  it('suggests turning off only a granted item unused for 30 days', () => {
    const lines = (state: 'granted' | 'denied', unused: boolean) =>
      pluginLines(detail({ plugins: [plugin({ items: [item({ state, unused_30d: unused })] })] }), NOW)[0]!
    expect(lines('granted', true).suggestOff).toBe(true)
    expect(lines('granted', false).suggestOff).toBe(false)
    expect(lines('denied', false).suggestOff).toBe(false)
    const lastOld = pluginLines(
      detail({ plugins: [plugin({ items: [item({ unused_30d: true, last_used: { at: '2026-09-02T00:00:00Z', origin_kind: null, outcome: 'allowed' } })] })] }),
      NOW,
    )[0]!
    expect(lastOld.suggestOff).toBe(true)
  })
  it('an uninstalled plugin has no switch; an installed one without the permission says so', () => {
    const [gone, none] = pluginLines(
      detail({
        plugins: [
          plugin({ plugin_id: 'old', installed: false, has_permission: false, items: [item({ state: null, condition: null, last_used: { at: '2026-11-20T00:00:00Z', origin_kind: null, outcome: 'allowed' } })] }),
          plugin({ plugin_id: 'translate', has_permission: false, items: [item({ state: 'denied' })] }),
        ],
      }),
      NOW,
    )
    expect([gone!.switchable, gone!.noPermission, gone!.installed]).toEqual([false, false, false])
    expect([none!.switchable, none!.noPermission]).toEqual([true, true])
  })
})

describe('switchRequest', () => {
  it('grants with the kept condition, refuses otherwise, clears what cannot be refused', () => {
    // Granting saves what the permission tab saves for the same row.
    const required = row({ permission: MEMORY, state: 'undecided', condition: null, anytime_allowed: true, declared: { level: 'require', reason: null, background: true, background_reason: null, hosts: [] } })
    expect(switchRequest({ permission: MEMORY }, true, required)).toEqual({ kind: 'decide', state: 'granted', condition: 'anytime' })
    const optional = row({ permission: MEMORY, state: 'denied', condition: null, anytime_allowed: true, declared: { level: 'ask', reason: null, background: true, background_reason: null, hosts: [] } })
    expect(switchRequest({ permission: MEMORY }, true, optional)).toEqual({ kind: 'decide', state: 'granted', condition: 'in_use' })
    expect(switchRequest({ permission: MEMORY }, false)).toEqual({ kind: 'decide', state: 'denied', condition: 'in_use' })
    expect(switchRequest({ permission: PUSH }, false)).toEqual({ kind: 'clear' })
  })

  it('keeps a stored "any time" while the declaration cannot be read', () => {
    const held = row({ permission: MEMORY, state: 'denied', condition: 'anytime', anytime_allowed: null })
    expect(switchRequest({ permission: MEMORY }, true, held)).toEqual({ kind: 'decide', state: 'granted', condition: 'anytime' })
  })

  it('does not send a stored "any time" the server no longer allows', () => {
    const legacy = row({ permission: MEMORY, state: 'denied', condition: 'anytime', anytime_allowed: false, legacy_any: true })
    expect(switchRequest({ permission: MEMORY }, true, legacy)).toEqual({ kind: 'decide', state: 'granted', condition: 'in_use' })
  })
})

describe('recentUses', () => {
  it('filters to uses that would have been blocked', () => {
    const recent = [
      { at: '2026-11-28T06:00:00Z', plugin_id: 'weather', permission: MEMORY, outcome: 'allowed' as const, origin_kind: null, target: null },
      { at: '2026-11-27T06:00:00Z', plugin_id: 'translate', permission: MEMORY, outcome: 'would_block' as const, origin_kind: null, target: null },
      { at: '2026-11-26T06:00:00Z', plugin_id: 'translate', permission: MEMORY, outcome: 'blocked' as const, origin_kind: null, target: null },
    ]
    expect(recentUses(detail({ recent }), false)).toHaveLength(3)
    expect(recentUses(detail({ recent }), true).map((u) => u.outcome)).toEqual(['would_block', 'blocked'])
  })
})

describe('validOverview / validGroupDetail', () => {
  it('treats other shapes as missing', () => {
    expect(validOverview(overview())).toBe(true)
    expect(validOverview({ groups: [] })).toBe(false)
    expect(validOverview(null)).toBe(false)
    expect(validGroupDetail(detail())).toBe(true)
    expect(validGroupDetail({ group: 'memory', plugins: [] })).toBe(false)
    expect(validGroupDetail({ group: 'memory', plugins: [], recent: [], incomplete: [] })).toBe(false)
    expect(validGroupDetail(detail({ history: { since: null, covers_30d: false, last_use_since: null, last_use_covers_30d: false } }))).toBe(true)
  })
})
