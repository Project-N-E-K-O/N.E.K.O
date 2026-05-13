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

export function resolveLocalizedText(
  value: LocalizedText | null | undefined,
  locale: string,
  fallback: string = '',
): string {
  if (value == null || value === '') return fallback
  if (typeof value === 'string') return value
  if (typeof value !== 'object') return fallback

  const dict = value as Record<string, string>
  const primary = String(locale).split(/[-_]/)[0]
  return (
    dict[locale]
    ?? (primary && primary !== locale ? dict[primary] : undefined)
    ?? dict['en-US']
    ?? dict['en']
    ?? Object.values(dict).find((v): v is string => typeof v === 'string' && v.length > 0)
    ?? fallback
  )
}
