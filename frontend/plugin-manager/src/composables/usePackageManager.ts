import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  analyzePluginBundle,
  getPluginCliPackages,
  getPluginCliPlugins,
  inspectPluginPackage,
  buildPluginCli,
  installPluginPackage,
  verifyPluginPackage,
  type PluginCliAnalyzeResponse,
  type PluginCliInspectResponse,
  type PluginCliLocalPackageItem,
  type PluginCliBuildMode,
  type PluginCliBuildRequest,
  type PluginCliInstallRequest,
} from '@/api/pluginCli'
import { usePluginStore } from '@/stores/plugin'
import {
  usePluginWorkbench,
  type PluginWorkbenchGroupType,
  type PluginWorkbenchItem,
  type PluginWorkbenchLayoutMode,
} from '@/composables/usePluginWorkbench'

export type LayoutMode = PluginWorkbenchLayoutMode
export type BuildMode = PluginCliBuildMode
export type PluginGroupType = PluginWorkbenchGroupType
export type PackageResultKind = '' | 'build' | 'inspect' | 'verify' | 'install' | 'analyze'

export type SelectablePlugin = PluginWorkbenchItem

export type PackageResultRecord = {
  id: string
  createdAt: string
  kind: Exclude<PackageResultKind, ''>
  resultText: string
  inspectResult: PluginCliInspectResponse | null
  summaryMetrics: Array<{ label: string; value: string }>
  summaryHighlights: Array<{ label: string; value: string }>
  summaryListItems: string[]
  summaryWarnings: string[]
}

