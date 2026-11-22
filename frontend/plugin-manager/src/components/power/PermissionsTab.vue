<template>
  <div v-loading="loading && !view" class="permissions-tab">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="block" />
    <template v-if="view">
      <el-alert
        v-if="view.unreadable"
        :title="t('power.runtime.permissions.unreadable')"
        type="warning"
        :closable="false"
        show-icon
        class="block"
        data-testid="permissions-unreadable"
      />
      <section class="panel">
        <h2>{{ t('power.runtime.permissions.title') }}</h2>
        <p class="muted">{{ t('power.runtime.permissions.hint') }}</p>
        <table class="rows">
          <thead>
            <tr>
              <th>{{ t('power.runtime.permissions.category') }}</th>
              <th>{{ t('power.runtime.permissions.setting') }}</th>
              <th>{{ t('power.runtime.permissions.autoIs') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in view.categories" :key="row.category" :data-category="row.category">
              <td>{{ t(`power.runtime.permissions.categories.${row.category}`) }}</td>
              <td>
                <el-select
                  :model-value="row.mode"
                  :disabled="view.unreadable || saving !== ''"
                  :loading="saving === row.category"
                  :aria-label="t(`power.runtime.permissions.categories.${row.category}`)"
                  class="mode"
                  @change="(mode: PermissionMode) => change(row.category, mode)"
                >
                  <el-option
                    v-for="mode in view.modes"
                    :key="mode"
                    :value="mode"
                    :label="t(`power.runtime.permissions.modes.${mode}`)"
                  />
                </el-select>
              </td>
              <td class="muted">
                {{ t(`power.runtime.permissions.modes.${row.auto}`) }}
                <template v-if="te(`power.runtime.permissions.notes.${row.category}`)">
                  · {{ t(`power.runtime.permissions.notes.${row.category}`) }}
                </template>
              </td>
            </tr>
          </tbody>
        </table>
        <p class="muted fixed">{{ t('power.runtime.permissions.fixed') }}</p>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { consoleErrorCode, getPermissions, setPermission } from '@/api/console'
import type { PermissionMode, PermissionsView } from '@/api/console'
import { describeConsoleError } from './powerFormat'

const { t, te } = useI18n()
const view = ref<PermissionsView | null>(null)
const loading = ref(false)
const saving = ref('')
const error = ref('')

async function load() {
  loading.value = true
  try {
    view.value = await getPermissions()
    error.value = ''
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  } finally {
    loading.value = false
  }
}

async function change(category: string, mode: PermissionMode) {
  if (!view.value) return
  saving.value = category
  try {
    view.value = await setPermission(category, mode, view.value.version)
    error.value = ''
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
    // Changed in another window meanwhile: show what is there now, so the
    // user decides on that and not on what this page still showed.
    if (consoleErrorCode(failure) === 'stale') {
      try {
        view.value = await getPermissions()
      } catch {
        /* The message above stays. */
      }
    }
  } finally {
    saving.value = ''
  }
}

onMounted(() => void load())
defineExpose({ load })
</script>

<style scoped>
.panel { background: var(--el-bg-color); border: 1px solid var(--surface-border, var(--el-border-color-lighter)); border-radius: var(--radius-card, 10px); padding: 18px 20px; }
.panel h2 { margin: 0 0 6px; font-size: 16px; }
.muted { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
.rows { width: 100%; border-collapse: collapse; margin-top: 8px; }
.rows th, .rows td { text-align: left; padding: 10px 8px; border-top: 1px solid var(--el-border-color-lighter); vertical-align: middle; }
.rows th { border-top: 0; font-weight: 500; font-size: 13px; color: var(--el-text-color-secondary); }
.mode { width: 180px; max-width: 100%; }
.fixed { margin: 14px 0 0; }
.block { margin-bottom: 12px; }
</style>
