import { computed, ref, toValue, type MaybeRefOrGetter } from 'vue'
import type { PluginMeta } from '@/types/api'

export type PluginWorkbenchLayoutMode = 'list' | 'single' | 'double' | 'compact'
export type PluginWorkbenchFilterMode = 'whitelist' | 'blacklist'
export type PluginWorkbenchGroupType = 'plugin' | 'adapter' | 'extension'

export type PluginWorkbenchItem = PluginMeta & {
  type: PluginWorkbenchGroupType
  enabled?: boolean
  autoStart?: boolean
}

const sharedFilterText = ref('')
const sharedUseRegex = ref(false)
const sharedFilterMode = ref<PluginWorkbenchFilterMode>('whitelist')
const sharedSelectedTypes = ref<PluginWorkbenchGroupType[]>(['plugin', 'adapter', 'extension'])
const sharedLayoutMode = ref<PluginWorkbenchLayoutMode>('double')
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

export function usePluginWorkbench<T extends PluginMeta & { type?: string; enabled?: boolean; autoStart?: boolean }>(
  pluginsSource: MaybeRefOrGetter<T[]>,
) {
  const items = computed<PluginWorkbenchItem[]>(() =>
    toValue(pluginsSource).map((plugin) => ({
      ...plugin,
      type: normalizePluginType(plugin.type),
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
          re.test(plugin.id || '') ||
          re.test(plugin.name || '') ||
          re.test(plugin.description || '') ||
          re.test(plugin.type || '')
        return sharedFilterMode.value === 'blacklist'
          ? visibleByType.filter((plugin) => !matches(plugin))
          : visibleByType.filter(matches)
      } catch {
        return visibleByType
      }
    }

    const lowered = text.toLowerCase()
    const matches = (plugin: PluginWorkbenchItem) =>
      (plugin.id || '').toLowerCase().includes(lowered) ||
      (plugin.name || '').toLowerCase().includes(lowered) ||
      (plugin.description || '').toLowerCase().includes(lowered) ||
      (plugin.type || '').toLowerCase().includes(lowered)

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
    clearSelection,
    pruneSelection,
    setMultiSelectEnabled,
    toggleMultiSelect,
  }
}
