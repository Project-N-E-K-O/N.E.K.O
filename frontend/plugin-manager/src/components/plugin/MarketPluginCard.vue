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
            {{ t('market.recommended') }}
          </el-tag>
          <h3 class="plugin-name">{{ plugin.name }}</h3>
          <el-tag v-if="installed" size="small" type="success">
            {{ t('market.installed') }}
          </el-tag>
        </div>
      </div>
    </template>

    <div class="plugin-card-body">
      <p class="plugin-description">
        {{ plugin.short_description || plugin.description || t('market.noDescription') }}
      </p>

      <div v-if="plugin.tags?.length" class="plugin-tags">
        <el-tag
          v-for="tag in plugin.tags.slice(0, 4)"
          :key="tag"
          size="small"
          type="info"
          effect="plain"
          class="plugin-tag"
        >
          {{ tag }}
        </el-tag>
        <span v-if="plugin.tags.length > 4" class="plugin-tags__more">
          +{{ plugin.tags.length - 4 }}
        </span>
      </div>

      <div class="plugin-meta">
        <el-tag size="small" type="info">v{{ plugin.version }}</el-tag>
        <span class="plugin-author">
          <el-icon><User /></el-icon>
          {{ plugin.author?.name || t('market.unknownAuthor') }}
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
          {{ installed ? t('market.installed') : (installing ? t('market.installing') : t('market.install')) }}
        </el-button>
      </div>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
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

const { t } = useI18n()

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

.plugin-tags {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin-top: 10px;
}

.plugin-tag {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.plugin-tags__more {
  font-size: 11px;
  color: var(--el-text-color-secondary);
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
