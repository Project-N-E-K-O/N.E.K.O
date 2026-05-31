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
