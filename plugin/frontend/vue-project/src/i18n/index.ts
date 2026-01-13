/**
 * i18n 配置
 */
import { createI18n } from 'vue-i18n'
import zhCN from './locales/zh-CN'
import enUS from './locales/en-US'

// 从 localStorage 获取保存的语言设置，默认为中文
const savedLocale = localStorage.getItem('locale') || 'zh-CN'

export const i18n = createI18n({
  legacy: false, // 使用 Composition API 模式
  locale: savedLocale,
  fallbackLocale: 'zh-CN',
  messages: {
    'zh-CN': zhCN,
    'en-US': enUS
  }
})

/**
 * Switches the application's active locale and saves the selection to localStorage.
 *
 * @param locale - The target locale, either `zh-CN` or `en-US`; this value becomes the active locale and is persisted under the `locale` key in localStorage.
 */
export function setLocale(locale: 'zh-CN' | 'en-US') {
  i18n.global.locale.value = locale
  localStorage.setItem('locale', locale)
}

/**
 * Get the currently active application locale.
 *
 * @returns The active locale, either `'zh-CN'` or `'en-US'`.
 */
export function getLocale(): 'zh-CN' | 'en-US' {
  return i18n.global.locale.value as 'zh-CN' | 'en-US'
}
