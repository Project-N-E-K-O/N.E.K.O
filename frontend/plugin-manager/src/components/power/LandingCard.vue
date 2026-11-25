<template>
  <section class="panel" data-testid="landing-settings">
    <h2>{{ t('power.landing.title') }}</h2>
    <p class="muted">{{ t('power.landing.hint') }}</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="block" data-testid="landing-error" />
    <template v-if="view">
      <el-radio-group :model-value="view.mode" :disabled="saving" data-testid="landing-mode" @change="setMode">
        <el-radio value="auto" data-testid="landing-auto">{{ t('power.landing.auto') }}</el-radio>
        <el-radio value="ask" data-testid="landing-ask">{{ t('power.landing.ask') }}</el-radio>
      </el-radio-group>
      <p class="muted">{{ view.mode === 'ask' ? t('power.landing.askHint') : t('power.landing.autoHint') }}</p>
      <div class="line">
        <el-switch :model-value="view.inspect" :disabled="saving" data-testid="landing-inspect" @change="setInspect" />
        <span>{{ t('power.landing.inspect') }}</span>
      </div>
      <p class="muted">{{ t('power.landing.inspectHint') }}</p>

      <!-- Missing where the service has no such setting. -->
      <template v-if="view.beside">
        <h3>{{ t('power.landing.beside.title') }}</h3>
        <p class="muted">{{ t('power.landing.beside.hint') }}</p>
        <el-radio-group :model-value="view.beside" :disabled="saving" class="choices" data-testid="landing-beside" @change="setBeside">
          <!-- What each comes to is said beside it, before it is chosen. -->
          <div v-for="choice in BESIDE" :key="choice" class="choice">
            <el-radio :value="choice" :data-testid="`landing-beside-${choice}`">{{ t(`power.landing.beside.${choice}`) }}</el-radio>
            <p class="muted" :class="{ warn: choice === 'open' }" :data-testid="`landing-beside-${choice}-hint`">
              {{ t(`power.landing.beside.${choice}Hint`) }}
            </p>
          </div>
        </el-radio-group>
        <p class="muted">{{ t('power.landing.beside.permission') }}</p>
      </template>
      <p v-if="note" class="muted" role="status" data-testid="landing-note">{{ note }}</p>
    </template>
    <p class="muted">{{ t('power.landing.notes') }}</p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getLanding, setLanding } from '@/api/console'
import type { BesideChoice, LandingSettings } from '@/api/console'
import { describeConsoleError } from './powerFormat'

/**
 * How what a familiar changed in a git worktree of its own comes back into the project's directory:
 * at once, or once the user says so; and whether an inspector looks at it first every time. And
 * what becomes of work that waits for a familiar busy with something else, in a project without a
 * leader: the user is asked whether another is to do it in a worktree of its own, another is, or
 * it waits.
 */
const BESIDE: readonly BesideChoice[] = ['ask', 'open', 'queue']
const { t, te } = useI18n()
const view = ref<LandingSettings | null>(null)
const saving = ref(false)
const error = ref('')
const note = ref('')

async function save(changes: Partial<LandingSettings>) {
  if (saving.value) return
  saving.value = true
  note.value = ''
  try {
    view.value = await setLanding(changes)
    error.value = ''
    note.value = t('power.landing.saved')
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  } finally {
    saving.value = false
  }
}

const setMode = (mode: string | number | boolean | undefined) => {
  if (mode === 'auto' || mode === 'ask') void save({ mode })
}
const setInspect = (inspect: string | number | boolean) => void save({ inspect: inspect === true })
const setBeside = (beside: string | number | boolean | undefined) => {
  const choice = BESIDE.find(item => item === beside)
  if (choice) void save({ beside: choice })
}

onMounted(async () => {
  try {
    view.value = await getLanding()
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  }
})
</script>

<style scoped>
.panel { margin-top: 16px; background: var(--el-bg-color); border: 1px solid var(--surface-border, var(--el-border-color-lighter)); border-radius: var(--radius-card, 10px); padding: 18px 20px; }
h2 { margin: 0 0 6px; font-size: 17px; }
.muted { margin: 6px 0 0; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
.block { margin: 8px 0; }
.line { display: flex; align-items: center; gap: 8px; margin-top: 14px; }
h3 { margin: 20px 0 4px; font-size: 15px; }
.choices { display: flex; flex-direction: column; align-items: stretch; gap: 6px; margin-top: 8px; }
.choice .muted { margin: 0 0 0 24px; }
.choice .warn { color: var(--el-color-warning); }
</style>
