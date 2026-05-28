/**
 * Lightweight cache of "what's the latest version of market plugin X on channel C".
 *
 * Loaded lazily when the plugin list view first asks about any installed
 * market plugin. We hit the same ``/plugins`` Market Bridge endpoint that
 * ``MarketPanel`` uses, but we DON'T try to compete with ``MarketPanel``
 * for data ownership — ``MarketPanel`` keeps its own local ref, this
 * store is purely for the install-source "update available" badge on
 * the main plugin list.
 *
 * Cache is keyed by ``${channel}::${slugOrId}``. ``_fetchAll`` fetches
 * the stable and beta channels separately so a plugin installed from
 * beta compares against the beta latest (and stable against stable);
 * otherwise the badge would compare apples to oranges and either hide
 * a real beta update or invent one against the wrong channel.
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { fetchMarketPlugins, type MarketPlugin } from '@/api/market'

const _REFRESH_INTERVAL_MS = 5 * 60 * 1000  // 5 minutes

export type MarketChannelKey = 'stable' | 'beta'
const _CHANNELS: MarketChannelKey[] = ['stable', 'beta']

function _cacheKey(channel: MarketChannelKey, slugOrId: string): string {
  return `${channel}::${slugOrId}`
}

function _normalizeChannel(channel: string | null | undefined): MarketChannelKey {
  return channel === 'beta' ? 'beta' : 'stable'
}

export const useMarketVersionsStore = defineStore('marketVersions', () => {
  /** ``${channel}::${slugOrId}`` → latest version string for that channel. */
  const latestByKey = ref<Record<string, string>>({})
  const lastFetchedAt = ref<number>(0)
  const loading = ref(false)
  const loadError = ref<string | null>(null)
  let inflight: Promise<void> | null = null

  /** Merge a page of market plugins into the cache.
   *
   * Used by external callers that want to seed the cache from an
   * already-fetched page (e.g. ``MarketPanel`` after its own list
   * load). The page was requested with a specific channel filter, so
   * the caller passes that channel here; items that report a
   * conflicting ``latest_channel`` (defensive against backend drift)
   * are still indexed under the requested channel since that's the
   * filter the user pages saw.
   */
  function ingestPage(items: MarketPlugin[], channel: MarketChannelKey = 'stable'): void {
    const next = { ...latestByKey.value }
    for (const p of items) {
      // Index by BOTH slug AND numeric id so lookups from the install-source
      // lock (which records ``plugin_market_id`` = ``plugin.rawId``, the
      // numeric/string Market id) hit the cache even when the Market API
      // returned a slug.
      if (p.slug) next[_cacheKey(channel, p.slug)] = p.version
      const idKey = p.id != null ? String(p.id) : ''
      if (idKey) next[_cacheKey(channel, idKey)] = p.version
    }
    latestByKey.value = next
  }

  /** Fetch all pages of the market's plugin list for one channel.
   *
   *  Pages accumulate into the shared accumulator; the caller does the
   *  atomic swap into ``latestByKey`` once every channel has finished
   *  so a partial fetch never overwrites a previous good snapshot. */
  async function _fetchChannel(
    channel: MarketChannelKey,
    accumulator: Record<string, string>,
  ): Promise<void> {
    let page = 1
    const pageSize = 100
    // Defensive cap — no market we care about has >10k plugins per channel.
    const maxPages = 100
    while (page <= maxPages) {
      const result = await fetchMarketPlugins({ page, page_size: pageSize, channel })
      if (!result?.items?.length) break
      for (const p of result.items) {
        if (p.slug) accumulator[_cacheKey(channel, p.slug)] = p.version
        const idKey = p.id != null ? String(p.id) : ''
        if (idKey) accumulator[_cacheKey(channel, idKey)] = p.version
      }
      const total = result.total ?? 0
      if (total && page * pageSize >= total) break
      if (result.items.length < pageSize) break
      page += 1
    }
  }

  /** Fetch every supported channel's plugin list. Swap-on-success
   *  semantics: pages accumulate into a local map and ``latestByKey``
   *  is replaced atomically only after every channel finishes. Any
   *  thrown exception leaves the previous successful snapshot intact —
   *  partial coverage that could mark a plugin as "no longer in market"
   *  just because we failed before reaching its page would be worse
   *  than serving a slightly stale snapshot. */
  async function _fetchAll(): Promise<void> {
    loading.value = true
    loadError.value = null
    const accumulator: Record<string, string> = {}
    try {
      for (const channel of _CHANNELS) {
        await _fetchChannel(channel, accumulator)
      }
      latestByKey.value = accumulator
      lastFetchedAt.value = Date.now()
    } catch (err: any) {
      loadError.value = err?.message ?? String(err)
      // Intentionally do NOT touch ``latestByKey.value`` — the previous
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

  /** Synchronous lookup against the current cache.
   *
   * ``channel`` is the channel the plugin was installed from — pass
   * ``source_detail.channel`` so a beta install compares against the
   * beta latest. Anything other than ``'beta'`` collapses to
   * ``'stable'`` (so ``undefined`` / ``'unknown'`` keep the historic
   * stable-only behavior). */
  function latest(
    slugOrId: string | undefined | null,
    channel?: string | null,
  ): string | null {
    if (!slugOrId) return null
    return latestByKey.value[_cacheKey(_normalizeChannel(channel), slugOrId)] ?? null
  }

  const isReady = computed(() => lastFetchedAt.value > 0)

  return {
    latestByKey,
    loading,
    loadError,
    isReady,
    ensureFresh,
    latest,
    ingestPage,
  }
})
