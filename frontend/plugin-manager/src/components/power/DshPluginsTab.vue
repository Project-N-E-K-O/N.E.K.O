<template>
  <div class="dsh-plugins-tab">
    <section class="panel">
      <h2>{{ t('power.runtime.plugins.title') }}</h2>
      <el-alert :title="t('power.runtime.plugins.warning')" type="warning" :closable="false" show-icon class="block" data-testid="dsh-plugin-warning" />
      <el-empty v-if="!installed" :description="t('power.runtime.plugins.needsDsh')" />
      <el-empty v-else-if="!plugins.length" :description="t('power.runtime.plugins.empty')" />
      <table v-else class="rows">
        <thead>
          <tr>
            <th>{{ t('power.runtime.plugins.name') }}</th>
            <th>{{ t('power.runtime.plugins.version') }}</th>
            <th>{{ t('power.runtime.plugins.enabled') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="plugin in plugins" :key="plugin.name" :data-plugin="plugin.name">
            <td>{{ plugin.name }}</td>
            <td class="muted">{{ plugin.version }}</td>
            <td><el-switch :model-value="plugin.enabled" disabled :aria-label="plugin.name" /></td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { DshPlugin } from '@/api/console'

defineProps<{ plugins: DshPlugin[]; installed: boolean }>()
const { t } = useI18n()
</script>

<style scoped>
.panel { background: var(--el-bg-color); border: 1px solid var(--surface-border, var(--el-border-color-lighter)); border-radius: var(--radius-card, 10px); padding: 18px 20px; }
.panel h2 { margin: 0 0 12px; font-size: 16px; }
.muted { color: var(--el-text-color-secondary); font-size: 13px; }
.rows { width: 100%; border-collapse: collapse; margin-top: 8px; }
.rows th, .rows td { text-align: left; padding: 10px 8px; border-top: 1px solid var(--el-border-color-lighter); }
.rows th { border-top: 0; font-weight: 500; font-size: 13px; color: var(--el-text-color-secondary); }
.block { margin-bottom: 12px; }
</style>
