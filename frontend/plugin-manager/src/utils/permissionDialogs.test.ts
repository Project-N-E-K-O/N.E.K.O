import { describe, expect, it } from 'vitest'
import type { PendingPermissions } from '@/api/pluginPermissions'
import { panel, row } from '@/utils/permissionTab.fixtures'
import {
  asksFirst,
  conditionChoices,
  dialogFor,
  dialogSections,
  firstUsePermission,
  laterAsks,
  startRequest,
  unchangedCount,
  undecidedCount,
} from './permissionDialogs'

const NET = 'system.network:connect'
const ENV = 'system.env:read'
const FS_READ = 'system.fs:read'
const MEMORY = 'plugin_sdk.main_agent.memory:read'
const MODELS = 'plugin_sdk.models:invoke'

const ask = (over = {}) => ({ level: 'ask' as const, reason: 'r', background: false, background_reason: null, hosts: [], ...over })
const rows = () => [
  row({ permission: NET, tier: 'high', state: 'undecided', condition: null, declared: ask({ level: 'require' }) }),
  row({ permission: ENV, tier: 'high', state: 'undecided', condition: null, declared: null, reconcile: 'undeclared' }),
  row({ permission: FS_READ, tier: 'medium', state: 'granted', declared: ask() }),
  row({ permission: MEMORY, tier: 'high', state: 'undecided', condition: null, declared: ask() }),
  row({ permission: MODELS, tier: 'low', state: 'granted', declared: ask() }),
]
const pending = (over: Partial<PendingPermissions> = {}): PendingPermissions => ({
  first_use: [],
  incomplete: [],
  to_confirm: [],
  upgraded: [],
  ...over,
})
const use = (permission: string, plugin_id = 'demo') => ({
  plugin_id, permission, at: '2026-11-27T10:00:00Z', count: 1, count_is_lower_bound: false,
})

describe('asksFirst', () => {
  it('asks about required, system and found-but-undeclared items only', () => {
    const [net, env, fs, memory] = rows()
    expect(asksFirst(net!)).toBe(true)
    expect(asksFirst(env!)).toBe(true)
    expect(asksFirst(fs!)).toBe(true)
    expect(asksFirst(memory!)).toBe(false)
    expect(asksFirst(row({ permission: MEMORY, declared: ask({ level: 'require' }) }))).toBe(true)
    expect(asksFirst(row({ permission: MEMORY, declared: null, reconcile: 'undeclared' }))).toBe(true)
  })
})

describe('dialogFor', () => {
  const p = panel({ rows: rows() })

  it('opens the first-start dialog while the plugin was never decided', () => {
    expect(dialogFor(p, pending({ to_confirm: [{ plugin_id: 'demo', items: [NET, ENV] }] }))).toEqual({
      kind: 'first', items: [NET, ENV],
    })
  })

  it('opens the update dialog when it asks for more', () => {
    expect(dialogFor(p, pending({ upgraded: [{ plugin_id: 'demo', items: [ENV] }] }))).toEqual({
      kind: 'upgrade', items: [ENV],
    })
  })

  it('first start wins over uses while undecided; the update over uses too', () => {
    const uses = { first_use: [use(MEMORY)] }
    expect(dialogFor(p, pending({ ...uses, to_confirm: [{ plugin_id: 'demo', items: [NET] }] }))?.kind).toBe('first')
    expect(dialogFor(p, pending({ ...uses, upgraded: [{ plugin_id: 'demo', items: [NET] }] }))?.kind).toBe('upgrade')
  })

  it('opens ② for the first undecided item used while not decided', () => {
    expect(dialogFor(p, pending({ first_use: [use(MEMORY), use(NET)] }))).toEqual({ kind: 'firstUse', permission: NET })
    // Decided since: nothing is waiting for it.
    expect(dialogFor(p, pending({ first_use: [use(FS_READ)] }))).toBeNull()
  })

  it('ignores other plugins, empty item lists, and a missing or older pending answer', () => {
    expect(dialogFor(p, pending({ to_confirm: [{ plugin_id: 'other', items: [NET] }], first_use: [use(NET, 'other')] }))).toBeNull()
    expect(dialogFor(p, pending({ to_confirm: [{ plugin_id: 'demo', items: [] }] }))).toBeNull()
    expect(dialogFor(p, null)).toBeNull()
    expect(dialogFor(p, { first_use: [], incomplete: [] })).toBeNull()
  })

  it('skips entries of another shape, also in the lists of the start dialog', () => {
    const bad = pending({ to_confirm: [{ plugin_id: 'demo', items: 'network' as never }, null as never] })
    expect(dialogFor(p, bad)).toBeNull()
    expect(startRequest(pending({ upgraded: 'demo' as never }), 'demo')).toBeNull()
    expect(startRequest(pending({ to_confirm: [{ plugin_id: 'demo', items: [1 as never] }] }), 'demo')).toBeNull()
    expect(startRequest(pending({ upgraded: [{ plugin_id: 'demo', items: [NET] }] }), 'demo')).toEqual({ kind: 'upgrade', items: [NET] })
  })
})

describe('firstUsePermission', () => {
  it('follows the panel order', () => {
    const p = panel({ rows: rows() })
    expect(firstUsePermission(p, pending({ first_use: [use(MEMORY), use(ENV)] }))).toBe(ENV)
    expect(firstUsePermission(p, null)).toBeNull()
  })
})

describe('dialogSections', () => {
  const p = panel({ rows: rows() })

  it('lists ① by tier, most dangerous first, with decided items too', () => {
    const sections = dialogSections(p, { kind: 'first', items: [NET, ENV] })
    expect(sections.map((section) => [section.tier, section.rows.map((r) => r.permission)])).toEqual([
      ['high', [NET, ENV]],
      ['medium', [FS_READ]],
    ])
    expect(undecidedCount(sections)).toBe(2)
  })

  it('lists only the added items in ③', () => {
    const sections = dialogSections(p, { kind: 'upgrade', items: [ENV, 'system.gone:x'] })
    expect(sections.map((section) => section.rows.map((r) => r.permission))).toEqual([[ENV]])
    expect(undecidedCount(sections)).toBe(1)
  })

  it('has no sections for ②', () => {
    expect(dialogSections(p, { kind: 'firstUse', permission: MEMORY })).toEqual([])
  })

  it('leaves out the safe tier', () => {
    const safe = panel({ rows: [row({ permission: 'system.x:y', tier: 'safe', state: 'undecided' })] })
    expect(dialogSections(safe, { kind: 'first', items: ['system.x:y'] })).toEqual([])
  })
})

describe('laterAsks', () => {
  it('names the undecided optional items ① does not ask about', () => {
    expect(laterAsks(panel({ rows: rows() })).map((r) => r.permission)).toEqual([MEMORY])
  })
})

describe('unchangedCount', () => {
  it('counts what the plugin had before the update', () => {
    // NET (declared), FS_READ, MEMORY, MODELS; ENV is the added one.
    expect(unchangedCount(panel({ rows: rows() }), [ENV])).toBe(4)
    // An undeclared undecided item is nothing it had.
    expect(unchangedCount(panel({ rows: rows() }), [NET])).toBe(3)
  })
})

describe('conditionChoices', () => {
  it('offers "any time" only when the plugin declares background use', () => {
    expect(conditionChoices(row({ anytime_allowed: true }))).toEqual(['in_use', 'anytime'])
    expect(conditionChoices(row({ anytime_allowed: false }))).toEqual(['in_use'])
    expect(conditionChoices(row({ anytime_allowed: null }))).toEqual(['in_use'])
  })
})
