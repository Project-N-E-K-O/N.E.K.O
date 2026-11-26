<template>
  <div class="power-home">
    <PowerBar @changed="board?.load()" />
    <div class="actions">
      <div class="views" role="group" :aria-label="t('power.home.view')">
        <button
          v-for="name in VIEWS" :key="name" type="button" class="view" :class="{ on: view === name }"
          :aria-pressed="view === name" :data-view="name" @click="show(name)"
        >{{ t(`power.home.views.${name}`) }}</button>
      </div>
      <button type="button" class="create" data-testid="familiar-create" @click="openCreate">＋ {{ t('power.home.create') }}</button>
    </div>
    <FamiliarColony v-if="view === 'colony'" ref="board" />
    <!-- The cards are loaded when they are first asked for. -->
    <FamiliarBoard v-else ref="board" />
    <!-- Loaded when first asked for: the form and what it is drawn with are not part of the first page. -->
    <NewFamiliarDialog v-if="asked" v-model="creating" @made="made" />
  </div>
</template>

<script setup lang="ts">
import { defineAsyncComponent, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import FamiliarColony from '@/components/power/colony/FamiliarColony.vue'
import PowerBar from '@/components/power/home/PowerBar.vue'

const FamiliarBoard = defineAsyncComponent(() => import('@/components/power/home/FamiliarBoard.vue'))
const NewFamiliarDialog = defineAsyncComponent(() => import('@/components/power/home/NewFamiliarDialog.vue'))
const VIEWS = ['colony', 'list'] as const
type View = (typeof VIEWS)[number]
const VIEW_KEY = 'neko-power-home-view'
const { t } = useI18n()
const router = useRouter()
// Whichever of the two is shown: both can be asked to read the board anew.
const board = ref<{ load: () => unknown } | null>(null)
// The colony unless the user chose the list; kept in this browser.
const view = ref<View>(kept())

function kept(): View {
  try {
    return localStorage.getItem(VIEW_KEY) === 'list' ? 'list' : 'colony'
  } catch {
    return 'colony'
  }
}
function show(name: View) {
  view.value = name
  try {
    localStorage.setItem(VIEW_KEY, name)
  } catch {
    // Storage that is switched off: the choice holds until the page is left.
  }
}
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
.actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.views { display: flex; border: 1px solid var(--el-border-color); border-radius: 8px; overflow: hidden; }
.view { font: inherit; font-size: 13px; color: var(--el-text-color-regular); background: var(--el-bg-color); border: 0; padding: 5px 14px; cursor: pointer; }
.view.on { color: #fff; background: var(--el-color-primary); }
.create { font: inherit; font-weight: 600; color: #fff; background: var(--el-color-primary); border: 0; border-radius: 8px; padding: 8px 16px; cursor: pointer; }
.create:hover { background: var(--el-color-primary-light-3); }
.create:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
</style>
