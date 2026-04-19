<template>
  <div class="plugin-workbench" :class="{ 'plugin-workbench--package-open': packagePanelVisible }">
    <section class="plugin-workbench__main">
      <el-card class="plugin-list-card">
        <template #header>
          <div class="workbench-header">
            <div class="workbench-header__copy">
              <el-button
                class="multi-select-trigger"
                :class="{ 'multi-select-trigger--active': multiSelectEnabled }"
                :type="multiSelectEnabled ? 'primary' : 'default'"
                plain
                @click="toggleMultiSelectMode"
              >
                <el-icon><Finished /></el-icon>
                {{ multiSelectEnabled ? $t('plugins.exitMultiSelect') : $t('plugins.multiSelect') }}
              </el-button>
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

          <div class="filter-bar">
            <div class="filter-controls">
              <div
                ref="filterRulesAnchorRef"
                class="filter-rules-anchor"
              >
                <button
                  class="filter-rules-trigger"
                  :class="{ 'filter-rules-trigger--active': filterRulesVisible }"
                  type="button"
                  @click.stop="toggleFilterRules"
                >
                  <el-icon><Operation /></el-icon>
                  <span>{{ $t('plugins.filterRules') }}</span>
                  <svg class="filter-rules-trigger__arrow" viewBox="0 0 12 12" fill="none">
                    <path d="M3 5L6 8L9 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                  </svg>
                </button>
              </div>

              <Teleport to="body">
                <Transition name="rules-panel">
                  <div
                    v-if="filterRulesVisible"
                    ref="filterRulesPanelRef"
                    class="filter-rules-dropdown"
                    :style="rulesDropdownStyle"
                  >
                    <div class="filter-rules-panel">
                      <div class="filter-rules-panel__header">
                        <div class="filter-rules-panel__title">{{ $t('plugins.filterRulesTitle') }}</div>
                        <div class="filter-rules-panel__hint">{{ $t('plugins.filterRulesHint') }}</div>
                      </div>

                      <div
                        v-for="(group, gi) in filterRuleGroups"
                        :key="group.key"
                        class="filter-rules-group"
                        :style="{ '--group-index': gi }"
                      >
                        <div class="filter-rules-group__title">{{ group.title }}</div>
                        <div class="filter-rules-group__list">
                          <button
                            v-for="(rule, ri) in group.rules"
                            :key="rule.token"
                            type="button"
                            class="filter-rule-chip"
                            :style="{ '--chip-index': gi * 6 + ri }"
                            @click="appendFilterRule(rule.token)"
                          >
                            <span class="filter-rule-chip__token">{{ rule.token }}</span>
                            <span class="filter-rule-chip__label">{{ rule.label }}</span>
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                </Transition>
              </Teleport>

              <el-input
                ref="filterInputRef"
                v-model="filterText"
                clearable
                class="filter-input"
                :placeholder="$t('plugins.filterPlaceholder')"
              />

              <div class="filter-toggles">
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
              </div>

              <Transition name="filter-error-fade">
                <span v-if="regexError" class="filter-error">{{ $t('plugins.invalidRegex') }}</span>
              </Transition>
            </div>
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
              <el-icon class="layout-toolbar__icon"><Grid /></el-icon>
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

    <!-- Floating multi-select action bar -->
    <Transition name="float-bar">
      <div v-if="multiSelectEnabled" class="floating-select-bar">
        <div class="floating-select-bar__inner">
          <div class="floating-select-bar__count">
            <span class="floating-select-bar__count-num">{{ selectedCount }}</span>
            <span class="floating-select-bar__count-label">{{ $t('plugins.selectedCount', { count: selectedCount }) }}</span>
          </div>

          <div class="floating-select-bar__divider" />

          <div class="floating-select-bar__actions">
            <button class="fab-action" @click="selectAllVisible">
              <el-icon><Finished /></el-icon>
              <span>{{ $t('plugins.selectAllVisible') }}</span>
            </button>
            <button class="fab-action" @click="invertVisibleSelection">
              <el-icon><Sort /></el-icon>
              <span>{{ $t('plugins.invertVisibleSelection') }}</span>
            </button>
            <button class="fab-action fab-action--danger" @click="clearSelection">
              <el-icon><CircleClose /></el-icon>
              <span>{{ $t('plugins.clearSelection') }}</span>
            </button>
          </div>

          <div class="floating-select-bar__divider" />

          <button class="fab-action fab-action--exit" @click="toggleMultiSelectMode">
            <el-icon><Close /></el-icon>
            <span>{{ $t('plugins.exitMultiSelect') }}</span>
          </button>
        </div>
      </div>
    </Transition>

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
import { Refresh, DataAnalysis, RefreshRight, Box, Connection, Expand, Operation, Finished, Sort, CircleClose, Close, Grid } from '@element-plus/icons-vue'
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
const filterRulesVisible = ref(false)
const filterInputRef = ref<any>(null)
const filterRulesAnchorRef = ref<HTMLElement | null>(null)
const filterRulesPanelRef = ref<HTMLElement | null>(null)
const showMetrics = ref(false)
let metricsRefreshTimer: number | null = null
let rulesHideTimer: number | null = null
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

