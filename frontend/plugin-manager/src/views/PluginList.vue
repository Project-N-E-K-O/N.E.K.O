<template>
  <div class="plugin-workbench" :class="{ 'plugin-workbench--package-open': packagePanelVisible }">
    <section class="plugin-workbench__main">
      <el-card class="plugin-list-card">
        <template #header>
          <div class="workbench-header">
            <div class="workbench-header__copy">
              <div class="selection-toolbar" :class="{ 'selection-toolbar--active': multiSelectEnabled }">
                <el-button
                  class="selection-toolbar__trigger"
                  :type="multiSelectEnabled ? 'primary' : 'default'"
                  plain
                  @click="toggleMultiSelectMode"
                >
                  <span class="selection-toolbar__trigger-dot" aria-hidden="true"></span>
                  {{ multiSelectEnabled ? $t('plugins.exitMultiSelect') : $t('plugins.multiSelect') }}
                </el-button>
                <div
                  class="selection-toolbar__expanded"
                  :class="{ 'selection-toolbar__expanded--active': multiSelectEnabled }"
                >
                  <el-tag class="selection-toolbar__count" size="small" type="info">
                    <span class="selection-toolbar__count-label">
                      {{ $t('plugins.selectedCount', { count: selectedCount }) }}
                    </span>
                  </el-tag>
                  <el-button
                    class="selection-toolbar__action"
                    text
                    :tabindex="multiSelectEnabled ? 0 : -1"
                    @click="selectAllVisible"
                  >
                    {{ $t('plugins.selectAllVisible') }}
                  </el-button>
                  <el-button
                    class="selection-toolbar__action"
                    text
                    :tabindex="multiSelectEnabled ? 0 : -1"
                    @click="invertVisibleSelection"
                  >
                    {{ $t('plugins.invertVisibleSelection') }}
                  </el-button>
                  <el-button
                    class="selection-toolbar__action selection-toolbar__action--danger"
                    text
                    :tabindex="multiSelectEnabled ? 0 : -1"
                    @click="clearSelection"
                  >
                    {{ $t('plugins.clearSelection') }}
                  </el-button>
                </div>
              </div>
            </div>

            <div class="header-actions">
              <el-button
                :type="packagePanelVisible ? 'primary' : 'default'"
                plain
                @click="togglePackagePanel"
              >
                {{ packagePanelVisible ? $t('plugins.closePackageManager') : $t('plugins.openPackageManager') }}
              </el-button>
              <el-button
                :type="showMetrics ? 'success' : 'default'"
                :icon="DataAnalysis"
                @click="toggleMetrics"
              >
                {{ showMetrics ? $t('plugins.hideMetrics') : $t('plugins.showMetrics') }}
              </el-button>
              <el-button
                type="warning"
                :icon="RefreshRight"
                :loading="reloadingAll"
                :disabled="runningPlugins.length === 0"
                @click="handleReloadAll"
              >
                {{ $t('plugins.reloadAll') }}
              </el-button>
              <el-button type="primary" :icon="Refresh" :loading="loading" @click="handleRefresh">
                {{ $t('common.refresh') }}
              </el-button>
            </div>
          </div>

          <div class="filter-bar" @mouseenter="showFilter" @mouseleave="scheduleHideFilter">
            <Transition name="filter-fade" mode="out-in">
              <div v-if="filterVisible" key="controls" class="filter-controls">
                <el-popover
                  v-model:visible="filterRulesVisible"
                  placement="bottom-start"
                  :width="360"
                  trigger="click"
                  popper-class="filter-rules-popover"
                >
                  <template #reference>
                    <el-button class="filter-rules-trigger" plain>
                      <el-icon><Operation /></el-icon>
                      {{ $t('plugins.filterRules') }}
                    </el-button>
                  </template>

                  <div class="filter-rules-panel">
                    <div class="filter-rules-panel__header">
                      <div class="filter-rules-panel__title">{{ $t('plugins.filterRulesTitle') }}</div>
                      <div class="filter-rules-panel__hint">{{ $t('plugins.filterRulesHint') }}</div>
                    </div>

                    <div
                      v-for="group in filterRuleGroups"
                      :key="group.key"
                      class="filter-rules-group"
                    >
                      <div class="filter-rules-group__title">{{ group.title }}</div>
                      <div class="filter-rules-group__list">
                        <button
                          v-for="rule in group.rules"
                          :key="rule.token"
                          type="button"
                          class="filter-rule-chip"
                          @click="appendFilterRule(rule.token)"
                        >
                          <span class="filter-rule-chip__token">{{ rule.token }}</span>
                          <span class="filter-rule-chip__label">{{ rule.label }}</span>
                        </button>
                      </div>
                    </div>
                  </div>
                </el-popover>

                <el-input
                  ref="filterInputRef"
                  v-model="filterText"
                  clearable
                  class="filter-input"
                  :placeholder="$t('plugins.filterPlaceholder')"
                />
                <el-switch
                  v-model="useRegex"
                  class="filter-switch"
                  active-text="Regex"
                  inactive-text="Text"
                />
                <el-radio-group v-model="filterMode" size="small" class="filter-mode">
                  <el-radio-button label="whitelist">{{ $t('plugins.filterWhitelist') }}</el-radio-button>
                  <el-radio-button label="blacklist">{{ $t('plugins.filterBlacklist') }}</el-radio-button>
                </el-radio-group>
                <span v-if="regexError" class="filter-error">{{ $t('plugins.invalidRegex') }}</span>
              </div>
              <span v-else key="placeholder" class="filter-placeholder">
                {{ $t('plugins.hoverToShowFilter') }}
              </span>
            </Transition>
          </div>

          <div class="workbench-toolbar">
            <div class="type-filter-bar">
              <el-checkbox-group v-model="selectedTypes" class="type-filter-group">
                <el-checkbox-button label="plugin">
                  <el-icon><Box /></el-icon>
                  {{ $t('plugins.typePlugin') }} ({{ pluginCount }})
                </el-checkbox-button>
                <el-checkbox-button label="adapter">
                  <el-icon><Connection /></el-icon>
                  {{ $t('plugins.typeAdapter') }} ({{ adapterCount }})
                </el-checkbox-button>
                <el-checkbox-button label="extension">
                  <el-icon><Expand /></el-icon>
                  {{ $t('plugins.typeExtension') }} ({{ extensionCount }})
                </el-checkbox-button>
              </el-checkbox-group>
            </div>

            <div class="layout-toolbar">
              <span class="layout-toolbar__label">视图</span>
              <el-radio-group v-model="layoutMode" size="small">
                <el-radio-button label="list">列表</el-radio-button>
                <el-radio-button label="single">单排</el-radio-button>
                <el-radio-button label="double">双排</el-radio-button>
                <el-radio-button label="compact">紧凑</el-radio-button>
              </el-radio-group>
            </div>
          </div>
        </template>

        <LoadingSpinner
          v-if="loading && rawPlugins.length === 0"
          :loading="true"
          :text="$t('common.loading')"
        />
        <EmptyState v-else-if="rawPlugins.length === 0" :description="$t('plugins.noPlugins')" />

        <template v-else>
          <PluginGridSection
            v-for="section in pluginSections"
            :key="section.key"
            :title="section.title"
            :icon="section.icon"
            :items="section.items"
            :layout-mode="layoutMode"
            :multi-select-enabled="multiSelectEnabled"
            :selected-plugin-ids="selectedPluginIds"
            :show-metrics="showMetrics"
            :variant="section.variant"
            @item-click="handlePluginPrimaryAction"
            @item-contextmenu="handlePluginContextMenu"
            @toggle-selection="togglePluginSelection"
          />
        </template>
      </el-card>
    </section>

    <aside class="plugin-workbench__side" :class="{ 'plugin-workbench__side--visible': packagePanelVisible }">
      <PackageManagerPanel embedded @close="closePackagePanel" />
    </aside>

    <PluginContextMenu
      :visible="contextMenuVisible"
      :x="contextMenuPosition.x"
      :y="contextMenuPosition.y"
      :actions="contextMenuActions"
      @close="closePluginContextMenu"
      @select="handleContextActionSelect"
    />

    <PluginDangerConfirmDialog
      :visible="dangerDialogVisible"
      :loading="dangerDialogLoading"
      :title="t('plugins.dangerDialog.title')"
      :message="pendingDangerAction?.confirm_message || t('plugins.dangerDialog.deleteMessage', {
        pluginName: pendingDangerPlugin?.name || pendingDangerPlugin?.id || '',
      })"
      :hint="t('plugins.dangerDialog.hint')"
      :action-label="pendingDangerAction?.label || t('plugins.delete')"
      :warning-title="t('plugins.dangerDialog.warningTitle')"
      :cancel-label="t('common.cancel')"
      :loading-label="t('plugins.dangerDialog.loading')"
      :hold-idle-label="t('plugins.dangerDialog.holdIdle')"
      :hold-active-label="t('plugins.dangerDialog.holdActive')"
      @close="closeDangerDialog"
      @confirm="handleDangerActionConfirm"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Refresh, DataAnalysis, RefreshRight, Box, Connection, Expand, Operation } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { usePluginStore } from '@/stores/plugin'
