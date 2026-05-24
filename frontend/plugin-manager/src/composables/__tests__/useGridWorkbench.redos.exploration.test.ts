/**
 * Regression property tests for ReDoS protection in `useGridWorkbench`
 * (PR-1480 review-fix Requirement 1.5 — `isReDoSPattern`).
 *
 * Goal:
 *   Keep the fixed filter path fast when users enter catastrophic
 *   backtracking patterns such as ``(a+)+$``. A prior implementation fed the
 *   user-supplied filter text directly into
 *   ``new RegExp(text, 'i').test(item.searchIndex)``; against an input like
 *   ``'a'.repeat(n) + 'b'`` that sends V8 into exponential backtracking.
 *
 * Test shape:
 *   - ``timeFilterPath`` mirrors the current guarded path:
 *     ``tryCompileSafeRegex`` rejects risky patterns and the composable falls
 *     back to case-insensitive substring matching.
 *   - ``timeUnsafeRegexMatch`` intentionally exercises the raw regex engine
 *     as a baseline, documenting why the guard exists.
 *
 * Current expectations:
 *   - The guarded property and documented counterexample stay below the
 *     100 ms budget.
 *   - The unguarded baseline remains slow for the canonical pattern.
 *   - ``tryCompileSafeRegex`` returns ``null`` for ``(a+)+$``.
 *
 * Validates: Requirements 1.5
 *
 * NOTE: This file remains calibrated for quick feedback:
 *   - ``numRuns: 3`` with ``endOnFailure: true`` avoids repeated
 *     multi-second backtracking runs if the guard regresses.
 *   - The per-test timeout is raised to 30 s because a single unguarded
 *     run at ``n = 30`` can exceed 10 s on slower V8 builds.
 *   - ``n`` ∈ [20, 30] is the range where unguarded matching reliably
 *     demonstrates the hazard without wedging CI.
 */

import { describe, expect, it } from 'vitest'
import fc from 'fast-check'

import { tryCompileSafeRegex } from '@/utils/safeRegex'

const REDOS_PATTERN = '(a+)+$'
const PER_TEST_TIMEOUT_MS = 30_000
const POST_FIX_BUDGET_MS = 100

/**
 * Reproduce the on-screen filter path used by ``useGridWorkbench``.
 *
 * The composable runs the user's regex through ``tryCompileSafeRegex`` first;
 * if that returns ``null`` (because the pattern hits the ReDoS heuristic), the
 * composable falls back to a case-insensitive substring match and never
 * executes the dangerous ``.test()`` call. We mirror that decision tree here
 * so the test measures what the user actually experiences.
 */
function timeFilterPath(n: number): number {
  const idx = 'a'.repeat(n) + 'b'
  const t0 = performance.now()
  const re = tryCompileSafeRegex(REDOS_PATTERN, 'i')
  if (re) {
    re.test(idx)
  } else {
    // Substring fallback — same as useGridWorkbench's fallback branch.
    idx.toLowerCase().includes(REDOS_PATTERN.toLowerCase())
  }
  return performance.now() - t0
}

function timeUnsafeRegexMatch(n: number): number {
  // Pre-fix simulation: no guard, straight to V8's backtracker.
  const idx = 'a'.repeat(n) + 'b'
  const t0 = performance.now()
  try {
    new RegExp(REDOS_PATTERN, 'i').test(idx)
  } catch {
    // Compile errors are not the failure mode we are surfacing.
  }
  return performance.now() - t0
}

describe('Phase 4 exploration · useGridWorkbench ReDoS pattern stalls main thread (1.5)', () => {
  it(
    'property: matching `(a+)+$` against an a-padded input MUST stay below the 100 ms post-fix budget',
    () => {
      fc.assert(
        fc.property(fc.integer({ min: 20, max: 30 }), (n) => {
          const elapsed = timeFilterPath(n)
          // Post-fix invariant (Task 2.4.2): the safe-regex guard rejects
          // `(a+)+$`, useGridWorkbench falls back to substring matching,
          // and the call returns in well under 100 ms.
          expect(elapsed).toBeLessThan(POST_FIX_BUDGET_MS)
        }),
        { numRuns: 3, endOnFailure: true },
      )
    },
    PER_TEST_TIMEOUT_MS,
  )

  it(
    'documented counterexample: `(a+)+$` against a 25-char "a"-padded input completes in < 100 ms',
    () => {
      // Specific failing case captured for the bugfix log:
      //   n = 25 → searchIndex = 'a'.repeat(25) + 'b'
      //   On post-fix code the safe-regex guard rejects the pattern
      //   long before ``.test()`` is ever called, so elapsed collapses
      //   to ~0 ms and this assertion holds.
      const elapsed = timeFilterPath(25)
      expect(elapsed).toBeLessThan(POST_FIX_BUDGET_MS)
    },
    PER_TEST_TIMEOUT_MS,
  )

  it(
    'unguarded baseline still confirms the underlying engine bug exists',
    () => {
      // Sanity check: even after the safe-regex guard ships, V8 still
      // has the catastrophic-backtracking behaviour we were guarding
      // against. If a future engine update made `(a+)+$` cheap, this
      // assertion would flip and we'd want to revisit whether the
      // guard is still needed. Until then it pins the rationale.
      const elapsed = timeUnsafeRegexMatch(25)
      expect(elapsed).toBeGreaterThan(POST_FIX_BUDGET_MS)
    },
    PER_TEST_TIMEOUT_MS,
  )

  it('safe-regex guard returns null for the canonical ReDoS pattern', () => {
    // Direct unit-level pin: independently of timing, the guard MUST
    // refuse to compile `(a+)+$`. Reproducible without timing flakes
    // on slower CI runners.
    expect(tryCompileSafeRegex(REDOS_PATTERN, 'i')).toBeNull()
  })
})
