import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  analyzePluginBundle,
  getPluginCliPackages,
  getPluginCliPlugins,
  inspectPluginPackage,
  packPluginCli,
  unpackPluginPackage,
  verifyPluginPackage,
  type PluginCliAnalyzeResponse,
  type PluginCliInspectResponse,
  type PluginCliLocalPackageItem,
  type PluginCliPackMode,
  type PluginCliPackRequest,
  type PluginCliUnpackRequest,
} from '@/api/pluginCli'
import { usePluginStore } from '@/stores/plugin'
import type { PluginMeta } from '@/types/api'

export type LayoutMode = 'list' | 'single' | 'double' | 'compact'
export type PackMode = PluginCliPackMode
export type PluginGroupType = 'plugin' | 'adapter' | 'extension'

export type SelectablePlugin = PluginMeta & {
  type: PluginGroupType
  enabled?: boolean
  autoStart?: boolean
}

export function usePackageManager() {
  const pluginStore = usePluginStore()

  const activeTab = ref('pack')
  const layoutMode = ref<LayoutMode>('double')
  const packMode = ref<PackMode>('selected')
  const pluginFilter = ref('')
  const localPluginIds = ref<string[]>([])
  const selectedPluginIds = ref<string[]>([])
  const selectedTypes = ref<PluginGroupType[]>(['plugin', 'adapter', 'extension'])
  const pluginsLoading = ref(false)
  const packagesLoading = ref(false)
  const localPackages = ref<PluginCliLocalPackageItem[]>([])
  const targetDir = ref('')
  const packageFilterType = ref<'all' | 'plugin' | 'bundle'>('all')

  const packing = ref(false)
  const inspecting = ref(false)
  const verifying = ref(false)
  const unpacking = ref(false)
  const analyzing = ref(false)

  const resultKind = ref('')
  const resultText = ref('')
  const resultData = ref<Record<string, any> | null>(null)
  const inspectResult = ref<PluginCliInspectResponse | null>(null)

  const packForm = ref<PluginCliPackRequest>({
    mode: 'selected',
    plugin: '',
    plugins: [],
    target_dir: '',
    keep_staging: false,
    bundle_id: '',
    package_name: '',
    package_description: '',
    version: '',
  })

  const packageRef = ref({ package: '' })

  const unpackForm = ref<PluginCliUnpackRequest>({
    package: '',
    plugins_root: '',
    profiles_root: '',
    on_conflict: 'rename',
  })

  const analyzeForm = ref({
    plugins: [] as string[],
    current_sdk_version: '',
  })

  const selectablePlugins = computed<SelectablePlugin[]>(() => {
    const metaById = new Map(
      pluginStore.pluginsWithStatus.map((plugin) => [
        plugin.id,
        {
          id: plugin.id,
          name: plugin.name || plugin.id,
          description: plugin.description || '',
          version: plugin.version || '0.0.0',
          type: normalizePluginType(plugin.type),
          status: plugin.status,
          host_plugin_id: plugin.host_plugin_id,
          entries: plugin.entries || [],
          runtime_enabled: plugin.runtime_enabled,
          runtime_auto_start: plugin.runtime_auto_start,
          enabled: plugin.enabled,
          autoStart: plugin.autoStart,
        } satisfies SelectablePlugin,
      ])
    )

    return localPluginIds.value.map((pluginId) => {
      return (
        metaById.get(pluginId) ?? {
          id: pluginId,
          name: pluginId,
          description: '',
          version: '0.0.0',
          type: 'plugin',
          entries: [],
        }
      )
    })
  })

  const filteredPlugins = computed(() => {
    const keyword = pluginFilter.value.trim().toLowerCase()
    return selectablePlugins.value.filter((plugin) => {
      if (!selectedTypes.value.includes(plugin.type)) {
        return false
      }
      if (!keyword) {
        return true
      }
      return (
        plugin.id.toLowerCase().includes(keyword) ||
        plugin.name.toLowerCase().includes(keyword) ||
        plugin.description.toLowerCase().includes(keyword) ||
        plugin.type.toLowerCase().includes(keyword)
      )
    })
  })

  const pluginCount = computed(() => selectablePlugins.value.filter((plugin) => plugin.type === 'plugin').length)
  const adapterCount = computed(() => selectablePlugins.value.filter((plugin) => plugin.type === 'adapter').length)
  const extensionCount = computed(() => selectablePlugins.value.filter((plugin) => plugin.type === 'extension').length)

  const filteredPurePlugins = computed(() => filteredPlugins.value.filter((plugin) => plugin.type === 'plugin'))
  const filteredAdapters = computed(() => filteredPlugins.value.filter((plugin) => plugin.type === 'adapter'))
  const filteredExtensions = computed(() => filteredPlugins.value.filter((plugin) => plugin.type === 'extension'))

  const resolvedPackTargets = computed(() => {
    if (packMode.value === 'all') {
      return selectablePlugins.value.map((plugin) => plugin.id)
    }
    if (packMode.value === 'bundle') {
      return selectedPluginIds.value
    }
    if (packMode.value === 'single') {
      return packForm.value.plugin ? [packForm.value.plugin] : []
    }
    return selectedPluginIds.value
  })

  const filteredLocalPackages = computed(() => {
    if (packageFilterType.value === 'all') {
      return localPackages.value
    }
    return localPackages.value.filter((pkg) => inferPackageType(pkg) === packageFilterType.value)
  })

  const primaryPackResult = computed<Record<string, any> | null>(() => {
    const data = resultData.value
    if (!data || resultKind.value !== 'pack') return null
    const packed = Array.isArray(data.packed) ? data.packed : []
    if (packed.length === 1) {
      return packed[0] as Record<string, any>
    }
    return null
  })

  const summaryMetrics = computed(() => {
    const data = resultData.value
    if (!data) return []

    if (resultKind.value === 'pack') {
      const primaryPacked = primaryPackResult.value
      return [
        {
          label: '类型',
          value: primaryPacked?.package_type === 'bundle' ? '整合包' : '插件包',
        },
        { label: '成功', value: String(data.packed_count ?? 0) },
        { label: '失败', value: String(data.failed_count ?? 0) },
        {
          label: primaryPacked?.package_type === 'bundle' ? '包含插件' : '状态',
          value: primaryPacked?.package_type === 'bundle'
            ? String(primaryPacked?.plugin_ids?.length ?? 0)
            : data.ok ? '完成' : '部分失败',
        },
      ]
    }

    if (resultKind.value === 'inspect' || resultKind.value === 'verify') {
      return [
        { label: '插件数', value: String(data.plugin_count ?? 0) },
        { label: 'Profiles', value: String(data.profile_count ?? 0) },
        { label: 'Hash', value: formatHashStatus(data.payload_hash_verified) },
      ]
    }

    if (resultKind.value === 'unpack') {
      return [
        { label: '已处理插件', value: String(data.unpacked_plugin_count ?? 0) },
        { label: '冲突策略', value: String(data.conflict_strategy ?? '-') },
        { label: 'Hash', value: formatHashStatus(data.payload_hash_verified) },
      ]
    }

    if (resultKind.value === 'analyze') {
      return [
        { label: '插件数', value: String(data.plugin_count ?? 0) },
        { label: '共同依赖', value: String(data.common_dependencies?.length ?? 0) },
        { label: '共享依赖', value: String(data.shared_dependencies?.length ?? 0) },
      ]
    }

    return []
  })

  const summaryHighlights = computed(() => {
    const data = resultData.value
    if (!data) return []

    if (resultKind.value === 'pack') {
      const primaryPacked = primaryPackResult.value
      const firstPacked = data.packed?.[0]
      const latestPacked = data.packed?.[data.packed?.length - 1]
      if (primaryPacked?.package_type === 'bundle') {
        return [
          primaryPacked?.plugin_id ? { label: '整合包 ID', value: primaryPacked.plugin_id } : null,
          primaryPacked?.package_name ? { label: '整合包名称', value: primaryPacked.package_name } : null,
          primaryPacked?.version ? { label: '整合包版本', value: primaryPacked.version } : null,
          latestPacked?.package_path ? { label: '输出路径', value: latestPacked.package_path } : null,
        ].filter(Boolean) as Array<{ label: string; value: string }>
      }
      return [
        firstPacked?.plugin_id ? { label: '首个插件', value: firstPacked.plugin_id } : null,
        latestPacked?.package_path ? { label: '最新包路径', value: latestPacked.package_path } : null,
      ].filter(Boolean) as Array<{ label: string; value: string }>
    }

    if (resultKind.value === 'inspect' || resultKind.value === 'verify') {
      return [
        data.package_id ? { label: '包 ID', value: data.package_id } : null,
        data.package_type ? { label: '包类型', value: data.package_type } : null,
        data.version ? { label: '版本', value: data.version } : null,
      ].filter(Boolean) as Array<{ label: string; value: string }>
    }

    if (resultKind.value === 'unpack') {
      return [
        data.package_id ? { label: '包 ID', value: data.package_id } : null,
        data.plugins_root ? { label: '插件目录', value: data.plugins_root } : null,
        data.profile_dir ? { label: 'Profiles 目录', value: data.profile_dir } : null,
      ].filter(Boolean) as Array<{ label: string; value: string }>
    }

    if (resultKind.value === 'analyze') {
      const sdkSupported = data.sdk_supported_analysis
      const sdkRecommended = data.sdk_recommended_analysis
      return [
        sdkSupported?.current_sdk_version
          ? {
              label: '当前 SDK 支持',
              value: sdkSupported.current_sdk_supported_by_all ? `${sdkSupported.current_sdk_version} 全部支持` : `${sdkSupported.current_sdk_version} 存在不兼容`,
            }
          : null,
        sdkRecommended?.matching_versions?.length
          ? { label: '推荐交集', value: sdkRecommended.matching_versions.join(', ') }
          : null,
      ].filter(Boolean) as Array<{ label: string; value: string }>
    }

    return []
  })

  const summaryListItems = computed(() => {
    const data = resultData.value
    if (!data) return []

    if (resultKind.value === 'pack') {
      const primaryPacked = primaryPackResult.value
      if (primaryPacked?.package_type === 'bundle') {
        return (primaryPacked.plugin_ids ?? []).map((pluginId: string) => `plugin:${pluginId}`)
      }
      return (data.packed ?? []).map((item: Record<string, any>) => `${item.plugin_id} -> ${item.package_path}`)
    }

    if (resultKind.value === 'inspect' || resultKind.value === 'verify') {
      return [
        ...(data.plugins ?? []).map((item: Record<string, any>) => item.plugin_id),
        ...(data.profile_names ?? []).map((name: string) => `profile:${name}`),
      ]
    }

    if (resultKind.value === 'unpack') {
      return (data.unpacked_plugins ?? []).map((item: Record<string, any>) => {
        const suffix = item.renamed ? ' (renamed)' : ''
        return `${item.target_plugin_id}${suffix}`
      })
    }

    if (resultKind.value === 'analyze') {
      return (data.common_dependencies ?? []).map((item: Record<string, any>) => `${item.name} (${item.plugin_count})`)
    }

    return []
  })

  const summaryWarnings = computed(() => {
    const data = resultData.value
    if (!data) return []

    if (resultKind.value === 'pack') {
      const warnings = (data.failed ?? []).map((item: Record<string, any>) => `${item.plugin}: ${item.error}`)
      const primaryPacked = primaryPackResult.value
      if (primaryPacked?.package_type === 'bundle' && (primaryPacked.plugin_ids?.length ?? 0) < 2) {
        warnings.push('整合包通常应至少包含两个插件')
      }
      return warnings
    }

    if (resultKind.value === 'verify' && data.ok === false) {
      return ['包未通过 hash 校验，请不要直接导入运行环境']
    }

    if (resultKind.value === 'inspect' && data.payload_hash_verified === false) {
      return ['当前包 hash 校验失败，内容可能已被修改']
    }

    if (resultKind.value === 'analyze') {
      const warnings: string[] = []
      if (data.sdk_supported_analysis && data.sdk_supported_analysis.current_sdk_supported_by_all === false) {
        warnings.push('当前 SDK 版本不被所有插件共同支持')
      }
      if ((data.shared_dependencies?.length ?? 0) > 0) {
        warnings.push(`检测到 ${data.shared_dependencies.length} 个共享依赖，整合时需要重点检查版本约束`)
      }
      return warnings
    }

    return []
  })

  function normalizePluginType(type?: string): PluginGroupType {
    if (type === 'adapter') return 'adapter'
    if (type === 'extension') return 'extension'
    return 'plugin'
  }

  function setResult(kind: string, payload: unknown) {
    resultKind.value = kind
    resultData.value = payload && typeof payload === 'object' ? (payload as Record<string, any>) : null
    resultText.value = JSON.stringify(payload, null, 2)
  }

  function formatHashStatus(value: boolean | null | undefined): string {
    if (value === true) return '通过'
    if (value === false) return '失败'
    return '未校验'
  }

  function togglePlugin(pluginId: string) {
    if (selectedPluginIds.value.includes(pluginId)) {
      selectedPluginIds.value = selectedPluginIds.value.filter((item) => item !== pluginId)
      return
    }
    selectedPluginIds.value = [...selectedPluginIds.value, pluginId]
  }

  function selectAllVisible() {
    selectedPluginIds.value = Array.from(
      new Set([...selectedPluginIds.value, ...filteredPlugins.value.map((plugin) => plugin.id)])
    )
  }

  function clearSelection() {
    selectedPluginIds.value = []
  }

  async function refreshPluginSources() {
    pluginsLoading.value = true
    try {
      await pluginStore.fetchPlugins()
      const response = await getPluginCliPlugins()
      localPluginIds.value = response.plugins
      if (selectedPluginIds.value.length === 0) {
        selectedPluginIds.value = response.plugins.slice(0, 1)
      } else {
        selectedPluginIds.value = selectedPluginIds.value.filter((pluginId) => response.plugins.includes(pluginId))
      }
    } catch (error) {
      console.error('Failed to refresh plugin sources:', error)
    } finally {
      pluginsLoading.value = false
    }
  }

  async function refreshPackageSources() {
    packagesLoading.value = true
    try {
      const response = await getPluginCliPackages()
      localPackages.value = response.packages
      targetDir.value = response.target_dir
    } catch (error) {
      console.error('Failed to refresh package sources:', error)
    } finally {
      packagesLoading.value = false
    }
  }

  function applyPackageRef(packageValue: string) {
    packageRef.value.package = packageValue
    unpackForm.value.package = packageValue
  }

  function selectPackage(pkg: PluginCliLocalPackageItem) {
    applyPackageRef(pkg.path)
  }

  function focusPackageResult(packageValue: string) {
    applyPackageRef(packageValue)
    activeTab.value = 'inspect'
  }

  function inferPackageType(pkg: PluginCliLocalPackageItem): 'plugin' | 'bundle' {
    return pkg.name.endsWith('.neko-bundle') ? 'bundle' : 'plugin'
  }

  async function inspectSelectedPackage(pkg: PluginCliLocalPackageItem) {
    selectPackage(pkg)
    activeTab.value = 'inspect'
    await handleInspect()
  }

  async function verifySelectedPackage(pkg: PluginCliLocalPackageItem) {
    selectPackage(pkg)
    activeTab.value = 'inspect'
    await handleVerify()
  }

  function prepareUnpackPackage(pkg: PluginCliLocalPackageItem) {
    selectPackage(pkg)
    activeTab.value = 'unpack'
  }

  async function handlePack() {
    const targets = resolvedPackTargets.value
    if (targets.length === 0) {
      ElMessage.warning('请先选择要打包的插件')
      return
    }

    packing.value = true
    inspectResult.value = null

    try {
      if (packMode.value === 'bundle') {
        if (targets.length < 2) {
          ElMessage.warning('整合包至少需要选择两个插件')
          return
        }
        const response = await packPluginCli({
          mode: 'bundle',
          plugins: targets,
          bundle_id: packForm.value.bundle_id?.trim() || undefined,
          package_name: packForm.value.package_name?.trim() || undefined,
          package_description: packForm.value.package_description?.trim() || undefined,
          version: packForm.value.version?.trim() || undefined,
          target_dir: packForm.value.target_dir || undefined,
          keep_staging: !!packForm.value.keep_staging,
        })
        setResult('pack', response)
        await refreshPackageSources()
        const latestPacked = response.packed[response.packed.length - 1]
        if (latestPacked?.package_path) {
          focusPackageResult(latestPacked.package_path)
        }
        ElMessage.success('整合包打包完成')
        return
      }

      if (packMode.value === 'all') {
        const response = await packPluginCli({
          mode: 'all',
          target_dir: packForm.value.target_dir || undefined,
          keep_staging: !!packForm.value.keep_staging,
        })
        setResult('pack', response)
        await refreshPackageSources()
        const latestPacked = response.packed[response.packed.length - 1]
        if (latestPacked?.package_path) {
          focusPackageResult(latestPacked.package_path)
        }
        ElMessage.success(`打包完成，成功 ${response.packed_count} 个`)
        return
      }

      const packed: unknown[] = []
      const failed: Array<{ plugin: string; error: string }> = []

      for (const pluginId of targets) {
        try {
          const response = await packPluginCli({
            mode: 'single',
            plugin: pluginId,
            target_dir: packForm.value.target_dir || undefined,
            keep_staging: !!packForm.value.keep_staging,
          })
          packed.push(...response.packed)
          failed.push(...response.failed)
        } catch (error) {
          failed.push({ plugin: pluginId, error: error instanceof Error ? error.message : String(error) })
        }
      }

      const summary = {
        packed,
        packed_count: packed.length,
        failed,
        failed_count: failed.length,
        ok: failed.length === 0,
      }
      setResult('pack', summary)
      await refreshPackageSources()
      const latestPacked = packed[packed.length - 1] as { package_path?: string } | undefined
      if (latestPacked?.package_path) {
        focusPackageResult(latestPacked.package_path)
      }
      ElMessage.success(`打包完成，成功 ${packed.length} 个`)
    } finally {
      packing.value = false
    }
  }

  async function handleInspect() {
    if (!packageRef.value.package.trim()) {
      ElMessage.warning('请先输入包路径')
      return
    }
    inspecting.value = true
    try {
      const response = await inspectPluginPackage({ package: packageRef.value.package.trim() })
      inspectResult.value = response
      setResult('inspect', response)
      ElMessage.success('包检查完成')
    } finally {
      inspecting.value = false
    }
  }

  async function handleVerify() {
    if (!packageRef.value.package.trim()) {
      ElMessage.warning('请先输入包路径')
      return
    }
    verifying.value = true
    try {
      const response = await verifyPluginPackage({ package: packageRef.value.package.trim() })
      inspectResult.value = response
      setResult('verify', response)
      ElMessage[response.ok ? 'success' : 'warning'](response.ok ? '包校验通过' : '包未通过校验')
    } finally {
      verifying.value = false
    }
  }

  async function handleUnpack() {
    if (!unpackForm.value.package?.trim()) {
      ElMessage.warning('请先输入包路径')
      return
    }
    unpacking.value = true
    inspectResult.value = null
    try {
      const response = await unpackPluginPackage({
        package: unpackForm.value.package.trim(),
        plugins_root: unpackForm.value.plugins_root?.trim() || undefined,
        profiles_root: unpackForm.value.profiles_root?.trim() || undefined,
        on_conflict: unpackForm.value.on_conflict || 'rename',
      })
      setResult('unpack', response)
      await refreshPluginSources()
      ElMessage.success(`解包完成，处理了 ${response.unpacked_plugin_count} 个插件`)
    } finally {
      unpacking.value = false
    }
  }

  async function handleAnalyze() {
    if (analyzeForm.value.plugins.length === 0) {
      ElMessage.warning('请至少选择一个插件')
      return
    }
    analyzing.value = true
    inspectResult.value = null
    try {
      const response: PluginCliAnalyzeResponse = await analyzePluginBundle({
        plugins: analyzeForm.value.plugins,
        current_sdk_version: analyzeForm.value.current_sdk_version.trim() || undefined,
      })
      setResult('analyze', response)
      ElMessage.success('分析完成')
    } finally {
      analyzing.value = false
    }
  }

  watch(
    selectedPluginIds,
    (pluginIds) => {
      if (packMode.value !== 'single') {
        packForm.value.plugin = pluginIds[0] || ''
      }
      packForm.value.plugins = [...pluginIds]
      analyzeForm.value.plugins = [...pluginIds]
    },
    { immediate: true }
  )

  watch(packMode, (mode) => {
    packForm.value.mode = mode
    if (mode === 'single') {
      packForm.value.plugin = selectedPluginIds.value[0] || ''
    }
  })

  onMounted(() => {
    refreshPluginSources()
    refreshPackageSources()
  })

  return {
    activeTab,
    layoutMode,
    packMode,
    pluginFilter,
    selectedTypes,
    pluginsLoading,
    packagesLoading,
    localPackages,
    targetDir,
    packageFilterType,
    packing,
    inspecting,
    verifying,
    unpacking,
    analyzing,
    resultKind,
    resultText,
    inspectResult,
    packForm,
    packageRef,
    unpackForm,
    analyzeForm,
    selectablePlugins,
    pluginCount,
    adapterCount,
    extensionCount,
    filteredPurePlugins,
    filteredAdapters,
    filteredExtensions,
    selectedPluginIds,
    resolvedPackTargets,
    filteredLocalPackages,
    summaryMetrics,
    summaryHighlights,
    summaryListItems,
    summaryWarnings,
    togglePlugin,
    selectAllVisible,
    clearSelection,
    refreshPluginSources,
    refreshPackageSources,
    selectPackage,
    inspectSelectedPackage,
    verifySelectedPackage,
    prepareUnpackPackage,
    handlePack,
    handleInspect,
    handleVerify,
    handleUnpack,
    handleAnalyze,
  }
}
