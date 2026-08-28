<template>
  <div class="knowledge-manager">
    <header class="page-heading">
      <div>
        <h1>{{ t('knowledge.title') }}</h1>
        <p>{{ t('knowledge.subtitle') }}</p>
      </div>
      <el-button :loading="loading" @click="refreshAll">{{ t('common.refresh') }}</el-button>
    </header>

    <div class="market-entry">
      <el-alert :title="t('knowledge.marketConnected')" type="info" :closable="false" show-icon />
      <el-tag v-if="marketAuth.authenticated" type="success" effect="plain">
        {{ t('market.accountConnected', { name: marketAuthDisplayName }) }}
      </el-tag>
      <span v-else class="market-login-hint">{{ t('knowledge.loginRequired') }}</span>
      <el-button
        type="primary"
        plain
        :disabled="!marketAuth.authenticated"
        :loading="marketOpening"
        @click="openKnowledgeMarket"
      >
        {{ t('knowledge.openMarket') }}
      </el-button>
    </div>

    <el-tabs v-model="activeTab" class="knowledge-tabs">
      <el-tab-pane :label="t('knowledge.overview')" name="overview">
        <div class="status-grid" v-loading="loading">
          <el-card v-if="status" class="status-card" shadow="never">
            <template #header>
              <div class="card-heading">
                <strong>{{ status.name }}</strong>
                <el-tag :type="status.status === 'ready' ? 'success' : 'danger'">
                  {{ status.status === 'ready' ? t('knowledge.ready') : t('knowledge.degraded') }}
                </el-tag>
              </div>
            </template>
            <div class="overview-top">
              <section class="source-donut-card" :aria-label="t('knowledge.sourceDistribution')">
                <div class="source-donut" :style="{ background: sourceChartBackground }">
                  <div class="source-donut__label">
                    <span>{{ status.entries ?? 0 }}</span>
                    <small>{{ t('knowledge.entries') }}</small>
                  </div>
                </div>
                <div class="source-donut-card__body">
                  <h3>{{ t('knowledge.sourceDistribution') }}</h3>
                  <div class="source-legend">
                    <span v-for="source in sourceChartLegend" :key="source.key" class="source-legend__item" :title="source.name">
                      <i :style="{ background: source.color }" />
                      <span>{{ source.name }}</span>
                      <strong>{{ source.entries }}</strong>
                    </span>
                  </div>
                </div>
              </section>
              <dl class="status-metrics">
                <div class="status-metric"><dt>{{ t('knowledge.entries') }}</dt><dd>{{ status.entries ?? 0 }}</dd></div>
                <div class="status-metric"><dt>{{ t('knowledge.disabled') }}</dt><dd>{{ status.disabled_entries ?? 0 }}</dd></div>
                <div class="status-metric"><dt>{{ t('knowledge.packs') }}</dt><dd>{{ status.packs ?? 0 }}</dd></div>
              </dl>
            </div>
            <section class="overview-section">
              <div class="overview-section__heading">
                <h3>{{ t('knowledge.packageStatus') }}</h3>
              </div>
              <div class="pack-runtime-grid" v-loading="packsLoading">
                <div v-for="item in packRuntimeCards" :key="item.key" class="pack-runtime-card" :class="`is-${item.tone}`">
                  <span>{{ item.label }}</span>
                  <strong>{{ item.value }}</strong>
                  <small>{{ item.meta }}</small>
                </div>
              </div>
              <div v-if="packs.length" class="overview-pack-list">
                <div v-for="pack in packs" :key="pack.pack_id" class="overview-pack-row">
                  <div class="overview-pack-row__identity">
                    <strong :title="pack.pack_id">{{ pack.pack_id }}</strong>
                    <span>{{ pack.entries ?? 0 }} {{ t('knowledge.entries') }}</span>
                  </div>
                  <div class="overview-pack-row__states">
                    <div class="overview-state-cell is-info">
                      <span>{{ t('knowledge.materialType') }}</span>
                      <strong>{{ pack.effective_material_type || 'knowledge' }}</strong>
                    </div>
                    <div class="overview-state-cell" :class="pack.auto_context === true ? 'is-enabled' : 'is-disabled'">
                      <span>{{ t('knowledge.autoContext') }}</span>
                      <strong>{{ pack.auto_context === true ? t('knowledge.enabled') : t('knowledge.disabledState') }}</strong>
                    </div>
                    <div class="overview-state-cell" :class="pack.local_embedding_enabled === true ? 'is-enabled' : 'is-disabled'">
                      <span>{{ t('knowledge.allowLocalEmbedding') }}</span>
                      <strong>{{ pack.local_embedding_enabled === true ? t('knowledge.enabled') : t('knowledge.disabledState') }}</strong>
                    </div>
                    <div class="overview-state-cell" :class="packVectorStateClass(pack)">
                      <span :title="`${t('knowledge.indexStatus')} · ${packVectorStateLabel(pack)}`">
                        {{ t('knowledge.indexStatus') }} · {{ packVectorStateLabel(pack) }}
                      </span>
                      <strong>{{ formatPackVectorProgress(pack) }}</strong>
                    </div>
                  </div>
                </div>
              </div>
              <el-empty v-else :description="t('knowledge.noPacks')" :image-size="56" />
            </section>
          </el-card>
        </div>
      </el-tab-pane>

      <el-tab-pane :label="t('knowledge.catalog')" name="catalog">
        <div class="toolbar">
          <el-input v-model="query" clearable :placeholder="t('knowledge.searchPlaceholder')" @keyup.enter="loadEntries(true)" />
          <el-button type="primary" @click="loadEntries(true)">{{ t('common.search') }}</el-button>
        </div>
        <div class="table-shell">
          <el-table :data="entries" v-loading="entriesLoading" :row-key="knowledgeEntryRowKey">
            <el-table-column :label="t('knowledge.term')" min-width="180">
              <template #default="scope">
                <span class="catalog-cell catalog-cell--title" :title="scope.row.title">
                  {{ displayPrefix(scope.row.title, 18) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column :label="t('knowledge.summary')" min-width="320" show-overflow-tooltip>
              <template #default="scope">
                {{ displayEntryPreview(scope.row) }}
              </template>
            </el-table-column>
            <el-table-column :label="t('knowledge.source')" width="170">
              <template #default="scope">
                <span class="catalog-cell" :title="scope.row.source?.name">
                  {{ displayPrefix(scope.row.source?.name, 18) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column :label="t('knowledge.actions')" width="190">
              <template #default="scope">
                <el-button link type="primary" @click="openEntry(scope.row)">{{ t('knowledge.details') }}</el-button>
                <el-button link :type="scope.row.disabled ? 'success' : 'danger'" @click="toggleEntry(scope.row)">
                  {{ scope.row.disabled ? t('knowledge.restore') : t('knowledge.disable') }}
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
        <div class="pager">
          <el-button :disabled="offset === 0" @click="previousPage">{{ t('knowledge.previous') }}</el-button>
          <span>{{ entries.length ? offset + 1 : 0 }}–{{ entries.length ? offset + entries.length : 0 }}</span>
          <el-button :disabled="!hasMore" @click="nextPage">{{ t('knowledge.next') }}</el-button>
        </div>
      </el-tab-pane>

      <el-tab-pane :label="t('knowledge.packs')" name="packs">
        <div class="toolbar">
          <input ref="fileInput" type="file" accept="application/json,.json" hidden @change="importSelectedPack" />
          <el-button type="primary" @click="fileInput?.click()">{{ t('knowledge.importPack') }}</el-button>
        </div>
        <section v-if="activeImportJobs.length" class="import-job-panel" aria-live="polite">
          <div class="import-job-panel__heading">
            <div>
              <div class="import-job-panel__title">
                <strong>{{ t('knowledge.importingPacks') }}</strong>
                <el-tag type="primary" effect="plain">{{ activeImportJobs.length }}</el-tag>
              </div>
              <p>{{ t('knowledge.importingPackHint') }}</p>
            </div>
          </div>
          <div class="import-job-list">
            <div v-for="job in activeImportJobs" :key="job.job_id" class="import-job-row">
              <div class="import-job-row__heading">
                <strong>{{ job.pack_id || job.job_id }}</strong>
                <el-tag type="primary" effect="plain">{{ displayImportJobState(job.state) }}</el-tag>
              </div>
              <el-progress
                :percentage="jobProgressPercentage(job)"
                :indeterminate="!hasMeasuredJobProgress(job)"
                :show-text="false"
                :stroke-width="6"
                :duration="2"
              />
              <span>{{ formatImportJobMeta(job) }}</span>
            </div>
          </div>
        </section>
        <section v-if="degradedPackJobs.length" class="degraded-job-panel" aria-live="polite">
          <div class="degraded-job-panel__heading">
            <div>
              <div class="degraded-job-panel__title">
                <strong>{{ t('knowledge.degradedJobs') }}</strong>
                <el-tag type="warning" effect="plain">{{ degradedPackJobs.length }}</el-tag>
              </div>
              <p>{{ t('knowledge.degradedJobHint') }}</p>
            </div>
          </div>
          <div class="degraded-job-list">
            <div v-for="job in degradedPackJobs" :key="job.job_id" class="degraded-job-row">
              <div class="degraded-job-row__identity">
                <strong>{{ job.pack_id || job.job_id }}</strong>
                <span>{{ job.reason || t('knowledge.degraded') }} · {{ job.job_id }}</span>
              </div>
              <el-button
                type="danger"
                plain
                :loading="discardingJobId === job.job_id"
                :disabled="Boolean(discardingJobId)"
                @click="discardDegradedJob(job)"
              >
                {{ t('common.delete') }}
              </el-button>
            </div>
          </div>
        </section>
        <div class="table-shell">
          <el-table class="packs-table" :data="packs" v-loading="packsLoading">
            <el-table-column type="expand" width="40">
              <template #default="scope">
                <dl class="index-status-list index-status-list--expanded">
                  <div><dt>{{ t('knowledge.indexOrigin') }}</dt><dd>{{ displayIndexValue(scope.row.index_origin) }}</dd></div>
                  <div><dt>{{ t('knowledge.indexTrust') }}</dt><dd>{{ displayIndexValue(scope.row.index_trust) }}</dd></div>
                  <div><dt>{{ t('knowledge.indexValidation') }}</dt><dd>{{ displayIndexValue(scope.row.index_validation) }}</dd></div>
                  <div><dt>{{ t('knowledge.indexFallback') }}</dt><dd>{{ displayIndexValue(scope.row.index_fallback_reason) }}</dd></div>
                  <div>
                    <dt>{{ t('knowledge.localEmbeddingState') }}</dt>
                    <dd>{{ scope.row.local_embedding_enabled ? t('knowledge.enabled') : t('knowledge.disabledState') }}</dd>
                  </div>
                  <div>
                    <dt>{{ t('knowledge.indexStatus') }}</dt>
                    <dd>{{ formatPackVectorProgress(scope.row) }} · {{ packVectorStateLabel(scope.row) }}</dd>
                  </div>
                </dl>
              </template>
            </el-table-column>
            <el-table-column prop="pack_id" :label="t('knowledge.packId')" width="160" show-overflow-tooltip />
            <el-table-column :label="t('knowledge.materialType')" width="132">
              <template #default="scope">
                <el-select
                  :model-value="scope.row.effective_material_type || 'knowledge'"
                  @change="setPackMaterialType(scope.row, String($event))"
                >
                  <el-option label="knowledge" value="knowledge" />
                  <el-option label="corpus" value="corpus" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column prop="entries" :label="t('knowledge.entries')" width="72" align="center" />
            <el-table-column :label="t('knowledge.subscription')" width="150" show-overflow-tooltip>
              <template #default="scope">
                {{ scope.row.subscription ? `${scope.row.subscription.provider} · ${scope.row.subscription.version}` : t('knowledge.localImport') }}
              </template>
            </el-table-column>
            <el-table-column :label="t('knowledge.autoContext')" width="126" align="center">
              <template #default="scope">
                <el-switch
                  :model-value="scope.row.auto_context === true"
                  @change="setPackAuto(scope.row, Boolean($event))"
                />
              </template>
            </el-table-column>
            <el-table-column :label="t('knowledge.allowLocalEmbedding')" width="145" align="center">
              <template #default="scope">
                <el-tooltip :content="t('knowledge.indexPolicyHint')" placement="top">
                  <el-switch
                    :model-value="scope.row.local_embedding_enabled === true"
                    :aria-label="t('knowledge.allowLocalEmbedding')"
                    @change="setPackIndexPolicy(scope.row, Boolean($event))"
                  />
                </el-tooltip>
              </template>
            </el-table-column>
            <el-table-column :label="t('knowledge.actions')" width="82" align="center">
              <template #default="scope">
                <el-button link type="danger" @click="removePack(scope.row)">{{ t('common.delete') }}</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <el-tab-pane :label="t('knowledge.diagnostics')" name="diagnostics">
        <div class="table-shell">
          <el-table class="diagnostics-table" :data="diagnostics" v-loading="diagnosticsLoading">
            <el-table-column :label="t('knowledge.time')" width="176">
              <template #default="scope">
                <time class="diagnostic-time" :datetime="scope.row.timestamp">
                  <span class="diagnostic-time__date">{{ formatDiagnosticDate(scope.row.timestamp) }}</span>
                  <span class="diagnostic-time__clock">{{ formatDiagnosticTime(scope.row.timestamp) }}</span>
                </time>
              </template>
            </el-table-column>
            <el-table-column :label="t('knowledge.term')" min-width="280">
              <template #default="scope">
                <span class="diagnostic-term" :class="{ 'is-empty': !scope.row.entry_title }">
                  {{ scope.row.entry_title || t('common.nA') }}
                </span>
              </template>
            </el-table-column>
            <el-table-column :label="t('knowledge.matchMode')" width="180">
              <template #default="scope">
                <el-tag class="match-mode-tag" :type="diagnosticMatchTagType(scope.row.match_mode)" effect="plain">
                  {{ displayMatchMode(scope.row.match_mode) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="t('knowledge.delivered')" width="110" align="center">
              <template #default="scope">
                <el-tag class="delivered-tag" :type="scope.row.card_delivered ? 'success' : 'info'" effect="plain">
                  {{ scope.row.card_delivered ? t('knowledge.yes') : t('knowledge.no') }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-drawer v-model="drawerOpen" class="knowledge-entry-drawer" size="620px">
      <template #header>
        <div v-if="selectedEntry" class="entry-drawer-header">
          <strong :title="selectedEntry.title">{{ selectedEntry.title }}</strong>
          <div class="entry-drawer-meta">
            <el-tag effect="plain">{{ displaySourceTag(selectedEntry.source?.tag || selectedEntry.source?.name) }}</el-tag>
            <el-tag :type="selectedEntry.disabled ? 'danger' : 'success'" effect="plain">
              {{ selectedEntry.disabled ? t('knowledge.disabledState') : t('knowledge.enabled') }}
            </el-tag>
          </div>
        </div>
      </template>
      <template v-if="selectedEntry">
        <div class="entry-drawer-body">
          <section class="entry-detail-section entry-detail-section--summary">
            <h3>{{ t('knowledge.summary') }}</h3>
            <p>{{ selectedEntry.summary || displayEntryPreview(selectedEntry) }}</p>
          </section>

          <section class="entry-detail-section">
            <h3>{{ t('knowledge.terms') }}</h3>
            <div class="term-groups">
              <section v-for="group in selectedEntryTermGroups" :key="group.key" class="term-group">
                <span>{{ group.label }}</span>
                <div class="term-chips">
                  <el-tag v-for="value in group.values" :key="value" effect="plain">
                    {{ value }}
                  </el-tag>
                </div>
              </section>
            </div>
          </section>

          <section class="entry-detail-section">
            <h3>{{ t('knowledge.tags') }}</h3>
            <div class="entry-tag-list">
              <el-tag v-for="tag in selectedEntry.tags" :key="tag" effect="plain">
                {{ tag }}
              </el-tag>
            </div>
          </section>

          <section class="entry-detail-section">
            <h3>{{ t('knowledge.content') }}</h3>
            <pre class="entry-content">{{ selectedEntry.content }}</pre>
          </section>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { KnowledgeApiError, MAX_KNOWLEDGE_PACK_FILE_BYTES, knowledgeApi, removeManagedPack, type KnowledgeStatus, type KnowledgeEntrySummary, type KnowledgePackSummary, type KnowledgePackJob } from '@/api/knowledge'
import { getMarketUrl } from '@/api/market'
import { useMarketAuth } from '@/composables/useMarketAuth'
import { createLatestRequestGate } from '@/utils/latestRequest'
import {
  createOverviewRequestGate,
  type OverviewRequestTicket,
  type OverviewResource,
} from '@/utils/overviewRequestGate'
import { openExternalUrl } from '@/utils/openExternal'
import dayjs from 'dayjs'

type KnowledgeStatusView = KnowledgeStatus & { status: 'ready' | 'degraded' }

const KNOWLEDGE_OVERVIEW_CACHE_TTL_MS = 30_000
const KNOWLEDGE_OVERVIEW_CACHE_KEY = 'neko.pluginManager.knowledgeOverview'
const PACK_JOB_POLL_LIMIT_MS = 10 * 60_000
const VECTOR_PROGRESS_POLL_MS = 10_000
let cachedStatus: { value: KnowledgeStatusView; loadedAt: number } | null = null
let cachedPacks: { value: KnowledgePackSummary[]; loadedAt: number } | null = null
let overviewRefreshTimer: number | null = null
let overviewRefreshInFlight: Promise<void> | null = null
let packJobPollTimer: number | null = null
let vectorProgressPollTimer: number | null = null
let packJobPollAttempts = 0
let packJobPollInFlight = false
let deferredIdleHandle: number | null = null
let deferredLoadTimer: number | null = null
let disposed = false
const pendingImportJobs = new Map<string, number>()
const packJobs = ref<KnowledgePackJob[]>([])
const discardingJobId = ref('')

const { t } = useI18n()
const {
  marketAuth,
  marketAuthDisplayName,
  loadMarketAuthStatus,
} = useMarketAuth()
const activeTab = ref('overview')
const loading = ref(false)
const status = ref<KnowledgeStatusView | null>(null)
const query = ref('')
const entries = ref<KnowledgeEntrySummary[]>([])
const entriesLoading = ref(false)
const offset = ref(0)
const pageSize = 50
const hasMore = ref(false)
const entriesRequestGate = createLatestRequestGate()
const entryRequestGate = createLatestRequestGate()
const packJobsRequestGate = createLatestRequestGate()
const overviewRequestGate = createOverviewRequestGate()
const drawerOpen = ref(false)
const selectedEntry = ref<KnowledgeEntrySummary | null>(null)
const packs = ref<KnowledgePackSummary[]>([])
const packsLoading = ref(false)
const diagnostics = ref<any[]>([])
const diagnosticsLoading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const marketOpening = ref(false)
const sourceChartColors = [
  'var(--el-color-primary)',
  'var(--el-color-success)',
  'var(--el-color-warning)',
  'var(--el-color-danger)',
  'var(--el-color-info)',
]

const sourceChartLegend = computed(() => {
  const sources = (status.value?.sources || [])
    .map((source) => ({
      key: String(source.tag ?? ''),
      name: displaySourceTag(source.tag),
      entries: Number(source.entries) || 0,
    }))
    .filter((source) => source.entries > 0)
    .sort((a, b) => b.entries - a.entries)
  const visible = sources.slice(0, 4)
  const rest = sources.slice(4).reduce((sum, source) => sum + source.entries, 0)
  const legend = rest > 0
    ? [...visible, { key: '__other__', name: t('knowledge.otherSources'), entries: rest }]
    : visible
  return legend.map((source, index) => ({
    ...source,
    color: sourceChartColors[index % sourceChartColors.length],
  }))
})

const sourceChartBackground = computed(() => {
  const total = sourceChartLegend.value.reduce((sum, source) => sum + source.entries, 0)
  if (total <= 0) return 'var(--knowledge-line)'
  let cursor = 0
  const stops = sourceChartLegend.value.map((source) => {
    const start = cursor
    cursor += (source.entries / total) * 100
    return `${source.color} ${start}% ${cursor}%`
  })
  return `conic-gradient(${stops.join(', ')})`
})

const degradedPackJobs = computed(() => (
  packJobs.value.filter((job) => job.state === 'degraded')
))

const activeImportJobs = computed(() => (
  packJobs.value.filter((job) => !['active', 'cancelled', 'failed', 'degraded'].includes(String(job.state || '')))
))

const packRuntimeCards = computed(() => {
  const total = packs.value.length
  const autoEnabled = packs.value.filter((pack) => pack.auto_context === true).length
  const acceptedIndex = packs.value.filter((pack) => String(pack.index_validation || '') === 'accepted').length
  const corpusPacks = packs.value.filter((pack) => pack.effective_material_type === 'corpus').length
  const knowledgePacks = total - corpusPacks
  const packVectorSnapshot = packs.value.reduce(
    (summary, pack) => {
      const counts = packVectorCounts(pack)
      summary.ready += counts.ready
      summary.total += counts.total
      return summary
    },
    { ready: 0, total: 0 },
  )
  const vectorTotal = packVectorSnapshot.total > 0
    ? packVectorSnapshot.total
    : Math.max(0, Number(status.value?.chunks_total) || 0)
  const vectorReady = packVectorSnapshot.total > 0
    ? packVectorSnapshot.ready
    : Math.min(vectorTotal, Math.max(0, Number(status.value?.chunks_ready) || 0))
  const vectorPercent = vectorTotal > 0 ? Math.round(vectorReady * 1000 / vectorTotal) / 10 : 0
  const vectorBuilding = packs.value.some((pack) => {
    const counts = packVectorCounts(pack)
    return pack.local_embedding_enabled === true && counts.total > counts.ready
  })
  return [
    {
      key: 'auto-context',
      label: t('knowledge.autoContext'),
      value: autoEnabled,
      meta: `${t('knowledge.enabled')} ${autoEnabled} / ${t('knowledge.disabledState')} ${total - autoEnabled}`,
      tone: autoEnabled > 0 ? 'success' : 'muted',
    },
    {
      key: 'local-vector',
      label: t('knowledge.indexStatus'),
      value: `${vectorReady} / ${vectorTotal}`,
      meta: vectorTotal > 0
        ? `${vectorReady === vectorTotal ? t('knowledge.vectorComplete') : t('knowledge.vectorBuilding')} · ${t('knowledge.vectorReadyPercent', { percent: vectorPercent })}`
        : t('knowledge.noVectorChunks'),
      tone: vectorTotal > 0 && vectorReady === vectorTotal
        ? 'success'
        : (vectorBuilding ? 'info' : (vectorReady > 0 ? 'warning' : 'muted')),
    },
    {
      key: 'index-health',
      label: t('knowledge.indexValidation'),
      value: acceptedIndex,
      meta: `accepted ${acceptedIndex} / ${t('knowledge.needsAttention')} ${Math.max(0, total - acceptedIndex)}`,
      tone: total === acceptedIndex ? 'success' : 'warning',
    },
    {
      key: 'material-mix',
      label: t('knowledge.materialMix'),
      value: total,
      meta: `knowledge ${knowledgePacks} / corpus ${corpusPacks}`,
      tone: 'info',
    },
  ] as const
})

function packVectorCounts(pack: KnowledgePackSummary): { ready: number; total: number } {
  const ready = Math.max(0, Number(pack.prebuilt_chunks_ready) || 0)
  const missing = Math.max(0, Number(pack.prebuilt_chunks_missing) || 0)
  return { ready, total: ready + missing }
}

function formatPackVectorProgress(pack: KnowledgePackSummary): string {
  const { ready, total } = packVectorCounts(pack)
  if (total <= 0) return t('knowledge.noVectorChunks')
  return `${ready} / ${total}`
}

function packVectorStateClass(pack: KnowledgePackSummary): string {
  const { ready, total } = packVectorCounts(pack)
  if (total > 0 && ready === total) return 'is-enabled'
  if (total > ready && pack.local_embedding_enabled === true) return 'is-progress'
  if (ready > 0) return 'is-warning'
  return 'is-disabled'
}

function packVectorStateLabel(pack: KnowledgePackSummary): string {
  const { ready, total } = packVectorCounts(pack)
  if (total > 0 && ready === total) return t('knowledge.vectorComplete')
  if (total > 0 && ready === 0) return t('knowledge.vectorWaiting')
  if (total > ready && pack.local_embedding_enabled === true) return t('knowledge.vectorBuilding')
  return t('knowledge.vectorWaiting')
}

function displayImportJobState(state: string): string {
  const keyByState: Record<string, string> = {
    queued: 'knowledge.importStateQueued',
    validating: 'knowledge.importStateValidating',
    building_fts: 'knowledge.importStateBuildingFts',
    verifying_index: 'knowledge.importStateVerifyingIndex',
    embedding: 'knowledge.importStateEmbedding',
  }
  const key = keyByState[String(state || '')]
  return key ? t(key) : String(state || t('knowledge.importStateQueued'))
}

function hasMeasuredJobProgress(job: KnowledgePackJob): boolean {
  return Number(job.indexed_percent) > 0
}

function jobProgressPercentage(job: KnowledgePackJob): number {
  if (!hasMeasuredJobProgress(job)) return 35
  return Math.min(100, Math.max(0, Number(job.indexed_percent) || 0))
}

function formatImportJobMeta(job: KnowledgePackJob): string {
  const entries = Math.max(0, Number(job.entries_total) || 0)
  const chunks = Math.max(0, Number(job.chunks_total) || 0)
  if (hasMeasuredJobProgress(job)) {
    return t('knowledge.importProgressMeta', {
      percent: jobProgressPercentage(job).toFixed(1),
      entries,
      chunks,
    })
  }
  return t('knowledge.importPreparingMeta', { entries, chunks })
}

function scheduleVectorProgressRefresh() {
  if (vectorProgressPollTimer !== null) {
    window.clearTimeout(vectorProgressPollTimer)
    vectorProgressPollTimer = null
  }
  if (
    disposed
    || !['overview', 'packs'].includes(activeTab.value)
    || !packs.value.some((pack) => {
      const { ready, total } = packVectorCounts(pack)
      return pack.local_embedding_enabled === true && total > ready
    })
  ) return
  vectorProgressPollTimer = window.setTimeout(async () => {
    vectorProgressPollTimer = null
    await loadPacks({ force: true, silent: true })
    scheduleVectorProgressRefresh()
  }, VECTOR_PROGRESS_POLL_MS)
}

const selectedEntryTermGroups = computed(() => {
  const entry = selectedEntry.value
  if (!entry) return []
  const terms = entry.terms || {}
  return [
    {
      key: 'title',
      label: t('knowledge.titleMatch'),
      values: [entry.title],
    },
    {
      key: 'alias',
      label: t('knowledge.aliasTerms'),
      values: uniqueTerms(terms.alias || terms.aliases),
    },
    {
      key: 'recognition',
      label: t('knowledge.recognitionPhrases'),
      values: uniqueTerms(terms.recognition),
    },
  ].filter((group) => group.values.length > 0)
})

function knowledgeEntryRowKey(row: KnowledgeEntrySummary): string {
  return JSON.stringify([
    row.source?.tag || '',
    row.title,
  ])
}

async function openKnowledgeMarket() {
  if (!marketAuth.value.authenticated) {
    ElMessage.warning(t('knowledge.loginRequired'))
    return
  }
  marketOpening.value = true
  try {
    const base = await getMarketUrl()
    if (!base) throw new Error(t('knowledge.marketUnavailable'))
    const response = await fetch('/market/pair-code', { method: 'POST' })
    if (!response.ok) throw new Error(t('knowledge.marketPairFailed'))
    const pairing = await response.json()
    const code = String(pairing.one_time_code || '')
    const port = Number(pairing.port)
    if (!code || !Number.isInteger(port)) throw new Error(t('knowledge.marketPairFailed'))
    const query = new URLSearchParams({
      neko_pair: code,
      neko_port: String(port),
    })
    openExternalUrl(`${base.replace(/\/+$/, '')}/#/knowledge?${query}`)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('knowledge.marketPairFailed'))
  } finally {
    marketOpening.value = false
  }
}

async function refreshAll() {
  const epoch = invalidateOverviewRequests()
  const statusTicket = overviewRequestGate.begin('status', epoch)!
  const packsTicket = overviewRequestGate.begin('packs', epoch)!
  loading.value = true
  packsLoading.value = true
  const packJobsRequestId = packJobsRequestGate.begin()
  try {
    const [statusResult, packsResult, jobsResult] = await Promise.allSettled([
      knowledgeApi.status(),
      knowledgeApi.packs(),
      knowledgeApi.packJobs(),
    ])
    if (statusResult.status === 'fulfilled' && isCurrentOverviewRequest('status', statusTicket)) {
      status.value = statusResult.value.status || null
      if (status.value) cachedStatus = { value: status.value, loadedAt: Date.now() }
      writeOverviewCache()
    }
    if (packsResult.status === 'fulfilled' && isCurrentOverviewRequest('packs', packsTicket)) {
      packs.value = packsResult.value.packs || []
      cachedPacks = { value: packs.value, loadedAt: Date.now() }
      writeOverviewCache()
    }
    if (
      jobsResult.status === 'fulfilled'
      && !disposed
      && packJobsRequestGate.isLatest(packJobsRequestId)
    ) {
      packJobs.value = jobsResult.value.jobs || []
      for (const job of activeImportJobs.value) watchImportJob(job.job_id)
    }
    const currentJobsFailed = jobsResult.status === 'rejected'
      && !disposed
      && packJobsRequestGate.isLatest(packJobsRequestId)
    const currentStatusFailed = statusResult.status === 'rejected'
      && isCurrentOverviewRequest('status', statusTicket)
    const currentPacksFailed = packsResult.status === 'rejected'
      && isCurrentOverviewRequest('packs', packsTicket)
    if (currentStatusFailed || currentPacksFailed || currentJobsFailed) {
      ElMessage.error(t('knowledge.loadFailed'))
    }
  } catch {
    if (
      isCurrentOverviewRequest('status', statusTicket)
      || isCurrentOverviewRequest('packs', packsTicket)
    ) ElMessage.error(t('knowledge.loadFailed'))
  } finally {
    if (isCurrentOverviewRequest('status', statusTicket)) loading.value = false
    if (isCurrentOverviewRequest('packs', packsTicket)) packsLoading.value = false
  }
}

function invalidateOverviewRequests(): number {
  const epoch = overviewRequestGate.invalidate()
  loading.value = false
  packsLoading.value = false
  return epoch
}

function isCurrentOverviewRequest(
  resource: OverviewResource,
  ticket: OverviewRequestTicket,
): boolean {
  return !disposed && overviewRequestGate.isCurrent(resource, ticket)
}

function isFresh(loadedAt: number): boolean {
  return Date.now() - loadedAt < KNOWLEDGE_OVERVIEW_CACHE_TTL_MS
}

function readOverviewCache() {
  if (cachedStatus || cachedPacks) return
  try {
    const raw = window.sessionStorage.getItem(KNOWLEDGE_OVERVIEW_CACHE_KEY)
    if (!raw) return
    const parsed = JSON.parse(raw)
    if (parsed?.status?.value && Number.isFinite(parsed.status.loadedAt)) {
      cachedStatus = parsed.status
    }
    if (Array.isArray(parsed?.packs?.value) && Number.isFinite(parsed.packs.loadedAt)) {
      cachedPacks = parsed.packs
    }
  } catch {
    cachedStatus = null
    cachedPacks = null
  }
}

function writeOverviewCache() {
  try {
    window.sessionStorage.setItem(
      KNOWLEDGE_OVERVIEW_CACHE_KEY,
      JSON.stringify({ status: cachedStatus, packs: cachedPacks }),
    )
  } catch {}
}

function restoreOverviewCache(): { statusFresh: boolean; packsFresh: boolean } {
  readOverviewCache()
  const statusFresh = cachedStatus ? isFresh(cachedStatus.loadedAt) : false
  const packsFresh = cachedPacks ? isFresh(cachedPacks.loadedAt) : false
  if (cachedStatus) status.value = cachedStatus.value
  if (cachedPacks) packs.value = cachedPacks.value
  return { statusFresh, packsFresh }
}

function markOverviewCacheStale() {
  if (cachedStatus) cachedStatus = { ...cachedStatus, loadedAt: 0 }
  if (cachedPacks) cachedPacks = { ...cachedPacks, loadedAt: 0 }
  writeOverviewCache()
}

function refreshOverviewInBackground(delayMs = 0) {
  if (disposed) return
  const epoch = invalidateOverviewRequests()
  markOverviewCacheStale()
  if (overviewRefreshTimer) window.clearTimeout(overviewRefreshTimer)
  overviewRefreshTimer = window.setTimeout(() => {
    overviewRefreshTimer = null
    if (disposed) return
    const run = () => Promise.allSettled([
      loadStatus({ force: true, silent: true, epoch }),
      loadPacks({ force: true, silent: true, epoch }),
    ]).then(() => {})
    const queuedRefresh = (overviewRefreshInFlight || Promise.resolve()).then(run)
    overviewRefreshInFlight = queuedRefresh
    void queuedRefresh.finally(() => {
      if (overviewRefreshInFlight === queuedRefresh) {
        overviewRefreshInFlight = null
      }
    })
  }, delayMs)
}

async function loadStatus(options: { force?: boolean; silent?: boolean; epoch?: number } = {}) {
  if (!options.force && cachedStatus && isFresh(cachedStatus.loadedAt)) {
    status.value = cachedStatus.value
    return
  }
  const ticket = overviewRequestGate.begin('status', options.epoch)
  if (!ticket || disposed) return
  if (!options.silent) loading.value = true
  try {
    const nextStatus = (await knowledgeApi.status()).status || null
    if (!isCurrentOverviewRequest('status', ticket)) return
    status.value = nextStatus
    if (status.value) cachedStatus = { value: status.value, loadedAt: Date.now() }
    writeOverviewCache()
  } catch {
    if (isCurrentOverviewRequest('status', ticket)) ElMessage.error(t('knowledge.loadFailed'))
  } finally {
    if (!options.silent && isCurrentOverviewRequest('status', ticket)) loading.value = false
  }
}

async function loadEntries(reset = false) {
  if (reset) offset.value = 0
  const requestId = entriesRequestGate.begin()
  const requestedQuery = query.value
  const requestedOffset = offset.value
  entriesLoading.value = true
  try {
    const response = await knowledgeApi.entries({
      query: requestedQuery,
      limit: pageSize,
      offset: requestedOffset,
    })
    if (!entriesRequestGate.isLatest(requestId)) return
    entries.value = response.items || []
    hasMore.value = Boolean(response.has_more)
  } catch {
    if (entriesRequestGate.isLatest(requestId)) ElMessage.error(t('knowledge.loadFailed'))
  } finally {
    if (entriesRequestGate.isLatest(requestId)) entriesLoading.value = false
  }
}

async function openEntry(row: KnowledgeEntrySummary) {
  const requestId = entryRequestGate.begin()
  try {
    const response = await knowledgeApi.entry({ source: row.source.tag, title: row.title })
    if (disposed || !entryRequestGate.isLatest(requestId)) return
    selectedEntry.value = response.entry || null
    drawerOpen.value = Boolean(selectedEntry.value)
  } catch {
    if (!disposed && entryRequestGate.isLatest(requestId)) {
      ElMessage.error(t('knowledge.loadFailed'))
    }
  }
}

async function toggleEntry(row: KnowledgeEntrySummary) {
  const requestedDisabled = !row.disabled
  try {
    await knowledgeApi.setEntryDisabled({
      source: row.source.tag,
      title: row.title,
      disabled: requestedDisabled,
    })
    row.disabled = requestedDisabled
    refreshOverviewInBackground()
  } catch { ElMessage.error(t('knowledge.operationFailed')) }
}

function previousPage() { offset.value = Math.max(0, offset.value - pageSize); loadEntries() }
function nextPage() { offset.value += pageSize; loadEntries() }

async function loadPacks(options: { force?: boolean; silent?: boolean; epoch?: number } = {}) {
  if (!options.force && cachedPacks && isFresh(cachedPacks.loadedAt)) {
    packs.value = cachedPacks.value
    return
  }
  if (packsLoading.value && !options.force) return
  const ticket = overviewRequestGate.begin('packs', options.epoch)
  if (!ticket || disposed) return
  if (!options.silent) packsLoading.value = true
  try {
    const nextPacks = (await knowledgeApi.packs()).packs || []
    if (!isCurrentOverviewRequest('packs', ticket)) return
    packs.value = nextPacks
    cachedPacks = { value: packs.value, loadedAt: Date.now() }
    writeOverviewCache()
  }
  catch { if (isCurrentOverviewRequest('packs', ticket)) ElMessage.error(t('knowledge.loadFailed')) }
  finally {
    if (!options.silent && isCurrentOverviewRequest('packs', ticket)) packsLoading.value = false
  }
}

async function loadPackJobs(options: { silent?: boolean } = {}) {
  const requestId = packJobsRequestGate.begin()
  try {
    const response = await knowledgeApi.packJobs()
    if (!disposed && packJobsRequestGate.isLatest(requestId)) {
      packJobs.value = response.jobs || []
      for (const job of activeImportJobs.value) watchImportJob(job.job_id)
    }
  } catch {
    if (!disposed && packJobsRequestGate.isLatest(requestId) && !options.silent) {
      ElMessage.error(t('knowledge.loadFailed'))
    }
  }
}

async function discardDegradedJob(job: KnowledgePackJob) {
  const jobId = String(job.job_id || '').trim()
  if (!jobId || discardingJobId.value) return
  try {
    await ElMessageBox.confirm(
      t('knowledge.discardJobConfirm', { name: job.pack_id || jobId }),
      t('common.warning'),
      { type: 'warning' },
    )
    discardingJobId.value = jobId
    await knowledgeApi.discardPackJob({ job_id: jobId })
    packJobsRequestGate.invalidate()
    packJobs.value = packJobs.value.filter((item) => item.job_id !== jobId)
    ElMessage.success(t('knowledge.jobDiscarded'))
    refreshOverviewInBackground()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(t('knowledge.operationFailed'))
    }
  } finally {
    if (discardingJobId.value === jobId) discardingJobId.value = ''
  }
}

async function importSelectedPack(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (file.size > MAX_KNOWLEDGE_PACK_FILE_BYTES) {
    ElMessage.error(t('knowledge.importTooLarge'))
    return
  }
  let pack: unknown
  try {
    pack = JSON.parse(await file.text())
  } catch {
    ElMessage.error(t('knowledge.invalidPack'))
    return
  }
  try {
    const response = await knowledgeApi.importPack(pack)
    ElMessage.info(
      response.state === 'queued'
        ? t('knowledge.importQueued')
        : t('knowledge.importSuccess'),
    )
    const jobId = String(response.job_id || '').trim()
    if (response.state === 'queued' && jobId) {
      watchImportJob(jobId)
    } else {
      refreshOverviewInBackground(response.state === 'queued' ? 1500 : 0)
    }
  } catch (error) {
    ElMessage.error(t(
      error instanceof KnowledgeApiError && error.reason === 'invalid_pack'
        ? 'knowledge.invalidPack'
        : 'knowledge.operationFailed',
    ))
  }
}

function watchImportJob(jobId: string) {
  if (!pendingImportJobs.has(jobId)) pendingImportJobs.set(jobId, Date.now())
  if (packJobPollTimer === null && !packJobPollInFlight) {
    packJobPollTimer = window.setTimeout(pollImportJobs, 1000)
  }
}

async function pollImportJobs() {
  packJobPollTimer = null
  if (disposed || pendingImportJobs.size === 0 || packJobPollInFlight) return
  packJobPollInFlight = true
  const requestId = packJobsRequestGate.begin()
  let completed = false
  try {
    const response = await knowledgeApi.packJobs()
    if (!disposed && packJobsRequestGate.isLatest(requestId)) {
      packJobs.value = response.jobs || []
      const jobs = new Map(
        (response.jobs || []).map((job) => [String(job.job_id || ''), job]),
      )
      for (const [jobId] of [...pendingImportJobs]) {
        const job = jobs.get(jobId)
        const state = String(job?.state || '')
        if (state === 'active') {
          pendingImportJobs.delete(jobId)
          completed = true
          ElMessage.success(t('knowledge.importSuccess'))
        } else if (state === 'failed' || state === 'cancelled' || state === 'degraded') {
          pendingImportJobs.delete(jobId)
          completed = true
          ElMessage.error(t('knowledge.operationFailed'))
        }
      }
      packJobPollAttempts = 0
    }
  } catch {
    packJobPollAttempts += 1
  } finally {
    packJobPollInFlight = false
  }
  if (disposed) return
  const now = Date.now()
  for (const [jobId, startedAt] of [...pendingImportJobs]) {
    if (now - startedAt >= PACK_JOB_POLL_LIMIT_MS) {
      pendingImportJobs.delete(jobId)
      ElMessage.warning(t('knowledge.importStillProcessing'))
    }
  }
  if (completed) refreshOverviewInBackground()
  if (pendingImportJobs.size > 0 && !disposed) {
    const delay = Math.min(1000 * 2 ** packJobPollAttempts, 5000)
    packJobPollTimer = window.setTimeout(pollImportJobs, delay)
  }
}

async function setPackAuto(row: KnowledgePackSummary, enabled: boolean) {
  try {
    await knowledgeApi.setPackAutoContext({ pack_id: row.pack_id, enabled })
    row.auto_context = enabled
    refreshOverviewInBackground()
  } catch { ElMessage.error(t('knowledge.operationFailed')) }
}

async function setPackMaterialType(row: KnowledgePackSummary, materialType: string) {
  try {
    await knowledgeApi.setPackMaterialType({ pack_id: row.pack_id, material_type: materialType })
    row.effective_material_type = materialType as 'knowledge' | 'corpus'
    if (materialType === 'corpus') row.auto_context = true
    refreshOverviewInBackground()
  } catch { ElMessage.error(t('knowledge.operationFailed')) }
}

function displayIndexValue(value: unknown): string {
  const text = String(value ?? '').trim()
  return text || t('common.nA')
}

function displaySourceTag(value: unknown): string {
  return String(value ?? '').replace(/^source:/, '') || t('common.nA')
}

function displayPrefix(value: unknown, maxLength: number): string {
  const text = String(value ?? '').trim()
  if (!text) return t('common.nA')
  const chars = Array.from(text)
  return chars.length > maxLength ? `${chars.slice(0, maxLength).join('')}...` : text
}

function displayEntryPreview(row: KnowledgeEntrySummary): string {
  return String(row.content_preview || row.summary || '').trim() || t('common.nA')
}

function uniqueTerms(values: unknown): string[] {
  if (!Array.isArray(values)) return []
  const seen = new Set<string>()
  const result: string[] = []
  for (const value of values) {
    const text = String(value ?? '').trim()
    if (!text || seen.has(text)) continue
    seen.add(text)
    result.push(text)
  }
  return result
}

function formatDiagnosticDate(value: unknown): string {
  const parsed = dayjs(String(value ?? ''))
  return parsed.isValid() ? parsed.format('YYYY-MM-DD') : t('common.nA')
}

function formatDiagnosticTime(value: unknown): string {
  const parsed = dayjs(String(value ?? ''))
  return parsed.isValid() ? parsed.format('HH:mm:ss') : ''
}

function displayMatchMode(value: unknown): string {
  const text = String(value ?? '').trim()
  if (!text) return t('common.nA')
  return text.replace(/^automatic_/, '').replace(/_/g, ' ')
}

function diagnosticMatchTagType(value: unknown): 'success' | 'info' | 'warning' {
  const text = String(value ?? '')
  if (text.includes('hybrid')) return 'success'
  if (text.includes('miss')) return 'info'
  return 'warning'
}

async function setPackIndexPolicy(row: KnowledgePackSummary, enabled: boolean) {
  try {
    await knowledgeApi.setPackIndexPolicy({
      pack_id: row.pack_id,
      local_embedding_enabled: enabled,
    })
    row.local_embedding_enabled = enabled
    refreshOverviewInBackground()
  } catch { ElMessage.error(t('knowledge.operationFailed')) }
}

async function removePack(row: KnowledgePackSummary) {
  try {
    await ElMessageBox.confirm(t('knowledge.removeConfirm', { name: row.pack_id }), t('common.warning'), { type: 'warning' })
    await removeManagedPack(row)
    packs.value = packs.value.filter((pack) => pack.pack_id !== row.pack_id)
    refreshOverviewInBackground()
  } catch (error: any) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(t('knowledge.operationFailed'))
  }
}

async function loadDiagnostics() {
  diagnosticsLoading.value = true
  try {
    const items = (await knowledgeApi.diagnostics()).items || []
    diagnostics.value = items.filter(hasDiagnosticEntry)
  }
  catch { ElMessage.error(t('knowledge.loadFailed')) }
  finally { diagnosticsLoading.value = false }
}

function hasDiagnosticEntry(item: any): boolean {
  const title = String(item?.entry_title ?? '').trim()
  return Boolean(title) && title.toLowerCase() !== 'null'
}

watch(activeTab, (tab) => {
  if (tab === 'catalog') loadEntries(true)
  if (tab === 'packs') {
    loadPacks()
    void loadPackJobs()
  }
  if (tab === 'diagnostics') loadDiagnostics()
})

watch([activeTab, packs], scheduleVectorProgressRefresh)

function deferInitialSecondaryLoads(silent = false) {
  const run = () => {
    deferredIdleHandle = null
    deferredLoadTimer = null
    if (disposed) return
    void loadPacks({ silent })
    void loadPackJobs({ silent })
    void loadMarketAuthStatus()
  }
  const requestIdleCallback = window.requestIdleCallback
  if (typeof requestIdleCallback === 'function') {
    deferredIdleHandle = requestIdleCallback(run, { timeout: 1200 })
    return
  }
  deferredLoadTimer = window.setTimeout(run, 120)
}

onMounted(() => {
  const cache = restoreOverviewCache()
  if (!cache.statusFresh) {
    void loadStatus({ silent: Boolean(status.value) })
  }
  if (!cache.packsFresh) {
    deferInitialSecondaryLoads(Boolean(cachedPacks))
  } else {
    void loadPackJobs({ silent: true })
    void loadMarketAuthStatus()
  }
})

onUnmounted(() => {
  disposed = true
  overviewRequestGate.invalidate()
  entryRequestGate.begin()
  packJobsRequestGate.invalidate()
  pendingImportJobs.clear()
  if (overviewRefreshTimer !== null) window.clearTimeout(overviewRefreshTimer)
  if (packJobPollTimer !== null) window.clearTimeout(packJobPollTimer)
  if (vectorProgressPollTimer !== null) window.clearTimeout(vectorProgressPollTimer)
  if (deferredLoadTimer !== null) window.clearTimeout(deferredLoadTimer)
  if (deferredIdleHandle !== null && typeof window.cancelIdleCallback === 'function') {
    window.cancelIdleCallback(deferredIdleHandle)
  }
  overviewRefreshTimer = null
  packJobPollTimer = null
  vectorProgressPollTimer = null
  deferredLoadTimer = null
  deferredIdleHandle = null
})
</script>

<style scoped>
.knowledge-manager {
  --knowledge-surface: var(--el-bg-color);
  --knowledge-surface-muted: var(--el-fill-color-extra-light);
  --knowledge-line: var(--el-border-color-lighter);
  --knowledge-accent-soft: var(--el-color-primary-light-9);
  position: relative;
  padding: 24px 24px 72px;
  display: flex;
  flex-direction: column;
  gap: 18px;
  width: 100%;
  min-width: 0;
  overflow-x: clip;
}

.market-entry {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--knowledge-line);
  border-radius: 10px;
  background: var(--knowledge-surface);
  min-width: 0;
  box-shadow: 0 8px 22px rgba(15, 23, 42, 0.03);
}

.market-entry .el-alert {
  flex: 1 1 320px;
  min-width: 0;
  padding: 0;
  border: 0;
  background: transparent;
}

.market-entry .el-button {
  margin-left: auto;
}

.market-login-hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.page-heading,
.card-heading,
.switch-row,
.toolbar,
.pager {
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
  border-radius: 10px;
  background: var(--knowledge-surface);
  box-shadow: 0 10px 28px rgba(15, 23, 42, 0.04);
}

.knowledge-tabs :deep(.el-tabs__header) {
  margin: 0;
  padding: 8px;
  border-bottom: 1px solid var(--knowledge-line);
  background: var(--knowledge-surface-muted);
  border-radius: 10px 10px 0 0;
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

.knowledge-tabs :deep(.el-tabs__nav-scroll::-webkit-scrollbar) {
  display: none;
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
  width: 104px;
  height: 36px;
  padding: 0 12px;
  border-radius: 7px;
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
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05);
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

.knowledge-tabs :deep(.el-tabs__item.is-focus) {
  box-shadow: inset 0 0 0 1px var(--el-color-primary-light-5);
}

.knowledge-tabs :deep(.el-tabs__item.is-active.is-focus) {
  box-shadow:
    0 1px 2px rgba(15, 23, 42, 0.05),
    0 0 0 3px rgba(64, 158, 255, 0.12);
}

.knowledge-tabs :deep(.el-tabs__content) {
  padding: 18px;
  min-width: 0;
}

.status-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(290px, 1fr));
  gap: 16px;
}

.status-card {
  border-color: transparent;
  border-radius: 10px;
  box-shadow: none;
}

.status-card :deep(.el-card__header) {
  padding: 16px 18px;
  border-bottom-color: var(--knowledge-line);
}

.status-card :deep(.el-card__body) {
  padding: 18px;
}

.status-metrics {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.status-metric {
  position: relative;
  padding: 16px;
  border: 0;
  border-radius: 8px;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.68), rgba(255, 255, 255, 0)),
    var(--knowledge-surface-muted);
  overflow: hidden;
}

.status-metric::after {
  position: absolute;
  top: 14px;
  right: 14px;
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--el-color-primary-light-5);
  opacity: 0.58;
  content: '';
}

