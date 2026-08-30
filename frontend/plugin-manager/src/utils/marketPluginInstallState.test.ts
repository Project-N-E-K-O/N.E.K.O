import { describe, expect, it } from 'vitest'

import {
  deriveMarketPluginAction,
  fetchInstalledProjection,
  inferUnresolvedLocalConflict,
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

  it('offers a confirmed replacement for a manual user installation', () => {
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
      )
    ).toMatchObject({ kind: 'upgrade', effectiveSource: 'manual' })
  })

  it('blocks a local-only identity match with unknown ownership', () => {
    expect(deriveMarketPluginAction(null, '0.1.6', true, true)).toMatchObject({
      kind: 'blocked',
      effectiveSource: 'unknown',
      installed: true,
    })
  })

  it.each(['imported', 'unknown'])(
    'blocks %s user ownership instead of offering manual takeover',
    (effectiveSource) => {
      expect(
        deriveMarketPluginAction(
          {
            plugin_id: 'study_companion',
            effective_source: effectiveSource,
            effective_version: '0.1.5',
          },
          '0.1.6',
          true
        )
      ).toMatchObject({
        kind: 'blocked',
        effectiveSource,
        installed: true,
      })
    }
  )

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

  it('infers an unresolved local conflict only after the installed projection loaded', () => {
    expect(inferUnresolvedLocalConflict(false, undefined, true)).toBe(false)
    expect(inferUnresolvedLocalConflict(true, undefined, true)).toBe(true)
    expect(inferUnresolvedLocalConflict(true, { plugin_id: 'demo' }, true)).toBe(false)
  })
})
