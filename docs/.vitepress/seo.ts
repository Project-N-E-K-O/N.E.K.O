import { readFileSync } from 'node:fs'
import { resolve, sep } from 'node:path'
import type { HeadConfig, TransformContext } from 'vitepress'
import { isNoindexRoute } from './indexing-policy.mjs'

export const SITE_ORIGIN = 'https://project-neko.online'

const PROJECT_ORIGIN = 'https://project-neko.cn/'
const GITHUB_URL = 'https://github.com/Project-N-E-K-O/N.E.K.O'
const STEAM_URL = 'https://store.steampowered.com/app/4099310/__NEKO/'
const LOGO_URL = `${SITE_ORIGIN}/logo.jpg`
const ORGANIZATION_ID = `${SITE_ORIGIN}/#organization`
const WEBSITE_ID = `${SITE_ORIGIN}/#website`
const SOFTWARE_ID = `${SITE_ORIGIN}/#software`

type PageData = TransformContext['pageData']
type LocaleKey = 'en' | 'zh-CN' | 'ja'
type PageSchemaType = 'WebPage' | 'CollectionPage' | 'TechArticle'

interface LocaleDefinition {
  key: LocaleKey
  prefix: '' | '/zh-CN' | '/ja'
  hreflang: 'en' | 'zh-CN' | 'ja'
  htmlLang: 'en-US' | 'zh-CN' | 'ja'
  ogLocale: 'en_US' | 'zh_CN' | 'ja_JP'
  docsLabel: string
}

interface AlternatePage {
  locale: LocaleDefinition
  route: string
  url: string
}

const LOCALES: readonly LocaleDefinition[] = [
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
]

const SECTION_LABELS: Record<LocaleKey, Record<string, string>> = {
  en: {
    guide: 'Guide',
    architecture: 'Architecture',
    api: 'API Reference',
    plugins: 'Plugins',
    config: 'Configuration',
    modules: 'Core Modules',
    frontend: 'Frontend',
    deployment: 'Deployment',
    contributing: 'Contributing',
    benchmarks: 'Benchmarks',
    changelog: 'Changelog',
  },
  'zh-CN': {
    guide: '指南',
    architecture: '架构',
    api: 'API 参考',
    plugins: '插件',
    config: '配置',
    modules: '核心模块',
    frontend: '前端',
    deployment: '部署',
    contributing: '贡献',
    benchmarks: '基准测试',
    changelog: '更新日志',
  },
  ja: {
    guide: 'ガイド',
    architecture: 'アーキテクチャ',
    api: 'API リファレンス',
    plugins: 'プラグイン',
    config: '設定',
    modules: 'コアモジュール',
    frontend: 'フロントエンド',
    deployment: 'デプロイ',
    contributing: 'コントリビュート',
    benchmarks: 'ベンチマーク',
    changelog: '変更履歴',
  },
}

export function sourcePathToRoute(sourcePath: string): string {
  const normalizedPath = sourcePath.replaceAll('\\', '/').replace(/^\/+/, '')
  return `/${normalizedPath}`
    .replace(/(^|\/)index\.md$/, '$1')
    .replace(/\.md$/, '')
}

function normalizeRoute(urlOrRoute: string): string {
  const route = new URL(urlOrRoute, `${SITE_ORIGIN}/`).pathname
  return route || '/'
}

export function isIndexableRoute(urlOrRoute: string): boolean {
  return !isNoindexRoute(normalizeRoute(urlOrRoute))
}

function localeForRoute(route: string): LocaleDefinition {
  return LOCALES.find(
    (locale) =>
      locale.prefix &&
      (route === locale.prefix || route.startsWith(`${locale.prefix}/`)),
  ) ?? LOCALES[0]
}

function routeWithinLocale(route: string, locale: LocaleDefinition): string {
  if (!locale.prefix) return route
  const routeWithoutPrefix = route.slice(locale.prefix.length)
  return routeWithoutPrefix || '/'
}

function routeForLocale(
  routeWithinCurrentLocale: string,
  locale: LocaleDefinition,
): string {
  if (!locale.prefix) return routeWithinCurrentLocale
  return routeWithinCurrentLocale === '/'
    ? `${locale.prefix}/`
    : `${locale.prefix}${routeWithinCurrentLocale}`
}

function absoluteUrl(route: string): string {
  return new URL(route, `${SITE_ORIGIN}/`).toString()
}

