<template>
  <section
    class="run-output"
    data-yui-guide-id="runs-export-section"
    aria-labelledby="runs-output-title"
    :aria-busy="resource.phase === 'loading'"
  >
    <header class="output-heading">
      <h3 id="runs-output-title">{{ t('runs.outputTitle') }}</h3>
      <span v-if="resource.loaded" class="output-count">{{ items.length }}</span>
    </header>
    <div v-if="resource.error" class="output-feedback" role="alert">
      <span>{{ t(resource.error) }}</span>
      <el-button size="small" @click="$emit('retry')">{{ t('runs.retry') }}</el-button>
    </div>
    <p v-if="!resource.loaded && !resource.error" class="output-empty" role="status">
      {{ t('runs.loadingOutputs') }}
    </p>
    <p
      v-else-if="resource.loaded && items.length === 0"
      class="output-empty"
      data-testid="run-output-empty"
    >
      <el-icon><Document /></el-icon>{{ t('runs.noExport') }}
    </p>
    <div
      v-if="resource.loaded && items.length"
      class="output-items"
      data-yui-guide-id="runs-export-table"
    >
      <details v-for="item in items" :key="item.export_item_id" class="output-item" open>
        <summary class="output-item__header">
          <el-icon><Document /></el-icon>
          <span class="output-item__label"
            >{{ item.label || item.description || typeLabel(item)
            }}<span v-if="item.label || item.description" class="output-item__type">{{
              typeLabel(item)
            }}</span></span
          >
          <time :title="formatRunTimestamp(item.created_at)">{{
            formatRunTimestamp(item.created_at, true)
          }}</time>
          <el-icon class="output-chevron"><ArrowRight /></el-icon>
        </summary>
        <RunOutputReader
          :model="readerFor(item.export_item_id)"
          :active="expandedId !== item.export_item_id"
          @expand="expandedId = item.export_item_id"
        />
      </details>
    </div>
    <el-dialog
      v-model="expanded"
      fullscreen
      append-to-body
      destroy-on-close
      :title="expandedItem?.label || expandedItem?.description || t('runs.outputTitle')"
      class="run-output-dialog"
    >
      <RunOutputReader
        v-if="expandedItem"
        :key="expandedItem.export_item_id"
        :model="readerFor(expandedItem.export_item_id)"
        full-screen
      />
    </el-dialog>
  </section>
</template>
<script setup lang="ts">
import { computed, onUnmounted, ref, shallowReactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ArrowRight, Document } from '@element-plus/icons-vue'
import type { ExportItem } from '@/api/runs'
import type { RunResourceState } from '@/composables/useRunWorkbench'
import { formatRunTimestamp } from '@/utils/runDisplay'
import RunOutputReader from './RunOutputReader.vue'
import {
  createRunOutputReader,
  type RunOutputReader as ReadingSession,
} from '@/composables/useRunOutputReader'
const props = defineProps<{ items: ExportItem[]; resource: RunResourceState }>()
defineEmits<{ retry: [] }>()
const { t } = useI18n()
function typeLabel(item: ExportItem) {
  return typeof item.type === 'string' ? item.type.toUpperCase() : t('common.unknown')
}
const readers = shallowReactive(new Map<string, ReadingSession>())
watch(
  () => props.items,
  (items) => {
    const current = new Set(items.map((item) => item.export_item_id))
    for (const [id, reader] of readers)
      if (!current.has(id)) {
        reader.dispose()
        readers.delete(id)
      }
    for (const item of items) {
      const previous = readers.get(item.export_item_id)
      if (previous) previous.update(item)
      else readers.set(item.export_item_id, createRunOutputReader(item))
    }
  },
  { immediate: true, flush: 'sync' }
)
function readerFor(id: string) {
  return readers.get(id)!
}
onUnmounted(() => {
  for (const reader of readers.values()) reader.dispose()
})
const expandedId = ref<string | null>(null)
const expandedItem = computed(() =>
  props.items.find((item) => item.export_item_id === expandedId.value)
)
const expanded = computed({
  get: () => !!expandedItem.value,
  set: (value) => {
    if (!value) expandedId.value = null
  },
})
</script>
<style scoped>
.run-output {
  margin-top: 18px;
  min-width: 0;
}
.output-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.output-heading h3 {
  font-size: 0.875rem;
  font-weight: 600;
  margin: 0;
}
.output-count {
  color: var(--el-text-color-secondary);
  font-size: 0.75rem;
  font-variant-numeric: tabular-nums;
}
.output-empty {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 0;
  color: var(--el-text-color-secondary);
  font-size: 0.8125rem;
}
.output-feedback {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 12px;
  color: var(--el-color-warning);
  font-size: 0.8125rem;
}
.output-items {
  display: grid;
  gap: 16px;
}
.output-item {
  min-width: 0;
  border-radius: var(--run-radius);
  overflow: clip;
  border: 1px solid var(--surface-border);
  background: var(--surface-workspace);
}
.output-item__header {
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr) auto 16px;
  align-items: center;
  gap: 8px;
  padding: 11px 14px;
  background: color-mix(in srgb, var(--surface-inset) 35%, var(--surface-workspace));
  font-size: 0.75rem;
  color: var(--el-text-color-regular);
  cursor: pointer;
}
.output-item__header > span {
  min-width: 0;
  overflow-wrap: anywhere;
}
.output-item__header:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: -2px;
  border-radius: var(--run-radius);
}
.output-item[open] .output-chevron {
  transform: rotate(90deg);
}
.output-item__type {
  display: inline-block;
  white-space: nowrap;
  margin-inline-start: 8px;
  font-size: 0.6875rem;
  color: var(--el-text-color-secondary);
}
.output-item__header time {
  color: var(--el-text-color-secondary);
}
.output-item__header time {
  margin-inline-start: auto;
  font-variant-numeric: tabular-nums;
}
/* The page (or reading dialog) owns vertical scrolling, not individual outputs. */
:global(.run-output-dialog) {
  display: flex;
  flex-direction: column;
  --run-radius: var(--radius-card);
}
:global(.run-output-dialog .el-dialog__header) {
  margin: 0;
  padding: 16px 24px;
  border-bottom: 1px solid var(--surface-border);
}
:global(.run-output-dialog .el-dialog__body) {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0;
}
:global(.run-output-dialog .el-button) {
  border-radius: var(--run-radius);
}
</style>
