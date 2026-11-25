<template>
  <section
    ref="land"
    class="land"
    :class="{ 'land--loose': territory.loose }"
    :style="{ width: `${size.width}px`, height: `${size.height}px` }"
    :data-territory="territory.key"
  >
    <h2 class="land-name">{{ territory.name }}</h2>
    <!-- Lines are drawn only while a familiar is held: to whoever it has to do with. -->
    <svg v-if="held" class="lines" aria-hidden="true" data-testid="colony-lines">
      <line
        v-for="other in heldLinks"
        :key="other"
        :x1="`${at(held).x * 100}%`" :y1="`${at(held).y * 100}%`"
        :x2="`${at(other).x * 100}%`" :y2="`${at(other).y * 100}%`"
      />
    </svg>
    <!-- Those a leader started for one commission, with the leader: a team, framed together. -->
    <!-- Styled here and not in the stylesheet: what the first page loads of that is held to a budget. -->
    <div
      v-for="team in teams"
      :key="team.id"
      :style="{ ...TEAM_BOX, left: `${team.left * 100}%`, top: `${team.top * 100}%`, width: `${team.width * 100}%`, height: `${team.height * 100}%` }"
      :data-team="team.id"
      aria-hidden="true"
    >
      <span :style="TEAM_NAME">{{ t('power.colony.team') }}</span>
    </div>
    <button
      v-for="member in shown"
      :key="member.id"
      type="button"
      class="cat"
      :class="[`cat--${member.look}`, { 'cat--waits': WAITS.has(member.look), 'cat--lead': member.leader, 'cat--grabbed': held === member.id }]"
      :style="{ left: `${at(member.id).x * 100}%`, top: `${at(member.id).y * 100}%`, ...(member.look === 'unborn' ? UNBORN : {}) }"
      :data-cat="member.id"
      :data-look="member.look"
      :aria-label="member.look === 'unborn' ? t('power.colony.unbornLabel', { name: member.name }) : t('power.colony.catLabel', { name: member.name, state: stateWord(member.look) })"
      :title="member.look === 'unborn' ? t('power.colony.besideTip') : undefined"
      @pointerdown="member.look === 'unborn' || grab(member.id, $event)"
      @pointermove="drag($event)"
      @pointerup="drop($event)"
      @pointercancel="cancel($event)"
      @keydown="member.look === 'unborn' || nudge(member.id, $event)"
      @click="clicked(member.id, $event)"
    >
      <span v-if="bubble(member)" class="bubble" :class="{ 'bubble--loud': member.look === 'waiting_user' }">{{ bubble(member) }}</span>
      <!-- Merging what it changed is drawn as work; one not there yet, faded. -->
      <ColonyFigure
        :avatar="member.avatar" :look="MERGING.has(member.look) ? 'working' : member.look" :leader="member.leader"
        :style="member.look === 'unborn' ? UNBORN_FIGURE : undefined"
      />
      <span class="tag" :style="member.look === 'unborn' ? UNBORN_TAG : undefined">{{ member.name }}</span>
    </button>
    <!-- What one that waits for the user asks, above its head and answered there. Beside the familiar's
         own button, not in it: a press in the card neither picks the familiar up nor opens it. -->
    <div
      v-for="member in asking" :key="`ask:${member.id}`"
      :style="{ ...ASK_SPOT, left: `${at(member.id).x * 100}%`, top: `${at(member.id).y * 100}%` }" :data-ask-of="member.id"
    >
      <AskBubble
        v-if="cardOpen(member.id)"
        :ask="member.ask!" :name="member.name" :busy="answering?.[member.commission ?? ''] ?? ''" :error="failed?.[member.commission ?? ''] ?? ''"
        :below="below(member.id)" :shift="shift(member.id)"
        :style="below(member.id) ? { top: '40px' } : { bottom: member.leader ? '52px' : '44px' }"
        @answer="emit('answer', member.id, $event)" @more="emit('ask', member.id)"
      />
      <!-- Several wait at once: their cards would cover one another, so each is a label until it is pressed. -->
      <button
        v-else type="button" :style="{ ...ASK_LABEL, bottom: member.leader ? '52px' : '44px' }" data-testid="ask-label"
        :aria-label="t('power.colony.askLabel', { name: member.name })" @pointerdown.stop @click.stop="opened = member.id"
      >{{ t('power.colony.waitingBubble') }}</button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { BoardAsk } from '@/api/console'
