import { describe, expect, it } from 'vitest'

import {
  findLocalPluginForMarket,
  resolveMarketCardAction,
} from './marketInstallation'

describe('market installation presentation', () => {
  it('uses the active imported plugin version instead of the Market release version', () => {
    const local = findLocalPluginForMarket(
      {
        slug: 'warthunder',
        github_repo: 'https://github.com/CN-Zephyr/N.E.K.O_plugin_neko_warthunder',
      },
      [
        {
          id: 'neko_warthunder',
          name: '战雷猫娘副驾驶',
          version: '0.1.0',
          install_source: { source: 'imported' },
        },
      ],
    )

    expect(local).toMatchObject({
      id: 'neko_warthunder',
      version: '0.1.0',
      install_source: { source: 'imported' },
    })
  })

  it('offers a Market source switch when an imported copy is older', () => {
    expect(resolveMarketCardAction({
      localInstalled: true,
      marketManaged: false,
      localVersion: '0.1.0',
      marketVersion: '0.1.1',
      hasRelease: true,
    })).toBe('switch_upgrade')
  })

  it('does not label an imported same-version copy as Market-installed', () => {
    expect(resolveMarketCardAction({
      localInstalled: true,
      marketManaged: false,
      localVersion: '0.1.1',
      marketVersion: '0.1.1',
      hasRelease: true,
    })).toBe('switch_source')
  })
})
