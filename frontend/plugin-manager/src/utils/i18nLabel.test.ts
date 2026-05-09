import { describe, expect, it } from 'vitest'
import { resolveLocalizedText, resolvePluginI18nMessage } from './i18nLabel'

describe('resolvePluginI18nMessage', () => {
  it('uses plugin i18n messages for the current locale', () => {
    const i18n = {
      default_locale: 'zh-CN',
      messages: {
        'zh-CN': { 'plugin.name': 'Galgame 游玩助手' },
        en: { 'plugin.name': 'Galgame Play Assistant' },
        ja: { 'plugin.name': 'ギャルゲームプレイアシスタント' },
      },
    }

    expect(resolvePluginI18nMessage(i18n, 'plugin.name', 'zh-CN', 'fallback')).toBe('Galgame 游玩助手')
    expect(resolvePluginI18nMessage(i18n, 'plugin.name', 'en-US', 'fallback')).toBe('Galgame Play Assistant')
    expect(resolvePluginI18nMessage(i18n, 'plugin.name', 'ja', 'fallback')).toBe(
      'ギャルゲームプレイアシスタント',
    )
  })

  it('falls back through primary and default locales', () => {
    const i18n = {
      default_locale: 'zh-CN',
      messages: {
        ja: { 'plugin.description': '説明' },
        'zh-CN': { 'plugin.description': '默认说明' },
      },
    }

    expect(resolvePluginI18nMessage(i18n, 'plugin.description', 'ja-JP', 'fallback')).toBe('説明')
    expect(resolvePluginI18nMessage(i18n, 'plugin.description', 'ko', 'fallback')).toBe('默认说明')
    expect(resolvePluginI18nMessage(i18n, 'plugin.name', 'ko', 'fallback')).toBe('fallback')
  })
})

describe('resolveLocalizedText', () => {
  it('uses exact locale matches and primary locale fallbacks', () => {
    const text = {
      'en-US': 'American English',
      en: 'English',
      ja: 'Japanese',
    }

    expect(resolveLocalizedText(text, 'en-US', 'fallback')).toBe('American English')
    expect(resolveLocalizedText(text, 'en-GB', 'fallback')).toBe('English')
    expect(resolveLocalizedText(text, 'ja-JP', 'fallback')).toBe('Japanese')
  })

  it('returns the provided fallback when no localized value exists', () => {
    expect(resolveLocalizedText({ 'en-US': 'American English', en: 'English' }, 'fr-FR', 'fallback')).toBe(
      'English',
    )
    expect(resolveLocalizedText({ 'en-US': 'American English' }, 'fr-FR', 'fallback')).toBe('American English')
    expect(resolveLocalizedText({ ja: 'Japanese', de: 'German' }, 'fr-FR', 'fallback')).toBe('Japanese')
    expect(resolveLocalizedText({}, 'fr-FR', 'fallback')).toBe('fallback')
    expect(resolveLocalizedText(null, 'fr-FR', 'fallback')).toBe('fallback')
  })
})
