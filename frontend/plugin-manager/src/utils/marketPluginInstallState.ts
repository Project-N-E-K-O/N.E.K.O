import { compareVersion } from '@/utils/version'

export type EffectivePluginSource = 'builtin' | 'market' | 'manual' | 'unknown'

export interface MarketInstalledState {
  plugin_id: string
  effective_source?: string | null
  effective_version?: string | null
  market_installed?: boolean
  builtin_version?: string | null
  latest_market_version?: string | null
  latest_install_source?: {
    plugin_market_id?: string
    channel: 'stable' | 'beta'
    version: string
    package_sha256: string
    payload_hash: string | null
    package_url: string
    published_at: string
  } | null
}

export type MarketPluginActionKind =
  | 'install'
  | 'override_builtin'
  | 'upgrade'
  | 'installed'
  | 'builtin'
  | 'blocked'
  | 'unavailable'

export interface MarketPluginAction {
  kind: MarketPluginActionKind
  effectiveSource: EffectivePluginSource
  currentVersion: string
  targetVersion: string
  installed: boolean
}

export async function fetchInstalledProjection<T extends MarketInstalledState>(
  fetcher: () => Promise<Response | null>
): Promise<T[] | null> {
  try {
    const response = await fetcher()
    if (!response?.ok) return null
    const data = await response.json()
    return Array.isArray(data?.installed) ? (data.installed as T[]) : []
  } catch {
    return null
  }
}

export function inferManualInstallConflict(
  projectionLoaded: boolean,
  installedState: MarketInstalledState | null | undefined,
  localIdentityMatch: boolean
): boolean {
  return projectionLoaded && !installedState && localIdentityMatch
}

export function normalizeEffectiveSource(
  state: MarketInstalledState | null | undefined
): EffectivePluginSource {
  if (!state) return 'unknown'
  const source = String(state.effective_source || '')
    .trim()
    .toLowerCase()
  if (source === 'builtin') return 'builtin'
  if (source === 'market') return 'market'
  if (source === 'manual') return 'manual'
  if (source === 'user') return state.market_installed ? 'market' : 'manual'
  if (state.market_installed || state.latest_install_source) return 'market'
  return source ? 'manual' : 'unknown'
}

export function deriveMarketPluginAction(
  state: MarketInstalledState | null | undefined,
  catalogVersion: string,
  hasRelease: boolean,
  manualConflict = false
): MarketPluginAction {
  const effectiveSource = manualConflict && !state ? 'manual' : normalizeEffectiveSource(state)
  const currentVersion = String(
    state?.effective_version ||
      state?.latest_install_source?.version ||
      state?.builtin_version ||
      ''
  )
  // The local bridge may only know the lock version. The catalog card is the
  // authoritative latest release when it is available.
  const targetVersion = String(catalogVersion || state?.latest_market_version || '')
  const installed = effectiveSource !== 'unknown'

  if (effectiveSource === 'manual') {
    return { kind: 'blocked', effectiveSource, currentVersion, targetVersion, installed }
  }
  if (effectiveSource === 'builtin') {
    const canUpgrade =
      hasRelease &&
      !!targetVersion &&
      (!currentVersion || compareVersion(currentVersion, targetVersion) < 0)
    return {
      kind: canUpgrade ? 'override_builtin' : 'builtin',
      effectiveSource,
      currentVersion,
      targetVersion,
      installed,
    }
  }
  if (effectiveSource === 'market') {
    const canUpgrade =
      hasRelease &&
      !!currentVersion &&
      !!targetVersion &&
      compareVersion(currentVersion, targetVersion) < 0
    return {
      kind: canUpgrade ? 'upgrade' : 'installed',
      effectiveSource,
      currentVersion,
      targetVersion,
      installed,
    }
  }
  return {
    kind: hasRelease ? 'install' : 'unavailable',
    effectiveSource,
    currentVersion,
    targetVersion,
    installed: false,
  }
}
