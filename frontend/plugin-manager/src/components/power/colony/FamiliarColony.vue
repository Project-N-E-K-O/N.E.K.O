<template>
  <section class="colony" data-testid="familiar-colony">
    <p v-if="error" class="notice notice--warn" role="status" data-testid="board-error">{{ error }}</p>
    <div v-else-if="loaded && !lands.length" class="notice" data-testid="board-empty">
      <strong>{{ t('power.home.empty') }}</strong>
      <span class="muted">{{ t('power.home.emptyHint') }}</span>
    </div>

    <!-- Whoever waits for the user is said first, and answered from here. -->
    <ul v-if="position === null && waiting.length" class="waiting" data-testid="colony-waiting">
      <li v-for="card in waiting" :key="card.id" :data-waiting="card.id">
        <span class="who">{{ nameOfCard(card) }}</span>
        <span class="what">{{ card.commission?.ask ? askSummary(card.commission.ask, t, te) : card.commission?.title }}</span>
        <el-button type="primary" size="small" @click="asking = card.id">{{ t('power.colony.answer') }}</el-button>
      </li>
    </ul>

    <template v-if="lands.length">
      <div class="tools">
        <span class="tally" data-testid="board-tally">
          <span>{{ t('power.home.working', { count: tally.working }) }}</span>
          <span :class="{ loud: tally.waiting > 0 }">{{ t('power.home.waiting', { count: tally.waiting }) }}</span>
        </span>
        <span class="grow" />
        <el-button size="small" data-testid="colony-tidy" @click="tidy += 1">{{ t('power.colony.tidy') }}</el-button>
      </div>

      <div class="field" data-testid="colony-field">
        <ColonyTerritory
          v-for="land in lands"
          :key="land.key"
          :territory="land"
          :links="links"
          :can-dismiss="!land.loose && position === null"
          :tidy="tidy"
          @open="open(land, $event)"
          @dismiss="parting = land.name"
          @holding="onHolding"
        />
        <div class="gate" :class="{ on: holding && position === null }" data-colony-gate data-testid="colony-gate">{{ t('power.colony.gate') }}</div>
      </div>
      <p class="muted hint">{{ t('power.colony.hint') }}</p>
    </template>

    <!-- What happened can be looked at with nobody left as well: those who took part come back while it is replayed. -->
    <!-- Said with nobody there and nothing read as well: nothing to replay is not the same as nothing that could be read. -->
    <p v-if="timelineError" class="muted" role="status" data-testid="timeline-error">{{ timelineError }}</p>
    <template v-if="lands.length || entries.length">
      <div class="replay" data-testid="colony-replay">
        <el-button size="small" :disabled="!entries.length" data-testid="replay-play" @click="togglePlay">
          {{ playing ? t('power.colony.pause') : t('power.colony.replay') }}
        </el-button>
        <input
          type="range" min="0" :max="Math.max(0, entries.length - 1)" :value="position ?? Math.max(0, entries.length - 1)"
          :disabled="entries.length < 2" :aria-label="t('power.colony.moment')" data-testid="replay-slider"
          @input="seek(Number(($event.target as HTMLInputElement).value))"
        >
        <span class="muted moment" data-testid="replay-moment">{{ momentLabel }}</span>
        <el-button v-if="position !== null" size="small" data-testid="replay-now" @click="toNow">{{ t('power.colony.now') }}</el-button>
        <el-button v-else-if="!whole && !dropped && entries.length" size="small" data-testid="replay-whole" @click="fromBeginning">
          {{ t('power.colony.fromBeginning') }}
        </el-button>
      </div>
      <!-- More happened than is held: reading from the beginning would end with the same latest ones, so it is said and not offered. -->
      <p v-if="dropped" class="muted" data-testid="timeline-capped">{{ t('power.activity.onlyLatest', { count: KEPT_ENTRIES }) }}</p>
      <p v-if="gone && position !== null" class="muted" role="status" data-testid="replay-gone">{{ t('power.colony.momentGone') }}</p>

      <h2 class="feed-title">{{ t('power.activity.title') }}</h2>
      <ActivityFeed :entries="entries" :current="position" :name-of="nameOf" :loading="timelineLoading" @pick="seek" />
    </template>

    <!-- Loaded when first needed: what a dialog is drawn with is not part of the first page. -->
    <ColonyDialogs
      v-if="dialogs"
      :asking="askingCard" :asking-name="askingCard ? nameOfCard(askingCard) : ''" :parting="parting" :busy="busy" :error="actionError"
      @answer="answer" @part="part" @close-ask="asking = ''" @close-part="parting = ''"
    />
  </section>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { answerCommission, archiveProject, getWaits } from '@/api/console'
