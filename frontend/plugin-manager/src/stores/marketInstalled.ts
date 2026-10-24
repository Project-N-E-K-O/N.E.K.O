/** The bridge's installed projection has one owner, independent of drawer life. */
import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import { fetchBridge } from '@/api/marketBridge'
import { usePluginStore } from '@/stores/plugin'
import {
  fetchInstalledProjection,
  type MarketInstalledState,
} from '@/utils/marketPluginInstallState'
import {
  indexInstalledPluginIdentities,
  marketLocalIdentityKeys,
  marketRecordIdentityKeys,
  type MarketPluginIdentity,
} from '@/utils/marketPluginIdentity'

export interface InstalledMarketEntry extends MarketInstalledState {
  market_id?: string
  installed_version: string
  channel?: 'stable' | 'beta'
  package_url?: string
}

export const useMarketInstalledStore = defineStore('marketInstalled', () => {
  const pluginStore = usePluginStore()
  const entries = ref<InstalledMarketEntry[]>([])
  // Loaded means a successful snapshot exists; invalidation retains that snapshot.
  const loaded = ref(false)
  const stale = ref(true)
  const loading = ref(false)
  const indexes = computed(() => indexInstalledPluginIdentities(entries.value))
  let epoch = 0
  let pending: { epoch: number; promise: Promise<InstalledMarketEntry[] | null> } | null = null

  // This outlives MarketPanel's active/hidden state. A response started before a
  // registry change can never make the projection fresh again.
  watch(
    () => pluginStore.registryRevision,
    () => {
      epoch += 1
      stale.value = true
    },
    { flush: 'sync' }
  )

  function getInstalledState(plugin: MarketPluginIdentity): InstalledMarketEntry | undefined {
    for (const key of marketRecordIdentityKeys(plugin)) {
      const entry = indexes.value.byMarketId.get(key)
      if (entry) return entry
    }
    for (const key of marketLocalIdentityKeys(plugin)) {
      const entry = indexes.value.byPluginId.get(key)
      if (entry) return entry
    }
    return undefined
  }

  function ensureFresh(options: { force?: boolean } = {}): Promise<InstalledMarketEntry[] | null> {
    if (pending?.epoch === epoch) return pending.promise
    if (loaded.value && !stale.value && !options.force) return Promise.resolve(entries.value)

    const requestEpoch = epoch
    loading.value = true
    stale.value = true
    const promise = fetchInstalledProjection(() => fetchBridge('/market/installed'))
      .then((installed) => {
        if (requestEpoch !== epoch || installed === null) return null
        const next = installed.map((item): InstalledMarketEntry => ({
          ...item,
          market_id: item.latest_install_source?.plugin_market_id,
          installed_version:
            item.effective_version ||
            item.latest_install_source?.version ||
            item.builtin_version ||
            '',
          channel: item.latest_install_source?.channel,
          package_url: item.latest_install_source?.package_url,
        }))
        entries.value = next
        loaded.value = true
        stale.value = false
        return next
      })
      .finally(() => {
        if (pending?.promise === promise) {
          pending = null
          loading.value = false
        }
      })
    pending = { epoch: requestEpoch, promise }
    return promise
  }

  return { entries, loaded, stale, loading, getInstalledState, ensureFresh }
})
