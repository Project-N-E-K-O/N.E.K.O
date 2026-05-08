import { describe, expect, it } from 'vitest'
import { buildPluginStaticUiUrl, withGalgameStaticUiLocale } from './staticUiUrl'

describe('static plugin UI URL helpers', () => {
  it('adds the manager locale to the Galgame static UI URL', () => {
    expect(buildPluginStaticUiUrl('galgame_plugin', 123, 'en-US')).toBe(
      '/plugin/galgame_plugin/ui/?_ui=123&locale=en-US',
    )
  })

  it('does not add Galgame-specific locale params to other plugin UI URLs', () => {
    expect(buildPluginStaticUiUrl('mijia', 123, 'en-US')).toBe('/plugin/mijia/ui/?_ui=123')
  })

  it('preserves existing static surface query params when adding Galgame locale', () => {
    expect(withGalgameStaticUiLocale('/plugin/galgame_plugin/ui/?_ui=abc', 'galgame_plugin', 'ja')).toBe(
      '/plugin/galgame_plugin/ui/?_ui=abc&locale=ja',
    )
  })

  it('does not change non-Galgame static surface URLs', () => {
    expect(withGalgameStaticUiLocale('/plugin/demo/ui/?_ui=abc', 'demo', 'ja')).toBe('/plugin/demo/ui/?_ui=abc')
  })
})
