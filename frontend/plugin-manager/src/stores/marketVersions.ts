/**
 * Lightweight cache of "what's the latest version of market plugin X on channel C".
 *
 * Loaded lazily when the plugin list view first asks about any installed
 * market plugin. We use the Market Bridge's compact
 * ``/plugins/latest-versions`` endpoint rather than competing with
 * ``MarketPanel`` for catalog data; this store is purely for the
 * install-source "update available" badge on the main plugin list.
 *
 * Cache is keyed by ``${channel}::${slugOrId}``.  Rather than scanning every
 * page of the Market catalog, ``_fetchAll`` asks the compact latest-versions
 * endpoint only about plugin ids recorded in local market install sources.
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  fetchMarketLatestVersions,
  type MarketPlugin,
} from '@/api/market'

const _REFRESH_INTERVAL_MS = 5 * 60 * 1000  // 5 minutes

export type MarketChannelKey = 'stable' | 'beta'
export interface MarketVersionTarget {
  pluginId: string | number
  channel: MarketChannelKey
}

const _LOOKUP_CHUNK_SIZE = 100

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
  let lastTargetSignature = ''
  let refreshSequence = 0
  let inflight: { signature: string; promise: Promise<void> } | null = null

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

  /** Fetch compact latest-version rows for one channel in bounded batches. */
  async function _fetchChannel(
    channel: MarketChannelKey,
    pluginIds: string[],
    accumulator: Record<string, string>,
  ): Promise<void> {
    for (let offset = 0; offset < pluginIds.length; offset += _LOOKUP_CHUNK_SIZE) {
      const chunk = pluginIds.slice(offset, offset + _LOOKUP_CHUNK_SIZE)
      const versions = await fetchMarketLatestVersions(chunk, channel)
      // A failed chunk makes the whole snapshot untrustworthy: retain the
      // previous complete one rather than making absent ids look up-to-date.
      if (versions === null) {
        throw new Error(`marketVersions: ${channel} latest-version lookup failed`)
      }
      for (const version of versions) {
        accumulator[_cacheKey(channel, String(version.plugin_id))] = version.version
      }
    }
  }

  function _normalizeTargets(targets: MarketVersionTarget[]): MarketVersionTarget[] {
    const unique = new Map<string, MarketVersionTarget>()
    for (const target of targets) {
      const pluginId = String(target.pluginId || '').trim()
      if (!pluginId || !/^\d+$/.test(pluginId) || target.channel !== 'stable' && target.channel !== 'beta') {
        continue
      }
      unique.set(_cacheKey(target.channel, pluginId), { pluginId, channel: target.channel })
    }
    return [...unique.values()]
  }

  function _signatureFor(targets: MarketVersionTarget[]): string {
    return targets
      .map((target) => _cacheKey(target.channel, String(target.pluginId)))
      .sort()
      .join('|')
  }

  /** Fetch exactly the locally installed Market plugins.  Results build in a
   *  local accumulator and atomically replace the cache only on success. */
  async function _fetchAll(
    targets: MarketVersionTarget[],
    signature: string,
    sequence: number,
  ): Promise<void> {
    loading.value = true
    loadError.value = null
    const accumulator: Record<string, string> = {}
    try {
      const idsByChannel = new Map<MarketChannelKey, string[]>()
      for (const target of targets) {
        const ids = idsByChannel.get(target.channel) ?? []
        ids.push(String(target.pluginId))
        idsByChannel.set(target.channel, ids)
      }
      for (const [channel, pluginIds] of idsByChannel) {
        await _fetchChannel(channel, pluginIds, accumulator)
      }
      if (sequence === refreshSequence) {
        latestByKey.value = accumulator
        lastFetchedAt.value = Date.now()
        lastTargetSignature = signature
      }
    } catch (err: any) {
      if (sequence === refreshSequence) {
        loadError.value = err?.message ?? String(err)
      }
      // Intentionally do NOT touch ``latestByKey.value`` — the previous
      // successful snapshot stays live so ``latest()`` callers still get
      // an answer for plugins they care about. ``isReady`` likewise
      // stays based on ``lastFetchedAt`` so the UI doesn't flip into a
      // "never loaded" state on transient network errors.
    } finally {
      if (sequence === refreshSequence) loading.value = false
    }
  }

  /** Trigger a refresh for the currently installed Market plugin targets. */
  function ensureFresh(targets: MarketVersionTarget[]): Promise<void> {
    const normalizedTargets = _normalizeTargets(targets)
    const signature = _signatureFor(normalizedTargets)
    const stale = Date.now() - lastFetchedAt.value > _REFRESH_INTERVAL_MS
    if (!stale && !loadError.value && signature === lastTargetSignature) {
      return Promise.resolve()
    }
    if (inflight?.signature === signature) {
      return inflight.promise
    }
    const sequence = ++refreshSequence
    const promise = _fetchAll(normalizedTargets, signature, sequence)
    inflight = { signature, promise }
    void promise.then(
      () => { if (inflight?.promise === promise) inflight = null },
      () => { if (inflight?.promise === promise) inflight = null },
    )
    return promise
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
