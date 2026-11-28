import { describe, expect, it } from 'vitest'
import { AxiosError, AxiosHeaders } from 'axios'
import type { PermissionCatalog, UsageHistory } from '@/api/pluginPermissions'
import { panel, row } from './permissionTab.fixtures'
import {
  blindSpotFiles,
  conditionLock,
  controlSide,
  decisionErrorKey,
  grantCondition,
  permissionsToml,
  scanNotice,
  validPanel,
  enforcementTag,
  firstUse,
  incomplete,
  partialBlock,
  reconcileMark,
  relativeDay,
  scanFiles,
  showsCondition,
  tierSections,
  usageLine,
} from './permissionTab'

const covered: UsageHistory = {
  since: '2026-10-01T00:00:00Z',
  covers_30d: true,
  last_use_since: '2026-10-01T00:00:00Z',
  last_use_covers_30d: true,
}
const partial: UsageHistory = {
  since: '2026-11-20T00:00:00Z',
  covers_30d: false,
  last_use_since: '2026-11-20T00:00:00Z',
  last_use_covers_30d: false,
}

describe('usageLine', () => {
  it('shows nothing when uses are not recorded, never "never used"', () => {
    expect(usageLine(row({ usage_tracked: false, state: 'undecided' }), covered, null)).toBeNull()
    expect(usageLine(row({ usage_tracked: false, unused_30d: true }), covered, null)).toBeNull()
  })

  it('shows the last use and marks one that would have been blocked', () => {
    const last = { at: '2026-11-27T10:00:00Z', origin_kind: null, outcome: 'would_block' as const }
    expect(usageLine(row({ last_used: last }), covered, null)).toEqual({
      kind: 'last', at: last.at, blocked: true, unused30d: false,
    })
    expect(usageLine(row({ last_used: { ...last, outcome: 'allowed' } }), covered, null)).toMatchObject({ blocked: false })
    expect(usageLine(row({ last_used: { ...last, outcome: 'blocked' } }), covered, null)).toMatchObject({ blocked: true })
  })

  it('keeps the 30-day tag of an old last use', () => {
    const last = { at: '2026-09-01T10:00:00Z', origin_kind: null, outcome: 'allowed' as const }
    expect(usageLine(row({ last_used: last, unused_30d: true }), covered, null)).toMatchObject({ unused30d: true })
  })

  it('says unused for 30 days for a granted item when the server says so', () => {
    expect(usageLine(row({ unused_30d: true }), covered, null)).toEqual({ kind: 'unused30d' })
  })

  it('claims nothing for a granted item the server cannot judge on a covered history', () => {
    expect(usageLine(row({ unused_30d: null }), covered, null)).toBeNull()
    expect(usageLine(row({ unused_30d: false }), covered, null)).toBeNull()
  })

  it('says "unused since X" instead of 30 days when the history is shorter', () => {
    expect(usageLine(row({ unused_30d: null }), partial, null)).toEqual({
      kind: 'unusedSince', since: partial.last_use_since,
    })
    expect(usageLine(row({ state: 'undecided', unused_30d: null }), partial, null)).toEqual({
      kind: 'unusedSince', since: partial.last_use_since,
    })
  })

  it('uses the last-use coverage, not the audit-list coverage', () => {
    const mixed = { ...partial, last_use_since: covered.last_use_since, last_use_covers_30d: true }
    expect(usageLine(row({ state: 'undecided' }), mixed, null)).toEqual({ kind: 'unused30d' })
    const other = { ...covered, last_use_since: partial.last_use_since, last_use_covers_30d: false }
    expect(usageLine(row({ state: 'undecided' }), other, null)).toEqual({
      kind: 'unusedSince', since: partial.last_use_since,
    })
  })

  it('claims nothing about a system item when entries were lost', () => {
    expect(usageLine(row({ state: 'undecided' }), covered, '2026-11-25T00:00:00Z')).toBeNull()
    expect(usageLine(row({ unused_30d: null }), partial, '2026-11-25T00:00:00Z')).toBeNull()
    // Lost system entries say nothing about a non-system permission.
    expect(
      usageLine(row({ permission: 'plugin_sdk.models:invoke', state: 'undecided' }), covered, '2026-11-25T00:00:00Z'),
    ).toEqual({ kind: 'unused30d' })
  })

  it('does not show a last use older than lost system entries as the last one', () => {
    const used = { at: '2026-11-20T10:00:00Z', origin_kind: null, outcome: 'allowed' as const }
    expect(usageLine(row({ last_used: used }), covered, '2026-11-25T00:00:00Z')).toBeNull()
    // Lost before the last use: that use is still the last one.
    expect(usageLine(row({ last_used: used }), covered, '2026-11-19T00:00:00Z')).toMatchObject({ kind: 'last' })
    // Lost system entries say nothing about a non-system permission.
    expect(
      usageLine(row({ permission: 'plugin_sdk.models:invoke', last_used: used }), covered, '2026-11-25T00:00:00Z'),
    ).toMatchObject({ kind: 'last' })
  })

  it('claims nothing without a history or a start time', () => {
    expect(usageLine(row({ state: 'undecided' }), null, null)).toBeNull()
    expect(usageLine(row({ state: 'undecided' }), { ...partial, last_use_since: null }, null)).toBeNull()
  })
})

