/**
 * 泛型网格工作台（过滤 + 分组 + 选择 + 布局）
 *
 * 提供与业务无关的"过滤栏 + 网格"状态管理，支撑：
 * - 简单文本搜索 / 正则搜索 / 白名单-黑名单模式
 * - 进阶 `key:value` 限定词（例如 `is:running -author:foo`），由调用方注入 matcher
 * - 分组可见性切换（支持重叠 predicate，item 可同时属于多个分组）
 * - 多选、全选、反选、按可见项裁剪
 * - 布局模式（list/single/double/compact）
 *
 * 状态按 `scope` 在模块级缓存，同 scope 共享，不同 scope 隔离。业务相关语义全部
 * 通过 predicate / qualifier / spec 注入，composable 本身不知业务。
 */
import { computed, ref, toValue, type MaybeRefOrGetter, type Ref } from 'vue'
import { pinyin } from 'pinyin-pro'

export type LayoutMode = 'list' | 'single' | 'double' | 'compact'
export type FilterMode = 'whitelist' | 'blacklist'
export type GroupSelectionMode = 'single' | 'multiple'

export interface GridWorkbenchItemBase {
  id: string
  /** 预构建的搜索索引；若未提供，useGridWorkbench 会用 config.buildSearchIndex 生成 */
  searchIndex?: string
}

export type QueryToken =
  | { kind: 'term'; value: string; negated: boolean }
  | { kind: 'qualifier'; key: string; value: string; negated: boolean }

export interface QualifierContext {
  readonly selectedIds: readonly string[]
}

export type QualifierMatcher<T> = (
  item: T,
  value: string,
  context: QualifierContext,
) => boolean

export interface GridWorkbenchGroupSpec<T> {
  id: string
  predicate: (item: T) => boolean
}

export interface GridWorkbenchConfig<T extends GridWorkbenchItemBase> {
  /** 状态作用域 key；同 scope 的调用共享状态。 */
  scope: string
  /** 分组声明（predicate 定义成员归属；可重叠）。 */
  groups: GridWorkbenchGroupSpec<T>[]
  /** 分组选择模式：single = 单选（radio），multiple = 多选（checkbox，默认）。 */
  groupSelection?: GroupSelectionMode
  /** 默认选中分组 id 列表（默认全选）。 */
  defaultSelectedGroupIds?: readonly string[]
  /** 构建搜索索引（用于正则搜索和默认 term 匹配）。 */
  buildSearchIndex?: (item: T) => string
  /** `key:value` 限定词匹配器。未注册的 key 默认不匹配。 */
  qualifierMatchers?: Record<string, QualifierMatcher<T>>
  /** 初始默认值。 */
  defaults?: {
    filterText?: string
    useRegex?: boolean
    filterMode?: FilterMode
    layoutMode?: LayoutMode
  }
}

interface ScopedState {
  filterText: Ref<string>
  useRegex: Ref<boolean>
  filterMode: Ref<FilterMode>
  selectedGroupIds: Ref<string[]>
  layoutMode: Ref<LayoutMode>
  selectedIds: Ref<string[]>
  multiSelectEnabled: Ref<boolean>
}

const _scopes = new Map<string, ScopedState>()

function getOrCreateScope(
  scope: string,
  initial: {
    filterText: string
    useRegex: boolean
    filterMode: FilterMode
    selectedGroupIds: string[]
    layoutMode: LayoutMode
  },
): ScopedState {
  const cached = _scopes.get(scope)
  if (cached) return cached
  const created: ScopedState = {
    filterText: ref(initial.filterText),
    useRegex: ref(initial.useRegex),
    filterMode: ref(initial.filterMode),
    selectedGroupIds: ref([...initial.selectedGroupIds]),
    layoutMode: ref(initial.layoutMode),
    selectedIds: ref([]),
    multiSelectEnabled: ref(false),
  }
  _scopes.set(scope, created)
  return created
}

// ─── 纯工具函数（导出供业务层复用）─────────────────────────────────

function isCjkText(value: string): boolean {
  return /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/.test(value)
}

