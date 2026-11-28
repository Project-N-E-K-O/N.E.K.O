/**
 * View model of the plugin permission tab: declaration (left) against the
 * scan (right), one line per permission, most dangerous tier first.
 *
 * Pure functions over the panel data, so every data state is unit tested
 * without the component.
 */
import { isAxiosError } from 'axios'
import dayjs from 'dayjs'
import type {
  ManifestStatus,
  PermissionCondition,
  PanelRow,
  PermissionCatalog,
  PermissionPanel,
  PendingPermissions,
  RiskTier,
  UsageHistory,
} from '@/api/pluginPermissions'
import { readErrorCode } from '@/utils/request'
import { permissionErrorKey } from '@/utils/pluginPermissionErrors'

export const LISTED_TIERS: RiskTier[] = ['high', 'medium', 'low']

/** The server does not take "refuse" for these; switching off returns them to undecided. */
export const NOT_REFUSABLE = new Set(['plugin_sdk.main_agent.messages:push'])

/** A plugin.toml that was read but cannot be used counts as no declaration. */
export function declarationUsable(manifest: ManifestStatus): boolean {
  return manifest === 'read' || manifest === 'cached'
}

/** Where the switch (declared) or the allow / refuse buttons (not declared) go. */
export function controlSide(row: PanelRow, manifest: ManifestStatus): 'left' | 'right' {
  return declarationUsable(manifest) && row.declared ? 'left' : 'right'
}

/**
 * The marker between the two columns: '=' both sides, '≠' found but not
 * declared, '?' declared but not found. Nothing when the declaration is
 * unknown or the scan has not given an answer.
 */
export function reconcileMark(row: PanelRow): '=' | '≠' | '?' | '' {
  switch (row.reconcile) {
    case 'matched':
      return '='
    case 'undeclared':
      return '≠'
    case 'unscanned':
      return '?'
    default:
      return ''
  }
}

export interface TierSection {
  tier: RiskTier
  rows: PanelRow[]
  collapsed: boolean
}

/** High, medium, low with their rows in the panel's order; safe is one sentence. */
export function tierSections(panel: PermissionPanel): TierSection[] {
  return LISTED_TIERS.map((tier) => {
    const rows = panel.rows.filter((row) => row.tier === tier)
    const info = panel.tiers.find((item) => item.tier === tier)
    return { tier, rows, collapsed: info?.collapsed ?? false }
  })
}

/** Some ways of reading this data are not covered yet (memory, conversations). */
export function partialBlock(catalog: PermissionCatalog | null, permission: string): boolean {
  if (!catalog) return false
  return catalog.privacy_groups.some(
    (group) => group.partial_block && group.permissions.includes(permission),
  )
}

/** The small tag about what the host does with a use of this permission. */
export function enforcementTag(row: Pick<PanelRow, 'enforced' | 'usage_tracked'>): 'notEnforcedNotTracked' | 'recordOnly' | null {
  if (row.enforced) return null
  return row.usage_tracked ? 'recordOnly' : 'notEnforcedNotTracked'
}

/**
 * The condition to grant with. A required item has no "when it may" control:
 * it is granted "any time" when the server allows it (the declaration covers
 * background use), so background use is not left off with no way to turn it on.
 * A stored "any time" the server no longer allows falls back to "while in use";
 * while the declaration cannot be read (``null``) the stored condition is
 * sent as it is, and the server asks to try again later.
 */
export function grantCondition(row: PanelRow, chosen?: PermissionCondition): PermissionCondition {
  if (chosen) return chosen
  if (row.anytime_allowed === false) return 'in_use'
  if (row.declared?.level === 'require' && row.anytime_allowed === true) return 'anytime'
  return row.condition ?? 'in_use'
}

/** "When it may": granted, not required (a required item covers it). */
export function showsCondition(row: PanelRow): boolean {
  return row.state === 'granted' && row.declared?.level !== 'require'
}

export type ConditionLock = 'unavailable' | 'unusable' | 'notDeclared' | null

/** Why "any time" cannot be chosen, or null when it can. */
export function conditionLock(row: PanelRow, manifest: ManifestStatus): ConditionLock {
  if (row.anytime_allowed === true) return null
  if (row.anytime_allowed === null || manifest === 'unavailable') return 'unavailable'
  if (manifest === 'unusable') return 'unusable'
  return 'notDeclared'
}

