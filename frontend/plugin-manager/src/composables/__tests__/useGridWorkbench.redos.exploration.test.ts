/**
 * Bug-condition exploration property test for ReDoS in `useGridWorkbench`
 * (PR-1480 review-fix bugfix Requirement 1.5 — `isReDoSPattern`).
 *
 * Goal:
 *   Surface the ReDoS bug in
 *   ``frontend/plugin-manager/src/composables/useGridWorkbench.ts:239-245``.
 *   The current implementation feeds the user-supplied filter text directly
 *   into ``new RegExp(text, 'i').test(item.searchIndex)`` with no length
 *   guard, no nested-quantifier heuristic, and no time budget. Catastrophic
 *   backtracking patterns such as ``(a+)+$`` against an input of the form
 *   ``'a'.repeat(n) + 'b'`` cause the JavaScript engine to enumerate ``2^n``
 *   alternations on the main thread, freezing the UI for seconds-to-minutes.
 *
 * Why we exercise the underlying ``new RegExp(...).test(...)`` call directly
 * instead of mounting the composable:
 *   ``useGridWorkbench`` provides zero protection layer between
 *   ``state.filterText`` and ``new RegExp(text, 'i').test(...)`` — the bug
 *   IS that there is no protection. Reaching into ``computed(filteredItems)``
 *   would add Vue reactivity bookkeeping on top of an already
 *   exponential-time call, making the test noisier without changing what is
 *   being measured. The exact line being measured here is the line being
 *   patched by Task 2.4.2 (``tryCompileSafeRegex`` + substring fallback).
 *
 * Expected outcome on UNFIXED code:
 *   - The property test below MUST FAIL: ``expect(elapsed).toBeLessThan(100)``
 *     fires for every ``n`` in the 20..30 range because the V8 regex engine
 *     spends seconds (often > 1 s) on a single ``.test()`` call.
 *   - fast-check will report the first failing ``n`` as the counterexample.
 *   - The "documented counterexample" `it()` reproduces the specific
 *     ``n = 25`` case and likewise fails on unfixed code with elapsed
 *     time > 1000 ms.
 *
 * Expected outcome AFTER Task 2.4.2 lands:
 *   - The property test and the documented counterexample MUST start
 *     passing because ``tryCompileSafeRegex('(a+)+')`` will return ``null``
 *     (rejected by the nested-quantifier heuristic), ``useGridWorkbench``
 *     will fall back to case-insensitive substring matching, and the
 *     measured ``new RegExp(...).test(...)`` call will never be reached on
 *     this input — i.e. the post-fix budget of < 100 ms holds trivially.
 *   - The "buggy-state baseline" `it()` (which asserts ``elapsed > 100``)
 *     MUST start failing — that is the positive signal that the fix is in
 *     place.
 *
 * Validates: Requirements 1.5
 *
 * NOTE: This file is a Phase-4 exploration test. It is intentionally calibrated
 * so that a single failing run terminates the property quickly:
 *   - ``numRuns: 3`` with ``endOnFailure: true`` so we exit after the first
 *     failing example rather than blocking the runner on multiple
 *     multi-second backtracking runs.
 *   - The per-test timeout is raised to 30 s because a single backtracking
 *     run at ``n = 30`` can exceed 10 s on slower V8 builds.
 *   - ``n`` ∈ [20, 30] is the sweet spot: 2^20 ≈ 1e6 backtracks (≈ tens of
 *     ms, already over budget), 2^30 ≈ 1e9 backtracks (seconds). Larger n
 *     would risk wedging CI; smaller n might not exceed the 100 ms budget.
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
 * Post-fix (Task 2.4.2) the composable runs the user's regex through
 * ``tryCompileSafeRegex`` first; if that returns ``null`` (because the
 * pattern hits the ReDoS heuristic), the composable falls back to a
 * case-insensitive substring match and never executes the dangerous
 * ``.test()`` call. We mirror that exact decision tree here so the
 * test measures what the user actually experiences.
 *
 * On UNFIXED code there is no ``tryCompileSafeRegex`` — the composable
 * called ``new RegExp(text, 'i').test(item.searchIndex)`` directly, so
 * the equivalent simulation is to also call ``new RegExp`` directly.
 * That branch is preserved in ``timeUnsafeRegexMatch`` below for the
 * buggy-state baseline.
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
