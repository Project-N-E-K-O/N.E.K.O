<template>
  <span class="figure" :class="[`figure--${look}`, { 'figure--lead': leader }]" aria-hidden="true">{{ face }}</span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { FAMILIAR_FACES } from '../powerFormat'
import type { CatLook } from './activity'

/**
 * What a familiar looks like in the colony.
 *
 * A stand-in until each kind of familiar has art and movement of its own:
 * whatever replaces it takes the same three things (which kind, how it
 * stands, whether it leads) and draws them its own way.
 */
const props = defineProps<{ avatar: string; look: CatLook; leader: boolean }>()
const face = computed(() => FAMILIAR_FACES[props.avatar] ?? FAMILIAR_FACES.general)
</script>

<style scoped>
.figure { display: block; font-size: 34px; line-height: 1.2; filter: drop-shadow(0 3px 2px rgb(0 0 0 / 18%)); }
.figure--lead { font-size: 46px; }
.figure--working { animation: colony-bob 0.9s ease-in-out infinite; }
.figure--idle { opacity: 0.8; }
.figure--asleep, .figure--resting, .figure--queued { opacity: 0.5; }
@keyframes colony-bob { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-5px); } }
@media (prefers-reduced-motion: reduce) { .figure--working { animation: none; } }
</style>
