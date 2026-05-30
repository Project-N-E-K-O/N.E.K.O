/**
 * Best-effort guard against catastrophic-backtracking regex patterns
 * before they're compiled & ``.test()``-ed on the main thread.
 *
 * Why this helper exists (PR #1480 review-fix bug 1.5)
 * ----------------------------------------------------
 *
 * ``useGridWorkbench`` lets the user type a regex into a search box and
 * runs it directly against every plugin's ``searchIndex`` on every
 * keystroke. V8's regex engine has no time budget; an adversarial
 * pattern like ``(a+)+$`` against an ``'a'.repeat(25) + 'b'`` input
 * takes hundreds of milliseconds to seconds of synchronous wall-clock
 * time, freezing the UI and looking to users like the app crashed.
 *
 * Real-world precedent: Cloudflare's 2019 outage was triggered by a
 * single ``.*(?:.*=.*)`` rule in a WAF that added catastrophic
 * backtracking on a long input.
 *
 * Strategy
 * --------
 *
 * 1. **Length cap** — patterns longer than ``MAX_PATTERN_LEN`` get
 *    rejected. 256 chars is well over what a power user types
 *    interactively; below it, even the worst V8 backtracker stays
 *    within tens of milliseconds for innocent inputs.
 *
 * 2. **Heuristic structural rejection** — three patterns drawn from
 *    the literature (``redos.json``, OWASP ReDoS cheat sheet) cover
 *    the bulk of real catastrophic patterns:
 *      a. ``(...x+...)+`` — group containing a quantifier wrapped by
 *         another quantifier (the canonical ``(a+)+`` form).
 *      b. Two quantifiers in a row (``+*``, ``?+``, ``**``, ...).
 *      c. ``(...)\s*[+*]\s*[+*]`` — group followed by stacked
 *         quantifiers (rarer but observed in the wild).
 *    These are NOT a complete decision procedure (`safe-regex2` does
 *    that, but pulls in a parser-as-dependency we don't want); they
 *    are a tightly bounded heuristic that errs on the side of
 *    "looks suspicious → reject and let the caller fall back to a
 *    plain substring search". False positives degrade UX from
 *    "regex match" to "substring match", which is acceptable; false
 *    negatives stall the main thread for seconds, which is not.
 *
 * 3. **Compile failures** — if ``new RegExp`` throws (syntax error)
 *    we also return ``null``; the caller falls back to substring
 *    matching, same as for the security-rejected case. This unifies
 *    "user typed nonsense" and "user typed something dangerous"
 *    under one safe path.
 *
 * The companion ``warnReDoSOnce`` keeps the dev console clean while
 * still surfacing the rejection — devs investigating "why isn't my
 * regex matching" get one informative line per session, not spam.
 */
const MAX_PATTERN_LEN = 256

// 1. Group with internal quantifier wrapped by an outer quantifier.
//    Matches the canonical (a+)+ / (\w*)+ / (\d?)*  form.
// 2. Two unescaped quantifiers in a row (++, **, +*, ?+, ...).
// 3. Group followed by stacked quantifiers separated by whitespace.
//    Each branch is anchored to NOT match an escaped quantifier
//    by requiring the quantifier to follow a closing paren or
//    word-class character, not a backslash.
const REDOS_HEURISTIC = /(?:\([^()]*[+*?][^()]*\)\s*[+*])|(?:[^\\][+*?]\s*[+*?])|(?:\(.*\)\s*[+*]\s*[+*])/

export function tryCompileSafeRegex(
  pattern: string,
  flags: string = 'i',
): RegExp | null {
  if (!pattern) return null
  if (pattern.length > MAX_PATTERN_LEN) return null
  if (REDOS_HEURISTIC.test(pattern)) return null
  try {
    return new RegExp(pattern, flags)
  } catch {
    return null
  }
}

// Module-level flag so a single ReDoS-rejected query during the user's
// session warns once and then stays quiet. Reset only on full page
// reload — intentional; multiple distinct rejections in a single
// session are far more likely to be the same user retrying than to be
// distinct dangerous inputs we'd want to log separately.
let _warnedThisSession = false

export function warnReDoSOnce(pattern: string): void {
  if (_warnedThisSession) return
  _warnedThisSession = true
  // eslint-disable-next-line no-console
  console.warn(
    '[neko] regex pattern rejected by ReDoS guard, falling back to ' +
      'substring search:',
    pattern.length > 60 ? `${pattern.slice(0, 60)}…` : pattern,
  )
}

// Test-only helper: re-arm the once-flag between vitest cases without
// re-importing the module. NOT exported from the package barrel.
export function _resetReDoSWarningForTests(): void {
  _warnedThisSession = false
}
