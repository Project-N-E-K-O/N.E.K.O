<template>
  <el-drawer :model-value="modelValue" :title="pack?.source.name || pack?.pack_id" size="min(760px, 96vw)" @close="emit('update:modelValue', false)">
    <template v-if="pack">
      <div class="pack-summary">
        <el-tag :type="pack.enabled === false ? 'info' : 'success'">{{ t(pack.enabled === false ? 'knowledge.entryDisabled' : 'knowledge.entryEnabled') }}</el-tag>
        <span>{{ t('knowledge.vectorCounts', { ready: pack.chunks_ready, total: pack.chunks_total }) }}</span>
      </div>
      <template v-if="pack.sources_managed === true">
      <h3>{{ t('knowledge.sourceDocuments') }}</h3>
      <input ref="sourceInput" type="file" accept=".txt,.md,.docx,.pdf" hidden @change="selectSource" />
      <el-button v-if="!pack.retained" type="primary" plain @click="chooseSource()">{{ t('knowledge.addSource') }}</el-button>
      <el-table :data="documents" v-loading="loading" :empty-text="t('knowledge.noSources')">
        <el-table-column :label="t('knowledge.sourceName')" min-width="180"><template #default="scope">{{ scope.row.name }} <el-tag v-if="scope.row.removed" type="info" size="small">{{ t('knowledge.removedSource') }}</el-tag></template></el-table-column>
        <el-table-column prop="chars" :label="t('knowledge.sourceChars')" width="100" />
        <el-table-column :label="t('knowledge.sourceVersion')" width="90"><template #default="scope">{{ scope.row.current_version ?? '—' }}</template></el-table-column>
        <el-table-column :label="t('knowledge.actions')" min-width="210">
          <template #default="scope">
            <el-button link type="primary" @click="showVersions(scope.row)">{{ t('knowledge.sourceHistory') }}</el-button>
            <el-button v-if="!pack.retained && !scope.row.removed" link type="primary" @click="chooseSource(scope.row)">{{ t('knowledge.replaceSource') }}</el-button>
            <el-button v-if="!pack.retained && !scope.row.removed" link type="danger" :disabled="mutating" @click="removeSource(scope.row)">{{ t('knowledge.remove') }}</el-button>
          </template>
        </el-table-column>
      </el-table>
      </template>

      <section class="export-panel">
        <div class="export-create">
          <div class="export-heading">
            <span class="export-icon" aria-hidden="true"><el-icon :size="22"><FolderOpened /></el-icon></span>
            <div class="export-heading-copy">
              <h3>{{ t('knowledge.exportTitle') }}</h3>
              <p>{{ t('knowledge.exportScope') }}</p>
            </div>
            <div v-if="!pack.retained" class="export-actions">
              <!-- Export still uses the existing task and vector-readiness gate. -->
              <el-button type="primary" :loading="exporting" :disabled="exporting || !exportReady" @click="exportPack()">
                <el-icon><Download /></el-icon><span>{{ t('knowledge.exportPackage') }}</span>
              </el-button>
            </div>
          </div>
          <template v-if="!pack.retained">
            <div class="export-readiness" :class="{ 'is-ready': exportReady }" role="status">
              <el-icon><CircleCheck v-if="exportReady" /><Clock v-else /></el-icon>
              <span>{{ t(exportReadinessHint) }}</span>
            </div>
            <p class="export-version-hint">{{ t('knowledge.exportAutoVersionHint') }}</p>
          </template>
        </div>
        <div class="export-history-heading">
          <h4>{{ t('knowledge.exportHistoryTitle') }}</h4>
          <span class="export-count">{{ exports.length }}</span>
        </div>
        <div v-if="!exports.length" class="export-empty">
          <el-icon :size="24" aria-hidden="true"><Document /></el-icon>
          <span>{{ t('knowledge.exportEmpty') }}</span>
        </div>
        <div class="export-history">
          <article v-for="job in exports" :key="job.job_id" class="artifact-row">
            <span class="artifact-icon" aria-hidden="true"><el-icon :size="22"><Document /></el-icon></span>
            <div class="artifact-info">
              <!-- Opaque task/release identities remain protocol metadata, never card titles. -->
              <div class="artifact-name">{{ job.result?.name || pack.source.name || t('knowledge.pack') }}</div>
              <div class="artifact-meta">
                <time :datetime="job.created_at" :title="t('knowledge.exportTime')">{{ formatTime(job.created_at, true) }}</time>
                <span v-if="job.result" :title="t('knowledge.exportSize')">{{ formatSize(job.result.bytes) }}</span>
                <span v-if="job.result" class="artifact-format">ZIP</span>
              </div>
            </div>
            <el-tag class="artifact-status" :type="job.state === 'active' ? 'success' : job.state === 'failed' ? 'danger' : 'info'" effect="light">
              {{ t(job.state === 'active' ? 'knowledge.exportCompleted' : `knowledge.${job.state === 'queued' ? 'jobQueued' : job.state === 'building' ? 'jobBuilding' : job.state === 'failed' ? 'jobFailed' : 'jobCancelled'}`) }}
            </el-tag>
            <div v-if="job.state === 'active' && job.result?.artifact_id" class="artifact-actions">
              <el-button type="primary" :loading="downloading === job.job_id" :disabled="deleting === job.job_id" @click="download(job)">
                <el-icon><Download /></el-icon><span>{{ t('knowledge.downloadExport') }}</span>
              </el-button>
              <el-button plain :disabled="deleting === job.job_id" @click="preparePublication(job)">{{ t('knowledge.publishMarket') }}</el-button>
              <el-button class="artifact-delete" type="danger" text :loading="deleting === job.job_id"
                :disabled="downloading === job.job_id || publishing || (publicationOpen && publicationJob?.job_id === job.job_id)"
                @click="deleteExport(job)">{{ t('knowledge.deleteExport') }}</el-button>
            </div>
          </article>
        </div>
      </section>
    </template>
    <KnowledgeSourceImport v-model="sourceDialog" :file="sourceFile" :pack="pack" :doc-id="replacing?.doc_id" @committed="sourceCommitted" />
    <el-dialog v-model="historyOpen" :title="t('knowledge.sourceHistory')" append-to-body width="min(640px, 94vw)">
      <template v-if="requiresAcknowledgment">
        <el-alert :title="t('knowledge.enabledPackDisclosure')" type="warning" :closable="false" show-icon />
        <el-checkbox v-model="acknowledged" data-testid="source-restore-privacy-confirm">{{ t('knowledge.acknowledgeEnabledPack') }}</el-checkbox>
      </template>
      <el-table :data="versions" v-loading="versionsLoading">
        <el-table-column prop="version" :label="t('knowledge.sourceVersion')" width="90" />
        <el-table-column prop="name" :label="t('knowledge.sourceName')" min-width="160" />
        <el-table-column :label="t('knowledge.time')" min-width="170"><template #default="scope">{{ formatTime(scope.row.created_at) }}</template></el-table-column>
        <el-table-column :label="t('knowledge.actions')" width="100"><template #default="scope"><el-button link type="primary" :disabled="scope.row.current || mutating || (requiresAcknowledgment && !acknowledged)" @click="restore(scope.row)">{{ t('knowledge.restoreSource') }}</el-button></template></el-table-column>
      </el-table>
    </el-dialog>
    <el-dialog v-model="publicationOpen" :title="t('knowledge.publishMarket')" append-to-body width="min(640px, 94vw)" :close-on-click-modal="!publishing" :close-on-press-escape="!publishing" :show-close="!publishing">
      <el-alert type="warning" :closable="false" show-icon :title="t('knowledge.publishDisclosure')" />
      <p class="publication-pack-name">{{ publicationJob?.result?.name || pack?.source.name || t('knowledge.pack') }}</p>
      <el-form label-position="top">
        <el-form-item :label="t('knowledge.publishTitle')"><el-input v-model="publicationTitle" :disabled="publicationStarted" maxlength="120" /></el-form-item>
        <el-form-item :label="t('knowledge.publishDescription')"><el-input v-model="publicationDescription" :disabled="publicationStarted" type="textarea" maxlength="2000" :rows="4" /></el-form-item>
        <el-checkbox v-model="publicationConfirmed" data-testid="knowledge-publish-confirm">{{ t('knowledge.publishConfirmation') }}</el-checkbox>
      </el-form>
      <template #footer>
        <el-button :disabled="publishing" @click="publicationOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!publicationConfirmed || !publicationTitle.trim()" :loading="publishing" @click="publish">{{ t('knowledge.publishMarket') }}</el-button>
      </template>
    </el-dialog>
  </el-drawer>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CircleCheck, Clock, Document, Download, FolderOpened } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'
