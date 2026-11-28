<template>
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
</template>

<script setup lang="ts">
/** Entry detail presentation; the page keeps request identity and write reconciliation. */
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { KnowledgeEntryDetail, KnowledgeMaterialType } from '@/api/knowledge'
import { openExternalUrl } from '@/utils/openExternal'

const props = defineProps<{ selectedEntry: KnowledgeEntryDetail | null }>()
const drawerOpen = defineModel<boolean>({ required: true })
const { t } = useI18n()
const EMPTY_MARK = '—'
const selectedEntryTermGroups = computed(() => {
  const entry = props.selectedEntry
  if (!entry) return []
  return [
    { key: 'alias', label: t('knowledge.aliasTerms'), values: uniqueTerms(entry.terms?.alias) },
    { key: 'recognition', label: t('knowledge.recognitionPhrases'), values: uniqueTerms(entry.terms?.recognition) },
  ].filter((group) => group.values.length > 0)
})

function materialTypeLabel(type: KnowledgeMaterialType | string | null | undefined): string {
  return type === 'corpus' ? t('knowledge.typeCorpus') : t('knowledge.typeKnowledge')
}

function uniqueTerms(values: unknown): string[] {
  if (!Array.isArray(values)) return []
  return [...new Set(values.map((value) => String(value ?? '').trim()).filter(Boolean))]
}
</script>

<style scoped>
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

:global(.knowledge-entry-drawer) {
  --knowledge-surface: var(--surface-card);
  --knowledge-surface-muted: var(--surface-inset);
  --knowledge-line: var(--surface-border);
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
  border-radius: var(--radius-chip);
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
  border-radius: var(--radius-panel);
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
  border-radius: var(--radius-panel);
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
}
</style>
