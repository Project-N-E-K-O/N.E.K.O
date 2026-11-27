<template>
  <div class="knowledge-manager">
    <header class="page-heading">
      <div>
        <h1>{{ t('knowledge.title') }}</h1>
        <p>{{ t('knowledge.subtitle') }}</p>
      </div>
      <el-button :loading="loading" @click="refreshAll">{{ t('common.refresh') }}</el-button>
    </header>

    <el-tabs v-model="activeTab" class="knowledge-tabs">
      <el-tab-pane :label="t('knowledge.tabOverview')" name="overview">
        <KnowledgeOverview :status="status" :packs="packs" :loading="loading" :saving-enabled="savingEnabled" @enabled="setGlobalEnabled" />
      </el-tab-pane>
      <el-tab-pane :label="t('knowledge.tabCatalog')" name="catalog">
        <KnowledgeCatalog
          v-model:query="query" v-model:pack-filter="packFilter"
          :packs="packs" :entries="entries" :entries-loading="entriesLoading" :offset="offset"
          :total-entries="totalEntries" :has-more="hasMore" :pending-entries="pendingEntries"
          @search="loadEntries(true)" @previous="previousPage" @next="nextPage"
          @open-entry="openEntry" @toggle-entry="toggleEntry"
        />
      </el-tab-pane>
      <el-tab-pane :label="t('knowledge.tabPacks')" name="packs">
        <KnowledgePacks
          :packs="packs" :packs-loading="packsLoading" :jobs="packJobs" :retried-jobs="retriedJobs"
          :pending-jobs="pendingJobs" :pending-packs="pendingPacks" :importing="importing" :market-opening="marketOpening"
          @import="importSelectedPack" @open-market="openKnowledgeMarket" @open-exports="openCompletedExports"
          @cancel-job="cancelJob" @retry-job="retryJob" @discard-job="discardJob"
          @enabled="setPackEnabled" @material-type="setPackMaterialType" @index-policy="setPackIndexPolicy"
          @auto-context="setPackAuto" @open-pack="openPack" @remove-pack="removePack"
        />
      </el-tab-pane>
      <el-tab-pane :label="t('knowledge.tabDiagnostics')" name="diagnostics">
        <KnowledgeDiagnostics :queries="queries" :index-batches="indexBatches" :diagnostics-loading="diagnosticsLoading" @refresh="loadDiagnostics" />
      </el-tab-pane>
    </el-tabs>

    <KnowledgeEntryDrawer v-model="drawerOpen" :selected-entry="selectedEntry" />
    <KnowledgePackDrawer v-model="packDrawerOpen" :pack="selectedPack" :jobs="packJobs" :exports-only="exportsOnly" @updated="sourceCommitted" />
    <KnowledgeSourceImport v-model="sourceDialogOpen" :file="sourceFile" :packs="packs" @committed="sourceCommitted" />
  </div>
</template>

<script setup lang="ts">
/**
 * Knowledge page coordinator: one overview epoch, admission snapshot and poll
 * lifecycle across tabs. Children render those shared snapshots and emit intent;
 * they never start parallel polling or resolve consent against copied pack rows.
 */
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useI18n } from 'vue-i18n'
import {
  createMarketPairing,
  isKnownKnowledgeReason,
  KnowledgeApiError,
  knowledgeApi,
  knowledgeFailureReason,
  knowledgeMarketUrl,
  type KnowledgeImportResponse,
  type KnowledgePack,
  type KnowledgePackJob,
  type KnowledgeStatus,
} from '@/api/knowledge'
import { createLatestRequestGate } from '@/utils/latestRequest'
import { createOverviewRequestGate, type OverviewRequestTicket } from '@/utils/overviewRequestGate'
import {
  finishedJobTransitions,
  jobStateSnapshot,
  nextKnowledgePollDelay,
} from '@/utils/knowledgeDisplay'
import { prepareExternalWindow } from '@/utils/openExternal'
import { getMarketUrl } from '@/api/market'
import KnowledgePackDrawer from '@/components/knowledge/KnowledgePackDrawer.vue'
import KnowledgeSourceImport from '@/components/knowledge/KnowledgeSourceImport.vue'
import KnowledgeEntryDrawer from '@/components/knowledge/KnowledgeEntryDrawer.vue'
import KnowledgeDiagnostics from '@/components/knowledge/KnowledgeDiagnostics.vue'
import KnowledgePacks from '@/components/knowledge/KnowledgePacks.vue'
import KnowledgeCatalog from '@/components/knowledge/KnowledgeCatalog.vue'
import KnowledgeOverview from '@/components/knowledge/KnowledgeOverview.vue'
import { useKnowledgeCatalog } from '@/composables/useKnowledgeCatalog'
import { useKnowledgeDiagnostics } from '@/composables/useKnowledgeDiagnostics'

