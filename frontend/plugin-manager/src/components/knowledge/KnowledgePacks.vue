<template>
  <div>
    <div class="toolbar">
      <input ref="fileInput" type="file" accept=".json,.zip,.txt,.md,.docx,.pdf" hidden @change="emit('import', $event)" />
      <el-button type="primary" :loading="importing" @click="fileInput?.click()">{{ t('knowledge.importPack') }}</el-button>
      <span class="toolbar__hint">{{ t('knowledge.importHint') }}</span>
      <el-button class="toolbar__end" :loading="marketOpening" @click="emit('open-market')">
        {{ t('knowledge.openMarket') }}
      </el-button>
    </div>

    <section v-if="activeJobs.length" class="job-panel job-panel--active" aria-live="polite">
      <div class="job-panel__heading">
        <strong>{{ t('knowledge.activeJobs') }}</strong>
        <el-tag type="primary" effect="plain" size="small">{{ activeJobs.length }}</el-tag>
      </div>
      <p class="job-panel__hint">{{ t('knowledge.activeJobsHint') }}</p>
      <div v-for="job in activeJobs" :key="job.job_id" class="job-row" :data-job-id="job.job_id">
        <div class="job-row__identity">
          <strong>{{ job.pack_id || job.job_id }}</strong>
          <span>{{ t('knowledge.jobMeta', { entries: job.entries_total ?? 0, chunks: job.chunks_total ?? 0 }) }}</span>
        </div>
        <el-progress class="job-row__progress" :percentage="100" :indeterminate="true" :show-text="false" :stroke-width="6" :duration="2" />
        <el-tag type="primary" effect="plain">{{ jobStateLabel(job.state) }}</el-tag>
        <el-button
          plain
          :loading="pendingJobs.has(job.job_id)"
          :disabled="pendingJobs.has(job.job_id)"
          @click="emit('cancel-job', job)"
        >
          {{ t('knowledge.cancelJob') }}
        </el-button>
      </div>
    </section>

    <section v-if="finishedFailedJobs.length" class="job-panel job-panel--failed" aria-live="polite">
      <div class="job-panel__heading">
        <strong>{{ t('knowledge.failedJobs') }}</strong>
        <el-tag type="warning" effect="plain" size="small">{{ finishedFailedJobs.length }}</el-tag>
      </div>
      <div v-for="job in finishedFailedJobs" :key="job.job_id" class="job-row" :data-job-id="job.job_id">
        <div class="job-row__identity">
          <strong>{{ job.pack_id || job.job_id }}</strong>
          <span>{{ jobStateLabel(job.state) }}<template v-if="job.reason"> · {{ reasonMessage(job.reason) }}</template></span>
        </div>
        <el-button
          plain
          :loading="pendingJobs.has(job.job_id)"
          :disabled="pendingJobs.has(job.job_id)"
          @click="emit('retry-job', job)"
        >
          {{ t('knowledge.retryJob') }}
        </el-button>
        <el-button
          plain
          :loading="pendingJobs.has(job.job_id)"
          :disabled="pendingJobs.has(job.job_id)"
          @click="emit('discard-job', job)"
        >
          {{ t('knowledge.discardJob') }}
        </el-button>
      </div>
    </section>

    <div class="table-shell">
      <el-table
        class="packs-table"
        :data="packs"
        v-loading="packsLoading"
        row-key="pack_id"
        :row-class-name="packRowClass"
        :empty-text="t('knowledge.noPacks')"
      >
        <el-table-column :label="t('knowledge.pack')" min-width="210">
          <template #default="scope">
            <div class="pack-identity">
              <div class="pack-identity__title">
                <strong :title="scope.row.pack_id">{{ scope.row.source?.name || scope.row.pack_id }}</strong>
                <el-tag size="small" effect="plain">{{ originLabel(scope.row) }}</el-tag>
                <el-tag v-if="scope.row.retained" size="small" type="info">{{ t('knowledge.retainedPack') }}</el-tag>
                <el-tooltip v-if="scope.row.broken" :content="t('knowledge.brokenHint')" placement="top">
                  <el-tag type="danger" size="small">{{ t('knowledge.brokenTag') }}</el-tag>
                </el-tooltip>
                <el-tooltip v-if="scope.row.subscription" :content="t('knowledge.subscribedHint')" placement="top">
                  <el-tag type="success" size="small" effect="plain">
                    {{ t('knowledge.subscribedTag', { version: scope.row.subscription.version }) }}
                  </el-tag>
                </el-tooltip>
              </div>
              <span class="pack-identity__meta">{{ scope.row.pack_id }}</span>
              <span class="pack-identity__meta">
                <a
                  v-if="scope.row.source?.homepage"
                  href="#"
                  class="pack-identity__link"
                  @click.prevent="openExternalUrl(scope.row.source.homepage)"
                >{{ t('knowledge.homepage') }}</a>
                <template v-if="scope.row.source?.homepage && scope.row.source?.license"> · </template>
                <template v-if="scope.row.source?.license">{{ t('knowledge.license') }}: {{ scope.row.source.license }}</template>
              </span>
            </div>
          </template>
        </el-table-column>
        <el-table-column :label="t('knowledge.packEnabled')" width="96" align="center">
          <template #default="scope">
            <el-switch
              :model-value="scope.row.enabled !== false"
              :disabled="scope.row.broken || scope.row.retained || pendingPacks.has(scope.row.pack_id)"
              :aria-label="t('knowledge.packEnabled')"
              :data-testid="`pack-enabled-${scope.row.pack_id}`"
              @change="emit('enabled', scope.row, Boolean($event))"
            />
          </template>
        </el-table-column>
        <el-table-column :label="t('knowledge.materialType')" width="170">
          <template #default="scope">
            <el-select
              :model-value="scope.row.material_type_override ?? MATERIAL_FOLLOW"
              :disabled="scope.row.broken || scope.row.retained || pendingPacks.has(scope.row.pack_id)"
              :aria-label="t('knowledge.materialType')"
              size="small"
              @change="emit('material-type', scope.row, String($event))"
            >
              <el-option
                :value="MATERIAL_FOLLOW"
                :label="t('knowledge.materialFollow', { type: materialTypeLabel(scope.row.declared_material_type) })"
              />
              <el-option value="knowledge" :label="t('knowledge.typeKnowledge')" />
              <el-option value="corpus" :label="t('knowledge.typeCorpus')" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column :label="t('knowledge.entries')" width="110" align="center">
          <template #default="scope">
            <div class="pack-count">
              <strong>{{ scope.row.entries ?? 0 }}</strong>
              <small v-if="scope.row.disabled_entries > 0">{{ t('knowledge.disabledCount', { count: scope.row.disabled_entries }) }}</small>
            </div>
          </template>
        </el-table-column>
        <el-table-column :label="t('knowledge.vectorIndex')" width="150">
          <template #default="scope">
            <div class="pack-count pack-count--left">
              <el-tag :type="vectorStateTagType(scope.row.vector_state)" effect="plain" size="small">
                {{ vectorStateLabel(scope.row.vector_state) }}
              </el-tag>
              <small v-if="scope.row.chunks_total > 0">
                {{ scope.row.chunks_ready }} / {{ scope.row.chunks_total }}
                <template v-if="scope.row.chunks_failed > 0"> · {{ t('knowledge.chunksFailed', { count: scope.row.chunks_failed }) }}</template>
              </small>
            </div>
          </template>
        </el-table-column>
        <el-table-column width="120" align="center">
          <template #header>
            <el-tooltip :content="t('knowledge.localVectorsHint')" placement="top">
              <span class="column-hint">{{ t('knowledge.localVectors') }}</span>
            </el-tooltip>
          </template>
          <template #default="scope">
            <el-switch
              :model-value="scope.row.local_embedding === true"
              :disabled="scope.row.broken || scope.row.retained || pendingPacks.has(scope.row.pack_id)"
              :aria-label="t('knowledge.localVectors')"
              @change="emit('index-policy', scope.row, Boolean($event))"
            />
          </template>
        </el-table-column>
        <el-table-column width="120" align="center">
          <template #header>
            <el-tooltip :content="t('knowledge.autoContextHint')" placement="top">
              <span class="column-hint">{{ t('knowledge.autoContext') }}</span>
            </el-tooltip>
          </template>
          <template #default="scope">
            <el-switch
              :model-value="scope.row.auto_context === true"
              :disabled="scope.row.broken || scope.row.retained || pendingPacks.has(scope.row.pack_id)"
              :aria-label="t('knowledge.autoContext')"
              @change="emit('auto-context', scope.row, Boolean($event))"
            />
          </template>
        </el-table-column>
        <el-table-column :label="t('knowledge.actions')" width="165" align="center">
          <template #default="scope">
            <el-button link type="primary" @click="emit('open-pack', scope.row)">{{ t('knowledge.managePack') }}</el-button>
            <el-button
              link
              type="danger"
              :disabled="scope.row.retained || pendingPacks.has(scope.row.pack_id)"
              @click="emit('remove-pack', scope.row)"
            >
              {{ t('knowledge.remove') }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <p class="footnote">{{ t('knowledge.autoContextHint') }}</p>
  </div>
</template>

<script setup lang="ts">
/** Pack/task display emits user intent; admission, consent and polling stay on the page. */
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  isActiveKnowledgeJob, isKnownKnowledgeReason,
  type KnowledgeMaterialType, type KnowledgePack, type KnowledgePackJob, type KnowledgeVectorState,
} from '@/api/knowledge'
import { vectorStateTagType } from '@/utils/knowledgeDisplay'
import { openExternalUrl } from '@/utils/openExternal'