export function safePinyin(value: string, pattern: 'pinyin' | 'first'): string {
  if (!value.trim() || !isCjkText(value)) {
    return ''
  }
  try {
    return pinyin(value, {
      toneType: 'none',
      type: 'string',
      pattern,
      nonZh: 'consecutive',
      v: true,
      traditional: true,
    }).trim()
  } catch {
    return ''
  }
}

export function normalizeSearchPart(value?: string | null): string {
  return (value || '').trim().toLowerCase()
}

export function tokenizeQuery(input: string): QueryToken[] {
  const matches = input.match(/"[^"]+"|\S+/g) || []
  return matches
    .map((rawToken) => {
      const negated = rawToken.startsWith('-')
      const baseToken = negated ? rawToken.slice(1) : rawToken
      const token = baseToken.replace(/^"(.*)"$/, '$1').trim()
      if (!token) return null
      const separatorIndex = token.indexOf(':')
      if (separatorIndex > 0) {
        const key = token.slice(0, separatorIndex).trim().toLowerCase()
        const value = token.slice(separatorIndex + 1).trim().toLowerCase()
        if (key && value) {
          return { kind: 'qualifier' as const, key, value, negated }
        }
      }
      return { kind: 'term' as const, value: token.toLowerCase(), negated }
    })
    .filter((token): token is QueryToken => !!token)
}

function uniqueIds(ids: readonly string[]): string[] {
  return Array.from(new Set(ids.filter((id) => typeof id === 'string' && id)))
}

// ─── 主 composable ────────────────────────────────────────────────

