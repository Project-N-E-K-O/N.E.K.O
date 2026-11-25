<template>
  <div class="unborn" :data-beside="item.id" data-testid="beside-ask">
    <div class="who">
      <span class="face" aria-hidden="true">{{ FAMILIAR_FACES.general }}</span>
      <span class="name">{{ item.member || t('power.home.states.unborn') }}</span>
      <span class="state">{{ t('power.home.states.unborn') }}</span>
    </div>
    <AskBox :ask="item.ask" :busy="busy" @answer="emit('answer', $event)" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="error" data-testid="beside-error" />
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import AskBox from '../AskBox.vue'
import type { GivenAnswer } from '../ask'
import type { BesideAsk } from '../colony/useBesideAsks'
import { FAMILIAR_FACES } from '../powerFormat'

/**
 * The familiar that would be opened for what waits for a busy one, drawn beside that one before
 * it is there: what NekoPower asks of it, answered with a button. Whoever shows it sends the answer.
 */
defineProps<{ item: BesideAsk; busy: string; error: string }>()
const emit = defineEmits<{ answer: [given: GivenAnswer] }>()
const { t } = useI18n()
</script>

<style scoped>
.unborn { padding: 10px 12px; border: 1px dashed var(--el-color-warning); border-radius: 12px; background: var(--el-fill-color-lighter); }
.who { display: flex; align-items: baseline; gap: 8px; font-weight: 600; }
.face { font-size: 22px; opacity: 0.45; filter: grayscale(1); }
.name { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.state { flex: none; font-size: 12px; font-weight: 700; color: var(--el-color-warning); }
.error { margin-top: 8px; }
</style>