describe('declaration side', () => {
  it('puts the switch left for a declared item of a usable manifest', () => {
    expect(controlSide(row(), 'read')).toBe('left')
    expect(controlSide(row(), 'cached')).toBe('left')
    expect(controlSide(row({ declared: null }), 'read')).toBe('right')
  })

  it('treats an unusable or unreadable manifest as no declaration', () => {
    expect(controlSide(row(), 'unusable')).toBe('right')
    expect(controlSide(row(), 'unavailable')).toBe('right')
  })

  it('marks the three reconcile states and nothing for unknown ones', () => {
    expect(reconcileMark(row({ reconcile: 'matched' }))).toBe('=')
    expect(reconcileMark(row({ reconcile: 'undeclared' }))).toBe('≠')
    expect(reconcileMark(row({ reconcile: 'unscanned' }))).toBe('?')
    expect(reconcileMark(row({ reconcile: 'scan_pending' }))).toBe('')
    expect(reconcileMark(row({ reconcile: null }))).toBe('')
  })

  it('counts an unreadable manifest or a plugin the pending list lists as incomplete', () => {
    expect(incomplete(panel({ manifest: 'unavailable' }), null)).toBe(true)
    expect(incomplete(panel(), { first_use: [], incomplete: ['demo'] })).toBe(true)
    expect(incomplete(panel(), { first_use: [], incomplete: ['other'] })).toBe(false)
    expect(incomplete(panel({ manifest: 'unusable' }), null)).toBe(false)
  })
})

describe('when it may', () => {
  it('is not shown for required items or items not granted', () => {
    expect(showsCondition(row())).toBe(true)
    expect(showsCondition(row({ declared: { ...row().declared!, level: 'require' } }))).toBe(false)
    expect(showsCondition(row({ state: 'undecided' }))).toBe(false)
    expect(showsCondition(row({ state: 'denied' }))).toBe(false)
    expect(showsCondition(row({ declared: null }))).toBe(true)
  })

  it('says why "any time" cannot be chosen', () => {
    expect(conditionLock(row({ anytime_allowed: true }), 'read')).toBeNull()
    expect(conditionLock(row({ anytime_allowed: false }), 'read')).toBe('notDeclared')
    expect(conditionLock(row({ anytime_allowed: false }), 'unusable')).toBe('unusable')
    expect(conditionLock(row({ anytime_allowed: null }), 'unavailable')).toBe('unavailable')
    expect(conditionLock(row({ anytime_allowed: null }), 'read')).toBe('unavailable')
  })
})

