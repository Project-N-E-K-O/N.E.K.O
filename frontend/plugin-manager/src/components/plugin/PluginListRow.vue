<template>
  <el-card
    class="plugin-list-row-card"
    :class="{ 'plugin-list-row-card--selected': isSelected }"
    @click="$emit('click')"
    @contextmenu.prevent="$emit('contextmenu', $event)"
  >
    <div class="plugin-list-row-card__content">
      <div class="plugin-list-row-card__main">
        <div class="plugin-list-row-card__headline">
          <div class="plugin-list-row-card__heading-main">
            <el-tag size="small" effect="plain" :type="typeTagType">{{ typeLabel }}</el-tag>
            <h3 class="plugin-list-row-card__name">{{ plugin.name }}</h3>
            <StatusIndicator :status="plugin.status || 'stopped'" />
            <el-tag v-if="plugin.enabled === false && plugin.type !== 'extension'" size="small" type="info">
              {{ t('plugins.disabled') }}
            </el-tag>
            <el-tag v-else-if="plugin.autoStart === false && plugin.type !== 'extension'" size="small" type="warning">
              {{ t('plugins.manualStart') }}
            </el-tag>
          </div>
        </div>

        <p class="plugin-list-row-card__description">
          {{ plugin.description || t('common.noData') }}
        </p>

        <PluginMetricsInline
          v-if="showMetrics"
          :plugin-id="plugin.id"
          :plugin-status="plugin.status || 'stopped'"
        />
      </div>

      <div class="plugin-list-row-card__meta">
        <div class="plugin-list-row-card__meta-item">
          <span class="plugin-list-row-card__meta-label">ID</span>
          <span class="plugin-list-row-card__meta-value plugin-list-row-card__meta-value--code">{{ plugin.id }}</span>
        </div>
        <div class="plugin-list-row-card__meta-item">
          <span class="plugin-list-row-card__meta-label">Version</span>
          <span class="plugin-list-row-card__meta-value">v{{ plugin.version }}</span>
        </div>
        <div class="plugin-list-row-card__meta-item">
          <span class="plugin-list-row-card__meta-label">{{ t('plugins.entryPoint') }}</span>
          <span class="plugin-list-row-card__meta-value">{{ entryCount }}</span>
        </div>
        <div v-if="plugin.type === 'extension' && plugin.host_plugin_id" class="plugin-list-row-card__meta-item">
          <span class="plugin-list-row-card__meta-label">Host</span>
          <span class="plugin-list-row-card__meta-value plugin-list-row-card__meta-value--code">
            {{ plugin.host_plugin_id }}
          </span>
        </div>
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

defineEmits<{
  click: []
  contextmenu: [event: MouseEvent]
}>()

const { t } = useI18n()

const entryCount = computed(() => props.plugin.entries?.length || 0)

const typeLabel = computed(() => {
  if (props.plugin.type === 'adapter') return t('plugins.typeAdapter')
  if (props.plugin.type === 'extension') return t('plugins.typeExtension')
  return t('plugins.typePlugin')
})

const typeTagType = computed<'primary' | 'success' | 'warning'>(() => {
  if (props.plugin.type === 'adapter') return 'success'
  if (props.plugin.type === 'extension') return 'warning'
  return 'primary'
})
</script>

<style scoped>
.plugin-list-row-card {
  cursor: pointer;
  border-radius: var(--plugin-entry-radius, 18px);
  transition:
    transform 0.24s ease,
    box-shadow 0.24s ease,
    border-color 0.24s ease;
}

.plugin-list-row-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--el-box-shadow-light);
}

.plugin-list-row-card--selected {
  border-color: var(--el-color-primary);
  box-shadow:
    0 14px 28px color-mix(in srgb, var(--el-color-primary) 14%, transparent),
    0 6px 14px color-mix(in srgb, var(--el-text-color-primary) 8%, transparent);
}

.plugin-list-row-card__content {
  display: grid;
  grid-template-columns: minmax(0, 1.8fr) minmax(240px, 0.9fr);
  gap: 18px;
  align-items: center;
}

.plugin-list-row-card__main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.plugin-list-row-card__headline {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.plugin-list-row-card__heading-main {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  min-width: 0;
  flex: 1 1 auto;
}

.plugin-list-row-card__name {
  margin: 0;
  font-size: 16px;
  font-weight: 650;
  color: var(--el-text-color-primary);
  line-height: 1.35;
  word-break: break-word;
}

.plugin-list-row-card__description {
  margin: 0;
  color: var(--el-text-color-regular);
  font-size: 13px;
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.plugin-list-row-card__meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 16px;
  padding: 12px 14px;
  border-radius: 16px;
  background: color-mix(in srgb, var(--el-fill-color-light) 72%, white);
  border: 1px solid color-mix(in srgb, var(--el-color-primary) 8%, var(--el-border-color));
}

.plugin-list-row-card__meta-item {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.plugin-list-row-card__meta-label {
  font-size: 11px;
  font-weight: 600;
  line-height: 1.2;
  color: var(--el-text-color-secondary);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.plugin-list-row-card__meta-value {
  font-size: 13px;
  color: var(--el-text-color-primary);
  word-break: break-word;
}

.plugin-list-row-card__meta-value--code {
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 12px;
}

@media (max-width: 980px) {
  .plugin-list-row-card__content {
    grid-template-columns: 1fr;
  }

  .plugin-list-row-card__headline {
    align-items: flex-start;
  }

  .plugin-list-row-card__meta {
    grid-template-columns: 1fr;
  }
}
</style>
