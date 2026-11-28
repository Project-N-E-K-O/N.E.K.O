import { del, get, put } from './index'

/** Risk tiers, most dangerous first (plugin/sdk/shared/permissions/catalog.py). */
export type RiskTier = 'high' | 'medium' | 'low' | 'safe'
export type PermissionState = 'granted' | 'denied' | 'undecided'
export type PermissionCondition = 'in_use' | 'anytime'
/** Whether the panel read the plugin's plugin.toml this time. */
export type ManifestStatus = 'read' | 'cached' | 'unavailable' | 'unusable'
export type Reconcile = 'matched' | 'undeclared' | 'unscanned' | 'scan_pending' | 'scan_unavailable'
export type UseOutcome = 'allowed' | 'would_block' | 'blocked'

export interface PermissionCatalog {
  tiers: Array<{
    tier: RiskTier
    order: number
    always_expanded: boolean
    collapse_over: number | null
    listed: boolean
  }>
  permissions: Record<string, {
    tier: RiskTier
    enforced: boolean
    lock: boolean
    privacy_group: string
  }>
  blind_spot_tiers: Record<string, RiskTier>
  privacy_groups: Array<{
    group: string
    permissions: string[]
    tier: RiskTier
    /** Some ways of reading this data are not covered by the checks yet. */
    partial_block: boolean
  }>
}

export interface PanelDeclaration {
  level: 'require' | 'ask'
  reason: string | null
  background: boolean
  background_reason: string | null
  hosts: string[]
}

export interface PanelRow {
  permission: string
  tier: RiskTier
  /** null while the declaration could not be read. */
  reconcile: Reconcile | null
  declared: PanelDeclaration | null
  scan: { files: number; sites: number; basis: string | null; possible_only: boolean } | null
  state: PermissionState
  condition: PermissionCondition | null
  condition_basis: string
  /** Whether "any time" may be chosen; null while plugin.toml cannot be read. */
  anytime_allowed: boolean | null
  legacy_any: boolean
  enforced: boolean
  /** false: uses are not recorded, so a missing last_used says nothing. */
  usage_tracked: boolean
  last_used: { at: string; origin_kind: string | null; outcome: UseOutcome } | null
  unused_30d: boolean | null
}

export interface PanelBlindSpotFile {
  file: string | null
  count: number
  types: Record<string, number>
  high_risk: boolean
}

export interface PermissionPanel {
  plugin_id: string
  version: string
  source: { channel: string | null; fingerprint8: string | null; confirmed: boolean }
  grants_reset: { at: string | null; reason: string | null } | null
  checks_disabled: boolean
  manifest: ManifestStatus
  analysis: {
    status: string | null
    analyzer_version: string | null
    analyzed_at: string | null
    declarations: string | null
  }
  counts: { total: number; granted: number; denied: number; undecided: number; undeclared_found: number }
  rows: PanelRow[]
  tiers: Array<{ tier: RiskTier; count: number; collapsed: boolean }>
  blind_spots: { files: PanelBlindSpotFile[]; counted: number; not_analyzed_files: number }
  developer: {
    findings: Record<string, Array<{ file: string | null; line: number | null; method: string | null; basis: string | null }>>
    blind_spots: Array<{ type: string; file: string | null; line: number | null }>
    not_analyzed: Array<Record<string, unknown>>
    manifest_permissions: Record<string, unknown> | null
  }
}

/** From when last uses and ``unused_30d`` are complete. */
export interface UsageHistory {
  since: string | null
  covers_30d: boolean
  last_use_since: string | null
  last_use_covers_30d: boolean
}

export interface PermissionAuditPage {
  history: UsageHistory
  /** When entries of this plugin were last lost (within 31 days). */
  lost_at: string | null
}

/** A plugin and the items it still needs decided (first start, or more after an update). */
export interface PendingPluginItems {
  plugin_id: string
  items: string[]
}

export interface PendingPermissions {
  /** Plugins never decided, or decided under a source since replaced; older servers omit it. */
  to_confirm?: PendingPluginItems[]
  /** Plugins with earlier decisions that still have items to decide; older servers omit it. */
  upgraded?: PendingPluginItems[]
  first_use: Array<{
    plugin_id: string
    permission: string
    at: string
    count: number
    count_is_lower_bound: boolean
  }>
  incomplete: string[]
}

/** One use as the privacy page lists it; no content, only what and why. */
export interface PrivacyUse {
  at: string
  plugin_id: string
  permission: string
  outcome: UseOutcome
  origin_kind: string | null
  /** Network target host, when the use reported one. */
  target: string | null
}

export interface PrivacyOverview {
  checks_disabled: boolean
  history: UsageHistory
  pending: { to_confirm: number; upgraded: number; inferred: number; first_use: number }
  /** Plugins whose declaration could not be read this time. */
  incomplete: string[]
  groups: Array<{
    group: string
    tier: RiskTier
    /** Plugins that can use this data now. */
    plugins: number
    /** Newest use that was not refused, by any plugin. */
    latest: { plugin_id: string; at: string } | null
    partial_block: boolean
  }>
  /** Plugins that can both read personal data and send it out. */
  combos: Array<{ plugin_id: string; reads: string[]; sends: string[] }>
}

export interface PrivacyGroupItem {
  permission: string
  /** null for a plugin that is no longer installed. */
  state: PermissionState | null
  condition: PermissionCondition | null
  condition_basis: string
  legacy_any: boolean
  enforced: boolean
  usage_tracked: boolean
  last_used: { at: string; origin_kind: string | null; outcome: UseOutcome } | null
  unused_30d: boolean | null
  uses_7d: number
}

export interface PrivacyGroupPlugin {
  plugin_id: string
  name: string
  installed: boolean
  has_permission: boolean
  items: PrivacyGroupItem[]
}

export interface PrivacyGroupDetail {
  group: string
  tier: RiskTier
  partial_block: boolean
  checks_disabled: boolean
  history: UsageHistory
  incomplete: string[]
  plugins: PrivacyGroupPlugin[]
  /** Newest first. */
  recent: PrivacyUse[]
}

const base = (id: string) => `/plugin-permissions/plugins/${encodeURIComponent(id)}`
const quiet = { suppressErrorMessage: true }

export function getPermissionCatalog() {
  return get<PermissionCatalog>('/plugin-permissions/catalog', quiet)
}
export function getPermissionPanel(id: string) {
  return get<PermissionPanel>(`${base(id)}/panel`, quiet)
}
/** Only the history coverage and loss time are used: one item is enough. */
export function getPermissionAuditHistory(id: string) {
  return get<PermissionAuditPage>(`${base(id)}/audit?limit=1`, quiet)
}
export function getPendingPermissions() {
  return get<PendingPermissions>('/plugin-permissions/pending', quiet)
}
export function getPrivacyOverview() {
  return get<PrivacyOverview>('/plugin-permissions/privacy', quiet)
}
export function getPrivacyGroup(group: string) {
  return get<PrivacyGroupDetail>(`/plugin-permissions/privacy/${encodeURIComponent(group)}`, quiet)
}
export function decidePermission(
  id: string,
  permission: string,
  state: 'granted' | 'denied',
  condition: PermissionCondition = 'in_use',
) {
  return put<PermissionPanel>(
    `${base(id)}/permissions/${encodeURIComponent(permission)}`,
    { state, condition },
    quiet,
  )
}
export function clearPermission(id: string, permission: string) {
  return del<PermissionPanel>(`${base(id)}/permissions/${encodeURIComponent(permission)}`, quiet)
}