import { createKnowledgePublicationId, knowledgeApi, knowledgeFailureReason, type KnowledgeDocument, type KnowledgeDocumentVersion, type KnowledgePack, type KnowledgePackJob } from '@/api/knowledge'
import KnowledgeSourceImport from './KnowledgeSourceImport.vue'

const props = defineProps<{ modelValue: boolean; pack: KnowledgePack | null; jobs: KnowledgePackJob[] }>()
const emit = defineEmits<{ 'update:modelValue': [boolean]; updated: [] }>()
const { t, locale } = useI18n()
const documents = ref<KnowledgeDocument[]>([])
const loading = ref(false)
const mutating = ref(false)
const sourceInput = ref<HTMLInputElement | null>(null)
const sourceFile = ref<File | null>(null)
const sourceDialog = ref(false)
const replacing = ref<KnowledgeDocument | null>(null)
const historyOpen = ref(false)
const historySource = ref<KnowledgeDocument | null>(null)
const acknowledged = ref(false)
const requiresAcknowledgment = computed(() => Boolean(props.pack && props.pack.enabled !== false))
const versions = ref<KnowledgeDocumentVersion[]>([])
const versionsLoading = ref(false)
const exporting = ref(false)
// Stored-vector provenance, rather than local indexing policy or the current
// encoder's ready count, determines whether a complete package can be exported.
const exportReady = computed(() => props.pack?.export_ready === true)
// A disabled local policy needs an explicit action; a broken pack needs repair instead.
const exportReadinessHint = computed(() => {
  if (exportReady.value) return 'knowledge.exportReadyHint'
  if (props.pack?.broken) return 'knowledge.brokenHint'
  // Only missing vectors can be filled by turning local vectors on; other
  // readiness gaps (a stale revision or mixed specs) keep the waiting hint.
  const pack = props.pack
  const missing = typeof pack?.chunks_ready === 'number' && typeof pack?.chunks_total === 'number'
    && pack.chunks_ready < pack.chunks_total
  return pack?.local_embedding === false && missing ? 'knowledge.exportLocalVectorsOff' : 'knowledge.exportVectorWaiting'
})
const downloading = ref('')
const deleting = ref('')
// A successful durable deletion must not reappear while parent polling catches up.
const deletedExports = ref(new Set<string>())
const publicationOpen = ref(false)
const publicationJob = ref<KnowledgePackJob | null>(null)
const publicationTitle = ref('')
const publicationDescription = ref('')
const publicationConfirmed = ref(false)
const publicationStarted = ref(false)
const publishing = ref(false)
let publicationId = ''
// Keep uncertain requests per immutable artifact, even after opening another form.
const publicationRequests = new Map<string, { id: string; title: string; description: string }>()
let generation = 0
let historyGeneration = 0
// Retain old tasks on disk without exposing a retired JSON artifact's download.
const exports = computed(() => props.jobs.filter(job => job.kind === 'pack_export'
  && job.pack_id === props.pack?.pack_id && !deletedExports.value.has(job.job_id)
  && (!job.result || job.result.format_version === 2)))

