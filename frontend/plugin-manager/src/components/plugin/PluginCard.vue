<template>
  <el-card
    class="plugin-card"
    :class="{ 'plugin-card--selected': isSelected }"
    @click="$emit('click')"
    @contextmenu.prevent="$emit('contextmenu', $event)"
  >
    <template #header>
      <div class="plugin-card-header">
        <div class="plugin-info">
          <el-tag v-if="plugin.type === 'extension'" size="small" type="primary" effect="plain" class="type-tag">
            {{ t('plugins.extension') }}
          </el-tag>
          <h3 class="plugin-name">{{ plugin.name }}</h3>
          <StatusIndicator :status="plugin.status || 'stopped'" />
          <el-tag v-if="plugin.enabled === false && plugin.type !== 'extension'" size="small" type="info">
            {{ t('plugins.disabled') }}
          </el-tag>
          <el-tag v-else-if="plugin.autoStart === false && plugin.type !== 'extension'" size="small" type="warning">
            {{ t('plugins.manualStart') }}
          </el-tag>
        </div>
      </div>
    </template>

    <div class="plugin-card-body">
      <p class="plugin-description">{{ plugin.description || t('common.noData') }}</p>

      <PluginMetricsInline
        v-if="showMetrics"
        :plugin-id="plugin.id"
        :plugin-status="plugin.status || 'stopped'"
      />

      <div class="plugin-meta">
        <el-tag size="small" type="info">v{{ plugin.version }}</el-tag>
        <span v-if="plugin.type === 'extension' && plugin.host_plugin_id" class="plugin-host">
          → {{ plugin.host_plugin_id }}
        </span>
        <span class="plugin-entries">{{ t('plugins.entryPoint') }}: {{ entryCount }}</span>
      </div>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import StatusIndicator from '@/components/common/StatusIndicator.vue'
import PluginMetricsInline from '@/components/plugin/PluginMetricsInline.vue'
import type { PluginMeta } from '@/types/api'

interface Props {
  plugin: PluginMeta & { status?: string; enabled?: boolean; autoStart?: boolean; type?: string; host_plugin_id?: string }
  isSelected?: boolean
  showMetrics?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  isSelected: false,
  showMetrics: false,
})

const { t } = useI18n()

defineEmits<{
  click: []
  contextmenu: [event: MouseEvent]
}>()

const entryCount = computed(() => {
  return props.plugin.entries?.length || 0
})
</script>

<style scoped>
.plugin-card {
  cursor: pointer;
  border-radius: var(--plugin-entry-radius, 16px);
  transition:
    transform 0.24s ease,
    box-shadow 0.24s ease,
    border-color 0.24s ease;
}

.plugin-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--el-box-shadow);
}

.plugin-card--selected {
  border-color: var(--el-color-primary);
}

.plugin-card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}

.plugin-info {
  display: flex;
  align-items: center;
  align-content: flex-start;
  gap: 10px;
  flex-wrap: wrap;
  min-width: 0;
  flex: 1 1 auto;
}

.plugin-name {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  line-height: 1.35;
  word-break: break-word;
}

.plugin-card-body {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.plugin-description {
  margin: 0;
  color: var(--el-text-color-regular);
  font-size: 14px;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.plugin-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: auto;
  padding-top: 10px;
}

.plugin-entries {
  margin-left: auto;
}

.plugin-host {
  color: var(--el-color-primary);
  font-size: 12px;
  white-space: nowrap;
}

.type-tag {
  flex-shrink: 0;
}

@media (max-width: 640px) {
  .plugin-info {
    align-items: flex-start;
  }
}
</style>
