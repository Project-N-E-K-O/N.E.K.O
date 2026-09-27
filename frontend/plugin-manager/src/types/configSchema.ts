/** Supported form annotations from a plugin's optional config.schema.json. */
export interface ConfigEditorSchema {
  type?: string | string[]
  title?: string
  description?: string
  properties?: Record<string, ConfigEditorSchema>
  items?: ConfigEditorSchema
  enum?: unknown[]
  default?: unknown
  minimum?: number
  maximum?: number
  maxLength?: number
  readOnly?: boolean
  'x-title-i18n'?: Record<string, string>
  'x-description-i18n'?: Record<string, string>
}