watch([() => props.modelValue, () => props.pack?.pack_id], () => {
  generation += 1
  historyGeneration += 1
  documents.value = []
  historyOpen.value = false
  acknowledged.value = false
  sourceDialog.value = false
  if (props.modelValue && props.pack) void loadDocuments()
}, { immediate: true })
watch(requiresAcknowledgment, () => { acknowledged.value = false })
watch(() => props.jobs.map(job => `${job.job_id}:${job.state}`).join('|'), () => {
  if (props.modelValue && props.pack) void loadDocuments()
})
onBeforeUnmount(() => { generation += 1; historyGeneration += 1 })

function failure(error: unknown) { ElMessage.error(t(`knowledge.reasons.${knowledgeFailureReason(error)}`)) }
function formatTime(value: string, seconds = false) { const date = value ? dayjs(value) : null; return date?.isValid() ? date.format(seconds ? 'YYYY-MM-DD HH:mm:ss' : 'YYYY-MM-DD HH:mm') : '—' }
function formatSize(bytes: number) {
  if (!Number.isFinite(bytes) || bytes < 0) return '—'
  const unit = bytes >= 1024 * 1024 ? 'MiB' : bytes >= 1024 ? 'KiB' : 'B'
  const size = unit === 'MiB' ? bytes / (1024 * 1024) : unit === 'KiB' ? bytes / 1024 : bytes
  return `${new Intl.NumberFormat(locale.value, { maximumFractionDigits: 1 }).format(size)} ${unit}`
}

