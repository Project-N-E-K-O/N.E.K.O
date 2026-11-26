<template>
  <section class="board" data-testid="familiar-board">
    <div v-if="hives.length" class="tally" data-testid="board-tally">
      <span>{{ t('power.home.working', { count: tally.working }) }}</span>
      <span :class="{ loud: tally.waiting > 0 }">{{ t('power.home.waiting', { count: tally.waiting }) }}</span>
    </div>
    <!-- Of an answer whose added sentence was not taken, where no card of a familiar is there to say it on. -->
    <p v-if="lostAbove" class="notice notice--warn" role="status" data-testid="board-answer-note">{{ lostAbove }}</p>
    <p v-if="error" class="notice notice--warn" role="status" data-testid="board-error">{{ error }}</p>
    <div v-else-if="loaded && !hives.length" class="notice" data-testid="board-empty">
      <strong>{{ t('power.home.empty') }}</strong>
      <span class="muted">{{ t('power.home.emptyHint') }}</span>
    </div>

    <div class="hives">
      <article v-for="hive in hives" :key="hive.key" class="hive" :data-hive="hive.key">
        <header>
          <h2>{{ hive.name }}</h2>
          <p class="muted">
            {{ hive.loose ? t('power.home.looseHint') : hive.workdir || t('power.home.defaultWorkdir') }}
            <template v-if="!hive.loose && hive.doneToday"> · {{ t('power.home.doneToday', { count: hive.doneToday }) }}</template>
          </p>
        </header>
        <div class="members">
          <template v-for="card in hive.members" :key="card.id">
            <FamiliarCard
              :card="card" :class="{ sub: hive.led && !card.leader }" answers
              :busy="card.commission ? busyOf(card.commission) : ''" :error="lost[card.id] || (card.commission ? failedOf(card.commission) : '')"
              @open="open(hive, $event)" @answer="answerCard(card, $event)"
            />
            <!-- Whether another is to do what waits for this one, busy with something else: drawn beside it, not there yet. -->
            <BesideAsk
              v-for="item in besidesOf(hive, card.id)" :key="item.id"
              :item="item" :busy="busyOf(item)" :error="failedOf(item)" @answer="answer(item, $event)"
            />
          </template>
          <BesideAsk
            v-for="item in besidesOf(hive, null)" :key="item.id"
            :item="item" :busy="busyOf(item)" :error="failedOf(item)" @answer="answer(item, $event)"
          />
        </div>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { consoleErrorCode, getBoard } from '@/api/console'
import type { Board, BoardCard } from '@/api/console'
import { rememberAsks } from '../ask'
import type { GivenAnswer } from '../ask'
import { useBesideAnswers, useBesideAsks } from '../colony/useBesideAsks'
import type { BesideAsk as Beside } from '../colony/useBesideAsks'
import { describeConsoleError } from '../powerFormat'
import BesideAsk from './BesideAsk.vue'
import FamiliarCard from './FamiliarCard.vue'

// `led`: the project has a leader, and the others of it are drawn as under it. In a project with none, none is.
type Hive = { key: string; name: string; loose: boolean; led: boolean; workdir: string | null; doneToday: number; members: BoardCard[] }

const { t, te } = useI18n()
const router = useRouter()
const board = ref<Board | null>(null)
const loaded = ref(false)
const error = ref('')
rememberAsks()
// How often the board is asked for, in milliseconds: it answers "unchanged"
// in a few milliseconds when nothing moved. Slower once it could not be read.
const POLL_MS = 2000
const POLL_FAILED_MS = 15000
let timer: ReturnType<typeof setTimeout> | null = null
let alive = true
let turn = 0
const { asks: besides, load: loadBesides } = useBesideAsks(board)
// That a sentence added to an answer was not taken, by the familiar that was answered: its commission may be
// done and gone from its card by the time that is known, and the card is still there to say it on.
const lost = ref<Record<string, string>>({})
const answeredFor = new Map<string, string>()
// The same where the answer was to whether another familiar is to be opened (which is of no familiar's card), or
// the familiar is not on the board any more: said above the board.
const lostElsewhere = ref('')
// Said for a while, not for good: the familiar goes on with its work, and nothing takes the words away otherwise.
const LOST_NOTE_MS = 15000
const withoutCard = (cardId: string) => Object.fromEntries(Object.entries(lost.value).filter(([id]) => id !== cardId))
// When the words of each place are taken away again, by the card ('' for above the board). Words put anew have
// their own time: what was to take the last ones away does not take these (they may read the same).
const fading = new Map<string, ReturnType<typeof setTimeout>>()
function sayLost(cardId: string, said: string) {
  const before = fading.get(cardId)
  if (before) clearTimeout(before)
  fading.delete(cardId)
  if (cardId) lost.value = said ? { ...lost.value, [cardId]: said } : withoutCard(cardId)
  else lostElsewhere.value = said
  if (said) fading.set(cardId, setTimeout(() => sayLost(cardId, ''), LOST_NOTE_MS))
}
const { busyOf, failedOf, answer: answerOne } = useBesideAnswers(() => Promise.all([load(), loadBesides()]), (said, commissionId) => {
  sayLost(answeredFor.get(commissionId) ?? '', said)
})
function answer(item: Beside, given: GivenAnswer) {
  sayLost('', '')
  void answerOne(item, given)
}
function answerCard(card: BoardCard, given: GivenAnswer) {
  if (!card.commission) return
  answeredFor.set(card.commission.id, card.id)
  // What was said of its last answer is not about this one.
  sayLost(card.id, '')
  void answerOne({ id: card.commission.id, ask: card.commission.ask }, given)
}

