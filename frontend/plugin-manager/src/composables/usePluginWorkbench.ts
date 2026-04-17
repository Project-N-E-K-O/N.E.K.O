import { computed, ref, toValue, type MaybeRefOrGetter } from 'vue'
import { pinyin } from 'pinyin-pro'
import type { PluginMeta } from '@/types/api'

export type PluginWorkbenchLayoutMode = 'list' | 'single' | 'double' | 'compact'
export type PluginWorkbenchFilterMode = 'whitelist' | 'blacklist'
export type PluginWorkbenchGroupType = 'plugin' | 'adapter' | 'extension'

export type PluginWorkbenchItem = PluginMeta & {
  type: PluginWorkbenchGroupType
  enabled?: boolean
  autoStart?: boolean
  searchIndex?: string
}

type QueryToken =
  | { kind: 'term'; value: string; negated: boolean }
  | { kind: 'qualifier'; key: string; value: string; negated: boolean }

const sharedFilterText = ref('')
const sharedUseRegex = ref(false)
const sharedFilterMode = ref<PluginWorkbenchFilterMode>('whitelist')
const sharedSelectedTypes = ref<PluginWorkbenchGroupType[]>(['plugin', 'adapter', 'extension'])
const sharedLayoutMode = ref<PluginWorkbenchLayoutMode>('compact')
const sharedSelectedPluginIds = ref<string[]>([])
const sharedMultiSelectEnabled = ref(false)

function normalizePluginType(type?: string): PluginWorkbenchGroupType {
  if (type === 'adapter') return 'adapter'
  if (type === 'extension') return 'extension'
  return 'plugin'
}

function uniqueIds(ids: string[]): string[] {
  return Array.from(new Set(ids.filter((id) => typeof id === 'string' && id)))
}

function isCjkText(value: string): boolean {
  return /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/.test(value)
}

