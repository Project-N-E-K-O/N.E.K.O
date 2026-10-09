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
        <div v-loading="loading && !status" class="overview">
          <el-card v-if="status" class="status-card" shadow="never">
            <template #header>
              <div class="card-heading">
                <strong>{{ t('knowledge.title') }}</strong>
                <div class="card-heading__tags">
                  <el-tag :type="serviceStateTagType" data-testid="knowledge-state">
                    {{ serviceStateLabel }}
                  </el-tag>
                  <el-tag :type="status.tool_available ? 'success' : 'info'" effect="plain">
                    {{ status.tool_available ? t('knowledge.toolOffered') : t('knowledge.toolNotOffered') }}
                  </el-tag>
                </div>
              </div>
            </template>

            <el-alert
              v-if="status.state === 'starting'"
              class="overview-alert"
              type="info"
              :title="t('knowledge.startingHint')"
              :closable="false"
              show-icon
            />
            <el-alert
              v-else-if="status.state === 'unavailable'"
              class="overview-alert"
              type="error"
              :title="t('knowledge.unavailableHint')"
              :description="statusErrorMessage"
              :closable="false"
              show-icon
            />

            <section class="global-switch">
              <div class="global-switch__text">
                <strong>{{ t('knowledge.globalSwitch') }}</strong>
                <p>{{ t('knowledge.globalSwitchHint') }}</p>
              </div>
              <el-switch
                :model-value="status.enabled === true"
                :loading="savingEnabled"
                :disabled="status.state !== 'ready'"
                :aria-label="t('knowledge.globalSwitch')"
                data-testid="knowledge-global-switch"
                @change="setGlobalEnabled(Boolean($event))"
              />
            </section>

            <el-alert
              v-if="brokenPackIds.length"
              class="overview-alert"
              type="warning"
              :title="t('knowledge.brokenPacksTitle')"
              :description="t('knowledge.brokenPacksHint', { packs: brokenPackIds.join(', ') })"
              :closable="false"
              show-icon
            />

            <template v-if="status.state === 'ready'">
              <dl class="status-metrics">
                <div class="status-metric">
                  <dt>{{ t('knowledge.entries') }}</dt>
                  <dd>{{ status.entries ?? 0 }}</dd>
                </div>
                <div class="status-metric">
                  <dt>{{ t('knowledge.disabledEntries') }}</dt>
                  <dd>{{ status.disabled_entries ?? 0 }}</dd>
                </div>
                <div class="status-metric">
                  <dt>{{ t('knowledge.packs') }}</dt>
                  <dd>{{ status.packs ?? 0 }}</dd>
                </div>
              </dl>

              <div class="overview-grid">
                <section class="overview-panel">
                  <h3>{{ t('knowledge.materialSplit') }}</h3>
                  <div class="split-bar" role="presentation">
                    <span class="split-bar__knowledge" :style="{ width: `${knowledgeShare}%` }" />
                    <span class="split-bar__corpus" :style="{ width: `${100 - knowledgeShare}%` }" />
                  </div>
                  <div class="split-legend">
                    <div class="split-legend__item">
                      <i class="dot dot--knowledge" />
                      <span>{{ t('knowledge.typeKnowledge') }}</span>
                      <small>{{ t('knowledge.splitMeta', { packs: status.knowledge_packs ?? 0, entries: status.knowledge_entries ?? 0 }) }}</small>
                    </div>
                    <div class="split-legend__item">
                      <i class="dot dot--corpus" />
                      <span>{{ t('knowledge.typeCorpus') }}</span>
                      <small>{{ t('knowledge.splitMeta', { packs: status.corpus_packs ?? 0, entries: status.corpus_entries ?? 0 }) }}</small>
                    </div>
                  </div>
                </section>

                <section class="overview-panel">
                  <div class="overview-panel__heading">
                    <h3>{{ t('knowledge.vectorIndex') }}</h3>
                    <el-tag :type="embeddingTagType" effect="plain" size="small">
                      {{ embeddingStateLabel }}
                    </el-tag>
                  </div>
                  <template v-if="(status.chunks_total ?? 0) > 0">
                    <el-progress
                      :percentage="overviewVectorPercent"
                      :stroke-width="8"
                      :status="overviewVectorPercent >= 100 ? 'success' : ''"
                    />
                    <p class="overview-panel__meta">
                      {{ t('knowledge.vectorCounts', { ready: status.chunks_ready ?? 0, total: status.chunks_total ?? 0 }) }}
                      <template v-if="(status.chunks_failed ?? 0) > 0">
                        · <span class="text-warning">{{ t('knowledge.chunksFailed', { count: status.chunks_failed }) }}</span>
                      </template>
                    </p>
                  </template>
                  <p v-else class="overview-panel__meta">{{ t('knowledge.noVectorChunks') }}</p>
                  <p v-if="status.embedding?.model_id" class="overview-panel__meta" :title="status.embedding.model_id">
                    {{ t('knowledge.embeddingModel') }}: {{ status.embedding.model_id }}
                  </p>
                  <p class="overview-panel__hint">{{ t('knowledge.vectorHint') }}</p>
                </section>
              </div>

              <section v-if="sourceRows.length" class="overview-panel overview-sources">
                <h3>{{ t('knowledge.sourceDistribution') }}</h3>
                <div v-for="source in sourceRows" :key="source.pack_id" class="source-row">
                  <span class="source-row__name" :title="source.pack_id">{{ source.name || source.pack_id }}</span>
                  <div class="source-row__bar"><span :style="{ width: `${source.share}%` }" /></div>
                  <strong>{{ source.entries }}</strong>
                </div>
              </section>
              <el-empty v-else :description="t('knowledge.noPacks')" :image-size="56" />
            </template>
          </el-card>
          <el-empty v-else-if="!loading" :description="t('knowledge.loadFailed')" :image-size="56" />
        </div>
      </el-tab-pane>

      <el-tab-pane :label="t('knowledge.tabCatalog')" name="catalog">
        <div class="toolbar">
          <el-input
            v-model="query"
            clearable
            :placeholder="t('knowledge.searchPlaceholder')"
            @keyup.enter="loadEntries(true)"
            @clear="loadEntries(true)"
          />
          <el-select
            v-model="packFilter"
            clearable
            :placeholder="t('knowledge.allPacks')"
            @change="loadEntries(true)"
          >
            <el-option v-for="pack in packs" :key="pack.pack_id" :label="pack.pack_id" :value="pack.pack_id" />
          </el-select>
          <el-button type="primary" @click="loadEntries(true)">{{ t('common.search') }}</el-button>
        </div>
        <div class="table-shell">
          <el-table :data="entries" v-loading="entriesLoading" :row-key="entryRowKey" :empty-text="t('knowledge.noEntries')">
            <el-table-column :label="t('knowledge.entryTitle')" min-width="180">
              <template #default="scope">
                <button type="button" class="link-cell" :title="scope.row.title" @click="openEntry(scope.row)">
                  {{ scope.row.title }}
                </button>
              </template>
            </el-table-column>
            <el-table-column :label="t('knowledge.summary')" min-width="280" show-overflow-tooltip>
              <template #default="scope">
                {{ entryPreview(scope.row) }}
              </template>
            </el-table-column>
            <el-table-column :label="t('knowledge.pack')" width="170" show-overflow-tooltip>
              <template #default="scope">
                {{ scope.row.source?.name || scope.row.pack_id }}
              </template>
            </el-table-column>
            <el-table-column :label="t('knowledge.materialType')" width="110">
              <template #default="scope">
                <el-tag size="small" effect="plain" :type="scope.row.material_type === 'corpus' ? 'warning' : 'primary'">
                  {{ materialTypeLabel(scope.row.material_type) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="t('knowledge.entryEnabled')" width="96" align="center">
              <template #default="scope">
                <el-switch
                  :model-value="!scope.row.disabled"
                  :loading="pendingEntries.has(entryRowKey(scope.row))"
                  :aria-label="t('knowledge.entryEnabled')"
                  @change="toggleEntry(scope.row, !$event)"
                />
              </template>
            </el-table-column>
            <el-table-column :label="t('knowledge.actions')" width="90" align="center">
              <template #default="scope">
                <el-button link type="primary" @click="openEntry(scope.row)">{{ t('knowledge.details') }}</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
        <div class="pager">
          <el-button :disabled="offset === 0 || entriesLoading" @click="previousPage">{{ t('knowledge.previous') }}</el-button>
          <span>{{ pageRangeLabel }}</span>
          <el-button :disabled="!hasMore || entriesLoading" @click="nextPage">{{ t('knowledge.next') }}</el-button>
        </div>
      </el-tab-pane>

      <el-tab-pane :label="t('knowledge.tabPacks')" name="packs">
        <div class="toolbar">
          <input ref="fileInput" type="file" accept="application/json,.json" hidden @change="importSelectedPack" />
          <el-button type="primary" :loading="importing" @click="fileInput?.click()">{{ t('knowledge.importPack') }}</el-button>
          <span class="toolbar__hint">{{ t('knowledge.importHint') }}</span>
        </div>

        <section v-if="activeJobs.length" class="job-panel job-panel--active" aria-live="polite">
          <div class="job-panel__heading">
            <strong>{{ t('knowledge.activeJobs') }}</strong>
            <el-tag type="primary" effect="plain" size="small">{{ activeJobs.length }}</el-tag>
          </div>
          <p class="job-panel__hint">{{ t('knowledge.activeJobsHint') }}</p>
          <div v-for="job in activeJobs" :key="job.job_id" class="job-row">
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
              @click="cancelJob(job)"
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
          <div v-for="job in finishedFailedJobs" :key="job.job_id" class="job-row">
            <div class="job-row__identity">
              <strong>{{ job.pack_id || job.job_id }}</strong>
              <span>{{ jobStateLabel(job.state) }}<template v-if="job.reason"> · {{ reasonMessage(job.reason) }}</template></span>
            </div>
            <el-button
              plain
              :loading="pendingJobs.has(job.job_id)"
              :disabled="pendingJobs.has(job.job_id)"
              @click="discardJob(job)"
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
                    <el-tooltip v-if="scope.row.broken" :content="t('knowledge.brokenHint')" placement="top">
                      <el-tag type="danger" size="small">{{ t('knowledge.brokenTag') }}</el-tag>
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
            <el-table-column :label="t('knowledge.materialType')" width="170">
              <template #default="scope">
                <el-select
                  :model-value="scope.row.material_type_override ?? MATERIAL_FOLLOW"
                  :disabled="scope.row.broken || pendingPacks.has(scope.row.pack_id)"
                  :aria-label="t('knowledge.materialType')"
                  size="small"
                  @change="setPackMaterialType(scope.row, String($event))"
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
                  :disabled="scope.row.broken || pendingPacks.has(scope.row.pack_id)"
                  :aria-label="t('knowledge.localVectors')"
                  @change="setPackIndexPolicy(scope.row, Boolean($event))"
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
                  :disabled="scope.row.broken || pendingPacks.has(scope.row.pack_id)"
                  :aria-label="t('knowledge.autoContext')"
                  @change="setPackAuto(scope.row, Boolean($event))"
                />
              </template>
            </el-table-column>
            <el-table-column :label="t('knowledge.actions')" width="90" align="center">
              <template #default="scope">
                <el-button
                  link
                  type="danger"
                  :disabled="pendingPacks.has(scope.row.pack_id)"
                  @click="removePack(scope.row)"
                >
                  {{ t('knowledge.remove') }}
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
        <p class="footnote">{{ t('knowledge.autoContextHint') }}</p>
      </el-tab-pane>

      <el-tab-pane :label="t('knowledge.tabDiagnostics')" name="diagnostics">
        <div class="toolbar">
          <el-checkbox v-model="showMisses" data-testid="knowledge-show-misses">{{ t('knowledge.showMisses') }}</el-checkbox>
          <span v-if="!showMisses && hiddenMissCount > 0" class="toolbar__hint">
            {{ t('knowledge.hiddenMisses', { count: hiddenMissCount }) }}
          </span>
          <el-button class="toolbar__end" :loading="diagnosticsLoading" @click="loadDiagnostics">{{ t('common.refresh') }}</el-button>
        </div>
        <h3 class="section-title">{{ t('knowledge.recentQueries') }}</h3>
        <div class="table-shell">
          <el-table
            class="diagnostics-table"
            :data="visibleQueries"
            v-loading="diagnosticsLoading"
            :empty-text="t('knowledge.noQueries')"
          >
            <el-table-column :label="t('knowledge.time')" width="120">
              <template #default="scope">
                <time class="diagnostic-time" :datetime="scope.row.timestamp">
                  <span class="diagnostic-time__date">{{ formatDate(scope.row.timestamp) }}</span>
                  <span class="diagnostic-time__clock">{{ formatClock(scope.row.timestamp) }}</span>
                </time>
              </template>
            </el-table-column>
            <el-table-column :label="t('knowledge.result')" width="110">
              <template #default="scope">
                <el-tag
                  class="result-tag"
                  :type="diagnosticResultTagType(scope.row.result)"
                  effect="plain"
                  :data-result="scope.row.result"
                >
                  {{ queryResultLabel(scope.row.result) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="t('knowledge.matchedTitle')" min-width="200" show-overflow-tooltip>
              <template #default="scope">
                <span :class="{ 'is-empty': !scope.row.entry_title }">{{ scope.row.entry_title || EMPTY_MARK }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="t('knowledge.queryMode')" width="100">
              <template #default="scope">{{ queryModeLabel(scope.row.mode) }}</template>
            </el-table-column>
            <el-table-column :label="t('knowledge.retrievalMode')" width="110">
              <template #default="scope">{{ retrievalModeLabel(scope.row.retrieval_mode) }}</template>
            </el-table-column>
            <el-table-column :label="t('knowledge.hits')" width="70" align="right">
              <template #default="scope">{{ scope.row.hits ?? 0 }}</template>
            </el-table-column>
            <el-table-column :label="t('knowledge.elapsedMs')" width="96" align="right">
              <template #default="scope">{{ scope.row.elapsed_ms ?? 0 }}</template>
            </el-table-column>
            <el-table-column :label="t('knowledge.errorType')" min-width="130" show-overflow-tooltip>
              <template #default="scope">
                <span :class="{ 'is-empty': !scope.row.error_type }">{{ scope.row.error_type || EMPTY_MARK }}</span>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <h3 class="section-title">{{ t('knowledge.indexBatches') }}</h3>
        <div class="table-shell">
          <el-table :data="indexBatches" size="small" :empty-text="t('knowledge.noBatches')">
            <el-table-column :label="t('knowledge.time')" width="170">
              <template #default="scope">{{ formatDate(scope.row.timestamp) }} {{ formatClock(scope.row.timestamp) }}</template>
            </el-table-column>
            <el-table-column :label="t('knowledge.embeddingModel')" min-width="180" show-overflow-tooltip>
              <template #default="scope">{{ scope.row.model_id || EMPTY_MARK }}</template>
            </el-table-column>
            <el-table-column prop="selected" :label="t('knowledge.batchSelected')" width="90" align="right" />
            <el-table-column prop="stored" :label="t('knowledge.batchStored')" width="90" align="right" />
            <el-table-column :label="t('knowledge.batchFailed')" width="90" align="right">
              <template #default="scope">
                <span :class="{ 'text-warning': scope.row.failed > 0 }">{{ scope.row.failed }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="elapsed_ms" :label="t('knowledge.elapsedMs')" width="96" align="right" />
          </el-table>
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-drawer v-model="drawerOpen" class="knowledge-entry-drawer" size="620px">
      <template #header>
        <div v-if="selectedEntry" class="entry-drawer-header">
          <strong :title="selectedEntry.title">{{ selectedEntry.title }}</strong>
          <div class="entry-drawer-meta">
            <el-tag effect="plain">{{ selectedEntry.source?.name || selectedEntry.pack_id }}</el-tag>
            <el-tag effect="plain" :type="selectedEntry.material_type === 'corpus' ? 'warning' : 'primary'">
              {{ materialTypeLabel(selectedEntry.material_type) }}
            </el-tag>
            <el-tag :type="selectedEntry.disabled ? 'danger' : 'success'" effect="plain">
              {{ selectedEntry.disabled ? t('knowledge.entryDisabled') : t('knowledge.entryEnabled') }}
            </el-tag>
          </div>
        </div>
      </template>
      <div v-if="selectedEntry" class="entry-drawer-body">
        <section class="entry-detail-section">
          <h3>{{ t('knowledge.summary') }}</h3>
          <p>{{ selectedEntry.summary || EMPTY_MARK }}</p>
        </section>

        <section v-if="selectedEntryTermGroups.length" class="entry-detail-section">
          <h3>{{ t('knowledge.terms') }}</h3>
          <div class="term-groups">
            <section v-for="group in selectedEntryTermGroups" :key="group.key" class="term-group">
              <span>{{ group.label }}</span>
              <div class="chip-list">
                <el-tag v-for="value in group.values" :key="value" effect="plain">{{ value }}</el-tag>
              </div>
            </section>
          </div>
        </section>

        <section v-if="selectedEntry.tags?.length" class="entry-detail-section">
          <h3>{{ t('knowledge.tags') }}</h3>
          <div class="chip-list">
            <el-tag v-for="tag in selectedEntry.tags" :key="tag" effect="plain">{{ tag }}</el-tag>
          </div>
        </section>

        <section class="entry-detail-section">
          <h3>{{ t('knowledge.content') }}</h3>
          <pre class="entry-content">{{ selectedEntry.content }}</pre>
        </section>

        <section class="entry-detail-section">
          <h3>{{ t('knowledge.source') }}</h3>
          <dl class="source-detail">
            <div><dt>{{ t('knowledge.pack') }}</dt><dd>{{ selectedEntry.source?.name || EMPTY_MARK }} ({{ selectedEntry.pack_id }})</dd></div>
            <div>
              <dt>{{ t('knowledge.homepage') }}</dt>
              <dd>
                <a
                  v-if="selectedEntry.source?.homepage"
                  href="#"
                  @click.prevent="openExternalUrl(selectedEntry.source.homepage)"
                >{{ selectedEntry.source.homepage }}</a>
                <template v-else>{{ EMPTY_MARK }}</template>
              </dd>
            </div>
            <div><dt>{{ t('knowledge.license') }}</dt><dd>{{ selectedEntry.source?.license || EMPTY_MARK }}</dd></div>
          </dl>
        </section>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'
import {
  isActiveKnowledgeJob,
  isKnownKnowledgeReason,
  knowledgeApi,
  knowledgeFailureReason,
  type KnowledgeDiagnosticQuery,
  type KnowledgeEntry,
  type KnowledgeEntryDetail,
  type KnowledgeIndexBatch,
  type KnowledgeMaterialType,
  type KnowledgePack,
  type KnowledgePackJob,
  type KnowledgeStatus,
  type KnowledgeVectorState,
} from '@/api/knowledge'
import { createLatestRequestGate } from '@/utils/latestRequest'
import { createOverviewRequestGate, type OverviewRequestTicket } from '@/utils/overviewRequestGate'
import {
  diagnosticResultTagType,
  finishedJobTransitions,
  jobStateSnapshot,
  nextKnowledgePollDelay,
  vectorProgressPercent,
  vectorStateTagType,
  visibleDiagnosticQueries,
} from '@/utils/knowledgeDisplay'
import { openExternalUrl } from '@/utils/openExternal'

const MATERIAL_FOLLOW = '__follow__'
const EMPTY_MARK = '—'
const PAGE_SIZE = 50
// Load failures caused by the service not being ready are already explained
// by the status card; do not stack a toast on top of it.
const QUIET_LOAD_REASONS = new Set(['knowledge_starting', 'knowledge_unavailable'])

const { t } = useI18n()

const activeTab = ref('overview')
const loading = ref(false)
const status = ref<KnowledgeStatus | null>(null)
const savingEnabled = ref(false)

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

const packs = ref<KnowledgePack[]>([])
const packsLoading = ref(false)
const pendingPacks = reactive(new Set<string>())
const packJobs = ref<KnowledgePackJob[]>([])
const pendingJobs = reactive(new Set<string>())
const importing = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const queries = ref<KnowledgeDiagnosticQuery[]>([])
const indexBatches = ref<KnowledgeIndexBatch[]>([])
const diagnosticsLoading = ref(false)
const showMisses = ref(false)

const overviewGate = createOverviewRequestGate()
const jobsGate = createLatestRequestGate()
const entriesGate = createLatestRequestGate()
const entryGate = createLatestRequestGate()
const diagnosticsGate = createLatestRequestGate()

let lastJobStates = new Map<string, string>()
let pollTimer: number | null = null
let pollTimerDelay = 0
let pollInFlight = false
let disposed = false

// ── derived state ────────────────────────────────────────────────────

const serviceStateTagType = computed(() => {
  const state = status.value?.state
  if (state === 'ready') return 'success'
  if (state === 'starting') return 'warning'
  return 'danger'
})

const serviceStateLabel = computed(() => {
  const state = status.value?.state
  if (state === 'ready') return t('knowledge.stateReady')
  if (state === 'starting') return t('knowledge.stateStarting')
  return t('knowledge.stateUnavailable')
})

const statusErrorMessage = computed(() => {
  const code = String(status.value?.error_code || '').trim()
  if (!code) return ''
  if (code === 'registry_invalid') return t('knowledge.statusErrorRegistryInvalid')
  return t('knowledge.statusErrorGeneric', { code })
})

const brokenPackIds = computed(() => {
  const fromStatus = status.value?.broken_packs ?? []
  const fromPacks = packs.value.filter((pack) => pack.broken).map((pack) => pack.pack_id)
  return [...new Set([...fromStatus, ...fromPacks])]
})

const knowledgeShare = computed(() => {
  const knowledge = Math.max(0, Number(status.value?.knowledge_entries) || 0)
  const corpus = Math.max(0, Number(status.value?.corpus_entries) || 0)
  const total = knowledge + corpus
  return total > 0 ? Math.round((knowledge * 1000) / total) / 10 : 100
})

const overviewVectorPercent = computed(() =>
  vectorProgressPercent(status.value?.chunks_ready, status.value?.chunks_total),
)

const embeddingTagType = computed(() => {
  const state = status.value?.embedding?.state
  if (state === 'ready') return 'success'
  if (state === 'loading') return 'primary'
  if (state === 'unavailable') return 'warning'
  return 'info'
})

const embeddingStateLabel = computed(() => {
  const state = status.value?.embedding?.state
  if (state === 'ready') return t('knowledge.embeddingReady')
  if (state === 'loading') return t('knowledge.embeddingLoading')
  if (state === 'disabled') return t('knowledge.embeddingDisabled')
  return t('knowledge.embeddingUnavailable')
})

const sourceRows = computed(() => {
  const sources = (status.value?.sources ?? [])
    .map((source) => ({ ...source, entries: Math.max(0, Number(source.entries) || 0) }))
    .sort((a, b) => b.entries - a.entries)
  const max = Math.max(1, ...sources.map((source) => source.entries))
  return sources.map((source) => ({ ...source, share: Math.round((source.entries * 100) / max) }))
})

const pageRangeLabel = computed(() => {
  if (!entries.value.length) return '0'
  const from = offset.value + 1
  const to = offset.value + entries.value.length
  return totalEntries.value === null
    ? t('knowledge.pageRange', { from, to })
    : t('knowledge.pageRangeTotal', { from, to, total: totalEntries.value })
})

const activeJobs = computed(() => packJobs.value.filter(isActiveKnowledgeJob))
const finishedFailedJobs = computed(() =>
  packJobs.value.filter((job) => job.state === 'failed' || job.state === 'cancelled'),
)

const visibleQueries = computed(() => visibleDiagnosticQueries(queries.value, showMisses.value))
const hiddenMissCount = computed(() => queries.value.length - visibleQueries.value.length)

const selectedEntryTermGroups = computed(() => {
  const entry = selectedEntry.value
  if (!entry) return []
  return [
    { key: 'alias', label: t('knowledge.aliasTerms'), values: uniqueTerms(entry.terms?.alias) },
    { key: 'recognition', label: t('knowledge.recognitionPhrases'), values: uniqueTerms(entry.terms?.recognition) },
  ].filter((group) => group.values.length > 0)
})

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

function materialTypeLabel(type: KnowledgeMaterialType | string | null | undefined): string {
  return type === 'corpus' ? t('knowledge.typeCorpus') : t('knowledge.typeKnowledge')
}

function vectorStateLabel(state: KnowledgeVectorState | string): string {
  const keys: Record<string, string> = {
    none: 'knowledge.vectorNone',
    complete: 'knowledge.vectorComplete',
    building: 'knowledge.vectorBuilding',
    waiting: 'knowledge.vectorWaiting',
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

function queryResultLabel(result: string): string {
  const keys: Record<string, string> = {
    matched: 'knowledge.resultMatched',
    miss: 'knowledge.resultMiss',
    timeout: 'knowledge.resultTimeout',
    busy: 'knowledge.resultBusy',
    error: 'knowledge.resultError',
    disabled: 'knowledge.resultDisabled',
    unavailable: 'knowledge.resultUnavailable',
  }
  return keys[result] ? t(keys[result]) : String(result || EMPTY_MARK)
}

function queryModeLabel(mode: string): string {
  if (mode === 'lookup') return t('knowledge.modeLookup')
  if (mode === 'sample') return t('knowledge.modeSample')
  return mode || EMPTY_MARK
}

function retrievalModeLabel(mode: string): string {
  if (mode === 'bm25') return 'BM25'
  if (mode === 'hybrid') return t('knowledge.retrievalHybrid')
  if (mode === 'sample') return t('knowledge.modeSample')
  return EMPTY_MARK
}

function formatDate(value: unknown): string {
  const parsed = dayjs(String(value ?? ''))
  return parsed.isValid() ? parsed.format('YYYY-MM-DD') : EMPTY_MARK
}

function formatClock(value: unknown): string {
  const parsed = dayjs(String(value ?? ''))
  return parsed.isValid() ? parsed.format('HH:mm:ss') : ''
}

function entryRowKey(row: Pick<KnowledgeEntry, 'pack_id' | 'title'>): string {
  return JSON.stringify([row.pack_id, row.title])
}

function entryPreview(row: KnowledgeEntry): string {
  return String(row.summary || row.content_preview || '').trim() || EMPTY_MARK
}

function packRowClass({ row }: { row: KnowledgePack }): string {
  return row.broken ? 'is-broken' : ''
}

function uniqueTerms(values: unknown): string[] {
  if (!Array.isArray(values)) return []
  return [...new Set(values.map((value) => String(value ?? '').trim()).filter(Boolean))]
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
  const transitions = finishedJobTransitions(lastJobStates, jobs)
  for (const { job, outcome } of transitions) {
    const name = job.pack_id || job.job_id
    if (outcome === 'active') {
      ElMessage.success(t('knowledge.importDone', { name }))
    } else if (outcome === 'failed') {
      ElMessage.error(t('knowledge.importFailed', { name, reason: reasonMessage(job.reason) }))
    } else {
      ElMessage.info(t('knowledge.importCancelled', { name }))
    }
  }
  lastJobStates = jobStateSnapshot(jobs)
  packJobs.value = jobs
  // Status and packs were fetched in parallel with these jobs and may predate
  // the finish; once no job is running nothing else would reload them.
  if (transitions.length) {
    void Promise.allSettled([loadStatus({ silent: true }), loadPacks({ silent: true })])
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

async function loadDiagnostics() {
  const requestId = diagnosticsGate.begin()
  diagnosticsLoading.value = true
  try {
    const response = await knowledgeApi.diagnostics()
    if (disposed || !diagnosticsGate.isLatest(requestId)) return
    queries.value = response.queries ?? []
    indexBatches.value = response.index_batches ?? []
  } catch (error) {
    if (!disposed && diagnosticsGate.isLatest(requestId)) reportLoadFailure(error)
  } finally {
    if (diagnosticsGate.isLatest(requestId)) diagnosticsLoading.value = false
  }
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

async function openEntry(row: KnowledgeEntry) {
  const requestId = entryGate.begin()
  try {
    const response = await knowledgeApi.entry({ pack_id: row.pack_id, title: row.title })
    if (disposed || !entryGate.isLatest(requestId)) return
    selectedEntry.value = response.entry ?? null
    drawerOpen.value = Boolean(selectedEntry.value)
  } catch (error) {
    if (!disposed && entryGate.isLatest(requestId)) ElMessage.error(errorMessage(error))
  }
}

async function toggleEntry(row: KnowledgeEntry, disabled: boolean) {
  const key = entryRowKey(row)
  if (pendingEntries.has(key)) return
  pendingEntries.add(key)
  try {
    const response = await knowledgeApi.setEntryDisabled({ pack_id: row.pack_id, title: row.title, disabled })
    row.disabled = response.disabled ?? disabled
    if (selectedEntry.value && entryRowKey(selectedEntry.value) === key) {
      selectedEntry.value = { ...selectedEntry.value, disabled: row.disabled }
    }
    refreshAfterMutation()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    pendingEntries.delete(key)
  }
}

async function importSelectedPack(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || importing.value) return
  importing.value = true
  try {
    const response = await knowledgeApi.importPack(file)
    if (disposed) return
    if (response.unchanged) {
      ElMessage.info(t('knowledge.importUnchanged', { name: response.pack_id }))
    } else {
      ElMessage.success(t('knowledge.importQueued', { name: response.pack_id }))
      const jobId = String(response.job_id || '')
      if (jobId && !packJobs.value.some((job) => job.job_id === jobId)) {
        const queued: KnowledgePackJob = {
          job_id: jobId,
          pack_id: response.pack_id,
          state: 'queued',
          reason: '',
          entries_total: Number(response.entries_total) || 0,
          chunks_total: Number(response.chunks_total) || 0,
          created_at: '',
          updated_at: '',
        }
        lastJobStates.set(jobId, 'queued')
        packJobs.value = [queued, ...packJobs.value]
      }
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
  if (pendingJobs.has(job.job_id)) return
  pendingJobs.add(job.job_id)
  try {
    await knowledgeApi.discardPackJob({ job_id: job.job_id })
    jobsGate.invalidate()
    packJobs.value = packJobs.value.filter((item) => item.job_id !== job.job_id)
    lastJobStates.delete(job.job_id)
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
    diagnosticsGate.invalidate()
    diagnosticsLoading.value = false
  }
  if (tab === 'catalog') void loadEntries(true)
  if (tab === 'packs') {
    void loadPacks({ silent: packs.value.length > 0 })
    void loadJobs({ silent: true })
  }
  if (tab === 'diagnostics') void loadDiagnostics()
})

watch([packJobs, hasBackgroundProgress, activeTab], schedulePoll)

onMounted(() => {
  void refreshOverview()
})

onUnmounted(() => {
  disposed = true
  clearPollTimer()
  overviewGate.invalidate()
  jobsGate.invalidate()
  entriesGate.invalidate()
  entryGate.invalidate()
  diagnosticsGate.invalidate()
})
</script>

<style scoped>
.knowledge-manager {
  --knowledge-surface: var(--el-bg-color);
  --knowledge-surface-muted: var(--el-fill-color-extra-light);
  --knowledge-line: var(--el-border-color-lighter);
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 18px;
  width: 100%;
  min-width: 0;
  padding: 24px 24px 72px;
  overflow-x: clip;
}

.page-heading,
.card-heading,
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
  border-radius: 10px 10px 0 0;
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

.knowledge-tabs :deep(.el-tabs__content) {
  min-width: 0;
  padding: 18px;
}

.overview {
  min-height: 120px;
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
  display: grid;
  gap: 16px;
  padding: 18px;
}

.card-heading__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.overview-alert {
  border-radius: 8px;
}

.global-switch {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px;
  border: 1px solid var(--el-color-primary-light-7);
  border-radius: 8px;
  background: var(--el-color-primary-light-9);
}

.global-switch__text {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.global-switch__text strong {
  color: var(--el-text-color-primary);
  font-size: 14px;
}

.global-switch__text p {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.status-metrics {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin: 0;
}

.status-metric {
  padding: 16px;
  border-radius: 8px;
  background: var(--knowledge-surface-muted);
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

.overview-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(280px, 100%), 1fr));
  gap: 12px;
}

.overview-panel {
  display: grid;
  align-content: start;
  gap: 10px;
  min-width: 0;
  padding: 14px 16px;
  border: 1px solid var(--knowledge-line);
  border-radius: 8px;
  background: var(--knowledge-surface);
}

.overview-panel h3,
.section-title {
  margin: 0;
  color: var(--el-text-color-primary);
  font-size: 14px;
  font-weight: 700;
}

.section-title {
  margin: 18px 0 10px;
}

.section-title:first-of-type {
  margin-top: 0;
}

.overview-panel__heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.overview-panel__meta,
.overview-panel__hint {
  margin: 0;
  overflow: hidden;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  text-overflow: ellipsis;
  font-variant-numeric: tabular-nums;
}

.overview-panel__hint {
  white-space: normal;
  line-height: 1.6;
}

.split-bar {
  display: flex;
  height: 10px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--knowledge-line);
}

.split-bar__knowledge {
  background: var(--el-color-primary);
}

.split-bar__corpus {
  background: var(--el-color-warning);
}

.split-legend {
  display: grid;
  gap: 6px;
}

.split-legend__item {
  display: grid;
  grid-template-columns: 10px auto minmax(0, 1fr);
  gap: 8px;
  align-items: center;
  color: var(--el-text-color-regular);
  font-size: 13px;
}

.split-legend__item small {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.dot {
  width: 10px;
  height: 10px;
  border-radius: 999px;
}

.dot--knowledge {
  background: var(--el-color-primary);
}

.dot--corpus {
  background: var(--el-color-warning);
}

.overview-sources {
  gap: 8px;
}

.source-row {
  display: grid;
  grid-template-columns: minmax(0, 200px) minmax(0, 1fr) 56px;
  gap: 10px;
  align-items: center;
  font-size: 13px;
}

.source-row__name {
  overflow: hidden;
  color: var(--el-text-color-regular);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.source-row__bar {
  height: 6px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--knowledge-surface-muted);
}

.source-row__bar span {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: var(--el-color-primary-light-3);
}

.source-row strong {
  color: var(--el-text-color-primary);
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.text-warning {
  color: var(--el-color-warning);
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
  width: min(220px, 100%);
}

.toolbar .el-input {
  flex: 1 1 320px;
  min-width: 0;
  max-width: 520px;
}

.toolbar__hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.toolbar__end {
  margin-left: auto;
}

.job-panel {
  display: grid;
  gap: 10px;
  min-width: 0;
  margin-bottom: 14px;
  padding: 14px;
  border-radius: 8px;
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
  border-radius: 7px;
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
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-weight: 600;
}

.packs-table :deep(.el-table__row.is-broken) {
  --el-table-tr-bg-color: var(--el-color-danger-light-9);
}

.link-cell {
  display: block;
  max-width: 100%;
  padding: 0;
  overflow: hidden;
  border: 0;
  background: none;
  color: var(--el-text-color-primary);
  font: inherit;
  font-weight: 500;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.link-cell:hover,
.link-cell:focus-visible {
  color: var(--el-color-primary);
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

.result-tag {
  border-radius: 6px;
  font-weight: 500;
}

.is-empty {
  color: var(--el-text-color-placeholder);
}

.pager {
  justify-content: flex-end;
  margin-top: 14px;
}

.pager span {
  min-width: 70px;
  color: var(--el-text-color-secondary);
  text-align: center;
  font-variant-numeric: tabular-nums;
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
  overflow: hidden;
  color: var(--el-text-color-primary);
  font-size: 17px;
  line-height: 1.45;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.entry-drawer-meta,
.chip-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}

.entry-drawer-meta :deep(.el-tag),
.chip-list :deep(.el-tag) {
  max-width: 100%;
  border-radius: 6px;
}

.chip-list :deep(.el-tag__content) {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
  background: var(--knowledge-surface-muted);
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

.entry-content {
  max-height: 360px;
  margin: 0;
  padding: 12px 14px;
  overflow: auto;
  border: 1px solid var(--el-border-color-extra-light);
  border-radius: 8px;
  background: var(--knowledge-surface);
  color: var(--el-text-color-regular);
  font-size: 13px;
  line-height: 1.8;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.source-detail {
  display: grid;
  gap: 8px;
  margin: 0;
}

.source-detail div {
  display: grid;
  grid-template-columns: minmax(80px, auto) minmax(0, 1fr);
  gap: 8px;
}

.source-detail dd {
  margin: 0;
  font-size: 13px;
  font-weight: 500;
  overflow-wrap: anywhere;
}

.source-detail a {
  color: var(--el-color-primary);
}

@media (max-width: 640px) {
  .knowledge-manager {
    padding: 16px 16px calc(128px + env(safe-area-inset-bottom));
  }

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

  .knowledge-tabs :deep(.el-tabs__item) {
    min-width: 86px;
    height: 34px;
    padding: 0 8px;
  }

  .knowledge-tabs :deep(.el-tabs__content) {
    padding: 14px;
  }

  .status-metrics {
    grid-template-columns: 1fr;
  }

  .status-card :deep(.el-card__header),
  .status-card :deep(.el-card__body) {
    padding: 12px;
  }

  .global-switch {
    align-items: flex-start;
  }

  .source-row {
    grid-template-columns: minmax(0, 1fr) 56px;
  }

  .source-row__bar {
    display: none;
  }

  .toolbar .el-input,
  .toolbar .el-select,
  .toolbar .el-button {
    width: 100%;
    max-width: none;
  }

  .toolbar__end {
    margin-left: 0;
  }

  .pager {
    justify-content: center;
  }
}
</style>
