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
        <h3>{{ t('knowledge.exportTitle') }}</h3>
        <p v-if="!pack.retained">{{ t('knowledge.exportScope') }}</p>
        <el-input v-if="!pack.retained" v-model="exportVersion" :aria-label="t('knowledge.sourceVersion')" maxlength="100" class="export-version" />
        <div v-if="!pack.retained" class="export-actions">
          <el-button :loading="exporting === 1" :disabled="exporting !== null" @click="exportPack(1)">{{ t('knowledge.exportText') }}</el-button>
          <el-button type="primary" :loading="exporting === 2" :disabled="exporting !== null || pack.vector_state !== 'complete'" @click="exportPack(2)">{{ t('knowledge.exportVectors') }}</el-button>
        </div>
        <p v-if="!pack.retained && pack.vector_state !== 'complete'" class="muted">{{ t('knowledge.exportVectorWaiting') }}</p>
        <div v-for="job in exports" :key="job.job_id" class="artifact-row">
          <span>{{ job.result?.filename || job.job_id }}</span>
          <template v-if="job.state === 'active' && job.result?.artifact_id">
            <el-button :loading="downloading === job.job_id" @click="download(job)">{{ t('knowledge.downloadExport') }}</el-button>
            <el-button v-if="job.result.format_version === 2" type="primary" plain @click="preparePublication(job)">{{ t('knowledge.publishMarket') }}</el-button>
          </template>
          <el-tag v-else effect="plain">{{ t(`knowledge.${job.state === 'queued' ? 'jobQueued' : job.state === 'building' ? 'jobBuilding' : job.state === 'failed' ? 'jobFailed' : 'jobCancelled'}`) }}</el-tag>
        </div>
      </section>
    </template>
    <KnowledgeSourceImport v-model="sourceDialog" :file="sourceFile" :pack="pack" :doc-id="replacing?.doc_id" @committed="sourceCommitted" />
    <el-dialog v-model="historyOpen" :title="t('knowledge.sourceHistory')" append-to-body width="min(640px, 94vw)">
      <el-table :data="versions" v-loading="versionsLoading">
        <el-table-column prop="version" :label="t('knowledge.sourceVersion')" width="90" />
        <el-table-column prop="name" :label="t('knowledge.sourceName')" min-width="160" />
        <el-table-column :label="t('knowledge.time')" min-width="170"><template #default="scope">{{ formatTime(scope.row.created_at) }}</template></el-table-column>
        <el-table-column :label="t('knowledge.actions')" width="100"><template #default="scope"><el-button link type="primary" :disabled="scope.row.current || mutating" @click="restore(scope.row)">{{ t('knowledge.restoreSource') }}</el-button></template></el-table-column>
      </el-table>
    </el-dialog>
    <el-dialog v-model="publicationOpen" :title="t('knowledge.publishMarket')" append-to-body width="min(640px, 94vw)" :close-on-click-modal="!publishing" :close-on-press-escape="!publishing" :show-close="!publishing">
      <el-alert type="warning" :closable="false" show-icon :title="t('knowledge.publishDisclosure')" />
      <p>{{ publicationJob?.result?.filename }}</p>
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
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'
import { createKnowledgePublicationId, knowledgeApi, knowledgeFailureReason, type KnowledgeDocument, type KnowledgeDocumentVersion, type KnowledgePack, type KnowledgePackJob } from '@/api/knowledge'
import KnowledgeSourceImport from './KnowledgeSourceImport.vue'

const props = defineProps<{ modelValue: boolean; pack: KnowledgePack | null; jobs: KnowledgePackJob[] }>()
const emit = defineEmits<{ 'update:modelValue': [boolean]; updated: [] }>()
const { t } = useI18n()
const documents = ref<KnowledgeDocument[]>([])
const loading = ref(false)
const mutating = ref(false)
const sourceInput = ref<HTMLInputElement | null>(null)
const sourceFile = ref<File | null>(null)
const sourceDialog = ref(false)
const replacing = ref<KnowledgeDocument | null>(null)
const historyOpen = ref(false)
const historySource = ref<KnowledgeDocument | null>(null)
const versions = ref<KnowledgeDocumentVersion[]>([])
const versionsLoading = ref(false)
const exporting = ref<1 | 2 | null>(null)
const exportVersion = ref('1')
const downloading = ref('')
const publicationOpen = ref(false)
const publicationJob = ref<KnowledgePackJob | null>(null)
const publicationTitle = ref('')
const publicationDescription = ref('')
const publicationConfirmed = ref(false)
const publicationStarted = ref(false)
const publishing = ref(false)
let publicationId = ''
let generation = 0
let historyGeneration = 0
const exports = computed(() => props.jobs.filter(job => job.kind === 'pack_export' && job.pack_id === props.pack?.pack_id))

