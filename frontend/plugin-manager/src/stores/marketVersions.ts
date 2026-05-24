/**
 * Lightweight cache of "what's the latest version of market plugin X".
 *
 * Loaded lazily when the plugin list view first asks about any installed
 * market plugin. We hit the same ``/plugins`` Market Bridge endpoint that
 * ``MarketPanel`` uses, but we DON'T try to compete with ``MarketPanel``
 * for data ownership — ``MarketPanel`` keeps its own local ref, this
 * store is purely for the install-source "update available" badge on
 * the main plugin list.
 *
 * Cache key = market-side plugin slug (the ``plugin_market_id`` the
 * backend writes into ``source_detail.plugin_market_id``). Values are
 * refreshed at most every ``_REFRESH_INTERVAL_MS``; early callers
 * trigger a single in-flight request.
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { fetchMarketPlugins, type MarketPlugin } from '@/api/market'

const _REFRESH_INTERVAL_MS = 5 * 60 * 1000  // 5 minutes

export const useMarketVersionsStore = defineStore('marketVersions', () => {
  /** slug → latest version string. Populated from fetchMarketPlugins pages. */
  const latestBySlug = ref<Record<string, string>>({})
  const lastFetchedAt = ref<number>(0)
  const loading = ref(false)
  const loadError = ref<string | null>(null)
  let inflight: Promise<void> | null = null

  /** Merge a page of market plugins into the cache.
   *
   * Used by external callers that want to seed the cache from an
   * already-fetched page (e.g. ``MarketPanel`` after its own list
   * load). ``_fetchAll`` does NOT use this anymore — it accumulates
   * into a local map and swaps atomically on success — because merging
   * incrementally during a paginated fetch leaves stale keys for
   * plugins that disappeared from the market between fetches. See
   * ``_fetchAll`` for the swap-on-success pattern.
   */
  function ingestPage(items: MarketPlugin[]): void {
    const next = { ...latestBySlug.value }
    for (const p of items) {
      // Prefer the explicit slug when present; fall back to the numeric id
      // stringified so callers that only have a market_id number still
      // resolve (not expected in the v1 backend, but defensive).
      const key = p.slug ?? String(p.id)
      if (!key) continue
      next[key] = p.version
    }
    latestBySlug.value = next
  }

  /** Fetch all pages of the market's plugin list. Paginates until we've
   *  seen every item or hit a safety cap.
   *
   *  Swap-on-success semantics: pages accumulate into a local map and
   *  ``latestBySlug`` is replaced atomically only after every page
   *  arrives. Any thrown exception (network drop mid-fetch, malformed
   *  response, ...) leaves the previous successful snapshot intact —
   *  partial coverage that could mark a plugin as "no longer in market"
   *  just because we failed before reaching its page would be worse
   *  than serving a slightly stale snapshot. */
  async function _fetchAll(): Promise<void> {
    loading.value = true
    loadError.value = null
    const accumulator: Record<string, string> = {}
    try {
      let page = 1
      const pageSize = 100
      // Defensive cap — no market we care about has >10k plugins.
      const maxPages = 100
      while (page <= maxPages) {
        const result = await fetchMarketPlugins({ page, page_size: pageSize })
        if (!result?.items?.length) break
        for (const p of result.items) {
          const key = p.slug ?? String(p.id)
          if (!key) continue
          accumulator[key] = p.version
        }
        // Use reported total when available to short-circuit; otherwise
        // stop once we get back a partial page.
        const total = result.total ?? 0
        if (total && page * pageSize >= total) break
        if (result.items.length < pageSize) break
        page += 1
      }
      // Atomic swap. After this point any plugin whose slug isn't in
      // ``accumulator`` (because it was unpublished / yanked from the
      // market) will correctly disappear from ``latest()`` lookups.
      latestBySlug.value = accumulator
      lastFetchedAt.value = Date.now()
    } catch (err: any) {
      loadError.value = err?.message ?? String(err)
      // Intentionally do NOT touch ``latestBySlug.value`` — the previous
      // successful snapshot stays live so ``latest()`` callers still get
      // an answer for plugins they care about. ``isReady`` likewise
      // stays based on ``lastFetchedAt`` so the UI doesn't flip into a
      // "never loaded" state on transient network errors.
    } finally {
      loading.value = false
    }
  }

  /** Trigger a refresh if the cache is stale or empty. Callers can await
   *  this, but they don't have to — latest() will still return whatever
   *  was cached previously while the new fetch is in flight. */
  function ensureFresh(): Promise<void> {
    const stale = Date.now() - lastFetchedAt.value > _REFRESH_INTERVAL_MS
    if (!stale && !loadError.value) {
      return Promise.resolve()
    }
    if (!inflight) {
      inflight = _fetchAll().finally(() => {
        inflight = null
      })
    }
    return inflight
  }

  /** Synchronous lookup against the current cache. */
  function latest(slugOrId: string | undefined | null): string | null {
    if (!slugOrId) return null
    return latestBySlug.value[slugOrId] ?? null
  }

  const isReady = computed(() => lastFetchedAt.value > 0)

  return {
    latestBySlug,
    loading,
    loadError,
    isReady,
    ensureFresh,
    latest,
    ingestPage,
  }
})