// Load failures caused by the service not being ready are already explained
// by the status card; do not stack a toast on top of it.
const QUIET_LOAD_REASONS = new Set(['knowledge_starting', 'knowledge_unavailable'])

const { t } = useI18n()

const activeTab = ref('overview')
const loading = ref(false)
const status = ref<KnowledgeStatus | null>(null)
const savingEnabled = ref(false)

// The catalog owns its two request gates; this page retains cross-tab refresh.
const {
  query, packFilter, entries, entriesLoading, offset, hasMore, totalEntries,
  pendingEntries, drawerOpen, selectedEntry,
  loadEntries, previousPage, nextPage, openEntry, toggleEntry, dispose: disposeCatalog,
} = useKnowledgeCatalog({
  reportLoadFailure,
  reportError: (error) => ElMessage.error(errorMessage(error)),
  refreshAfterMutation,
})

const packs = ref<KnowledgePack[]>([])
const packsLoading = ref(false)
const pendingPacks = reactive(new Set<string>())
const packJobs = ref<KnowledgePackJob[]>([])
const pendingJobs = reactive(new Set<string>())
// A successful handoff consumes this row locally; a stale poll cannot reoffer it.
const retriedJobs = reactive(new Set<string>())
// Keep newly admitted jobs visible until polling acknowledges their IDs.
// A snapshot captured before admission must not hide the only cancellable row.
const unconfirmedJobs = new Map<string, KnowledgePackJob>()
const importing = ref(false)
const marketOpening = ref(false)
const sourceFile = ref<File | null>(null)
const sourceDialogOpen = ref(false)
const packDrawerOpen = ref(false)
const exportsOnly = ref(false)
const selectedPackId = ref('')
const selectedPack = computed(() => packs.value.find(row => row.pack_id === selectedPackId.value) ?? null)

// Diagnostic reads have their own epoch; this page decides when tabs enter/leave.
const {
  queries, indexBatches, diagnosticsLoading, loadDiagnostics,
  invalidate: invalidateDiagnostics, dispose: disposeDiagnostics,
} = useKnowledgeDiagnostics(reportLoadFailure)

const overviewGate = createOverviewRequestGate()
const jobsGate = createLatestRequestGate()

let lastJobStates = new Map<string, string>()
let pollTimer: number | null = null
let pollTimerDelay = 0
let pollInFlight = false
let disposed = false

// ── derived state ────────────────────────────────────────────────────

const hasBackgroundProgress = computed(() => {
  const current = status.value
  if (!current) return false
  if (current.state === 'starting') return true
  if (current.state !== 'ready') return false
  if (current.embedding?.state === 'loading') return true
  return packs.value.some((pack) => pack.vector_state === 'building')
})

// ── labels ───────────────────────────────────────────────────────────

function reasonMessage(reason: unknown): string {
  const key = isKnownKnowledgeReason(reason) ? reason : 'operation_failed'
  return t(`knowledge.reasons.${key}`)
}

function errorMessage(error: unknown): string {
  return reasonMessage(knowledgeFailureReason(error))
}

function isCancelled(error: unknown): boolean {
  return error === 'cancel' || error === 'close'
}

// ── loading ──────────────────────────────────────────────────────────