const rulesDropdownPos = ref({ top: 0, left: 0 })
const rulesDropdownStyle = computed(() => ({
  position: 'fixed' as const,
  top: `${rulesDropdownPos.value.top}px`,
  left: `${rulesDropdownPos.value.left}px`,
}))

function updateRulesDropdownPos() {
  const anchor = filterRulesAnchorRef.value
  if (!anchor) return
  const rect = anchor.getBoundingClientRect()
  rulesDropdownPos.value = {
    top: rect.bottom + 8,
    left: rect.left,
  }
}

function getRulesUnionRect(pad: number) {
  const rects: DOMRect[] = []
  if (filterRulesAnchorRef.value) rects.push(filterRulesAnchorRef.value.getBoundingClientRect())
  if (filterRulesPanelRef.value) rects.push(filterRulesPanelRef.value.getBoundingClientRect())
  if (rects.length === 0) return null
  return {
    left: Math.min(...rects.map((r) => r.left)) - pad,
    top: Math.min(...rects.map((r) => r.top)) - pad,
    right: Math.max(...rects.map((r) => r.right)) + pad,
    bottom: Math.max(...rects.map((r) => r.bottom)) + pad,
  }
}

function isInsideRulesArea(x: number, y: number) {
  const union = getRulesUnionRect(40)
  if (!union) return false
  return x >= union.left && x <= union.right && y >= union.top && y <= union.bottom
}

function clearRulesHideTimer() {
  if (rulesHideTimer) {
    clearTimeout(rulesHideTimer)
    rulesHideTimer = null
  }
}

function scheduleRulesHide() {
  clearRulesHideTimer()
  rulesHideTimer = window.setTimeout(() => {
    filterRulesVisible.value = false
    rulesHideTimer = null
  }, 1000)
}

function onDocumentMouseMove(event: MouseEvent) {
  if (!filterRulesVisible.value) return
  if (isInsideRulesArea(event.clientX, event.clientY)) {
    clearRulesHideTimer()
  } else if (!rulesHideTimer) {
    scheduleRulesHide()
  }
}

function onDocumentMouseDown(event: MouseEvent) {
  if (!filterRulesVisible.value) return
  const anchor = filterRulesAnchorRef.value
  const panel = filterRulesPanelRef.value
  const target = event.target as Node
  if (anchor?.contains(target) || panel?.contains(target)) return
  filterRulesVisible.value = false
  clearRulesHideTimer()
}

function startRulesListeners() {
  document.addEventListener('mousemove', onDocumentMouseMove)
  document.addEventListener('mousedown', onDocumentMouseDown, true)
}

function stopRulesListeners() {
  document.removeEventListener('mousemove', onDocumentMouseMove)
  document.removeEventListener('mousedown', onDocumentMouseDown, true)
  clearRulesHideTimer()
}

