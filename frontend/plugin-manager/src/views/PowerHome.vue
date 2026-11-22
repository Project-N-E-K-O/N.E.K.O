<template>
  <div class="power-home">
    <PowerBar @changed="board?.load()" />
    <div class="actions">
      <button type="button" class="create" data-testid="familiar-create" @click="openCreate">＋ {{ t('power.home.create') }}</button>
    </div>
    <FamiliarBoard ref="board" />
    <!-- Loaded when first asked for: the form and what it is drawn with are not part of the first page. -->
    <NewFamiliarDialog v-if="asked" v-model="creating" @made="made" />
  </div>
</template>

<script setup lang="ts">
import { defineAsyncComponent, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import FamiliarBoard from '@/components/power/home/FamiliarBoard.vue'
import PowerBar from '@/components/power/home/PowerBar.vue'

const NewFamiliarDialog = defineAsyncComponent(() => import('@/components/power/home/NewFamiliarDialog.vue'))
const { t } = useI18n()
const router = useRouter()
const board = ref<InstanceType<typeof FamiliarBoard> | null>(null)
const asked = ref(false)
const creating = ref(false)

function openCreate() {
  asked.value = true
  creating.value = true
}

/** A familiar was made: its own page in the familiar space is where the user goes on from. */
function made(project: string, sessionId: string) {
  void board.value?.load()
  void router.push({ path: '/familiars/space', query: { project, familiar: sessionId } })
}
</script>

<style scoped>
.power-home { max-width: 1400px; margin: 0 auto; padding: var(--spacing-lg, 24px); display: flex; flex-direction: column; gap: 14px; }
.actions { display: flex; justify-content: flex-end; }
.create { font: inherit; font-weight: 600; color: #fff; background: var(--el-color-primary); border: 0; border-radius: 8px; padding: 8px 16px; cursor: pointer; }
.create:hover { background: var(--el-color-primary-light-3); }
.create:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
</style>