async function loadDocuments() {
  // Local source ownership is a fact about this installation, not the origin label.
  if (!props.pack || props.pack.sources_managed !== true) {
    documents.value = []
    loading.value = false
    return
  }
  const request = ++generation
  loading.value = true
  try {
    const response = await knowledgeApi.sources(props.pack.pack_id)
    if (request === generation) documents.value = response.sources ?? []
  } catch (error) { if (request === generation) failure(error) }
  finally { if (request === generation) loading.value = false }
}

function chooseSource(row?: KnowledgeDocument) {
  replacing.value = row ?? null
  sourceInput.value?.click()
}

function selectSource(event: Event) {
  const input = event.target as HTMLInputElement
  sourceFile.value = input.files?.[0] ?? null
  input.value = ''
  sourceDialog.value = Boolean(sourceFile.value)
}

function sourceCommitted() { emit('updated'); void loadDocuments() }

async function showVersions(row: KnowledgeDocument) {
  if (!props.pack) return
  const request = ++historyGeneration
  historySource.value = row
  acknowledged.value = false
  versions.value = []
  historyOpen.value = true
  versionsLoading.value = true
  try {
    const response = await knowledgeApi.sourceVersions({ pack_id: props.pack.pack_id, doc_id: row.doc_id })
    if (request === historyGeneration) versions.value = response.versions
  } catch (error) { if (request === historyGeneration) failure(error) }
  finally { if (request === historyGeneration) versionsLoading.value = false }
}

async function removeSource(row: KnowledgeDocument) {
  if (!props.pack || mutating.value) return
  const packId = props.pack.pack_id
  const docId = row.doc_id
  mutating.value = true
  try {
    try { await ElMessageBox.confirm(t('knowledge.removeSourceConfirm', { name: row.name }), t('common.warning'), { type: 'warning' }) }
    catch { return }
    // Polling or navigation must not retarget an already visible confirmation.
    if (!props.modelValue || props.pack?.pack_id !== packId) {
      ElMessage.error(t('knowledge.reasons.not_found'))
      return
    }
    await knowledgeApi.removeSource({ pack_id: packId, doc_id: docId })
    sourceCommitted()
  }
  catch (error) { failure(error) }
  finally { mutating.value = false }
}

async function restore(version: KnowledgeDocumentVersion) {
  if (!props.pack || !historySource.value || mutating.value || (requiresAcknowledgment.value && !acknowledged.value)) return
  mutating.value = true
  try {
    // History can include versions rejected on activation. Restoring into an
    // enabled pack needs the same document consent as appending a source.
    await knowledgeApi.restoreSource({ pack_id: props.pack.pack_id, doc_id: historySource.value.doc_id, version: version.version,
      enabled_confirmed: requiresAcknowledgment.value && acknowledged.value })
    historyOpen.value = false
    ElMessage.success(t('knowledge.sourceQueued'))
    sourceCommitted()
  } catch (error) { failure(error) }
  finally { mutating.value = false }
}

