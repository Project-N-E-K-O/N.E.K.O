import { computed, onMounted, ref, toValue, watch, type MaybeRefOrGetter } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  analyzePluginBundle,
  getPluginCliPackages,
  getPluginCliPlugins,
  inspectPluginPackage,
  buildPluginCli,
  installPluginPackage,
  planPluginInstall,
  verifyPluginPackage,
  type PluginCliAnalyzeResponse,
  type PluginCliInspectResponse,
  type PluginCliLocalPackageItem,
  type PluginCliBuildMode,
  type PluginCliBuildRequest,
  type PluginCliBuildResponse,
  type PluginCliInstallRequest,
  type PluginCliInstallPlanResponse,
  type PluginCliPluginRef,
} from '@/api/pluginCli'
import { usePluginStore } from '@/stores/plugin'
import {
  usePluginWorkbench,
  type PluginWorkbenchGroupType,
  type PluginWorkbenchItem,
  type PluginWorkbenchLayoutMode,
} from '@/composables/usePluginWorkbench'
import { resolvePluginDisplayText } from '@/utils/pluginDisplay'
import { formatHttpError } from '@/utils/request'

export type LayoutMode = PluginWorkbenchLayoutMode
export type BuildMode = PluginCliBuildMode
export type PluginGroupType = PluginWorkbenchGroupType
export type PackageResultKind = '' | 'build' | 'inspect' | 'verify' | 'install' | 'analyze'

export type SelectablePlugin = PluginWorkbenchItem

export type UsePackageManagerOptions = {
  externalSelectedPluginIds?: MaybeRefOrGetter<readonly string[] | undefined>
}

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

