import { describe, expect, it } from 'vitest'
import { buildPluginStaticUiUrl, withStaticUiLocale } from './staticUiUrl'

describe('static plugin UI URL helpers', () => {
  it('adds the manager locale to the galgame static UI URL', () => {
    expect(buildPluginStaticUiUrl('galgame_plugin', 123, 'en-US')).toBe(
      '/plugin/galgame_plugin/ui/?_ui=123&locale=en-US',
    )
  })

  it('adds the manager locale to every plugin static UI URL (panel iframe must reload on language change)', () => {
    expect(buildPluginStaticUiUrl('mijia', 123, 'en-US')).toBe(
      '/plugin/mijia/ui/?_ui=123&locale=en-US',
    )
    expect(buildPluginStaticUiUrl('bilibili_danmaku', 456, 'ja')).toBe(
      '/plugin/bilibili_danmaku/ui/?_ui=456&locale=ja',
    )
  })

  it('omits the locale query when locale is empty', () => {
    expect(buildPluginStaticUiUrl('mijia', 123, '')).toBe('/plugin/mijia/ui/?_ui=123')
  })

  it('preserves existing static surface query params when adding locale', () => {
    expect(withStaticUiLocale('/plugin/galgame_plugin/ui/?_ui=abc', 'ja')).toBe(
      '/plugin/galgame_plugin/ui/?_ui=abc&locale=ja',
    )
  })

  it('preserves hash routes when adding locale', () => {
    expect(withStaticUiLocale('/plugin/galgame_plugin/ui/?_ui=abc#/route?panel=1', 'ja')).toBe(
      '/plugin/galgame_plugin/ui/?_ui=abc&locale=ja#/route?panel=1',
    )
  })

  it('returns input unchanged when locale is empty', () => {
    expect(withStaticUiLocale('/plugin/demo/ui/?_ui=abc', '')).toBe('/plugin/demo/ui/?_ui=abc')
  })
})