// What is said above the board: also of a familiar that has no card on it any more by the time the board is
// read again (it was let go, or its project put away), where its card cannot say it.
const lostAbove = computed(() => {
  if (lostElsewhere.value) return lostElsewhere.value
  const shown = new Set(hives.value.flatMap(hive => hive.members.map(card => card.id)))
  return Object.entries(lost.value).find(([id]) => !shown.has(id))?.[1] ?? ''
})

const hives = computed<Hive[]>(() => {
  const now = board.value
  if (!now) return []
  const made: Hive[] = (now.projects ?? [])
    .filter(project => !project.archived && project.members.length)
    .map(project => ({
      key: `project:${project.name}`, name: project.name, loose: false, led: project.members.some(card => card.leader),
      workdir: project.workdir,
      doneToday: project.done_today, members: project.members,
    }))
  if (now.loose?.length) {
    made.push({ key: 'loose', name: t('power.home.loose'), loose: true, led: false, workdir: null, doneToday: 0, members: now.loose })
  }
  return made
})
const tally = computed(() => {
  const all = hives.value.flatMap(hive => hive.members)
  return {
    working: all.filter(card => card.state === 'working').length,
    waiting: all.filter(card => card.state === 'waiting_user').length + besides.value.length,
  }
})

/** What is asked of opening another beside the one named; with none named, beside one that is not on the board. */
function besidesOf(hive: Hive, sessionId: string | null): Beside[] {
  return besides.value.filter(item => hive.key === `project:${item.project}`
    && (sessionId === null ? !hive.members.some(card => card.id === item.beside) : item.beside === sessionId))
}

function open(hive: Hive, card: BoardCard) {
  void router.push({
    path: '/familiars/space',
    query: { project: hive.loose ? undefined : hive.name, familiar: card.id },
  })
}

async function load() {
  const mine = ++turn
  if (timer) clearTimeout(timer)
  timer = null
  let wait = POLL_MS
  try {
    const answer = await getBoard(board.value?.revision)
    if (mine !== turn) return
    // "Unchanged" carries nothing else: what is shown stays.
    if (!answer.unchanged) board.value = answer
    error.value = ''
  } catch (failure) {
    if (mine !== turn) return
    // A service from before the board was added has no such route.
    const missing = (failure as { response?: { status?: number } })?.response?.status === 404 && !consoleErrorCode(failure)
    error.value = missing ? t('power.home.unavailable') : describeConsoleError(failure, t, te)
    wait = POLL_FAILED_MS
  } finally {
    if (mine === turn) {
      loaded.value = true
      // Not asked for while nobody looks at the page; asked again when they do.
      if (alive && !document.hidden) timer = setTimeout(() => void load(), wait)
    }
  }
}

function onVisibility() {
  if (!document.hidden && alive) void load()
}

onMounted(() => {
  document.addEventListener('visibilitychange', onVisibility)
  void load()
})
onBeforeUnmount(() => {
  alive = false
  fading.forEach(timer => clearTimeout(timer))
  document.removeEventListener('visibilitychange', onVisibility)
  if (timer) clearTimeout(timer)
})
defineExpose({ load })
</script>

<style scoped>
.board { display: flex; flex-direction: column; gap: 14px; min-height: 240px; }
.tally { display: flex; gap: 18px; color: var(--el-text-color-secondary); font-size: 13px; padding: 0 4px; }
.tally .loud { color: var(--el-color-warning); font-weight: 600; }
.notice { display: flex; flex-direction: column; align-items: center; gap: 6px; margin: 0; padding: 48px 20px; text-align: center; border: 1px dashed var(--el-border-color); border-radius: 16px; color: var(--el-text-color-regular); }
.notice--warn { padding: 14px 20px; border-style: solid; border-color: var(--el-color-warning); color: var(--el-color-warning); }
.hives { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr)); gap: 16px; align-items: start; }
.hive { background: var(--el-bg-color); border: 1px solid var(--surface-border, var(--el-border-color-lighter)); border-radius: 16px; padding: 16px; min-width: 0; }
.hive h2 { margin: 0 0 2px; font-size: 16px; overflow-wrap: anywhere; }
.muted { margin: 0; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; }
.members { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; }
.members .sub { margin-left: 22px; width: calc(100% - 22px); }
</style>
