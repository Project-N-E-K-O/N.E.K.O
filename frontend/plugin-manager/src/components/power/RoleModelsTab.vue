<template>
  <div v-loading="loading && !view" class="roles-tab">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="block" data-testid="roles-error" />
    <section v-if="view" class="panel">
      <h2>{{ t('power.roles.title') }}</h2>
      <p class="muted">{{ t('power.roles.hint') }}</p>
      <el-alert
        v-if="view.slots_error"
        :title="t('power.roles.slotsUnreadable', { reason: reason(view.slots_error) })"
        type="warning" :closable="false" show-icon class="block" data-testid="roles-slots-error"
      />
      <table class="rows">
        <thead>
          <tr>
            <th>{{ t('power.roles.preset') }}</th>
            <th>{{ t('power.roles.setting') }}</th>
            <th>{{ t('power.roles.inEffect') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in view.presets" :key="row.preset" :data-preset="row.preset">
            <td>
              <span class="face" aria-hidden="true">{{ FAMILIAR_FACES[row.preset] ?? FAMILIAR_FACES.general }}</span>
              {{ presetName(row, t, te) }}
              <span v-if="row.source !== 'builtin'" class="tag">{{ t('power.roles.template') }}</span>
              <span v-if="!row.valid" class="tag tag--warn">{{ t('power.roles.invalid') }}</span>
              <small v-if="te(`power.roles.advice.${row.preset}`)" class="muted advice">{{ t(`power.roles.advice.${row.preset}`) }}</small>
            </td>
            <td>
              <!-- The slots are whatever the model panel has now: none of them is named in this page. -->
              <select
                class="choice"
                :value="row.setting ?? ''"
                :disabled="saving !== ''"
                :aria-label="presetName(row, t, te)"
                :data-setting="row.preset"
                @change="change(row, ($event.target as HTMLSelectElement).value)"
              >
                <option value="">{{ t('power.roles.default', { what: defaultName(row) }) }}</option>
                <option v-if="row.can_inherit" value="inherit">{{ t('power.roles.inherit') }}</option>
                <option v-for="slot in view.slots" :key="slot.slot_id" :value="slot.slot_id">{{ slotName(slot) }}</option>
                <!-- A slot that is set and is not among them any more is still named, so that the setting can be read. -->
                <option v-if="row.setting && row.setting !== 'inherit' && !known(row.setting)" :value="row.setting">
                  {{ t('power.roles.goneSlot') }}
                </option>
              </select>
            </td>
            <td :data-effect="row.preset">
              <template v-if="row.in_effect.follows === 'starter'">{{ t('power.roles.inherit') }}</template>
              <template v-else>
                <code>{{ row.in_effect.model || t('power.roles.noModel') }}</code>
                <span v-if="row.in_effect.agent" class="muted"> · {{ t('power.roles.agentModel') }}</span>
                <span v-if="row.in_effect.free" class="tag">{{ t('power.roles.free') }}</span>
              </template>
              <small v-if="row.in_effect.fallback" class="fallback" data-testid="role-fallback">
                {{ t('power.roles.fellBackWhy', { reason: reason(row.in_effect.fallback) }) }}
              </small>
            </td>
          </tr>
        </tbody>
      </table>
      <ul class="notes muted">
        <li>{{ t('power.roles.noteNewOnly') }}</li>
        <li>{{ t('power.roles.noteBuiltinOnly') }}</li>
        <li>{{ t('power.roles.noteFree') }}</li>
        <li>{{ t('power.roles.noteProjectTemplates') }}</li>
      </ul>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRoleModels, setRoleModel } from '@/api/console'
import type { RoleModelPreset, RoleModelSlot, RoleModelsView } from '@/api/console'
import { FAMILIAR_FACES, describeConsoleError, presetName } from './powerFormat'

/** Which model the familiars of each kind are sent to: its default, the model of whoever starts it, or a slot of the model panel. */
const { t, te } = useI18n()
const view = ref<RoleModelsView | null>(null)
const loading = ref(true)
const saving = ref('')
const error = ref('')

const known = (slotId: string) => Boolean(view.value?.slots.some(slot => slot.slot_id === slotId))
function slotName(slot: RoleModelSlot): string {
  return slot.model ? `${slot.name} (${slot.model})` : `${slot.name} · ${t('power.roles.slotEmpty')}`
}
function defaultName(row: RoleModelPreset): string {
  if (row.default === 'inherit') return t('power.roles.inherit')
  return t(row.default === 'advanced' ? 'power.roles.advancedModel' : 'power.roles.agentModel')
}
/** Why a setting or the slots could not be used, in words; the code itself where the page has none for it. */
function reason(code: string): string {
  const key = `power.roles.fallbacks.${code}`
  return te(key) ? t(key) : code
}

async function load() {
  loading.value = true
  try {
    view.value = await getRoleModels()
    error.value = ''
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  } finally {
    loading.value = false
  }
}

async function change(row: RoleModelPreset, value: string) {
  saving.value = row.preset
  try {
    view.value = await setRoleModel(row.preset, value || null)
    error.value = ''
  } catch (failure) {
    const why = describeConsoleError(failure, t, te)
    // What is shown goes back to what is set; why it was not taken stays said.
    await load()
    error.value = why
  } finally {
    saving.value = ''
  }
}

onMounted(load)
</script>

<style scoped>
.panel { background: var(--el-bg-color); border: 1px solid var(--surface-border, var(--el-border-color-lighter)); border-radius: var(--radius-card, 10px); padding: 18px 20px; }
h2 { margin: 0 0 6px; font-size: 17px; }
.muted { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
.block { margin-bottom: 12px; }
.rows { width: 100%; border-collapse: collapse; margin-top: 8px; }
.rows th, .rows td { text-align: left; padding: 10px 8px; border-top: 1px solid var(--el-border-color-lighter); vertical-align: top; }
.rows th { font-weight: 500; font-size: 13px; color: var(--el-text-color-secondary); border-top: 0; }
.face { margin-right: 4px; }
.advice, .fallback { display: block; margin-top: 2px; }
.fallback { color: var(--el-color-warning); }
.choice { font: inherit; max-width: 280px; padding: 4px 8px; color: var(--el-text-color-regular); background: var(--el-bg-color); border: 1px solid var(--el-border-color); border-radius: 6px; }
.tag { margin-left: 6px; padding: 0 6px; font-size: 12px; border-radius: 8px; color: var(--el-text-color-secondary); background: var(--el-fill-color); }
.tag--warn { color: var(--el-color-warning); background: var(--el-color-warning-light-9); }
.notes { margin: 12px 0 0; padding-left: 18px; }
</style>
