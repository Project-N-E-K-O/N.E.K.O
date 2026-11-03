import type { PluginMeta } from '@/types/api'
import { resolvePluginI18nMessage } from '@/utils/i18nLabel'

export interface PluginDisplayText {
  name: string
  description: string
  shortDescription: string
}

function stringFallback(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value.length > 0 ? value : fallback
}

export function resolvePluginDisplayText(plugin: PluginMeta, locale: string): PluginDisplayText {
  const fallbackName = stringFallback(plugin.name, plugin.id)
  const fallbackDescription = stringFallback(plugin.description)
  const fallbackShortDescription = stringFallback(plugin.short_description, fallbackDescription)

  return {
    name: resolvePluginI18nMessage(plugin.i18n, 'plugin.name', locale, fallbackName),
    description: resolvePluginI18nMessage(
      plugin.i18n,
      'plugin.description',
      locale,
      fallbackDescription,
    ),
    shortDescription: resolvePluginI18nMessage(
      plugin.i18n,
      'plugin.short_description',
      locale,
      fallbackShortDescription,
    ),
  }
}

function normalizeDisplayName(value: string, locale: string): string {
  return value
    .normalize('NFKC')
    .trim()
    .replace(/\s+/g, ' ')
    .toLocaleLowerCase(locale)
}

/**
 * Return the logical IDs that need to be shown alongside their display name.
 *
 * Plugin names are presentation text and are not unique. Keeping the ID hidden
 * is easier to scan in the common case, but becomes misleading when two
 * different plugins resolve to the same localized name.
 */
export function findDuplicatePluginDisplayNameIds(
  plugins: readonly PluginMeta[],
  locale: string,
): ReadonlySet<string> {
  const idsByDisplayName = new Map<string, Set<string>>()

  for (const plugin of plugins) {
    const displayName = normalizeDisplayName(resolvePluginDisplayText(plugin, locale).name, locale)
    const ids = idsByDisplayName.get(displayName) ?? new Set<string>()
    ids.add(plugin.id)
    idsByDisplayName.set(displayName, ids)
  }

  const duplicateIds = new Set<string>()
  for (const ids of idsByDisplayName.values()) {
    if (ids.size < 2) continue
    for (const id of ids) duplicateIds.add(id)
  }
  return duplicateIds
}
