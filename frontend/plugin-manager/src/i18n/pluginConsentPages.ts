import type { AppLocale } from './index'
import type { PluginConsentPageMessages } from './plugin-consent-pages/en-US'
import { retryableModule } from '@/utils/retryableModule'

type Loaded = { default: PluginConsentPageMessages }
// The permission tab and the privacy page carry ~15 KB of words per language
// pair; they load with the routes that show them instead of the first screen.
// Explicit dynamic imports, one per language, like `./localeBundles.ts`.
const loaders: Record<AppLocale, () => Promise<Loaded>> = {
  'zh-CN': retryableModule(() => import('./plugin-consent-pages/zh-CN')),
  'zh-TW': retryableModule(() => import('./plugin-consent-pages/zh-TW')),
  'en-US': retryableModule(() => import('./plugin-consent-pages/en-US')),
  ja: retryableModule(() => import('./plugin-consent-pages/ja')),
  ko: retryableModule(() => import('./plugin-consent-pages/ko')),
  ru: retryableModule(() => import('./plugin-consent-pages/ru')),
  es: retryableModule(() => import('./plugin-consent-pages/es')),
  pt: retryableModule(() => import('./plugin-consent-pages/pt')),
}

export async function loadPluginConsentPageMessages(
  locale: AppLocale
): Promise<PluginConsentPageMessages> {
  return (await loaders[locale]()).default
}
