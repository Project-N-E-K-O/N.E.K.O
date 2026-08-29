import { describe, expect, it } from 'vitest'

import {
  deriveMarketPluginAction,
  fetchInstalledProjection,
  inferManualInstallConflict,
} from './marketPluginInstallState'

describe('deriveMarketPluginAction', () => {
  it('offers a safe override when the builtin version is older', () => {
    expect(
      deriveMarketPluginAction(
        {
          plugin_id: 'study_companion',
          effective_source: 'builtin',
          effective_version: '0.1.5',
          market_installed: false,
          builtin_version: '0.1.5',
          latest_market_version: '0.1.6',
        },
        '0.1.6',
        true
      )
    ).toMatchObject({
      kind: 'override_builtin',
      currentVersion: '0.1.5',
      targetVersion: '0.1.6',
      installed: true,
    })
  })

  it('keeps an equal builtin release disabled', () => {
    expect(
      deriveMarketPluginAction(
        {
          plugin_id: 'study_companion',
          effective_source: 'builtin',
          effective_version: '0.1.6',
          latest_market_version: '0.1.6',
        },
        '0.1.6',
        true
      ).kind
    ).toBe('builtin')
  })

  it('upgrades an older Market override and disables the latest one', () => {
    const state = {
      plugin_id: 'study_companion',
      effective_source: 'market',
      effective_version: '0.1.6',
      market_installed: true,
      latest_market_version: '0.1.7',
    }
    expect(deriveMarketPluginAction(state, '0.1.7', true).kind).toBe('upgrade')
    expect(
      deriveMarketPluginAction(
        {
          ...state,
          effective_version: '0.1.7',
        },
        '0.1.7',
        true
      ).kind
    ).toBe('installed')
  })

  it('prefers the catalog latest version over a stale local projection', () => {
    expect(deriveMarketPluginAction({
      plugin_id: 'study_companion',
      effective_source: 'market',
      effective_version: '0.1.6',
      market_installed: true,
      latest_market_version: '0.1.6',
    }, '0.1.7', true)).toMatchObject({ kind: 'upgrade', targetVersion: '0.1.7' })
  })

  it('blocks a manual user installation instead of replacing it', () => {
    expect(
      deriveMarketPluginAction(
        {
          plugin_id: 'study_companion',
          effective_source: 'manual',
          effective_version: '0.1.5',
          market_installed: false,
          latest_market_version: '0.1.6',
        },
        '0.1.6',
        true
      ).kind
    ).toBe('blocked')
  })

  it('treats a local-only identity match as a manual conflict', () => {
    expect(deriveMarketPluginAction(null, '0.1.6', true, true).kind).toBe('blocked')
  })

  it('preserves compatibility with the legacy latest_install_source projection', () => {
    expect(
      deriveMarketPluginAction(
        {
          plugin_id: 'study_companion',
          latest_install_source: {
            channel: 'stable',
            version: '0.1.5',
            package_sha256: 'abc',
            payload_hash: null,
            package_url: 'https://example.invalid/plugin.zip',
            published_at: '2026-08-24T00:00:00Z',
          },
        },
        '0.1.6',
        true
      )
    ).toMatchObject({ kind: 'upgrade', effectiveSource: 'market' })
  })
})

describe('installed projection loading', () => {
  it('keeps transport failures distinct from a successful empty projection', async () => {
    await expect(fetchInstalledProjection(async () => null)).resolves.toBeNull()
    await expect(
      fetchInstalledProjection(async () => ({ ok: false } as Response))
    ).resolves.toBeNull()
    await expect(
      fetchInstalledProjection(async () => {
        throw new Error('bridge unavailable')
      })
    ).resolves.toBeNull()
    await expect(
      fetchInstalledProjection(
        async () =>
          ({
            ok: true,
            json: async () => ({ installed: [] }),
          }) as Response
      )
    ).resolves.toEqual([])
  })

  it('infers a manual conflict only after the installed projection loaded', () => {
    expect(inferManualInstallConflict(false, undefined, true)).toBe(false)
    expect(inferManualInstallConflict(true, undefined, true)).toBe(true)
    expect(inferManualInstallConflict(true, { plugin_id: 'demo' }, true)).toBe(false)
  })
})
