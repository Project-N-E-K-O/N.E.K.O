/**
 * Backend `_normalize_plugin_list_action`（plugin/server/application/plugins/
 * ui_query_service.py L454-487）接受 plugin list_action 的 `label` /
 * `confirm_message` 等字段为字符串或按 locale 分组的字典，例如
 * `{"en-US": "Open UI", "zh-CN": "打开界面"}`。
 *
 * `resolve_i18n_refs`（plugin/sdk/shared/i18n.py L155）只解析 `$i18n` ref，
 * 不会把 locale-keyed 字典拍平成单一字符串，所以这种字典会原样发到
 * frontend。模板里直接 `{{ value }}` 在 dict 时会渲染成 "[object Object]"。
 *
 * 这个 helper 统一处理三种情况（string / dict / nullish），并按当前 locale
 * 选合适的字符串。匹配优先级：
 *   1) 当前 locale 完整匹配（e.g. "zh-CN"）
 *   2) 当前 locale 主语言（e.g. "zh-CN" → "zh"）
 *   3) "en-US" / "en" 兜底
 *   4) 字典里第一个字符串值
 *   5) 调用方提供的 fallback（默认空字符串）
 */
export type LocalizedText = string | Record<string, string>

export interface PluginI18nMessages {
  default_locale?: string
  messages?: Record<string, Record<string, string>>
}

export function resolvePluginI18nMessage(
  i18n: PluginI18nMessages | null | undefined,
  key: string,
  locale: string,
  fallback: string = '',
): string {
  const messages = i18n?.messages
  if (!messages || !key) return fallback

  const primary = String(locale).split(/[-_]/)[0]
  const candidates = [
    locale,
    primary && primary !== locale ? primary : undefined,
    i18n?.default_locale,
    'en-US',
    'en',
  ].filter((item): item is string => typeof item === 'string' && item.length > 0)

  for (const candidate of candidates) {
    const message = messages[candidate]?.[key]
    if (typeof message === 'string' && message.length > 0) {
      return message
    }
  }

  return fallback
}

export function resolveLocalizedText(
  value: LocalizedText | null | undefined,
  locale: string,
  fallback: string = '',
): string {
  if (value == null) return fallback
  if (typeof value === 'string') return value.length > 0 ? value : fallback
  if (typeof value !== 'object') return fallback

  const dict = value as Record<string, string>
  const primary = String(locale).split(/[-_]/)[0]
  const candidates = [
    locale,
    primary && primary !== locale ? primary : undefined,
    'en-US',
    'en',
  ]
  // Treat empty strings as missing — historically the ?? chain below
  // short-circuited on `''` (because `'' ?? x === ''`), which silently
  // returned an empty label whenever the current locale was registered
  // with a blank value. Aligning with `resolvePluginI18nMessage`, we
  // require ``length > 0`` for every locale candidate before accepting
  // it, then fall through to the first non-empty value in the dict.
  for (const c of candidates) {
    if (typeof c !== 'string' || c.length === 0) continue
    const v = dict[c]
    if (typeof v === 'string' && v.length > 0) return v
  }
  for (const v of Object.values(dict)) {
    if (typeof v === 'string' && v.length > 0) return v
  }
  return fallback
}