dt {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

dd {
  margin: 4px 0 0;
  color: var(--el-text-color-primary);
  font-size: 24px;
  font-weight: 700;
  line-height: 1.15;
  font-variant-numeric: tabular-nums;
}

.switch-row {
  margin-top: 14px;
}

.overview-top {
  display: grid;
  grid-template-columns: minmax(260px, 0.85fr) minmax(0, 1.15fr);
  gap: 12px;
  align-items: stretch;
}

.source-donut-card {
  display: grid;
  grid-template-columns: 86px minmax(0, 1fr);
  gap: 14px;
  align-items: center;
  min-width: 0;
  padding: 14px;
  border: 1px solid var(--knowledge-line);
  border-radius: 8px;
  background: var(--knowledge-surface-muted);
}

.source-donut {
  position: relative;
  display: grid;
  place-items: center;
  width: 76px;
  height: 76px;
  border-radius: 50%;
}

.source-donut::before {
  position: absolute;
  inset: 12px;
  width: 52px;
  height: 52px;
  border-radius: 50%;
  background: var(--knowledge-surface);
  content: '';
}

.source-donut__label {
  z-index: 1;
  display: grid;
  gap: 3px;
  max-width: 48px;
  overflow: hidden;
  text-align: center;
}

.source-donut__label span,
.source-donut__label small {
  min-width: 0;
  overflow: hidden;
  color: var(--el-text-color-primary);
  line-height: 1;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.source-donut__label span {
  font-size: 16px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.source-donut__label small {
  color: var(--el-text-color-secondary);
  font-size: 10px;
}

.source-donut-card__body {
  display: grid;
  gap: 8px;
  min-width: 0;
}

.source-donut-card__body h3 {
  margin: 0;
  color: var(--el-text-color-primary);
  font-size: 13px;
  font-weight: 700;
}

.source-legend {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 5px 10px;
  min-width: 0;
}

.source-legend__item {
  display: grid;
  grid-template-columns: 8px minmax(0, 1fr) auto;
  gap: 6px;
  align-items: center;
  min-width: 0;
  color: var(--el-text-color-secondary);
  font-size: 11px;
}

.source-legend__item i {
  width: 8px;
  height: 8px;
  border-radius: 999px;
}

.source-legend__item span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.source-legend__item strong {
  color: var(--el-text-color-primary);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.overview-section {
  margin-top: 20px;
}

.overview-section__heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.overview-section__heading h3 {
  margin: 0;
  color: var(--el-text-color-primary);
  font-size: 14px;
  font-weight: 700;
}

.pack-runtime-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 10px;
}

.pack-runtime-card {
  position: relative;
  display: grid;
  gap: 6px;
  min-width: 0;
  padding: 14px;
  border: 1px solid var(--knowledge-line);
  border-radius: 8px;
  background: var(--knowledge-surface);
}

.pack-runtime-card::before {
  position: absolute;
  top: 14px;
  right: 14px;
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--el-color-info-light-5);
  content: '';
}

.pack-runtime-card.is-success::before {
  background: var(--el-color-success);
}

.pack-runtime-card.is-warning::before {
  background: var(--el-color-warning);
}

.pack-runtime-card.is-info::before {
  background: var(--el-color-primary);
}

.pack-runtime-card span,
.pack-runtime-card small {
  min-width: 0;
  overflow: hidden;
  color: var(--el-text-color-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pack-runtime-card span {
  padding-right: 16px;
  font-size: 12px;
}

.pack-runtime-card strong {
  color: var(--el-text-color-primary);
  font-size: 24px;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}

.pack-runtime-card small {
  font-size: 12px;
}

.overview-pack-list {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.overview-pack-row {
  display: grid;
  grid-template-columns: minmax(180px, 0.34fr) minmax(0, 1fr);
  gap: 12px;
  align-items: center;
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--knowledge-line);
  border-radius: 8px;
  background: var(--knowledge-surface-muted);
}

.overview-pack-row__identity {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.overview-pack-row__identity strong {
  min-width: 0;
  overflow: hidden;
  color: var(--el-text-color-primary);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.overview-pack-row__identity span {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.overview-pack-row__states {
  display: grid;
  grid-template-columns: repeat(4, minmax(108px, 1fr));
  gap: 8px;
  min-width: 0;
}

.overview-state-cell {
  position: relative;
  display: grid;
  gap: 4px;
  min-width: 0;
  padding: 9px 10px 9px 12px;
  overflow: hidden;
  border: 1px solid var(--knowledge-line);
  border-left: 3px solid var(--el-color-info);
  border-radius: 7px;
  background: #fff;
}

.overview-state-cell span,
.overview-state-cell strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.overview-state-cell span {
  color: var(--el-text-color-secondary);
  font-size: 11px;
}

.overview-state-cell strong {
  color: var(--el-text-color-primary);
  font-size: 13px;
  font-weight: 700;
}

.overview-state-cell.is-enabled {
  border-left-color: var(--el-color-success);
  background: var(--el-color-success-light-9);
}

.overview-state-cell.is-disabled {
  border-left-color: var(--el-border-color-darker);
  background: #fff;
}

.overview-state-cell.is-disabled strong {
  color: var(--el-text-color-secondary);
}

.overview-state-cell.is-warning {
  border-left-color: var(--el-color-warning);
  background: var(--el-color-warning-light-9);
}

.overview-state-cell.is-progress {
  border-left-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.overview-state-cell.is-danger {
  border-left-color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}

.overview-state-cell.is-info {
  border-left-color: var(--el-color-primary);
}

.toolbar {
  justify-content: flex-start;
  margin-bottom: 14px;
  padding: 12px;
  border: 1px solid var(--knowledge-line);
  border-radius: 8px;
  background: var(--knowledge-surface-muted);
}

.toolbar .el-select {
  width: min(210px, 100%);
}

.toolbar .el-input {
  flex: 1 1 320px;
  min-width: 0;
  max-width: 520px;
}

.degraded-job-panel {
  display: grid;
  gap: 12px;
  min-width: 0;
  margin-bottom: 14px;
  padding: 14px;
  border: 1px solid var(--el-color-warning-light-5);
  border-radius: 8px;
  background: var(--el-color-warning-light-9);
}

.import-job-panel {
  display: grid;
  gap: 12px;
  min-width: 0;
  margin-bottom: 14px;
  padding: 14px;
  border: 1px solid var(--el-color-primary-light-7);
  border-radius: 8px;
  background: var(--el-color-primary-light-9);
}

.import-job-panel__heading,
.import-job-panel__title,
.import-job-row__heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.import-job-panel__heading p {
  margin: 6px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.import-job-list {
  display: grid;
  gap: 8px;
}

.import-job-row {
  display: grid;
  gap: 8px;
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--knowledge-line);
  border-radius: 7px;
  background: var(--knowledge-surface);
}

.import-job-row__heading strong,
.import-job-row > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.import-job-row__heading strong {
  color: var(--el-text-color-primary);
  font-size: 13px;
}

.import-job-row > span {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.degraded-job-panel__heading,
.degraded-job-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
}

.degraded-job-panel__heading > div,
.degraded-job-row__identity {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.degraded-job-panel__title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.degraded-job-panel__heading p {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.degraded-job-list {
  display: grid;
  gap: 8px;
}

.degraded-job-row {
  padding: 10px 12px;
  border: 1px solid var(--knowledge-line);
  border-radius: 6px;
  background: var(--knowledge-surface);
}

.degraded-job-row__identity strong,
.degraded-job-row__identity span {
  overflow-wrap: anywhere;
}

.degraded-job-row__identity span {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.degraded-job-row .el-button {
  min-width: 72px;
  min-height: 44px;
}

.table-shell {
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--knowledge-line);
  border-radius: 10px;
  background: var(--knowledge-surface);
}

.table-shell :deep(.el-table) {
  --el-table-border-color: var(--knowledge-line);
  --el-table-header-bg-color: var(--knowledge-surface-muted);
  --el-table-row-hover-bg-color: var(--el-color-primary-light-9);
}

.table-shell :deep(.el-table__inner-wrapper::before) {
  display: none;
}

.table-shell :deep(.el-table th.el-table__cell) {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}

.table-shell :deep(.el-table td.el-table__cell) {
  color: var(--el-text-color-regular);
}

.catalog-cell {
  display: block;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.catalog-cell--title {
  color: var(--el-text-color-primary);
  font-weight: 500;
}

.packs-table :deep(.el-table__expanded-cell) {
  padding: 14px 18px 16px 60px;
  background: var(--knowledge-surface-muted);
}

.packs-table :deep(.el-table__expand-icon) {
  color: var(--el-text-color-secondary);
}

.packs-table :deep(.el-table__expand-icon--expanded) {
  color: var(--el-color-primary);
}

.diagnostics-table :deep(.el-table__row) {
  height: 58px;
}

.diagnostics-table :deep(.el-table__cell) {
  vertical-align: middle;
}

.diagnostic-time {
  display: inline-flex;
  flex-direction: column;
  gap: 2px;
  line-height: 1.2;
  white-space: nowrap;
}

.diagnostic-time__date {
  color: var(--el-text-color-regular);
  font-size: 13px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.diagnostic-time__clock {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.diagnostic-term {
  display: block;
  min-width: 0;
  overflow: hidden;
  color: var(--el-text-color-regular);
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.diagnostic-term.is-empty {
  color: var(--el-text-color-placeholder);
}

.match-mode-tag,
.delivered-tag {
  max-width: 100%;
  border-radius: 6px;
  font-weight: 500;
}

.match-mode-tag :deep(.el-tag__content) {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pager {
  justify-content: flex-end;
  margin-top: 14px;
}

.pager span {
  min-width: 70px;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
  text-align: center;
}

.index-status-list {
  display: grid;
  grid-template-columns: 1fr;
  gap: 4px;
  margin: 0;
}

.index-status-list--expanded {
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 8px;
}

.index-status-list div {
  display: grid;
  grid-template-columns: minmax(90px, auto) minmax(0, 1fr);
  gap: 8px;
  padding: 0;
  border: 0;
  background: transparent;
}

.index-status-list dt {
  font-size: 12px;
}

.index-status-list dd {
  min-width: 0;
  margin: 0;
  font-size: 12px;
  font-weight: 500;
  overflow-wrap: anywhere;
}

:global(.knowledge-entry-drawer) {
  --knowledge-surface: var(--el-bg-color);
  --knowledge-surface-muted: var(--el-fill-color-extra-light);
  --knowledge-line: var(--el-border-color-lighter);
  min-width: 0;
}

:global(.knowledge-entry-drawer .el-drawer__header) {
  margin: 0;
  padding: 30px 30px 22px;
  border-bottom: 1px solid var(--knowledge-line);
}

:global(.knowledge-entry-drawer .el-drawer__body) {
  padding: 0;
  color: var(--el-text-color-regular);
}

.entry-drawer-header {
  display: grid;
  gap: 12px;
  min-width: 0;
}

.entry-drawer-header strong {
  min-width: 0;
  max-width: 420px;
  overflow: hidden;
  color: var(--el-text-color-primary);
  font-size: 17px;
  line-height: 1.45;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.entry-drawer-meta,
.entry-tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}

.entry-drawer-meta :deep(.el-tag),
.entry-tag-list :deep(.el-tag) {
  max-width: 100%;
  border-radius: 6px;
}

.entry-drawer-body {
  display: grid;
  gap: 18px;
  padding: 28px 30px 42px;
}

.entry-detail-section {
  display: grid;
  gap: 12px;
  min-width: 0;
  padding: 16px 18px;
  border: 1px solid var(--knowledge-line);
  border-radius: 8px;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.68), rgba(255, 255, 255, 0)),
    var(--knowledge-surface-muted);
}

.entry-detail-section h3 {
  margin: 0;
  color: var(--el-text-color-primary);
  font-size: 14px;
  font-weight: 700;
  line-height: 1.35;
}

.entry-detail-section p {
  margin: 0;
  color: var(--el-text-color-regular);
  font-size: 14px;
  line-height: 1.75;
  overflow-wrap: anywhere;
}

.term-groups {
  display: grid;
  gap: 12px;
}

.term-group {
  display: grid;
  gap: 8px;
  min-width: 0;
}

.term-group > span {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-weight: 600;
}

.term-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}

.term-chips :deep(.el-tag) {
  max-width: 100%;
  border-radius: 6px;
}

.term-chips :deep(.el-tag__content) {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.entry-tag-list :deep(.el-tag__content) {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.entry-content {
  max-height: 360px;
  overflow: auto;
  color: var(--el-text-color-regular);
  font-size: 13px;
  line-height: 1.8;
}

pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: 0;
  padding: 12px 14px;
  border: 1px solid var(--el-border-color-extra-light);
  border-radius: 8px;
  background: var(--knowledge-surface);
}

@media (max-width: 1100px) {
  .pack-runtime-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .overview-pack-row {
    grid-template-columns: 1fr;
  }

  .overview-pack-row__states {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  :global(.knowledge-entry-drawer) {
    width: min(100vw, 620px) !important;
  }

  :global(.knowledge-entry-drawer .el-drawer__header) {
    padding: 26px 18px 18px;
  }

  .entry-drawer-body {
    gap: 14px;
    padding: 22px 18px 32px;
  }

  .entry-detail-section {
    padding: 14px;
  }

  .entry-drawer-header strong {
    max-width: calc(100vw - 88px);
  }

  .knowledge-manager {
    padding: 16px 16px calc(128px + env(safe-area-inset-bottom));
  }

  .market-entry .el-alert,
  .market-entry .el-button {
    flex-basis: 100%;
    width: 100%;
    margin-left: 0;
  }

  .knowledge-tabs :deep(.el-tabs__header) {
    padding: 6px;
  }

  .knowledge-tabs :deep(.el-tabs__item) {
    width: 86px;
    height: 34px;
    padding: 0 8px;
  }

  .knowledge-tabs :deep(.el-tabs__content) {
    padding: 14px;
  }

  .overview-top {
    grid-template-columns: 1fr;
  }

  .source-donut-card {
    grid-template-columns: 76px minmax(0, 1fr);
  }

  .source-legend {
    grid-template-columns: 1fr;
  }

  .status-metrics {
    grid-template-columns: 1fr;
  }

  .pack-runtime-grid {
    grid-template-columns: 1fr;
  }

  .toolbar .el-input,
  .toolbar .el-select,
  .toolbar .el-button {
    width: 100%;
    max-width: none;
  }

  .packs-table :deep(.el-table__expanded-cell) {
    padding: 12px;
  }

  .index-status-list--expanded {
    grid-template-columns: 1fr;
  }

  .pager {
    justify-content: center;
  }
}
</style>