export type UsageLine =
  | { kind: 'last'; at: string; blocked: boolean; unused30d: boolean }
  | { kind: 'unused30d' }
  | { kind: 'unusedSince'; since: string }
  | null

/**
 * The "last use" line. Nothing when uses are not recorded (never "never
 * used"). "Unused for 30 days" only when the history covers 30 days; else
 * "unused since X" from when it is complete. A system use the plugin
 * process reported lost may hide a use: then nothing is claimed.
 */
export function usageLine(
  row: Pick<PanelRow, 'permission' | 'state' | 'usage_tracked' | 'last_used' | 'unused_30d'>,
  history: UsageHistory | null,
  lostAt: string | null,
): UsageLine {
  if (!row.usage_tracked) return null
  // A system use reported lost after the last recorded one may be the real last use.
  if (row.last_used && row.permission.startsWith('system.') && lostAt && isAfter(lostAt, row.last_used.at)) return null
  if (row.last_used) {
    return {
      kind: 'last',
      at: row.last_used.at,
      blocked: row.last_used.outcome !== 'allowed',
      unused30d: row.unused_30d === true,
    }
  }
  const granted = row.state === 'granted'
  // The server's own answer for a granted item (it also weighs lost entries).
  if (granted && row.unused_30d === true) return { kind: 'unused30d' }
  if (row.permission.startsWith('system.') && lostAt) return null
  if (!history) return null
  if (history.last_use_covers_30d) {
    // Granted: false (decided recently) or unknown, nothing to claim.
    return granted ? null : { kind: 'unused30d' }
  }
  return history.last_use_since ? { kind: 'unusedSince', since: history.last_use_since } : null
}

function isAfter(a: string, b: string): boolean {
  const left = Date.parse(a)
  const right = Date.parse(b)
  // An unreadable time cannot show the last use is complete.
  return Number.isNaN(left) || Number.isNaN(right) || left > right
}

/** Uses while undecided: "N times" or "at least N times". */
export function firstUse(
  pending: PendingPermissions | null,
  pluginId: string,
  row: PanelRow,
): { count: number; atLeast: boolean } | null {
  if (row.state !== 'undecided') return null
  const item = pending?.first_use.find(
    (use) => use.plugin_id === pluginId && use.permission === row.permission,
  )
  return item ? { count: item.count, atLeast: item.count_is_lower_bound } : null
}

/**
 * The plugin's entries of "never decided" and "more after an update", left
 * out when the list could not be read again: they belong to an older answer.
 */
export function withoutStartEntries(
  pending: PendingPermissions,
  pluginId: string,
): Pick<PendingPermissions, 'to_confirm' | 'upgraded'> {
  const keep = (entries: unknown) =>
    Array.isArray(entries) ? entries.filter((entry) => entry?.plugin_id !== pluginId) : []
  return { to_confirm: keep(pending.to_confirm), upgraded: keep(pending.upgraded) }
}

/** The declaration is unknown this time: the tab is incomplete. */
export function incomplete(panel: PermissionPanel, pending: PendingPermissions | null): boolean {
  return panel.manifest === 'unavailable' || Boolean(pending?.incomplete.includes(panel.plugin_id))
}

/** "Today 14:02", "Yesterday 21:17", else the date. Days start at local midnight. */
export function relativeDay(value: string, now: Date = new Date()): 'today' | 'yesterday' | null {
  const at = new Date(value)
  if (Number.isNaN(at.getTime())) return null
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1)
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1)
  if (at >= today && at < tomorrow) return 'today'
  if (at >= yesterday && at < today) return 'yesterday'
  return null
}

type Translate = (key: string, params?: Record<string, unknown>) => string

/** "Today 14:02", "Yesterday 21:17", else "2026-11-18 09:30". */
export function formatUseTime(value: string, t: Translate, now: Date = new Date()): string {
  const at = dayjs(value)
  if (!at.isValid()) return value
  const day = relativeDay(value, now)
  return day ? t(`pluginConsent.tab.${day}`, { time: at.format('HH:mm') }) : at.format('YYYY-MM-DD HH:mm')
}

export function formatUseDate(value: string): string {
  const at = dayjs(value)
  return at.isValid() ? at.format('YYYY-MM-DD') : value
}

/** Blind spot files, most first; rows without a file are not files. */
export function blindSpotFiles(panel: PermissionPanel) {
  return panel.blind_spots.files
    .filter((row) => row.file && row.count > 0)
    .slice()
    .sort((a, b) => Number(b.high_risk) - Number(a.high_risk) || b.count - a.count)
}

