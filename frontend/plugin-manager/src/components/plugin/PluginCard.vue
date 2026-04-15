<template>
  <el-card class="plugin-card" :class="{ 'plugin-card--selected': isSelected }" @click="$emit('click')">
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
      <!-- 显示性能指标模式 -->
      <Transition name="fade-slide" mode="out-in">
        <div v-if="showMetrics" key="metrics" class="plugin-metrics-wrapper">
          <PluginMetricsInline
            :key="`metrics-${plugin.id}`"
            :plugin-id="plugin.id"
            class="plugin-metrics-content"
          />
        </div>
        <!-- 默认模式：显示描述和元数据 -->
        <div v-else key="default" class="plugin-default-content">
          <p class="plugin-description">{{ plugin.description || t('common.noData') }}</p>
          <div class="plugin-meta">
            <el-tag size="small" type="info">v{{ plugin.version }}</el-tag>
            <span v-if="plugin.type === 'extension' && plugin.host_plugin_id" class="plugin-host">
              → {{ plugin.host_plugin_id }}
            </span>
            <span class="plugin-entries">{{ t('plugins.entryPoint') }}: {{ entryCount }}</span>
          </div>
        </div>
      </Transition>
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
  showMetrics: false
})

const { t } = useI18n()

defineEmits<{
  click: []
}>()

const entryCount = computed(() => {
  return props.plugin.entries?.length || 0
})
</script>

<style scoped>
.plugin-card {
  cursor: pointer;
  transition: all 0.3s ease;
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
  align-items: center;
}

.plugin-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.plugin-name {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.plugin-card-body {
  margin-top: 12px;
  flex: 1;
  display: flex;
  flex-direction: column;
}

.plugin-description {
  margin: 0 0 12px 0;
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
  margin-top: auto; /* 将元数据推到底部 */
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

.plugin-metrics-wrapper {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0; /* 确保 flex 子元素可以收缩 */
}

.plugin-default-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.plugin-metrics-content {
  margin-top: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-height: 0; /* 确保可以正确渲染 */
}

/* 淡入淡出 + 滑动动画 */
.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.fade-slide-enter-from {
  opacity: 0;
  transform: translateY(-10px);
}

.fade-slide-leave-to {
  opacity: 0;
  transform: translateY(10px);
}

.fade-slide-enter-to,
.fade-slide-leave-from {
  opacity: 1;
  transform: translateY(0);
}
</style>