export function useGridWorkbench<T extends GridWorkbenchItemBase>(
  source: MaybeRefOrGetter<T[]>,
  config: GridWorkbenchConfig<T>,
) {
  const defaults = {
    filterText: config.defaults?.filterText ?? '',
    useRegex: config.defaults?.useRegex ?? false,
    filterMode: config.defaults?.filterMode ?? ('whitelist' as FilterMode),
    layoutMode: config.defaults?.layoutMode ?? ('compact' as LayoutMode),
    selectedGroupIds: [
      ...(config.defaultSelectedGroupIds ?? config.groups.map((g) => g.id)),
    ],
  }

  const state = getOrCreateScope(config.scope, defaults)
  const qualifierMatchers = config.qualifierMatchers ?? {}
  const groupSelection: GroupSelectionMode = config.groupSelection ?? 'multiple'

  const groupMap = computed(() => {
    const map = new Map<string, GridWorkbenchGroupSpec<T>>()
    for (const group of config.groups) map.set(group.id, group)
    return map
  })

  const items = computed<T[]>(() => {
    const raw = toValue(source)
    const builder = config.buildSearchIndex
    if (!builder) return raw
    return raw.map((item) => ({
      ...item,
      searchIndex: item.searchIndex || builder(item),
    })) as T[]
  })

  const availableIdSet = computed(() => new Set(items.value.map((item) => item.id)))

  const regexError = computed(() => {
    if (!state.useRegex.value || !state.filterText.value.trim()) return false
    try {
      new RegExp(state.filterText.value.trim(), 'i')
      return false
    } catch {
      return true
    }
  })

  function matchQualifier(item: T, key: string, value: string): boolean {
    const matcher = qualifierMatchers[key]
    if (!matcher) return false
    return matcher(item, value, { selectedIds: state.selectedIds.value })
  }

  function matchesAdvancedQuery(item: T, input: string): boolean {
    const tokens = tokenizeQuery(input)
    if (tokens.length === 0) return true
    return tokens.every((token) => {
      const matches = token.kind === 'term'
        ? (item.searchIndex || '').includes(token.value)
        : matchQualifier(item, token.key, token.value)
      return token.negated ? !matches : matches
    })
  }

  function belongsToAnySelectedGroup(item: T): boolean {
    const selected = state.selectedGroupIds.value
    if (selected.length === 0) return false
    for (const id of selected) {
      const group = groupMap.value.get(id)
      if (group?.predicate(item)) return true
    }
    return false
  }

  const filteredItems = computed(() => {
    const text = state.filterText.value.trim()
    const visibleByGroup = items.value.filter((item) => belongsToAnySelectedGroup(item))
    if (!text) return visibleByGroup

    if (state.useRegex.value) {
      try {
        const re = new RegExp(text, 'i')
        const matches = (item: T) => re.test(item.searchIndex || '')
        return state.filterMode.value === 'blacklist'
          ? visibleByGroup.filter((item) => !matches(item))
          : visibleByGroup.filter(matches)
      } catch {
        return visibleByGroup
      }
    }

    const lowered = text.toLowerCase()
    const matches = (item: T) => matchesAdvancedQuery(item, lowered)
    return state.filterMode.value === 'blacklist'
      ? visibleByGroup.filter((item) => !matches(item))
      : visibleByGroup.filter(matches)
  })

  // 每个分组的总数（不受 filterText / selectedGroupIds 影响，用于显示徽标）
  const groupCounts = computed(() => {
    const counts = new Map<string, number>()
    for (const group of config.groups) {
      let count = 0
      for (const item of items.value) {
        if (group.predicate(item)) count++
      }
      counts.set(group.id, count)
    }
    return counts
  })

  // 按分组聚合已过滤的 items（用于分段渲染）
  const filteredByGroup = computed(() => {
    const map = new Map<string, T[]>()
    for (const group of config.groups) map.set(group.id, [])
    for (const item of filteredItems.value) {
      for (const group of config.groups) {
        if (group.predicate(item)) map.get(group.id)!.push(item)
      }
    }
    return map
  })

  const selectedIds = computed(() =>
    state.selectedIds.value.filter((id) => availableIdSet.value.has(id)),
  )
  const selectedCount = computed(() => selectedIds.value.length)

  function isSelected(id: string): boolean {
    return state.selectedIds.value.includes(id)
  }

  function setSelectedIds(ids: readonly string[]) {
    state.selectedIds.value = uniqueIds(ids)
  }

  function toggleItem(id: string) {
    if (isSelected(id)) {
      state.selectedIds.value = state.selectedIds.value.filter((x) => x !== id)
      return
    }
    state.selectedIds.value = [...state.selectedIds.value, id]
  }

  function selectAllVisible() {
    state.selectedIds.value = uniqueIds([
      ...state.selectedIds.value,
      ...filteredItems.value.map((item) => item.id),
    ])
  }

  function invertVisibleSelection() {
    const visibleIds = filteredItems.value.map((item) => item.id)
    const visibleSet = new Set(visibleIds)
    const preservedHidden = state.selectedIds.value.filter((id) => !visibleSet.has(id))
    const invertedVisible = visibleIds.filter((id) => !state.selectedIds.value.includes(id))
    state.selectedIds.value = uniqueIds([...preservedHidden, ...invertedVisible])
  }

  function clearSelection() {
    state.selectedIds.value = []
  }

  function pruneSelection(validIds: readonly string[]) {
    const validSet = new Set(validIds)
    state.selectedIds.value = state.selectedIds.value.filter((id) => validSet.has(id))
  }

  function toggleMultiSelect() {
    state.multiSelectEnabled.value = !state.multiSelectEnabled.value
  }

  function setMultiSelectEnabled(value: boolean) {
    state.multiSelectEnabled.value = value
  }

  return {
    items,
    filterText: state.filterText,
    useRegex: state.useRegex,
    filterMode: state.filterMode,
    selectedGroupIds: state.selectedGroupIds,
    layoutMode: state.layoutMode,
    multiSelectEnabled: state.multiSelectEnabled,
    selectedIds,
    selectedCount,
    regexError,
    groupCounts,
    filteredItems,
    filteredByGroup,
    groupSelection,
    isSelected,
    setSelectedIds,
    toggleItem,
    selectAllVisible,
    invertVisibleSelection,
    clearSelection,
    pruneSelection,
    toggleMultiSelect,
    setMultiSelectEnabled,
  }
}