async function exportPack() {
  if (!props.pack || exporting.value || !exportReady.value) return
  exporting.value = true
  try {
    // Release versions belong to the durable server task, not editable UI state.
    await knowledgeApi.exportPack({ pack_id: props.pack.pack_id })
    ElMessage.success(t('knowledge.exportQueued'))
    emit('updated')
  } catch (error) { failure(error) }
  finally { exporting.value = false }
}

async function download(job: KnowledgePackJob) {
  if (!job.result || downloading.value || deleting.value === job.job_id) return
  downloading.value = job.job_id
  try {
    const { blob, filename } = await knowledgeApi.artifact(job.result.artifact_id)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    // The response names the immutable artifact, including older exports whose
    // task records still contain internal identifiers in their old filenames.
    link.download = filename || job.result.filename
    link.click()
    // Let the browser start reading before releasing the local object URL.
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error) { failure(error) }
  finally { downloading.value = '' }
}

async function deleteExport(job: KnowledgePackJob) {
  if (job.state !== 'active' || !job.result?.artifact_id || deleting.value || downloading.value === job.job_id
    || publishing.value || (publicationOpen.value && publicationJob.value?.job_id === job.job_id)) return
  // Reserve this row before confirmation to prevent duplicate dialogs/actions.
  deleting.value = job.job_id
  try {
    try {
      await ElMessageBox.confirm(t('knowledge.deleteExportConfirm', {
        name: job.result.name || props.pack?.source.name || t('knowledge.pack'), time: formatTime(job.created_at, true),
      }), t('knowledge.deleteExportTitle'), {
        type: 'warning', confirmButtonText: t('knowledge.deleteExport'), cancelButtonText: t('common.cancel'),
      })
    } catch { return }
    const result = await knowledgeApi.deleteExport({ job_id: job.job_id })
    deletedExports.value.add(job.job_id)
    ElMessage.success(t(result.artifact_retained ? 'knowledge.exportDeletedRetained' : 'knowledge.exportDeleted'))
    emit('updated')
  } catch (error) { failure(error) }
  finally { deleting.value = '' }
}

function preparePublication(job: KnowledgePackJob) {
  if (!job.result || deleting.value === job.job_id) return
  const frozen = publicationRequests.get(job.result.artifact_id)
  publicationJob.value = job
  publicationId = frozen?.id ?? createKnowledgePublicationId()
  // Count Unicode code points like the market contract, preserving surrogate pairs.
  publicationTitle.value = frozen?.title ?? Array.from(job.result.name || props.pack?.source.name || job.pack_id).slice(0, 120).join('')
  publicationDescription.value = frozen?.description ?? ''
  publicationStarted.value = Boolean(frozen)
  publicationConfirmed.value = false
  publicationOpen.value = true
}

async function publish() {
  const job = publicationJob.value
  if (!job?.result || !publicationConfirmed.value || publishing.value || !publicationTitle.value.trim()) return
  publishing.value = true
  publicationStarted.value = true
  const frozen = publicationRequests.get(job.result.artifact_id) ?? {
    id: publicationId, title: publicationTitle.value.trim(), description: publicationDescription.value,
  }
  publicationRequests.set(job.result.artifact_id, frozen)
  try {
    // A retry reuses the frozen artifact, payload and UUID after a response loss.
    // Credentials remain in the existing plugin bridge, never in this form.
    await knowledgeApi.publish({
      pack_id: job.pack_id, artifact_id: job.result.artifact_id, publish_confirmed: true,
      client_request_id: frozen.id, title: frozen.title, description: frozen.description,
    })
    ElMessage.success(t('knowledge.publishSucceeded'))
    publicationOpen.value = false
  } catch (error) { failure(error) }
  finally { publishing.value = false }
}
</script>

