<template>
  <el-card class="selector-card" :aria-busy="loading">
    <template #header>
      <div class="card-header card-header--stack">
        <div class="selector-topline">
          <span>{{ t('plugins.localPlugins') }}</span>
          <div class="selector-topline__meta">
            <el-tag size="small" type="primary">{{ totalCount }}</el-tag>
            <el-tag size="small" type="info">{{ t('plugins.selectedCount', { count: selectedCount }) }}</el-tag>
          </div>
        </div>

        <el-input
          :model-value="pluginFilter"
          clearable
          :placeholder="t('plugins.filterPlaceholder')"
          :aria-label="t('common.filter')"
          @update:model-value="$emit('update:pluginFilter', $event)"
        />

        <div class="type-filter-bar">
          <el-checkbox-group
            :model-value="selectedTypes"
            class="type-filter-group"
            :aria-label="t('plugins.pluginType')"
            @update:model-value="$emit('update:selectedTypes', $event)"
          >
            <el-checkbox-button label="plugin">{{ t('plugins.pluginsSection') }} ({{ pluginCount }})</el-checkbox-button>
            <el-checkbox-button label="adapter">{{ t('plugins.adaptersSection') }} ({{ adapterCount }})</el-checkbox-button>
          </el-checkbox-group>
        </div>

        <div class="selector-toolbar">
          <el-radio-group
            :model-value="layoutMode"
            size="small"
            :aria-label="t('plugins.layoutLabel')"
            @update:model-value="$emit('update:layoutMode', $event)"
          >
            <el-radio-button label="list">{{ t('plugins.layoutList') }}</el-radio-button>
            <el-radio-button label="single">{{ t('plugins.layoutSingle') }}</el-radio-button>
            <el-radio-button label="double">{{ t('plugins.layoutDouble') }}</el-radio-button>
            <el-radio-button label="compact">{{ t('plugins.layoutCompact') }}</el-radio-button>
          </el-radio-group>

          <div class="selector-actions">
            <el-button text @click="$emit('selectAllVisible')">{{ t('plugins.selectAllVisible') }}</el-button>
            <el-button text @click="$emit('clearSelection')">{{ t('plugins.clearSelection') }}</el-button>
            <el-button :loading="loading" text @click="$emit('refresh')">{{ t('common.refresh') }}</el-button>
          </div>
        </div>
      </div>
    </template>

    <LoadingSpinner v-if="loading && totalFilteredCount === 0" :loading="true" />
    <el-empty
      v-else-if="totalFilteredCount === 0"
      :description="t('plugins.noLocalMatches')"
    />

    <div v-else class="selector-sections">
      <template v-for="section in sections" :key="section.key">
        <template v-if="section.items.length > 0">
          <div class="section-header" :class="section.headerClass">
            <span class="section-title">{{ section.title }} ({{ section.items.length }})</span>
          </div>
          <div class="plugin-selector-grid" :class="layoutClass">
            <div
              v-for="plugin in section.items"
              :key="plugin.id"
              class="plugin-select-item"
              :class="{
                'plugin-select-item--list': layoutMode === 'list',
                'plugin-select-card--active': isSelected(plugin.id),
              }"
              @click="layoutMode === 'list' && $emit('togglePlugin', plugin.id)"
            >
              <template v-if="layoutMode === 'list'">
                <div class="plugin-list-row">
                  <el-checkbox
                    :model-value="isSelected(plugin.id)"
                    :aria-label="`${t('common.toggleSelection')}: ${plugin.displayName || plugin.name}`"
                    @click.stop
                    @change="$emit('togglePlugin', plugin.id)"
                  />
                  <button
                    type="button"
                    class="plugin-list-row__name"
                    :aria-pressed="isSelected(plugin.id)"
                    @click.stop="$emit('togglePlugin', plugin.id)"
                  >{{ plugin.displayName || plugin.name }}</button>
                </div>
              </template>
              <template v-else>
                <div class="plugin-select-item__checkbox">
                  <el-checkbox
                    :model-value="isSelected(plugin.id)"
                    :aria-label="`${t('common.toggleSelection')}: ${plugin.displayName || plugin.name}`"
                    @click.stop
                    @change="$emit('togglePlugin', plugin.id)"
                  />
                </div>
                <PluginCard
                  :plugin="plugin"
                  :is-selected="isSelected(plugin.id)"
                  shared-rows
                  @click="$emit('togglePlugin', plugin.id)"
                />
              </template>
            </div>
          </div>
        </template>
      </template>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import PluginCard from '@/components/plugin/PluginCard.vue'
import LoadingSpinner from '@/components/common/LoadingSpinner.vue'
import type { PluginMeta } from '@/types/api'

