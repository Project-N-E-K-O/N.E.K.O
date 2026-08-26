import { describe, expect, it } from 'vitest'

import { localPluginIdentityKeys, marketIdentityKeys } from './marketPluginIdentity'

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
  })

  it('indexes local plugins by runtime id and excludes the display name', () => {
    const keys = localPluginIdentityKeys([{ id: 'actual_plugin_id', name: 'study-companion' }])

    expect(keys).toEqual(new Set(['actual_plugin_id']))
    expect(keys.has('study-companion')).toBe(false)
  })
})