<style scoped>
.pack-summary { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.export-panel { margin-top: 28px; color: var(--el-text-color-primary); }
.export-create { padding: 20px; border: 1px solid var(--el-color-primary-light-7); border-radius: 16px; background: var(--el-color-primary-light-9); }
.export-heading { display: flex; align-items: flex-start; gap: 14px; }
.export-icon, .artifact-icon { display: inline-flex; align-items: center; justify-content: center; flex: 0 0 auto; width: 44px; height: 44px; border-radius: 12px; background: var(--el-bg-color); color: var(--el-color-primary); }
.export-heading-copy { flex: 1; min-width: 0; }
.export-heading h3 { margin: 0 0 7px; font-size: 17px; font-weight: 600; line-height: 1.4; }
.export-heading p { margin: 0; color: var(--el-text-color-regular); font-size: 13px; line-height: 1.65; }
.export-actions { flex: 0 0 auto; padding-top: 2px; }
.export-actions .el-button, .artifact-actions .el-button { min-height: 36px; border-radius: 10px; }
.export-actions .el-icon, .artifact-actions .el-icon { margin-right: 6px; }
.export-readiness { display: flex; align-items: flex-start; gap: 7px; margin-top: 18px; color: var(--el-text-color-regular); font-size: 13px; line-height: 1.6; }
.export-readiness .el-icon { flex-shrink: 0; margin-top: 3px; }
.export-readiness.is-ready { color: var(--el-text-color-regular); }
.export-readiness.is-ready .el-icon { color: var(--el-color-success); }
.export-version-hint { margin: 6px 0 0 21px; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; }
.export-history-heading { display: flex; align-items: center; gap: 8px; margin: 26px 0 12px; }
.export-history-heading h4 { margin: 0; font-size: 14px; font-weight: 600; }
.export-count { display: inline-flex; align-items: center; justify-content: center; min-width: 22px; height: 22px; padding: 0 5px; border-radius: 7px; background: var(--el-fill-color); color: var(--el-text-color-secondary); font-size: 12px; }
.export-history { display: grid; gap: 12px; }
.artifact-row { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 12px; align-items: start; padding: 16px; border: 1px solid var(--el-border-color-lighter); border-radius: 14px; background: var(--el-bg-color); }
.artifact-icon { width: 40px; height: 44px; border-radius: 10px; background: var(--el-fill-color-light); }
.artifact-info { min-width: 0; }
.artifact-name, .publication-pack-name { font-size: 14px; font-weight: 600; line-height: 1.55; overflow-wrap: anywhere; }
.artifact-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; margin-top: 7px; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.5; }
.artifact-format { padding: 0 5px; border: 1px solid var(--el-border-color-lighter); border-radius: 4px; font-size: 10px; letter-spacing: .5px; }
.artifact-status { margin-top: 1px; }
.artifact-status.el-tag--success { color: var(--el-text-color-regular); }
.artifact-actions { grid-column: 2 / -1; display: flex; justify-content: flex-end; gap: 8px; padding-top: 2px; }
.artifact-actions .el-button + .el-button { margin-left: 0; }
.export-empty { display: flex; align-items: center; justify-content: center; gap: 12px; padding: 26px 18px; border: 1px dashed var(--el-border-color); border-radius: 14px; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
@media (max-width: 600px) {
  .export-create { padding: 16px; }
  .export-heading { flex-wrap: wrap; gap: 12px; }
  .export-icon { width: 38px; height: 38px; }
  .export-heading-copy { flex-basis: calc(100% - 50px); }
  .export-actions { width: 100%; padding-top: 4px; }
  .export-actions .el-button { width: 100%; }
  .artifact-row { grid-template-columns: minmax(0, 1fr) auto; gap: 10px; padding: 14px; }
  .artifact-icon { display: none; }
  .artifact-actions { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .artifact-actions .el-button { flex: 1; min-width: 0; height: auto; white-space: normal; }
  .artifact-actions .artifact-delete { grid-column: 1 / -1; justify-self: end; }
}
</style>
