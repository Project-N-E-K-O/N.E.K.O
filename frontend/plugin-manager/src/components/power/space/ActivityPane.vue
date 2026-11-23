<template>
  <section class="panel" data-testid="activity-pane">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="block" />
    <p class="muted">{{ t('power.activity.hint') }}</p>
    <ActivityFeed :entries="entries" :current="null" :name-of="nameOf" :loading="loading" @pick="() => {}" />
    <el-button v-if="!whole && !dropped && entries.length" size="small" class="earlier" data-testid="activity-whole" @click="fromBeginning">
      {{ t('power.colony.fromBeginning') }}
    </el-button>
    <!-- More happened than is held: reading from the beginning would end with the same latest ones. -->
    <p v-else-if="dropped" class="muted" data-testid="activity-capped">{{ t('power.activity.onlyLatest', { count: KEPT_ENTRIES }) }}</p>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getProjectBoard } from '@/api/console'
import type { BoardCard } from '@/api/console'
import ActivityFeed from '../colony/ActivityFeed.vue'
import { ghostsOf } from '../colony/activity'
import { KEPT_ENTRIES, useTimeline } from '../colony/useTimeline'
import { describeConsoleError, familiarName } from '../powerFormat'

/** What happened among the familiars of one project, or with `project` null among all of them. */
const props = defineProps<{ project: string | null }>()
const { t, te } = useI18n()
// Of one project all there is, from its beginning: its own history is what is looked for here, and reading
// it took a tenth of a second on a ledger of three hundred thousand events. Of all of them, the last stretch.
const { entries, loading, error, whole, dropped, fromBeginning } = useTimeline(
  () => props.project, failure => describeConsoleError(failure, t, te), () => props.project !== null,
)
const cards = ref<BoardCard[]>([])
let turn = 0

const names = computed(() => {
  const made = new Map(cards.value.map(card => [card.id, familiarName(card, t, te)]))
  // Whoever took part and is not on the board any more is named as the entries name it.
  for (const ghost of ghostsOf(entries.value, new Set(made.keys()))) if (ghost.name) made.set(ghost.id, ghost.name)
  return made
})
const nameOf = (sessionId: string | null | undefined) => names.value.get(sessionId ?? '') || t('power.activity.someone')

/**
 * The names are read with the board, again whenever something new happened: a familiar may have
 * joined. That is with every page that brought something, also once as many entries are held as
 * are kept and their number stays the same: what is watched is made anew each time they change.
 */
async function readNames() {
  const mine = ++turn
  try {
    const board = await getProjectBoard(props.project)
    if (mine !== turn) return
    cards.value = props.project === null
      ? [...(board.projects ?? []).flatMap(project => project.members), ...(board.loose ?? [])]
      : (board.projects ?? []).find(project => project.name === props.project)?.members ?? []
  } catch {
    // Named as the entries name them.
  }
}
watch(() => [props.project, entries.value.length], () => void readNames(), { immediate: true })
</script>

<style scoped>
.panel { background: var(--el-bg-color); border: 1px solid var(--surface-border, var(--el-border-color-lighter)); border-radius: var(--radius-card, 10px); padding: 18px 20px; }
.muted { margin: 0 0 8px; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
.block { margin-bottom: 12px; }
.earlier { margin-top: 10px; }
</style>
