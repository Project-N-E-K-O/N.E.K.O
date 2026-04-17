<template>
  <div class="plugin-workbench" :class="{ 'plugin-workbench--package-open': packagePanelVisible }">
    <section class="plugin-workbench__main">
      <el-card class="plugin-list-card">
        <template #header>
          <div class="workbench-header">
            <div class="workbench-header__copy">
              <div class="selection-toolbar">
                <el-button
                  :type="multiSelectEnabled ? 'primary' : 'default'"
                  plain
                  @click="toggleMultiSelectMode"
                >
                  {{ multiSelectEnabled ? $t('plugins.exitMultiSelect') : $t('plugins.multiSelect') }}
                </el-button>
                <div
                  class="selection-toolbar__expanded"
                  :class="{ 'selection-toolbar__expanded--active': multiSelectEnabled }"
                >
                  <el-tag size="small" type="info">
                    {{ $t('plugins.selectedCount', { count: selectedCount }) }}
                  </el-tag>
                  <el-button text :tabindex="multiSelectEnabled ? 0 : -1" @click="selectAllVisible">
                    {{ $t('plugins.selectAllVisible') }}
                  </el-button>
                  <el-button text :tabindex="multiSelectEnabled ? 0 : -1" @click="invertVisibleSelection">
                    {{ $t('plugins.invertVisibleSelection') }}
                  </el-button>
                  <el-button text :tabindex="multiSelectEnabled ? 0 : -1" @click="clearSelection">
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
                <el-input
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
          <template v-if="filteredPurePlugins.length > 0">
            <div class="section-header">
              <span class="section-title">
                <el-icon><Box /></el-icon>
                {{ $t('plugins.pluginsSection') }} ({{ filteredPurePlugins.length }})
              </span>
            </div>
            <TransitionGroup
              name="list"
              tag="div"
              class="plugin-grid"
              :class="pluginGridClass"
              @before-leave="pinLeavingItem"
              @after-leave="clearLeavingItemStyles"
            >
              <div
                v-for="plugin in filteredPurePlugins"
                :key="plugin.id"
                class="plugin-item"
                :class="pluginItemClass(plugin.id)"
              >
                <div v-if="multiSelectEnabled" class="plugin-item__select">
                  <el-checkbox
                    :model-value="isSelected(plugin.id)"
                    @click.stop
                    @change="togglePluginSelection(plugin.id)"
                  />
                </div>
                <component
                  :is="layoutMode === 'list' ? PluginListRow : PluginCard"
                  :plugin="plugin"
                  :is-selected="multiSelectEnabled && isSelected(plugin.id)"
                  :show-metrics="showMetrics"
                  @click="handlePluginPrimaryAction(plugin.id)"
                  @contextmenu="handlePluginContextMenu($event, plugin)"
                />
              </div>
            </TransitionGroup>
          </template>

          <template v-if="filteredAdapters.length > 0">
            <div class="section-header section-header--adapter">
              <span class="section-title">
                <el-icon><Connection /></el-icon>
                {{ $t('plugins.adaptersSection') }} ({{ filteredAdapters.length }})
              </span>
            </div>
            <TransitionGroup
              name="list"
              tag="div"
              class="plugin-grid"
              :class="pluginGridClass"
              @before-leave="pinLeavingItem"
              @after-leave="clearLeavingItemStyles"
            >
              <div
                v-for="adapter in filteredAdapters"
                :key="adapter.id"
                class="plugin-item"
                :class="pluginItemClass(adapter.id)"
              >
                <div v-if="multiSelectEnabled" class="plugin-item__select">
                  <el-checkbox
                    :model-value="isSelected(adapter.id)"
                    @click.stop
                    @change="togglePluginSelection(adapter.id)"
                  />
                </div>
                <component
                  :is="layoutMode === 'list' ? PluginListRow : PluginCard"
                  :plugin="adapter"
                  :is-selected="multiSelectEnabled && isSelected(adapter.id)"
                  :show-metrics="showMetrics"
                  @click="handlePluginPrimaryAction(adapter.id)"
                  @contextmenu="handlePluginContextMenu($event, adapter)"
                />
              </div>
            </TransitionGroup>
          </template>

          <template v-if="filteredExtensions.length > 0">
            <div class="section-header section-header--ext">
              <span class="section-title">
                <el-icon><Expand /></el-icon>
                {{ $t('plugins.extensionsSection') }} ({{ filteredExtensions.length }})
              </span>
            </div>
            <TransitionGroup
              name="list"
              tag="div"
              class="plugin-grid"
              :class="pluginGridClass"
              @before-leave="pinLeavingItem"
              @after-leave="clearLeavingItemStyles"
            >
              <div
                v-for="ext in filteredExtensions"
                :key="ext.id"
                class="plugin-item"
                :class="pluginItemClass(ext.id)"
              >
                <div v-if="multiSelectEnabled" class="plugin-item__select">
                  <el-checkbox
                    :model-value="isSelected(ext.id)"
                    @click.stop
                    @change="togglePluginSelection(ext.id)"
                  />
                </div>
                <component
                  :is="layoutMode === 'list' ? PluginListRow : PluginCard"
                  :plugin="ext"
                  :is-selected="multiSelectEnabled && isSelected(ext.id)"
                  :show-metrics="showMetrics"
                  @click="handlePluginPrimaryAction(ext.id)"
                  @contextmenu="handlePluginContextMenu($event, ext)"
                />
              </div>
            </TransitionGroup>
          </template>
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
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Refresh, DataAnalysis, RefreshRight, Box, Connection, Expand } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { usePluginStore } from '@/stores/plugin'
import { useMetricsStore } from '@/stores/metrics'
import PluginCard from '@/components/plugin/PluginCard.vue'
import PluginListRow from '@/components/plugin/PluginListRow.vue'
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
  isSelected,
  togglePlugin: togglePluginSelection,
  selectAllVisible,
  invertVisibleSelection,
  clearSelection,
  pruneSelection,
  toggleMultiSelect,
} = usePluginWorkbench(rawPlugins)