function isCurrent(resource: 'status' | 'packs', ticket: OverviewRequestTicket): boolean {
  return !disposed && overviewGate.isCurrent(resource, ticket)
}

function reportLoadFailure(error: unknown) {
  const reason = knowledgeFailureReason(error)
  if (QUIET_LOAD_REASONS.has(reason)) return
  ElMessage.error(`${t('knowledge.loadFailed')}: ${reasonMessage(reason)}`)
}

async function loadStatus(options: { silent?: boolean } = {}) {
  const ticket = overviewGate.begin('status')
  if (!ticket || disposed) return
  if (!options.silent) loading.value = true
  try {
    const response = await knowledgeApi.status()
    if (!isCurrent('status', ticket)) return
    const becameReady = status.value?.state !== 'ready' && response.status?.state === 'ready'
    status.value = response.status ?? null
    // The packs request sent alongside this one may have been refused while
    // the service was still starting; fetch them again now that it is ready.
    if (becameReady) void loadPacks({ silent: true })
  } catch (error) {
    if (isCurrent('status', ticket) && !options.silent) reportLoadFailure(error)
  } finally {
    if (isCurrent('status', ticket)) loading.value = false
  }
}

async function loadPacks(options: { silent?: boolean } = {}) {
  const ticket = overviewGate.begin('packs')
  if (!ticket || disposed) return
  if (!options.silent) packsLoading.value = true
  try {
    const response = await knowledgeApi.packs()
    if (!isCurrent('packs', ticket)) return
    packs.value = response.packs ?? []
  } catch (error) {
    if (!isCurrent('packs', ticket)) return
    if (QUIET_LOAD_REASONS.has(knowledgeFailureReason(error))) packs.value = []
    if (!options.silent) reportLoadFailure(error)
  } finally {
    if (isCurrent('packs', ticket)) packsLoading.value = false
  }
}

function applyJobs(jobs: KnowledgePackJob[]) {
  for (const job of jobs) unconfirmedJobs.delete(job.job_id)
  jobs = [...unconfirmedJobs.values(), ...jobs]
  const transitions = finishedJobTransitions(lastJobStates, jobs)
  for (const { job, outcome } of transitions) {
    const name = job.pack_id || job.job_id
    if (outcome === 'active') {
      ElMessage.success(t(job.kind === 'pack_export' ? 'knowledge.exportReady' : 'knowledge.importDone', { name }))
    } else if (outcome === 'failed') {
      ElMessage.error(t(job.kind === 'pack_export' ? 'knowledge.exportFailed' : 'knowledge.importFailed', { name, reason: reasonMessage(job.reason) }))
    } else {
      ElMessage.info(t(job.kind === 'pack_export' ? 'knowledge.exportCancelled' : 'knowledge.importCancelled', { name }))
    }
  }
  lastJobStates = jobStateSnapshot(jobs)
  packJobs.value = jobs
  // Status and packs were fetched in parallel with these jobs and may predate
  // the finish; once no job is running nothing else would reload them.
  if (transitions.length) {
    void Promise.allSettled([loadStatus({ silent: true }), loadPacks({ silent: true })])
    // A finished import changes the catalog too; the open page would
    // otherwise keep showing the rows from before it.
    if (activeTab.value === 'catalog' && transitions.some(({ outcome }) => outcome === 'active')) {
      void loadEntries()
    }
  }
}

async function loadJobs(options: { silent?: boolean } = {}) {
  const requestId = jobsGate.begin()
  try {
    const response = await knowledgeApi.packJobs()
    if (disposed || !jobsGate.isLatest(requestId)) return
    applyJobs(response.jobs ?? [])
  } catch (error) {
    if (!disposed && jobsGate.isLatest(requestId) && !options.silent) reportLoadFailure(error)
  }
}

async function refreshOverview(options: { silent?: boolean } = {}) {
  await Promise.allSettled([loadStatus(options), loadPacks(options), loadJobs(options)])
}

async function refreshAll() {
  await refreshOverview()
  if (activeTab.value === 'catalog') await loadEntries()
  if (activeTab.value === 'diagnostics') await loadDiagnostics()
}

