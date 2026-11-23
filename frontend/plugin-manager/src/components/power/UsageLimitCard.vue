<template>
  <section class="panel" data-testid="usage-limit">
    <h2>{{ t('power.limit.title') }}</h2>
    <p class="muted">{{ t('power.limit.hint') }}</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="block" data-testid="limit-error" />
    <div v-if="view" class="line">
      <input
        v-model="amount" type="number" class="amount" :min="view.min / unit" :max="view.max / unit" step="any"
        :aria-label="t('power.limit.title')" data-testid="limit-amount" @keyup.enter="save(false)"
      >
      <span>{{ t('power.limit.unit') }}</span>
      <el-button type="primary" :loading="saving" :disabled="!changed" data-testid="limit-save" @click="save(false)">{{ t('power.limit.save') }}</el-button>
      <el-button :disabled="saving || view.set === null" data-testid="limit-default" @click="save(true)">{{ t('power.limit.useDefault') }}</el-button>
      <span v-if="note" class="muted" role="status" data-testid="limit-note">{{ note }}</span>
    </div>
    <p v-if="view" class="muted">
      {{ t('power.limit.range', { min: shown(view.min), max: shown(view.max), default: view.default ? shown(view.default) : '—' }) }}
    </p>
    <p class="muted">{{ t('power.limit.notes') }}</p>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getUsageLimit, setUsageLimit } from '@/api/console'
import type { UsageLimit } from '@/api/console'
import { describeConsoleError } from './powerFormat'

/** How many tokens the work on one commission may use before it stops to ask the user. */
const { t, te } = useI18n()
const view = ref<UsageLimit | null>(null)
const amount = ref('')
const saving = ref(false)
const error = ref('')
const note = ref('')
// The number is typed in the unit people of the language count large numbers by; how many tokens that is.
const unit = computed(() => Number(t('power.limit.unitTokens')) || 1000)

function shown(tokens: number): string {
  return String(Math.round((tokens / unit.value) * 100) / 100)
}
const typed = computed(() => {
  const value = Number(amount.value)
  return amount.value !== '' && Number.isFinite(value) ? Math.round(value * unit.value) : null
})
const changed = computed(() => typed.value !== null && typed.value !== view.value?.tokens)

function show(now: UsageLimit) {
  view.value = now
  amount.value = now.tokens === null ? '' : shown(now.tokens)
}

async function save(toDefault: boolean) {
  if (!toDefault && typed.value === null) return
  saving.value = true
  note.value = ''
  try {
    show(await setUsageLimit(toDefault ? null : typed.value))
    error.value = ''
    note.value = t('power.limit.saved')
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  try {
    show(await getUsageLimit())
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
.line { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 10px; }
.amount { width: 110px; font: inherit; padding: 4px 8px; color: var(--el-text-color-regular); background: var(--el-bg-color); border: 1px solid var(--el-border-color); border-radius: 6px; }
</style>
