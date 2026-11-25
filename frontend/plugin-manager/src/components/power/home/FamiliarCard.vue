<template>
  <!-- The card is a button that opens the familiar; what it asks is answered below it, outside the button. -->
  <div class="familiar" :class="[`familiar--${look}`, { 'familiar--lead': card.leader }]" :data-familiar="card.id" :data-state="look" @click="emit('open', card)">
    <button class="open" type="button" :aria-label="t('power.home.open', { name: shownName })" data-testid="familiar-open">
      <span class="face" aria-hidden="true">
        <span v-if="card.leader" class="crown">👑</span>
        <span class="cat">{{ FAMILIAR_FACES[card.avatar] ?? FAMILIAR_FACES.general }}</span>
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
    <!-- Pressing anywhere on the card opens the familiar; answering it does not. -->
    <div v-if="asked || error" class="answering" @click.stop>
      <AskBox v-if="asked" :ask="asked" :busy="busy ?? ''" class="asked" @answer="emit('answer', $event)" />
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="error" data-testid="familiar-card-error" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { BoardCard } from '@/api/console'
import AskBox from '../AskBox.vue'
import type { GivenAnswer } from '../ask'
import { FAMILIAR_FACES, familiarLook, presetName } from '../powerFormat'

/**
 * `answers`: where the card is shown with the ways to answer what its familiar asks (the board).
 * `busy` and `error` are then how the answer to it fares; whoever shows the card sends it.
 */
const props = defineProps<{ card: BoardCard; answers?: boolean; busy?: string; error?: string }>()
const emit = defineEmits<{ open: [card: BoardCard]; answer: [given: GivenAnswer] }>()
const { t, te } = useI18n()
/** What it asks, where it is answered in the card. */
const asked = computed(() => (props.answers && props.card.commission?.status === 'waiting_user' ? props.card.commission.ask ?? null : null))

const look = computed(() => familiarLook(props.card))
const stateLabel = computed(() => {
  const known = `power.home.states.${look.value}`
  const given = `power.home.states.${props.card.state}`
  if (look.value === 'asleep' && !te(given)) return props.card.state
  return t(known)
})
const kind = computed(() => presetName(props.card, t, te))
const shownName = computed(() => props.card.name || kind.value)
/** The title it earned, or what kind of familiar it is while it has none. */
const shownTitle = computed(() => props.card.title || kind.value)
/** What it is doing now, else what it is there for. */
const line = computed(() => props.card.doing || props.card.duty)
</script>

<style scoped>
.familiar { width: 100%; min-width: 0; cursor: pointer; background: var(--el-fill-color-lighter); border: 1px solid var(--el-border-color-lighter); border-radius: 12px; padding: 10px 12px; }
.familiar:hover, .familiar:focus-within { border-color: var(--el-color-primary); }
.open { display: flex; align-items: flex-start; gap: 10px; width: 100%; min-width: 0; padding: 0; text-align: left; font: inherit; color: inherit; cursor: pointer; background: none; border: 0; border-radius: 8px; }
.open:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 3px; }
.answering { cursor: default; }
.asked { background: var(--el-bg-color); }
.error { margin-top: 8px; }
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
.familiar--compacting .state, .familiar--landing .state, .familiar--resolving .state { color: var(--el-color-primary); }
.familiar--landing, .familiar--resolving { border-color: var(--el-color-primary-light-5); }
.familiar--held, .familiar--unlanded { border-color: var(--el-color-warning); }
.familiar--held .state, .familiar--unlanded .state { color: var(--el-color-warning); font-weight: 700; }
.familiar--idle .face, .familiar--asleep .face, .familiar--resting .face, .familiar--queued .face { opacity: 0.55; }
.familiar--asleep, .familiar--resting, .familiar--queued { border-style: dashed; }
@keyframes bob { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-4px); } }
@media (prefers-reduced-motion: reduce) { .familiar--working .cat { animation: none; } }
</style>