import { useMetricsStore } from '@/stores/metrics'
import PluginGridSection from '@/components/plugin/PluginGridSection.vue'
import PluginContextMenu from '@/components/plugin/PluginContextMenu.vue'
import PluginDangerConfirmDialog from '@/components/plugin/PluginDangerConfirmDialog.vue'
import PackageManagerPanel from '@/components/plugin/PackageManagerPanel.vue'
import LoadingSpinner from '@/components/common/LoadingSpinner.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { reloadAllPlugins } from '@/api/plugins'
import { usePluginListContextActions, type ResolvedPluginListAction } from '@/composables/usePluginListContextActions'
import { usePluginWorkbench } from '@/composables/usePluginWorkbench'
import { METRICS_REFRESH_INTERVAL } from '@/utils/constants'
import { useI18n } from 'vue-i18n'
import type { PluginMeta } from '@/types/api'

const route = useRoute()
const router = useRouter()
const pluginStore = usePluginStore()
const metricsStore = useMetricsStore()
const { t } = useI18n()
const { buildActions, executeAction, shouldUseHoldConfirm } = usePluginListContextActions()

const reloadingAll = ref(false)
const packagePanelVisible = ref(false)
const contextMenuVisible = ref(false)
const contextMenuPosition = ref({ x: 0, y: 0 })
const contextMenuPlugin = ref<(PluginMeta & { status?: string; enabled?: boolean; autoStart?: boolean }) | null>(null)
const contextMenuActions = ref<ResolvedPluginListAction[]>([])
const dangerDialogVisible = ref(false)
const dangerDialogLoading = ref(false)
const pendingDangerAction = ref<ResolvedPluginListAction | null>(null)
const pendingDangerPlugin = ref<(PluginMeta & { status?: string; enabled?: boolean; autoStart?: boolean }) | null>(null)