/** After a successful write: drop in-flight reads that predate it, then reload quietly. */
function refreshAfterMutation() {
  overviewGate.invalidate()
  loading.value = false
  packsLoading.value = false
  void refreshOverview({ silent: true })
}

function registerImportJob(response: KnowledgeImportResponse, kind: KnowledgePackJob['kind']) {
  const jobId = response.job_id
  if (disposed || response.unchanged || !jobId || packJobs.value.some(job => job.job_id === jobId)) return
  // Observe the admitted state before reading the overview. Otherwise its
  // first terminal snapshot cannot trigger the normal completion reload.
  const admitted: KnowledgePackJob = {
    job_id: jobId, pack_id: response.pack_id, kind, state: response.state, reason: response.reason ?? '',
    entries_total: Number(response.entries_total) || 0, chunks_total: Number(response.chunks_total) || 0,
    created_at: '', updated_at: '',
  }
  lastJobStates.set(jobId, admitted.state)
  // Keep the admitted result until the server lists its ID, even if it has
  // already failed. Its real terminal state never creates synthetic busy work.
  unconfirmedJobs.set(jobId, admitted)
  packJobs.value = [admitted, ...packJobs.value]
}

function sourceCommitted(response?: KnowledgeImportResponse) {
  if (disposed) return
  if (response) registerImportJob(response, 'import_commit')
  refreshAfterMutation()
  // A source change can finish inside the request (e.g. removing the last
  // source) and be admitted as `active`; no later job transition would then
  // reload a catalog the user opened meanwhile. loadEntries() supersedes any
  // catalog read that predates the change.
  if (response?.state === 'active' && !response.unchanged && activeTab.value === 'catalog') void loadEntries()
}

// ── polling ──────────────────────────────────────────────────────────

function clearPollTimer() {
  if (pollTimer !== null) window.clearTimeout(pollTimer)
  pollTimer = null
  pollTimerDelay = 0
}

function schedulePoll() {
  if (disposed || pollInFlight) return
  const delay = nextKnowledgePollDelay({
    jobs: packJobs.value,
    backgroundProgress: hasBackgroundProgress.value,
    showsVectorProgress: activeTab.value === 'overview' || activeTab.value === 'packs',
    serviceStarting: status.value?.state === 'starting',
  })
  if (delay === null) {
    clearPollTimer()
    return
  }
  if (pollTimer !== null && pollTimerDelay <= delay) return
  clearPollTimer()
  pollTimerDelay = delay
  pollTimer = window.setTimeout(runPoll, delay)
}

async function runPoll() {
  pollTimer = null
  pollTimerDelay = 0
  if (disposed) return
  pollInFlight = true
  try {
    await refreshOverview({ silent: true })
  } finally {
    pollInFlight = false
  }
  schedulePoll()
}

// ── writes ───────────────────────────────────────────────────────────

async function setGlobalEnabled(enabled: boolean) {
  if (!status.value || savingEnabled.value) return
  savingEnabled.value = true
  try {
    const response = await knowledgeApi.setEnabled(enabled)
    if (disposed) return
    if (status.value) status.value = { ...status.value, enabled: response.enabled ?? enabled }
    ElMessage.success(enabled ? t('knowledge.enabledSaved') : t('knowledge.disabledSaved'))
    refreshAfterMutation()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    savingEnabled.value = false
  }
}

async function openKnowledgeMarket() {
  // Before any await: a browser only allows a popup inside the click itself.
  const target = prepareExternalWindow()
  if (target.blocked) {
    // No window to show the Market in: do not issue a pairing code for nothing.
    ElMessage.error(t('knowledge.marketPopupBlocked'))
    return
  }
  marketOpening.value = true
  try {
    const base = await getMarketUrl()
    if (!base) {
      target.cancel()
      ElMessage.error(t('knowledge.marketUnavailable'))
      return
    }
    target.open(knowledgeMarketUrl(base, await createMarketPairing()))
  } catch {
    target.cancel()
    ElMessage.error(t('knowledge.marketPairFailed'))
  } finally {
    marketOpening.value = false
  }
}