import type { BoardCard } from '@/api/console'
import { askSummary } from '../ask'
import { describeConsoleError, familiarLook, familiarName } from '../powerFormat'
import ActivityFeed from './ActivityFeed.vue'
import { SAID_SECONDS, clockTime, firstSeen, ghostsOf, sceneAt } from './activity'
import ColonyTerritory from './ColonyTerritory.vue'
import type { ColonyMember, Territory } from './ColonyTerritory.vue'
import { useBoard } from './useBoard'
import { KEPT_ENTRIES, useTimeline } from './useTimeline'

const ColonyDialogs = defineAsyncComponent(() => import('./ColonyDialogs.vue'))

/**
 * The familiars as a colony: each project a territory they stand about in,
 * who belongs to whom told by where they stand. What happened among them is
 * listed below and can be replayed.
 */
const { t, te } = useI18n()
const router = useRouter()
const describe = (failure: unknown) => describeConsoleError(failure, t, te)
const { board, loaded, error, load } = useBoard(() => t('power.home.unavailable'), describe)
const {
  entries, loading: timelineLoading, error: timelineError, whole, dropped, fromBeginning,
} = useTimeline(() => null, describe)

// The entry what happened is replayed up to; null: as things stand now.
const position = ref<number | null>(null)
const playing = ref(false)
const tidy = ref(0)
const holding = ref(false)
const links = ref<Record<string, string[]>>({})
let waitsTurn = 0
const asking = ref('')
const parting = ref('')
const busy = ref('')
const actionError = ref('')
// Moves on every few seconds, so that what a familiar said a while ago leaves its head.
const clock = ref(Date.now() / 1000)
let ticking: ReturnType<typeof setInterval> | null = null
let stepping: ReturnType<typeof setInterval> | null = null
const STEP_MS = 700

const cards = computed<BoardCard[]>(() => [
  ...(board.value?.projects ?? []).filter(project => !project.archived).flatMap(project => project.members),
  ...(board.value?.loose ?? []),
])
const byId = computed(() => new Map(cards.value.map(card => [card.id, card])))
const ghosts = computed(() => ghostsOf(entries.value, new Set(byId.value.keys())))
const ghostById = computed(() => new Map(ghosts.value.map(ghost => [ghost.id, ghost])))
const seen = computed(() => firstSeen(entries.value, whole.value))
const scene = computed(() => sceneAt(entries.value, position.value ?? entries.value.length - 1))

const nameOfCard = (card: BoardCard) => familiarName(card, t, te)
function nameOf(sessionId: string | null | undefined): string {
  if (!sessionId) return t('power.activity.someone')
  const card = byId.value.get(sessionId)
  if (card) return nameOfCard(card)
  return ghostById.value.get(sessionId)?.name || t('power.activity.someone')
}