describe('row facts', () => {
  it('tags what the host does with a use of an unenforced permission', () => {
    expect(enforcementTag(row())).toBeNull()
    expect(enforcementTag(row({ enforced: false, usage_tracked: true }))).toBe('recordOnly')
    expect(enforcementTag(row({ enforced: false, usage_tracked: false }))).toBe('notEnforcedNotTracked')
  })

  it('reads the partial-block flag from the privacy groups', () => {
    const catalog = {
      privacy_groups: [
        { group: 'memory', permissions: ['plugin_sdk.main_agent.memory:read'], tier: 'high', partial_block: true },
        { group: 'network', permissions: ['system.network:connect'], tier: 'high', partial_block: false },
      ],
    } as unknown as PermissionCatalog
    expect(partialBlock(catalog, 'plugin_sdk.main_agent.memory:read')).toBe(true)
    expect(partialBlock(catalog, 'system.network:connect')).toBe(false)
    expect(partialBlock(null, 'plugin_sdk.main_agent.memory:read')).toBe(false)
  })

  it('counts uses while undecided and keeps the lower-bound flag', () => {
    const pending = {
      incomplete: [],
      first_use: [
        { plugin_id: 'demo', permission: 'system.network:connect', at: 'x', count: 3, count_is_lower_bound: true },
        { plugin_id: 'other', permission: 'system.network:connect', at: 'x', count: 9, count_is_lower_bound: false },
      ],
    }
    expect(firstUse(pending, 'demo', row({ state: 'undecided' }))).toEqual({ count: 3, atLeast: true })
    expect(firstUse(pending, 'demo', row({ state: 'granted' }))).toBeNull()
    expect(firstUse(pending, 'nobody', row({ state: 'undecided' }))).toBeNull()
    expect(firstUse(null, 'demo', row({ state: 'undecided' }))).toBeNull()
  })
})

describe('sections and files', () => {
  it('lists high, medium and low with their rows and folding', () => {
    const p = panel({
      rows: [row(), row({ permission: 'system.fs:read', tier: 'medium' })],
      tiers: [
        { tier: 'high', count: 1, collapsed: false },
        { tier: 'medium', count: 1, collapsed: true },
        { tier: 'low', count: 0, collapsed: false },
      ],
    })
    expect(tierSections(p).map((s) => [s.tier, s.rows.length, s.collapsed])).toEqual([
      ['high', 1, false],
      ['medium', 1, true],
      ['low', 0, false],
    ])
  })

  it('groups findings per file without line numbers', () => {
    const p = panel({
      developer: {
        findings: {
          'system.network:connect': [
            { file: 'a.py', line: 1, method: 'httpx.get', basis: 'direct' },
            { file: 'a.py', line: 9, method: 'httpx.get', basis: 'direct' },
            { file: 'b.py', line: 3, method: null, basis: 'direct' },
          ],
        },
        blind_spots: [],
        not_analyzed: [],
        manifest_permissions: null,
      },
    })
    expect(scanFiles(p, 'system.network:connect')).toEqual([
      { file: 'a.py', sites: 2, methods: ['httpx.get'] },
      { file: 'b.py', sites: 1, methods: [] },
    ])
  })

  it('lists blind spot files high risk first and drops rows without a file', () => {
    const p = panel({
      blind_spots: {
        files: [
          { file: 'a.py', count: 3, types: { dynamic_import: 3 }, high_risk: false },
          { file: 'b.py', count: 1, types: { native_loader: 1 }, high_risk: true },
          { file: null, count: 2, types: { code_exec: 2 }, high_risk: false },
          { file: 'c.py', count: 0, types: {}, high_risk: false },
        ],
        counted: 6,
        not_analyzed_files: 0,
      },
    })
    expect(blindSpotFiles(p).map((f) => f.file)).toEqual(['b.py', 'a.py'])
  })

  it('tells today and yesterday apart in local time', () => {
    const now = new Date(2026, 10, 27, 15, 0)
    expect(relativeDay(new Date(2026, 10, 27, 0, 1).toISOString(), now)).toBe('today')
    expect(relativeDay(new Date(2026, 10, 26, 23, 59).toISOString(), now)).toBe('yesterday')
    expect(relativeDay(new Date(2026, 10, 25, 12, 0).toISOString(), now)).toBeNull()
    expect(relativeDay('not a date', now)).toBeNull()
  })
})

