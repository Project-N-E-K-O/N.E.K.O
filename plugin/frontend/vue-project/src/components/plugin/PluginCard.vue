<template>
  <el-card class="plugin-card" :class="{ 'plugin-card--selected': isSelected }" @click="$emit('click')">
    <template #header>
      <div class="plugin-card-header">
        <div class="plugin-info">
          <h3 class="plugin-name">{{ plugin.name }}</h3>
          <StatusIndicator :status="plugin.status" />
        </div>
      </div>
    </template>
    
    <div class="plugin-card-body">
      <p class="plugin-description">{{ plugin.description || '暂无描述' }}</p>
      <div class="plugin-meta">
        <el-tag size="small" type="info">v{{ plugin.version }}</el-tag>
        <span class="plugin-entries">入口点: {{ entryCount }}</span>
      </div>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import StatusIndicator from '@/components/common/StatusIndicator.vue'
import type { PluginMeta } from '@/types/api'

interface Props {
  plugin: PluginMeta & { status?: string }
  isSelected?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  isSelected: false
})

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
}

.plugin-entries {
  margin-left: auto;
}
</style>

