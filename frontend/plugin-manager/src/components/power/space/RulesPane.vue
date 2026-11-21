<template>
  <section v-loading="loading" class="panel">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="block" />
    <p class="muted">
      {{ t(readonly ? 'power.space.rules.archivedReadOnly' : scope.project ? 'power.space.rules.hintProject' : 'power.space.rules.hint') }}
    </p>
    <el-input
      v-model="text"
      type="textarea"
      :autosize="{ minRows: 8, maxRows: 24 }"
      :readonly="readonly"
      :placeholder="t('power.space.rules.placeholder')"
      :aria-label="t('power.space.tabs.rules')"
      data-testid="rules-text"
    />
    <div v-if="!readonly" class="actions">
      <el-button type="primary" :loading="saving" :disabled="text === saved" data-testid="rules-save" @click="save">
        {{ t('power.space.rules.save') }}
      </el-button>
      <span v-if="justSaved" class="muted" role="status">{{ t('power.space.rules.saved') }}</span>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { readRules, writeRules } from '@/api/console'
import type { SpaceScope } from '@/api/console'
import { describeConsoleError } from '../powerFormat'

const props = defineProps<{ scope: SpaceScope; readonly?: boolean }>()
const { t, te } = useI18n()
const text = ref('')
const saved = ref('')
const loading = ref(false)
const saving = ref(false)
const justSaved = ref(false)
const error = ref('')
// Which load is the latest: an answer for a scope the user has left is dropped.
let turn = 0

async function load() {
  const mine = ++turn
  loading.value = true
  justSaved.value = false
  try {
    const answer = await readRules(props.scope)
    if (mine !== turn) return
    text.value = saved.value = answer.text
    error.value = ''
  } catch (failure) {
    if (mine === turn) error.value = describeConsoleError(failure, t, te)
  } finally {
    if (mine === turn) loading.value = false
  }
}

async function save() {
  saving.value = true
  justSaved.value = false
  try {
    const answer = await writeRules(text.value, props.scope)
    text.value = saved.value = answer.text
    error.value = ''
    justSaved.value = true
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  } finally {
    saving.value = false
  }
}

watch(() => [props.scope.project, props.scope.archive_year], () => void load(), { immediate: true })
</script>

<style scoped>
.panel { background: var(--el-bg-color); border: 1px solid var(--surface-border, var(--el-border-color-lighter)); border-radius: var(--radius-card, 10px); padding: 18px 20px; }
.muted { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; margin: 0 0 10px; }
.actions { display: flex; align-items: center; gap: 12px; margin-top: 12px; }
.actions .muted { margin: 0; }
.block { margin-bottom: 12px; }
</style>
