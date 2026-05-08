/**
 * i18n 配置
 */
import { createI18n } from 'vue-i18n'
import zhCN from './locales/zh-CN'
import zhTW from './locales/zh-TW'
import enUS from './locales/en-US'
import ja from './locales/ja'
import ko from './locales/ko'
import ru from './locales/ru'
import es from './locales/es'
import pt from './locales/pt'

export const SUPPORTED_LOCALES = ['zh-CN', 'zh-TW', 'en-US', 'ja', 'ko', 'ru', 'es', 'pt'] as const
export type AppLocale = (typeof SUPPORTED_LOCALES)[number]
export type LocaleSetting = AppLocale | 'auto'
const DEFAULT_LOCALE: AppLocale = 'zh-CN'

/**
 * 根据浏览器语言自动匹配最合适的支持语言
 */
function resolveLocaleFromBrowser(): AppLocale {
  const languages = navigator.languages?.length ? navigator.languages : [navigator.language]

  for (const lang of languages) {
    if (!lang) continue

    // 完全匹配
    if (SUPPORTED_LOCALES.includes(lang as AppLocale)) {
      return lang as AppLocale
    }

    // 按基础语言代码部分匹配
    const langCode = lang.split('-')[0]?.toLowerCase() ?? ''
    if (langCode === 'en') return 'en-US'
    if (langCode === 'ja') return 'ja'
    if (langCode === 'ko') return 'ko'
    if (langCode === 'ru') return 'ru'
    if (langCode === 'es') return 'es'
    if (langCode === 'pt') return 'pt'
    if (langCode === 'zh') {
      const upper = lang.toUpperCase()
      if (upper.includes('HANS')) return 'zh-CN'
      if (upper.includes('HANT') || upper.includes('TW') || upper.includes('HK') || upper.includes('MO')) {
        return 'zh-TW'
      }
      return 'zh-CN'
    }
  }

  return DEFAULT_LOCALE
}

/**
 * 解析实际生效的语言
 */
function resolveEffectiveLocale(): AppLocale {
  const raw = localStorage.getItem('locale')
  // 首次访问（null）或明确设置为 auto 时，自动检测浏览器语言
  if (raw === null || raw === 'auto') {
    return resolveLocaleFromBrowser()
  }
  if (SUPPORTED_LOCALES.includes(raw as AppLocale)) {
    return raw as AppLocale
  }
  return DEFAULT_LOCALE
}

export const i18n = createI18n({
  legacy: false, // 使用 Composition API 模式
  locale: resolveEffectiveLocale(),
  fallbackLocale: 'zh-CN',
  messages: {
    'zh-CN': zhCN,
    'zh-TW': zhTW,
    'en-US': enUS,
    'ja': ja,
    'ko': ko,
    'ru': ru,
    'es': es,
    'pt': pt
  }
})

/**
 * 获取用户的语言设置（可能是 'auto' 或具体语言）
 */
export function getLocaleSetting(): LocaleSetting {
  const raw = localStorage.getItem('locale')
  if (raw === 'auto') return 'auto'
  if (raw && SUPPORTED_LOCALES.includes(raw as AppLocale)) return raw as AppLocale
  // 首次访问，默认为 auto
  return 'auto'
}

/**
 * 切换语言设置
 */
export function setLocale(setting: LocaleSetting) {
  localStorage.setItem('locale', setting)
  if (setting === 'auto') {
    i18n.global.locale.value = resolveLocaleFromBrowser()
  } else {
    i18n.global.locale.value = setting
  }
}

/**
 * 获取当前实际生效的语言（始终返回具体的 locale，不会返回 'auto'）
 */
export function getLocale(): AppLocale {
  const locale = i18n.global.locale.value
  return SUPPORTED_LOCALES.includes(locale as AppLocale)
    ? (locale as AppLocale)
    : DEFAULT_LOCALE
}
