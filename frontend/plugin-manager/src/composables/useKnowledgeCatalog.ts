/**
 * Catalog requests, pagination and selected-entry state for one knowledge page.
 * The page owns polling, shared pack snapshots and presentation of errors.
 * It explicitly disposes these gates in its existing unmount sequence; this
 * composable starts no requests, timers or lifecycle hooks on construction.
 */
import { reactive, ref } from 'vue'
import { knowledgeApi, type KnowledgeEntry, type KnowledgeEntryDetail } from '@/api/knowledge'
import { createLatestRequestGate } from '@/utils/latestRequest'
import { knowledgeEntryKey as entryRowKey } from '@/utils/knowledgeDisplay'

interface CatalogCallbacks {
  reportLoadFailure: (error: unknown) => void
  reportError: (error: unknown) => void
  refreshAfterMutation: () => void
}

const PAGE_SIZE = 50

export function useKnowledgeCatalog({ reportLoadFailure, reportError, refreshAfterMutation }: CatalogCallbacks) {
  const query = ref('')
  const packFilter = ref('')
  const entries = ref<KnowledgeEntry[]>([])
  const entriesLoading = ref(false)
  const offset = ref(0)
  const hasMore = ref(false)
  const totalEntries = ref<number | null>(null)
  const pendingEntries = reactive(new Set<string>())
  const drawerOpen = ref(false)
  const selectedEntry = ref<KnowledgeEntryDetail | null>(null)
  const entriesGate = createLatestRequestGate()
  const entryGate = createLatestRequestGate()
  let disposed = false

  async function loadEntries(reset = false) {
    if (reset) offset.value = 0
    const requestId = entriesGate.begin()
    entriesLoading.value = true
    try {
      const response = await knowledgeApi.entries({
        query: query.value.trim(),
        pack_id: packFilter.value || '',
        limit: PAGE_SIZE,
        offset: offset.value,
      })
      if (disposed || !entriesGate.isLatest(requestId)) return
      entries.value = response.items ?? []
      hasMore.value = Boolean(response.has_more)
      totalEntries.value = typeof response.total === 'number' ? response.total : null
    } catch (error) {
      if (!disposed && entriesGate.isLatest(requestId)) reportLoadFailure(error)
    } finally {
      if (entriesGate.isLatest(requestId)) entriesLoading.value = false
    }
  }

  function previousPage() {
    offset.value = Math.max(0, offset.value - PAGE_SIZE)
    void loadEntries()
  }

  function nextPage() {
    offset.value += PAGE_SIZE
    void loadEntries()
  }

  async function openEntry(row: KnowledgeEntry) {
    const requestId = entryGate.begin()
    try {
      const response = await knowledgeApi.entry({ pack_id: row.pack_id, title: row.title })
      if (disposed || !entryGate.isLatest(requestId)) return
      selectedEntry.value = response.entry ?? null
      drawerOpen.value = Boolean(selectedEntry.value)
    } catch (error) {
      if (!disposed && entryGate.isLatest(requestId)) reportError(error)
    }
  }

  async function toggleEntry(row: KnowledgeEntry, disabled: boolean) {
    const key = entryRowKey(row)
    if (pendingEntries.has(key)) return
    pendingEntries.add(key)
    try {
      const response = await knowledgeApi.setEntryDisabled({ pack_id: row.pack_id, title: row.title, disabled })
      // A catalog read started before this write would put the old flag back;
      // drop it and read the current query/page again.
      entriesGate.invalidate()
      row.disabled = response.disabled ?? disabled
      void loadEntries()
      if (selectedEntry.value && entryRowKey(selectedEntry.value) === key) {
        selectedEntry.value = { ...selectedEntry.value, disabled: row.disabled }
      }
      refreshAfterMutation()
    } catch (error) {
      reportError(error)
    } finally {
      pendingEntries.delete(key)
    }
  }

  /** Drop outstanding reads at the same point the page retires its other gates. */
  function dispose() {
    disposed = true
    entriesGate.invalidate()
    entryGate.invalidate()
  }

  return {
    query, packFilter, entries, entriesLoading, offset, hasMore, totalEntries,
    pendingEntries, drawerOpen, selectedEntry,
    loadEntries, previousPage, nextPage, openEntry, toggleEntry, dispose,
  }
}