const rawPlugins = computed(() => pluginStore.pluginsWithStatus)
const rawNormalPlugins = computed(() => pluginStore.normalPlugins)
const {
  filterText,
  useRegex,
  filterMode,
  selectedTypes,
  layoutMode,
  selectedCount,
  multiSelectEnabled,
  regexError,
  pluginCount,
  adapterCount,
  extensionCount,
  filteredPurePlugins,
  filteredAdapters,
  filteredExtensions,
  selectedPluginIds,
  togglePlugin: togglePluginSelection,
  selectAllVisible,
  invertVisibleSelection,
  clearSelection,
  pruneSelection,
  toggleMultiSelect,
} = usePluginWorkbench(rawPlugins)

const loading = computed(() => pluginStore.loading)
const filterVisible = ref(false)
const filterRulesVisible = ref(false)
const filterInputRef = ref<any>(null)
const showMetrics = ref(false)
let hideTimer: number | null = null
let metricsRefreshTimer: number | null = null
const pluginSections = computed(() => [
  {
    key: 'plugin',
    title: t('plugins.pluginsSection'),
    icon: Box,
    items: filteredPurePlugins.value,
    variant: 'default' as const,
  },
  {
    key: 'adapter',
    title: t('plugins.adaptersSection'),
    icon: Connection,
    items: filteredAdapters.value,
    variant: 'adapter' as const,
  },
  {
    key: 'extension',
    title: t('plugins.extensionsSection'),
    icon: Expand,
    items: filteredExtensions.value,
    variant: 'extension' as const,
  },
])
const filterRuleGroups = computed(() => [
  {
    key: 'state',
    title: t('plugins.filterRuleGroups.state'),
    rules: [
      { token: 'is:running', label: t('plugins.filterRuleLabels.running') },
      { token: 'is:stopped', label: t('plugins.filterRuleLabels.stopped') },
      { token: 'is:disabled', label: t('plugins.filterRuleLabels.disabled') },
      { token: 'is:selected', label: t('plugins.filterRuleLabels.selected') },
      { token: 'is:manual', label: t('plugins.filterRuleLabels.manual') },
      { token: 'is:auto', label: t('plugins.filterRuleLabels.auto') },
    ],
  },
  {
    key: 'type',
    title: t('plugins.filterRuleGroups.type'),
    rules: [
      { token: 'type:plugin', label: t('plugins.filterRuleLabels.plugin') },
      { token: 'type:adapter', label: t('plugins.filterRuleLabels.adapter') },
      { token: 'type:extension', label: t('plugins.filterRuleLabels.extension') },
      { token: 'is:ui', label: t('plugins.filterRuleLabels.ui') },
      { token: 'has:entries', label: t('plugins.filterRuleLabels.entries') },
      { token: 'has:host', label: t('plugins.filterRuleLabels.host') },
    ],
  },
  {
    key: 'meta',
    title: t('plugins.filterRuleGroups.meta'),
    rules: [
      { token: 'name:', label: t('plugins.filterRuleLabels.name') },
      { token: 'id:', label: t('plugins.filterRuleLabels.id') },
      { token: 'host:', label: t('plugins.filterRuleLabels.hostTarget') },
      { token: 'version:', label: t('plugins.filterRuleLabels.version') },
      { token: 'entry:', label: t('plugins.filterRuleLabels.entry') },
      { token: 'author:', label: t('plugins.filterRuleLabels.author') },
    ],
  },
])