async function importSelectedPack(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || importing.value) return
  // JSON and ZIP already describe a pack. Documents require an explicit local
  // preview and retrieval/privacy choice before they join the shared index.
  if (!/\.(json|zip)$/i.test(file.name)) {
    sourceFile.value = file
    sourceDialogOpen.value = true
    return
  }
  importing.value = true
  try {
    const response = await knowledgeApi.importPack(file)
    if (disposed) return
    if (response.unchanged) {
      ElMessage.info(t('knowledge.importUnchanged', { name: response.pack_id }))
    } else {
      ElMessage.success(t('knowledge.importQueued', { name: response.pack_id }))
      registerImportJob(response, 'pack_import')
    }
    refreshAfterMutation()
  } catch (error) {
    ElMessage.error(t('knowledge.importRejected', { reason: errorMessage(error) }))
  } finally {
    importing.value = false
  }
}

async function cancelJob(job: KnowledgePackJob) {
  if (pendingJobs.has(job.job_id)) return
  try {
    await ElMessageBox.confirm(
      t('knowledge.cancelJobConfirm', { name: job.pack_id || job.job_id }),
      t('common.warning'),
      { type: 'warning', confirmButtonText: t('knowledge.cancelJob'), cancelButtonText: t('common.cancel') },
    )
  } catch {
    return
  }
  pendingJobs.add(job.job_id)
  try {
    await knowledgeApi.cancelPackJob({ job_id: job.job_id })
    refreshAfterMutation()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    pendingJobs.delete(job.job_id)
  }
}

async function discardJob(job: KnowledgePackJob) {
  if (pendingJobs.has(job.job_id) || retriedJobs.has(job.job_id)) return
  pendingJobs.add(job.job_id)
  try {
    await knowledgeApi.discardPackJob({ job_id: job.job_id })
    jobsGate.invalidate()
    // A discarded job must not be merged back from its unconfirmed local row.
    unconfirmedJobs.delete(job.job_id)
    packJobs.value = packJobs.value.filter((item) => item.job_id !== job.job_id)
    lastJobStates.delete(job.job_id)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    pendingJobs.delete(job.job_id)
  }
}

async function retryJob(job: KnowledgePackJob) {
  if (pendingJobs.has(job.job_id) || retriedJobs.has(job.job_id) || !['failed', 'cancelled'].includes(job.state)) return
  pendingJobs.add(job.job_id)
  try {
    // Only terminal tasks reach this action; busy work stays in its original queue.
    const response = await knowledgeApi.retryPackJob({ job_id: job.job_id })
    // An unchanged retry has no replacement task; its retained input still
    // belongs to this card, so keep retry/discard available.
    if (response.job_id) retriedJobs.add(job.job_id)
    else if (response.unchanged) ElMessage.info(t('knowledge.importUnchanged', { name: response.pack_id || job.pack_id }))
    jobsGate.invalidate()
    // Show the admitted retry immediately, before the follow-up list is available.
    if (response.job_id && !packJobs.value.some(item => item.job_id === response.job_id)) {
      // Admission may already finish with an actionable error before polling catches up.
      const retry = { ...job, ...response, job_id: response.job_id, reason: response.reason ?? '' }
      unconfirmedJobs.set(retry.job_id, retry)
      lastJobStates.set(retry.job_id, retry.state)
      packJobs.value = [retry, ...packJobs.value]
    }
    refreshAfterMutation()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    pendingJobs.delete(job.job_id)
  }
}

async function mutatePack(row: KnowledgePack, mutation: () => Promise<void>) {
  if (pendingPacks.has(row.pack_id)) return
  pendingPacks.add(row.pack_id)
  try {
    await mutation()
    refreshAfterMutation()
  } catch (error) {
    if (!isCancelled(error)) ElMessage.error(errorMessage(error))
  } finally {
    pendingPacks.delete(row.pack_id)
  }
}

