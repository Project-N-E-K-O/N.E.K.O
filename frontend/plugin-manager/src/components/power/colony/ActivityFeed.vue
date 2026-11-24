<template>
  <section class="feed" data-testid="activity-feed">
    <p v-if="!rows.length" class="muted">{{ loading ? t('power.activity.loading') : t('power.activity.nothing') }}</p>
    <ol v-else ref="list" class="rows">
      <li v-if="left > 0" class="muted">
        <el-button link type="primary" size="small" data-testid="activity-earlier" @click="showEarlier">
          {{ t('power.activity.earlierLeftOut', { count: left }) }}
        </el-button>
      </li>
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
import { clockTime, describeEntry, toldInFeed } from './activity'

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
// So many lines are drawn at first: the latest, or those around the moment shown. Pressing the
// line that says how many are left out draws as many more of those before them.
const SHOWN = 200
const shown = ref(SHOWN)

// Where the entries the list has a line for are (`toldInFeed`). The others are still there to replay, but
// are neither drawn nor counted: the lines drawn are the latest so many of those, however many others lie between.
const told = computed(() => props.entries.flatMap((entry, index) => toldInFeed(entry) ? [index] : []))
const window = computed(() => {
  const total = props.entries.length
  const end = Math.floor(props.current === null ? total : Math.min(total, props.current + 1 + SHOWN / 4))
  const before = told.value.filter(index => index < end)
  return { left: Math.max(0, before.length - shown.value), drawn: before.slice(-shown.value) }
})
const left = computed(() => window.value.left)
const rows = computed(() => window.value.drawn.map(index => {
  const entry = props.entries[index]
  return { entry, index, line: describeEntry(entry, props.nameOf, t, te) }
}))

/** Draw as many more of the entries before those drawn, and stay at the line that was at the top. */
async function showEarlier() {
  const box = list.value
  const before = box ? box.scrollHeight - box.scrollTop : 0
  shown.value += SHOWN
  await nextTick()
  if (box) box.scrollTop = box.scrollHeight - before
}

// What is new comes at the bottom and is scrolled to, unless the user went up to read what came
// before: then the list stays where it is read. While a moment is replayed, that moment is
// scrolled to; back from a moment to now, the list is at its end again, wherever the moment was.
const NEAR_END_PX = 40
watch(() => [props.entries.length, props.current] as const, async ([, current], before) => {
  // Looked at before the new lines are drawn: where the list stood as the user left it.
  const was = list.value
  const atEnd = !was || was.scrollHeight - was.scrollTop - was.clientHeight <= NEAR_END_PX
  const backToNow = current === null && before !== undefined && before[1] !== null
  await nextTick()
  const box = list.value
  if (!box) return
  if (current !== null) box.querySelector('.row.now')?.scrollIntoView?.({ block: 'nearest' })
  else if (atEnd || backToNow) box.scrollTop = box.scrollHeight
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