function member(card: BoardCard): ColonyMember {
  const at = scene.value.get(card.id)
  const base = { id: card.id, name: nameOfCard(card), avatar: card.avatar, leader: card.leader, parent: card.parent, team: card.team ?? null }
  if (position.value === null) {
    // As it stands now; what it said lately stays above it for a while.
    const fresh = at && clock.value - at.saidAt <= SAID_SECONDS ? at.said : ''
    return { ...base, look: familiarLook(card), said: fresh, hidden: false }
  }
  // Replayed: as the entries up to the moment leave it. One that comes in later was not
  // there yet; one the entries never name stood by all along, and so does one from the
  // moment it comes in, whatever kind of entry names it first.
  if (at) return { ...base, look: at.look, said: at.said, hidden: false }
  return { ...base, look: 'idle', said: '', hidden: (seen.value.get(card.id) ?? -1) > position.value }
}

const lands = computed<Territory[]>(() => {
  const now = board.value
  if (!now) return []
  const made: Territory[] = (now.projects ?? [])
    .filter(project => !project.archived && project.members.length)
    .map(project => ({ key: `project:${project.name}`, name: project.name, loose: false, members: project.members.map(member) }))
  if (now.loose?.length) made.push({ key: 'loose', name: t('power.home.loose'), loose: true, members: now.loose.map(member) })
  if (position.value === null) return made
  // While what happened is replayed, those who took part and are gone since stand where they did.
  for (const ghost of ghosts.value) {
    const at = scene.value.get(ghost.id)
    if (!at) continue
    const key = ghost.project ? `project:${ghost.project}` : 'loose'
    let land = made.find(item => item.key === key)
    if (!land) {
      land = { key, name: ghost.project || t('power.home.loose'), loose: !ghost.project, members: [] }
      made.push(land)
    }
    land.members.push({
      id: ghost.id, name: ghost.name || t('power.activity.someone'), avatar: ghost.preset || 'worker', leader: false,
      parent: ghost.parent, look: at.look, said: at.said, hidden: false,
    })
  }
  return made
})
const tally = computed(() => ({
  working: cards.value.filter(card => card.state === 'working').length,
  waiting: cards.value.filter(card => card.state === 'waiting_user').length,
}))
const waiting = computed(() => cards.value.filter(card => card.state === 'waiting_user' && card.commission))
const askingCard = computed(() => waiting.value.find(card => card.id === asking.value) ?? null)
// The moment shown is an entry, kept by where it is among those held: when the oldest are let go to
// make room for new ones, it is that many places further to the front.
// Whether the moment that was shown went with them: said until another moment is asked for, since
// what is shown then is another entry, the oldest that is left.
const gone = ref(false)
watch(dropped, (now, before) => {
  if (position.value === null || now <= before) return
  const kept = position.value - (now - before)
  if (kept < 0) gone.value = true
  position.value = Math.max(0, kept)
})
/** Show the moment the user asks for (null: as things stand now). */
function show(index: number | null) {
  gone.value = false
  position.value = index
}
// Whether a dialog was ever asked for: they are loaded then, and stay.
const dialogs = ref(false)
watch([asking, parting], () => { if (asking.value || parting.value) dialogs.value = true })
// Why the last one was not taken is not said of the next.
watch([asking, parting], () => { actionError.value = '' })
const momentLabel = computed(() => {
  if (position.value === null) return t('power.colony.live')
  const entry = entries.value[position.value]
  return entry ? t('power.colony.at', { time: clockTime(entry.at, true) }) : ''
})

function open(land: Territory, id: string) {
  void router.push({ path: '/familiars/space', query: { project: land.loose ? undefined : land.name, familiar: id } })
}

/** Whom each familiar waits for is read when one is picked up: the lines are drawn only then. */
async function onHolding(held: boolean) {
  holding.value = held
  if (!held) return
  // Of two reads the one asked for last holds, whichever answers first.
  const mine = ++waitsTurn
  try {
    const made: Record<string, string[]> = {}
    for (const wait of (await getWaits()).waits) {
      const from = wait.waiter.session_id
      const to = wait.target.type === 'session' ? wait.target.session_id : null
      if (!from || !to) continue
      ;(made[from] ??= []).push(to)
      ;(made[to] ??= []).push(from)
    }
    if (mine === waitsTurn) links.value = made
  } catch {
    // Without them the lines to whom it started and who started it are drawn all the same.
    if (mine === waitsTurn) links.value = {}
  }
}

