/**
 * Bug-condition exploration property test for ``compareVersion``
 * (PR-1480 review-fix bugfix Requirement 1.9 — `isPrereleaseVersionEq`).
 *
 * Goal:
 *   Surface the prerelease-equality bug in
 *   ``frontend/plugin-manager/src/utils/version.ts``. The current
 *   implementation does
 *
 *       v.split(/[.\-+]/).map((n) => parseInt(n, 10) || 0)
 *
 *   which collapses every non-numeric prerelease segment (e.g. ``rc1``,
 *   ``beta``, ``alpha``) to ``0``. As a result the parsed core arrays
 *   ``[1, 0, 0, 0]`` (from ``1.0.0-rc1``) and ``[1, 0, 0]`` (from
 *   ``1.0.0``) compare equal, contradicting the file's own docstring
 *   which claims ``1.0.0-rc1 < 1.0.0``.
 *
 * Expected outcome on UNFIXED code:
 *   - The property test below MUST FAIL.
 *   - The fast-check counterexample will be a member of
 *     ``{'rc1','rc.1','beta','beta.2','alpha'}`` for which
 *     ``compareVersion('1.0.0-${pre}', '1.0.0')`` is ``>= 0``
 *     instead of strictly negative.
 *   - The "documented counterexample" `it()` directly asserts the
 *     specific case ``compareVersion('1.0.0-rc1', '1.0.0') < 0`` and
 *     also fails on unfixed code (returns ``0``).
 *
 * The third "buggy-state baseline" `it()` documents the present
 * (incorrect) behavior so the contradiction with the docstring is
 * captured in the test suite itself.
 *
 * Validates: Requirements 1.9
 *
 * NOTE: This file is a Phase-2 exploration test. It is NOT meant to
 * stay green forever — once Requirement 2.9's fix lands, both the
 * property test and the documented counterexample MUST start passing
 * and the buggy-state baseline MUST start failing (signalling that the
 * fix has taken effect).
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
        // On unfixed code this assertion fails because parseInt(<tag>) || 0
        // collapses non-numeric prerelease segments to 0, making
        // [1, 0, 0, ...] compare equal (or even greater) to [1, 0, 0].
        expect(compareVersion(`1.0.0-${pre}`, '1.0.0')).toBeLessThan(0)
      }),
      { numRuns: 200 },
    )
  })

  it('documented counterexample: compareVersion("1.0.0-rc1", "1.0.0") < 0', () => {
    // Specific failing case captured for the bugfix log:
    //   on unfixed code parseInt('rc1', 10) || 0 → 0,
    //   so split arrays become [1, 0, 0, 0] vs [1, 0, 0]
    //   → compareVersion returns 0 (NOT < 0).
    expect(compareVersion('1.0.0-rc1', '1.0.0')).toBeLessThan(0)
  })

  it('buggy-state baseline: compareVersion("1.0.0-rc1", "1.0.0") currently returns 0 (THE BUG)', () => {
    // This test passes on the CURRENT (unfixed) implementation and
    // documents the contradiction with version.ts's own docstring
    // ("1.0.0-rc1 < 1.0.0"). Once Requirement 2.9 lands this assertion
    // will start failing — that is the expected positive signal that
    // the fix is in place.
    expect(compareVersion('1.0.0-rc1', '1.0.0')).toBe(0)
  })
})
