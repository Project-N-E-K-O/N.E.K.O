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
 *   3) Chinese UI locales additionally try "zh-CN"
 *   4) 调用方提供的 fallback locale
 *   5) "en-US" / "en" 兜底
 *   6) 字典里第一个字符串值
 *   7) 调用方提供的 fallback（默认空字符串）
 */
export type LocalizedText = string | Record<string, string>

function localeCandidates(locale: string, fallbackLocale = 'en'): string[] {
  const candidates: string[] = []

  const add = (value?: string | null) => {
    const normalized = String(value || '').trim()
    if (normalized && !candidates.includes(normalized)) {
      candidates.push(normalized)
    }
  }

  add(locale)
  const primary = String(locale || '').split(/[-_]/)[0]
  if (primary && primary !== locale) {
    add(primary)
  }
  const localeLower = String(locale || '').trim().toLowerCase()
  if (localeLower === 'zh' || localeLower.startsWith('zh-') || localeLower.startsWith('zh_')) {
    add('zh-CN')
  }
  add(fallbackLocale)
  const fallbackPrimary = String(fallbackLocale || '').split(/[-_]/)[0]
  if (fallbackPrimary && fallbackPrimary !== fallbackLocale) {
    add(fallbackPrimary)
  }
  add('en-US')
  add('en')
  return candidates
}

export function resolveLocalizedText(
  value: LocalizedText | null | undefined,
  locale: string,
  fallback: string = '',
): string {
  if (value == null || value === '') return fallback
  if (typeof value === 'string') return value
  if (typeof value !== 'object') return fallback

  const dict = value as Record<string, string>
  return (
    localeCandidates(locale)
      .map((candidate) => dict[candidate])
      .find((candidate): candidate is string => typeof candidate === 'string' && candidate.length > 0)
    ?? Object.values(dict).find((v): v is string => typeof v === 'string' && v.length > 0)
    ?? fallback
  )
}

export function resolvePluginI18nMessage(
  i18n: unknown,
  key: string,
  locale: string,
  fallback: string = '',
): string {
  if (!i18n || typeof i18n !== 'object') return fallback
  const payload = i18n as {
    default_locale?: unknown
    messages?: Record<string, Record<string, unknown>>
  }
  const messages = payload.messages
  if (!messages || typeof messages !== 'object') return fallback
  const defaultLocale = typeof payload.default_locale === 'string' ? payload.default_locale : 'en'

  for (const candidate of localeCandidates(locale, defaultLocale)) {
    const bundle = messages[candidate]
    if (!bundle || typeof bundle !== 'object') continue
    const value = bundle[key]
    if (typeof value === 'string' && value.length > 0) {
      return value
    }
  }
  return fallback
}
