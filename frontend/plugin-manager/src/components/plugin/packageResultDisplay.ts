import type { PluginCliInspectResponse } from '@/api/pluginCli'

export type PackageResultKind = 'pack' | 'inspect' | 'verify' | 'unpack' | 'analyze'

export interface PackageResultSourceRecord {
  id: string
  createdAtTs: number
  kind: PackageResultKind
  resultText: string
  resultData: Record<string, any> | null
  inspectResult: PluginCliInspectResponse | null
}

export interface PackageResultDisplayRecord {
  id: string
  createdAtTs: number
  createdAt: string
  kind: PackageResultKind
  kindLabel: string
  resultText: string
  inspectResult: PluginCliInspectResponse | null
  summaryMetrics: Array<{ label: string; value: string }>
  summaryHighlights: Array<{ label: string; value: string }>
  summaryListItems: string[]
  summaryWarnings: string[]
}

type TranslateFn = (key: string, params?: Record<string, any>) => string

function formatDateTime(timestamp: number, locale: string): string {
  if (!Number.isFinite(timestamp) || timestamp <= 0) return '-'
  return new Intl.DateTimeFormat(locale || undefined, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(timestamp))
}

function getPackageTypeLabel(type: string | null | undefined, t: TranslateFn): string {
  if (type === 'bundle') return t('packageManager.resultDialog.inspect.packageTypes.bundle')
  if (type === 'plugin') return t('packageManager.resultDialog.inspect.packageTypes.plugin')
  return String(type || '-')
}

function getHashStatusLabel(value: boolean | null | undefined, t: TranslateFn): string {
  if (value === true) return t('packageManager.resultDialog.inspect.hashStatus.passed')
  if (value === false) return t('packageManager.resultDialog.inspect.hashStatus.failed')
  return t('packageManager.resultDialog.inspect.hashStatus.notChecked')
}

function buildSummaryMetrics(kind: PackageResultKind, data: Record<string, any> | null, t: TranslateFn) {
  if (!data) return []

  if (kind === 'pack') {
    const packed = Array.isArray(data.packed) ? data.packed : []
    const primaryPacked = packed.length === 1 ? packed[0] as Record<string, any> : null
    const isBundle = primaryPacked?.package_type === 'bundle'
    return [
      {
        label: t('packageManager.resultDialog.metrics.pack.type'),
        value: isBundle
          ? t('packageManager.resultDialog.inspect.packageTypes.bundle')
          : t('packageManager.resultDialog.inspect.packageTypes.plugin'),
      },
      { label: t('packageManager.resultDialog.metrics.pack.succeeded'), value: String(data.packed_count ?? 0) },
      { label: t('packageManager.resultDialog.metrics.pack.failed'), value: String(data.failed_count ?? 0) },
      {
        label: isBundle
          ? t('packageManager.resultDialog.metrics.pack.containsPlugins')
          : t('packageManager.resultDialog.metrics.pack.status'),
        value: isBundle
          ? String(primaryPacked?.plugin_ids?.length ?? 0)
          : (data.ok ? t('packageManager.resultDialog.metrics.pack.complete') : t('packageManager.resultDialog.metrics.pack.partialFailed')),
      },
    ]
  }

  if (kind === 'inspect' || kind === 'verify') {
    return [
      { label: t('packageManager.resultDialog.metrics.inspect.pluginCount'), value: String(data.plugin_count ?? 0) },
      { label: t('packageManager.resultDialog.metrics.inspect.profileCount'), value: String(data.profile_count ?? 0) },
      { label: t('packageManager.resultDialog.metrics.inspect.hash'), value: getHashStatusLabel(data.payload_hash_verified, t) },
    ]
  }

  if (kind === 'unpack') {
    return [
      { label: t('packageManager.resultDialog.metrics.unpack.processedPlugins'), value: String(data.unpacked_plugin_count ?? 0) },
      {
        label: t('packageManager.resultDialog.metrics.unpack.conflictStrategy'),
        value: String(data.conflict_strategy ?? '-'),
      },
      { label: t('packageManager.resultDialog.metrics.unpack.hash'), value: getHashStatusLabel(data.payload_hash_verified, t) },
    ]
  }

  return [
    { label: t('packageManager.resultDialog.metrics.analyze.pluginCount'), value: String(data.plugin_count ?? 0) },
    { label: t('packageManager.resultDialog.metrics.analyze.commonDependencies'), value: String(data.common_dependencies?.length ?? 0) },
    { label: t('packageManager.resultDialog.metrics.analyze.sharedDependencies'), value: String(data.shared_dependencies?.length ?? 0) },
  ]
}

