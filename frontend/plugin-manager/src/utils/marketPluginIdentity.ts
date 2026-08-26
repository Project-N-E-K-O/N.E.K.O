export interface MarketPluginIdentity {
  slug?: string
  id: string | number
  rawId?: number | string
  github_repo?: string
}

export interface InstalledPluginIdentity {
  plugin_id: string
  latest_install_source?: {
    plugin_market_id?: string
  } | null
}

export interface InstalledPluginIdentityIndexes<T> {
  byPluginId: Map<string, T>
  byMarketId: Map<string, T>
}

export function extractRepoPluginId(githubRepo?: string): string | undefined {
  const match = githubRepo?.match(/n\.e\.k\.o_plugin_([a-z_][a-z0-9_]*)/i)
  return match?.[1]
}

export function marketIdentityKeys(plugin: MarketPluginIdentity): string[] {
  const keys = new Set<string>()
  for (const value of [
    plugin.slug,
    String(plugin.id),
    plugin.rawId !== undefined ? String(plugin.rawId) : '',
    extractRepoPluginId(plugin.github_repo),
  ]) {
    const normalized = String(value || '')
      .trim()
      .toLowerCase()
    if (normalized) keys.add(normalized)
  }
  return [...keys]
}

export function marketRecordIdentityKeys(plugin: MarketPluginIdentity): string[] {
  const keys = new Set<string>()
  for (const value of [plugin.id, plugin.rawId]) {
    const normalized = String(value ?? '')
      .trim()
      .toLowerCase()
    if (normalized) keys.add(normalized)
  }
  return [...keys]
}

export function marketLocalIdentityKeys(plugin: MarketPluginIdentity): string[] {
  const keys = new Set<string>()
  for (const value of [plugin.slug, extractRepoPluginId(plugin.github_repo)]) {
    const normalized = String(value || '')
      .trim()
      .toLowerCase()
    if (normalized) keys.add(normalized)
  }
  return [...keys]
}

export function localPluginIdentityKeys(
  plugins: Iterable<{ id?: unknown; name?: unknown }>
): Set<string> {
  const keys = new Set<string>()
  for (const plugin of plugins) {
    const id = String(plugin.id || '')
      .trim()
      .toLowerCase()
    if (id) keys.add(id)
  }
  return keys
}

export function indexInstalledPluginIdentities<T extends InstalledPluginIdentity>(
  installed: Iterable<T>
): InstalledPluginIdentityIndexes<T> {
  const byPluginId = new Map<string, T>()
  const byMarketId = new Map<string, T>()
  for (const entry of installed) {
    const pluginId = String(entry.plugin_id || '')
      .trim()
      .toLowerCase()
    if (pluginId) byPluginId.set(pluginId, entry)

    const marketId = String(entry.latest_install_source?.plugin_market_id || '')
      .trim()
      .toLowerCase()
    if (marketId) byMarketId.set(marketId, entry)
  }
  return { byPluginId, byMarketId }
}
