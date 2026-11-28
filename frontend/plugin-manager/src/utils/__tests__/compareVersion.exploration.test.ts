/**
 * Regression tests for ``compareVersion``
 * (PR-1480 review-fix Requirement 1.9 — `isPrereleaseVersionEq`).
 *
 * Goal:
 *   Keep prerelease tags ordered before their matching release. A prior
 *   implementation parsed all segments with ``parseInt(n, 10) || 0``, which
 *   collapsed tags such as ``rc1``, ``beta``, and ``alpha`` to ``0`` and made
 *   ``1.0.0-rc1`` compare equal to ``1.0.0``.
 *
 * Current expectations:
 *   - Every listed prerelease tag sorts before ``1.0.0``; ``rc1`` is the
 *     original ``1.0.0-rc1`` counterexample.
 *
 * Validates: Requirements 1.9
 */

import { describe, expect, it } from 'vitest'

import { compareVersion } from '@/utils/version'

const PRERELEASE_TAGS = ['rc1', 'rc.1', 'beta', 'beta.2', 'alpha'] as const

describe('compareVersion prerelease ordering (1.9)', () => {
  it.each(PRERELEASE_TAGS)('1.0.0-%s sorts before 1.0.0', (pre) => {
    expect(compareVersion(`1.0.0-${pre}`, '1.0.0')).toBeLessThan(0)
  })
})
