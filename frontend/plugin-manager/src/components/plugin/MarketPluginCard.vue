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
          <!-- v2 (R8): yanked 红色徽章；当前装的版本被作者撤回时显示 -->
          <el-tooltip
            v-if="yanked"
            :content="yankReason || t('market.yankedDefault')"
            placement="top"
          >
            <el-tag size="small" type="danger" class="yank-badge">
              {{ t('market.yanked') }}
            </el-tag>
          </el-tooltip>
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
        <el-tag v-if="plugin.version" size="small" type="info">v{{ plugin.version }}</el-tag>
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
        <!-- v2 (R9.1 / R9.8): 已装且本地版本 < market 最新 → 显示 upgrade 按钮 -->
        <el-button
          v-if="showUpgrade"
          type="primary"
          size="small"
          :loading="upgrading"
          :disabled="upgrading"
          @click.stop="$emit('upgrade')"
        >
          {{ upgrading ? t('market.upgrading') : t('market.upgradeTo', { version: plugin.version }) }}
        </el-button>
        <!-- 已装且无新版可升 → 显示禁用的"已安装"按钮 -->
        <el-button
          v-else-if="installed"
          type="primary"
          size="small"
          disabled
        >
          {{ t('market.installed') }}
        </el-button>
        <!-- 未装 + Market 没有发布版本 → 禁用安装，文案"暂无可用版本" -->
        <el-button
          v-else-if="!plugin.has_release"
          type="primary"
          size="small"
          disabled
        >
          {{ t('market.noVersionAvailable') }}
        </el-button>
        <!-- 未装且 Market 有发布版本 → 正常 install -->
        <el-button
          v-else
          type="primary"
          size="small"
          :loading="installing"
          @click.stop="$emit('install')"
        >
          {{ installing ? t('market.installing') : t('market.install') }}
        </el-button>
      </div>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { User, Download } from '@element-plus/icons-vue'
import type { MarketPlugin } from '@/api/market'
import { compareVersion } from '@/utils/version'

interface Props {
  plugin: MarketPlugin
  installed?: boolean
  installing?: boolean
  /** v2: 本地已装版本；用于和 plugin.version 比较是否需要升级。 */
  localVersion?: string
  /** v2: 当前装的版本被 Market 作者撤回。 */
  yanked?: boolean
  /** v2: yanked 工具提示文案。 */
  yankReason?: string
  /** v2: upgrading 状态（按钮 loading + disabled）。 */
  upgrading?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  installed: false,
  installing: false,
  localVersion: undefined,
  yanked: false,
  yankReason: '',
  upgrading: false,
})

defineEmits<{
  click: []
  install: []
  upgrade: []
}>()

const { t } = useI18n()

/**
 * v2 (R9.1 / R9.8): 升级按钮显示条件 —— 已装、本地有版本号、Market 有版本号、
 * 且 semver 比较显示本地落后。任一条件失败即不显示。
 */
const showUpgrade = computed(() => {
  if (!props.installed) return false
  if (!props.localVersion || !props.plugin.version) return false
  if (!props.plugin.has_release) return false
  return compareVersion(props.localVersion, props.plugin.version) < 0
})

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
  /* v2: 已装态不再整体置灰 —— 升级按钮 / yanked 徽章需要清晰显示 */
  opacity: 1;
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

.yank-badge {
  /* el-tag type="danger" 已经是红色，加点描边强化"危险"语义 */
  border-color: var(--el-color-danger);
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
