/** Complete synchronous fallback; optional locale bundles never gate app mount. */
import { createI18n } from 'vue-i18n'
import { reactive, ref, shallowRef } from 'vue'
import zhCN from './locales/zh-CN'
import zhCnElement from 'element-plus/dist/locale/zh-cn.mjs'
import { loadLocaleBundle } from './localeBundles'
import { loadPluginConsentPageMessages } from './pluginConsentPages'
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

// Words of the permission tab and the privacy page (`pluginConsent.tab`,
// `pluginConsent.privacy` except its title) load with those routes, not with
// the language pack. Once a route asked for them, every language switch loads
// them too, so a switch never leaves the open page without its words.
// Unlike the rest of the pack, they are merged into the applied language only,
// so the zh-CN fallback lacks them while another language is applied; the
// per-language key parity test in localeSplit.test.ts keeps every language
// complete instead. A failed load fails a language switch only while one of
// those pages is shown (the switch keeps the current language and reports a
// recoverable error); elsewhere the pack is committed without them and the
// next ensurePluginConsentPageMessages() call (the router retries on
// navigation) merges them in.
type ConsentPageMessages = Awaited<ReturnType<typeof loadPluginConsentPageMessages>>
let consentPagesWanted = false
let consentPageShown = false
const consentPages = new Map<AppLocale, ConsentPageMessages>()
const CONSENT_PAGE_PROBE = 'pluginConsent.tab.loading'

async function consentPagesFor(locale: AppLocale): Promise<ConsentPageMessages> {
  const cached = consentPages.get(locale)
  if (cached) return cached
  const messages = await loadPluginConsentPageMessages(locale)
  consentPages.set(locale, messages)
  return messages
}

// Builds a new dictionary: the language pack modules are never mutated.
type Dictionary = Record<string, unknown>
function withConsentPages(messages: Dictionary, pages: ConsentPageMessages | undefined) {
  if (!pages) return messages
  const base = (messages.pluginConsent ?? {}) as Dictionary
  const privacy = { ...(base.privacy as Dictionary | undefined), ...pages.privacy }
  return { ...messages, pluginConsent: { ...base, ...pages, privacy } }
}

/** Set by the router after each navigation: whether a page using these words is shown. */
export function setPluginConsentPageShown(shown: boolean) {
  consentPageShown = shown
}

/** Loads the permission tab and privacy page words for the applied language
 * and keeps them loaded across language switches. Route loaders await it. */
export async function ensurePluginConsentPageMessages(): Promise<void> {
  consentPagesWanted = true
  const locale = getLocale()
  const pages = await consentPagesFor(locale)
  // A switch committed meanwhile carries its own copy (it saw the flag set).
  if (getLocale() !== locale || i18n.global.te(CONSENT_PAGE_PROBE, locale)) return
  i18n.global.setLocaleMessage(
    locale,
    withConsentPages(i18n.global.getLocaleMessage(locale), pages) as typeof zhCN
  )
}

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
    if (consentPagesWanted) {
      try {
        await consentPagesFor(target)
      } catch (error) {
        if (consentPageShown) throw error
      }
      if (seq !== generation) return false
    }
    // One synchronous commit. No visible locale changes before BOTH app and
    // Element dictionaries are ready; Vue batches their reactive consumers.
    i18n.global.setLocaleMessage(
      target,
      withConsentPages(
        bundle.messages,
        consentPagesWanted ? consentPages.get(target) : undefined
      ) as typeof zhCN
    )
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
