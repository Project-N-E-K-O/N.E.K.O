/**
 * Regression property tests for ``compareVersion``
 * (PR-1480 review-fix Requirement 1.9 — `isPrereleaseVersionEq`).
 *
 * Goal:
 *   Keep prerelease tags ordered before their matching release. A prior
 *   implementation parsed all segments with ``parseInt(n, 10) || 0``, which
 *   collapsed tags such as ``rc1``, ``beta``, and ``alpha`` to ``0`` and made
 *   ``1.0.0-rc1`` compare equal to ``1.0.0``.
 *
 * Current expectations:
 *   - Every sampled prerelease tag sorts before ``1.0.0``.
 *   - The original ``1.0.0-rc1`` counterexample stays pinned as a direct
 *     regression check.
 *
 * Validates: Requirements 1.9
 *
 * NOTE: This file began as a Phase-2 exploration test and now stays green
 * as a regression test for Requirement 2.9's fixed core/pre comparator.
 */

import { describe, expect, it } from 'vitest'
import fc from 'fast-check'

import { compareVersion } from '@/utils/version'

const PRERELEASE_TAGS = ['rc1', 'rc.1', 'beta', 'beta.2', 'alpha'] as const

describe('Phase 2 exploration · compareVersion prerelease ordering (1.9)', () => {
  it('property: every prerelease tag MUST sort before its release counterpart', () => {
    fc.assert(
      fc.property(fc.constantFrom(...PRERELEASE_TAGS), (pre) => {
        // Per the file's own docstring: "1.0.0-rc1 < 1.0.0".
        expect(compareVersion(`1.0.0-${pre}`, '1.0.0')).toBeLessThan(0)
      }),
      { numRuns: 200 },
    )
  })

  it('documented counterexample: compareVersion("1.0.0-rc1", "1.0.0") < 0', () => {
    // Specific bugfix-log counterexample pinned as a regression test.
    expect(compareVersion('1.0.0-rc1', '1.0.0')).toBeLessThan(0)
  })

  it('post-fix baseline: compareVersion("1.0.0-rc1", "1.0.0") now returns < 0', () => {
    // Re-assert the counterexample one more time so a future regression is
    // caught even if the property test gets skipped or thinned out.
    expect(compareVersion('1.0.0-rc1', '1.0.0')).toBeLessThan(0)
  })
})
