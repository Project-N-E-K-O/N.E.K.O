/**
 * i18n 配置
 */
import { createI18n } from 'vue-i18n'
import zhCN from './locales/zh-CN'
import enUS from './locales/en-US'

const SUPPORTED_LOCALES = ['zh-CN', 'en-US'] as const
type AppLocale = (typeof SUPPORTED_LOCALES)[number]
const DEFAULT_LOCALE: AppLocale = 'zh-CN'

// 从 localStorage 获取保存的语言设置，校验合法性，默认为中文
const rawLocale = localStorage.getItem('locale')
const savedLocale: AppLocale =
  rawLocale && SUPPORTED_LOCALES.includes(rawLocale as AppLocale)
    ? (rawLocale as AppLocale)
    : DEFAULT_LOCALE

export const i18n = createI18n({
  legacy: false, // 使用 Composition API 模式
  locale: savedLocale,
  fallbackLocale: 'zh-CN',
  messages: {
    'zh-CN': zhCN,
    'en-US': enUS
  }
})

// 导出切换语言的辅助函数
export function setLocale(locale: AppLocale) {
  i18n.global.locale.value = locale
  localStorage.setItem('locale', locale)
}

// 导出获取当前语言的辅助函数
export function getLocale(): AppLocale {
  const locale = i18n.global.locale.value
  return SUPPORTED_LOCALES.includes(locale as AppLocale)
    ? (locale as AppLocale)
    : DEFAULT_LOCALE
}
