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
          <h3 class="plugin-name">{{ displayText.name }}</h3>
          <StatusIndicator :status="plugin.status || 'stopped'" />
          <el-tag v-if="plugin.autoStart === false" size="small" type="warning">
            {{ t('plugins.manualStart') }}
          </el-tag>
        </div>
      </div>
    </template>

    <div class="plugin-card-body">
      <p class="plugin-description">{{ displayText.description || t('common.noData') }}</p>

      <PluginMetricsInline
        v-if="showMetrics"
        :plugin-id="plugin.id"
        :plugin-status="plugin.status || 'stopped'"
      />

      <SourceDetailRow
        v-if="showSourceDetail"
        :install-source="plugin.install_source"
        :latest-version="latestVersion"
      />

      <footer class="plugin-card-footer">
        <div class="plugin-card-footer__main">
          <el-tag size="small" type="info" class="plugin-version-tag" :title="`v${plugin.version}`">
            <span class="plugin-version-tag__label">v{{ plugin.version }}</span>
          </el-tag>
          <SourceTag
            :source="plugin.install_source?.source"
            :has-update="hasUpdate"
            compact
          />
        </div>
        <span class="plugin-entries">{{ t('plugins.entryPoint') }}: {{ entryCount }}</span>
      </footer>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import StatusIndicator from '@/components/common/StatusIndicator.vue'
import PluginMetricsInline from '@/components/plugin/PluginMetricsInline.vue'
import SourceTag from '@/components/plugin/SourceTag.vue'
import SourceDetailRow from '@/components/plugin/SourceDetailRow.vue'
import { useMarketVersionsStore } from '@/stores/marketVersions'
import { hasNewerVersion } from '@/utils/version'
import { resolvePluginDisplayText } from '@/utils/pluginDisplay'
import type { PluginMeta, PluginInstallSourceDetailMarket } from '@/types/api'

interface Props {
  plugin: PluginMeta & { status?: string; enabled?: boolean; autoStart?: boolean; type?: string }
  isSelected?: boolean
  showMetrics?: boolean
  showSourceDetail?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  isSelected: false,
  showMetrics: false,
  showSourceDetail: false,
})

const { t, locale } = useI18n()
const marketVersions = useMarketVersionsStore()

defineEmits<{
  click: []
  contextmenu: [event: MouseEvent]
}>()

const entryCount = computed(() => {
  return props.plugin.entries?.length || 0
})

const displayText = computed(() => resolvePluginDisplayText(props.plugin, locale.value))

/** Look up the market's latest version for this plugin, IF it was installed
 *  from the market. Returns null for non-market / unknown plugins. Callers
 *  (PluginList) kick off the market refresh when they turn the "show source
 *  detail" toggle on, so by the time we render here the store is populated
 *  (or never will be, if market is offline — in which case latest stays
 *  null and no "update available" badge appears). */
const latestVersion = computed<string | null>(() => {
  const src = props.plugin.install_source
  if (!src || src.source !== 'market') return null
  const detail = src.source_detail as PluginInstallSourceDetailMarket | null
  if (!detail?.plugin_market_id) return null
  return marketVersions.latest(detail.plugin_market_id, detail.channel)
})

const hasUpdate = computed<boolean>(() => {
  const src = props.plugin.install_source
  if (!src || src.source !== 'market') return false
  const detail = src.source_detail as PluginInstallSourceDetailMarket | null
  return hasNewerVersion(detail?.version, latestVersion.value)
})
</script>

<style scoped>
.plugin-card {
  container-type: inline-size;
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
  min-width: 0;
  min-height: 0;
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

.plugin-card-footer {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: auto;
  padding-top: 10px;
  min-width: 0;
}

.plugin-card-footer__main {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  min-width: 0;
}

.plugin-version-tag {
  flex: 0 1 auto;
  min-width: 0;
  max-width: 100%;
}

.plugin-version-tag :deep(.el-tag__content) {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.plugin-version-tag__label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.plugin-entries {
  justify-self: end;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.plugin-host {
  color: var(--el-color-primary);
  font-size: 12px;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
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

@container (max-width: 220px) {
  .plugin-card-footer {
    grid-template-columns: 1fr;
    align-items: start;
  }

  .plugin-entries {
    justify-self: start;
  }
}
</style>