function openPack(row: KnowledgePack) {
  exportsOnly.value = false
  selectedPackId.value = row.pack_id
  packDrawerOpen.value = true
}

function openCompletedExports() {
  // Use retained task/artifact facts, never a fabricated installed pack. The
  // same drawer preserves uncertain publication requests across both entries.
  selectedPackId.value = ''
  exportsOnly.value = true
  packDrawerOpen.value = true
}

function setPackEnabled(row: KnowledgePack, enabled: boolean) {
  // Consent belongs to the body shown when the user opens the confirmation,
  // not a newer row installed by polling while that confirmation is pending.
  const packId = row.pack_id
  const confirmedDigest = row.pack_sha256
  return mutatePack(row, async () => {
    try {
      if (enabled) {
        if (!confirmedDigest) throw new KnowledgeApiError('pack_content_changed')
        await ElMessageBox.confirm(t('knowledge.cloudDisclosure'), t('common.warning'), { type: 'warning' })
      }
      const response = await knowledgeApi.setPackEnabled({
        pack_id: packId, enabled, ...(enabled ? { expected_pack_sha256: confirmedDigest } : {}),
      })
      // A newer observed policy wins even for the same body. Counters from
      // different service lifetimes cannot be ordered; keep that refreshed row.
      const current = packs.value.find(pack => pack.pack_id === packId)
      const saved = response.policy_revision
      const observed = current?.policy_revision
      if (current && current.pack_sha256 === confirmedDigest && saved && observed
          && saved.epoch === observed.epoch && observed.counter <= saved.counter) {
        current.enabled = response.pack_enabled ?? enabled
        current.policy_revision = saved
      }
    } catch (error) {
      if (knowledgeFailureReason(error) === 'pack_content_changed') await loadPacks({ silent: true })
      throw error
    }
  })
}

function setPackMaterialType(row: KnowledgePack, value: string) {
  const materialType = value === 'knowledge' || value === 'corpus' ? value : null
  return mutatePack(row, async () => {
    const response = await knowledgeApi.setPackMaterialType({ pack_id: row.pack_id, material_type: materialType })
    row.material_type_override = response.material_type_override ?? null
    if (response.effective_material_type) row.effective_material_type = response.effective_material_type
  })
}

function setPackIndexPolicy(row: KnowledgePack, enabled: boolean) {
  return mutatePack(row, async () => {
    const response = await knowledgeApi.setPackIndexPolicy({ pack_id: row.pack_id, local_embedding_enabled: enabled })
    row.local_embedding = response.local_embedding ?? enabled
  })
}

function setPackAuto(row: KnowledgePack, enabled: boolean) {
  return mutatePack(row, async () => {
    const response = await knowledgeApi.setPackAutoContext({ pack_id: row.pack_id, enabled })
    row.auto_context = response.auto_context ?? enabled
  })
}

function removePack(row: KnowledgePack) {
  return mutatePack(row, async () => {
    await ElMessageBox.confirm(
      t('knowledge.removeConfirm', { name: row.source?.name || row.pack_id, count: row.entries ?? 0 }),
      t('common.warning'),
      { type: 'warning', confirmButtonText: t('knowledge.remove'), cancelButtonText: t('common.cancel') },
    )
    await knowledgeApi.removePack({ pack_id: row.pack_id })
    packs.value = packs.value.filter((pack) => pack.pack_id !== row.pack_id)
    if (packFilter.value === row.pack_id) packFilter.value = ''
    ElMessage.success(t('knowledge.removed', { name: row.source?.name || row.pack_id }))
  })
}

// ── lifecycle ────────────────────────────────────────────────────────

watch(activeTab, (tab, previousTab) => {
  if (previousTab === 'diagnostics' && tab !== 'diagnostics') {
    // A load still in flight belongs to a tab the user has left.
    invalidateDiagnostics()
  }
  if (tab === 'catalog') void loadEntries(true)
  if (tab === 'packs') {
    void loadPacks({ silent: packs.value.length > 0 })
    void loadJobs({ silent: true })
  }
  if (tab === 'diagnostics') void loadDiagnostics()
})