function buildSummaryHighlights(kind: PackageResultKind, data: Record<string, any> | null, t: TranslateFn) {
  if (!data) return []

  if (kind === 'pack') {
    const packed = Array.isArray(data.packed) ? data.packed : []
    const primaryPacked = packed.length === 1 ? packed[0] as Record<string, any> : null
    const firstPacked = packed[0] as Record<string, any> | undefined
    const latestPacked = packed[packed.length - 1] as Record<string, any> | undefined
    if (primaryPacked?.package_type === 'bundle') {
      return [
        primaryPacked?.plugin_id ? { label: t('packageManager.resultDialog.highlights.pack.bundlePluginId'), value: primaryPacked.plugin_id } : null,
        primaryPacked?.package_name ? { label: t('packageManager.resultDialog.highlights.pack.bundleName'), value: primaryPacked.package_name } : null,
        primaryPacked?.version ? { label: t('packageManager.resultDialog.highlights.pack.bundleVersion'), value: primaryPacked.version } : null,
        latestPacked?.package_path ? { label: t('packageManager.resultDialog.highlights.pack.outputPath'), value: latestPacked.package_path } : null,
      ].filter(Boolean) as Array<{ label: string; value: string }>
    }
    return [
      firstPacked?.plugin_id ? { label: t('packageManager.resultDialog.highlights.pack.firstPlugin'), value: firstPacked.plugin_id } : null,
      latestPacked?.package_path ? { label: t('packageManager.resultDialog.highlights.pack.latestPackagePath'), value: latestPacked.package_path } : null,
    ].filter(Boolean) as Array<{ label: string; value: string }>
  }

  if (kind === 'inspect' || kind === 'verify') {
    return [
      data.package_id ? { label: t('packageManager.resultDialog.highlights.inspect.packageId'), value: data.package_id } : null,
      data.package_type ? { label: t('packageManager.resultDialog.highlights.inspect.packageType'), value: getPackageTypeLabel(data.package_type, t) } : null,
      data.version ? { label: t('packageManager.resultDialog.highlights.inspect.version'), value: data.version } : null,
    ].filter(Boolean) as Array<{ label: string; value: string }>
  }

  if (kind === 'unpack') {
    return [
      data.package_id ? { label: t('packageManager.resultDialog.highlights.unpack.packageId'), value: data.package_id } : null,
      data.plugins_root ? { label: t('packageManager.resultDialog.highlights.unpack.pluginsRoot'), value: data.plugins_root } : null,
      data.profile_dir ? { label: t('packageManager.resultDialog.highlights.unpack.profilesRoot'), value: data.profile_dir } : null,
    ].filter(Boolean) as Array<{ label: string; value: string }>
  }

  const sdkSupported = data.sdk_supported_analysis
  const sdkRecommended = data.sdk_recommended_analysis
  return [
    sdkSupported?.current_sdk_version
      ? {
          label: t('packageManager.resultDialog.highlights.analyze.currentSdk'),
          value: sdkSupported.current_sdk_supported_by_all === false
            ? `${sdkSupported.current_sdk_version} ${t('packageManager.resultDialog.highlights.analyze.unsupported')}`
            : `${sdkSupported.current_sdk_version} ${t('packageManager.resultDialog.highlights.analyze.supported')}`,
        }
      : null,
    sdkRecommended?.matching_versions?.length
      ? { label: t('packageManager.resultDialog.highlights.analyze.matchingVersions'), value: sdkRecommended.matching_versions.join(', ') }
      : null,
  ].filter(Boolean) as Array<{ label: string; value: string }>
}