function showFilter() {
  if (hideTimer) {
    clearTimeout(hideTimer)
    hideTimer = null
  }
  filterVisible.value = true
}

function scheduleHideFilter() {
  if (hideTimer) clearTimeout(hideTimer)
  hideTimer = window.setTimeout(() => {
    filterVisible.value = false
    hideTimer = null
  }, 1000)
}

function appendFilterRule(token: string) {
  const current = filterText.value.trim()
  const nextValue = current ? `${current} ${token}` : token
  filterText.value = nextValue
  filterRulesVisible.value = false
  nextTick(() => {
    filterInputRef.value?.focus?.()
  })
}

async function handleRefresh() {
  let warningMessage = ''
  try {
    const syncResult = await pluginStore.syncRegistryAndFetch()
    warningMessage = syncResult.warningMessage || ''
    await pluginStore.fetchPluginStatus()
  } catch (error) {
    console.warn('Failed to refresh plugin data:', error)
  }
  if (showMetrics.value) {
    try {
      await metricsStore.fetchAllMetrics()
    } catch (error) {
      console.warn('Failed to refresh metrics:', error)
    }
  }
  if (warningMessage) {
    ElMessage.warning(warningMessage)
  }
}

async function toggleMetrics() {
  if (!showMetrics.value) {
    try {
      await metricsStore.fetchAllMetrics()
      showMetrics.value = true
      startMetricsAutoRefresh()
    } catch (error) {
      console.error('Failed to fetch metrics:', error)
      showMetrics.value = false
      stopMetricsAutoRefresh()
    }
  } else {
    showMetrics.value = false
    stopMetricsAutoRefresh()
  }
}

function startMetricsAutoRefresh() {
  stopMetricsAutoRefresh()
  metricsRefreshTimer = window.setInterval(() => {
    metricsStore.fetchAllMetrics().catch((error) => {
      console.warn('Auto-refresh metrics failed:', error)
    })
  }, METRICS_REFRESH_INTERVAL)
}

function stopMetricsAutoRefresh() {
  if (metricsRefreshTimer) {
    clearInterval(metricsRefreshTimer)
    metricsRefreshTimer = null
  }
}