const props = defineProps<{
  packs: KnowledgePack[]
  packsLoading: boolean
  jobs: KnowledgePackJob[]
  retriedJobs: ReadonlySet<string>
  pendingJobs: ReadonlySet<string>
  pendingPacks: ReadonlySet<string>
  importing: boolean
  marketOpening: boolean
}>()
const emit = defineEmits<{
  import: [event: Event]
  'open-market': []
  'cancel-job': [job: KnowledgePackJob]
  'retry-job': [job: KnowledgePackJob]
  'discard-job': [job: KnowledgePackJob]
  enabled: [pack: KnowledgePack, enabled: boolean]
  'material-type': [pack: KnowledgePack, value: string]
  'index-policy': [pack: KnowledgePack, enabled: boolean]
  'auto-context': [pack: KnowledgePack, enabled: boolean]
  'open-pack': [pack: KnowledgePack]
  'remove-pack': [pack: KnowledgePack]
}>()
const { t } = useI18n()
// Keep the real input in the component that renders it; pass its event unchanged.
const fileInput = ref<HTMLInputElement | null>(null)
const MATERIAL_FOLLOW = '__follow__'
const EMPTY_MARK = '—'
const activeJobs = computed(() => props.jobs.filter(isActiveKnowledgeJob))
const finishedFailedJobs = computed(() =>
  props.jobs.filter((job) => (job.state === 'failed' || job.state === 'cancelled') && !props.retriedJobs.has(job.job_id)),
)