type LayoutMode = 'list' | 'single' | 'double' | 'compact'
type PluginGroupType = 'plugin' | 'adapter'

type SelectablePlugin = PluginMeta & {
  type: PluginGroupType
  enabled?: boolean
  autoStart?: boolean
  displayName?: string
}

const props = defineProps<{
  loading: boolean
  totalCount: number
  selectedCount: number
  pluginFilter: string
  selectedTypes: PluginGroupType[]
  layoutMode: LayoutMode
  pluginCount: number
  adapterCount: number
  filteredPurePlugins: SelectablePlugin[]
  filteredAdapters: SelectablePlugin[]
  selectedPluginIds: string[]
}>()

defineEmits<{
  refresh: []
  selectAllVisible: []
  clearSelection: []
  togglePlugin: [pluginId: string]
  'update:pluginFilter': [value: string]
  'update:selectedTypes': [value: PluginGroupType[]]
  'update:layoutMode': [value: LayoutMode]
}>()

const { t } = useI18n()

const layoutClass = computed(() => `plugin-selector-grid--${props.layoutMode}`)
const totalFilteredCount = computed(
  () => props.filteredPurePlugins.length + props.filteredAdapters.length
)

const sections = computed(() => [
  { key: 'plugin', title: t('plugins.pluginsSection'), items: props.filteredPurePlugins, headerClass: '' },
  { key: 'adapter', title: t('plugins.adaptersSection'), items: props.filteredAdapters, headerClass: 'section-header--adapter' },
])

function isSelected(pluginId: string): boolean {
  return props.selectedPluginIds.includes(pluginId)
}
</script>

<style scoped>
.selector-card {
  border-radius: var(--radius-card);
  background: var(--surface-card);
  border-color: var(--surface-border);
  box-shadow: var(--surface-shadow);
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.card-header--stack {
  flex-direction: column;
  align-items: stretch;
}

.selector-topline {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.selector-topline__meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.selector-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.selector-toolbar :deep(.el-radio-group) {
  display: flex;
  flex-wrap: wrap;
}

.selector-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.type-filter-bar {
  padding: 2px 0;
}

.type-filter-group {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.type-filter-group .el-checkbox-button__inner {
  display: flex;
  align-items: center;
  gap: 4px;
}

.selector-sections {
  max-height: 820px;
  overflow: auto;
  padding-right: 4px;
}

.section-header {
  margin-bottom: 12px;
}

.section-header--adapter {
  margin-top: 24px;
}

.section-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.plugin-selector-grid {
  display: grid;
  gap: 12px;
}

.plugin-selector-grid--single {
  grid-template-columns: 1fr;
}

.plugin-selector-grid--list {
  grid-template-columns: 1fr;
  gap: 8px;
}

.plugin-selector-grid--double {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.plugin-selector-grid--compact {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.plugin-select-item {
  position: relative;
  cursor: pointer;
  transition:
    transform var(--motion-duration-normal) var(--motion-ease-standard),
    border-color var(--motion-duration-normal) var(--motion-ease-standard);
}

@supports (grid-template-rows: subgrid) {
  .plugin-select-item:not(.plugin-select-item--list) {
    display: grid;
    grid-row: span 3;
    grid-template-rows: subgrid;
    row-gap: 0;
  }
}

.plugin-select-item--list {
  border: 1px solid var(--surface-border);
  border-radius: var(--radius-control);
  background: var(--surface-card);
}

.plugin-select-item--list:hover {
  transform: translateY(-2px);
}

.plugin-select-item__checkbox {
  position: absolute;
  top: 8px;
  right: 10px;
  z-index: 2;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--surface-card);
}

.plugin-select-item :deep(.plugin-card) {
  height: 100%;
}

.plugin-select-item :deep(.plugin-card-header) {
  padding-right: 56px;
}

.plugin-list-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 42px;
  padding: 0 12px;
}

.plugin-list-row__name {
  min-width: 0;
  padding: 4px 0;
  border: 0;
  border-radius: 4px;
  background: transparent;
  font: inherit;
  font-size: 14px;
  color: var(--el-text-color-primary);
  line-height: 1.4;
  word-break: break-all;
  text-align: left;
  cursor: pointer;
}

.plugin-list-row__name:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 3px;
}

@media (max-width: 980px) {
  .plugin-selector-grid--double,
  .plugin-selector-grid--compact {
    grid-template-columns: 1fr;
  }
}

@media (prefers-reduced-motion: reduce) {
  .plugin-select-item {
    transition: none;
  }

  .plugin-select-item:hover {
    transform: none;
  }
}
</style>
