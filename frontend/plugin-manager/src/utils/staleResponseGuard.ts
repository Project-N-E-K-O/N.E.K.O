/**
 * Orders the results of overlapping async refreshes.
 *
 * Each refresh takes a ticket when it starts. An output accepts a result only
 * if its ticket is at least as new as the one it last applied, so:
 *
 * - a slow, older response landing after a newer one is dropped, and
 * - a newer refresh that fails (and so never applies) does not discard an
 *   older refresh's valid result — unlike a plain "latest request wins" check.
 *
 * Outputs written at different points of one refresh (e.g. an index written
 * after the first fetch and a derived map written at the end) take separate
 * keys, so each keeps its own high-water mark.
 */
export function createStaleResponseGuard<K extends string>() {
  let issued = 0
  const applied = new Map<K, number>()
  return {
    begin(): number {
      issued += 1
      return issued
    },
    accept(key: K, ticket: number): boolean {
      if (ticket < (applied.get(key) ?? 0)) return false
      applied.set(key, ticket)
      return true
    },
  }
}
