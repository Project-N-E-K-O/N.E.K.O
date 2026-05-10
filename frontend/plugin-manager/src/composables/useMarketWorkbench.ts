/**
 * 插件市场工作台 composable —— 在 useGridWorkbench 基础上注入 Market 专属语义。
 *
 * groups = recommended / all（单选切换）
 * qualifierMatchers: zone / tag / author / is:recommended|installed / v:>=x
 * searchIndex 覆盖 name / description / tags / author
 */
import { computed, toValue, type MaybeRefOrGetter } from 'vue'
import {
  useGridWorkbench,
  normalizeSearchPart,
  safePinyin,
  type QualifierMatcher,
} from '@/composables/useGridWorkbench'
import type { MarketPlugin } from '@/api/market'

export type MarketWorkbenchItem = Omit<MarketPlugin, 'id'> & {
  /** Grid workbench 要求字符串 id，原始 Market 数字 id 统一转字符串。 */
  id: string
  /** 保留原始类型的 id（`number | string`），用于调用后端接口。 */
  rawId: number | string
  searchIndex?: string
}

export interface MarketWorkbenchOptions {
  /** 判断某个 Market 插件是否本地已安装。由调用方注入本地插件集合并做 slug/name 配对。 */
  isInstalled?: (plugin: MarketPlugin) => boolean
}

function buildMarketSearchIndex(plugin: MarketWorkbenchItem): string {
  const textParts: Array<string | undefined> = [
    String(plugin.id),
    plugin.slug,
    plugin.name,
    plugin.description,
    plugin.short_description,
    plugin.version,
    plugin.author?.name,
    plugin.zone,
    ...(plugin.tags || []),
  ]

  const pinyinParts = [plugin.name, plugin.description, plugin.short_description]
    .flatMap((value) => {
      const source = value || ''
      const full = safePinyin(source, 'pinyin').replace(/\s+/g, ' ').trim()
      const initials = safePinyin(source, 'first').replace(/\s+/g, '').trim()
      return [full, full.replace(/\s+/g, ''), initials]
    })

  return [...textParts, ...pinyinParts]
    .map(normalizeSearchPart)
    .filter(Boolean)
    .join('\n')
}

// ─── 简单的 semver 比较（够用即可）────────────────────────────────
function compareVersion(a: string, b: string): number {
  const pa = a.replace(/^v/i, '').split(/[.\-+]/).map((n) => parseInt(n, 10) || 0)
  const pb = b.replace(/^v/i, '').split(/[.\-+]/).map((n) => parseInt(n, 10) || 0)
  const len = Math.max(pa.length, pb.length)
  for (let i = 0; i < len; i++) {
    const diff = (pa[i] ?? 0) - (pb[i] ?? 0)
    if (diff !== 0) return diff
  }
  return 0
}

