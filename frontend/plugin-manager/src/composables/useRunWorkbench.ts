import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRunsStore } from '@/stores/runs'
import { isRunsRequestCancelled, RunsRequestError, runsRequestErrorKey } from '@/utils/runsError'

export type RunResourcePhase = 'idle' | 'loading' | 'ready' | 'error'
export interface RunResourceState {
  phase: RunResourcePhase
  error: string | null
  loaded: boolean
}
type RequestTarget = 'both' | 'detail' | 'output'
interface SelectionResources {
  detail: RunResourceState
  output: RunResourceState
  cancelUncertain: boolean
  exportRevision: number
  detailRevision: number
  detailStamp: string
  detailPending: { revision: number; stamp: string } | null
  cancelRevision: number
}
const idle = (): RunResourceState => ({ phase: 'idle', error: null, loaded: false })

/** The store owns records/selection; this page-scoped module owns request
 * presentation. At most one selection is loading, with only the latest intent
 * queued. A late response can populate its record but cannot paint another. */
export function useRunWorkbench() {
  const store = useRunsStore()
  const { t } = useI18n()
  const listLoading = ref(false)
  const listLoaded = ref(false)
  const listError = ref<string | null>(null)
  const manualRefreshing = ref(false)
  const cancellingId = ref<string | null>(null)
  const resources = reactive<Record<string, SelectionResources>>(Object.create(null))
  const empty: SelectionResources = {
    detail: idle(),
    output: idle(),
    cancelUncertain: false,
    exportRevision: 0,
    detailRevision: -1,
    detailStamp: '',
    detailPending: null,
    cancelRevision: 0,
  }
  let disposed = false
  let activeId: string | null = null
  let next: { id: string; force: boolean; target: RequestTarget } | null = null
  let task: Promise<void> | null = null

  function forRun(id: string) {
    return (
      resources[id] ??
      (resources[id] = {
        detail: idle(),
        output: idle(),
        cancelUncertain: false,
        exportRevision: 0,
        detailRevision: -1,
        detailStamp: '',
        detailPending: null,
        cancelRevision: 0,
      })
    )
  }
  const selectedId = computed(() => store.selectedRunId)
  const selectedRun = computed(() => store.selectedRun)
  const selection = computed(() =>
    selectedId.value ? (resources[selectedId.value] ?? empty) : empty
  )
  const refreshing = computed(() => manualRefreshing.value || listLoading.value)
  const canCancel = computed(
    () =>
      !!selectedRun.value &&
      ['queued', 'running'].includes(selectedRun.value.status) &&
      !selection.value.cancelUncertain
  )

  function detailStamp(id: string) {
    const record =
      store.runsById?.[id] ??
      store.runs.find((run) => run.run_id === id) ??
      (selectedId.value === id ? selectedRun.value : null)
    return record ? `${record.status}:${record.updated_at ?? ''}` : ''
  }
  function detailIsStale(id: string, state: SelectionResources) {
    const snapshot = state.detailPending ?? {
      revision: state.detailRevision,
      stamp: state.detailStamp,
    }
    return (
      snapshot.revision !== (store.runRevisions?.[id] ?? 0) || snapshot.stamp !== detailStamp(id)
    )
  }
  async function load(
    id: string,
    resource: RunResourceState,
    action: () => Promise<unknown>,
    force: boolean,
    detail: boolean
  ) {
    if (!force && resource.phase === 'ready') return
    const revision = detail ? (store.runRevisions?.[id] ?? 0) : (store.exportRevisions[id] ?? 0)
    const cancelRevision = forRun(id).cancelRevision
    if (detail) forRun(id).detailPending = { revision, stamp: detailStamp(id) }
    resource.phase = 'loading'
    resource.error = null
    try {
      const result = await action()
      if (disposed) return
      if (!detail && result === undefined) {
        resource.phase = 'idle'
        return
      }
      resource.phase = 'ready'
      resource.loaded = true
      if (!detail) forRun(id).exportRevision = revision
      else {
        forRun(id).detailRevision = revision
        forRun(id).detailStamp = detailStamp(id)
      }
      if (detail && forRun(id).cancelRevision === cancelRevision) forRun(id).cancelUncertain = false
    } catch (error) {
      if (disposed) return
      if (isRunsRequestCancelled(error)) resource.phase = 'idle'
      else {
        resource.phase = 'error'
        resource.error = runsRequestErrorKey(error)
      }
    } finally {
      if (detail && !disposed) forRun(id).detailPending = null
    }
  }
  async function drain() {
    while (next && !disposed) {
      const request = next
      next = null
      activeId = request.id
      const state = forRun(request.id)
      await Promise.all([
        request.target !== 'output'
          ? load(
              request.id,
              state.detail,
              () => store.loadRun(request.id),
              request.force || detailIsStale(request.id, state),
              true
            )
          : undefined,
        request.target !== 'detail'
          ? load(
              request.id,
              state.output,
              () => store.loadExports(request.id),
              request.force || state.exportRevision !== (store.exportRevisions[request.id] ?? 0),
              false
            )
          : undefined,
      ])
    }
    activeId = null
  }
  function requestSelection(
    id: string,
    force = false,
    target: RequestTarget = 'both'
  ): Promise<void> {
    if (disposed) return Promise.resolve()
    const state = forRun(id)
    // Returning to an in-flight row must preserve a refresh required by an
    // export event, even if another selection was briefly queued in between.
    if (target !== 'detail' && state.exportRevision !== (store.exportRevisions[id] ?? 0))
      force = true
    if (
      target !== 'output' &&
      (state.detail.loaded || state.detail.phase === 'loading') &&
      detailIsStale(id, state)
    )
      force = true
    if (task) {
      if (activeId === id && !force && target === 'both') {
        if (next?.id !== id) next = null
      } else if (next?.id === id) {
        next = { id, force: next.force || force, target: next.target === target ? target : 'both' }
      } else next = { id, force, target }
      return task
    }
    next = { id, force, target }
    task = drain().finally(async () => {
      task = null
      if (next && !disposed) await requestSelection(next.id, next.force, next.target)
    })
    return task
  }
  function select(id: string) {
    if (disposed) return
    store.selectRun(id)
    return requestSelection(id)
  }
  function refreshSelected() {
    return selectedId.value ? requestSelection(selectedId.value, true) : Promise.resolve()
  }
  function retryDetail() {
    return selectedId.value
      ? requestSelection(selectedId.value, false, 'detail')
      : Promise.resolve()
  }
  function retryOutput() {
    return selectedId.value
      ? requestSelection(selectedId.value, false, 'output')
      : Promise.resolve()
  }
  async function refreshList() {
    if (disposed || listLoading.value) return
    listLoading.value = true
    listError.value = null
    try {
      await store.refreshRuns()
      if (!disposed) listLoaded.value = true
    } catch (error) {
      if (!disposed && !isRunsRequestCancelled(error)) listError.value = runsRequestErrorKey(error)
    } finally {
      if (!disposed) listLoading.value = false
    }
  }
  async function refreshWorkbench() {
    if (disposed || manualRefreshing.value || listLoading.value) return
    manualRefreshing.value = true
    try {
      await refreshList()
      if (!disposed && selectedRun.value) await refreshSelected()
    } finally {
      manualRefreshing.value = false
    }
  }
  async function cancelSelected() {
    const id = selectedId.value
    if (!id || !canCancel.value || cancellingId.value || disposed) return
    cancellingId.value = id
    try {
      try {
        await ElMessageBox.confirm(
          t('runs.cancelConfirmMessage', { runId: id }),
          t('runs.cancelConfirmTitle'),
          { type: 'warning' }
        )
      } catch {
        return
      }
      if (disposed) return
      try {
        await store.cancelRun(id)
        if (disposed) return
        ElMessage.success(t('runs.cancelSuccess') + (selectedId.value === id ? '' : ` · ${id}`))
        if (selectedId.value === id) await refreshSelected()
      } catch (error) {
        if (!disposed && !isRunsRequestCancelled(error)) {
          if (error instanceof RunsRequestError && error.code === 'outcome_unknown') {
            forRun(id).cancelUncertain = true
            forRun(id).cancelRevision++
          }
          ElMessage.error(t(runsRequestErrorKey(error)))
        }
      }
    } finally {
      if (!disposed) cancellingId.value = null
    }
  }

  // An export can arrive after the terminal run event. Refresh only the
  // selected record, coalescing bursts through the same selection queue.
  watch(
    () =>
      [
        selectedId.value,
        selectedId.value ? store.exportRevisions[selectedId.value] : undefined,
      ] as const,
    ([id, value], [oldId, oldValue]) => {
      if (id && id === oldId && value !== undefined && value !== oldValue) void refreshSelected()
    }
  )
  watch(
    () => [selectedId.value, selectedRun.value?.status] as const,
    ([id, status], [oldId, oldStatus]) => {
      if (id && id === oldId && oldStatus && status !== oldStatus && status) void refreshSelected()
    }
  )
  onMounted(async () => {
    await refreshList()
    if (!disposed && selectedId.value && selectedRun.value) await requestSelection(selectedId.value)
  })
  onUnmounted(() => {
    disposed = true
    next = null
    store.disconnect()
  })
  return {
    runs: computed(() => store.runs),
    selectedId,
    selectedRun,
    outputs: computed(() => store.selectedExports),
    connected: computed(() => store.connected),
    connecting: computed(() => store.connecting),
    detail: computed(() => selection.value.detail),
    output: computed(() => selection.value.output),
    cancelUncertain: computed(
      () =>
        selection.value.cancelUncertain &&
        !!selectedRun.value &&
        ['running', 'queued'].includes(selectedRun.value.status)
    ),
    listLoading,
    listLoaded,
    listError,
    refreshing,
    cancellingId,
    canCancel,
    select,
    refreshWorkbench,
    retryDetail,
    retryOutput,
    cancelSelected,
  }
}
