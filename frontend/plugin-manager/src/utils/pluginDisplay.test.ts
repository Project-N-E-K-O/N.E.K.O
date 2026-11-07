import { describe, expect, it } from 'vitest'
import { findDuplicatePluginDisplayNameIds, resolvePluginDisplayText } from './pluginDisplay'
import type { PluginMeta } from '@/types/api'

function pluginFixture(): PluginMeta {
  return {
    id: 'demo_plugin',
    name: '默认名称',
    description: '默认描述',
    short_description: '默认短描述',
    version: '0.1.0',
    i18n: {
      default_locale: 'zh-CN',
      messages: {
        'zh-CN': {
          'plugin.name': '中文名称',
          'plugin.description': '中文描述',
          'plugin.short_description': '中文短描述',
        },
        en: {
          'plugin.name': 'English Name',
          'plugin.description': 'English description',
          'plugin.short_description': 'English short description',
        },
      },
    },
  }
}

describe('resolvePluginDisplayText', () => {
  it('resolves card text from plugin i18n for the active locale', () => {
    expect(resolvePluginDisplayText(pluginFixture(), 'zh-CN')).toEqual({
      name: '中文名称',
      description: '中文描述',
      shortDescription: '中文短描述',
    })

    expect(resolvePluginDisplayText(pluginFixture(), 'en-US')).toEqual({
      name: 'English Name',
      description: 'English description',
      shortDescription: 'English short description',
    })
  })

  it('falls back to manifest text when plugin i18n does not provide a field', () => {
    const plugin = pluginFixture()
    plugin.i18n = {
      default_locale: 'zh-CN',
      messages: {
        en: {
          'plugin.name': 'English Name',
        },
      },
    }

    expect(resolvePluginDisplayText(plugin, 'en-US')).toEqual({
      name: 'English Name',
      description: '默认描述',
      shortDescription: '默认短描述',
    })
  })

  it('preserves zh-CN fallback for Chinese locales before default_locale', () => {
    const plugin = pluginFixture()
    plugin.name = '后端已解析中文名称'
    plugin.description = '后端已解析中文描述'
    plugin.short_description = '后端已解析中文短描述'
    plugin.i18n = {
      default_locale: 'en',
      messages: {
        'zh-CN': {
          'plugin.name': '简体中文名称',
          'plugin.description': '简体中文描述',
          'plugin.short_description': '简体中文短描述',
        },
        en: {
          'plugin.name': 'English Name',
          'plugin.description': 'English description',
          'plugin.short_description': 'English short description',
        },
      },
    }

    expect(resolvePluginDisplayText(plugin, 'zh-TW')).toEqual({
      name: '简体中文名称',
      description: '简体中文描述',
      shortDescription: '简体中文短描述',
    })
  })

  it('ignores malformed default_locale values instead of throwing', () => {
    const plugin = pluginFixture()
    plugin.i18n = {
      default_locale: 42,
      messages: {
        en: {
          'plugin.name': 'English Name',
          'plugin.description': 'English description',
        },
      },
    } as any

    expect(resolvePluginDisplayText(plugin, 'ko')).toEqual({
      name: 'English Name',
      description: 'English description',
      shortDescription: '默认短描述',
    })
  })
})

describe('findDuplicatePluginDisplayNameIds', () => {
  it('marks different plugin IDs that resolve to the same localized name', () => {
    const original = pluginFixture()
    const renamed = pluginFixture()
    renamed.id = 'demo_plugin_renamed'
    renamed.version = '2.0.0'

    expect(findDuplicatePluginDisplayNameIds([original, renamed], 'zh-CN')).toEqual(
      new Set(['demo_plugin', 'demo_plugin_renamed']),
    )
  })

  it('normalizes harmless visual differences but does not flag a repeated copy of one ID', () => {
    const original = pluginFixture()
    const visuallyEqual = pluginFixture()
    visuallyEqual.id = 'another_plugin'
    visuallyEqual.i18n = undefined
    visuallyEqual.name = '  中文名称  '
    const repeatedOriginal = { ...original }

    expect(
      findDuplicatePluginDisplayNameIds([original, visuallyEqual, repeatedOriginal], 'zh-CN'),
    ).toEqual(new Set(['demo_plugin', 'another_plugin']))
  })

  it('leaves ordinary uniquely named plugins uncluttered', () => {
    const first = pluginFixture()
    const second = pluginFixture()
    second.id = 'another_plugin'
    second.i18n = undefined
    second.name = 'Another plugin'

    expect(findDuplicatePluginDisplayNameIds([first, second], 'zh-CN')).toEqual(new Set())
  })

  it('uses the active UI locale when normalizing duplicate names', () => {
    const capitalI = pluginFixture()
    capitalI.id = 'capital_i'
    capitalI.i18n = undefined
    capitalI.name = 'I'
    const dotlessI = pluginFixture()
    dotlessI.id = 'dotless_i'
    dotlessI.i18n = undefined
    dotlessI.name = 'ı'

    expect(findDuplicatePluginDisplayNameIds([capitalI, dotlessI], 'tr-TR')).toEqual(
      new Set(['capital_i', 'dotless_i']),
    )
  })
})