import type { GivenAnswer } from '../ask'
import type { CatLook } from './activity'
import ColonyFigure from './ColonyFigure.vue'
import { defaultSpots, intoTerritory, inTerritory, keepSpots, keptSpots, territorySize } from './layout'
import type { Spot } from './layout'

// Loaded when the first familiar asks something: what the card is drawn with is not part of the first page.
const AskBubble = defineAsyncComponent(() => import('./AskBubble.vue'))

/** One familiar as the colony draws it. */
export interface ColonyMember {
  id: string
  name: string
  avatar: string
  leader: boolean
  parent: string | null
  look: CatLook
  /** What it said a moment ago; shown above it. */
  said: string
  /** Not there at the moment shown (while what happened is replayed). */
  hidden: boolean
  /** The same for a leader and those it started for the commission it has in hand. */
  team?: string | null
  /** What it asks the user as things stand now, and the commission that asks; not while what happened is replayed. */
  ask?: BoardAsk | null
  commission?: string | null
}

export interface Territory {
  key: string
  name: string
  loose: boolean
  members: ColonyMember[]
}

const props = defineProps<{
  territory: Territory
  /** Whom each familiar has to do with besides the one that started it: whom it waits for, who waits for it. */
  links?: Record<string, string[]>
  /** Whether a familiar that is dropped at `[data-colony-gate]` is sent away. */
  canDismiss?: boolean
  /** Set when the familiars are to be put back where they stand by default. */
  tidy?: number
  /** By the commission that asks: with which button its question is being answered, and why an answer was not taken. */
  answering?: Record<string, string>
  failed?: Record<string, string>
}>()
const emit = defineEmits<{
  open: [id: string]; dismiss: []; holding: [held: boolean]
  /** A question was answered above a familiar's head; or the whole of it is asked for. */
  answer: [id: string, given: GivenAnswer]; ask: [id: string]
}>()
const { t, te } = useI18n()

const land = ref<HTMLElement | null>(null)
const placed = reactive<Record<string, Spot>>({})
// How far each idle familiar has strolled from where it stands.
const stroll = reactive<Record<string, Spot>>({})
const held = ref('')
let moved = false
let start: { x: number; y: number } | null = null
// Where the one that is held was put by the user before it was picked up; null: nowhere.
let from: Spot | null = null
// The pointer it was picked up with: another finger on the screen is no part of it.
let pointer: number | null = null
let timer: ReturnType<typeof setInterval> | null = null
const STROLL_MS = 2800
const STROLL_REACH = 0.05

const shown = computed(() => props.territory.members.filter(member => !member.hidden))
/** Those whose question is shown above them. Not the one that is held: it is being carried about. */
const asking = computed(() => shown.value.filter(member => member.look === 'waiting_user' && member.ask && member.commission && held.value !== member.id))
const size = computed(() => territorySize(props.territory.members.length))
const standing = computed(() => defaultSpots(props.territory.members))

const byId = computed(() => new Map(props.territory.members.map(member => [member.id, member])))

function home(id: string): Spot {
  const own = placed[id]
  if (own) return own
  const spot = standing.value[id] ?? { x: 0.5, y: 0.5 }
  // The one that would be opened beside a busy one stands by it where the user put that one: it is
  // told apart from the others only by where it stands. (Not put anywhere by the user itself.)
  const member = byId.value.get(id)
  const by = member?.look === 'unborn' && member.parent ? placed[member.parent] : undefined
  const was = member?.parent ? standing.value[member.parent] : undefined
  if (!by || !was) return spot
  const { x, y } = { x: spot.x - was.x, y: spot.y - was.y }
  // Where that would be outside, as far on another side of it: brought back to the edge, it would
  // stand on the busy one put there, and cover it. The other way first, then across.
  for (const [dx, dy] of [[x, y], [-x, -y], [y, -x], [-y, x]] as const) {
    const there = { x: by.x + dx, y: by.y + dy }
    if (inTerritory(there)) return there
  }
  return intoTerritory({ x: by.x + x, y: by.y + y })
}
function at(id: string): Spot {
  const base = home(id)
  const off = held.value === id ? undefined : stroll[id]
  return off ? { x: base.x + off.x, y: base.y + off.y } : base
}

