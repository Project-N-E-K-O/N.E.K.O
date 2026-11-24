<template>
  <section v-if="waiting.length || error" class="trees" data-testid="project-trees">
    <h4>{{ t('power.trees.waiting', { count: waiting.length }) }}</h4>
    <p class="muted">{{ t('power.trees.waitingHint') }}</p>
    <p v-if="error" class="error" role="alert" data-testid="trees-error">{{ error }}</p>
    <TreeActions
      v-for="item in waiting"
      :key="item.id"
      :tree="item"
      :title="[item.session_name, item.title].filter(Boolean).join(' · ')"
      @changed="load"
    />
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { listWorktrees } from '@/api/console'
import type { Worktree } from '@/api/console'
import { describeConsoleError } from '../powerFormat'
import TreeActions from './TreeActions.vue'

/**
 * What the familiars of a project changed in worktrees of their own and left for the user to decide
 * on. Listed apart from the familiars: one that has not been at work for a while is not on the board,
 * and what it left is here all the same.
 */
const props = defineProps<{
  project: string
  /** Changes whenever the board moved: read again then. */
  refresh?: string
}>()
const { t, te } = useI18n()
const trees = ref<Worktree[]>([])
const error = ref('')
let turn = 0

const waiting = computed(() => trees.value.filter(item => ['held', 'unlanded', 'kept'].includes(item.state)))

async function load() {
  const mine = ++turn
  try {
    const listed = (await listWorktrees(props.project)).worktrees
    if (mine !== turn) return
    trees.value = listed
    error.value = ''
  } catch (failure) {
    if (mine !== turn) return
    error.value = describeConsoleError(failure, t, te)
  }
}

watch(() => props.project, () => {
  trees.value = []
  void load()
}, { immediate: true })
watch(() => props.refresh, () => void load())
defineExpose({ load })
</script>

<style scoped>
.trees { margin-bottom: 14px; }
h4 { margin: 0 0 2px; font-size: 13px; font-weight: 600; }
.muted { margin: 0; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; }
.error { margin: 6px 0 0; color: var(--el-color-danger); font-size: 12px; }
</style>
