export const SITE_ORIGIN = 'https://project-neko.online'

export const LOCALES = /** @type {const} */ ([
  {
    key: 'en',
    prefix: '',
    hreflang: 'en',
    htmlLang: 'en-US',
    ogLocale: 'en_US',
    docsLabel: 'N.E.K.O. Docs',
  },
  {
    key: 'zh-CN',
    prefix: '/zh-CN',
    hreflang: 'zh-CN',
    htmlLang: 'zh-CN',
    ogLocale: 'zh_CN',
    docsLabel: 'N.E.K.O. 文档',
  },
  {
    key: 'ja',
    prefix: '/ja',
    hreflang: 'ja',
    htmlLang: 'ja',
    ogLocale: 'ja_JP',
    docsLabel: 'N.E.K.O. ドキュメント',
  },
])

export const LOCALE_HOME_PATHS = new Set(
  LOCALES.map(({ prefix }) => (prefix ? `${prefix}/` : '/')),
)
