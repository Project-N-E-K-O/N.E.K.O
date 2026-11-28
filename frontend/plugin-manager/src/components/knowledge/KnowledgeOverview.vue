<template>
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
          @change="emit('enabled', Boolean($event))"
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
</template>

<script setup lang="ts">
/** Overview presentation only; the page owns status loading and the global write. */
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { KnowledgePack, KnowledgeStatus } from '@/api/knowledge'
import { vectorProgressPercent } from '@/utils/knowledgeDisplay'

const props = defineProps<{
  status: KnowledgeStatus | null
  packs: KnowledgePack[]
  loading: boolean
  savingEnabled: boolean
}>()
const emit = defineEmits<{ enabled: [enabled: boolean] }>()
const { t } = useI18n()
const serviceStateTagType = computed(() => {
  const state = props.status?.state
  if (state === 'ready') return 'success'
  if (state === 'starting') return 'warning'
  return 'danger'
})

const serviceStateLabel = computed(() => {
  const state = props.status?.state
  if (state === 'ready') return t('knowledge.stateReady')
  if (state === 'starting') return t('knowledge.stateStarting')
  return t('knowledge.stateUnavailable')
})

const statusErrorMessage = computed(() => {
  const code = String(props.status?.error_code || '').trim()
  if (!code) return ''
  if (code === 'registry_invalid') return t('knowledge.statusErrorRegistryInvalid')
  return t('knowledge.statusErrorGeneric', { code })
})

const brokenPackIds = computed(() => {
  const fromStatus = props.status?.broken_packs ?? []
  const fromPacks = props.packs.filter((pack) => pack.broken).map((pack) => pack.pack_id)
  return [...new Set([...fromStatus, ...fromPacks])]
})

const knowledgeShare = computed(() => {
  const knowledge = Math.max(0, Number(props.status?.knowledge_entries) || 0)
  const corpus = Math.max(0, Number(props.status?.corpus_entries) || 0)
  const total = knowledge + corpus
  return total > 0 ? Math.round((knowledge * 1000) / total) / 10 : 100
})

const overviewVectorPercent = computed(() =>
  vectorProgressPercent(props.status?.chunks_ready, props.status?.chunks_total),
)

const embeddingTagType = computed(() => {
  const state = props.status?.embedding?.state
  if (state === 'ready') return 'success'
  if (state === 'loading') return 'primary'
  if (state === 'unavailable') return 'warning'
  return 'info'
})

const embeddingStateLabel = computed(() => {
  const state = props.status?.embedding?.state
  if (state === 'ready') return t('knowledge.embeddingReady')
  if (state === 'loading') return t('knowledge.embeddingLoading')
  if (state === 'disabled') return t('knowledge.embeddingDisabled')
  return t('knowledge.embeddingUnavailable')
})

const sourceRows = computed(() => {
  const sources = (props.status?.sources ?? [])
    .map((source) => ({ ...source, entries: Math.max(0, Number(source.entries) || 0) }))
    .sort((a, b) => b.entries - a.entries)
  const max = Math.max(1, ...sources.map((source) => source.entries))
  return sources.map((source) => ({ ...source, share: Math.round((source.entries * 100) / max) }))
})
</script>

<style scoped>
.card-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.overview {
  min-height: 120px;
}

.status-card {
  border-color: transparent;
  border-radius: var(--radius-panel);
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
  border-radius: var(--radius-panel);
}

.global-switch {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px;
  border: 1px solid var(--el-color-primary-light-7);
  border-radius: var(--radius-panel);
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
  border-radius: var(--radius-panel);
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
  border-radius: var(--radius-panel);
  background: var(--knowledge-surface);
}

.overview-panel h3 {
  margin: 0;
  color: var(--el-text-color-primary);
  font-size: 14px;
  font-weight: 700;
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

@media (max-width: 640px) {
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
}
</style>
