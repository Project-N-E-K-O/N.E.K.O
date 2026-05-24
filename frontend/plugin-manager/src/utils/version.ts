/**
 * Lightweight semver-ish version comparison.
 *
 * Strips a leading ``v`` or ``V`` and ``+build`` metadata, then splits
 * each side into ``core`` (the dot-separated numeric prefix) and
 * ``pre`` (the dash-separated prerelease suffix). Comparison rules:
 *
 *   1. Compare ``core`` segments numerically, missing segments treated
 *      as ``0`` (so ``1.0`` ≡ ``1.0.0``).
 *   2. If ``core`` is equal:
 *      - no prerelease > with prerelease, i.e. ``1.0.0 > 1.0.0-rc1``
 *        (this matches semver §11.4 and the doc-comment on the
 *         ``compareVersion`` v1 implementation).
 *      - both have prerelease: compare segment-by-segment
 *        ``pre.split('.')``, where each segment is numeric → numeric
 *        compare; alphabetic → lexicographic compare; mixed → numeric
 *        sorts BEFORE alphabetic (per semver §11.4.3).
 *      - shorter prerelease wins on equal prefixes (per semver §11.4.4),
 *        so ``1.0.0-rc < 1.0.0-rc.1``.
 *   3. Build metadata (``+...``) is ignored.
 *
 * Why this rewrite (PR #1480 review-fix bug 1.9): the v1 implementation
 * collapsed every non-numeric segment to ``0`` via ``parseInt(...) || 0``,
 * which made ``1.0.0-rc1`` compare equal to ``1.0.0`` and silently
 * suppressed every "prerelease → release" upgrade prompt across
 * ``MarketPluginCard.showUpgrade``, ``PluginCard``/``PluginListRow``
 * update badges, and ``hasNewerVersion``. See bugfix.md §1.9 / §2.9.
 *
 * Preservation guarantee for triple-numeric inputs (e.g. ``1.2.3`` vs
 * ``1.2.4``, ``v2.0.0`` vs ``1.9.9``): the new implementation returns
 * the same sign as the v1 implementation, since both reduce to the
 * core-segment numeric comparison in step (1). Property test in
 * ``__tests__/compareVersion.preserve.test.ts`` (Phase 4 task 4.2) will
 * lock that.
 */
function splitVersion(v: string): { core: number[]; pre: string[] } {
  const cleaned = String(v).replace(/^v/i, '').replace(/\+.*$/, '')
  const dashIdx = cleaned.indexOf('-')
  const core = dashIdx === -1 ? cleaned : cleaned.slice(0, dashIdx)
  const pre = dashIdx === -1 ? '' : cleaned.slice(dashIdx + 1)
  return {
    core: core.split('.').map((n) => parseInt(n, 10) || 0),
    pre: pre ? pre.split('.') : [],
  }
}

function comparePreSegment(a: string, b: string): number {
  const aIsNum = /^\d+$/.test(a)
  const bIsNum = /^\d+$/.test(b)
  if (aIsNum && bIsNum) {
    const na = parseInt(a, 10)
    const nb = parseInt(b, 10)
    return na === nb ? 0 : na < nb ? -1 : 1
  }
  if (aIsNum) return -1 // numeric identifiers sort before alphabetic (semver §11.4.3)
  if (bIsNum) return 1
  return a === b ? 0 : a < b ? -1 : 1
}

export function compareVersion(a: string, b: string): number {
  const sa = splitVersion(a)
  const sb = splitVersion(b)
  const len = Math.max(sa.core.length, sb.core.length)
  for (let i = 0; i < len; i++) {
    const diff = (sa.core[i] ?? 0) - (sb.core[i] ?? 0)
    if (diff !== 0) return diff
  }
  // Cores equal — apply semver prerelease rules.
  if (sa.pre.length === 0 && sb.pre.length === 0) return 0
  if (sa.pre.length === 0) return 1 // no prerelease > with prerelease
  if (sb.pre.length === 0) return -1
  const preLen = Math.max(sa.pre.length, sb.pre.length)
  for (let i = 0; i < preLen; i++) {
    const segA = sa.pre[i]
    const segB = sb.pre[i]
    if (segA === undefined) return -1 // shorter prefix wins (semver §11.4.4)
    if (segB === undefined) return 1
    const diff = comparePreSegment(segA, segB)
    if (diff !== 0) return diff
  }
  return 0
}

/** Strict "is there a newer version available" check — returns true iff
 *  both strings parse to something and the latest strictly exceeds the
 *  current. Empty / null inputs return false so callers don't have to
 *  guard separately.
 */
export function hasNewerVersion(current: string | null | undefined, latest: string | null | undefined): boolean {
  if (!current || !latest) return false
  return compareVersion(latest, current) > 0
}
