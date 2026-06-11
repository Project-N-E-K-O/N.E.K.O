/**
 * Property-based tests for normalizeMarketPlugin (Feature: neko-market-version-sync, Property 4).
 *
 * Validates two invariants:
 *
 * 1. ``raw.latest_version`` is the **only** source of ``download_url``;
 *    ``raw.repo_url`` is never used as a fallback.
 * 2. The ``showUpgrade`` predicate (used by MarketPluginCard) is strictly
 *    equivalent to ``compareVersion(localVersion, latestVersion) < 0``,
 *    and short-circuits to ``false`` when ``latestVersion`` is the empty
 *    string.
 */

import { describe, expect, it } from 'vitest'
import fc from 'fast-check'

import { normalizeMarketPlugin, type LatestVersion } from '@/api/market'
import { compareVersion } from '@/utils/version'

const HEX = '0123456789abcdef'

const dateIsoArb = fc
  .integer({ min: 1_577_836_800_000, max: 4_102_444_800_000 }) // 2020-01-01 ~ 2100-01-01
  .map((ts) => new Date(ts).toISOString())

const latestVersionArb: fc.Arbitrary<LatestVersion> = fc.record({
  version: fc.string({ minLength: 1, maxLength: 32 }),
  channel: fc.constantFrom('stable', 'beta'),
  package_url: fc.webUrl(),
  package_sha256: fc.stringMatching(new RegExp(`^[${HEX}]{64}$`)),
  payload_hash: fc.option(
    fc.stringMatching(new RegExp(`^[${HEX}]{64}$`)),
    { nil: null },
  ),
  created_at: dateIsoArb,
})

const rawArb = fc.record({
  id: fc.integer(),
  slug: fc.option(fc.string({ minLength: 1, maxLength: 16 }), { nil: undefined }),
  name: fc.string({ minLength: 1, maxLength: 24 }),
  author_name: fc.string({ minLength: 1, maxLength: 16 }),
  description: fc.option(fc.string(), { nil: null }),
  short_description: fc.option(fc.string(), { nil: null }),
  repo_url: fc.option(fc.webUrl(), { nil: null }),
  latest_version: fc.option(latestVersionArb, { nil: null }),
  tags: fc.option(fc.array(fc.string({ minLength: 1, maxLength: 8 }), { maxLength: 6 }), { nil: null }),
  download_count: fc.integer({ min: 0, max: 100_000 }),
  likes: fc.integer({ min: 0, max: 100_000 }),
  is_featured: fc.boolean(),
  created_at: dateIsoArb,
  updated_at: dateIsoArb,
})

describe('Property 4: normalizeMarketPlugin', () => {
  it('reads download_url / version solely from latest_version', () => {
    fc.assert(
      fc.property(rawArb, (raw) => {
        // The cast is safe — rawArb structure matches MarketPluginRaw
        // ignoring optional fields the normalizer doesn't touch.
        const out = normalizeMarketPlugin(raw as Parameters<typeof normalizeMarketPlugin>[0])

        if (raw.latest_version === null) {
          // No release → no download URL, no version, has_release=false.
          // repo_url existence MUST NOT influence either.
          expect(out.download_url).toBeUndefined()
          expect(out.version).toBe('')
          expect(out.has_release).toBe(false)
        } else {
          expect(out.download_url).toBe(raw.latest_version.package_url)
          expect(out.version).toBe(raw.latest_version.version)
          expect(out.has_release).toBe(true)
          expect(out.latest_channel).toBe(raw.latest_version.channel)
          expect(out.latest_package_sha256).toBe(raw.latest_version.package_sha256)
          expect(out.latest_payload_hash).toBe(raw.latest_version.payload_hash)
          expect(out.latest_published_at).toBe(raw.latest_version.created_at)
        }

        // github_repo always reflects raw.repo_url (display only)
        expect(out.github_repo).toBe(raw.repo_url ?? undefined)
      }),
      { numRuns: 200 },
    )
  })
})

/**
 * Mirror of MarketPluginCard's ``showUpgrade`` computed for property
 * verification — keep these two definitions structurally identical.
 */
function showUpgrade(opts: {
  installed: boolean
  localVersion: string | undefined
  latestVersion: string
  hasRelease: boolean
}): boolean {
  if (!opts.installed) return false
  if (!opts.localVersion || !opts.latestVersion) return false
  if (!opts.hasRelease) return false
  return compareVersion(opts.localVersion, opts.latestVersion) < 0
}

describe('Property 4: showUpgrade vs compareVersion', () => {
  const semverArb = fc
    .tuple(fc.nat(20), fc.nat(20), fc.nat(20))
    .map(([a, b, c]) => `${a}.${b}.${c}`)

  it('matches semver compare when installed + has release', () => {
    fc.assert(
      fc.property(semverArb, semverArb, (local, latest) => {
        const expected = compareVersion(local, latest) < 0
        expect(
          showUpgrade({
            installed: true,
            localVersion: local,
            latestVersion: latest,
            hasRelease: true,
          }),
        ).toBe(expected)
      }),
      { numRuns: 200 },
    )
  })

  it('returns false when latest is empty', () => {
    fc.assert(
      fc.property(semverArb, (local) => {
        expect(
          showUpgrade({
            installed: true,
            localVersion: local,
            latestVersion: '',
            hasRelease: false,
          }),
        ).toBe(false)
      }),
      { numRuns: 100 },
    )
  })

  it('returns false when not installed', () => {
    fc.assert(
      fc.property(semverArb, semverArb, (local, latest) => {
        expect(
          showUpgrade({
            installed: false,
            localVersion: local,
            latestVersion: latest,
            hasRelease: true,
          }),
        ).toBe(false)
      }),
      { numRuns: 100 },
    )
  })
})
