<template>
  <section class="feed" data-testid="activity-feed">
    <p v-if="!rows.length" class="muted">{{ loading ? t('power.activity.loading') : t('power.activity.nothing') }}</p>
    <ol v-else ref="list" class="rows">
      <li v-if="left > 0" class="muted">{{ t('power.activity.earlierLeftOut', { count: left }) }}</li>
      <li
        v-for="row in rows"
        :key="row.entry.cursor"
        class="row"
        :class="{ now: row.index === current, later: current !== null && row.index > current }"
        :data-entry="row.entry.kind"
      >
        <button type="button" class="pick" :title="t('power.activity.showMoment')" @click="emit('pick', row.index)">
          <time>{{ clockTime(row.entry.at) }}</time>
          <span class="line">{{ row.line }}</span>
        </button>
      </li>
    </ol>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { TimelineEntry } from '@/api/console'
import { clockTime, describeEntry } from './activity'

/**
 * What happened among the familiars, a line each, the latest at the bottom.
 * `current` is the entry the colony is replayed up to (null: now); pressing
 * a line asks to be shown that moment.
 */
const props = defineProps<{
  entries: TimelineEntry[]
  current: number | null
  nameOf: (sessionId: string | null | undefined) => string
  loading?: boolean
}>()
const emit = defineEmits<{ pick: [index: number] }>()
const { t, te } = useI18n()
const list = ref<HTMLElement | null>(null)
// So many lines are drawn at most: the latest, or those around the moment shown.
const SHOWN = 200

const window = computed(() => {
  const total = props.entries.length
  const end = props.current === null ? total : Math.min(total, props.current + 1 + SHOWN / 4)
  return { from: Math.max(0, Math.floor(end) - SHOWN), to: Math.floor(end) }
})
const left = computed(() => window.value.from)
const rows = computed(() =>
  props.entries.slice(window.value.from, window.value.to).map((entry, offset) => ({
    entry,
    index: window.value.from + offset,
    line: describeEntry(entry, props.nameOf, t, te),
  })),
)

// What is new comes at the bottom and is scrolled to; while a moment is replayed, that moment is.
watch(() => [props.entries.length, props.current], async () => {
  await nextTick()
  const box = list.value
  if (!box) return
  if (props.current === null) box.scrollTop = box.scrollHeight
  else box.querySelector('.row.now')?.scrollIntoView?.({ block: 'nearest' })
}, { immediate: true })
</script>

<style scoped>
.muted { margin: 0; padding: 6px 0; color: var(--el-text-color-secondary); font-size: 12px; list-style: none; }
.rows { margin: 0; padding: 0; max-height: 240px; overflow: auto; list-style: none; }
.row { border-top: 1px solid var(--el-border-color-lighter); }
.row.now { background: var(--el-color-primary-light-9); }
.row.later { opacity: 0.4; }
.pick { display: flex; gap: 10px; width: 100%; padding: 5px 4px; font: inherit; font-size: 13px; color: inherit; text-align: left; background: none; border: 0; cursor: pointer; }
.pick time { flex: none; width: 44px; color: var(--el-text-color-secondary); font-variant-numeric: tabular-nums; }
.line { min-width: 0; overflow-wrap: anywhere; }
</style>