function alternatePages(
  route: string,
  availableRoutes: ReadonlySet<string>,
): AlternatePage[] {
  const currentLocale = localeForRoute(route)
  const routeWithoutLocale = routeWithinLocale(route, currentLocale)

  const alternates = LOCALES.flatMap((locale) => {
    const candidate = routeForLocale(routeWithoutLocale, locale)
    if (!availableRoutes.has(candidate) || !isIndexableRoute(candidate)) return []
    return [{ locale, route: candidate, url: absoluteUrl(candidate) }]
  })

  return alternates.length > 1 ? alternates : []
}

function stripFrontmatter(markdown: string): string {
  return markdown.replace(
    /^\uFEFF?---[ \t]*\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)/,
    '',
  )
}

function cleanMarkdownText(markdown: string): string {
  return markdown
    .replace(/!\[[^\]]*]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)]\[[^\]]*]/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/<[^>]+>/g, ' ')
    .replace(/[*~]/g, '')
    .replace(/\\([\\`*{}[\]()#+\-.!_>])/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
}

function truncateDescription(description: string, maximum = 160): string {
  const characters = Array.from(description)
  if (characters.length <= maximum) return description

  const shortened = characters.slice(0, maximum).join('')
  const punctuationMatches = [...shortened.matchAll(/[.!?。！？；;]/g)]
  const lastPunctuation = punctuationMatches.at(-1)?.index ?? -1
  if (lastPunctuation >= Math.floor(maximum * 0.55)) {
    return shortened.slice(0, lastPunctuation + 1).trim()
  }

  const lastSpace = shortened.lastIndexOf(' ')
  const safeCut = lastSpace >= Math.floor(maximum * 0.7)
    ? shortened.slice(0, lastSpace)
    : shortened
  return `${safeCut.trim()}…`
}

function extractMarkdownDescription(markdown: string): string | undefined {
  const body = stripFrontmatter(markdown)
    .replace(/```[\s\S]*?```/g, '')
    .replace(/~~~[\s\S]*?~~~/g, '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<script\b[\s\S]*?<\/script>/gi, '')
    .replace(/<style\b[\s\S]*?<\/style>/gi, '')

  for (const block of body.split(/\r?\n[ \t]*\r?\n/)) {
    const lines = block
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)

    if (!lines.length) continue
    if (lines.some((line) => line.startsWith('|'))) continue

    const paragraphLines = lines
      .filter((line) => !/^(?:#{1,6}\s|:::+|import\s|export\s|<\w)/.test(line))
      .filter((line) => !/^(?:[-*+]\s|\d+[.)]\s)/.test(line))
      .map((line) => line.replace(/^>\s?/, ''))

    const candidate = cleanMarkdownText(paragraphLines.join(' '))
    if (Array.from(candidate).length < 50) continue
    if (!/[A-Za-z\u3040-\u30ff\u3400-\u9fff]/.test(candidate)) continue
    return truncateDescription(candidate)
  }

  return undefined
}

function fallbackDescription(pageData: PageData): string {
  const route = sourcePathToRoute(pageData.relativePath)
  const locale = localeForRoute(route)

  if (locale.key === 'zh-CN') {
    return truncateDescription(
      `Project N.E.K.O.「${pageData.title}」开发文档，包含相关配置、接口、系统行为与实现说明。`,
    )
  }
  if (locale.key === 'ja') {
    return truncateDescription(
      `Project N.E.K.O. の「${pageData.title}」開発ドキュメント。設定、API、システム動作、実装の詳細を説明します。`,
    )
  }
  return truncateDescription(
    `${pageData.title} documentation for Project N.E.K.O., including configuration, APIs, system behavior, and implementation details.`,
  )
}

function descriptionFromSource(pageData: PageData, docsRoot: string): string {
  if (pageData.description.trim()) return pageData.description.trim()

  try {
    const docsRootPath = resolve(docsRoot)
    const sourcePath = resolve(docsRootPath, pageData.filePath)
    if (
      sourcePath !== docsRootPath &&
      !sourcePath.startsWith(`${docsRootPath}${sep}`)
    ) {
      return fallbackDescription(pageData)
    }
    const markdown = readFileSync(sourcePath, 'utf8')
    return extractMarkdownDescription(markdown) ?? fallbackDescription(pageData)
  } catch {
    return fallbackDescription(pageData)
  }
}

export function buildSeoPageData(
  pageData: PageData,
  docsRoot: string,
): Partial<PageData> | undefined {
  if (pageData.isNotFound || pageData.description.trim()) return undefined
  return { description: descriptionFromSource(pageData, docsRoot) }
}

function sectionRoute(
  route: string,
  locale: LocaleDefinition,
): string | undefined {
  const withinLocale = routeWithinLocale(route, locale)
  const [section] = withinLocale.split('/').filter(Boolean)
  if (!section) return undefined
  return routeForLocale(`/${section}/`, locale)
}

function breadcrumbData(
  context: TransformContext,
  route: string,
  canonical: string,
  locale: LocaleDefinition,
  availableRoutes: ReadonlySet<string>,
): Record<string, unknown> | undefined {
  const homeRoute = routeForLocale('/', locale)
  if (route === homeRoute) return undefined

  const elements: Array<Record<string, unknown>> = [
    {
      '@type': 'ListItem',
      position: 1,
      name: locale.docsLabel,
      item: absoluteUrl(homeRoute),
    },
  ]

  const candidateSectionRoute = sectionRoute(route, locale)
  if (
    candidateSectionRoute &&
    availableRoutes.has(candidateSectionRoute) &&
    isIndexableRoute(candidateSectionRoute)
  ) {
    const section = routeWithinLocale(candidateSectionRoute, locale)
      .split('/')
      .filter(Boolean)[0]
    elements.push({
      '@type': 'ListItem',
      position: elements.length + 1,
      name: SECTION_LABELS[locale.key][section] ?? section,
      item: absoluteUrl(candidateSectionRoute),
    })
  }

  if (!elements.some((element) => element.item === canonical)) {
    elements.push({
      '@type': 'ListItem',
      position: elements.length + 1,
      name: context.pageData.title,
      item: canonical,
    })
  }

  return {
    '@type': 'BreadcrumbList',
    '@id': `${canonical}#breadcrumb`,
    itemListElement: elements,
  }
}

function homeStructuredData(
  context: TransformContext,
  canonical: string,
  locale: LocaleDefinition,
): Record<string, unknown> {
  return {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'Organization',
        '@id': ORGANIZATION_ID,
        name: 'Project N.E.K.O.',
        url: PROJECT_ORIGIN,
        logo: {
          '@type': 'ImageObject',
          url: LOGO_URL,
        },
        sameAs: [GITHUB_URL, STEAM_URL],
      },
      {
        '@type': 'WebSite',
        '@id': WEBSITE_ID,
        url: `${SITE_ORIGIN}/`,
        name: 'N.E.K.O. Docs',
        alternateName: 'Project N.E.K.O. Developer Documentation',
        publisher: { '@id': ORGANIZATION_ID },
        inLanguage: LOCALES.map((item) => item.htmlLang),
      },
      {
        '@type': 'WebPage',
        '@id': `${canonical}#webpage`,
        url: canonical,
        name: context.pageData.title,
        description: context.description,
        inLanguage: locale.htmlLang,
        isPartOf: { '@id': WEBSITE_ID },
        about: { '@id': SOFTWARE_ID },
      },
      {
        '@type': 'SoftwareApplication',
        '@id': SOFTWARE_ID,
        name: 'Project N.E.K.O.',
        url: PROJECT_ORIGIN,
        applicationCategory: 'EntertainmentApplication',
        author: { '@id': ORGANIZATION_ID },
        sameAs: [GITHUB_URL, STEAM_URL],
      },
    ],
  }
}

