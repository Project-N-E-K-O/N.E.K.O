<template>
  <el-card class="selector-card">
    <template #header>
      <div class="card-header card-header--stack">
        <div class="selector-topline">
          <span>本地插件</span>
          <div class="selector-topline__meta">
            <el-tag size="small" type="primary">{{ totalCount }}</el-tag>
            <el-tag size="small" type="info">已选 {{ selectedCount }}</el-tag>
          </div>
        </div>

        <el-input
          :model-value="pluginFilter"
          clearable
          :placeholder="t('plugins.filterPlaceholder')"
          @update:model-value="$emit('update:pluginFilter', $event)"
        />

        <div class="selector-filter-row">
          <el-switch
            :model-value="useRegex"
            class="selector-filter-switch"
            active-text="Regex"
            inactive-text="Text"
            @update:model-value="$emit('update:useRegex', $event)"
          />
          <el-radio-group
            :model-value="filterMode"
            size="small"
            @update:model-value="$emit('update:filterMode', $event)"
          >
            <el-radio-button label="whitelist">{{ t('plugins.filterWhitelist') }}</el-radio-button>
            <el-radio-button label="blacklist">{{ t('plugins.filterBlacklist') }}</el-radio-button>
          </el-radio-group>
          <span v-if="regexError" class="selector-filter-error">{{ t('plugins.invalidRegex') }}</span>
        </div>

        <div class="type-filter-bar">
          <el-checkbox-group
            :model-value="selectedTypes"
            class="type-filter-group"
            @update:model-value="$emit('update:selectedTypes', $event)"
          >
            <el-checkbox-button label="plugin">插件 ({{ pluginCount }})</el-checkbox-button>
            <el-checkbox-button label="adapter">适配器 ({{ adapterCount }})</el-checkbox-button>
            <el-checkbox-button label="extension">扩展 ({{ extensionCount }})</el-checkbox-button>
          </el-checkbox-group>
        </div>

        <div class="selector-toolbar">
          <el-radio-group
            :model-value="layoutMode"
            size="small"
            @update:model-value="$emit('update:layoutMode', $event)"
          >
            <el-radio-button label="list">列表</el-radio-button>
            <el-radio-button label="single">单排</el-radio-button>
            <el-radio-button label="double">双排</el-radio-button>
            <el-radio-button label="compact">小矩阵</el-radio-button>
          </el-radio-group>

          <div class="selector-actions">
            <el-button text @click="$emit('selectAllVisible')">全选</el-button>
            <el-button text @click="$emit('clearSelection')">清空</el-button>
            <el-button :loading="loading" text @click="$emit('refresh')">刷新</el-button>
          </div>
        </div>
      </div>
    </template>

    <el-empty
      v-if="!loading && totalFilteredCount === 0"
      description="没有匹配的本地插件"
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
              @click="$emit('togglePlugin', plugin.id)"
            >
              <template v-if="layoutMode === 'list'">
                <div class="plugin-list-row">
                  <el-checkbox
                    :model-value="isSelected(plugin.id)"
                    @click.stop
                    @change="$emit('togglePlugin', plugin.id)"
                  />
                  <span class="plugin-list-row__name">{{ plugin.name }}</span>
                </div>
              </template>
              <template v-else>
                <div class="plugin-select-item__checkbox">
                  <el-checkbox
                    :model-value="isSelected(plugin.id)"
                    @click.stop
                    @change="$emit('togglePlugin', plugin.id)"
                  />
                </div>
                <PluginCard :plugin="plugin" :is-selected="isSelected(plugin.id)" />
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
import type { PluginMeta } from '@/types/api'

type LayoutMode = 'list' | 'single' | 'double' | 'compact'
type PluginGroupType = 'plugin' | 'adapter' | 'extension'
type FilterMode = 'whitelist' | 'blacklist'

type SelectablePlugin = PluginMeta & {
  type: PluginGroupType
  enabled?: boolean
  autoStart?: boolean
}

const props = defineProps<{
  loading: boolean
  totalCount: number
  selectedCount: number
  pluginFilter: string
  useRegex: boolean
  filterMode: FilterMode
  regexError: boolean
  selectedTypes: PluginGroupType[]
  layoutMode: LayoutMode
  pluginCount: number
  adapterCount: number
  extensionCount: number
  filteredPurePlugins: SelectablePlugin[]
  filteredAdapters: SelectablePlugin[]
  filteredExtensions: SelectablePlugin[]
  selectedPluginIds: string[]
}>()

defineEmits<{
  refresh: []
  selectAllVisible: []
  clearSelection: []
  togglePlugin: [pluginId: string]
  'update:pluginFilter': [value: string]
  'update:useRegex': [value: boolean]
  'update:filterMode': [value: FilterMode]
  'update:selectedTypes': [value: PluginGroupType[]]
  'update:layoutMode': [value: LayoutMode]
}>()

const { t } = useI18n()

const layoutClass = computed(() => `plugin-selector-grid--${props.layoutMode}`)
const totalFilteredCount = computed(
  () => props.filteredPurePlugins.length + props.filteredAdapters.length + props.filteredExtensions.length
)

const sections = computed(() => [
  { key: 'plugin', title: '插件', items: props.filteredPurePlugins, headerClass: '' },
  { key: 'adapter', title: '适配器', items: props.filteredAdapters, headerClass: 'section-header--adapter' },
  { key: 'extension', title: '扩展', items: props.filteredExtensions, headerClass: 'section-header--ext' },
])

function isSelected(pluginId: string): boolean {
  return props.selectedPluginIds.includes(pluginId)
}
</script>

<style scoped>
.selector-card {
  border-radius: 18px;
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
}

.selector-topline__meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.selector-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.selector-filter-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.selector-filter-switch {
  flex-shrink: 0;
}

.selector-filter-error {
  color: var(--el-color-danger);
  font-size: 12px;
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

.section-header--adapter,
.section-header--ext {
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
}

.plugin-select-item--list {
  border: 1px solid var(--el-border-color-light);
  border-radius: 10px;
  background: var(--el-bg-color);
  transition: all 0.2s ease;
}

.plugin-select-item:hover {
  transform: translateY(-2px);
}

.plugin-select-item__checkbox {
  position: absolute;
  top: 14px;
  right: 16px;
  z-index: 2;
  padding: 4px 6px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--el-bg-color) 88%, transparent);
  backdrop-filter: blur(6px);
}

.plugin-select-item :deep(.plugin-card) {
  height: 100%;
}

.plugin-select-item :deep(.plugin-card-header) {
  padding-right: 26px;
}

.plugin-list-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 42px;
  padding: 0 12px;
}

.plugin-list-row__name {
  font-size: 14px;
  color: var(--el-text-color-primary);
  line-height: 1.4;
  word-break: break-all;
}

@media (max-width: 980px) {
  .plugin-selector-grid--double,
  .plugin-selector-grid--compact {
    grid-template-columns: 1fr;
  }
}
</style>
