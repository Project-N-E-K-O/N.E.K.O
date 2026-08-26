import { describe, expect, it } from 'vitest'

import {
  indexInstalledPluginIdentities,
  localPluginIdentityKeys,
  marketIdentityKeys,
  marketLocalIdentityKeys,
  marketRecordIdentityKeys,
} from './marketPluginIdentity'

describe('market plugin identity matching', () => {
  it('uses stable Market identities and excludes the display name', () => {
    const plugin = {
      id: 42,
      rawId: 42,
      slug: 'study-companion',
      github_repo: 'https://github.com/example/N.E.K.O_plugin_study_companion',
      name: 'unrelated_local_id',
    }
    const keys = marketIdentityKeys(plugin)

    expect(keys).toEqual(['study-companion', '42', 'study_companion'])
    expect(keys).not.toContain('unrelated_local_id')
    expect(marketLocalIdentityKeys(plugin)).toEqual(['study-companion', 'study_companion'])
    expect(marketLocalIdentityKeys(plugin)).not.toContain('42')
  })

  it('indexes local plugins by runtime id and excludes the display name', () => {
    const keys = localPluginIdentityKeys([{ id: 'actual_plugin_id', name: 'study-companion' }])

    expect(keys).toEqual(new Set(['actual_plugin_id']))
    expect(keys.has('study-companion')).toBe(false)
  })

  it('keeps runtime plugin ids separate from Market record ids', () => {
    const manual = { plugin_id: '42', latest_install_source: null }
    const market = {
      plugin_id: 'study_companion',
      latest_install_source: { plugin_market_id: '42' },
    }
    const indexes = indexInstalledPluginIdentities([manual, market])

    expect(indexes.byPluginId.get('42')).toBe(manual)
    expect(indexes.byMarketId.get('42')).toBe(market)
    expect(marketRecordIdentityKeys({ id: 42, rawId: 42 })).toEqual(['42'])
  })
})