export function usePackageManager() {
  const pluginStore = usePluginStore()

  const activeTab = ref('build')
  const buildMode = ref<BuildMode>('selected')
  const localPluginIds = ref<string[]>([])
  const pluginsLoading = ref(false)
  const packagesLoading = ref(false)
  const localPackages = ref<PluginCliLocalPackageItem[]>([])
  const targetDir = ref('')
  const packageFilterType = ref<'all' | 'plugin' | 'bundle'>('all')

  const building = ref(false)
  const inspecting = ref(false)
  const verifying = ref(false)
  const installing = ref(false)
  const analyzing = ref(false)

  const resultKind = ref<PackageResultKind>('')
  const resultText = ref('')
  const resultData = ref<Record<string, any> | null>(null)
  const inspectResult = ref<PluginCliInspectResponse | null>(null)
  const resultDialogVisible = ref(false)
  const resultHistory = ref<PackageResultRecord[]>([])
  const activeResultId = ref('')

  const buildForm = ref<PluginCliBuildRequest>({
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

  const installForm = ref<PluginCliInstallRequest>({
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
  const {
    filterText: pluginFilter,
    useRegex,
    filterMode,
    selectedTypes,
    layoutMode,
    selectedPluginIds,
    regexError,
    pluginCount,
    adapterCount,
    extensionCount,
    filteredPurePlugins,
    filteredAdapters,
    filteredExtensions,
    setSelectedPluginIds,
    togglePlugin: toggleWorkbenchPlugin,
    selectAllVisible,
    clearSelection,
  } = usePluginWorkbench(selectablePlugins)

  const resolvedBuildTargets = computed(() => {
    if (buildMode.value === 'all') {
      return selectablePlugins.value.map((plugin) => plugin.id)
    }
    if (buildMode.value === 'bundle') {
      return selectedPluginIds.value
    }
    if (buildMode.value === 'single') {
      return buildForm.value.plugin ? [buildForm.value.plugin] : []
    }
    return selectedPluginIds.value
  })

  const filteredLocalPackages = computed(() => {
    if (packageFilterType.value === 'all') {
      return localPackages.value
    }
    return localPackages.value.filter((pkg) => inferPackageType(pkg) === packageFilterType.value)
  })

  const activeResultRecord = computed<PackageResultRecord | null>(() => {
    if (resultHistory.value.length === 0) {
      return null
    }
    return resultHistory.value.find((item) => item.id === activeResultId.value) ?? resultHistory.value[0] ?? null
  })

  function normalizePluginType(type?: string): PluginGroupType {
    if (type === 'adapter') return 'adapter'
    if (type === 'extension') return 'extension'
    return 'plugin'
  }

  function createPrimaryBuildResult(data: Record<string, any> | null, kind: PackageResultKind) {
    if (!data || kind !== 'build') return null
    const built = Array.isArray(data.built) ? data.built : []
    if (built.length !== 1) return null
    return built[0] as Record<string, any>
  }

  function buildSummaryMetrics(kind: Exclude<PackageResultKind, ''>, data: Record<string, any> | null) {
    if (!data) return []

    if (kind === 'build') {
      const primaryBuilt = createPrimaryBuildResult(data, kind)
      return [
        {
          label: '类型',
          value: primaryBuilt?.package_type === 'bundle' ? '整合包' : '插件包',
        },
        { label: '成功', value: String(data.built_count ?? 0) },
        { label: '失败', value: String(data.failed_count ?? 0) },
        {
          label: primaryBuilt?.package_type === 'bundle' ? '包含插件' : '状态',
          value: primaryBuilt?.package_type === 'bundle'
            ? String(primaryBuilt?.plugin_ids?.length ?? 0)
            : data.ok ? '完成' : '部分失败',
        },
      ]
    }

    if (kind === 'inspect' || kind === 'verify') {
      return [
        { label: '插件数', value: String(data.plugin_count ?? 0) },
        { label: 'Profiles', value: String(data.profile_count ?? 0) },
        { label: 'Hash', value: formatHashStatus(data.payload_hash_verified) },
      ]
    }

    if (kind === 'install') {
      return [
        { label: '已处理插件', value: String(data.installed_plugin_count ?? 0) },
        { label: '冲突策略', value: String(data.conflict_strategy ?? '-') },
        { label: 'Hash', value: formatHashStatus(data.payload_hash_verified) },
      ]
    }

    const kindData = data
    return [
      { label: '插件数', value: String(kindData.plugin_count ?? 0) },
      { label: '共同依赖', value: String(kindData.common_dependencies?.length ?? 0) },
      { label: '共享依赖', value: String(kindData.shared_dependencies?.length ?? 0) },
    ]
  }

  function buildSummaryHighlights(kind: Exclude<PackageResultKind, ''>, data: Record<string, any> | null) {
    if (!data) return []

    if (kind === 'build') {
      const primaryBuilt = createPrimaryBuildResult(data, kind)
      const firstBuilt = data.built?.[0]
      const latestBuilt = data.built?.[data.built?.length - 1]
      if (primaryBuilt?.package_type === 'bundle') {
        return [
          primaryBuilt?.plugin_id ? { label: '整合包 ID', value: primaryBuilt.plugin_id } : null,
          primaryBuilt?.package_name ? { label: '整合包名称', value: primaryBuilt.package_name } : null,
          primaryBuilt?.version ? { label: '整合包版本', value: primaryBuilt.version } : null,
          latestBuilt?.package_path ? { label: '输出路径', value: latestBuilt.package_path } : null,
        ].filter(Boolean) as Array<{ label: string; value: string }>
      }
      return [
        firstBuilt?.plugin_id ? { label: '首个插件', value: firstBuilt.plugin_id } : null,
        latestBuilt?.package_path ? { label: '最新包路径', value: latestBuilt.package_path } : null,
      ].filter(Boolean) as Array<{ label: string; value: string }>
    }

    if (kind === 'inspect' || kind === 'verify') {
      return [
        data.package_id ? { label: '包 ID', value: data.package_id } : null,
        data.package_type ? { label: '包类型', value: data.package_type } : null,
        data.version ? { label: '版本', value: data.version } : null,
      ].filter(Boolean) as Array<{ label: string; value: string }>
    }

    if (kind === 'install') {
      return [
        data.package_id ? { label: '包 ID', value: data.package_id } : null,
        data.plugins_root ? { label: '插件目录', value: data.plugins_root } : null,
        data.profile_dir ? { label: 'Profiles 目录', value: data.profile_dir } : null,
      ].filter(Boolean) as Array<{ label: string; value: string }>
    }

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

  function buildSummaryListItems(kind: Exclude<PackageResultKind, ''>, data: Record<string, any> | null) {
    if (!data) return []

    if (kind === 'build') {
      const primaryBuilt = createPrimaryBuildResult(data, kind)
      if (primaryBuilt?.package_type === 'bundle') {
        return (primaryBuilt.plugin_ids ?? []).map((pluginId: string) => `plugin:${pluginId}`)
      }
      return (data.built ?? []).map((item: Record<string, any>) => `${item.plugin_id} -> ${item.package_path}`)
    }

    if (kind === 'inspect' || kind === 'verify') {
      return [
        ...(data.plugins ?? []).map((item: Record<string, any>) => item.plugin_id),
        ...(data.profile_names ?? []).map((name: string) => `profile:${name}`),
      ]
    }

    if (kind === 'install') {
      return (data.installed_plugins ?? []).map((item: Record<string, any>) => {
        const suffix = item.renamed ? ' (renamed)' : ''
        return `${item.target_plugin_id}${suffix}`
      })
    }

    return (data.common_dependencies ?? []).map((item: Record<string, any>) => `${item.name} (${item.plugin_count})`)
  }

  function buildSummaryWarnings(kind: Exclude<PackageResultKind, ''>, data: Record<string, any> | null) {
    if (!data) return []

    if (kind === 'build') {
      const warnings = (data.failed ?? []).map((item: Record<string, any>) => `${item.plugin}: ${item.error}`)
      const primaryBuilt = createPrimaryBuildResult(data, kind)
      if (primaryBuilt?.package_type === 'bundle' && (primaryBuilt.plugin_ids?.length ?? 0) < 2) {
        warnings.push('整合包通常应至少包含两个插件')
      }
      return warnings
    }

    if (kind === 'verify' && data.ok === false) {
      return ['包未通过 hash 校验，请不要直接导入运行环境']
    }

    if (kind === 'inspect' && data.payload_hash_verified === false) {
      return ['当前包 hash 校验失败，内容可能已被修改']
    }

    if (kind === 'analyze') {
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
  }

  function openResultDialog() {
    resultDialogVisible.value = true
  }

  function setActiveResult(recordId: string) {
    activeResultId.value = recordId
  }

  function setResult(kind: Exclude<PackageResultKind, ''>, payload: unknown) {
    resultKind.value = kind
    resultData.value = payload && typeof payload === 'object' ? (payload as Record<string, any>) : null
    resultText.value = JSON.stringify(payload, null, 2)
    const record: PackageResultRecord = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      createdAt: new Date().toLocaleString('zh-CN', { hour12: false }),
      kind,
      resultText: resultText.value,
      inspectResult: kind === 'inspect' || kind === 'verify' ? (resultData.value as PluginCliInspectResponse | null) : null,
      summaryMetrics: buildSummaryMetrics(kind, resultData.value),
      summaryHighlights: buildSummaryHighlights(kind, resultData.value),
      summaryListItems: buildSummaryListItems(kind, resultData.value),
      summaryWarnings: buildSummaryWarnings(kind, resultData.value),
    }
    resultHistory.value = [record, ...resultHistory.value].slice(0, 30)
    activeResultId.value = record.id
    resultDialogVisible.value = true
  }

  function formatHashStatus(value: boolean | null | undefined): string {
    if (value === true) return '通过'
    if (value === false) return '失败'
    return '未校验'
  }

  function togglePlugin(pluginId: string) {
    toggleWorkbenchPlugin(pluginId)
  }

  async function refreshPluginSources() {
    pluginsLoading.value = true
    try {
      const syncResult = await pluginStore.syncRegistryAndFetch()
      const response = await getPluginCliPlugins()
      localPluginIds.value = response.plugins
      setSelectedPluginIds(selectedPluginIds.value.filter((pluginId) => response.plugins.includes(pluginId)))
      if (syncResult.warningMessage) {
        ElMessage.warning(syncResult.warningMessage)
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
    installForm.value.package = packageValue
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

  function prepareInstallPackage(pkg: PluginCliLocalPackageItem) {
    selectPackage(pkg)
    activeTab.value = 'install'
  }

  async function handleBuild() {
    const targets = resolvedBuildTargets.value
    if (targets.length === 0) {
      ElMessage.warning('请先选择要构建的插件')
      return
    }

    building.value = true
    inspectResult.value = null

    try {
      if (buildMode.value === 'bundle') {
        if (targets.length < 2) {
          ElMessage.warning('整合包至少需要选择两个插件')
          return
        }
        const response = await buildPluginCli({
          mode: 'bundle',
          plugins: targets,
          bundle_id: buildForm.value.bundle_id?.trim() || undefined,
          package_name: buildForm.value.package_name?.trim() || undefined,
          package_description: buildForm.value.package_description?.trim() || undefined,
          version: buildForm.value.version?.trim() || undefined,
          target_dir: buildForm.value.target_dir || undefined,
          keep_staging: !!buildForm.value.keep_staging,
        })
        setResult('build', response)
        await refreshPackageSources()
        const latestBuilt = response.built[response.built.length - 1]
        if (latestBuilt?.package_path) {
          focusPackageResult(latestBuilt.package_path)
        }
        ElMessage.success('整合包构建完成')
        return
      }

      if (buildMode.value === 'all') {
        const response = await buildPluginCli({
          mode: 'all',
          target_dir: buildForm.value.target_dir || undefined,
          keep_staging: !!buildForm.value.keep_staging,
        })
        setResult('build', response)
        await refreshPackageSources()
        const latestBuilt = response.built[response.built.length - 1]
        if (latestBuilt?.package_path) {
          focusPackageResult(latestBuilt.package_path)
        }
        ElMessage.success(`构建完成，成功 ${response.built_count} 个`)
        return
      }

      const built: unknown[] = []
      const failed: Array<{ plugin: string; error: string }> = []

      for (const pluginId of targets) {
        try {
          const response = await buildPluginCli({
            mode: 'single',
            plugin: pluginId,
            target_dir: buildForm.value.target_dir || undefined,
            keep_staging: !!buildForm.value.keep_staging,
          })
          built.push(...response.built)
          failed.push(...response.failed)
        } catch (error) {
          failed.push({ plugin: pluginId, error: error instanceof Error ? error.message : String(error) })
        }
      }

      const summary = {
        built,
        built_count: built.length,
        failed,
        failed_count: failed.length,
        ok: failed.length === 0,
      }
      setResult('build', summary)
      await refreshPackageSources()
      const latestBuilt = built[built.length - 1] as { package_path?: string } | undefined
      if (latestBuilt?.package_path) {
        focusPackageResult(latestBuilt.package_path)
      }
      ElMessage.success(`构建完成，成功 ${built.length} 个`)
    } finally {
      building.value = false
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
    } catch (error) {
      ElMessage.error(`包检查失败：${error instanceof Error ? error.message : String(error)}`)
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
    } catch (error) {
      ElMessage.error(`包校验失败：${error instanceof Error ? error.message : String(error)}`)
    } finally {
      verifying.value = false
    }
  }

  async function handleInstall() {
    if (!installForm.value.package?.trim()) {
      ElMessage.warning('请先输入包路径')
      return
    }
    installing.value = true
    inspectResult.value = null
    try {
      const response = await installPluginPackage({
        package: installForm.value.package.trim(),
        plugins_root: installForm.value.plugins_root?.trim() || undefined,
        profiles_root: installForm.value.profiles_root?.trim() || undefined,
        on_conflict: installForm.value.on_conflict || 'rename',
      })
      setResult('install', response)
      await refreshPluginSources()
      ElMessage.success(`安装完成，处理了 ${response.installed_plugin_count} 个插件`)
    } catch (error) {
      ElMessage.error(`安装失败：${error instanceof Error ? error.message : String(error)}`)
    } finally {
      installing.value = false
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
    } catch (error) {
      ElMessage.error(`分析失败：${error instanceof Error ? error.message : String(error)}`)
    } finally {
      analyzing.value = false
    }
  }

  watch(
    selectedPluginIds,
    (pluginIds) => {
      if (buildMode.value !== 'single') {
        buildForm.value.plugin = pluginIds[0] || ''
      }
      buildForm.value.plugins = [...pluginIds]
      analyzeForm.value.plugins = [...pluginIds]
    },
    { immediate: true }
  )

  watch(buildMode, (mode) => {
    buildForm.value.mode = mode
    if (mode === 'single') {
      buildForm.value.plugin = selectedPluginIds.value[0] || ''
    }
  })

  onMounted(() => {
    refreshPluginSources()
    refreshPackageSources()
  })

  return {
    activeTab,
    layoutMode,
    buildMode,
    pluginFilter,
    useRegex,
    filterMode,
    selectedTypes,
    regexError,
    pluginsLoading,
    packagesLoading,
    localPackages,
    targetDir,
    packageFilterType,
    building,
    inspecting,
    verifying,
    installing,
    analyzing,
    resultDialogVisible,
    resultHistory,
    activeResultId,
    activeResultRecord,
    resultKind,
    resultText,
    inspectResult,
    buildForm,
    packageRef,
    installForm,
    analyzeForm,
    selectablePlugins,
    pluginCount,
    adapterCount,
    extensionCount,
    filteredPurePlugins,
    filteredAdapters,
    filteredExtensions,
    selectedPluginIds,
    resolvedBuildTargets,
    filteredLocalPackages,
    setActiveResult,
    openResultDialog,
    togglePlugin,
    selectAllVisible,
    clearSelection,
    refreshPluginSources,
    refreshPackageSources,
    selectPackage,
    inspectSelectedPackage,
    verifySelectedPackage,
    prepareInstallPackage,
    handleBuild,
    handleInspect,
    handleVerify,
    handleInstall,
    handleAnalyze,
  }
}
