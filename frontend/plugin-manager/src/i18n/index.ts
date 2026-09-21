/** Complete synchronous fallback; optional locale bundles never gate app mount. */
import { createI18n } from 'vue-i18n'
import { reactive, ref, shallowRef } from 'vue'
import zhCN from './locales/zh-CN'
import zhCnElement from 'element-plus/dist/locale/zh-cn.mjs'
import { loadLocaleBundle } from './localeBundles'
import { OptionalModuleError } from '@/utils/retryableModule'

export const SUPPORTED_LOCALES = ['zh-CN', 'zh-TW', 'en-US', 'ja', 'ko', 'ru', 'es', 'pt'] as const
export type AppLocale = (typeof SUPPORTED_LOCALES)[number]
export type LocaleSetting = AppLocale | 'auto'
const DEFAULT_LOCALE: AppLocale = 'zh-CN'

export function resolveLocaleFromBrowser(): AppLocale {
  const languages = navigator.languages?.length ? navigator.languages : [navigator.language]
  for (const lang of languages) {
    if (!lang) continue
    if (SUPPORTED_LOCALES.includes(lang as AppLocale)) return lang as AppLocale
    const langCode = lang.split('-')[0]?.toLowerCase() ?? ''
    if (langCode === 'en') return 'en-US'
    if (['ja', 'ko', 'ru', 'es', 'pt'].includes(langCode)) return langCode as AppLocale
    if (langCode === 'zh') {
      const upper = lang.toUpperCase()
      if (upper.includes('HANS')) return 'zh-CN'
      if (
        upper.includes('HANT') ||
        upper.includes('TW') ||
        upper.includes('HK') ||
        upper.includes('MO')
      )
        return 'zh-TW'
      return 'zh-CN'
    }
  }
  return DEFAULT_LOCALE
}
function readSetting(): LocaleSetting {
  try {
    const raw = localStorage.getItem('locale')
    if (raw === null || raw === 'auto') return 'auto'
    return SUPPORTED_LOCALES.includes(raw as AppLocale) ? (raw as AppLocale) : DEFAULT_LOCALE
  } catch {
    return 'auto'
  }
}
const RETRY_KEY = 'neko_locale_reload_retry'
function takeReloadRetry(): LocaleSetting | null {
  try {
    const raw = sessionStorage.getItem(RETRY_KEY)
    sessionStorage.removeItem(RETRY_KEY)
    return raw === 'auto' || SUPPORTED_LOCALES.includes(raw as AppLocale)
      ? (raw as LocaleSetting)
      : null
  } catch {
    return null
  }
}
const reloadRetry = takeReloadRetry()
const initialSetting = reloadRetry ?? readSetting()
const appliedSetting = ref<LocaleSetting>(DEFAULT_LOCALE)
export const elementLocale = shallowRef(zhCnElement)
export const localeLoadState = reactive<{
  pending: LocaleSetting | null
  error: string | null
  failedSetting: LocaleSetting | null
  reloadRequired: boolean
}>({ pending: null, error: null, failedSetting: null, reloadRequired: false })

export const i18n = createI18n({
  legacy: false,
  locale: DEFAULT_LOCALE as string,
  fallbackLocale: DEFAULT_LOCALE,
  messages: { 'zh-CN': zhCN } as Record<string, typeof zhCN>,
})
let generation = 0
let initialized = false
let failedPersist = true

async function applySetting(setting: LocaleSetting, persist: boolean): Promise<boolean> {
  if (setting !== 'auto' && !SUPPORTED_LOCALES.includes(setting)) return false
  const seq = ++generation
  const target = setting === 'auto' ? resolveLocaleFromBrowser() : setting
  localeLoadState.pending = setting
  localeLoadState.error = null
  localeLoadState.failedSetting = null
  localeLoadState.reloadRequired = false
  try {
    const bundle =
      target === DEFAULT_LOCALE
        ? { messages: zhCN, element: zhCnElement }
        : await loadLocaleBundle(target as Exclude<AppLocale, 'zh-CN'>)
    if (seq !== generation) return false
    // One synchronous commit. No visible locale changes before BOTH app and
    // Element dictionaries are ready; Vue batches their reactive consumers.
    i18n.global.setLocaleMessage(target, bundle.messages as typeof zhCN)
    elementLocale.value = bundle.element
    i18n.global.locale.value = target
    appliedSetting.value = setting
    document.documentElement.lang = target
    if (persist) {
      try {
        localStorage.setItem('locale', setting)
      } catch {
        /* session-only preference */
      }
    }
    return true
  } catch (error) {
    if (seq !== generation) return false
    localeLoadState.error = error instanceof Error ? error.message : String(error)
    localeLoadState.failedSetting = setting
    localeLoadState.reloadRequired = error instanceof OptionalModuleError && error.reloadRequired
    failedPersist = persist
    return false
  } finally {
    if (seq === generation) localeLoadState.pending = null
  }
}

/** Invoked once from main, without awaiting before mount. Startup failure keeps
 * the persisted preference so the user can retry instead of silently losing it. */
export function initializeLocale(): Promise<boolean> | undefined {
  if (initialized) return
  initialized = true
  return applySetting(initialSetting, reloadRetry !== null)
}
export function setLocale(setting: LocaleSetting): Promise<boolean> {
  return applySetting(setting, true)
}
export function retryLocale(): Promise<boolean> {
  return localeLoadState.failedSetting === null
    ? Promise.resolve(false)
    : applySetting(localeLoadState.failedSetting, failedPersist)
}
/** Explicit user action only. Keep the failed target in a one-shot tab-local
 * record; durable preferences are still committed only after a successful load. */
export function reloadLocalePage() {
  try {
    if (localeLoadState.failedSetting !== null)
      sessionStorage.setItem(RETRY_KEY, localeLoadState.failedSetting)
  } catch {
    /* storage denied: normal reload can still recover the saved locale */
  }
  window.location.reload()
}

/** Applied preference, not an uncommitted in-flight selection. */
export function getLocaleSetting(): LocaleSetting {
  return appliedSetting.value
}
export function getLocale(): AppLocale {
  const value = i18n.global.locale.value
  return SUPPORTED_LOCALES.includes(value as AppLocale) ? (value as AppLocale) : DEFAULT_LOCALE
}