function safePinyin(value: string, pattern: 'pinyin' | 'first'): string {
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

function normalizeSearchPart(value?: string): string {
  return (value || '').trim().toLowerCase()
}

function tokenizeQuery(input: string): QueryToken[] {
  const matches = input.match(/"[^"]+"|\S+/g) || []
  return matches
    .map((rawToken) => {
      const negated = rawToken.startsWith('-')
      const baseToken = negated ? rawToken.slice(1) : rawToken
      const token = baseToken.replace(/^"(.*)"$/, '$1').trim()
      if (!token) {
        return null
      }

      const separatorIndex = token.indexOf(':')
      if (separatorIndex > 0) {
        const key = token.slice(0, separatorIndex).trim().toLowerCase()
        const value = token.slice(separatorIndex + 1).trim().toLowerCase()
        if (key && value) {
          return {
            kind: 'qualifier' as const,
            key,
            value,
            negated,
          }
        }
      }

      return {
        kind: 'term' as const,
        value: token.toLowerCase(),
        negated,
      }
    })
    .filter((token): token is QueryToken => !!token)
}

function hasUi(plugin: PluginWorkbenchItem): boolean {
  return Array.isArray(plugin.list_actions) && plugin.list_actions.some((action) => action.kind === 'ui')
}

function qualifierMatches(plugin: PluginWorkbenchItem, key: string, value: string): boolean {
  const normalizedValue = value.toLowerCase()
  const entryText = (plugin.entries || [])
    .flatMap((entry) => [entry.id, entry.name, entry.description])
    .map(normalizeSearchPart)
    .join('\n')
  const dependencyText = (plugin.dependencies || [])
    .flatMap((dependency) => [dependency.id, dependency.entry, dependency.custom_event])
    .map(normalizeSearchPart)
    .join('\n')
  const authorText = [plugin.author?.name, plugin.author?.email].map(normalizeSearchPart).join('\n')

  switch (key) {
    case 'is': {
      switch (normalizedValue) {
        case 'running':
        case 'stopped':
        case 'crashed':
        case 'pending':
        case 'injected':
        case 'disabled':
        case 'load_failed':
          return (plugin.status || '').toLowerCase() === normalizedValue
        case 'enabled':
          return plugin.enabled !== false
        case 'selected':
          return sharedSelectedPluginIds.value.includes(plugin.id)
        case 'unselected':
          return !sharedSelectedPluginIds.value.includes(plugin.id)
        case 'manual':
        case 'manual_start':
          return plugin.autoStart === false
        case 'auto':
        case 'auto_start':
          return plugin.autoStart !== false
        case 'plugin':
        case 'adapter':
        case 'extension':
          return plugin.type === normalizedValue
        case 'ui':
          return hasUi(plugin)
        case 'hosted':
          return !!plugin.host_plugin_id
        case 'standalone':
          return !plugin.host_plugin_id
        default:
          return false
      }
    }
    case 'type':
      return plugin.type === normalizedValue
    case 'status':
      return (plugin.status || '').toLowerCase().includes(normalizedValue)
    case 'id':
      return normalizeSearchPart(plugin.id).includes(normalizedValue)
    case 'name':
      return normalizeSearchPart(plugin.name).includes(normalizedValue)
    case 'desc':
    case 'description':
      return normalizeSearchPart(plugin.description).includes(normalizedValue)
    case 'host':
      return normalizeSearchPart(plugin.host_plugin_id).includes(normalizedValue)
    case 'version':
      return normalizeSearchPart(plugin.version).includes(normalizedValue)
    case 'entry':
    case 'entries':
      return entryText.includes(normalizedValue)
    case 'dep':
    case 'dependency':
    case 'dependencies':
      return dependencyText.includes(normalizedValue)
    case 'author':
      return authorText.includes(normalizedValue)
    case 'sdk':
      return [
        plugin.sdk_version,
        plugin.sdk_recommended,
        plugin.sdk_supported,
        plugin.sdk_untested,
      ]
        .map(normalizeSearchPart)
        .join('\n')
        .includes(normalizedValue)
    case 'has': {
      switch (normalizedValue) {
        case 'description':
          return !!plugin.description?.trim()
        case 'entries':
        case 'entry':
          return (plugin.entries?.length || 0) > 0
        case 'host':
          return !!plugin.host_plugin_id
        case 'dependencies':
        case 'dependency':
          return (plugin.dependencies?.length || 0) > 0
        case 'schema':
          return !!plugin.input_schema
        case 'actions':
          return (plugin.list_actions?.length || 0) > 0
        case 'ui':
          return hasUi(plugin)
        case 'author':
          return !!plugin.author?.name || !!plugin.author?.email
        default:
          return false
      }
    }
    default:
      return false
  }
}

function matchesAdvancedQuery(plugin: PluginWorkbenchItem, input: string): boolean {
  const tokens = tokenizeQuery(input)
  if (tokens.length === 0) {
    return true
  }

  return tokens.every((token) => {
    const matches = token.kind === 'term'
      ? (plugin.searchIndex || '').includes(token.value)
      : qualifierMatches(plugin, token.key, token.value)
    return token.negated ? !matches : matches
  })
}

function buildSearchIndex(plugin: PluginMeta & { type?: string }): string {
  const textParts = [
    plugin.id,
    plugin.name,
    plugin.description,
    plugin.type,
    plugin.version,
    plugin.host_plugin_id,
  ]

  const pinyinParts = [plugin.name, plugin.description].flatMap((value) => {
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

export function usePluginWorkbench<
  T extends PluginMeta & { type?: string; enabled?: boolean; autoStart?: boolean; searchIndex?: string }
>(
  pluginsSource: MaybeRefOrGetter<T[]>,
) {
  const items = computed<PluginWorkbenchItem[]>(() =>
    toValue(pluginsSource).map((plugin) => ({
      ...plugin,
      type: normalizePluginType(plugin.type),
      searchIndex: plugin.searchIndex || buildSearchIndex(plugin),
    })),
  )

  const availableIdSet = computed(() => new Set(items.value.map((plugin) => plugin.id)))
  const regexError = computed(() => {
    if (!sharedUseRegex.value || !sharedFilterText.value.trim()) {
      return false
    }
    try {
      new RegExp(sharedFilterText.value.trim(), 'i')
      return false
    } catch {
      return true
    }
  })

  const filteredItems = computed(() => {
    const text = sharedFilterText.value.trim()
    const visibleByType = items.value.filter((plugin) => sharedSelectedTypes.value.includes(plugin.type))
    if (!text) {
      return visibleByType
    }

    if (sharedUseRegex.value) {
      try {
        const re = new RegExp(text, 'i')
        const matches = (plugin: PluginWorkbenchItem) =>
          re.test(plugin.searchIndex || '')
        return sharedFilterMode.value === 'blacklist'
          ? visibleByType.filter((plugin) => !matches(plugin))
          : visibleByType.filter(matches)
      } catch {
        return visibleByType
      }
    }

    const lowered = text.toLowerCase()
    const matches = (plugin: PluginWorkbenchItem) => matchesAdvancedQuery(plugin, lowered)

    return sharedFilterMode.value === 'blacklist'
      ? visibleByType.filter((plugin) => !matches(plugin))
      : visibleByType.filter(matches)
  })

  const pluginCount = computed(() => items.value.filter((plugin) => plugin.type === 'plugin').length)
  const adapterCount = computed(() => items.value.filter((plugin) => plugin.type === 'adapter').length)
  const extensionCount = computed(() => items.value.filter((plugin) => plugin.type === 'extension').length)

  const filteredPurePlugins = computed(() => filteredItems.value.filter((plugin) => plugin.type === 'plugin'))
  const filteredAdapters = computed(() => filteredItems.value.filter((plugin) => plugin.type === 'adapter'))
  const filteredExtensions = computed(() => filteredItems.value.filter((plugin) => plugin.type === 'extension'))
  const selectedPluginIds = computed(() =>
    sharedSelectedPluginIds.value.filter((pluginId) => availableIdSet.value.has(pluginId)),
  )
  const selectedCount = computed(() => selectedPluginIds.value.length)

  function isSelected(pluginId: string): boolean {
    return sharedSelectedPluginIds.value.includes(pluginId)
  }

  function setSelectedPluginIds(ids: string[]) {
    sharedSelectedPluginIds.value = uniqueIds(ids)
  }

  function togglePlugin(pluginId: string) {
    if (isSelected(pluginId)) {
      sharedSelectedPluginIds.value = sharedSelectedPluginIds.value.filter((item) => item !== pluginId)
      return
    }
    sharedSelectedPluginIds.value = [...sharedSelectedPluginIds.value, pluginId]
  }

  function selectAllVisible() {
    sharedSelectedPluginIds.value = uniqueIds([
      ...sharedSelectedPluginIds.value,
      ...filteredItems.value.map((plugin) => plugin.id),
    ])
  }

  function invertVisibleSelection() {
    const visibleIds = filteredItems.value.map((plugin) => plugin.id)
    const visibleIdSet = new Set(visibleIds)
    const preservedHiddenIds = sharedSelectedPluginIds.value.filter((pluginId) => !visibleIdSet.has(pluginId))
    const invertedVisibleIds = visibleIds.filter((pluginId) => !sharedSelectedPluginIds.value.includes(pluginId))
    sharedSelectedPluginIds.value = uniqueIds([...preservedHiddenIds, ...invertedVisibleIds])
  }

  function clearSelection() {
    sharedSelectedPluginIds.value = []
  }

  function pruneSelection(validIds: string[]) {
    const validIdSet = new Set(validIds)
    sharedSelectedPluginIds.value = sharedSelectedPluginIds.value.filter((pluginId) => validIdSet.has(pluginId))
  }

  function setMultiSelectEnabled(value: boolean) {
    sharedMultiSelectEnabled.value = value
  }

  function toggleMultiSelect() {
    sharedMultiSelectEnabled.value = !sharedMultiSelectEnabled.value
  }

  return {
    items,
    filterText: sharedFilterText,
    useRegex: sharedUseRegex,
    filterMode: sharedFilterMode,
    selectedTypes: sharedSelectedTypes,
    layoutMode: sharedLayoutMode,
    selectedPluginIds,
    selectedCount,
    multiSelectEnabled: sharedMultiSelectEnabled,
    regexError,
    pluginCount,
    adapterCount,
    extensionCount,
    filteredItems,
    filteredPurePlugins,
    filteredAdapters,
    filteredExtensions,
    isSelected,
    setSelectedPluginIds,
    togglePlugin,
    selectAllVisible,
    invertVisibleSelection,
    clearSelection,
    pruneSelection,
    setMultiSelectEnabled,
    toggleMultiSelect,
  }
}
