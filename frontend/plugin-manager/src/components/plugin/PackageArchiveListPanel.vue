<template>
  <el-card class="result-card">
    <template #header>
      <div class="card-header">
        <span>本地包</span>
        <div class="package-header-actions">
          <el-radio-group
            :model-value="packageFilterType"
            size="small"
            @update:model-value="$emit('update:packageFilterType', $event)"
          >
            <el-radio-button label="all">全部</el-radio-button>
            <el-radio-button label="plugin">插件包</el-radio-button>
            <el-radio-button label="bundle">整合包</el-radio-button>
          </el-radio-group>
          <el-tag size="small" type="info">{{ totalCount }}</el-tag>
          <el-button text :loading="loading" @click="$emit('refresh')">刷新</el-button>
        </div>
      </div>
    </template>

    <div v-if="targetDir" class="package-list-meta">
      <span class="package-list-meta__label">目录</span>
      <span class="package-list-meta__value">{{ targetDir }}</span>
    </div>

    <el-empty v-if="!loading && packages.length === 0" description="没有匹配的本地包" />

    <div v-else class="package-list">
      <TransitionGroup name="pkg-item" tag="div" class="package-list__inner">
        <button
          v-for="(pkg, index) in packages"
          :key="pkg.path"
          type="button"
          class="package-list-item"
          :class="{ 'package-list-item--active': activePackage === pkg.path || activePackage === pkg.name }"
          :style="{ '--item-i': index }"
          @click="$emit('select', pkg)"
        >
        <div class="package-list-item__main">
          <div class="package-list-item__title">
            <div class="package-list-item__name">{{ pkg.name }}</div>
            <el-tag size="small" effect="plain" :type="packageTagType(pkg)">
              {{ packageLabel(pkg) }}
            </el-tag>
          </div>
          <div class="package-list-item__meta">
            <span>{{ formatSize(pkg.size_bytes) }}</span>
            <span>{{ formatTime(pkg.modified_at) }}</span>
          </div>
        </div>

        <div class="package-list-item__actions">
          <el-button text @click.stop="$emit('inspect', pkg)">检查</el-button>
          <el-button text @click.stop="$emit('verify', pkg)">校验</el-button>
          <el-button text @click.stop="$emit('prepareUnpack', pkg)">解包</el-button>
        </div>
      </button>
      </TransitionGroup>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import type { PluginCliLocalPackageItem } from '@/api/pluginCli'

defineProps<{
  loading: boolean
  totalCount: number
  targetDir: string
  activePackage: string
  packageFilterType: 'all' | 'plugin' | 'bundle'
  packages: PluginCliLocalPackageItem[]
}>()

defineEmits<{
  refresh: []
  select: [pkg: PluginCliLocalPackageItem]
  inspect: [pkg: PluginCliLocalPackageItem]
  verify: [pkg: PluginCliLocalPackageItem]
  prepareUnpack: [pkg: PluginCliLocalPackageItem]
  'update:packageFilterType': [value: 'all' | 'plugin' | 'bundle']
}>()

function inferPackageType(pkg: PluginCliLocalPackageItem): 'plugin' | 'bundle' {
  return pkg.name.endsWith('.neko-bundle') ? 'bundle' : 'plugin'
}

function packageLabel(pkg: PluginCliLocalPackageItem): string {
  return inferPackageType(pkg) === 'bundle' ? '整合包' : '插件包'
}

function packageTagType(pkg: PluginCliLocalPackageItem): 'primary' | 'success' {
  return inferPackageType(pkg) === 'bundle' ? 'success' : 'primary'
}

function formatSize(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`
  if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} KB`
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatTime(raw: string): string {
  const date = new Date(raw)
  if (Number.isNaN(date.getTime())) return raw
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}
</script>

<style scoped>
.result-card {
  border-radius: 18px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.package-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.package-list-meta {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  word-break: break-all;
}

.package-list-meta__label {
  flex-shrink: 0;
}

.package-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.package-list__inner {
  display: flex;
  flex-direction: column;
  gap: 10px;
  position: relative;
}

.package-list-item {
  width: 100%;
  border: 1px solid var(--el-border-color-light);
  background: var(--el-bg-color);
  border-radius: 12px;
  padding: 12px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  text-align: left;
  cursor: pointer;
  transition:
    transform 0.24s ease,
    box-shadow 0.24s ease,
    border-color 0.24s ease,
    background-color 0.24s ease,
    opacity 0.24s ease;
}

.package-list-item:hover {
  transform: translateY(-2px);
  box-shadow: var(--el-box-shadow-light);
}

.package-list-item--active {
  border-color: var(--el-color-primary);
  background: color-mix(in srgb, var(--el-color-primary) 6%, var(--el-bg-color));
}

/* ── Package list item transitions ── */
.pkg-item-enter-active,
.pkg-item-leave-active {
  transition:
    transform 0.34s cubic-bezier(0.22, 1, 0.36, 1),
    opacity 0.24s ease,
    filter 0.24s ease;
}

.pkg-item-enter-active {
  transition-delay: calc(var(--item-i, 0) * 35ms);
}

.pkg-item-enter-from {
  opacity: 0;
  transform: scale(0.95) translateY(12px);
  filter: blur(6px);
}

.pkg-item-leave-to {
  opacity: 0;
  transform: scale(0.94) translateY(-12px);
  filter: blur(6px);
}

.pkg-item-enter-to,
.pkg-item-leave-from {
  opacity: 1;
  transform: scale(1) translateY(0);
  filter: blur(0);
}

.pkg-item-leave-active {
  position: absolute;
  z-index: 0;
  pointer-events: none;
}

.pkg-item-move {
  transition: transform 0.34s cubic-bezier(0.22, 1, 0.36, 1);
}

.package-list-item__main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.package-list-item__title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.package-list-item__name {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  word-break: break-all;
}

.package-list-item__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.package-list-item__actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

@media (max-width: 980px) {
  .package-list-item {
    flex-direction: column;
    align-items: flex-start;
  }

  .package-list-item__actions {
    width: 100%;
    justify-content: flex-start;
    flex-wrap: wrap;
  }
}
</style>
