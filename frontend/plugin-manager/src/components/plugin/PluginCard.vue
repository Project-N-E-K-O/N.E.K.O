<template>
  <article
    class="plugin-card"
    :class="{ 'plugin-card--selected': isSelected, 'plugin-card--shared-rows': sharedRows }"
    @click="$emit('click')"
    @contextmenu.prevent="$emit('contextmenu', $event)"
  >
    <header class="plugin-card-header">
      <div class="plugin-heading">
        <h3 class="plugin-name">
          <button
            type="button"
            class="plugin-name__button"
            data-testid="plugin-details-button"
            @click.stop="$emit('click')"
          >{{ displayText.name }}</button>
        </h3>
        <span
          v-if="showIdentity"
          class="plugin-identity"
          data-testid="plugin-identity"
        >ID: {{ plugin.id }}</span>
      </div>
      <div class="plugin-statuses">
        <StatusIndicator :status="plugin.status || 'stopped'" />
        <PluginAutoStartBadge v-if="plugin.autoStart === true" />
      </div>
    </header>

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
    </div>

    <footer class="plugin-card-footer">
      <div class="plugin-card-footer__main">
        <el-tag size="small" type="info" class="plugin-version-tag" :title="`v${plugin.version}`">
          <span class="plugin-version-tag__label">v{{ plugin.version }}</span>
        </el-tag>
        <SourceTag :source="plugin.install_source?.source" :has-update="hasUpdate" compact />
      </div>
      <el-button
        v-if="availableUiAction"
        data-testid="plugin-open-ui"
        class="plugin-ui-action"
        size="small"
        type="primary"
        plain
        @click.stop="$emit('open-ui', availableUiAction)"
      >{{ t('plugins.ui.open') }}</el-button>
    </footer>
  </article>
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
  showIdentity?: boolean
  sharedRows?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  isSelected: false,
  showMetrics: false,
  showSourceDetail: false,
  enableUiAction: false,
  showIdentity: false,
  sharedRows: false,
})

const { t, locale } = useI18n()
const marketVersions = useMarketVersionsStore()

defineEmits<{
  click: []
  contextmenu: [event: MouseEvent]
  'open-ui': [action: PluginListAction]
}>()

const displayText = computed(() => resolvePluginDisplayText(props.plugin, locale.value))
const availableUiAction = computed(() => {
  if (!props.enableUiAction) return null
  return props.plugin.list_actions?.find((action) => {
    if (!isOpenUiNavigationAction(action) || action.disabled) return false
    return !action.requires_running || props.plugin.status === 'running'
  }) || null
})

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
  --plugin-card-control-height: 22px;
  display: grid;
  grid-template-rows: auto 1fr auto;
  gap: 0;
  min-width: 0;
  cursor: pointer;
  border: 1px solid var(--surface-border);
  border-radius: var(--plugin-entry-radius, var(--radius-card));
  background: var(--surface-card);
  box-shadow: var(--surface-shadow);
  transition:
    transform var(--motion-duration-normal) var(--motion-ease-standard),
    box-shadow var(--motion-duration-normal) var(--motion-ease-standard),
    border-color var(--motion-duration-normal) var(--motion-ease-standard);
}

/* Three tracks: header (name + state), body, footer.
 * Only grids that opt in share these tracks with their neighbouring cards. */
@supports (grid-template-rows: subgrid) {
  .plugin-card--shared-rows {
    grid-row: 1 / -1;
    grid-template-rows: subgrid;
  }
}

.plugin-card:hover {
  transform: translateY(-2px);
  border-color: color-mix(in srgb, var(--el-color-primary) 36%, var(--surface-border));
  box-shadow: var(--surface-shadow-hover);
}

.plugin-card--selected,
.plugin-card--selected:hover {
  border-color: var(--el-color-primary);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--el-color-primary) 14%, transparent);
}

.plugin-card-header {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  padding: 16px 16px 0;
}

.plugin-heading {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  min-width: 0;
}

.plugin-name {
  margin: 0;
  font-size: 1rem;
  font-weight: 600;
  color: var(--el-text-color-primary);
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.plugin-name__button {
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
  overflow-wrap: inherit;
  cursor: pointer;
}

.plugin-name__button:focus-visible,
.plugin-ui-action:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 3px;
}

.plugin-card:focus-within {
  border-color: color-mix(in srgb, var(--el-color-primary) 36%, var(--surface-border));
}

.plugin-statuses {
  display: flex;
  align-items: center;
  align-content: start;
  flex-wrap: wrap;
  gap: 4px 8px;
  min-width: 0;
}

.plugin-statuses :deep(.status-indicator) {
  min-width: 0;
  max-width: 100%;
  overflow-wrap: anywhere;
}

.plugin-identity {
  color: var(--el-text-color-secondary);
  font-size: 0.75rem;
  line-height: 1.4;
  overflow-wrap: anywhere;
}

.plugin-identity {
  max-width: 100%;
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 0.6875rem;
}

.plugin-card-body {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 12px 16px 0;
}

.plugin-card-body :deep(.metrics-cell),
.plugin-card-body :deep(.source-cell) {
  min-width: 0;
  max-width: 100%;
  flex-wrap: wrap;
  line-height: 1.4;
}

.plugin-card-body :deep(.metrics-cell__value),
.plugin-card-body :deep(.source-cell__value) {
  min-width: 0;
  overflow-wrap: anywhere;
}

.plugin-card-body :deep(.source-cell__value--mono) {
  white-space: normal;
}

.plugin-description {
  margin: 0;
  color: var(--el-text-color-regular);
  font-size: 0.875rem;
  line-height: 1.5;
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* Metadata and the optional action share one line and wrap only when they
 * cannot fit. The footer sits at the bottom of its shared track, so a card
 * without an action keeps the same bottom inset as its neighbours. */
.plugin-card-footer {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  align-self: end;
  gap: 8px;
  padding: 12px 16px 16px;
  min-width: 0;
  font-size: 0.75rem;
  color: var(--el-text-color-secondary);
}

.plugin-card-footer__main {
  display: flex;
  align-items: center;
  gap: 4px 8px;
  flex-wrap: wrap;
  min-width: 0;
  max-width: 100%;
}

.plugin-ui-action {
  margin: 0 0 0 auto;
  max-width: 100%;
  height: auto;
  min-height: var(--plugin-card-control-height);
  padding: 2px 10px;
  font-size: 0.75rem;
  line-height: 1.4;
  white-space: normal;
  overflow-wrap: anywhere;
  transition:
    color var(--motion-duration-normal) var(--motion-ease-standard),
    background-color var(--motion-duration-normal) var(--motion-ease-standard),
    border-color var(--motion-duration-normal) var(--motion-ease-standard);
}

.plugin-ui-action :deep(> span) {
  min-width: 0;
}

.plugin-version-tag,
.plugin-card-footer :deep(.source-tag),
.plugin-statuses :deep(.status-tag) {
  max-width: 100%;
  height: auto;
  min-height: var(--plugin-card-control-height);
  padding-block: 1px;
  font-size: 0.75rem;
  line-height: 1.4;
  white-space: normal;
}

.plugin-version-tag,
.plugin-version-tag :deep(.el-tag__content),
.plugin-version-tag__label {
  min-width: 0;
  overflow-wrap: anywhere;
}

@media (prefers-reduced-motion: reduce) {
  .plugin-card,
  .plugin-ui-action {
    transition: none;
  }

  .plugin-card:hover {
    transform: none;
  }
}
</style>
