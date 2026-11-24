<template>
  <el-dialog :model-value="Boolean(asking)" :title="askingName" width="600px" @update:model-value="(open: boolean) => { if (!open) emit('closeAsk') }">
    <p v-if="asking?.commission" class="muted">{{ asking.commission.title }}</p>
    <AskBox v-if="asking?.commission?.ask" :ask="asking.commission.ask" :busy="busy" @answer="emit('answer', $event)" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon data-testid="colony-ask-error" />
  </el-dialog>

  <el-dialog
    :model-value="Boolean(parting)" :title="t('power.colony.partTitle', { project: parting })" width="480px"
    @update:model-value="(open: boolean) => { if (!open) emit('closePart') }"
  >
    <p>{{ t('power.colony.partText') }}</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon data-testid="colony-part-error" />
    <template #footer>
      <el-button @click="emit('closePart')">{{ t('common.cancel') }}</el-button>
      <el-button type="danger" :loading="busy === 'part'" data-testid="colony-part-confirm" @click="emit('part')">{{ t('power.colony.part') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { BoardCard } from '@/api/console'
import AskBox from '../AskBox.vue'

/**
 * The two things the colony asks the user in a dialog: an answer for a
 * familiar that waits, and whether a project is really to be sent away.
 * Loaded when the first of them is needed: the colony is the first page,
 * and what a dialog is drawn with is not part of it.
 */
defineProps<{ asking: BoardCard | null; askingName: string; parting: string; busy: string; error: string }>()
const emit = defineEmits<{
  answer: [given: { text?: string; choice?: string }]; part: []; closeAsk: []; closePart: []
}>()
const { t } = useI18n()
</script>

<style scoped>
.muted { margin: 0; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; }
</style>