function stopPlaying() {
  if (stepping) clearInterval(stepping)
  stepping = null
  playing.value = false
}
function seek(index: number) {
  stopPlaying()
  show(Math.min(Math.max(0, index), Math.max(0, entries.value.length - 1)))
}
function toNow() {
  stopPlaying()
  show(null)
}
function togglePlay() {
  if (playing.value) return stopPlaying()
  if (!entries.value.length) return
  // From the beginning, unless a moment before the end is shown already.
  if (position.value === null || position.value >= entries.value.length - 1) show(0)
  playing.value = true
  stepping = setInterval(() => {
    const next = (position.value ?? 0) + 1
    if (next >= entries.value.length) return toNow()
    show(next)
  }, STEP_MS)
}

async function answer(given: { text?: string; choice?: string }) {
  const card = askingCard.value
  const commission = card?.commission
  if (!commission || (!given.choice && !given.text?.trim())) return
  busy.value = given.choice ? `choice:${given.choice}` : 'answer'
  actionError.value = ''
  try {
    await answerCommission(commission.id, { ...given, text: given.text?.trim(), question_id: commission.ask?.id })
    asking.value = ''
  } catch (failure) {
    actionError.value = describe(failure)
  }
  busy.value = ''
  await load()
}

/** Put the project away into the archive: its familiars leave the colony with it. */
async function part() {
  if (!parting.value) return
  busy.value = 'part'
  actionError.value = ''
  try {
    await archiveProject(parting.value)
    parting.value = ''
  } catch (failure) {
    actionError.value = describe(failure)
  }
  busy.value = ''
  await load()
}

onMounted(() => { ticking = setInterval(() => { clock.value = Date.now() / 1000 }, 5000) })
onBeforeUnmount(() => {
  if (ticking) clearInterval(ticking)
  stopPlaying()
})
defineExpose({ load })
</script>

<style scoped>
.colony { display: flex; flex-direction: column; gap: 10px; min-height: 240px; }
.notice { display: flex; flex-direction: column; align-items: center; gap: 6px; margin: 0; padding: 48px 20px; text-align: center; border: 1px dashed var(--el-border-color); border-radius: 16px; }
.notice--warn { padding: 14px 20px; border-style: solid; border-color: var(--el-color-warning); color: var(--el-color-warning); }
.muted { margin: 0; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; }
.waiting { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; }
.waiting li { display: flex; align-items: center; gap: 10px; padding: 8px 12px; border: 1px solid var(--el-color-warning); border-radius: 10px; background: var(--el-color-warning-light-9); }
.who { flex: none; font-weight: 600; }
.what { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tools, .replay { display: flex; align-items: center; gap: 10px; }
.tally { display: flex; gap: 18px; color: var(--el-text-color-secondary); font-size: 13px; }
.tally .loud { color: var(--el-color-warning); font-weight: 600; }
.grow { flex: 1; }
.field { position: relative; display: flex; flex-wrap: wrap; justify-content: center; align-items: center; gap: 18px; padding: 18px 12px; border: 1px solid var(--el-border-color-lighter); border-radius: 16px; background: var(--el-bg-color); overflow: hidden; }
/* Fixed to the window, not to the field: the field can be taller than the window, and a familiar that is held must be able to reach it. */
.gate { position: fixed; z-index: 5; right: 32px; bottom: 28px; padding: 6px 12px; font-size: 12px; color: var(--el-color-danger); border: 1.5px dashed var(--el-color-danger); border-radius: 10px; background: var(--el-bg-color); opacity: 0; pointer-events: none; transition: opacity 0.2s; }
.gate.on { opacity: 1; }
.replay input { flex: 1; min-width: 80px; }
.moment { flex: none; min-width: 70px; }
.feed-title { margin: 6px 0 0; font-size: 14px; }
</style>