/** Per file: how many finding sites and which calls, without line numbers. */
export function scanFiles(panel: PermissionPanel, permission: string) {
  const out = new Map<string, { file: string; sites: number; methods: string[] }>()
  for (const finding of panel.developer.findings[permission] ?? []) {
    const file = finding.file ?? ''
    const entry = out.get(file) ?? { file, sites: 0, methods: [] }
    entry.sites += 1
    if (finding.method && !entry.methods.includes(finding.method)) entry.methods.push(finding.method)
    out.set(file, entry)
  }
  return [...out.values()]
}

/** Message key for a failed allow / refuse / condition change. */
export function decisionErrorKey(error: unknown): string {
  if (isAxiosError(error)) {
    const code = readErrorCode(error)
    switch (code) {
      case 'PLUGIN_MANIFEST_UNAVAILABLE':
        return 'pluginConsent.tab.errors.manifestUnavailable'
      case 'PLUGIN_MANIFEST_UNUSABLE':
        return 'pluginConsent.tab.errors.manifestUnusable'
      case 'PERMISSION_BACKGROUND_NOT_DECLARED':
        return 'pluginConsent.tab.errors.backgroundNotDeclared'
      case 'PLUGIN_OPERATION_BUSY':
        return 'pluginConsent.tab.errors.busy'
    }
    const known = permissionErrorKey(code)
    if (known) return known
  }
  return 'pluginConsent.saveFailed'
}

/** Scan-wide state when it is not a finished scan: rows alone cannot tell. */
export function scanNotice(panel: PermissionPanel): 'pending' | 'failed' | 'partial' | null {
  const status = panel.analysis.status
  if (status === 'completed') return null
  if (status === 'partial') return 'partial'
  // ``unavailable``: no current report yet, one is being made (as the consent views read it).
  if (status === 'queued' || status === 'running' || status === 'unavailable') return 'pending'
  return 'failed'
}

/** Whether a scan is still to come, so the panel is loaded again. */
export function scanPending(panel: PermissionPanel): boolean {
  return scanNotice(panel) === 'pending'
}

const BARE_KEY = /^[A-Za-z0-9_-]+$/

function tomlKey(key: string): string {
  return BARE_KEY.test(key) ? key : JSON.stringify(key)
}

function tomlValue(value: unknown): string {
  if (typeof value === 'string') return JSON.stringify(value)
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) return `[${value.map(tomlValue).join(', ')}]`
  if (value && typeof value === 'object') {
    const items = Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => item !== null && item !== undefined)
      .map(([key, item]) => `${tomlKey(key)} = ${tomlValue(item)}`)
    return items.length ? `{ ${items.join(', ')} }` : '{}'
  }
  return '""'
}

/** The manifest's [permissions] table written back as TOML. */
export function permissionsToml(table: Record<string, unknown> | null): string {
  if (!table) return ''
  const blocks: string[] = []
  for (const [level, items] of Object.entries(table)) {
    if (!items || typeof items !== 'object' || Array.isArray(items)) continue
    const lines = [`[permissions.${tomlKey(level)}]`]
    for (const [id, value] of Object.entries(items as Record<string, unknown>)) {
      lines.push(`${tomlKey(id)} = ${tomlValue(value)}`)
    }
    blocks.push(lines.join('\n'))
  }
  return blocks.join('\n\n')
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value)

/** Responses of another shape (an older or a stub server) count as missing. */
export function validPanel(value: unknown): value is PermissionPanel {
  return isObject(value) && Array.isArray(value.rows) && Array.isArray(value.tiers)
    && isObject(value.counts) && isObject(value.analysis) && isObject(value.source)
    && isObject(value.blind_spots) && Array.isArray(value.blind_spots.files)
    && isObject(value.developer) && isObject(value.developer.findings)
    && Array.isArray(value.developer.blind_spots)
}
export function validCatalog(value: unknown): value is PermissionCatalog {
  return isObject(value) && Array.isArray(value.privacy_groups)
}
export function validPending(value: unknown): value is PendingPermissions {
  return isObject(value) && Array.isArray(value.first_use) && value.first_use.every(isObject)
    && Array.isArray(value.incomplete)
}
export function validHistory(value: unknown): value is { history: UsageHistory; lost_at: string | null } {
  return isObject(value) && isObject(value.history)
}
