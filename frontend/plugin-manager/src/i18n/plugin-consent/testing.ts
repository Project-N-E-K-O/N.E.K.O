/**
 * Every language's consent messages, for tests that check all of them.
 * Test-only: app code imports a single language file, so that a language pack
 * carries its own words only (see `../localeBundles.ts`).
 */
import type { AppLocale } from '../index'
import zhCN from './zh-CN'
import zhTW from './zh-TW'
import enUS from './en-US'
import ja from './ja'
import ko from './ko'
import ru from './ru'
import es from './es'
import pt from './pt'

export const allPluginConsentMessages = {
  'zh-CN': zhCN,
  'zh-TW': zhTW,
  'en-US': enUS,
  ja,
  ko,
  ru,
  es,
  pt,
} satisfies Record<AppLocale, typeof enUS>
