import { resolveLocalizedText } from '@/utils/i18nLabel'

import type { ConfigEditorSchema } from '@/types/configSchema'
export type { ConfigEditorSchema } from '@/types/configSchema'

export function schemaText(
  schema: ConfigEditorSchema | undefined,
  key: 'title' | 'description',
  locale: string,
  fallback = '',
): string {
  return resolveLocalizedText(schema?.[`x-${key}-i18n`], locale, schema?.[key] || fallback)
}

export function schemaEnum(schema?: ConfigEditorSchema): Array<string | number | boolean> {
  const values = schema?.enum
  if (!values?.length || !values.every((v) =>
    typeof v === 'string' || typeof v === 'boolean' || (typeof v === 'number' && Number.isFinite(v))
  )) return []
  return values as Array<string | number | boolean>
}

/** Used only for explicit additions, never to materialize defaults on page load. */
export function newSchemaValue(schema?: ConfigEditorSchema): unknown {
  if (schema?.default !== undefined && schema.default !== null) {
    return JSON.parse(JSON.stringify(schema.default))
  }
  const options = schemaEnum(schema)
  if (options.length) return options[0]
  switch (schema?.type) {
    case 'object': return {}
    case 'array': return []
    case 'boolean': return false
    case 'number': return schema.minimum ?? 0
    case 'integer': return Math.ceil(schema.minimum ?? 0)
    default: return ''
  }
}
