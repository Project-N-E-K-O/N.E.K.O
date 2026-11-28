/**
 * View model of the three permission dialogs: before the first start (①),
 * an optional permission used before it was decided (②), and more
 * permissions after an update (③).
 *
 * Pure functions over the panel and the pending list, so every state is
 * unit tested without the component.
 */
import type {
  PanelRow,
  PendingPermissions,
  PendingPluginItems,
  PermissionCondition,
  PermissionPanel,
  RiskTier,
} from '@/api/pluginPermissions'
import { LISTED_TIERS } from '@/utils/permissionTab'

export type DialogRequest =
  | { kind: 'first'; items: string[] }
  | { kind: 'upgrade'; items: string[] }
  | { kind: 'firstUse'; permission: string }

/** An item the first-start dialog asks about: required, a system one, or found but not declared. */
export function asksFirst(row: PanelRow): boolean {
  return (
    row.declared?.level === 'require'
    || row.permission.startsWith('system.')
    || row.reconcile === 'undeclared'
  )
}

/**
 * Which dialog "Decide item by item" opens for a plugin: the first-start
 * one while it was never decided, the update one when it asks for more,
 * else the first undecided permission it used while not decided.
 * Null when nothing is waiting.
 */
export function dialogFor(
  panel: PermissionPanel,
  pending: PendingPermissions | null,
): DialogRequest | null {
  if (!pending) return null
  const start = startRequest(pending, panel.plugin_id)
  if (start) return start
  const used = firstUsePermission(panel, pending)
  return used ? { kind: 'firstUse', permission: used } : null
}

/**
 * The dialog shown before a start, and the first choice of "Decide item by
 * item": ① while the plugin was never decided, ③ when an update asks for
 * more. Entries of another shape are skipped.
 */
export function startRequest(pending: PendingPermissions, pluginId: string): DialogRequest | null {
  const listed = (entries: unknown) =>
    (Array.isArray(entries) ? entries : []).find(
      (entry): entry is PendingPluginItems =>
        Boolean(entry)
        && entry.plugin_id === pluginId
        && Array.isArray(entry.items)
        && entry.items.length > 0
        && entry.items.every((item: unknown) => typeof item === 'string'),
    )
  const first = listed(pending.to_confirm)
  if (first) return { kind: 'first', items: [...first.items] }
  const upgrade = listed(pending.upgraded)
  return upgrade ? { kind: 'upgrade', items: [...upgrade.items] } : null
}

/** The first undecided permission, in the panel's order, used while not decided. */
export function firstUsePermission(panel: PermissionPanel, pending: PendingPermissions | null): string | null {
  const used = new Set(
    (pending?.first_use ?? [])
      .filter((use) => use.plugin_id === panel.plugin_id)
      .map((use) => use.permission),
  )
  return panel.rows.find((row) => row.state === 'undecided' && used.has(row.permission))?.permission ?? null
}

export interface DialogSection {
  tier: RiskTier
  rows: PanelRow[]
}

/**
 * The rows of dialog ① or ③ by tier, most dangerous first. ① lists every
 * item it asks about (decided ones too, so a reopened dialog shows the
 * answers); ③ lists only the items the update added. Items the pending
 * list names but the panel no longer has are left out.
 */
export function dialogSections(panel: PermissionPanel, request: DialogRequest): DialogSection[] {
  if (request.kind === 'firstUse') return []
  const named = new Set(request.items)
  const rows = panel.rows.filter((row) =>
    request.kind === 'first' ? named.has(row.permission) || asksFirst(row) : named.has(row.permission),
  )
  return LISTED_TIERS.map((tier) => ({ tier, rows: rows.filter((row) => row.tier === tier) })).filter(
    (section) => section.rows.length > 0,
  )
}

/** Optional permissions ① does not ask about: "it may ask you when it uses them". */
export function laterAsks(panel: PermissionPanel): PanelRow[] {
  return panel.rows.filter(
    (row) => row.declared?.level === 'ask' && !asksFirst(row) && row.state === 'undecided',
  )
}

/** Items of ③ the update did not touch: what the plugin had before. */
export function unchangedCount(panel: PermissionPanel, items: string[]): number {
  const named = new Set(items)
  return panel.rows.filter(
    (row) => !named.has(row.permission) && (row.declared !== null || row.state !== 'undecided'),
  ).length
}

export function undecidedCount(sections: DialogSection[]): number {
  return sections.reduce(
    (total, section) => total + section.rows.filter((row) => row.state === 'undecided').length,
    0,
  )
}

/**
 * "When it may" choices of dialog ②: "while I use it" always, "any time"
 * only when the plugin declares background use (``anytime_allowed``).
 */
export function conditionChoices(row: PanelRow): PermissionCondition[] {
  return row.anytime_allowed === true ? ['in_use', 'anytime'] : ['in_use']
}
