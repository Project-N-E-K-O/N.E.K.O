/**
 * 插件专属的工作台 composable —— 薄包装，复用通用 `useGridWorkbench`。
 *
 * 这里注入插件语义：
 * - groups = plugin / adapter / extension（predicate 定义）
 * - 搜索索引包含 id / name / description / type / version / host / 拼音
 * - qualifierMatchers 支持 is:running / type:adapter / has:entries / host:xxx 等
 *
 * 对外 API 与改造前保持一致，现有调用点（PluginList.vue、usePackageManager）零改动。
 */
import { computed, toValue, type MaybeRefOrGetter, type WritableComputedRef } from 'vue'
import { useI18n } from 'vue-i18n'
import type { PluginMeta } from '@/types/api'
import {
  useGridWorkbench,
  normalizeSearchPart,
  safePinyin,
  type FilterMode,
  type LayoutMode,
  type QualifierMatcher,
} from '@/composables/useGridWorkbench'
import { resolvePluginDisplayText } from '@/utils/pluginDisplay'

export type PluginWorkbenchLayoutMode = LayoutMode
export type PluginWorkbenchFilterMode = FilterMode
export type PluginWorkbenchGroupType = 'plugin' | 'adapter' | 'extension'

export type PluginWorkbenchItem = PluginMeta & {
  type: PluginWorkbenchGroupType
  enabled?: boolean
  autoStart?: boolean
  searchIndex?: string
  displayName?: string
  displayDescription?: string
  displayShortDescription?: string
}

const PLUGIN_GROUPS: readonly PluginWorkbenchGroupType[] = ['plugin', 'adapter', 'extension']

function normalizePluginType(type?: string): PluginWorkbenchGroupType {
  if (type === 'adapter') return 'adapter'
  if (type === 'extension') return 'extension'
  return 'plugin'
}

function hasUi(plugin: PluginWorkbenchItem): boolean {
  return Array.isArray(plugin.list_actions) && plugin.list_actions.some((action) => action.kind === 'ui')
}

