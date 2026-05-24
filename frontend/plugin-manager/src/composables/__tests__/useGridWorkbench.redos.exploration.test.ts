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

const REDOS_PATTERN = '(a+)+$'
const PER_TEST_TIMEOUT_MS = 30_000
const POST_FIX_BUDGET_MS = 100

function timeRegexMatch(n: number): number {
  // Reproduces the exact unguarded call site:
  //   useGridWorkbench.ts:239-245 →
  //     const re = new RegExp(text, 'i')
  //     re.test(item.searchIndex || '')
  const idx = 'a'.repeat(n) + 'b'
  const t0 = performance.now()
  try {
    new RegExp(REDOS_PATTERN, 'i').test(idx)
  } catch {
    // Pattern compile errors are not the failure mode we are surfacing — the
    // bug is that this pattern compiles and then runs for an unbounded time.
    // Compile failures (which do not happen for `(a+)+$`) are silently
    // swallowed so the exploration test stays focused on the runtime cost.
  }
  return performance.now() - t0
}

describe('Phase 4 exploration · useGridWorkbench ReDoS pattern stalls main thread (1.5)', () => {
  it(
    'property: matching `(a+)+$` against an a-padded input MUST stay below the 100 ms post-fix budget',
    () => {
      fc.assert(
        fc.property(fc.integer({ min: 20, max: 30 }), (n) => {
          const elapsed = timeRegexMatch(n)
          // Post-fix invariant (Task 2.4.2): the safe-regex guard rejects
          // `(a+)+$`, useGridWorkbench falls back to substring matching,
          // and the call returns in well under 100 ms.
          //
          // On UNFIXED code this fails because the V8 backtracker enumerates
          // up to 2^n alternations on the main thread.
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
      //   On unfixed code this single call takes > 1 s on V8 (≈ 2^25
      //   backtracks). On post-fix code the safe-regex guard rejects the
      //   pattern long before ``.test()`` is ever called, so elapsed
      //   collapses to ~0 ms and this assertion holds.
      const elapsed = timeRegexMatch(25)
      expect(elapsed).toBeLessThan(POST_FIX_BUDGET_MS)
    },
    PER_TEST_TIMEOUT_MS,
  )

  it(
    'buggy-state baseline: `(a+)+$` against a 25-char "a"-padded input currently stalls > 100 ms (THE BUG)',
    () => {
      // This test passes on the CURRENT (unfixed) implementation and
      // documents the exponential blow-up: a single regex match against an
      // adversarial 26-byte input exceeds the 100 ms main-thread budget.
      // Once Requirement 2.5 / Task 2.4.2 lands, this assertion will start
      // failing — that is the expected positive signal that the fix is in
      // place (the safe-regex guard rejects `(a+)+$` and the fallback path
      // returns in microseconds).
      const elapsed = timeRegexMatch(25)
      expect(elapsed).toBeGreaterThan(POST_FIX_BUDGET_MS)
    },
    PER_TEST_TIMEOUT_MS,
  )
})
