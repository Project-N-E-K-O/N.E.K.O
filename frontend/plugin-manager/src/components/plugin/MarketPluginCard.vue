<template>
  <el-card
    class="plugin-card market-plugin-card"
    :class="{ 'market-plugin-card--installed': installed }"
    @click="$emit('click')"
  >
    <template #header>
      <div class="plugin-card-header">
        <div class="plugin-info">
          <el-tag v-if="plugin.is_recommended" size="small" type="warning" effect="plain" class="type-tag">
            推荐
          </el-tag>
          <h3 class="plugin-name">{{ plugin.name }}</h3>
          <el-tag v-if="installed" size="small" type="success">已安装</el-tag>
        </div>
      </div>
    </template>

    <div class="plugin-card-body">
      <p class="plugin-description">{{ plugin.description || '暂无描述' }}</p>

      <div class="plugin-meta">
        <el-tag size="small" type="info">v{{ plugin.version }}</el-tag>
        <span class="plugin-author">
          <el-icon><User /></el-icon>
          {{ plugin.author?.name || '未知' }}
        </span>
        <span class="plugin-downloads">
          <el-icon><Download /></el-icon>
          {{ formatCount(plugin.downloads) }}
        </span>
      </div>

      <div class="plugin-card-actions">
        <el-button
          type="primary"
          size="small"
          :loading="installing"
          :disabled="installed"
          @click.stop="$emit('install')"
        >
          {{ installed ? '已安装' : (installing ? '安装中...' : '安装') }}
        </el-button>
      </div>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { User, Download } from '@element-plus/icons-vue'
import type { MarketPlugin } from '@/api/market'

interface Props {
  plugin: MarketPlugin
  installed?: boolean
  installing?: boolean
}

withDefaults(defineProps<Props>(), {
  installed: false,
  installing: false,
})

defineEmits<{
  click: []
  install: []
}>()

function formatCount(count: number): string {
  if (count >= 1000) return `${(count / 1000).toFixed(1)}k`
  return String(count || 0)
}
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

.market-plugin-card--installed {
  opacity: 0.7;
}

.plugin-card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}

.plugin-info {
  display: flex;
  align-items: center;
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
  margin-top: 10px;
  padding-top: 10px;
  flex-wrap: wrap;
}

.plugin-author,
.plugin-downloads {
  display: flex;
  align-items: center;
  gap: 3px;
}

.plugin-card-actions {
  display: flex;
  justify-content: flex-end;
  padding-top: 12px;
  margin-top: auto;
}

.type-tag {
  flex-shrink: 0;
}
</style>