function buildMarketQualifiers(
  options: MarketWorkbenchOptions,
): Record<string, QualifierMatcher<MarketWorkbenchItem>> {
  return {
    is(plugin, value) {
      switch (value) {
        case 'recommended':
        case 'featured':
          return !!plugin.is_recommended
        case 'installed':
          return options.isInstalled?.(plugin) ?? false
        case 'uninstalled':
        case 'new':
          return !(options.isInstalled?.(plugin) ?? false)
        default:
          return false
      }
    },
    zone(plugin, value) {
      return normalizeSearchPart(plugin.zone) === value
    },
    tag(plugin, value) {
      return (plugin.tags || []).some((tag) => normalizeSearchPart(tag).includes(value))
    },
    tags(plugin, value) {
      return (plugin.tags || []).some((tag) => normalizeSearchPart(tag).includes(value))
    },
    author(plugin, value) {
      return normalizeSearchPart(plugin.author?.name).includes(value)
    },
    name(plugin, value) {
      return normalizeSearchPart(plugin.name).includes(value)
    },
    id(plugin, value) {
      return normalizeSearchPart(String(plugin.id)).includes(value)
    },
    slug(plugin, value) {
      return normalizeSearchPart(plugin.slug ?? '').includes(value)
    },
    desc(plugin, value) {
      return normalizeSearchPart(plugin.description).includes(value)
    },
    description(plugin, value) {
      return normalizeSearchPart(plugin.description).includes(value)
    },
    version(plugin, value) {
      return normalizeSearchPart(plugin.version).includes(value)
    },
    // 版本约束：v:>=1.2.0, v:<2, v:=1.0.0（无操作符时做 includes 匹配）
    v(plugin, value) {
      const match = value.match(/^(>=|<=|>|<|=)?(.+)$/)
      if (!match) return normalizeSearchPart(plugin.version).includes(value)
      const op = match[1] || ''
      const target = match[2] || ''
      if (!target) return false
      if (!op) return normalizeSearchPart(plugin.version).includes(target)
      const cmp = compareVersion(plugin.version, target)
      switch (op) {
        case '>=': return cmp >= 0
        case '<=': return cmp <= 0
        case '>': return cmp > 0
        case '<': return cmp < 0
        case '=': return cmp === 0
        default: return false
      }
    },
    has(plugin, value) {
      switch (value) {
        case 'description':
          return !!plugin.description?.trim()
        case 'tags':
          return (plugin.tags?.length || 0) > 0
        case 'repo':
          return !!plugin.github_repo
        case 'download':
          return !!plugin.download_url
        case 'icon':
          return !!plugin.icon_url
        default:
          return false
      }
    },
  }
}

export function useMarketWorkbench(
  pluginsSource: MaybeRefOrGetter<MarketPlugin[]>,
  options: MarketWorkbenchOptions = {},
) {
  const normalized = computed<MarketWorkbenchItem[]>(() =>
    toValue(pluginsSource).map((plugin) => ({
      ...plugin,
      // GridWorkbench 要求 `id: string`；Market 的数字 id 统一转字符串
      id: String(plugin.id),
      rawId: plugin.id,
    })),
  )

  const workbench = useGridWorkbench<MarketWorkbenchItem>(normalized, {
    scope: 'market-workbench',
    groups: [
      { id: 'recommended', predicate: (p) => !!p.is_recommended },
      { id: 'all', predicate: () => true },
    ],
    groupSelection: 'single',
    defaultSelectedGroupIds: ['all'],
    buildSearchIndex: buildMarketSearchIndex,
    qualifierMatchers: buildMarketQualifiers(options),
    defaults: {
      layoutMode: 'compact',
    },
  })

  const recommendedItems = computed(
    () => workbench.filteredByGroup.value.get('recommended') || [],
  )
  const allItems = computed(() => workbench.filteredByGroup.value.get('all') || [])
  const recommendedCount = computed(
    () => workbench.groupCounts.value.get('recommended') || 0,
  )
  const allCount = computed(() => workbench.groupCounts.value.get('all') || 0)

  return {
    items: workbench.items,
    filterText: workbench.filterText,
    useRegex: workbench.useRegex,
    filterMode: workbench.filterMode,
    selectedGroupIds: workbench.selectedGroupIds,
    layoutMode: workbench.layoutMode,
    multiSelectEnabled: workbench.multiSelectEnabled,
    selectedIds: workbench.selectedIds,
    selectedCount: workbench.selectedCount,
    regexError: workbench.regexError,
    groupCounts: workbench.groupCounts,
    filteredItems: workbench.filteredItems,
    filteredByGroup: workbench.filteredByGroup,
    recommendedItems,
    allItems,
    recommendedCount,
    allCount,
    toggleItem: workbench.toggleItem,
    selectAllVisible: workbench.selectAllVisible,
    invertVisibleSelection: workbench.invertVisibleSelection,
    clearSelection: workbench.clearSelection,
    pruneSelection: workbench.pruneSelection,
    toggleMultiSelect: workbench.toggleMultiSelect,
    setMultiSelectEnabled: workbench.setMultiSelectEnabled,
  }
}
