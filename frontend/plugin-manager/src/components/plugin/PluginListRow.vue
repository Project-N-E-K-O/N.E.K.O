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
            <h3 class="plugin-list-row-card__name">
              <button
                type="button"
                class="plugin-list-row-card__name-button"
                data-testid="plugin-details-button"
                @click.stop="$emit('click')"
              >{{ displayText.name }}</button>
            </h3>
            <StatusIndicator :status="plugin.status || 'stopped'" />
            <PluginAutoStartBadge v-if="plugin.autoStart === true" />
            <SourceTag
              :source="plugin.install_source?.source"
              :has-update="hasUpdate"
            />
          </div>
          <el-button
            v-if="availableUiAction"
            data-testid="plugin-open-ui"
            class="plugin-list-row-card__ui-action"
            size="small"
            type="primary"
            plain
            @click.stop="$emit('open-ui', availableUiAction)"
          >
            {{ t('plugins.ui.open') }}
          </el-button>
        </div>

        <p class="plugin-list-row-card__description">
          {{ displayText.description || t('common.noData') }}
        </p>

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
      </div>

      <div class="plugin-list-row-card__meta">
        <div class="plugin-list-row-card__meta-item">
          <span class="plugin-list-row-card__meta-label">ID</span>
          <span class="plugin-list-row-card__meta-value plugin-list-row-card__meta-value--code">{{ plugin.id }}</span>
        </div>
        <div class="plugin-list-row-card__meta-item">
          <span class="plugin-list-row-card__meta-label">{{ t('plugins.version') }}</span>
          <span class="plugin-list-row-card__meta-value">v{{ plugin.version }}</span>
        </div>
      </div>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import StatusIndicator from '@/components/common/StatusIndicator.vue'
import PluginAutoStartBadge from './PluginAutoStartBadge.vue'
import PluginMetricsInline from '@/components/plugin/PluginMetricsInline.vue'
import SourceTag from '@/components/plugin/SourceTag.vue'
import SourceDetailRow from '@/components/plugin/SourceDetailRow.vue'
import { useMarketVersionsStore } from '@/stores/marketVersions'
import { hasNewerVersion } from '@/utils/version'
import { resolvePluginDisplayText } from '@/utils/pluginDisplay'
import { isOpenUiNavigationAction } from '@/utils/pluginListActions'
import type { PluginListAction, PluginMeta, PluginInstallSourceDetailMarket } from '@/types/api'

interface Props {
  plugin: PluginMeta & { status?: string; enabled?: boolean; autoStart?: boolean; type?: string }
  isSelected?: boolean
  showMetrics?: boolean
  showSourceDetail?: boolean
  enableUiAction?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  isSelected: false,
  showMetrics: false,
  showSourceDetail: false,
  enableUiAction: false,
})

defineEmits<{
  click: []
  contextmenu: [event: MouseEvent]
  'open-ui': [action: PluginListAction]
}>()

const { t, locale } = useI18n()
const marketVersions = useMarketVersionsStore()

const displayText = computed(() => resolvePluginDisplayText(props.plugin, locale.value))
const availableUiAction = computed(() => {
  if (!props.enableUiAction) return null
  return props.plugin.list_actions?.find((action) => {
    if (!isOpenUiNavigationAction(action) || action.disabled) return false
    return !action.requires_running || props.plugin.status === 'running'
  }) || null
})

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

const typeLabel = computed(() => {
  if (props.plugin.type === 'adapter') return t('plugins.typeAdapter')
  return t('plugins.typePlugin')
})

const typeTagType = computed<'primary' | 'success' | 'warning'>(() => {
  if (props.plugin.type === 'adapter') return 'success'
  return 'primary'
})
</script>

<style scoped>
.plugin-list-row-card {
  cursor: pointer;
  border-radius: var(--plugin-entry-radius, var(--radius-card));
  background: var(--surface-card);
  border-color: var(--surface-border);
  box-shadow: var(--surface-shadow);
  transition:
    transform var(--motion-duration-normal) var(--motion-ease-standard),
    box-shadow var(--motion-duration-normal) var(--motion-ease-standard),
    border-color var(--motion-duration-normal) var(--motion-ease-standard);
}

.plugin-list-row-card:hover {
  transform: translateY(-2px);
  border-color: color-mix(in srgb, var(--el-color-primary) 36%, var(--surface-border));
  box-shadow: var(--surface-shadow-hover);
}

.plugin-list-row-card--selected,
.plugin-list-row-card--selected:hover {
  border-color: var(--el-color-primary);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--el-color-primary) 14%, transparent);
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

.plugin-list-row-card__name-button {
  display: block;
  max-width: 100%;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: inherit;
  font: inherit;
  line-height: inherit;
  text-align: left;
  cursor: pointer;
}

.plugin-list-row-card__name-button:focus-visible,
.plugin-list-row-card__ui-action:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 3px;
}

.plugin-list-row-card:focus-within {
  border-color: color-mix(in srgb, var(--el-color-primary) 36%, var(--surface-border));
}

.plugin-list-row-card__ui-action {
  flex-shrink: 0;
  margin-left: 0;
}

.plugin-list-row-card__meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 16px;
  padding: 12px 14px;
  border-radius: var(--radius-panel);
  background: var(--surface-inset);
  border: 1px solid var(--surface-border);
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

@media (prefers-reduced-motion: reduce) {
  .plugin-list-row-card {
    transition: none;
  }

  .plugin-list-row-card:hover {
    transform: none;
  }
}
</style>
