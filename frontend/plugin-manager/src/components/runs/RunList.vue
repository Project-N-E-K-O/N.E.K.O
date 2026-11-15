<template>
  <div
    ref="listElement"
    class="run-list"
    data-yui-guide-id="runs-table"
    :aria-label="t('runs.title')"
  >
    <div class="run-list__columns" aria-hidden="true">
      <span>{{ t('runs.status') }}</span
      ><span>{{ t('runs.entryId') }}</span
      ><span>{{ t('runs.updatedAt') }}</span>
    </div>
    <button
      v-for="(run, index) in runs"
      :key="run.run_id"
      type="button"
      class="run-row"
      :data-run-id="run.run_id"
      :aria-pressed="run.run_id === selectedId"
      :tabindex="hasSelectionInList ? (run.run_id === selectedId ? 0 : -1) : index === 0 ? 0 : -1"
      aria-controls="run-detail-panel"
      @click="$emit('select', run.run_id)"
      @keydown="navigate($event, run.run_id)"
    >
      <RunStatusBadge :status="run.status" />
      <span class="run-row__main">
        <span class="run-row__name" :title="run.entry_id">{{ run.entry_id }}</span>
        <span class="run-row__identity" :title="`${run.plugin_id} · ${run.run_id}`"
          >{{ run.plugin_id }} · #{{ run.run_id.slice(-6) }}</span
        >
      </span>
      <time class="run-row__time" :title="formatRunTimestamp(run.updated_at)">{{
        formatRunTimestamp(run.updated_at, true)
      }}</time>
    </button>
  </div>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { RunRecord } from '@/api/runs'
import { formatRunTimestamp } from '@/utils/runDisplay'
import RunStatusBadge from './RunStatusBadge.vue'
const props = defineProps<{ runs: RunRecord[]; selectedId: string | null }>()
defineEmits<{ select: [id: string] }>()
const { t } = useI18n()
const listElement = ref<HTMLElement>()
const hasSelectionInList = computed(() => props.runs.some((run) => run.run_id === props.selectedId))
function buttons() {
  return Array.from(
    listElement.value?.querySelectorAll<HTMLButtonElement>('button[data-run-id]') ?? []
  )
}
function navigate(event: KeyboardEvent, id: string) {
  if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const entries = buttons()
  const index = props.runs.findIndex((run) => run.run_id === id)
  const next =
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? entries.length - 1
        : Math.max(0, Math.min(entries.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)))
  entries[next]?.focus()
}
defineExpose({
  focusSelected() {
    const button =
      buttons().find((button) => button.dataset.runId === props.selectedId) ?? buttons()[0]
    button?.focus({ preventScroll: true })
    button?.scrollIntoView?.({ block: 'nearest' })
  },
})
</script>
<style scoped>
.run-list {
  display: grid;
  gap: 2px;
  padding: 0 8px 8px;
}
.run-list__columns,
.run-row {
  display: grid;
  grid-template-columns: minmax(52px, 0.7fr) minmax(0, 2fr) 90px;
  align-items: center;
  gap: 8px;
  padding-inline: 12px;
}
.run-list__columns {
  position: sticky;
  top: 0;
  z-index: 1;
  min-height: 36px;
  background: var(--surface-workspace);
  color: var(--el-text-color-secondary);
  font-size: 0.75rem;
  border-bottom: 1px solid var(--surface-border);
}
.run-list__columns span:last-child {
  text-align: end;
}
.run-row {
  width: 100%;
  min-height: 58px;
  padding-block: 9px;
  border: 0;
  border-radius: var(--run-radius);
  background: transparent;
  color: var(--el-text-color-primary);
  font: inherit;
  text-align: start;
  cursor: pointer;
}
.run-row:hover {
  background: var(--surface-inset);
}
.run-row[aria-pressed='true'] {
  background: color-mix(in srgb, var(--el-color-primary) 9%, var(--surface-workspace));
  box-shadow: inset 2px 0 var(--el-color-primary);
}
.run-row:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: -2px;
}
.run-row__main {
  min-width: 0;
  display: grid;
  gap: 3px;
}
.run-row__name {
  font-size: 0.8125rem;
  font-weight: 600;
}
.run-row__identity {
  font-size: 0.75rem;
  color: var(--el-text-color-secondary);
}
.run-row__name,
.run-row__identity {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.run-row__time {
  font-size: 0.75rem;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
  text-align: end;
}
@container run-workbench (max-width: 380px) {
  .run-list__columns,
  .run-row {
    grid-template-columns: minmax(44px, 0.6fr) minmax(0, 2fr) 66px;
    gap: 6px;
    padding-inline: 8px;
  }
}
</style>