const loading = computed(() => pluginStore.loading)
const pluginGridClass = computed(() => `plugin-grid--${layoutMode.value}`)
const filterVisible = ref(false)
const showMetrics = ref(false)
let hideTimer: number | null = null
let metricsRefreshTimer: number | null = null

function pluginItemClass(pluginId: string) {
  return {
    'plugin-item--selection-mode': multiSelectEnabled.value,
    'plugin-item--selected': multiSelectEnabled.value && isSelected(pluginId),
    'plugin-item--list-layout': layoutMode.value === 'list',
  }
}

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

function pinLeavingItem(element: Element) {
  const node = element as HTMLElement
  node.style.left = `${node.offsetLeft}px`
  node.style.top = `${node.offsetTop}px`
  node.style.width = `${node.offsetWidth}px`
  node.style.height = `${node.offsetHeight}px`
}

function clearLeavingItemStyles(element: Element) {
  const node = element as HTMLElement
  node.style.left = ''
  node.style.top = ''
  node.style.width = ''
  node.style.height = ''
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
}

.selection-toolbar__expanded {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
}

.selection-toolbar__expanded--active {
  opacity: 1;
  visibility: visible;
  pointer-events: auto;
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

.section-header {
  margin-bottom: 12px;
}

.section-header--adapter,
.section-header--ext {
  margin-top: 24px;
}

.section-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.plugin-grid {
  display: grid;
  gap: 16px;
  align-items: stretch;
  position: relative;
}

.plugin-grid--list,
.plugin-grid--single {
  grid-template-columns: 1fr;
}

.plugin-grid--double {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.plugin-grid--compact {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.plugin-item {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  will-change: transform, opacity;
}

.plugin-item--selection-mode {
  padding-top: 0;
}

.plugin-item__select {
  position: absolute;
  top: -8px;
  right: -8px;
  z-index: 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 32px;
  min-height: 32px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--el-bg-color) 86%, white);
  box-shadow: 0 8px 18px color-mix(in srgb, var(--el-text-color-primary) 10%, transparent);
  backdrop-filter: blur(10px);
}

.plugin-item--list-layout .plugin-item__select {
  top: -8px;
  right: -8px;
  transform: none;
}

.plugin-item--selected :deep(.plugin-card) {
  border-color: var(--el-color-primary);
  box-shadow:
    0 16px 32px color-mix(in srgb, var(--el-color-primary) 16%, transparent),
    0 6px 14px color-mix(in srgb, var(--el-text-color-primary) 8%, transparent);
}

.plugin-item--selected :deep(.plugin-list-row-card) {
  border-color: var(--el-color-primary);
}

.plugin-item--list-layout :deep(.plugin-list-row-card) {
  min-height: 0;
}

.plugin-item :deep(.plugin-card) {
  height: 100%;
  display: flex;
  flex-direction: column;
  transition:
    transform 0.24s ease,
    box-shadow 0.24s ease,
    border-color 0.24s ease;
}

.plugin-item:hover :deep(.plugin-card) {
  transform: translateY(-3px);
}

.plugin-item :deep(.el-card__body) {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.plugin-card-body {
  flex: 1;
  display: flex;
  flex-direction: column;
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

.list-enter-active,
.list-leave-active {
  transition:
    transform 0.34s cubic-bezier(0.22, 1, 0.36, 1),
    opacity 0.24s ease,
    filter 0.24s ease;
}

.list-enter-from {
  opacity: 0;
  transform: scale(0.94) translateY(12px);
  filter: blur(6px);
}

.list-leave-to {
  opacity: 0;
  transform: scale(0.94) translateY(-12px);
  filter: blur(6px);
}

.list-enter-to,
.list-leave-from {
  opacity: 1;
  transform: scale(1) translateY(0);
  filter: blur(0);
}

.list-leave-active {
  position: absolute;
  z-index: 0;
  pointer-events: none;
  margin: 0;
}

.list-move {
  transition: transform 0.34s cubic-bezier(0.22, 1, 0.36, 1);
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

  .plugin-grid--compact {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 1180px) {
  .plugin-grid--compact {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 900px) {
  .plugin-grid--double,
  .plugin-grid--compact {
    grid-template-columns: 1fr;
  }
}
</style>