watch([packJobs, hasBackgroundProgress, activeTab], schedulePoll)

// A tab opened while the service was starting got its first request refused;
// load it again once the service is ready.
watch(
  () => status.value?.state,
  (state, previous) => {
    if (state !== 'ready' || previous === 'ready' || previous === undefined) return
    if (activeTab.value === 'catalog') void loadEntries()
    if (activeTab.value === 'diagnostics') void loadDiagnostics()
  },
)

onMounted(() => {
  void refreshOverview()
})

onUnmounted(() => {
  disposed = true
  clearPollTimer()
  overviewGate.invalidate()
  jobsGate.invalidate()
  disposeCatalog()
  disposeDiagnostics()
})
</script>

<style scoped>
.knowledge-manager {
  --knowledge-surface: var(--surface-card);
  --knowledge-surface-muted: var(--surface-inset);
  --knowledge-line: var(--surface-border);
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 18px;
  width: 100%;
  min-width: 0;
  padding: var(--spacing-lg) var(--spacing-lg) 72px;
  overflow-x: clip;
}

.page-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.page-heading h1 {
  margin: 0 0 6px;
  font-size: 24px;
  line-height: 1.2;
}

.page-heading p {
  margin: 0;
  color: var(--el-text-color-secondary);
}

.knowledge-tabs {
  min-width: 0;
  border: 1px solid var(--knowledge-line);
  border-radius: var(--radius-card);
  background: var(--knowledge-surface);
  box-shadow: var(--surface-shadow);
}

.knowledge-tabs :deep(.el-tabs__header) {
  margin: 0;
  padding: 8px;
  border-bottom: 1px solid var(--knowledge-line);
  border-radius: var(--radius-card) var(--radius-card) 0 0;
  background: var(--knowledge-surface-muted);
}

.knowledge-tabs :deep(.el-tabs__nav-wrap) {
  min-width: 0;
}

.knowledge-tabs :deep(.el-tabs__nav-wrap::after),
.knowledge-tabs :deep(.el-tabs__active-bar) {
  display: none;
}

.knowledge-tabs :deep(.el-tabs__nav-scroll) {
  overflow-x: auto;
  scrollbar-width: none;
}

.knowledge-tabs :deep(.el-tabs__nav) {
  display: flex;
  flex-wrap: nowrap;
  gap: 8px;
  min-width: max-content;
}

.knowledge-tabs :deep(.el-tabs__item) {
  position: relative;
  display: inline-flex;
  justify-content: center;
  min-width: 104px;
  height: 36px;
  padding: 0 12px;
  border-radius: var(--radius-control);
  color: var(--el-text-color-regular);
  font-size: 14px;
  font-weight: 500;
  transition:
    color 160ms ease,
    background-color 160ms ease,
    box-shadow 160ms ease;
}

.knowledge-tabs :deep(.el-tabs__item:hover) {
  color: var(--el-color-primary);
  background: var(--el-fill-color-light);
}

.knowledge-tabs :deep(.el-tabs__item.is-active) {
  color: var(--el-color-primary);
  background: var(--knowledge-surface);
  box-shadow: var(--surface-shadow);
}

.knowledge-tabs :deep(.el-tabs__item.is-active::after) {
  position: absolute;
  right: 12px;
  bottom: 4px;
  left: 12px;
  height: 2px;
  border-radius: 999px;
  background: var(--el-color-primary);
  content: '';
}

.knowledge-tabs :deep(.el-tabs__content) {
  min-width: 0;
  padding: 18px;
}

@media (max-width: 640px) {
  .knowledge-manager {
      padding: var(--spacing-md) var(--spacing-md) calc(128px + env(safe-area-inset-bottom));
    }

  .knowledge-tabs :deep(.el-tabs__item) {
      min-width: 86px;
      height: 34px;
      padding: 0 8px;
    }

  .knowledge-tabs :deep(.el-tabs__content) {
      padding: 14px;
    }
}
</style>