function buildSummaryListItems(kind: PackageResultKind, data: Record<string, any> | null, t: TranslateFn) {
  if (!data) return []

  if (kind === 'pack') {
    const packed = Array.isArray(data.packed) ? data.packed : []
    const primaryPacked = packed.length === 1 ? packed[0] as Record<string, any> : null
    if (primaryPacked?.package_type === 'bundle') {
      return (primaryPacked.plugin_ids ?? []).map((pluginId: string) => `${t('packageManager.resultDialog.list.pluginPrefix')}${pluginId}`)
    }
    return packed.map((item: Record<string, any>) => `${item.plugin_id} ${t('packageManager.resultDialog.list.arrow')} ${item.package_path}`)
  }

  if (kind === 'inspect' || kind === 'verify') {
    return [
      ...(data.plugins ?? []).map((item: Record<string, any>) => item.plugin_id),
      ...(data.profile_names ?? []).map((name: string) => `${t('packageManager.resultDialog.list.profilePrefix')}${name}`),
    ]
  }

  if (kind === 'unpack') {
    return (data.unpacked_plugins ?? []).map((item: Record<string, any>) => {
      const suffix = item.renamed ? ` ${t('packageManager.resultDialog.list.renamedSuffix')}` : ''
      return `${item.target_plugin_id}${suffix}`
    })
  }

  return (data.common_dependencies ?? []).map((item: Record<string, any>) => `${item.name} (${item.plugin_count})`)
}

function buildSummaryWarnings(kind: PackageResultKind, data: Record<string, any> | null, t: TranslateFn) {
  if (!data) return []

  if (kind === 'pack') {
    const warnings = (data.failed ?? []).map((item: Record<string, any>) => `${item.plugin}: ${item.error}`)
    const packed = Array.isArray(data.packed) ? data.packed : []
    const primaryPacked = packed.length === 1 ? packed[0] as Record<string, any> : null
    if (primaryPacked?.package_type === 'bundle' && (primaryPacked.plugin_ids?.length ?? 0) < 2) {
      warnings.push(t('packageManager.resultDialog.warnings.bundleNeedsTwoPlugins'))
    }
    return warnings
  }

  if (kind === 'verify' && data.ok === false) {
    return [t('packageManager.resultDialog.warnings.verifyFailed')]
  }

  if (kind === 'inspect' && data.payload_hash_verified === false) {
    return [t('packageManager.resultDialog.warnings.inspectHashFailed')]
  }

  if (kind === 'analyze') {
    const warnings: string[] = []
    if (data.sdk_supported_analysis && data.sdk_supported_analysis.current_sdk_supported_by_all === false) {
      warnings.push(t('packageManager.resultDialog.warnings.analyzeSdkMismatch'))
    }
    if ((data.shared_dependencies?.length ?? 0) > 0) {
      warnings.push(
        t('packageManager.resultDialog.warnings.analyzeSharedDependencies', {
          count: data.shared_dependencies.length,
        }),
      )
    }
    return warnings
  }

  return []
}

export function buildPackageResultDisplayRecord(
  record: PackageResultSourceRecord,
  t: TranslateFn,
  locale: string,
): PackageResultDisplayRecord {
  return {
    id: record.id,
    createdAtTs: record.createdAtTs,
    createdAt: formatDateTime(record.createdAtTs, locale),
    kind: record.kind,
    kindLabel: t(`packageManager.resultDialog.kinds.${record.kind}`),
    resultText: record.resultText,
    inspectResult: record.inspectResult,
    summaryMetrics: buildSummaryMetrics(record.kind, record.resultData, t),
    summaryHighlights: buildSummaryHighlights(record.kind, record.resultData, t),
    summaryListItems: buildSummaryListItems(record.kind, record.resultData, t),
    summaryWarnings: buildSummaryWarnings(record.kind, record.resultData, t),
  }
}

export function buildPackageResultDisplayRecords(
  records: PackageResultSourceRecord[],
  t: TranslateFn,
  locale: string,
): PackageResultDisplayRecord[] {
  return records.map((record) => buildPackageResultDisplayRecord(record, t, locale))
}

export { formatDateTime, getHashStatusLabel, getPackageTypeLabel }