function handlePluginClick(pluginId: string) {
  const safeId = encodeURIComponent(pluginId)
  router.push(`/plugins/${safeId}`)
}

function handlePluginPrimaryAction(pluginId: string) {
  if (multiSelectEnabled.value) {
    togglePluginSelection(pluginId)
    return
  }
  handlePluginClick(pluginId)
}

function toggleMultiSelectMode() {
  toggleMultiSelect()
}

function togglePackagePanel() {
  packagePanelVisible.value = !packagePanelVisible.value
}

function closePackagePanel() {
  packagePanelVisible.value = false
}

function closePluginContextMenu() {
  contextMenuVisible.value = false
}

function closeDangerDialog() {
  if (dangerDialogLoading.value) {
    return
  }
  dangerDialogVisible.value = false
  pendingDangerAction.value = null
  pendingDangerPlugin.value = null
}

function openDangerDialog(
  action: ResolvedPluginListAction,
  plugin: PluginMeta & { status?: string; enabled?: boolean; autoStart?: boolean },
) {
  pendingDangerAction.value = action
  pendingDangerPlugin.value = plugin
  dangerDialogVisible.value = true
}

function resolveActionErrorMessage(error: any): string {
  return error?.response?.data?.detail || error?.message || t('messages.requestFailed')
}

function shouldShowLocalError(error: any): boolean {
  const status = error?.response?.status
  if (status === 401 || status === 403 || status === 404) {
    return true
  }
  return !error?.response
}

function handlePluginContextMenu(
  event: MouseEvent,
  plugin: PluginMeta & { status?: string; enabled?: boolean; autoStart?: boolean },
) {
  contextMenuPlugin.value = plugin
  contextMenuActions.value = buildActions(plugin)
  contextMenuPosition.value = {
    x: event.clientX,
    y: event.clientY,
  }
  contextMenuVisible.value = contextMenuActions.value.length > 0
}

async function handleContextActionSelect(action: ResolvedPluginListAction) {
  const plugin = contextMenuPlugin.value
  closePluginContextMenu()
  if (!plugin) {
    return
  }
  if (shouldUseHoldConfirm(action)) {
    openDangerDialog(action, plugin)
    return
  }
  try {
    await executeAction(action, plugin)
  } catch (error: any) {
    console.error('Failed to execute plugin context action:', error)
    if (shouldShowLocalError(error)) {
      ElMessage.error(resolveActionErrorMessage(error))
    }
  }
}

async function handleDangerActionConfirm() {
  const action = pendingDangerAction.value
  const plugin = pendingDangerPlugin.value
  if (!action || !plugin) {
    closeDangerDialog()
    return
  }

  dangerDialogLoading.value = true
  try {
    await executeAction(action, plugin)
    dangerDialogVisible.value = false
    pendingDangerAction.value = null
    pendingDangerPlugin.value = null
  } catch (error: any) {
    console.error('Failed to execute dangerous plugin action:', error)
    if (shouldShowLocalError(error)) {
      ElMessage.error(resolveActionErrorMessage(error))
    }
  } finally {
    dangerDialogLoading.value = false
  }
}

const runningPlugins = computed(() => {
  return rawNormalPlugins.value.filter((plugin) => plugin.status === 'running' && plugin.enabled !== false)
})

async function handleReloadAll() {
  const plugins = runningPlugins.value
  if (plugins.length === 0) return

  try {
    await ElMessageBox.confirm(
      t('plugins.reloadAllConfirm', { count: plugins.length }),
      t('common.confirm'),
      {
        confirmButtonText: t('common.confirm'),
        cancelButtonText: t('common.cancel'),
        type: 'warning',
      },
    )
  } catch {
    return
  }

  reloadingAll.value = true

  try {
    const result = await reloadAllPlugins()
    const successCount = result.reloaded.length
    const failCount = result.failed.length

    result.failed.forEach((item) => {
      console.error(`Failed to reload plugin ${item.plugin_id}:`, item.error)
    })

    if (failCount === 0) {
      ElMessage.success(t('plugins.reloadAllSuccess', { count: successCount }))
    } else {
      ElMessage.warning(t('plugins.reloadAllPartial', { success: successCount, fail: failCount }))
    }
  } catch (error) {
    console.error('Failed to reload all plugins:', error)
    ElMessage.error(t('messages.reloadFailed'))
  } finally {
    reloadingAll.value = false
  }

  await handleRefresh()
}

