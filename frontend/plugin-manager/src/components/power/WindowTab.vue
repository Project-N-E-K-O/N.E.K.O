<template>
  <div v-loading="loading && !loaded" class="window-tab">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="block" />
    <el-empty v-if="loaded && !window" :description="t('power.runtime.noModel')" data-testid="window-no-model" />
    <section v-if="window" class="panel">
      <h2>{{ t('power.runtime.window.title') }}</h2>
      <p class="muted">{{ t('power.runtime.window.perModel', { model: window.model }) }}</p>
      <el-alert
        v-if="window.reminded"
        :title="t('power.runtime.window.reminded', { window: window.reminded.toLocaleString() })"
        type="info"
        :closable="false"
        show-icon
        class="block"
      />

      <div class="row">
        <div class="label">
          {{ t('power.runtime.window.context') }}
          <small>{{ t('power.runtime.window.contextHint') }} {{ t('power.runtime.window.default', { value: window.default.toLocaleString() }) }}</small>
        </div>
        <el-input-number v-model="form.context" :min="1" :step="1000" :controls="false" :placeholder="String(window.default)" data-testid="window-context" />
        <el-button link :disabled="form.context === null" @click="form.context = null">{{ t('power.runtime.window.useDefault') }}</el-button>
      </div>

      <div class="row">
        <div class="label">
          {{ t('power.runtime.window.compactAt') }}
          <small>{{ t('power.runtime.window.compactAtHint') }}</small>
        </div>
        <el-input-number v-model="form.compactAt" :min="1" :step="1000" :controls="false" :placeholder="String(window.compact_at)" data-testid="window-compact-at" />
        <el-button link :disabled="form.compactAt === null" @click="form.compactAt = null">{{ t('power.runtime.window.useDefault') }}</el-button>
      </div>

      <div class="row">
        <div class="label">
          {{ t('power.runtime.window.idleAfter') }}
          <small>{{ t('power.runtime.window.idleHint') }} {{ t('power.runtime.window.idleAfterHint') }}</small>
        </div>
        <el-input-number v-model="form.idleAmount" :min="0" :controls="false" :placeholder="String(idleDefault.amount)" data-testid="window-idle" />
        <el-select v-model="form.idleUnit" class="unit" :aria-label="t('power.runtime.window.idleAfter')">
          <el-option v-for="unit in units" :key="unit" :value="unit" :label="t(`power.runtime.window.units.${unit}`)" />
        </el-select>
        <el-button link :disabled="form.idleAmount === null" @click="form.idleAmount = null">{{ t('power.runtime.window.useDefault') }}</el-button>
      </div>

      <div class="actions">
        <el-button type="primary" :loading="saving" :disabled="!dirty" data-testid="window-save" @click="save">{{ t('power.runtime.window.save') }}</el-button>
        <span v-if="saved" class="muted" role="status">{{ t('power.runtime.window.saved') }}</span>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getWindow, setWindow } from '@/api/console'
import type { ModelWindow, WindowChanges } from '@/api/console'
import { describeConsoleError, joinIdle, splitIdle } from './powerFormat'
import type { IdleUnit } from './powerFormat'

const { t, te } = useI18n()
const units: IdleUnit[] = ['minutes', 'hours', 'days']
const window = ref<ModelWindow | null>(null)
const loaded = ref(false)
const loading = ref(false)
const saving = ref(false)
const saved = ref(false)
const error = ref('')
// What the user set, or null for "the default": the fields show the default as a placeholder then.
const form = reactive<{ context: number | null; compactAt: number | null; idleAmount: number | null; idleUnit: IdleUnit }>({
  context: null, compactAt: null, idleAmount: null, idleUnit: 'minutes',
})

const idleDefault = computed(() => splitIdle(window.value?.idle_compact_after ?? 0))
const idleSeconds = computed(() => (form.idleAmount === null ? null : joinIdle(form.idleAmount, form.idleUnit)))
const dirty = computed(() => {
  const now = window.value
  if (!now) return false
  return form.context !== now.set || form.compactAt !== now.compact_at_set || idleSeconds.value !== now.idle_compact_after_set
})

function show(next: ModelWindow | null) {
  window.value = next
  if (!next) return
  form.context = next.set
  form.compactAt = next.compact_at_set
  if (next.idle_compact_after_set === null) {
    form.idleAmount = null
    form.idleUnit = splitIdle(next.idle_compact_after).unit
  } else {
    const split = splitIdle(next.idle_compact_after_set)
    form.idleAmount = split.amount
    form.idleUnit = split.unit
  }
}

async function load() {
  loading.value = true
  try {
    show((await getWindow()).window)
    error.value = ''
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  } finally {
    loaded.value = true
    loading.value = false
  }
}

async function save() {
  const now = window.value
  if (!now) return
  // Only what changed is sent: the rest stays as it is on the other side.
  const changes: WindowChanges = {}
  if (form.context !== now.set) changes.context_window = form.context
  if (form.compactAt !== now.compact_at_set) changes.compact_at = form.compactAt
  if (idleSeconds.value !== now.idle_compact_after_set) changes.idle_compact_after = idleSeconds.value
  saving.value = true
  saved.value = false
  try {
    show((await setWindow(changes)).window)
    error.value = ''
    saved.value = true
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  } finally {
    saving.value = false
  }
}

onMounted(() => void load())
defineExpose({ load })
</script>

<style scoped>
.panel { background: var(--el-bg-color); border: 1px solid var(--surface-border, var(--el-border-color-lighter)); border-radius: var(--radius-card, 10px); padding: 18px 20px; }
.panel h2 { margin: 0 0 6px; font-size: 16px; }
.muted { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
.row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding: 14px 0; border-top: 1px solid var(--el-border-color-lighter); }
.label { flex: 1 1 320px; min-width: 0; line-height: 1.5; }
.label small { display: block; color: var(--el-text-color-secondary); line-height: 1.6; margin-top: 2px; }
.unit { width: 110px; }
.actions { display: flex; align-items: center; gap: 12px; padding-top: 14px; border-top: 1px solid var(--el-border-color-lighter); }
.block { margin: 12px 0; }
</style>
