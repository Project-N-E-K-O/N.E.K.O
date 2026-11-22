<template>
  <button
    class="familiar"
    :class="[`familiar--${look}`, { 'familiar--lead': card.leader }]"
    :data-familiar="card.id"
    :data-state="look"
    :aria-label="t('power.home.open', { name: shownName })"
    @click="emit('open', card)"
  >
    <span class="face" aria-hidden="true">
      <span v-if="card.leader" class="crown">👑</span>
      <span class="cat">{{ FACES[card.avatar] ?? FACES.general }}</span>
    </span>
    <span class="words">
      <span class="name">
        {{ shownName }}
        <span class="state">{{ stateLabel }}</span>
      </span>
      <span class="title">{{ shownTitle }}</span>
      <span v-if="line" class="line">{{ line }}</span>
    </span>
  </button>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { BoardCard } from '@/api/console'

const props = defineProps<{ card: BoardCard }>()
const emit = defineEmits<{ open: [card: BoardCard] }>()
const { t, te } = useI18n()

// Stand-ins until there is art for each kind of familiar: one face per avatar key.
const FACES: Record<string, string> = {
  boss: '😼', worker: '🐱', scout: '🙀', inspector: '😾', planner: '😺', general: '🐈', shadow: '🐈‍⬛',
}

/**
 * How it is drawn. A familiar that is asleep with something in hand is
 * resting until it is woken, or waiting its turn: told apart by what it has
 * in hand. A state the page does not know is drawn as asleep and named as it came.
 */
const look = computed(() => {
  const { state, commission } = props.card
  if (state === 'asleep' && commission && (commission.status === 'resting' || commission.status === 'queued')) {
    return commission.status
  }
  return ['working', 'waiting_user', 'idle', 'asleep', 'compacting'].includes(state) ? state : 'asleep'
})
const stateLabel = computed(() => {
  const known = `power.home.states.${look.value}`
  const given = `power.home.states.${props.card.state}`
  if (look.value === 'asleep' && !te(given)) return props.card.state
  return t(known)
})
const presetName = computed(() => {
  const key = `power.home.presets.${props.card.preset}`
  // A template of the user's names itself; one without a name is shown by its key.
  return te(key) ? t(key) : props.card.label || props.card.preset
})
const shownName = computed(() => props.card.name || presetName.value)
/** The title it earned, or what kind of familiar it is while it has none. */
const shownTitle = computed(() => props.card.title || presetName.value)
/** What it is doing now, else what it is there for. */
const line = computed(() => props.card.doing || props.card.duty)
</script>

<style scoped>
.familiar { display: flex; align-items: flex-start; gap: 10px; width: 100%; text-align: left; font: inherit; color: inherit; cursor: pointer; background: var(--el-fill-color-lighter); border: 1px solid var(--el-border-color-lighter); border-radius: 12px; padding: 10px 12px; min-width: 0; }
.familiar:hover, .familiar:focus-visible { border-color: var(--el-color-primary); outline: none; }
.face { position: relative; flex: none; width: 40px; text-align: center; font-size: 28px; line-height: 1.2; }
.crown { position: absolute; top: -12px; left: 50%; transform: translateX(-50%); font-size: 14px; }
.words { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
.name { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; font-weight: 600; }
.state { flex: none; font-size: 12px; font-weight: 500; color: var(--el-text-color-secondary); }
.title { font-size: 12px; color: var(--el-text-color-secondary); }
.line { font-size: 12px; line-height: 1.5; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow-wrap: anywhere; }
.familiar--working { border-color: var(--el-color-success); }
.familiar--working .state { color: var(--el-color-success); }
.familiar--working .cat { display: inline-block; animation: bob 1s ease-in-out infinite; }
.familiar--waiting_user { border-color: var(--el-color-warning); background: var(--el-color-warning-light-9); }
.familiar--waiting_user .state { color: var(--el-color-warning); font-weight: 700; }
.familiar--compacting .state { color: var(--el-color-primary); }
.familiar--idle .face, .familiar--asleep .face, .familiar--resting .face, .familiar--queued .face { opacity: 0.55; }
.familiar--asleep, .familiar--resting, .familiar--queued { border-style: dashed; }
@keyframes bob { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-4px); } }
@media (prefers-reduced-motion: reduce) { .familiar--working .cat { animation: none; } }
</style>