watch(
  rawPlugins,
  (plugins) => {
    pruneSelection(plugins.map((plugin) => plugin.id))
  },
  { immediate: true },
)

watch(
  () => route.query.tab,
  (tab) => {
    const shouldOpen = tab === 'packages'
    if (packagePanelVisible.value !== shouldOpen) {
      packagePanelVisible.value = shouldOpen
    }
  },
  { immediate: true },
)

watch(packagePanelVisible, (visible) => {
  closePluginContextMenu()
  const nextQuery = { ...route.query }
  if (visible) {
    nextQuery.tab = 'packages'
  } else {
    delete nextQuery.tab
  }
  const currentTab = typeof route.query.tab === 'string' ? route.query.tab : undefined
  const nextTab = typeof nextQuery.tab === 'string' ? nextQuery.tab : undefined
  if (currentTab === nextTab) {
    return
  }
  router.replace({ path: route.path, query: nextQuery })
})

onMounted(async () => {
  await handleRefresh()
})

onUnmounted(() => {
  closePluginContextMenu()
  closeDangerDialog()
  stopMetricsAutoRefresh()
  if (hideTimer) {
    clearTimeout(hideTimer)
    hideTimer = null
  }
})
</script>

<style scoped>
.plugin-workbench {
  --plugin-entry-radius: 18px;
  --package-panel-width: clamp(420px, 42vw, 620px);
  display: flex;
  align-items: flex-start;
  gap: 20px;
  min-width: 0;
}

.plugin-workbench__main {
  flex: 1 1 auto;
  min-width: 0;
}

.plugin-workbench__side {
  flex: 0 0 0;
  min-width: 0;
  max-width: 0;
  opacity: 0;
  overflow: hidden;
  pointer-events: none;
  transform: translateX(28px) scale(0.985);
  transform-origin: right center;
  transition:
    flex-basis 0.32s cubic-bezier(0.22, 1, 0.36, 1),
    max-width 0.32s cubic-bezier(0.22, 1, 0.36, 1),
    opacity 0.26s ease,
    transform 0.32s cubic-bezier(0.22, 1, 0.36, 1);
}

.plugin-workbench__side--visible {
  flex-basis: var(--package-panel-width);
  max-width: var(--package-panel-width);
  opacity: 1;
  overflow: visible;
  pointer-events: auto;
  transform: translateX(0) scale(1);
}

.plugin-list-card {
  border-radius: 24px;
}

.workbench-header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: flex-start;
  gap: 16px;
}

.workbench-header__copy {
  display: flex;
  align-items: center;
  min-width: 0;
  flex: 1 1 auto;
}

.selection-toolbar {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
  gap: 12px;
  min-height: 32px;
  width: min(100%, 460px);
  min-width: 0;
  padding: 6px;
  border-radius: 999px;
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--el-fill-color-light) 82%, white), color-mix(in srgb, var(--el-color-primary) 5%, white));
  border: 1px solid color-mix(in srgb, var(--el-color-primary) 10%, var(--el-border-color));
  box-shadow:
    inset 0 1px 0 color-mix(in srgb, white 72%, transparent),
    0 10px 26px color-mix(in srgb, var(--el-text-color-primary) 6%, transparent);
  transition:
    border-color 0.24s ease,
    box-shadow 0.28s ease,
    background 0.28s ease,
    transform 0.24s ease;
}

.selection-toolbar--active,
.selection-toolbar:focus-within {
  border-color: color-mix(in srgb, var(--el-color-primary) 28%, var(--el-border-color));
  box-shadow:
    inset 0 1px 0 color-mix(in srgb, white 82%, transparent),
    0 14px 34px color-mix(in srgb, var(--el-color-primary) 12%, transparent);
}

