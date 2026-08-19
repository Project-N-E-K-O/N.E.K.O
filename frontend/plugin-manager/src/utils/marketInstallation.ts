import type { PluginInstallSource } from '@/types/api'
import { compareVersion } from '@/utils/version'

export interface MarketIdentityLike {
  slug?: string
  github_repo?: string
}

export interface LocalPluginLike {
  id: string
  name?: string
  version?: string
  install_source?: Pick<PluginInstallSource, 'source'>
}

export type MarketCardAction =
  | 'install'
  | 'upgrade'
  | 'switch_upgrade'
  | 'switch_source'
  | 'installed'
  | 'local_newer'
  | 'no_release'

export function extractRepoPluginId(githubRepo?: string): string | undefined {
  const match = githubRepo?.match(/n\.e\.k\.o[_-]plugin[_-]([a-z_][a-z0-9_]*)/i)
  return match?.[1]
}

export function resolveExpectedMarketPluginId(
  plugin: MarketIdentityLike,
): string | undefined {
  return extractRepoPluginId(plugin.github_repo) || plugin.slug || undefined
}

export function findLocalPluginForMarket<T extends LocalPluginLike>(
  plugin: MarketIdentityLike,
  localPlugins: readonly T[],
): T | undefined {
  const expectedId = resolveExpectedMarketPluginId(plugin)?.trim().toLowerCase()
  if (!expectedId) return undefined
  return localPlugins.find(
    candidate => String(candidate.id || '').trim().toLowerCase() === expectedId,
  )
}

export function resolveMarketCardAction(input: {
  localInstalled: boolean
  marketManaged: boolean
  localVersion?: string
  marketVersion?: string
  hasRelease: boolean
}): MarketCardAction {
  if (!input.hasRelease) return 'no_release'
  if (!input.localInstalled) return 'install'

  const hasComparableVersions = Boolean(input.localVersion && input.marketVersion)
  const versionOrder = hasComparableVersions
    ? compareVersion(input.localVersion!, input.marketVersion!)
    : 0

  if (input.marketManaged) {
    return versionOrder < 0 ? 'upgrade' : 'installed'
  }
  if (versionOrder < 0) return 'switch_upgrade'
  if (versionOrder > 0) return 'local_newer'
  return 'switch_source'
}
