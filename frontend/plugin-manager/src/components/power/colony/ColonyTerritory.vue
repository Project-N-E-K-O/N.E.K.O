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
    <button
      v-for="member in shown"
      :key="member.id"
      type="button"
      class="cat"
      :class="[`cat--${member.look}`, { 'cat--lead': member.leader, 'cat--held': held === member.id }]"
      :style="{ left: `${at(member.id).x * 100}%`, top: `${at(member.id).y * 100}%` }"
      :data-cat="member.id"
      :data-look="member.look"
      :aria-label="t('power.colony.catLabel', { name: member.name, state: stateWord(member.look) })"
      @pointerdown="grab(member.id, $event)"
      @pointermove="drag($event)"
      @pointerup="drop($event)"
      @pointercancel="letGo()"
      @keydown="nudge(member.id, $event)"
      @click="clicked(member.id, $event)"
    >
      <span v-if="bubble(member)" class="bubble" :class="{ 'bubble--loud': member.look === 'waiting_user' }">{{ bubble(member) }}</span>
      <ColonyFigure :avatar="member.avatar" :look="member.look" :leader="member.leader" />
      <span class="tag">{{ member.name }}</span>
    </button>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { CatLook } from './activity'
import ColonyFigure from './ColonyFigure.vue'
import { defaultSpots, intoTerritory, inTerritory, keepSpots, keptSpots, territorySize } from './layout'
import type { Spot } from './layout'

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
}>()
const emit = defineEmits<{ open: [id: string]; dismiss: []; holding: [held: boolean] }>()
const { t, te } = useI18n()

const land = ref<HTMLElement | null>(null)
const placed = reactive<Record<string, Spot>>({})
// How far each idle familiar has strolled from where it stands.
const stroll = reactive<Record<string, Spot>>({})
const held = ref('')
let moved = false
let start: { x: number; y: number } | null = null
let timer: ReturnType<typeof setInterval> | null = null
const STROLL_MS = 2800
const STROLL_REACH = 0.05

const shown = computed(() => props.territory.members.filter(member => !member.hidden))
const size = computed(() => territorySize(props.territory.members.length))
const standing = computed(() => defaultSpots(props.territory.members))

function home(id: string): Spot {
  return placed[id] ?? standing.value[id] ?? { x: 0.5, y: 0.5 }
}
function at(id: string): Spot {
  const base = home(id)
  const off = held.value === id ? undefined : stroll[id]
  return off ? { x: base.x + off.x, y: base.y + off.y } : base
}

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

function stateWord(look: CatLook): string {
  const key = `power.home.states.${look}`
  return te(key) ? t(key) : look
}
function bubble(member: ColonyMember): string {
  return member.look === 'waiting_user' ? t('power.colony.waitingBubble') : member.said
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
  start = { x: event.clientX, y: event.clientY }
  moved = false
  held.value = id
  ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
}
function drag(event: PointerEvent) {
  if (!held.value || !start) return
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
  if (!id) return
  if (moved) {
    if (props.canDismiss && overGate(event)) {
      delete placed[id]
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
function letGo() {
  if (moved) {
    emit('holding', false)
    // The click that ends a drag comes before this; where none comes, the next one is not taken for it.
    setTimeout(() => { moved = false }, 0)
  }
  held.value = ''
  start = null
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
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
.land { position: relative; flex: none; max-width: 100%; border-radius: 50%; border: 1.5px dashed var(--el-border-color); background: var(--el-fill-color-lighter); touch-action: none; user-select: none; }
.land--loose { border-style: dotted; }
.land-name { position: absolute; top: 6px; left: 50%; transform: translateX(-50%); margin: 0; padding: 0 10px; font-size: 13px; font-weight: 600; white-space: nowrap; max-width: 70%; overflow: hidden; text-overflow: ellipsis; color: var(--el-text-color-secondary); background: var(--el-bg-color); border-radius: 10px; }
.lines { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
.lines line { stroke: var(--el-color-primary); stroke-width: 2; stroke-dasharray: 5 5; }
.cat { position: absolute; transform: translate(-50%, -50%); width: 92px; padding: 0; border: 0; background: none; font: inherit; color: inherit; text-align: center; cursor: grab; transition: left 2.4s ease-in-out, top 2.4s ease-in-out; }
.cat--held { cursor: grabbing; transition: none; z-index: 2; }
.cat:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; border-radius: 10px; }
.tag { display: inline-block; max-width: 100%; padding: 0 7px; font-size: 11px; line-height: 1.6; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; border: 1px solid var(--el-border-color-lighter); border-radius: 9px; background: var(--el-bg-color); }
.cat--working .tag { border-color: var(--el-color-success); color: var(--el-color-success); }
.cat--waiting_user .tag { border-color: var(--el-color-warning); color: var(--el-color-warning); }
.bubble { position: absolute; left: 50%; bottom: 100%; transform: translateX(-50%); max-width: 180px; padding: 0 7px; font-size: 11px; line-height: 1.6; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; border: 1px solid var(--el-border-color-lighter); border-radius: 9px; background: var(--el-bg-color); }
.bubble--loud { color: #fff; font-weight: 600; border-color: var(--el-color-warning); background: var(--el-color-warning); }
@media (prefers-reduced-motion: reduce) { .cat { transition: none; } }
</style>