// How far a team's frame reaches beyond the middles of those in it, as parts of the territory: a
// familiar is drawn about 92 pixels wide, with its name below it.
const TEAM_PAD_X = 0.09
const TEAM_PAD_Y = 0.16
const TEAM_BOX = {
  position: 'absolute', border: '1.5px dashed var(--el-color-primary-light-5)', borderRadius: '16px',
  background: 'var(--el-color-primary-light-9)', pointerEvents: 'none',
} as const
const TEAM_NAME = {
  position: 'absolute', top: '-9px', left: '12px', padding: '0 6px', fontSize: '11px', lineHeight: '16px',
  color: 'var(--el-color-primary)', background: 'var(--el-bg-color)', borderRadius: '8px', whiteSpace: 'nowrap',
} as const
// The looks of one that waits for the user, drawn alike; and of one whose changes are being merged.
const WAITS = new Set<CatLook>(['waiting_user', 'held', 'unlanded', 'unborn'])
const MERGING = new Set<CatLook>(['landing', 'resolving'])
// One that would be opened, should the user say so: not picked up nor moved with the keys (it stands by
// the busy one) but pressed, faded, its name framed in dashes. What it asks is said when it is pointed at, not above it: it stands close below the one it
// would work beside, whose name a bubble would cover. Styled here and not in the stylesheet, as the teams are.
// Where a question is shown: a point at the middle of the familiar, the card standing on it. Above the others.
const ASK_SPOT = { position: 'absolute', width: '0', height: '0', zIndex: 3 } as const
// How many cards a territory shows at once before they are folded into labels; and how near the top
// (in pixels) a familiar stands for its card to go below it.
const CARDS_AT_ONCE = 2
const CARD_ROOM = 150
// As wide as `AskBubble` draws itself.
const CARD_WIDTH = 216
const ASK_LABEL = {
  position: 'absolute', left: '50%', transform: 'translateX(-50%)', padding: '0 8px', font: 'inherit', fontSize: '11px', fontWeight: '600',
  lineHeight: '1.6', whiteSpace: 'nowrap', cursor: 'pointer', color: '#fff', border: '1px solid var(--el-color-warning)',
  borderRadius: '9px', background: 'var(--el-color-warning)',
} as const
const UNBORN = { cursor: 'pointer' } as const
const UNBORN_FIGURE = { opacity: 0.45, filter: 'grayscale(1)' } as const
const UNBORN_TAG = { borderStyle: 'dashed' } as const
/** The teams of two or more that are there now, each with the box that holds those in it. */
const teams = computed(() => {
  const by = new Map<string, Spot[]>()
  for (const member of shown.value) {
    if (member.team) by.set(member.team, [...(by.get(member.team) ?? []), home(member.id)])
  }
  return [...by.entries()]
    .filter(([, spots]) => spots.length > 1)
    .map(([id, spots]) => {
      const left = Math.max(0, Math.min(...spots.map(spot => spot.x)) - TEAM_PAD_X)
      const top = Math.max(0, Math.min(...spots.map(spot => spot.y)) - TEAM_PAD_Y)
      const right = Math.min(1, Math.max(...spots.map(spot => spot.x)) + TEAM_PAD_X)
      const bottom = Math.min(1, Math.max(...spots.map(spot => spot.y)) + TEAM_PAD_Y)
      return { id, left, top, width: right - left, height: bottom - top }
    })
})

