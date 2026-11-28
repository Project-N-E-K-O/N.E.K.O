/**
 * View model of the privacy page: one row per kind of data (left), the
 * plugins that can use the chosen one and its recent records (right).
 *
 * Pure functions over the server's answers, so every data state is unit
 * tested without the component. Nothing here concludes more than the server
 * said: where it gives no answer, nothing is shown.
 */
import type {
  PermissionState,
  PermissionCondition,
  PrivacyGroupDetail,
  PrivacyGroupItem,
  PrivacyGroupPlugin,
  PrivacyOverview,
  PrivacyUse,
  RiskTier,
  UsageHistory,
} from '@/api/pluginPermissions'
import { LISTED_TIERS, NOT_REFUSABLE, usageLine, type UsageLine } from '@/utils/permissionTab'

/** Kinds of personal data: their count reads "N plugins can read". */
export const PERSONAL_GROUPS = new Set(['memory', 'conversations'])

/** Origin kinds with words of their own; others show no reason. */
export const ORIGIN_KINDS = new Set(['chat_tool', 'chat_hook', 'ui_action', 'run', 'plugin_self', 'system'])

export type OverviewGroup = PrivacyOverview['groups'][number]

export interface OverviewSection {
  tier: RiskTier
  groups: OverviewGroup[]
  /** Low risk starts folded, as on the design. */
  collapsed: boolean
}

/** High, medium, low with their groups in the server's order. */
export function overviewSections(overview: PrivacyOverview): OverviewSection[] {
  return LISTED_TIERS.map((tier) => ({
    tier,
    groups: overview.groups.filter((group) => group.tier === tier),
    collapsed: tier === 'low',
  })).filter((section) => section.groups.length > 0)
}

export type PendingKey = keyof PrivacyOverview['pending']
const PENDING_ORDER: PendingKey[] = ['to_confirm', 'upgraded', 'inferred', 'first_use']

/** The non-empty parts of "needs you", in a fixed order. */
export function pendingParts(overview: PrivacyOverview): Array<{ key: PendingKey; n: number }> {
  return PENDING_ORDER.map((key) => ({ key, n: overview.pending?.[key] ?? 0 })).filter((part) => part.n > 0)
}

/** The group shown on the right: the chosen one while it exists, else the first. */
export function selectedGroup(overview: PrivacyOverview, chosen: string | null): string | null {
  if (chosen && overview.groups.some((group) => group.group === chosen)) return chosen
  return overview.groups[0]?.group ?? null
}

export interface PluginLine {
  key: string
  pluginId: string
  name: string
  installed: boolean
  /** Shown next to the name when the group has more than one permission. */
  permission: string
  state: PermissionState | null
  /** "When it may", only for a granted item. */
  condition: PermissionCondition | null
  legacyAny: boolean
  enforced: boolean
  usageTracked: boolean
  /** The plugin cannot use any permission of this group now. */
  noPermission: boolean
  usage: UsageLine
  week: { count: number; atLeast: boolean } | null
  /** Granted and not used for 30 days: the one objective hint. */
  suggestOff: boolean
  /** Whether the switch is shown. */
  switchable: boolean
}

const WEEK_MS = 7 * 24 * 60 * 60 * 1000

/**
 * Uses in the last 7 days. Nothing when uses are not recorded or there were
 * none; "at least" while the complete history is shorter than 7 days.
 */
export function weekUses(
  item: PrivacyGroupItem,
  history: UsageHistory | null,
  now: Date = new Date(),
): { count: number; atLeast: boolean } | null {
  if (!item.usage_tracked || !(item.uses_7d > 0)) return null
  const since = history?.since ? new Date(history.since) : null
  const complete = since !== null && !Number.isNaN(since.getTime()) && now.getTime() - since.getTime() >= WEEK_MS
  return { count: item.uses_7d, atLeast: !complete }
}

/**
 * The "last use" line, as on the permission tab. This page has no
 * per-plugin loss time: for system uses (reported by the plugin process,
 * which may lose some) nothing is claimed beyond the server's own answer.
 */
export function itemUsage(item: PrivacyGroupItem, history: UsageHistory | null): UsageLine {
  const lostAt = item.permission.startsWith('system.') ? 'unknown' : null
  return usageLine({ ...item, state: item.state ?? 'undecided' }, history, lostAt)
}

/** One line per plugin and permission of the group. */
export function pluginLines(detail: PrivacyGroupDetail, now: Date = new Date()): PluginLine[] {
  const lines: PluginLine[] = []
  for (const plugin of detail.plugins) {
    for (const item of plugin.items) {
      const usage = itemUsage(item, detail.history)
      const granted = item.state === 'granted'
      lines.push({
        key: `${plugin.plugin_id}\u0000${item.permission}`,
        pluginId: plugin.plugin_id,
        name: plugin.name || plugin.plugin_id,
        installed: plugin.installed,
        permission: item.permission,
        state: item.state,
        condition: granted ? item.condition : null,
        legacyAny: granted && item.legacy_any,
        enforced: item.enforced,
        usageTracked: item.usage_tracked,
        noPermission: plugin.installed && !plugin.has_permission,
        usage,
        week: weekUses(item, detail.history, now),
        suggestOff:
          granted && (usage?.kind === 'unused30d' || (usage?.kind === 'last' && usage.unused30d)),
        switchable: plugin.installed && item.state !== null,
      })
    }
  }
  return lines
}

/** What switching a line on or off asks the server for. */
export function switchRequest(
  line: Pick<PluginLine, 'permission' | 'condition'>,
  on: boolean,
): { kind: 'decide'; state: 'granted' | 'denied'; condition: PermissionCondition } | { kind: 'clear' } {
  if (on) return { kind: 'decide', state: 'granted', condition: line.condition ?? 'in_use' }
  if (NOT_REFUSABLE.has(line.permission)) return { kind: 'clear' }
  return { kind: 'decide', state: 'denied', condition: 'in_use' }
}

/** Uses judged not allowed; while checks only record, they were let through. */
export function wouldBlock(use: Pick<PrivacyUse, 'outcome'>): boolean {
  return use.outcome !== 'allowed'
}

export function recentUses(detail: PrivacyGroupDetail, onlyWouldBlock: boolean): PrivacyUse[] {
  return onlyWouldBlock ? detail.recent.filter(wouldBlock) : detail.recent
}

/** Plugin names known to the page, from the group's plugins. */
export function pluginNames(detail: PrivacyGroupDetail | null): Map<string, string> {
  return new Map((detail?.plugins ?? []).map((plugin: PrivacyGroupPlugin) => [plugin.plugin_id, plugin.name]))
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value)

/** Responses of another shape (an older or a stub server) count as missing. */
export function validOverview(value: unknown): value is PrivacyOverview {
  return isObject(value) && Array.isArray(value.groups) && Array.isArray(value.combos)
    && isObject(value.pending) && Array.isArray(value.incomplete)
}
export function validGroupDetail(value: unknown): value is PrivacyGroupDetail {
  return isObject(value) && typeof value.group === 'string' && Array.isArray(value.plugins)
    && Array.isArray(value.recent) && Array.isArray(value.incomplete)
}