watch(filterRulesVisible, (visible) => {
  if (visible) {
    startRulesListeners()
  } else {
    stopRulesListeners()
  }
})

function toggleFilterRules() {
  if (!filterRulesVisible.value) {
    updateRulesDropdownPos()
  }
  filterRulesVisible.value = !filterRulesVisible.value
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
  stopRulesListeners()
})
</script>

<style scoped>
.plugin-workbench {
  --plugin-entry-radius: var(--radius-card);
  --package-panel-width: clamp(420px, 42vw, 620px);
  /* ── Unified radius system ── */
  --radius-card: 16px;       /* large containers: card, dropdown */
  --radius-panel: 14px;      /* medium panels: filter bar, toolbar, floating bar */
  --radius-control: 10px;    /* buttons, inputs, interactive controls */
  --radius-chip: 8px;        /* small chips, tags, badges */
  display: flex;
  align-items: flex-start;
  gap: 20px;
  min-width: 0;
  padding-bottom: 80px; /* space for floating bar */
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
  border-radius: var(--radius-card);
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

/* ── Multi-select trigger button (top) ── */
.multi-select-trigger {
  --el-button-border-radius: var(--radius-control);
  font-weight: 600;
  padding: 8px 18px;
  gap: 6px;
  transition:
    transform 0.22s ease,
    box-shadow 0.22s ease,
    background-color 0.22s ease,
    border-color 0.22s ease;
}

.multi-select-trigger:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 16px color-mix(in srgb, var(--el-color-primary) 14%, transparent);
}

.multi-select-trigger--active {
  box-shadow:
    0 0 0 2px color-mix(in srgb, var(--el-color-primary) 24%, transparent),
    0 6px 16px color-mix(in srgb, var(--el-color-primary) 14%, transparent);
}

/* ── Floating bottom action bar ── */
.floating-select-bar {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 2000;
  pointer-events: auto;
}

.floating-select-bar__inner {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  border-radius: var(--radius-panel);
  background: color-mix(in srgb, var(--el-bg-color) 78%, transparent);
  backdrop-filter: blur(20px) saturate(1.6);
  -webkit-backdrop-filter: blur(20px) saturate(1.6);
  border: 1px solid color-mix(in srgb, var(--el-color-primary) 18%, var(--el-border-color));
  box-shadow:
    0 20px 60px color-mix(in srgb, var(--el-text-color-primary) 16%, transparent),
    0 8px 24px color-mix(in srgb, var(--el-color-primary) 10%, transparent),
    inset 0 1px 0 color-mix(in srgb, white 40%, transparent);
}

.floating-select-bar__count {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 12px;
  min-height: 40px;
}

.floating-select-bar__count-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 28px;
  height: 28px;
  padding: 0 8px;
  border-radius: var(--radius-chip);
  background: var(--el-color-primary);
  color: #fff;
  font-size: 14px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1;
  box-shadow: 0 4px 12px color-mix(in srgb, var(--el-color-primary) 36%, transparent);
}

.floating-select-bar__count-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--el-text-color-regular);
  white-space: nowrap;
}

.floating-select-bar__divider {
  width: 1px;
  height: 24px;
  background: color-mix(in srgb, var(--el-border-color) 60%, transparent);
  flex-shrink: 0;
  margin: 0 4px;
}

.floating-select-bar__actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

/* ── Floating bar action buttons ── */
.fab-action {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border: none;
  border-radius: var(--radius-control);
  background: transparent;
  color: var(--el-text-color-regular);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition:
    background-color 0.2s ease,
    color 0.2s ease,
    transform 0.18s ease;
}

.fab-action .el-icon {
  font-size: 16px;
}

.fab-action:hover {
  background: color-mix(in srgb, var(--el-color-primary) 10%, transparent);
  color: var(--el-color-primary);
  transform: translateY(-1px);
}

.fab-action:active {
  transform: translateY(0) scale(0.97);
}

.fab-action--danger:hover {
  background: color-mix(in srgb, var(--el-color-danger) 10%, transparent);
  color: var(--el-color-danger);
}