const heldLinks = computed(() => {
  const id = held.value
  if (!id) return []
  const here = new Set(shown.value.map(member => member.id))
  const mine = props.territory.members.find(member => member.id === id)
  const related = new Set<string>(props.links?.[id] ?? [])
  if (mine?.parent) related.add(mine.parent)
  for (const member of props.territory.members) if (member.parent === id) related.add(member.id)
  related.delete(id)
  return [...related].filter(other => here.has(other))
})

// The one whose card is open while several wait; '' for none.
const opened = ref('')
const cardOpen = (id: string) => asking.value.length <= CARDS_AT_ONCE || opened.value === id
const below = (id: string) => at(id).y * size.value.height < CARD_ROOM
/**
 * How far (in pixels) the card of one that stands near the left or right edge is moved inwards, so that
 * all of it is within the territory: what reaches beyond is cut off by what the territories stand in.
 */
function shift(id: string): number {
  const middle = at(id).x * size.value.width
  const half = CARD_WIDTH / 2
  return Math.round(Math.min(Math.max(middle, half), Math.max(half, size.value.width - half)) - middle)
}
function stateWord(look: CatLook): string {
  const key = `power.home.states.${look}`
  return te(key) ? t(key) : look
}
function bubble(member: ColonyMember): string {
  if (member.look !== 'waiting_user') return member.said
  // What it asks is shown above it with the ways to answer; where that is not known, that it waits.
  return member.ask && member.commission ? '' : t('power.colony.waitingBubble')
}

function spotOf(event: PointerEvent): Spot | null {
  const box = land.value?.getBoundingClientRect()
  if (!box || !box.width || !box.height) return null
  return { x: (event.clientX - box.left) / box.width, y: (event.clientY - box.top) / box.height }
}
function overGate(event: PointerEvent): boolean {
  const gate = document.querySelector('[data-colony-gate]')?.getBoundingClientRect()
  return Boolean(
    gate && event.clientX >= gate.left && event.clientX <= gate.right && event.clientY >= gate.top && event.clientY <= gate.bottom,
  )
}

function grab(id: string, event: PointerEvent) {
  if (event.button !== 0) return
  if (held.value && pointer !== null && event.pointerId !== pointer) return
  start = { x: event.clientX, y: event.clientY }
  moved = false
  held.value = id
  from = placed[id] ? { ...placed[id] } : null
  pointer = event.pointerId
  ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
}
function drag(event: PointerEvent) {
  if (!held.value || !start || event.pointerId !== pointer) return
  // A press that barely moves is a click, not a drag.
  if (!moved && Math.hypot(event.clientX - start.x, event.clientY - start.y) < 5) return
  if (!moved) {
    moved = true
    emit('holding', true)
  }
  const spot = spotOf(event)
  if (spot) placed[held.value] = { x: Math.min(1, Math.max(0, spot.x)), y: Math.min(1, Math.max(0, spot.y)) }
}
function drop(event: PointerEvent) {
  const id = held.value
  if (!id || event.pointerId !== pointer) return
  if (moved) {
    if (props.canDismiss && overGate(event)) {
      // Back to where it stood, put there by the user or not: the user may still say no.
      if (from) placed[id] = from
      else delete placed[id]
      emit('dismiss')
    } else {
      // Dropped outside the territory, it stands at its edge.
      placed[id] = intoTerritory(placed[id] ?? home(id))
      delete stroll[id]
    }
    keep()
  }
  letGo()
}
/** The drag is called off (by the browser, or because what was held is not there any more): as if it had not begun. */
function cancel(event?: PointerEvent) {
  const id = held.value
  if (!id || (event && event.pointerId !== pointer)) return
  if (moved) {
    if (from) placed[id] = from
    else delete placed[id]
    // Where another one was put with the keys meanwhile was kept with where this one was
    // dragged to: kept again, without that.
    keep()
  }
  letGo()
}
function letGo() {
  if (moved) {
    emit('holding', false)
    // The click that ends a drag comes before this; where none comes, the next one is not taken for it.
    setTimeout(() => { moved = false }, 0)
  }
  held.value = ''
  start = null
  pointer = null
}
function clicked(id: string, event: MouseEvent) {
  // The click that ends a drag opens nothing.
  if (moved) {
    moved = false
    event.preventDefault()
    return
  }
  emit('open', id)
}
/** Arrow keys move the familiar that has the focus: arranging them does not need a mouse. */
function nudge(id: string, event: KeyboardEvent) {
  const steps: Record<string, Spot> = {
    ArrowLeft: { x: -0.03, y: 0 }, ArrowRight: { x: 0.03, y: 0 }, ArrowUp: { x: 0, y: -0.03 }, ArrowDown: { x: 0, y: 0.03 },
  }
  const step = steps[event.key]
  if (!step) return
  event.preventDefault()
  const from = home(id)
  placed[id] = intoTerritory({ x: from.x + step.x, y: from.y + step.y })
  delete stroll[id]
  keep()
}