.selection-toolbar__trigger {
  --el-button-border-radius: 999px;
  --el-button-bg-color: color-mix(in srgb, var(--el-bg-color) 92%, white);
  --el-button-hover-bg-color: color-mix(in srgb, var(--el-color-primary-light-9) 72%, white);
  --el-button-active-bg-color: color-mix(in srgb, var(--el-color-primary-light-8) 76%, white);
  padding-inline: 16px;
  min-width: 108px;
  justify-content: center;
  font-weight: 600;
  box-shadow:
    0 6px 14px color-mix(in srgb, var(--el-text-color-primary) 6%, transparent),
    inset 0 1px 0 color-mix(in srgb, white 70%, transparent);
  transition:
    transform 0.22s ease,
    box-shadow 0.22s ease,
    background-color 0.22s ease,
    border-color 0.22s ease;
}

.selection-toolbar__trigger:hover {
  transform: translateY(-1px);
}

.selection-toolbar__trigger :deep(.el-button__text) {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.selection-toolbar__trigger-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--el-color-primary) 88%, white);
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--el-color-primary) 16%, transparent);
  transition:
    transform 0.22s ease,
    box-shadow 0.22s ease,
    background 0.22s ease;
}

.selection-toolbar__trigger:hover .selection-toolbar__trigger-dot {
  transform: scale(1.08);
}

.selection-toolbar__expanded {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
  transform: translateX(-8px) scale(0.985);
  transform-origin: left center;
  transition:
    opacity 0.24s ease,
    transform 0.28s cubic-bezier(0.22, 1, 0.36, 1);
}

.selection-toolbar__expanded > * {
  opacity: 0;
  transform: translateX(-6px);
  transition:
    opacity 0.2s ease,
    transform 0.26s cubic-bezier(0.22, 1, 0.36, 1);
}

.selection-toolbar__expanded--active {
  opacity: 1;
  visibility: visible;
  pointer-events: auto;
  transform: translateX(0) scale(1);
}

.selection-toolbar__expanded--active > * {
  opacity: 1;
  transform: translateX(0);
}

.selection-toolbar__expanded--active > :nth-child(1) {
  transition-delay: 30ms;
}

.selection-toolbar__expanded--active > :nth-child(2) {
  transition-delay: 70ms;
}

.selection-toolbar__expanded--active > :nth-child(3) {
  transition-delay: 110ms;
}

.selection-toolbar__expanded--active > :nth-child(4) {
  transition-delay: 150ms;
}

.selection-toolbar__count {
  border-radius: 999px;
  padding-inline: 8px;
  min-height: 28px;
  display: inline-flex;
  align-items: center;
  border: 1px solid color-mix(in srgb, var(--el-color-info) 18%, var(--el-border-color));
  background: color-mix(in srgb, var(--el-color-info-light-9) 72%, white);
  box-shadow: inset 0 1px 0 color-mix(in srgb, white 72%, transparent);
}

.selection-toolbar__count-label {
  display: inline-flex;
  align-items: center;
  font-weight: 600;
  letter-spacing: 0.01em;
}

.selection-toolbar__action {
  --el-button-text-color: var(--el-text-color-regular);
  --el-button-hover-text-color: var(--el-color-primary);
  --el-button-bg-color: transparent;
  --el-button-hover-bg-color: color-mix(in srgb, var(--el-color-primary-light-9) 74%, white);
  --el-button-active-bg-color: color-mix(in srgb, var(--el-color-primary-light-8) 78%, white);
  min-height: 28px;
  padding-inline: 10px;
  border-radius: 999px;
  font-weight: 500;
  transition:
    transform 0.2s ease,
    background-color 0.22s ease,
    color 0.22s ease;
}

.selection-toolbar__action:hover {
  transform: translateY(-1px);
}

.selection-toolbar__action--danger {
  --el-button-hover-text-color: var(--el-color-danger);
  --el-button-hover-bg-color: color-mix(in srgb, var(--el-color-danger-light-9) 82%, white);
}

.header-actions {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: nowrap;
  justify-content: flex-end;
  min-width: 0;
}