function pageSchemaType(
  context: TransformContext,
  route: string,
  locale: LocaleDefinition,
): PageSchemaType {
  const declaredType = context.pageData.frontmatter.seoSchemaType
  if (
    declaredType === 'WebPage' ||
    declaredType === 'CollectionPage' ||
    declaredType === 'TechArticle'
  ) {
    return declaredType
  }

  const routeWithoutLocale = routeWithinLocale(route, locale)
  return routeWithoutLocale.endsWith('/') ? 'CollectionPage' : 'TechArticle'
}

function pageStructuredData(
  context: TransformContext,
  route: string,
  canonical: string,
  locale: LocaleDefinition,
  availableRoutes: ReadonlySet<string>,
  schemaType: PageSchemaType,
): Record<string, unknown> {
  const dateModified = context.pageData.lastUpdated
    ? new Date(context.pageData.lastUpdated).toISOString()
    : undefined
  const breadcrumb = breadcrumbData(
    context,
    route,
    canonical,
    locale,
    availableRoutes,
  )

  const page: Record<string, unknown> = schemaType === 'TechArticle'
    ? {
        '@type': schemaType,
        '@id': `${canonical}#article`,
        headline: context.pageData.title,
        description: context.description,
        url: canonical,
        inLanguage: locale.htmlLang,
        mainEntityOfPage: {
          '@type': 'WebPage',
          '@id': `${canonical}#webpage`,
        },
        isPartOf: { '@id': WEBSITE_ID },
        author: {
          '@type': 'Organization',
          '@id': ORGANIZATION_ID,
          name: 'Project N.E.K.O.',
          url: PROJECT_ORIGIN,
        },
        publisher: { '@id': ORGANIZATION_ID },
      }
    : {
        '@type': schemaType,
        '@id': `${canonical}#webpage`,
        url: canonical,
        name: context.pageData.title,
        description: context.description,
        inLanguage: locale.htmlLang,
        isPartOf: { '@id': WEBSITE_ID },
        about: { '@id': SOFTWARE_ID },
        publisher: { '@id': ORGANIZATION_ID },
      }
  if (dateModified) page.dateModified = dateModified

  return {
    '@context': 'https://schema.org',
    '@graph': breadcrumb ? [page, breadcrumb] : [page],
  }
}

