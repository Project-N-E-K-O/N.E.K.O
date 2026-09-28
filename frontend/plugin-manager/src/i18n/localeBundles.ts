import type { AppLocale } from './index'
import type zhCn from 'element-plus/dist/locale/zh-cn.mjs'
import { retryableModule } from '@/utils/retryableModule'

type Bundle = { messages: Record<string, any>; element: typeof zhCn }
// Explicit dynamic imports: no eager glob/barrel may pull other locales into boot.
const loaders = {
  'zh-TW': retryableModule(async (): Promise<Bundle> => {
    const [m, e] = await Promise.all([
      import('./locales/zh-TW'),
      import('element-plus/dist/locale/zh-tw.mjs'),
    ])
    return { messages: m.default, element: e.default }
  }),
  'en-US': retryableModule(async (): Promise<Bundle> => {
    const [m, e] = await Promise.all([
      import('./locales/en-US'),
      import('element-plus/dist/locale/en.mjs'),
    ])
    return { messages: m.default, element: e.default }
  }),
  ja: retryableModule(async (): Promise<Bundle> => {
    const [m, e] = await Promise.all([
      import('./locales/ja'),
      import('element-plus/dist/locale/ja.mjs'),
    ])
    return { messages: m.default, element: e.default }
  }),
  ko: retryableModule(async (): Promise<Bundle> => {
    const [m, e] = await Promise.all([
      import('./locales/ko'),
      import('element-plus/dist/locale/ko.mjs'),
    ])
    return { messages: m.default, element: e.default }
  }),
  ru: retryableModule(async (): Promise<Bundle> => {
    const [m, e] = await Promise.all([
      import('./locales/ru'),
      import('element-plus/dist/locale/ru.mjs'),
    ])
    return { messages: m.default, element: e.default }
  }),
  es: retryableModule(async (): Promise<Bundle> => {
    const [m, e] = await Promise.all([
      import('./locales/es'),
      import('element-plus/dist/locale/es.mjs'),
    ])
    return { messages: m.default, element: e.default }
  }),
  pt: retryableModule(async (): Promise<Bundle> => {
    const [m, e] = await Promise.all([
      import('./locales/pt'),
      import('element-plus/dist/locale/pt.mjs'),
    ])
    return { messages: m.default, element: e.default }
  }),
}
export function loadLocaleBundle(locale: Exclude<AppLocale, 'zh-CN'>) {
  return loaders[locale]()
}