function keep() {
  keepSpots(props.territory.key, { ...placed }, props.territory.members.map(member => member.id))
}

function strollOn() {
  if (document.hidden || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
  for (const member of shown.value) {
    if (member.look !== 'idle' || held.value === member.id) {
      delete stroll[member.id]
      continue
    }
    if (Math.random() < 0.5) continue
    const off = { x: (Math.random() * 2 - 1) * STROLL_REACH, y: (Math.random() * 2 - 1) * STROLL_REACH }
    const base = home(member.id)
    // Never out of its territory.
    if (inTerritory({ x: base.x + off.x, y: base.y + off.y })) stroll[member.id] = off
  }
}

watch(() => props.territory.key, key => {
  for (const id of Object.keys(placed)) delete placed[id]
  Object.assign(placed, keptSpots(key))
}, { immediate: true })
watch(() => props.tidy, () => {
  for (const id of Object.keys(placed)) delete placed[id]
  for (const id of Object.keys(stroll)) delete stroll[id]
  keep()
})

onMounted(() => { timer = setInterval(strollOn, STROLL_MS) })
// The one that is held went away (its project was put away elsewhere): the drag is over, and whoever was told of it is told.
watch(shown, members => { if (held.value && !members.some(member => member.id === held.value)) cancel() })
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  cancel()
})
</script>

<style scoped>
.land { position: relative; flex: none; max-width: 100%; border-radius: 50%; border: 1.5px dashed var(--el-border-color); background: var(--el-fill-color-lighter); touch-action: none; user-select: none; }
.land--loose { border-style: dotted; }
.land-name { position: absolute; top: 6px; left: 50%; transform: translateX(-50%); margin: 0; padding: 0 10px; font-size: 13px; font-weight: 600; white-space: nowrap; max-width: 70%; overflow: hidden; text-overflow: ellipsis; color: var(--el-text-color-secondary); background: var(--el-bg-color); border-radius: 10px; }
.lines { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
.lines line { stroke: var(--el-color-primary); stroke-width: 2; stroke-dasharray: 5 5; }
.cat { position: absolute; transform: translate(-50%, -50%); width: 92px; padding: 0; border: 0; background: none; font: inherit; color: inherit; text-align: center; cursor: grab; transition: left 2.4s ease-in-out, top 2.4s ease-in-out; }
.cat--grabbed { cursor: grabbing; transition: none; z-index: 2; }
.cat:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; border-radius: 10px; }
.tag { display: inline-block; max-width: 100%; padding: 0 7px; font-size: 11px; line-height: 1.6; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; border: 1px solid var(--el-border-color-lighter); border-radius: 9px; background: var(--el-bg-color); }
.cat--working .tag { border-color: var(--el-color-success); color: var(--el-color-success); }
.cat--waits .tag { border-color: var(--el-color-warning); color: var(--el-color-warning); }
.bubble { position: absolute; left: 50%; bottom: 100%; transform: translateX(-50%); max-width: 180px; padding: 0 7px; font-size: 11px; line-height: 1.6; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; border: 1px solid var(--el-border-color-lighter); border-radius: 9px; background: var(--el-bg-color); }
.bubble--loud { color: #fff; font-weight: 600; border-color: var(--el-color-warning); background: var(--el-color-warning); }
@media (prefers-reduced-motion: reduce) { .cat { transition: none; } }
</style>
