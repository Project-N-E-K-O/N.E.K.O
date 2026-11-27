<template>
  <div>
    <div class="toolbar">
      <el-input
        v-model="query"
        clearable
        :placeholder="t('knowledge.searchPlaceholder')"
        @keydown="enterKey.down"
        @keyup.enter="enterKey.up(() => emit('search'))"
        @clear="emit('search')"
      />
      <el-select
        v-model="packFilter"
        clearable
        :placeholder="t('knowledge.allPacks')"
        @change="emit('search')"
      >
        <el-option v-for="pack in packs" :key="pack.pack_id" :label="pack.pack_id" :value="pack.pack_id" />
      </el-select>
      <el-button type="primary" @click="emit('search')">{{ t('common.search') }}</el-button>
    </div>
    <div class="table-shell">
      <el-table :data="entries" v-loading="entriesLoading" :row-key="entryRowKey" :empty-text="t('knowledge.noEntries')">
        <el-table-column :label="t('knowledge.entryTitle')" min-width="180">
          <template #default="scope">
            <button type="button" class="link-cell" :title="scope.row.title" @click="emit('open-entry', scope.row)">
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
              @change="emit('toggle-entry', scope.row, !$event)"
            />
          </template>
        </el-table-column>
        <el-table-column :label="t('knowledge.actions')" width="90" align="center">
          <template #default="scope">
            <el-button link type="primary" @click="emit('open-entry', scope.row)">{{ t('knowledge.details') }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <div class="pager">
      <el-button :disabled="offset === 0 || entriesLoading" @click="emit('previous')">{{ t('knowledge.previous') }}</el-button>
      <span>{{ pageRangeLabel }}</span>
      <el-button :disabled="!hasMore || entriesLoading" @click="emit('next')">{{ t('knowledge.next') }}</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
/** Catalog display and filter inputs; reads and mutations stay on the page. */
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { KnowledgeEntry, KnowledgeMaterialType, KnowledgePack } from '@/api/knowledge'
import { knowledgeEntryKey as entryRowKey } from '@/utils/knowledgeDisplay'
import { useEnterKey } from '@/utils/enterKey'

const props = defineProps<{
  packs: KnowledgePack[]
  entries: KnowledgeEntry[]
  entriesLoading: boolean
  offset: number
  totalEntries: number | null
  hasMore: boolean
  pendingEntries: ReadonlySet<string>
}>()
const query = defineModel<string>('query', { required: true })
const packFilter = defineModel<string>('packFilter', { required: true })
const emit = defineEmits<{
  search: []
  previous: []
  next: []
  'open-entry': [entry: KnowledgeEntry]
  'toggle-entry': [entry: KnowledgeEntry, disabled: boolean]
}>()
const { t } = useI18n()
// Keep the input-method Enter guard with the catalog's search field.
const enterKey = useEnterKey()
const EMPTY_MARK = '—'
const pageRangeLabel = computed(() => {
  if (!props.entries.length) return '0'
  const from = props.offset + 1
  const to = props.offset + props.entries.length
  return props.totalEntries === null
    ? t('knowledge.pageRange', { from, to })
    : t('knowledge.pageRangeTotal', { from, to, total: props.totalEntries })
})

function materialTypeLabel(type: KnowledgeMaterialType | string | null | undefined): string {
  return type === 'corpus' ? t('knowledge.typeCorpus') : t('knowledge.typeKnowledge')
}

function entryPreview(row: KnowledgeEntry): string {
  return String(row.summary || row.content_preview || '').trim() || EMPTY_MARK
}
</script>

<!-- Scope shared table chrome to this component, never to unrelated pages. -->
<style scoped src="./knowledgeTables.css"></style>

<style scoped>
.pager {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
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

@media (max-width: 640px) {
  .pager {
      justify-content: center;
    }
}
</style>
