<template>
  <section v-loading="loading" class="panel">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="block" />
    <p class="muted">{{ t('power.space.preferences.hint') }}</p>
    <el-empty v-if="!loading && !preferences.length" :description="t('power.space.preferences.empty')" />
    <table v-else class="rows">
      <thead>
        <tr>
          <th>{{ t('power.space.preferences.text') }}</th>
          <th>{{ t('power.space.preferences.kind') }}</th>
          <th>{{ t('power.space.preferences.origin') }}</th>
          <th>{{ t('power.space.preferences.active') }}</th>
          <th />
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in preferences" :key="item.id" :data-preference="item.id">
          <td class="text">{{ item.text }}</td>
          <td class="muted">{{ label('kinds', item.kind) }}</td>
          <td class="muted">{{ label('origins', item.origin) }}</td>
          <td>
            <el-switch
              :model-value="item.active"
              :loading="busy === item.id"
              :disabled="busy !== 0"
              :aria-label="item.text"
              @change="(value: string | number | boolean) => change(item, { active: value === true })"
            />
          </td>
          <td class="act">
            <el-button size="small" @click="edit(item)">{{ t('power.space.preferences.edit') }}</el-button>
            <el-button size="small" type="danger" plain @click="askRemove(item)">{{ t('power.space.preferences.remove') }}</el-button>
          </td>
        </tr>
      </tbody>
    </table>

    <el-dialog v-model="editing" :title="t('power.space.preferences.editTitle')" width="520px">
      <el-input v-model="draft" type="textarea" :autosize="{ minRows: 3, maxRows: 8 }" :aria-label="t('power.space.preferences.text')" data-testid="preference-text" />
      <template #footer>
        <el-button @click="editing = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!draft.trim()" :loading="busy !== 0" data-testid="preference-save" @click="saveEdit">{{ t('power.space.preferences.save') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="removing" :title="t('power.space.preferences.removeTitle')" width="440px">
      <p>{{ target?.text }}</p>
      <p class="muted">{{ t('power.space.preferences.removeText') }}</p>
      <template #footer>
        <el-button @click="removing = false">{{ t('common.cancel') }}</el-button>
        <el-button type="danger" :loading="busy !== 0" data-testid="preference-remove-confirm" @click="remove">{{ t('power.space.preferences.remove') }}</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { editPreference, listPreferences, removePreference } from '@/api/console'
import type { TaskPreference } from '@/api/console'
import { describeConsoleError } from '../powerFormat'

const { t, te } = useI18n()
const preferences = ref<TaskPreference[]>([])
const loading = ref(false)
// The id of the preference a request is under way for; 0 for none.
const busy = ref(0)
const error = ref('')
const editing = ref(false)
const removing = ref(false)
const target = ref<TaskPreference | null>(null)
const draft = ref('')

function label(group: 'kinds' | 'origins', value: string): string {
  const key = `power.space.preferences.${group}.${value}`
  return te(key) ? t(key) : value
}

/** Read the list; `keep` leaves the message of what just failed where it is. */
async function load(keep = false) {
  loading.value = true
  try {
    preferences.value = (await listPreferences()).preferences
    if (!keep) error.value = ''
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  } finally {
    loading.value = false
  }
}

async function change(item: TaskPreference, changes: { text?: string; active?: boolean }): Promise<boolean> {
  busy.value = item.id
  try {
    const changed = (await editPreference(item.id, changes)).preference
    preferences.value = preferences.value.map(other => (other.id === changed.id ? changed : other))
    error.value = ''
    return true
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
    await load(true)
    return false
  } finally {
    busy.value = 0
  }
}

function edit(item: TaskPreference) {
  target.value = item
  draft.value = item.text
  editing.value = true
}

async function saveEdit() {
  if (!target.value) return
  await change(target.value, { text: draft.value })
  editing.value = false
}

function askRemove(item: TaskPreference) {
  target.value = item
  removing.value = true
}

async function remove() {
  if (!target.value) return
  busy.value = target.value.id
  let failed = false
  try {
    await removePreference(target.value.id)
    error.value = ''
  } catch (failure) {
    failed = true
    error.value = describeConsoleError(failure, t, te)
  } finally {
    busy.value = 0
    removing.value = false
    await load(failed)
  }
}

onMounted(() => void load())
</script>

<style scoped>
.panel { background: var(--el-bg-color); border: 1px solid var(--surface-border, var(--el-border-color-lighter)); border-radius: var(--radius-card, 10px); padding: 18px 20px; }
.muted { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; margin: 0; }
.rows { width: 100%; border-collapse: collapse; margin-top: 12px; }
.rows th, .rows td { text-align: left; padding: 10px 8px; border-top: 1px solid var(--el-border-color-lighter); vertical-align: middle; }
.rows th { border-top: 0; font-weight: 500; font-size: 13px; color: var(--el-text-color-secondary); }
.text { overflow-wrap: anywhere; }
.act { text-align: right; white-space: nowrap; }
.act .el-button + .el-button { margin-left: 6px; }
.block { margin-bottom: 12px; }
</style>