.fab-action--exit {
  color: var(--el-text-color-secondary);
}

.fab-action--exit:hover {
  background: color-mix(in srgb, var(--el-text-color-primary) 8%, transparent);
  color: var(--el-text-color-primary);
}

/* ── Float bar transition ── */
.float-bar-enter-active {
  transition:
    opacity 0.32s cubic-bezier(0.22, 1, 0.36, 1),
    transform 0.38s cubic-bezier(0.22, 1, 0.36, 1);
}

.float-bar-leave-active {
  transition:
    opacity 0.22s ease,
    transform 0.26s cubic-bezier(0.55, 0, 1, 0.45);
}

.float-bar-enter-from {
  opacity: 0;
  transform: translateX(-50%) translateY(24px) scale(0.92);
}

.float-bar-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(16px) scale(0.95);
}

.float-bar-enter-to,
.float-bar-leave-from {
  opacity: 1;
  transform: translateX(-50%) translateY(0) scale(1);
}

.header-actions {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: nowrap;
  justify-content: flex-end;
  min-width: 0;
}

.header-actions :deep(.el-button) {
  --el-button-border-radius: var(--radius-control);
}

/* ── Filter bar ── */
.filter-bar {
  margin-top: 14px;
  padding: 10px 14px;
  background: color-mix(in srgb, var(--el-bg-color) 78%, transparent);
  backdrop-filter: blur(16px) saturate(1.4);
  -webkit-backdrop-filter: blur(16px) saturate(1.4);
  border: 1px solid color-mix(in srgb, var(--el-border-color) 50%, transparent);
  border-radius: var(--radius-panel);
  box-shadow:
    inset 0 1px 0 color-mix(in srgb, white 30%, transparent),
    0 4px 16px color-mix(in srgb, var(--el-text-color-primary) 4%, transparent);
}

.filter-controls {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  width: 100%;
}

.filter-input {
  flex: 1;
  min-width: 200px;
}

.filter-input :deep(.el-input__wrapper) {
  border-radius: var(--radius-control);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--el-border-color) 50%, transparent) inset;
  transition:
    box-shadow 0.2s ease,
    border-color 0.2s ease;
}

.filter-input :deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--el-color-primary) 30%, var(--el-border-color)) inset;
}

.filter-input :deep(.el-input__wrapper.is-focus) {
  box-shadow:
    0 0 0 1px var(--el-color-primary) inset,
    0 0 0 3px color-mix(in srgb, var(--el-color-primary) 12%, transparent);
}

.filter-toggles {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.filter-switch,
.filter-mode {
  flex-shrink: 0;
}

.filter-mode :deep(.el-radio-button__inner) {
  border-radius: var(--radius-chip);
}

.filter-error {
  color: var(--el-color-danger);
  font-size: 12px;
  font-weight: 500;
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: var(--radius-chip);
  background: color-mix(in srgb, var(--el-color-danger) 8%, transparent);
}

.filter-error-fade-enter-active,
.filter-error-fade-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.filter-error-fade-enter-from,
.filter-error-fade-leave-to {
  opacity: 0;
  transform: translateX(-4px);
}

/* ── Filter rules dropdown ── */
.filter-rules-anchor {
  position: relative;
  flex-shrink: 0;
}

.filter-rules-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border: 1px solid color-mix(in srgb, var(--el-border-color) 60%, transparent);
  border-radius: var(--radius-control);
  background: color-mix(in srgb, var(--el-bg-color) 90%, white);
  color: var(--el-text-color-regular);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  flex-shrink: 0;
  transition:
    background-color 0.2s ease,
    border-color 0.2s ease,
    color 0.2s ease,
    transform 0.18s ease,
    box-shadow 0.2s ease;
}

.filter-rules-trigger .el-icon {
  font-size: 15px;
}

