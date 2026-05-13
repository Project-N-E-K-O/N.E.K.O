/**
 * Lightweight semver-ish version comparison.
 *
 * Strips a leading ``v`` or ``V`` and splits on ``.``, ``-``, and ``+``
 * so that pre-release / build metadata parts still contribute to
 * ordering (``1.0.0-rc1 < 1.0.0``). Non-numeric segments collapse to 0
 * — full PEP 440 / semver ordering of pre-release identifiers is out of
 * scope; we only need enough fidelity to say "1.0.1 > 1.0.0".
 */
export function compareVersion(a: string, b: string): number {
  const pa = a.replace(/^v/i, '').split(/[.\-+]/).map((n) => parseInt(n, 10) || 0)
  const pb = b.replace(/^v/i, '').split(/[.\-+]/).map((n) => parseInt(n, 10) || 0)
  const len = Math.max(pa.length, pb.length)
  for (let i = 0; i < len; i++) {
    const diff = (pa[i] ?? 0) - (pb[i] ?? 0)
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