function jsonLdHead(data: Record<string, unknown>): HeadConfig {
  const json = JSON.stringify(data).replace(/</g, '\\u003c')
  return ['script', { type: 'application/ld+json' }, json]
}

export function buildSeoHead(
  context: TransformContext,
  availableRoutes: ReadonlySet<string>,
): HeadConfig[] {
  if (context.pageData.isNotFound) {
    return [['meta', { name: 'robots', content: 'noindex,follow' }]]
  }

  const route = sourcePathToRoute(context.pageData.relativePath)
  const canonical = absoluteUrl(route)
  const locale = localeForRoute(route)
  const indexable = isIndexableRoute(route)
  const alternates = indexable
    ? alternatePages(route, availableRoutes)
    : []
  const localeHomeRoute = routeForLocale('/', locale)
  const isHome = route === localeHomeRoute
  const schemaType = isHome
    ? 'WebPage'
    : pageSchemaType(context, route, locale)
  const isArticle = schemaType === 'TechArticle'

  const head: HeadConfig[] = [
    ['link', { rel: 'canonical', href: canonical }],
    ['meta', { name: 'description', content: context.description }],
    [
      'meta',
      {
        name: 'robots',
        content: indexable
          ? 'index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1'
          : 'noindex,follow',
      },
    ],
    ['meta', { property: 'og:type', content: isArticle ? 'article' : 'website' }],
    ['meta', { property: 'og:site_name', content: 'N.E.K.O. Docs' }],
    ['meta', { property: 'og:title', content: context.title }],
    ['meta', { property: 'og:description', content: context.description }],
    ['meta', { property: 'og:url', content: canonical }],
    ['meta', { property: 'og:image', content: LOGO_URL }],
    ['meta', { property: 'og:image:alt', content: 'Project N.E.K.O. logo' }],
    ['meta', { property: 'og:locale', content: locale.ogLocale }],
    ['meta', { name: 'twitter:card', content: 'summary' }],
    ['meta', { name: 'twitter:title', content: context.title }],
    ['meta', { name: 'twitter:description', content: context.description }],
    ['meta', { name: 'twitter:image', content: LOGO_URL }],
  ]

  for (const alternate of alternates) {
    head.push([
      'link',
      {
        rel: 'alternate',
        hreflang: alternate.locale.hreflang,
        href: alternate.url,
      },
    ])
    if (alternate.locale.key !== locale.key) {
      head.push([
        'meta',
        {
          property: 'og:locale:alternate',
          content: alternate.locale.ogLocale,
        },
      ])
    }
  }

  const englishAlternate = alternates.find(
    (alternate) => alternate.locale.key === 'en',
  )
  if (englishAlternate) {
    head.push([
      'link',
      {
        rel: 'alternate',
        hreflang: 'x-default',
        href: englishAlternate.url,
      },
    ])
  }

  if (isArticle && context.pageData.lastUpdated) {
    head.push([
      'meta',
      {
        property: 'article:modified_time',
        content: new Date(context.pageData.lastUpdated).toISOString(),
      },
    ])
  }

  if (indexable) {
    head.push(
      jsonLdHead(
        isHome
          ? homeStructuredData(context, canonical, locale)
          : pageStructuredData(
              context,
              route,
              canonical,
              locale,
              availableRoutes,
              schemaType,
            ),
      ),
    )
  }

  return head
}