watch([() => props.modelValue, () => props.pack?.pack_id], () => {
  generation += 1
  historyGeneration += 1
  documents.value = []
  historyOpen.value = false
  sourceDialog.value = false
  exportVersion.value = '1'
  if (props.modelValue && props.pack) void loadDocuments()
}, { immediate: true })
watch(() => props.jobs.map(job => `${job.job_id}:${job.state}`).join('|'), () => {
  if (props.modelValue && props.pack) void loadDocuments()
})
onBeforeUnmount(() => { generation += 1; historyGeneration += 1 })

function failure(error: unknown) { ElMessage.error(t(`knowledge.reasons.${knowledgeFailureReason(error)}`)) }
function formatTime(value: string) { const date = dayjs(value); return date.isValid() ? date.format('YYYY-MM-DD HH:mm') : '—' }

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
  try { await ElMessageBox.confirm(t('knowledge.removeSourceConfirm', { name: row.name }), t('common.warning'), { type: 'warning' }) }
  catch { return }
  mutating.value = true
  try { await knowledgeApi.removeSource({ pack_id: props.pack.pack_id, doc_id: row.doc_id }); sourceCommitted() }
  catch (error) { failure(error) }
  finally { mutating.value = false }
}

async function restore(version: KnowledgeDocumentVersion) {
  if (!props.pack || !historySource.value || mutating.value) return
  mutating.value = true
  try {
    await knowledgeApi.restoreSource({ pack_id: props.pack.pack_id, doc_id: historySource.value.doc_id, version: version.version })
    historyOpen.value = false
    ElMessage.success(t('knowledge.sourceQueued'))
    sourceCommitted()
  } catch (error) { failure(error) }
  finally { mutating.value = false }
}

async function exportPack(format_version: 1 | 2) {
  if (!props.pack || exporting.value !== null) return
  exporting.value = format_version
  try {
    await knowledgeApi.exportPack({ pack_id: props.pack.pack_id, format_version, version: exportVersion.value.trim() || '1' })
    ElMessage.success(t('knowledge.exportQueued'))
    emit('updated')
  } catch (error) { failure(error) }
  finally { exporting.value = null }
}

async function download(job: KnowledgePackJob) {
  if (!job.result || downloading.value) return
  downloading.value = job.job_id
  try {
    const blob = await knowledgeApi.artifact(job.result.artifact_id)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = job.result.filename
    link.click()
    // Let the browser start reading before releasing the local object URL.
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error) { failure(error) }
  finally { downloading.value = '' }
}

function preparePublication(job: KnowledgePackJob) {
  if (!job.result) return
  if (publicationJob.value?.result?.artifact_id !== job.result.artifact_id) {
    publicationJob.value = job
    publicationId = createKnowledgePublicationId()
    // The market title limit is narrower than the local source display name.
    publicationTitle.value = (props.pack?.source.name || job.pack_id).slice(0, 120)
    publicationDescription.value = ''
    publicationStarted.value = false
  }
  publicationConfirmed.value = false
  publicationOpen.value = true
}

async function publish() {
  const job = publicationJob.value
  if (!job?.result || !publicationConfirmed.value || publishing.value || !publicationTitle.value.trim()) return
  publishing.value = true
  publicationStarted.value = true
  try {
    // A retry reuses the frozen artifact, payload and UUID after a response loss.
    // Credentials remain in the existing plugin bridge, never in this form.
    await knowledgeApi.publish({
      pack_id: job.pack_id, artifact_id: job.result.artifact_id, publish_confirmed: true,
      client_request_id: publicationId, title: publicationTitle.value.trim(), description: publicationDescription.value,
    })
    ElMessage.success(t('knowledge.publishSucceeded'))
    publicationOpen.value = false
  } catch (error) { failure(error) }
  finally { publishing.value = false }
}
</script>

<style scoped>
.pack-summary, .export-actions, .artifact-row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.export-panel { margin-top: 24px; border-top: 1px solid var(--el-border-color-lighter); padding-top: 12px; }
.export-version { max-width: 220px; margin-bottom: 12px; }
.artifact-row { justify-content: space-between; margin-top: 12px; overflow-wrap: anywhere; }
.muted { color: var(--el-text-color-secondary); font-size: 13px; }
</style>