function reasonMessage(reason: unknown): string {
  const key = isKnownKnowledgeReason(reason) ? reason : 'operation_failed'
  return t(`knowledge.reasons.${key}`)
}

function materialTypeLabel(type: KnowledgeMaterialType | string | null | undefined): string {
  return type === 'corpus' ? t('knowledge.typeCorpus') : t('knowledge.typeKnowledge')
}

function vectorStateLabel(state: KnowledgeVectorState | string): string {
  const keys: Record<string, string> = {
    none: 'knowledge.vectorNone',
    complete: 'knowledge.vectorComplete',
    building: 'knowledge.vectorBuilding',
    waiting: 'knowledge.vectorWaiting',
    paused: 'knowledge.vectorPaused',
    off: 'knowledge.vectorOff',
    partial: 'knowledge.vectorPartial',
  }
  return keys[state] ? t(keys[state]) : String(state || EMPTY_MARK)
}

function jobStateLabel(state: string): string {
  const keys: Record<string, string> = {
    queued: 'knowledge.jobQueued',
    building: 'knowledge.jobBuilding',
    active: 'knowledge.jobActive',
    failed: 'knowledge.jobFailed',
    cancelled: 'knowledge.jobCancelled',
  }
  return keys[state] ? t(keys[state]) : String(state || EMPTY_MARK)
}

function packRowClass({ row }: { row: KnowledgePack }): string {
  return row.broken ? 'is-broken' : ''
}

function originLabel(row: KnowledgePack) {
  const origin = row.origin ?? (row.subscription ? 'market' : 'builtin')
  return t(`knowledge.${origin === 'imported' ? 'originImported' : origin === 'market' ? 'originMarket' : 'originBuiltin'}`)
}
</script>

<!-- Scope shared table chrome to this component, never to unrelated pages. -->
<style scoped src="./knowledgeTables.css"></style>

<style scoped>
.job-panel {
  display: grid;
  gap: 10px;
  min-width: 0;
  margin-bottom: 14px;
  padding: 14px;
  border-radius: var(--radius-panel);
}

.job-panel--active {
  border: 1px solid var(--el-color-primary-light-7);
  background: var(--el-color-primary-light-9);
}

.job-panel--failed {
  border: 1px solid var(--el-color-warning-light-5);
  background: var(--el-color-warning-light-9);
}

.job-panel__heading {
  display: flex;
  align-items: center;
  gap: 8px;
}

.job-panel__hint {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.job-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid var(--knowledge-line);
  border-radius: var(--radius-control);
  background: var(--knowledge-surface);
}

.job-row__identity {
  display: grid;
  flex: 1 1 220px;
  gap: 3px;
  min-width: 0;
}

.job-row__identity strong,
.job-row__identity span {
  overflow-wrap: anywhere;
}

.job-row__identity strong {
  color: var(--el-text-color-primary);
  font-size: 13px;
}

.job-row__identity span {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.job-row__progress {
  flex: 1 1 160px;
  min-width: 120px;
}

.packs-table :deep(.el-table__row.is-broken) {
  --el-table-tr-bg-color: var(--el-color-danger-light-9);
}

.pack-identity {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.pack-identity__title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.pack-identity__title strong {
  min-width: 0;
  overflow: hidden;
  color: var(--el-text-color-primary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pack-identity__meta {
  overflow: hidden;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pack-identity__link {
  color: var(--el-color-primary);
  text-decoration: none;
}

.pack-count {
  display: grid;
  justify-items: center;
  gap: 2px;
  font-variant-numeric: tabular-nums;
}

.pack-count--left {
  justify-items: start;
}

.pack-count small {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.column-hint {
  border-bottom: 1px dashed var(--el-text-color-placeholder);
  cursor: help;
}

.footnote {
  margin: 10px 2px 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.6;
}
</style>