describe('decisionErrorKey', () => {
  function failure(status: number, code: string) {
    const headers = new AxiosHeaders({ 'X-Error-Code': code })
    return new AxiosError('x', 'ERR', undefined, undefined, {
      status, statusText: '', headers, config: { headers }, data: { detail: { code } },
    })
  }

  it('asks to retry later on 503 and says the manifest is unusable on 422', () => {
    expect(decisionErrorKey(failure(503, 'PLUGIN_MANIFEST_UNAVAILABLE'))).toBe('pluginConsent.tab.errors.manifestUnavailable')
    expect(decisionErrorKey(failure(422, 'PLUGIN_MANIFEST_UNUSABLE'))).toBe('pluginConsent.tab.errors.manifestUnusable')
    expect(decisionErrorKey(failure(422, 'PERMISSION_BACKGROUND_NOT_DECLARED'))).toBe(
      'pluginConsent.tab.errors.backgroundNotDeclared',
    )
    expect(decisionErrorKey(failure(409, 'PLUGIN_OPERATION_BUSY'))).toBe('pluginConsent.tab.errors.busy')
  })

  it('falls back to the generic save failure', () => {
    expect(decisionErrorKey(failure(500, 'SOMETHING_ELSE'))).toBe('pluginConsent.saveFailed')
    expect(decisionErrorKey(new Error('x'))).toBe('pluginConsent.saveFailed')
  })
})

describe('grantCondition', () => {
  it('grants a required item any time when the server allows it', () => {
    const required = row({ declared: { ...row().declared!, level: 'require' }, condition: null })
    expect(grantCondition(required)).toBe('anytime')
    expect(grantCondition({ ...required, anytime_allowed: false })).toBe('in_use')
    expect(grantCondition(required, 'in_use')).toBe('in_use')
  })

  it('keeps the current condition of an optional item', () => {
    expect(grantCondition(row({ condition: null }))).toBe('in_use')
    expect(grantCondition(row({ condition: 'anytime' }))).toBe('anytime')
  })

  it('falls back to "while in use" for a stored "any time" the server does not allow', () => {
    expect(grantCondition(row({ condition: 'anytime', anytime_allowed: false }))).toBe('in_use')
    expect(grantCondition(row({ condition: 'anytime', anytime_allowed: null }))).toBe('in_use')
    // An explicit choice is still sent as chosen.
    expect(grantCondition(row({ condition: 'anytime', anytime_allowed: false }), 'anytime')).toBe('anytime')
  })
})

describe('scan-wide state', () => {
  it.each([
    ['completed', null],
    ['partial', 'partial'],
    ['queued', 'pending'],
    ['running', 'pending'],
    ['failed', 'failed'],
    ['unavailable', 'pending'],
    [null, 'failed'],
  ])('%s -> %s', (status, notice) => {
    expect(scanNotice(panel({ analysis: { ...panel().analysis, status } }))).toBe(notice)
  })
})

describe('permissionsToml', () => {
  it('writes valid TOML inline tables, quoting keys that need it', () => {
    expect(
      permissionsToml({
        require: { 'system.network:connect': { reason: 'say "hi"', hosts: ['a', 'b'], background: false } },
        ask: { 'plugin_sdk.models:invoke': {} },
        broken: 'not a table',
      }),
    ).toBe(
      '[permissions.require]\n"system.network:connect" = { reason = "say \\"hi\\"", hosts = ["a", "b"], background = false }\n\n'
        + '[permissions.ask]\n"plugin_sdk.models:invoke" = {}',
    )
    expect(permissionsToml(null)).toBe('')
  })
})

describe('validPanel', () => {
  it('rejects responses of another shape', () => {
    expect(validPanel({})).toBe(false)
    expect(validPanel(null)).toBe(false)
    expect(validPanel(panel())).toBe(true)
  })

  it('rejects a panel without the nested lists it renders', () => {
    expect(validPanel({ ...panel(), developer: {} })).toBe(false)
    expect(validPanel({ ...panel(), developer: { ...panel().developer, findings: null } })).toBe(false)
    expect(validPanel({ ...panel(), developer: { ...panel().developer, blind_spots: null } })).toBe(false)
    expect(validPanel({ ...panel(), blind_spots: { counted: 0, not_analyzed_files: 0 } })).toBe(false)
  })
})