.filter-bar {
  margin-top: 16px;
  padding: 14px 16px;
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--el-fill-color-light) 78%, white), color-mix(in srgb, var(--el-color-primary) 4%, white));
  border: 1px solid color-mix(in srgb, var(--el-color-primary) 8%, var(--el-border-color));
  border-radius: 18px;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  min-height: 62px;
  box-sizing: border-box;
}

.filter-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  width: 100%;
}

.filter-rules-trigger {
  --el-button-border-radius: 999px;
  flex-shrink: 0;
  min-width: 110px;
  box-shadow:
    inset 0 1px 0 color-mix(in srgb, white 70%, transparent),
    0 6px 16px color-mix(in srgb, var(--el-text-color-primary) 6%, transparent);
}

.filter-input {
  flex: 1;
  min-width: 220px;
}

.filter-switch,
.filter-mode {
  flex-shrink: 0;
}

.filter-error {
  color: var(--el-color-danger);
  font-size: 12px;
  flex-shrink: 0;
}

.filter-placeholder {
  color: var(--el-text-color-placeholder);
  font-style: italic;
  font-size: 14px;
  line-height: 32px;
}

:global(.filter-rules-popover) {
  padding: 14px;
  border-radius: 18px;
  border: 1px solid color-mix(in srgb, var(--el-color-primary) 10%, var(--el-border-color));
  box-shadow:
    0 18px 40px color-mix(in srgb, var(--el-text-color-primary) 12%, transparent),
    0 6px 14px color-mix(in srgb, var(--el-color-primary) 8%, transparent);
}

.filter-rules-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.filter-rules-panel__header {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.filter-rules-panel__title {
  font-size: 14px;
  font-weight: 700;
  color: var(--el-text-color-primary);
}

.filter-rules-panel__hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}

.filter-rules-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.filter-rules-group__title {
  font-size: 11px;
  font-weight: 700;
  color: var(--el-text-color-secondary);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.filter-rules-group__list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.filter-rule-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid color-mix(in srgb, var(--el-color-primary) 10%, var(--el-border-color));
  border-radius: 14px;
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--el-fill-color-light) 74%, white), var(--el-bg-color));
  color: var(--el-text-color-primary);
  cursor: pointer;
  transition:
    transform 0.18s ease,
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    background 0.18s ease;
}

.filter-rule-chip:hover {
  transform: translateY(-1px);
  border-color: color-mix(in srgb, var(--el-color-primary) 28%, var(--el-border-color));
  box-shadow: 0 10px 22px color-mix(in srgb, var(--el-color-primary) 10%, transparent);
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--el-color-primary-light-9) 72%, white), var(--el-bg-color));
}

.filter-rule-chip__token {
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 12px;
  font-weight: 600;
  color: var(--el-color-primary);
}

.filter-rule-chip__label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.workbench-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 14px;
  padding: 12px 16px;
  border-radius: 18px;
  background: color-mix(in srgb, var(--el-fill-color-light) 72%, white);
}

.type-filter-bar {
  flex: 1 1 420px;
  min-width: 0;
}

.type-filter-group {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.type-filter-group .el-checkbox-button {
  --el-checkbox-button-checked-bg-color: var(--el-color-primary);
}

.type-filter-group .el-checkbox-button__inner {
  display: flex;
  align-items: center;
  gap: 4px;
}

.layout-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.layout-toolbar__label {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.filter-fade-enter-active,
.filter-fade-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}

.filter-fade-enter-from {
  opacity: 0;
  transform: translateY(-4px);
}

.filter-fade-leave-to {
  opacity: 0;
  transform: translateY(4px);
}

.filter-fade-enter-to,
.filter-fade-leave-from {
  opacity: 1;
  transform: translateY(0);
}

@media (max-width: 1280px) {
  .plugin-workbench {
    flex-direction: column;
  }

  .workbench-header {
    grid-template-columns: 1fr;
  }

  .plugin-workbench__side,
  .plugin-workbench__side--visible {
    max-width: 100%;
    min-width: 0;
    flex-basis: auto;
    transform: translateY(16px) scale(0.99);
  }

  .plugin-workbench__side--visible {
    transform: translateY(0) scale(1);
  }

  .selection-toolbar {
    min-width: 0;
    width: 100%;
  }

  .header-actions {
    flex-wrap: wrap;
  }
}
</style>
