// @vitest-environment happy-dom
/**
 * The authoring, model API, model binding, Market feedback and binding-error
 * words are split into one module per language. These tests pin that a
 * language pack imports its own language only, that every language keeps the
 * same keys, and that switching language loads the right words.
 */
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import {
  SUPPORTED_LOCALES,
  ensurePluginConsentPageMessages,
  i18n,
  setLocale,
  type AppLocale,
} from '@/i18n'

const here = dirname(fileURLToPath(import.meta.url))
const SPLIT_MODULES = {
  authoring: 'authoring',
  modelApi: 'model-api',
  modelBindings: 'model-bindings',
  marketFeedback: 'market-feedback',
} as const
type Section = keyof typeof SPLIT_MODULES
type Messages = Record<string, unknown>

const packs = import.meta.glob<{ default: Messages }>('./locales/*.ts', { eager: true })
const pieces = import.meta.glob<{ default: Messages }>(
  [
    './{authoring,model-api,model-bindings,market-feedback,model-binding-errors,plugin-consent-pages}/*.ts',
    '!./**/index.ts',
  ],
  { eager: true },
)

function piece(dir: string, locale: AppLocale): Messages {
  const mod = pieces[`./${dir}/${locale}.ts`]
  expect(mod, `${dir}/${locale}.ts`).toBeDefined()
  return mod!.default
}
function pack(locale: AppLocale): Messages {
  return packs[`./locales/${locale}.ts`]!.default
}
function keyPaths(value: unknown, prefix = ''): string[] {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return [prefix]
  return Object.entries(value as Messages).flatMap(([key, child]) =>
    keyPaths(child, prefix ? `${prefix}.${key}` : key),
  )
}

describe('per-language message modules', () => {
  it.each(SUPPORTED_LOCALES)('%s pack imports only its own language', (locale) => {
    const source = readFileSync(resolve(here, `locales/${locale}.ts`), 'utf8')
    const imports = [...source.matchAll(/from '\.\.\/([^']+)'/g)].map((match) => match[1]!)
    for (const dir of [...Object.values(SPLIT_MODULES), 'plugin-consent', 'power'])
      expect(imports).toContain(`${dir}/${locale}`)
    // Every relative import must end in this language; a whole-module import
    // (e.g. '../authoring') would ship all eight languages with this pack.
    expect(imports.filter((path) => !path.endsWith(`/${locale}`))).toEqual([])
  })

  it.each(SUPPORTED_LOCALES)('%s pack carries the split modules unchanged', (locale) => {
    const messages = pack(locale)
    for (const [section, dir] of Object.entries(SPLIT_MODULES))
      expect(messages[section], `${locale}.${section}`).toBe(piece(dir, locale))
    for (const dir of ['model-api', 'model-bindings'])
      expect(piece(dir, locale).bindingErrors).toBe(piece('model-binding-errors', locale))
  })

  it.each([...Object.values(SPLIT_MODULES), 'model-binding-errors', 'plugin-consent-pages'])(
    'every language of %s has the en-US key set',
    (dir) => {
      const reference = keyPaths(piece(dir, 'en-US')).sort()
      expect(reference.length).toBeGreaterThan(0)
      for (const locale of SUPPORTED_LOCALES)
        expect(keyPaths(piece(dir, locale)).sort(), `${dir}/${locale}`).toEqual(reference)
    },
  )
})

describe('switching language', () => {
  const probes: Record<Section, string> = {
    authoring: 'title',
    modelApi: 'subtitle',
    modelBindings: 'title',
    marketFeedback: 'issuesTab',
  }

  it('keeps the current words until the new language has loaded', async () => {
    await expect(setLocale('zh-CN')).resolves.toBe(true)
    const t = i18n.global.t as (key: string) => string
    const before = t('authoring.title')
    expect(before).toBe(piece('authoring', 'zh-CN').title)
    const switching = setLocale('ja')
    expect(i18n.global.locale.value).toBe('zh-CN')
    expect(t('authoring.title')).toBe(before)
    await expect(switching).resolves.toBe(true)
    expect(t('authoring.title')).toBe(piece('authoring', 'ja').title)
  })

  it.each(SUPPORTED_LOCALES)('loads the split words for %s on demand', async (locale) => {
    await expect(setLocale(locale)).resolves.toBe(true)
    expect(i18n.global.locale.value).toBe(locale)
    const t = i18n.global.t as (key: string) => string
    for (const [section, key] of Object.entries(probes) as [Section, string][])
      expect(t(`${section}.${key}`), `${locale}.${section}.${key}`).toBe(
        piece(SPLIT_MODULES[section], locale)[key],
      )
    expect(t('modelApi.bindingErrors.conflict')).toBe(piece('model-binding-errors', locale).conflict)
  })
})

describe('permission tab and privacy page words', () => {
  const t = i18n.global.t as (key: string) => string
  const te = i18n.global.te as (key: string, locale?: string) => boolean

  it.each(SUPPORTED_LOCALES)('%s pack leaves them to the routes', (locale) => {
    const consent = pack(locale).pluginConsent as Messages
    expect(consent.tab).toBeUndefined()
    // The sidebar and the route title still need the page title at boot.
    expect(Object.keys(consent.privacy as Messages)).toEqual(['title'])
  })

  // Runs in order: the first case is the only one before any route asked.
  it('are absent until a route asks, then load for the applied language', async () => {
    await expect(setLocale('ja')).resolves.toBe(true)
    expect(te('pluginConsent.tab.loading', 'ja')).toBe(false)
    expect(t('pluginConsent.privacy.title')).toBe(
      (pack('ja').pluginConsent as { privacy: { title: string } }).privacy.title,
    )
    await ensurePluginConsentPageMessages()
    const pages = piece('plugin-consent-pages', 'ja') as { tab: Messages; privacy: Messages }
    expect(t('pluginConsent.tab.loading')).toBe(pages.tab.loading)
    expect(t('pluginConsent.privacy.subtitle')).toBe(pages.privacy.subtitle)
    // The rest of the consent words and the title are kept.
    expect(t('pluginConsent.title')).toBe((pack('ja').pluginConsent as Messages).title)
    expect(t('pluginConsent.privacy.title')).toBe(
      (pack('ja').pluginConsent as { privacy: { title: string } }).privacy.title,
    )
    // The language pack module itself is not changed.
    expect((pack('ja').pluginConsent as Messages).tab).toBeUndefined()
  })

  it.each(SUPPORTED_LOCALES)('stay loaded when switching to %s', async (locale) => {
    await expect(setLocale(locale)).resolves.toBe(true)
    const pages = piece('plugin-consent-pages', locale) as { tab: Messages; privacy: Messages }
    expect(t('pluginConsent.tab.loading')).toBe(pages.tab.loading)
    expect(t('pluginConsent.privacy.subtitle')).toBe(pages.privacy.subtitle)
    expect(t('pluginConsent.privacy.title')).toBe(
      (pack(locale).pluginConsent as { privacy: { title: string } }).privacy.title,
    )
  })
})
