<template>
  <div>
    <div class="toolbar">
      <el-checkbox v-model="showMisses" data-testid="knowledge-show-misses">{{ t('knowledge.showMisses') }}</el-checkbox>
      <span v-if="!showMisses && hiddenMissCount > 0" class="toolbar__hint">
        {{ t('knowledge.hiddenMisses', { count: hiddenMissCount }) }}
      </span>
      <el-button class="toolbar__end" :loading="diagnosticsLoading" @click="emit('refresh')">{{ t('common.refresh') }}</el-button>
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
  </div>
</template>

<script setup lang="ts">
/** Diagnostics filtering is local presentation; the page owns fetch ordering. */
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'
import type { KnowledgeDiagnosticQuery, KnowledgeIndexBatch } from '@/api/knowledge'
import { diagnosticResultTagType, visibleDiagnosticQueries } from '@/utils/knowledgeDisplay'

const props = defineProps<{
  queries: KnowledgeDiagnosticQuery[]
  indexBatches: KnowledgeIndexBatch[]
  diagnosticsLoading: boolean
}>()
const emit = defineEmits<{ refresh: [] }>()
const { t } = useI18n()
const showMisses = ref(false)
const EMPTY_MARK = '—'
const visibleQueries = computed(() => visibleDiagnosticQueries(props.queries, showMisses.value))
const hiddenMissCount = computed(() => props.queries.length - visibleQueries.value.length)

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
  if (mode === 'passive') return t('knowledge.autoContext')
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
</script>

<!-- Scope shared table chrome to this component, never to unrelated pages. -->
<style scoped src="./knowledgeTables.css"></style>

<style scoped>
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

.text-warning {
  color: var(--el-color-warning);
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
  border-radius: var(--radius-chip);
  font-weight: 500;
}

.is-empty {
  color: var(--el-text-color-placeholder);
}
</style>