export function usePackageManager(options: UsePackageManagerOptions = {}) {
  const pluginStore = usePluginStore()
  // PR #1480 review-fix 1.31 (Phase 7): summary labels and the createdAt
  // timestamp must follow the active i18n locale. ``t`` reads from the
  // ``package.summary.*`` namespace defined in the locale files; ``locale``
  // drives ``Intl.DateTimeFormat`` in ``setResult`` so the recorded creation
  // time matches whatever language the user is currently viewing.
  const { t, locale } = useI18n()

  const activeTab = ref('build')
  const buildMode = ref<BuildMode>('selected')
  const localPluginIds = ref<string[]>([])
  const localPluginRefs = ref<PluginCliPluginRef[]>([])
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
    on_conflict: 'fail',
  })
  const installPlan = ref<PluginCliInstallPlanResponse | null>(null)

  const analyzeForm = ref({
    plugins: [] as string[],
    current_sdk_version: '',
  })

  const pluginRefByKey = computed(() => {
    return new Map(localPluginRefs.value.map((ref) => [pluginRefKey(ref), ref] as const))
  })

  const selectablePlugins = computed<SelectablePlugin[]>(() => {
    const metaById = new Map(
      pluginStore.pluginsWithStatus.map((plugin) => {
        const displayText = resolvePluginDisplayText(plugin, locale.value)
        return [
          plugin.id,
          {
          id: plugin.id,
          name: plugin.name || plugin.id,
          description: plugin.description || '',
          short_description: plugin.short_description,
          displayName: displayText.name,
          displayDescription: displayText.description,
          displayShortDescription: displayText.shortDescription,
          version: plugin.version || '0.0.0',
          type: normalizePluginType(plugin.type),
          status: plugin.status,
          entries: plugin.entries || [],
          i18n: plugin.i18n,
          runtime_enabled: plugin.runtime_enabled,
          runtime_auto_start: plugin.runtime_auto_start,
          enabled: plugin.enabled,
          autoStart: plugin.autoStart,
          } satisfies SelectablePlugin,
        ] as const
      })
    )

    return localPluginIds.value.map((pluginKey) => {
      const ref = pluginRefByKey.value.get(pluginKey)
      const meta = ref
        ? metaById.get(ref.plugin_id || '') ?? metaById.get(ref.directory_name)
        : metaById.get(pluginKey)
      if (meta) {
        return {
          ...meta,
          id: pluginKey,
          displayName: ref?.label || meta.displayName,
        }
      }
      const fallbackName = ref?.label || ref?.plugin_id || ref?.directory_name || pluginKey
      return {
          id: pluginKey,
          name: fallbackName,
          description: '',
          version: '0.0.0',
          type: 'plugin',
          entries: [],
        }
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
    filteredPurePlugins,
    filteredAdapters,
    setSelectedPluginIds,
    togglePlugin: toggleWorkbenchPlugin,
    selectAllVisible,
    clearSelection,
  } = usePluginWorkbench(selectablePlugins, { scope: 'plugin-package-workbench' })

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
    return 'plugin'
  }

  function pluginRefKey(ref: PluginCliPluginRef): string {
    return `${ref.root_id}:${ref.directory_name}`
  }

  function pluginRefAliases(ref: PluginCliPluginRef): string[] {
    return [
      pluginRefKey(ref),
      ref.plugin_id,
      ref.directory_name,
    ].filter((value): value is string => !!value)
  }

  function externalPluginIdsToTargets(pluginIds: readonly string[]): string[] {
    const availableIds = new Set(localPluginIds.value)
    const targetByAlias = new Map<string, string>()
    for (const ref of localPluginRefs.value) {
      const key = pluginRefKey(ref)
      for (const alias of pluginRefAliases(ref)) {
        targetByAlias.set(alias, key)
      }
    }

    return pluginIds
      .map((pluginId) => targetByAlias.get(pluginId) || (availableIds.has(pluginId) ? pluginId : ''))
      .filter((pluginId): pluginId is string => !!pluginId)
  }

  function syncExternalSelection() {
    const externalSelected = toValue(options.externalSelectedPluginIds)
    if (!externalSelected) return
    setSelectedPluginIds(externalPluginIdsToTargets(externalSelected))
  }

  function targetRef(target: string): PluginCliPluginRef | undefined {
    const ref = pluginRefByKey.value.get(target)
    if (!ref) return undefined
    return {
      root_id: ref.root_id,
      directory_name: ref.directory_name,
    }
  }

  function targetRefs(targets: string[]): PluginCliPluginRef[] {
    const refs = targets.map((target) => targetRef(target))
    return refs.every(Boolean) ? (refs as PluginCliPluginRef[]) : []
  }

  function targetLabel(target: string): string {
    const ref = pluginRefByKey.value.get(target)
    return ref?.label || ref?.plugin_id || ref?.directory_name || target
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
          label: t('package.summary.metrics.type'),
          value: primaryBuilt?.package_type === 'bundle'
            ? t('package.summary.values.bundle')
            : t('package.summary.values.plugin'),
        },
        { label: t('package.summary.metrics.success'), value: String(data.built_count ?? 0) },
        { label: t('package.summary.metrics.failed'), value: String(data.failed_count ?? 0) },
        {
          label: primaryBuilt?.package_type === 'bundle'
            ? t('package.summary.metrics.included')
            : t('package.summary.metrics.status'),
          value: primaryBuilt?.package_type === 'bundle'
            ? String(primaryBuilt?.plugin_ids?.length ?? 0)
            : data.ok
              ? t('package.summary.metrics.completed')
              : t('package.summary.metrics.partialFailure'),
        },
      ]
    }

    if (kind === 'inspect' || kind === 'verify') {
      return [
        { label: t('package.summary.metrics.pluginCount'), value: String(data.plugin_count ?? 0) },
        { label: t('package.summary.metrics.profiles'), value: String(data.profile_count ?? 0) },
        { label: t('package.summary.metrics.hash'), value: formatHashStatus(data.payload_hash_verified) },
      ]
    }

    if (kind === 'install') {
      return [
        { label: t('package.summary.metrics.installedPluginCount'), value: String(data.installed_plugin_count ?? 0) },
        { label: t('package.summary.metrics.conflictStrategy'), value: String(data.conflict_strategy ?? '-') },
        { label: t('package.summary.metrics.hash'), value: formatHashStatus(data.payload_hash_verified) },
      ]
    }

    const kindData = data
    return [
      { label: t('package.summary.metrics.pluginCount'), value: String(kindData.plugin_count ?? 0) },
      { label: t('package.summary.metrics.commonDeps'), value: String(kindData.common_dependencies?.length ?? 0) },
      { label: t('package.summary.metrics.sharedDeps'), value: String(kindData.shared_dependencies?.length ?? 0) },
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
          primaryBuilt?.plugin_id ? { label: t('package.summary.highlights.bundleId'), value: primaryBuilt.plugin_id } : null,
          primaryBuilt?.package_name ? { label: t('package.summary.highlights.bundleName'), value: primaryBuilt.package_name } : null,
          primaryBuilt?.version ? { label: t('package.summary.highlights.bundleVersion'), value: primaryBuilt.version } : null,
          latestBuilt?.package_path ? { label: t('package.summary.highlights.outputPath'), value: latestBuilt.package_path } : null,
        ].filter(Boolean) as Array<{ label: string; value: string }>
      }
      return [
        firstBuilt?.plugin_id ? { label: t('package.summary.highlights.firstPlugin'), value: firstBuilt.plugin_id } : null,
        latestBuilt?.package_path ? { label: t('package.summary.highlights.latestPath'), value: latestBuilt.package_path } : null,
      ].filter(Boolean) as Array<{ label: string; value: string }>
    }

    if (kind === 'inspect' || kind === 'verify') {
      return [
        data.package_id ? { label: t('package.summary.highlights.packageId'), value: data.package_id } : null,
        data.package_type ? { label: t('package.summary.highlights.packageType'), value: data.package_type } : null,
        data.version ? { label: t('package.summary.highlights.version'), value: data.version } : null,
      ].filter(Boolean) as Array<{ label: string; value: string }>
    }

    if (kind === 'install') {
      return [
        data.package_id ? { label: t('package.summary.highlights.packageId'), value: data.package_id } : null,
        data.plugins_root ? { label: t('package.summary.highlights.pluginsRoot'), value: data.plugins_root } : null,
        data.profile_dir ? { label: t('package.summary.highlights.profilesRoot'), value: data.profile_dir } : null,
      ].filter(Boolean) as Array<{ label: string; value: string }>
    }

    const sdkSupported = data.sdk_supported_analysis
    const sdkRecommended = data.sdk_recommended_analysis
    return [
      sdkSupported?.current_sdk_version
        ? {
            label: t('package.summary.highlights.currentSdk'),
            value: sdkSupported.current_sdk_supported_by_all
              ? t('package.summary.values.sdkAllSupported', { version: sdkSupported.current_sdk_version })
              : t('package.summary.values.sdkPartiallyIncompatible', { version: sdkSupported.current_sdk_version }),
          }
        : null,
      sdkRecommended?.matching_versions?.length
        ? { label: t('package.summary.highlights.recommendedIntersection'), value: sdkRecommended.matching_versions.join(', ') }
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
        warnings.push(t('package.summary.warnings.bundleNeedsTwoPlugins'))
      }
      return warnings
    }

    if (kind === 'verify' && data.ok === false) {
      return [t('package.summary.warnings.verifyHashFailed')]
    }

    if (kind === 'inspect' && data.payload_hash_verified === false) {
      return [t('package.summary.warnings.inspectHashFailed')]
    }

    if (kind === 'analyze') {
      const warnings: string[] = []
      if (data.sdk_supported_analysis && data.sdk_supported_analysis.current_sdk_supported_by_all === false) {
        warnings.push(t('package.summary.warnings.sdkNotSupportedByAll'))
      }
      if ((data.shared_dependencies?.length ?? 0) > 0) {
        warnings.push(t('package.summary.warnings.sharedDepsDetected', { count: data.shared_dependencies.length }))
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
      // PR #1480 review-fix 1.31 (Phase 7): use ``Intl.DateTimeFormat``
      // bound to the active vue-i18n locale so the recorded creation time
      // follows the user's current language instead of being locked to
      // ``zh-CN``. Options preserve the original 24-hour, two-digit shape so
      // existing UI table widths still fit.
      createdAt: new Intl.DateTimeFormat(locale.value, {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      }).format(new Date()),
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
    // PR #1480 review-fix 1.31 (Phase 7): reuse the existing
    // ``package.hash.*`` keys (already i18n-ed for en-US / zh-CN by Phase 2)
    // so the metric value matches the dialog label vocabulary.
    if (value === true) return t('package.hash.passed')
    if (value === false) return t('package.hash.failed')
    return t('package.hash.notVerified')
  }

  function togglePlugin(pluginId: string) {
    toggleWorkbenchPlugin(pluginId)
  }

  async function refreshPluginSources() {
    pluginsLoading.value = true
    try {
      const syncResult = await pluginStore.syncRegistryAndFetch()
      const response = await getPluginCliPlugins()
      const refs = response.plugin_refs || []
      localPluginRefs.value = refs
      localPluginIds.value = refs.length > 0 ? refs.map((ref) => pluginRefKey(ref)) : response.plugins
      const availableIds = new Set(localPluginIds.value)
      if (options.externalSelectedPluginIds) {
        syncExternalSelection()
      } else {
        setSelectedPluginIds(selectedPluginIds.value.filter((pluginId) => availableIds.has(pluginId)))
      }
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

  function buildErrorMessage(error: unknown): string {
    return formatHttpError(error)
  }

  function failedBuildResponse(plugin: string, error: unknown): PluginCliBuildResponse {
    return {
      built: [],
      built_count: 0,
      failed: [{ plugin, error: buildErrorMessage(error) }],
      failed_count: 1,
      ok: false,
    }
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
        let response: PluginCliBuildResponse
        const refs = targetRefs(targets)
        try {
          response = await buildPluginCli({
            mode: 'bundle',
            plugin_refs: refs.length > 0 ? refs : undefined,
            plugins: refs.length > 0 ? undefined : targets,
            bundle_id: buildForm.value.bundle_id?.trim() || undefined,
            package_name: buildForm.value.package_name?.trim() || undefined,
            package_description: buildForm.value.package_description?.trim() || undefined,
            version: buildForm.value.version?.trim() || undefined,
            target_dir: buildForm.value.target_dir || undefined,
            keep_staging: !!buildForm.value.keep_staging,
          })
        } catch (error) {
          response = failedBuildResponse(targets.map(targetLabel).join(', '), error)
          setResult('build', response)
          ElMessage.error(`整合包构建失败：${buildErrorMessage(error)}`)
          return
        }
        setResult('build', response)
        await refreshPackageSources()
        const latestBuilt = response.built[response.built.length - 1]
        if (latestBuilt?.package_path) {
          focusPackageResult(latestBuilt.package_path)
        }
        ElMessage[response.ok ? 'success' : 'warning'](
          response.ok ? '整合包构建完成' : `整合包构建失败 ${response.failed_count} 个`,
        )
        return
      }

      if (buildMode.value === 'all') {
        let response: PluginCliBuildResponse
        try {
          response = await buildPluginCli({
            mode: 'all',
            target_dir: buildForm.value.target_dir || undefined,
            keep_staging: !!buildForm.value.keep_staging,
          })
        } catch (error) {
          response = failedBuildResponse('all', error)
          setResult('build', response)
          ElMessage.error(`构建失败：${buildErrorMessage(error)}`)
          return
        }
        setResult('build', response)
        await refreshPackageSources()
        const latestBuilt = response.built[response.built.length - 1]
        if (latestBuilt?.package_path) {
          focusPackageResult(latestBuilt.package_path)
        }
        ElMessage[response.failed_count > 0 ? 'warning' : 'success'](
          response.failed_count > 0
            ? `构建完成，成功 ${response.built_count} 个，失败 ${response.failed_count} 个`
            : `构建完成，成功 ${response.built_count} 个`,
        )
        return
      }

      const built: PluginCliBuildResponse['built'] = []
      const failed: Array<{ plugin: string; error: string }> = []

      for (const pluginId of targets) {
        const ref = targetRef(pluginId)
        try {
          const response = await buildPluginCli({
            mode: 'single',
            plugin_ref: ref,
            plugin: ref ? undefined : pluginId,
            target_dir: buildForm.value.target_dir || undefined,
            keep_staging: !!buildForm.value.keep_staging,
          })
          built.push(...response.built)
          failed.push(...response.failed)
        } catch (error) {
          failed.push({ plugin: targetLabel(pluginId), error: buildErrorMessage(error) })
        }
      }

      const summary: PluginCliBuildResponse = {
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
      ElMessage[failed.length > 0 ? 'warning' : 'success'](
        failed.length > 0 ? `构建完成，成功 ${built.length} 个，失败 ${failed.length} 个` : `构建完成，成功 ${built.length} 个`,
      )
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
      ElMessage.error(`包检查失败：${formatHttpError(error)}`)
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
      ElMessage.error(`包校验失败：${formatHttpError(error)}`)
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
    installPlan.value = null
    try {
      const packagePath = installForm.value.package.trim()
      const pluginsRoot = installForm.value.plugins_root?.trim() || undefined
      const plan = await planPluginInstall({
        package: packagePath,
        plugins_root: pluginsRoot,
      })
      installPlan.value = plan

      if (plan.action === 'blocked') {
        const blockedKey = plan.reason === 'bundle_conflict'
          ? 'package.install.blockedBundleConflict'
          : plan.reason === 'legacy_plugin_present'
            ? 'package.install.blockedLegacyPlugin'
            : 'package.install.blockedDirectoryConflict'
        ElMessage.error(
          plan.reason === 'legacy_plugin_present'
            ? t(blockedKey, {
                plugin: plan.legacy_plugin_ids[0] || plan.plugin_id || plan.directory_name,
              })
            : t(blockedKey),
        )
        return
      }

      const request: PluginCliInstallRequest = {
        package: packagePath,
        plugins_root: pluginsRoot,
        profiles_root: installForm.value.profiles_root?.trim() || undefined,
        on_conflict: 'fail',
      }
      if (plan.action === 'upgrade') {
        try {
          await ElMessageBox.confirm(
            t('package.install.upgradeBody', {
              current: plan.current_version || '-',
              target: plan.target_version || '-',
            }),
            t('package.install.upgradeTitle', {
              plugin: plan.plugin_id || plan.directory_name,
            }),
            {
              type: 'warning',
              confirmButtonText: t('package.install.upgradeConfirm'),
              cancelButtonText: t('common.cancel'),
            },
          )
        } catch {
          ElMessage.info(t('package.install.upgradeCancelled'))
          return
        }
        request.confirm_upgrade = true
        request.confirmation_token = plan.confirmation_token
      }

      const response = await installPluginPackage(request)
      setResult('install', response)
      await refreshPluginSources()
      if (response.operation === 'upgrade') {
        ElMessage.success(t('package.install.upgradeSucceeded', {
          plugin: plan.plugin_id || plan.directory_name,
        }))
      } else {
        ElMessage.success(`安装完成，处理了 ${response.installed_plugin_count} 个插件`)
      }
    } catch (error) {
      const errorCode = (error as any)?.response?.data?.detail?.code
        || (error as any)?.response?.data?.code
      if (errorCode === 'PLUGIN_UPGRADE_ROLLED_BACK') {
        const rollbackStatus = (error as any)?.response?.data?.detail?.details?.rollback_status
        ElMessage.error(t(
          rollbackStatus === 'completed'
            ? 'package.install.rollbackCompleted'
            : 'package.install.rollbackIncomplete',
        ))
      } else if (!installPlan.value) {
        ElMessage.error(t('package.install.planFailed'))
      } else {
        ElMessage.error(`安装失败：${formatHttpError(error)}`)
      }
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
      const refs = targetRefs(analyzeForm.value.plugins)
      const response: PluginCliAnalyzeResponse = await analyzePluginBundle({
        plugin_refs: refs.length > 0 ? refs : undefined,
        plugins: refs.length > 0 ? undefined : analyzeForm.value.plugins,
        current_sdk_version: analyzeForm.value.current_sdk_version.trim() || undefined,
      })
      setResult('analyze', response)
      ElMessage.success('分析完成')
    } catch (error) {
      ElMessage.error(`分析失败：${formatHttpError(error)}`)
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

  watch(
    () => toValue(options.externalSelectedPluginIds),
    () => {
      syncExternalSelection()
    },
    { immediate: true },
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
    installPlan,
    analyzeForm,
    selectablePlugins,
    pluginCount,
    adapterCount,
    filteredPurePlugins,
    filteredAdapters,
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