function buildPluginSearchIndex(plugin: PluginWorkbenchItem): string {
  const name = plugin.displayName || plugin.name
  const description = plugin.displayDescription || plugin.description
  const shortDescription = plugin.displayShortDescription || plugin.short_description
  const textParts = [
    plugin.id,
    name,
    description,
    shortDescription,
    plugin.type,
    plugin.version,
    plugin.host_plugin_id,
  ]

  const pinyinParts = [name, description, shortDescription].flatMap((value) => {
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

// ─── qualifier matchers ─────────────────────────────────────────────

const pluginQualifiers: Record<string, QualifierMatcher<PluginWorkbenchItem>> = {
  is(plugin, value, ctx) {
    switch (value) {
      case 'running':
      case 'stopped':
      case 'crashed':
      case 'pending':
      case 'injected':
      case 'disabled':
      case 'load_failed':
        return (plugin.status || '').toLowerCase() === value
      case 'enabled':
        return plugin.enabled !== false
      case 'selected':
        return ctx.selectedIds.includes(plugin.id)
      case 'unselected':
        return !ctx.selectedIds.includes(plugin.id)
      case 'manual':
      case 'manual_start':
        return plugin.autoStart === false
      case 'auto':
      case 'auto_start':
        return plugin.autoStart !== false
      case 'plugin':
      case 'adapter':
      case 'extension':
        return plugin.type === value
      case 'ui':
        return hasUi(plugin)
      case 'hosted':
        return !!plugin.host_plugin_id
      case 'standalone':
        return !plugin.host_plugin_id
      default:
        return false
    }
  },
  type(plugin, value) {
    return plugin.type === value
  },
  status(plugin, value) {
    return (plugin.status || '').toLowerCase().includes(value)
  },
  id(plugin, value) {
    return normalizeSearchPart(plugin.id).includes(value)
  },
  name(plugin, value) {
    return normalizeSearchPart(plugin.displayName || plugin.name).includes(value)
  },
  desc(plugin, value) {
    return normalizeSearchPart(plugin.displayDescription || plugin.description).includes(value)
  },
  description(plugin, value) {
    return normalizeSearchPart(plugin.displayDescription || plugin.description).includes(value)
  },
  host(plugin, value) {
    return normalizeSearchPart(plugin.host_plugin_id).includes(value)
  },
  version(plugin, value) {
    return normalizeSearchPart(plugin.version).includes(value)
  },
  entry(plugin, value) {
    return pluginEntryText(plugin).includes(value)
  },
  entries(plugin, value) {
    return pluginEntryText(plugin).includes(value)
  },
  dep(plugin, value) {
    return pluginDependencyText(plugin).includes(value)
  },
  dependency(plugin, value) {
    return pluginDependencyText(plugin).includes(value)
  },
  dependencies(plugin, value) {
    return pluginDependencyText(plugin).includes(value)
  },
  author(plugin, value) {
    return pluginAuthorText(plugin).includes(value)
  },
  sdk(plugin, value) {
    return [
      plugin.sdk_version,
      plugin.sdk_recommended,
      plugin.sdk_supported,
      plugin.sdk_untested,
    ]
      .map(normalizeSearchPart)
      .join('\n')
      .includes(value)
  },
  has(plugin, value) {
    switch (value) {
      case 'description':
        return !!(plugin.displayDescription || plugin.description)?.trim()
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
  },
}

function pluginEntryText(plugin: PluginWorkbenchItem): string {
  return (plugin.entries || [])
    .flatMap((entry) => [entry.id, entry.name, entry.description])
    .map(normalizeSearchPart)
    .join('\n')
}

function pluginDependencyText(plugin: PluginWorkbenchItem): string {
  return (plugin.dependencies || [])
    .flatMap((dep) => [dep.id, dep.entry, dep.custom_event])
    .map(normalizeSearchPart)
    .join('\n')
}

function pluginAuthorText(plugin: PluginWorkbenchItem): string {
  return [plugin.author?.name, plugin.author?.email].map(normalizeSearchPart).join('\n')
}

// ─── 对外导出 ──────────────────────────────────────────────────────

export function usePluginWorkbench<
  T extends PluginMeta & { type?: string; enabled?: boolean; autoStart?: boolean; searchIndex?: string },
>(pluginsSource: MaybeRefOrGetter<T[]>, options?: { scope?: string }) {
  const { locale } = useI18n()
  const normalized = computed<PluginWorkbenchItem[]>(() =>
    toValue(pluginsSource).map((plugin) => {
      const displayText = resolvePluginDisplayText(plugin, locale.value)
      return {
        ...plugin,
        type: normalizePluginType(plugin.type),
        displayName: displayText.name,
        displayDescription: displayText.description,
        displayShortDescription: displayText.shortDescription,
      }
    }),
  )

  const workbench = useGridWorkbench<PluginWorkbenchItem>(normalized, {
    scope: options?.scope || 'plugin-workbench',
    groups: PLUGIN_GROUPS.map((groupId) => ({
      id: groupId,
      predicate: (item) => item.type === groupId,
    })),
    buildSearchIndex: buildPluginSearchIndex,
    qualifierMatchers: pluginQualifiers,
  })

  const filteredPurePlugins = computed(() => workbench.filteredByGroup.value.get('plugin') || [])
  const filteredAdapters = computed(() => workbench.filteredByGroup.value.get('adapter') || [])
  const filteredExtensions = computed(() => workbench.filteredByGroup.value.get('extension') || [])

  const pluginCount = computed(() => workbench.groupCounts.value.get('plugin') || 0)
  const adapterCount = computed(() => workbench.groupCounts.value.get('adapter') || 0)
  const extensionCount = computed(() => workbench.groupCounts.value.get('extension') || 0)

  // 暴露 plugin-typed selectedTypes，屏蔽来自共享 scope 的无效 id。
  const selectedTypes: WritableComputedRef<PluginWorkbenchGroupType[]> = computed({
    get: () =>
      workbench.selectedGroupIds.value.filter((id): id is PluginWorkbenchGroupType =>
        (PLUGIN_GROUPS as readonly string[]).includes(id),
      ),
    set: (value) => {
      workbench.selectedGroupIds.value = [...value]
    },
  })

  return {
    items: workbench.items,
    filterText: workbench.filterText,
    useRegex: workbench.useRegex,
    filterMode: workbench.filterMode,
    selectedTypes,
    layoutMode: workbench.layoutMode,
    selectedPluginIds: workbench.selectedIds,
    selectedCount: workbench.selectedCount,
    multiSelectEnabled: workbench.multiSelectEnabled,
    regexError: workbench.regexError,
    groupCounts: workbench.groupCounts,
    pluginCount,
    adapterCount,
    extensionCount,
    filteredItems: workbench.filteredItems,
    filteredPurePlugins,
    filteredAdapters,
    filteredExtensions,
    isSelected: workbench.isSelected,
    setSelectedPluginIds: workbench.setSelectedIds,
    togglePlugin: workbench.toggleItem,
    selectAllVisible: workbench.selectAllVisible,
    invertVisibleSelection: workbench.invertVisibleSelection,
    clearSelection: workbench.clearSelection,
    pruneSelection: workbench.pruneSelection,
    setMultiSelectEnabled: workbench.setMultiSelectEnabled,
    toggleMultiSelect: workbench.toggleMultiSelect,
  }
}
