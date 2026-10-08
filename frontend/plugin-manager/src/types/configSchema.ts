/** Supported form annotations from a plugin's optional config.schema.json. */
export interface ConfigEditorSchema {
  type?: 'object' | 'array' | 'string' | 'number' | 'integer' | 'boolean'
  title?: string
  description?: string
  properties?: Record<string, ConfigEditorSchema>
  additionalProperties?: ConfigEditorSchema | boolean
  items?: ConfigEditorSchema
  enum?: unknown[]
  default?: unknown
  minimum?: number
  maximum?: number
  maxLength?: number
  readOnly?: boolean
  writeOnly?: boolean
  'x-title-i18n'?: Record<string, string>
  'x-description-i18n'?: Record<string, string>
}
