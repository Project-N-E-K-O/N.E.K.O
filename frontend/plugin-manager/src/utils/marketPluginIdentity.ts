export interface MarketPluginIdentity {
  slug?: string
  id: string | number
  rawId?: number | string
  github_repo?: string
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