.filter-rules-trigger__arrow {
  width: 12px;
  height: 12px;
  margin-left: 2px;
  transition: transform 0.28s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.filter-rules-trigger--active .filter-rules-trigger__arrow {
  transform: rotate(180deg);
}

.filter-rules-trigger:hover,
.filter-rules-trigger--active {
  border-color: color-mix(in srgb, var(--el-color-primary) 30%, var(--el-border-color));
  color: var(--el-color-primary);
  background: color-mix(in srgb, var(--el-color-primary) 6%, var(--el-bg-color));
  transform: translateY(-1px);
  box-shadow: 0 4px 12px color-mix(in srgb, var(--el-color-primary) 10%, transparent);
}

.filter-rules-trigger--active {
  transform: translateY(0);
  box-shadow:
    0 0 0 2px color-mix(in srgb, var(--el-color-primary) 14%, transparent),
    0 4px 12px color-mix(in srgb, var(--el-color-primary) 10%, transparent);
}

/* ── Filter rules dropdown (teleported to body, needs :global) ── */
:global(.filter-rules-dropdown) {
  z-index: 2100;
  width: 400px;
  padding: 16px;
  border-radius: var(--radius-card);
  border: 1px solid color-mix(in srgb, var(--el-border-color) 40%, transparent);
  background: color-mix(in srgb, var(--el-bg-color) 86%, transparent);
  backdrop-filter: blur(24px) saturate(1.8);
  -webkit-backdrop-filter: blur(24px) saturate(1.8);
  box-shadow:
    0 24px 64px color-mix(in srgb, var(--el-text-color-primary) 16%, transparent),
    0 8px 24px color-mix(in srgb, var(--el-color-primary) 6%, transparent),
    inset 0 1px 0 color-mix(in srgb, white 30%, transparent);
  transform-origin: top left;
}

/* ── Rules panel transition ── */
:global(.rules-panel-enter-active) {
  transition:
    opacity 0.28s cubic-bezier(0.22, 1, 0.36, 1),
    transform 0.32s cubic-bezier(0.34, 1.56, 0.64, 1),
    filter 0.28s ease;
}

:global(.rules-panel-leave-active) {
  transition:
    opacity 0.2s ease,
    transform 0.2s cubic-bezier(0.55, 0, 1, 0.45),
    filter 0.2s ease;
}

:global(.rules-panel-enter-from) {
  opacity: 0;
  transform: scale(0.92) translateY(-6px);
  filter: blur(8px);
}

:global(.rules-panel-leave-to) {
  opacity: 0;
  transform: scale(0.95) translateY(-4px);
  filter: blur(4px);
}

:global(.rules-panel-enter-to),
:global(.rules-panel-leave-from) {
  opacity: 1;
  transform: scale(1) translateY(0);
  filter: blur(0);
}

:global(.filter-rules-panel) {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

:global(.filter-rules-panel__header) {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

:global(.filter-rules-panel__title) {
  font-size: 14px;
  font-weight: 700;
  color: var(--el-text-color-primary);
}

:global(.filter-rules-panel__hint) {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}

:global(.filter-rules-group) {
  display: flex;
  flex-direction: column;
  gap: 8px;
  animation: group-slide-in 0.32s cubic-bezier(0.22, 1, 0.36, 1) backwards;
  animation-delay: calc(var(--group-index, 0) * 60ms + 80ms);
}

@keyframes group-slide-in {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

:global(.filter-rules-group__title) {
  font-size: 11px;
  font-weight: 700;
  color: var(--el-text-color-secondary);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

:global(.filter-rules-group__list) {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

:global(.filter-rule-chip) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border: 1px solid color-mix(in srgb, var(--el-border-color) 50%, transparent);
  border-radius: var(--radius-chip);
  background: color-mix(in srgb, var(--el-bg-color) 90%, white);
  color: var(--el-text-color-primary);
  font-size: 12px;
  cursor: pointer;
  animation: chip-pop-in 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) backwards;
  animation-delay: calc(var(--chip-index, 0) * 25ms + 120ms);
  transition:
    transform 0.18s ease,
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    background 0.18s ease,
    color 0.18s ease;
}

@keyframes chip-pop-in {
  from {
    opacity: 0;
    transform: scale(0.85) translateY(4px);
  }
  to {
    opacity: 1;
    transform: scale(1) translateY(0);
  }
}

:global(.filter-rule-chip:hover) {
  transform: translateY(-2px);
  border-color: color-mix(in srgb, var(--el-color-primary) 30%, var(--el-border-color));
  box-shadow: 0 6px 16px color-mix(in srgb, var(--el-color-primary) 12%, transparent);
  background: color-mix(in srgb, var(--el-color-primary) 8%, var(--el-bg-color));
}

:global(.filter-rule-chip:active) {
  transform: translateY(0) scale(0.96);
  transition-duration: 0.08s;
}

:global(.filter-rule-chip__token) {
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--el-color-primary);
}

:global(.filter-rule-chip__label) {
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
}

/* ── Workbench toolbar (type filter + layout) ── */
.workbench-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 10px;
  padding: 8px 14px;
  border-radius: var(--radius-panel);
  background: color-mix(in srgb, var(--el-bg-color) 78%, transparent);
  backdrop-filter: blur(16px) saturate(1.4);
  -webkit-backdrop-filter: blur(16px) saturate(1.4);
  border: 1px solid color-mix(in srgb, var(--el-border-color) 40%, transparent);
  box-shadow:
    inset 0 1px 0 color-mix(in srgb, white 24%, transparent),
    0 2px 8px color-mix(in srgb, var(--el-text-color-primary) 3%, transparent);
}

.type-filter-bar {
  flex: 1 1 auto;
  min-width: 0;
}

.type-filter-group {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.type-filter-group :deep(.el-checkbox-button) {
  --el-checkbox-button-checked-bg-color: var(--el-color-primary);
}

.type-filter-group :deep(.el-checkbox-button__inner) {
  display: flex;
  align-items: center;
  gap: 5px;
  border-radius: var(--radius-control);
  padding: 6px 14px;
  font-size: 13px;
  font-weight: 500;
  border: 1px solid color-mix(in srgb, var(--el-border-color) 50%, transparent);
  transition:
    background-color 0.2s ease,
    border-color 0.2s ease,
    color 0.2s ease,
    box-shadow 0.2s ease,
    transform 0.18s ease;
}

.type-filter-group :deep(.el-checkbox-button__inner:hover) {
  transform: translateY(-1px);
}

.type-filter-group :deep(.el-checkbox-button.is-checked .el-checkbox-button__inner) {
  box-shadow: 0 4px 12px color-mix(in srgb, var(--el-color-primary) 24%, transparent);
  border-color: transparent;
}

.layout-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.layout-toolbar__icon {
  font-size: 15px;
  color: var(--el-text-color-secondary);
}

.layout-toolbar :deep(.el-radio-button__inner) {
  border-radius: var(--radius-chip);
  padding: 5px 12px;
  font-size: 12px;
  font-weight: 500;
  transition:
    background-color 0.2s ease,
    color 0.2s ease,
    box-shadow 0.2s ease;
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

  .header-actions {
    flex-wrap: wrap;
  }

  .floating-select-bar__inner {
    flex-wrap: wrap;
    justify-content: center;
    max-width: calc(100vw - 32px);
  }

  .floating-select-bar__count-label {
    display: none;
  }
}

@media (max-width: 640px) {
  .floating-select-bar {
    left: 16px;
    right: 16px;
    transform: none;
  }

  .floating-select-bar__inner {
    width: 100%;
    justify-content: center;
  }

  .float-bar-enter-from {
    opacity: 0;
    transform: translateY(24px) scale(0.92);
  }

  .float-bar-leave-to {
    opacity: 0;
    transform: translateY(16px) scale(0.95);
  }

  .float-bar-enter-to,
  .float-bar-leave-from {
    opacity: 1;
    transform: translateY(0) scale(1);
  }

  .fab-action span {
    display: none;
  }

  .fab-action {
    padding: 8px 10px;
  }
}
</style>
