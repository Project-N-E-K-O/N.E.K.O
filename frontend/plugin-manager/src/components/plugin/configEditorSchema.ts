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

/** Redact display copies only; configuration values and save payloads stay intact. */
export function redactConfigSecrets(value: unknown, schema?: ConfigEditorSchema): unknown {
  if (value === undefined) return undefined
  if (schema?.writeOnly) return '********'
  if (Array.isArray(value)) return value.map((item) => redactConfigSecrets(item, schema?.items))
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, child]) => {
      const field = schema?.properties && Object.prototype.hasOwnProperty.call(schema.properties, key)
        ? schema.properties[key] : undefined
      return [key, redactConfigSecrets(child, field)]
    }))
  }
  return value
}
