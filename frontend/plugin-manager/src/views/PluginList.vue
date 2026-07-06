<template>
  <div
    class="plugin-workbench"
    :class="{
      'plugin-workbench--market-open': marketPanelVisible,
      'plugin-workbench--package-open': packagePanelVisible,
    }"
    data-yui-guide-id="plugin-list-workbench"
  >
    <aside
      class="plugin-workbench__rail plugin-workbench__rail--market"
      :aria-hidden="!marketPanelVisible"
      :inert="!marketPanelVisible"
    >
      <div class="plugin-workbench__rail-inner">
        <MarketPanel
          v-if="marketPanelEverOpened"
          v-show="marketPanelVisible"
          embedded
          :active="marketPanelVisible"
          @close="closeMarketPanel"
        />
      </div>
    </aside>

    <section
      class="plugin-workbench__main"
      data-yui-guide-id="plugin-list-main"
      v-motion
      :initial="{ opacity: 0, y: 16, filter: 'blur(4px)' }"
      :enter="{ opacity: 1, y: 0, filter: 'blur(0px)', transition: { duration: 360, type: 'spring', stiffness: 240, damping: 24 } }"
    >
      <el-card class="plugin-list-card" data-yui-guide-id="plugin-list-card-shell">
        <template #header>
          <div class="workbench-header">
            <div class="workbench-header__copy">
              <button
                v-if="marketUrl"
                class="market-trigger"
                :class="{ 'market-trigger--active': marketPanelVisible }"
                type="button"
                data-yui-guide-id="plugin-list-market-toggle"
                :title="marketPanelVisible ? $t('market.closeMarket') : $t('market.openMarket')"
                @click="toggleMarketPanel"
              >
                <el-icon class="market-trigger__icon"><ShoppingCart /></el-icon>
                <span class="market-trigger__label">
                  {{ marketPanelVisible ? $t('market.closeMarket') : $t('market.getNewPlugins') }}
                </span>
                <el-icon class="market-trigger__arrow">
                  <component :is="marketPanelVisible ? ArrowLeft : ArrowRight" />
                </el-icon>
              </button>
              <el-button
                v-if="marketUrl"
                class="market-auth-trigger"
                :class="{ 'market-auth-trigger--connected': marketAuth.authenticated }"
                :loading="marketAuthBusy"
                plain
                @click="marketAuth.authenticated ? logoutMarketAccount() : startMarketLogin()"
              >
                <el-icon><User /></el-icon>
                {{
                  marketAuth.authenticated
                    ? $t('market.accountConnected', { name: marketAuthDisplayName })
                    : $t('market.login')
                }}
              </el-button>
              <el-button
                class="multi-select-trigger"
                :class="{ 'multi-select-trigger--active': multiSelectEnabled }"
                :type="multiSelectEnabled ? 'primary' : 'default'"
                data-yui-guide-id="plugin-list-multi-select"
                plain
                @click="toggleMultiSelectMode"
              >
                <el-icon><Finished /></el-icon>
                {{ multiSelectEnabled ? $t('plugins.exitMultiSelect') : $t('plugins.multiSelect') }}
              </el-button>
            </div>

            <div class="header-actions">
              <button
                class="header-btn header-btn--accent"
                :disabled="importing"
                data-yui-guide-id="plugin-list-import"
                @click="triggerImportFile"
              >
                <el-icon><Upload /></el-icon>
                <span>{{ importing ? $t('plugins.importing') : $t('plugins.import') }}</span>
              </button>
              <input
                ref="importFileInputRef"
                type="file"
                accept=".neko-plugin,.neko-bundle"
                class="import-file-input"
                @change="handleImportFileChange"
              />
              <button
                class="header-btn"
                :class="{ 'header-btn--active': packagePanelVisible }"
                data-yui-guide-id="plugin-list-package-panel-toggle"
                @click="togglePackagePanel"
              >
                <el-icon><Box /></el-icon>
                <span>{{ packagePanelVisible ? $t('plugins.closePackageManager') : $t('plugins.openPackageManager') }}</span>
              </button>
              <button
                class="header-btn"
                :class="{ 'header-btn--active header-btn--success': showMetrics }"
                data-yui-guide-id="plugin-list-metrics-toggle"
                @click="toggleMetrics"
              >
                <el-icon><DataAnalysis /></el-icon>
                <span>{{ showMetrics ? $t('plugins.hideMetrics') : $t('plugins.showMetrics') }}</span>
              </button>
              <button
                class="header-btn"
                :class="{ 'header-btn--active': showSourceDetail }"
                data-yui-guide-id="plugin-list-source-toggle"
                @click="toggleSourceDetail"
              >
                <el-icon><InfoFilled /></el-icon>
                <span>{{ showSourceDetail ? $t('plugins.hideSourceDetail') : $t('plugins.showSourceDetail') }}</span>
              </button>
              <button
                class="header-btn header-btn--warn"
                :disabled="reloadingAll || runningPlugins.length === 0"
                @click="handleReloadAll"
              >
                <el-icon><RefreshRight /></el-icon>
                <span>{{ $t('plugins.reloadAll') }}</span>
              </button>
              <button
                class="header-btn header-btn--primary"
                :disabled="loading"
                data-yui-guide-id="plugin-list-refresh"
                @click="handleRefresh"
              >
                <el-icon><Refresh /></el-icon>
                <span>{{ $t('common.refresh') }}</span>
              </button>
            </div>
          </div>

          <WorkbenchFilterBar
            v-model:filter-text="filterText"
            v-model:use-regex="useRegex"
            v-model:filter-mode="filterMode"
            :regex-error="regexError"
            :rule-groups="filterRuleGroups"
            :placeholder="$t('plugins.filterPlaceholder')"
            :rules-trigger-label="$t('plugins.filterRules')"
            :rules-title="$t('plugins.filterRulesTitle')"
            :rules-hint="$t('plugins.filterRulesHint')"
            :whitelist-label="$t('plugins.filterWhitelist')"
            :blacklist-label="$t('plugins.filterBlacklist')"
            :invalid-regex-label="$t('plugins.invalidRegex')"
            :guide-ids="{ rulesTrigger: 'plugin-list-filter-rules', input: 'plugin-list-filter-input' }"
            class="filter-bar-spacing"
          />

          <WorkbenchToolbar class="toolbar-spacing">
            <WorkbenchGroupFilter
              v-model:selected-ids="selectedTypes"
              :choices="typeFilterChoices"
              :counts="groupCounts"
              guide-id="plugin-list-type-filter"
            />
            <WorkbenchLayoutSwitcher
              v-model:layout-mode="layoutMode"
              :choices="layoutChoices"
              guide-id="plugin-list-layout-mode"
            />
          </WorkbenchToolbar>
        </template>

        <LoadingSpinner
          v-if="loading && rawPlugins.length === 0"
          :loading="true"
          :text="$t('common.loading')"
        />
        <EmptyState v-else-if="rawPlugins.length === 0" :description="$t('plugins.noPlugins')" />

        <template v-else>
          <div
            v-for="(section, si) in pluginSections"
            :key="section.key"
            v-motion
            :initial="{ opacity: 0, y: 20 }"
            :enter="{ opacity: 1, y: 0, transition: { delay: 120 + si * 80, duration: 420, type: 'spring', stiffness: 220, damping: 22 } }"
          >
            <PluginGridSection
              :title="section.title"
              :icon="section.icon"
              :items="section.items"
              :layout-mode="layoutMode"
              :multi-select-enabled="multiSelectEnabled"
              :selected-plugin-ids="selectedPluginIds"
              :show-metrics="showMetrics"
              :show-source-detail="showSourceDetail"
              :variant="section.variant"
              @item-click="handlePluginPrimaryAction"
              @item-contextmenu="handlePluginContextMenu"
              @toggle-selection="togglePluginSelection"
            />
          </div>
        </template>
      </el-card>
    </section>

    <aside
      class="plugin-workbench__rail plugin-workbench__rail--package"
      :aria-hidden="!packagePanelVisible"
      :inert="!packagePanelVisible"
    >
      <div class="plugin-workbench__rail-inner">
        <PackageManagerPanel
          v-if="packagePanelEverOpened"
          v-show="packagePanelVisible"
          embedded
          :external-selected-plugin-ids="selectedPluginIds"
          @close="closePackagePanel"
        />
      </div>
    </aside>

    <!-- Floating multi-select action bar -->
    <Transition name="float-bar">
      <div v-if="multiSelectEnabled" class="floating-select-bar" data-yui-guide-id="plugin-list-float-bar">
        <!-- Batch operations row (visible when plugins are selected) -->
        <Transition name="batch-row">
          <div v-if="selectedCount > 0" class="floating-select-bar__batch">
            <button
              class="fab-action fab-action--batch fab-action--success"
              :disabled="batchBusy"
              @click="handleBatchStart"
            >
              <el-icon><VideoPlay /></el-icon>
              <span>{{ $t('plugins.start') }}</span>
            </button>
            <button
              class="fab-action fab-action--batch fab-action--warn"
              :disabled="batchBusy"
              @click="handleBatchStop"
            >
              <el-icon><VideoPause /></el-icon>
              <span>{{ $t('plugins.stop') }}</span>
            </button>
            <button
              class="fab-action fab-action--batch"
              :disabled="batchBusy"
              @click="handleBatchReload"
            >
              <el-icon><RefreshRight /></el-icon>
              <span>{{ $t('plugins.reload') }}</span>
            </button>
            <div class="floating-select-bar__batch-divider" />
            <button
              class="fab-action fab-action--batch fab-action--danger"
              :disabled="batchBusy"
              @click="handleBatchDelete"
            >
              <el-icon><Delete /></el-icon>
              <span>{{ $t('plugins.delete') }}</span>
            </button>
            <div class="floating-select-bar__batch-divider" />
            <button
              class="fab-action fab-action--batch fab-action--export"
              :disabled="batchBusy"
              @click="handleBatchExport"
            >
              <el-icon><Download /></el-icon>
              <span>{{ $t('plugins.export') }}</span>
            </button>
          </div>
        </Transition>

        <!-- Selection controls row -->
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
      :message="dangerDialogMessage"
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
import { Refresh, DataAnalysis, RefreshRight, Box, Connection, Expand, Finished, Sort, CircleClose, Close, VideoPlay, VideoPause, Delete, Upload, Download, ShoppingCart, ArrowRight, ArrowLeft, InfoFilled, User } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { usePluginStore } from '@/stores/plugin'
import { useMetricsStore } from '@/stores/metrics'
import { useMarketVersionsStore } from '@/stores/marketVersions'
import PluginGridSection from '@/components/plugin/PluginGridSection.vue'
import PluginContextMenu from '@/components/plugin/PluginContextMenu.vue'
import PluginDangerConfirmDialog from '@/components/plugin/PluginDangerConfirmDialog.vue'
import PackageManagerPanel from '@/components/plugin/PackageManagerPanel.vue'
import MarketPanel from '@/components/plugin/MarketPanel.vue'
import LoadingSpinner from '@/components/common/LoadingSpinner.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import WorkbenchFilterBar from '@/components/common/WorkbenchFilterBar.vue'
import WorkbenchGroupFilter from '@/components/common/WorkbenchGroupFilter.vue'
import WorkbenchLayoutSwitcher from '@/components/common/WorkbenchLayoutSwitcher.vue'
import WorkbenchToolbar from '@/components/common/WorkbenchToolbar.vue'
import type {
  FilterRuleGroupDescriptor,
  GroupChoiceDescriptor,
  LayoutChoiceDescriptor,
} from '@/composables/workbenchDescriptors'
import { getMarketUrl } from '@/api/market'
import { reloadAllPlugins, deletePlugin } from '@/api/plugins'
import { uploadAndInstallPlugin, buildPluginCli, downloadPluginPackage } from '@/api/pluginCli'
import { usePluginListContextActions, type ResolvedPluginListAction } from '@/composables/usePluginListContextActions'
import { usePluginWorkbench } from '@/composables/usePluginWorkbench'
import { useMarketAuth } from '@/composables/useMarketAuth'
import { METRICS_REFRESH_INTERVAL } from '@/utils/constants'
import { formatHttpError } from '@/utils/request'
import { resolveLocalizedText } from '@/utils/i18nLabel'
import { useI18n } from 'vue-i18n'
import type { PluginMeta } from '@/types/api'

const route = useRoute()
const router = useRouter()
const pluginStore = usePluginStore()
const metricsStore = useMetricsStore()
const { t, locale } = useI18n()
const { buildActions, executeAction, shouldUseHoldConfirm } = usePluginListContextActions()
const TUTORIAL_ACTION_EVENT = 'neko:plugin-tutorial:action'

const reloadingAll = ref(false)
const batchBusy = ref(false)
const importing = ref(false)
const importFileInputRef = ref<HTMLInputElement | null>(null)
const packagePanelVisible = ref(false)
const packagePanelEverOpened = ref(false)
const marketPanelVisible = ref(false)
const marketPanelEverOpened = ref(false)
const contextMenuVisible = ref(false)
const contextMenuPosition = ref({ x: 0, y: 0 })
const contextMenuPlugin = ref<(PluginMeta & { status?: string; enabled?: boolean; autoStart?: boolean }) | null>(null)
const contextMenuActions = ref<ResolvedPluginListAction[]>([])
const dangerDialogVisible = ref(false)
const dangerDialogLoading = ref(false)
const pendingDangerAction = ref<ResolvedPluginListAction | null>(null)
const pendingDangerPlugin = ref<(PluginMeta & { status?: string; enabled?: boolean; autoStart?: boolean }) | null>(null)
const marketUrl = ref('')
const {
  marketAuth,
  marketAuthBusy,
  marketAuthDisplayName,
  loadMarketAuthStatus,
  logoutMarketAccount,
  startMarketLogin,
} = useMarketAuth()

// confirm_message 是 LocalizedText（string 或 locale-keyed dict），不能直接
// 透传给 PluginDangerConfirmDialog 的 :message="string" prop。模板里
// `pendingDangerAction?.confirm_message || t(...)` 在 dict 真值时会跳过
// fallback 并把 dict 渲染成 "[object Object]"。统一走 helper 解析。
const dangerDialogMessage = computed(() => {
  const fallback = t('plugins.dangerDialog.deleteMessage', {
    pluginName: pendingDangerPlugin.value?.name || pendingDangerPlugin.value?.id || '',
  })
  return resolveLocalizedText(
    pendingDangerAction.value?.confirm_message,
    locale.value,
    fallback,
  )
})

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
  groupCounts,
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
const showMetrics = ref(false)
let metricsRefreshTimer: number | null = null

// Show-source-detail mirrors the Metrics toggle pattern. Turning it on
// also kicks off a best-effort fetch of the Market's latest versions so
// the "update available" badge can light up on market-installed plugins.
const showSourceDetail = ref(false)
const marketVersionsStore = useMarketVersionsStore()

async function toggleSourceDetail() {
  showSourceDetail.value = !showSourceDetail.value
  if (showSourceDetail.value) {
    // Fire-and-forget; if Market is unreachable the badge simply won't
    // appear, which is a fine degraded state.
    marketVersionsStore.ensureFresh().catch((err) => {
      console.warn('Failed to refresh market versions:', err)
    })
  }
}
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

const typeFilterChoices = computed<GroupChoiceDescriptor[]>(() => [
  { id: 'plugin', label: t('plugins.typePlugin'), icon: Box },
  { id: 'adapter', label: t('plugins.typeAdapter'), icon: Connection },
  { id: 'extension', label: t('plugins.typeExtension'), icon: Expand },
])

const layoutChoices = computed<LayoutChoiceDescriptor[]>(() => [
  { value: 'list', label: t('plugins.layoutList') },
  { value: 'single', label: t('plugins.layoutSingle') },
  { value: 'double', label: t('plugins.layoutDouble') },
  { value: 'compact', label: t('plugins.layoutCompact') },
])

const filterRuleGroups = computed<FilterRuleGroupDescriptor[]>(() => [
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

type PluginListRefreshMode = 'full' | 'initial-open' | 'light'

async function refreshPluginListData(mode: PluginListRefreshMode) {
  let warningMessage = ''
  try {
    if (mode === 'full') {
      const syncResult = await pluginStore.syncRegistryAndFetch()
      warningMessage = syncResult.warningMessage || ''
    } else if (mode === 'initial-open') {
      try {
        const syncResult = await pluginStore.ensurePluginListRegistrySynced()
        warningMessage = syncResult?.warningMessage || ''
        if (!syncResult) {
          await pluginStore.fetchPlugins()
        }
      } catch (syncError) {
        console.warn('Failed to sync plugin registry on first plugin list open:', syncError)
        await pluginStore.fetchPlugins()
      }
    } else {
      await pluginStore.fetchPlugins()
    }
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

async function handleRefresh() {
  await refreshPluginListData('full')
}

async function refreshAfterPluginChange() {
  await refreshPluginListData('full')
}

async function refreshForInitialOpen() {
  await refreshPluginListData('initial-open')
}

async function toggleMetrics() {
  if (!showMetrics.value) {
    showMetrics.value = true
    // Only fetch if there are running plugins
    if (runningPlugins.value.length > 0) {
      try {
        await metricsStore.fetchAllMetrics()
      } catch (error) {
        console.warn('Failed to fetch initial metrics:', error)
      }
    }
    startMetricsAutoRefresh()
  } else {
    showMetrics.value = false
    stopMetricsAutoRefresh()
  }
}

function startMetricsAutoRefresh() {
  stopMetricsAutoRefresh()
  metricsRefreshTimer = window.setInterval(() => {
    // Skip refresh if no running plugins
    if (runningPlugins.value.length === 0) return
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
  const next = !packagePanelVisible.value
  packagePanelVisible.value = next
  if (next) {
    packagePanelEverOpened.value = true
    marketPanelVisible.value = false
  }
}

function openPackagePanel() {
  packagePanelVisible.value = true
  packagePanelEverOpened.value = true
  marketPanelVisible.value = false
}

function closePackagePanel() {
  packagePanelVisible.value = false
}

function toggleMarketPanel() {
  const next = !marketPanelVisible.value
  marketPanelVisible.value = next
  if (next) {
    marketPanelEverOpened.value = true
    packagePanelVisible.value = false
  }
}

function closeMarketPanel() {
  marketPanelVisible.value = false
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

async function loadMarketEntry() {
  try {
    const url = await getMarketUrl()
    if (!url) return
    marketUrl.value = url
    loadMarketAuthStatus().catch(() => {})
  } catch {
    // Market is optional; keep the local plugin list usable if it is absent.
  }
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

function getTutorialPlugin() {
  return filteredPurePlugins.value[0] || filteredAdapters.value[0] || filteredExtensions.value[0] || rawPlugins.value[0] || null
}

async function showTutorialContextMenu() {
  const plugin = getTutorialPlugin()
  if (!plugin) {
    return
  }
  packagePanelVisible.value = false
  await nextTick()
  const target = document.querySelector('[data-yui-guide-id="plugin-list-card"]') as HTMLElement | null
  const rect = target?.getBoundingClientRect()
  contextMenuPlugin.value = plugin
  contextMenuActions.value = buildActions(plugin)
  contextMenuPosition.value = {
    x: rect ? Math.min(rect.left + rect.width * 0.72, window.innerWidth - 240) : window.innerWidth / 2,
    y: rect ? Math.min(rect.top + 24, window.innerHeight - 260) : window.innerHeight / 2,
  }
  contextMenuVisible.value = contextMenuActions.value.length > 0
}

function openTutorialPluginDetail() {
  const plugin = getTutorialPlugin()
  if (!plugin) {
    return
  }
  handlePluginClick(plugin.id)
}

function handleTutorialAction(event: Event) {
  const action = (event as CustomEvent<{ action?: string }>).detail?.action
  if (action === 'open-package-panel') {
    openPackagePanel()
    return
  }
  if (action === 'show-plugin-context-menu') {
    void showTutorialContextMenu()
    return
  }
  if (action === 'open-first-plugin-detail') {
    openTutorialPluginDetail()
  }
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
  return rawNormalPlugins.value.filter((plugin) => plugin.status === 'running')
})

// ── Import (upload + install) ─────────────────────────────────────────

function triggerImportFile() {
  importFileInputRef.value?.click()
}

async function handleImportFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return

  // Reset input so the same file can be re-selected
  input.value = ''

  importing.value = true
  try {
    const result = await uploadAndInstallPlugin(file)
    const count = result.install.installed_plugin_count ?? 0
    ElMessage.success(t('plugins.importSuccess', { name: file.name, count }))
    await refreshAfterPluginChange()
  } catch (error: any) {
    console.error('Failed to import plugin package:', error)
    const detail = formatHttpError(error)
    ElMessage.error(detail ? t('plugins.importFailed') + ': ' + detail : t('plugins.importFailed'))
  } finally {
    importing.value = false
  }
}

// ── Export (build + download) ─────────────────────────────────────────

async function handleBatchExport() {
  const plugins = getSelectedPlugins()
  if (plugins.length === 0) return

  const ids = plugins.map((p) => p.id)
  const isSingle = ids.length === 1

  batchBusy.value = true
  try {
    const result = await buildPluginCli(
      isSingle
        ? { mode: 'single', plugin: ids[0] }
        : { mode: 'bundle', plugins: ids },
    )

    if (result.built.length === 0) {
      const firstFailure = result.failed?.[0]
      const detail = firstFailure?.error
        ? firstFailure.plugin
          ? `${firstFailure.plugin}: ${firstFailure.error}`
          : firstFailure.error
        : ''
      ElMessage.error(detail ? `${t('plugins.exportBuildFailed')}: ${detail}` : t('plugins.exportBuildFailed'))
      return
    }

    // Download each built file using the full path returned by backend
    for (const built of result.built) {
      downloadPluginPackage(built.package_path)
    }

    if (result.failed && result.failed.length > 0) {
      ElMessage.warning(t('plugins.batchPartial', { success: result.built.length, fail: result.failed.length }))
    } else {
      ElMessage.success(t('plugins.exportSuccess', { count: result.built.length }))
    }
  } catch (error: any) {
    console.error('Failed to export plugins:', error)
    const detail = formatHttpError(error)
    ElMessage.error(detail ? t('plugins.exportFailed') + ': ' + detail : t('plugins.exportFailed'))
  } finally {
    batchBusy.value = false
  }
}

// ── Batch operations ──────────────────────────────────────────────────

function getSelectedPlugins() {
  return rawPlugins.value.filter((p) => selectedPluginIds.value.includes(p.id))
}

async function handleBatchStart() {
  const plugins = getSelectedPlugins().filter((p) => p.status !== 'running' && p.status !== 'disabled')
  if (plugins.length === 0) {
    ElMessage.info(t('plugins.batchNoStartable'))
    return
  }
  try {
    await ElMessageBox.confirm(
      t('plugins.batchStartConfirm', { count: plugins.length }),
      t('common.confirm'),
      { type: 'info' },
    )
  } catch { return }

  batchBusy.value = true
  let ok = 0; let fail = 0
  for (const p of plugins) {
    try { await pluginStore.start(p.id, { refresh: false }); ok++ } catch { fail++ }
  }
  batchBusy.value = false
  if (fail === 0) ElMessage.success(t('plugins.batchStartSuccess', { count: ok }))
  else ElMessage.warning(t('plugins.batchPartial', { success: ok, fail }))
  await refreshAfterPluginChange()
}

async function handleBatchStop() {
  const plugins = getSelectedPlugins().filter((p) => p.status === 'running')
  if (plugins.length === 0) {
    ElMessage.info(t('plugins.batchNoStoppable'))
    return
  }
  try {
    await ElMessageBox.confirm(
      t('plugins.batchStopConfirm', { count: plugins.length }),
      t('common.confirm'),
      { type: 'warning' },
    )
  } catch { return }

  batchBusy.value = true
  let ok = 0; let fail = 0
  for (const p of plugins) {
    try { await pluginStore.stop(p.id, { refresh: false }); ok++ } catch { fail++ }
  }
  batchBusy.value = false
  if (fail === 0) ElMessage.success(t('plugins.batchStopSuccess', { count: ok }))
  else ElMessage.warning(t('plugins.batchPartial', { success: ok, fail }))
  await refreshAfterPluginChange()
}

async function handleBatchReload() {
  const plugins = getSelectedPlugins().filter((p) => p.status === 'running')
  if (plugins.length === 0) {
    ElMessage.info(t('plugins.batchNoReloadable'))
    return
  }
  try {
    await ElMessageBox.confirm(
      t('plugins.batchReloadConfirm', { count: plugins.length }),
      t('common.confirm'),
      { type: 'warning' },
    )
  } catch { return }

  batchBusy.value = true
  let ok = 0; let fail = 0
  for (const p of plugins) {
    try { await pluginStore.reload(p.id, { refresh: false }); ok++ } catch { fail++ }
  }
  batchBusy.value = false
  if (fail === 0) ElMessage.success(t('plugins.batchReloadSuccess', { count: ok }))
  else ElMessage.warning(t('plugins.batchPartial', { success: ok, fail }))
  await refreshAfterPluginChange()
}

async function handleBatchDelete() {
  const plugins = getSelectedPlugins()
  if (plugins.length === 0) return
  try {
    await ElMessageBox.confirm(
      t('plugins.batchDeleteConfirm', { count: plugins.length }),
      t('common.confirm'),
      { type: 'error', confirmButtonText: t('common.delete') },
    )
  } catch { return }

  batchBusy.value = true
  let ok = 0; let fail = 0
  for (const p of plugins) {
    try { await deletePlugin(p.id); ok++ } catch { fail++ }
  }
  batchBusy.value = false
  clearSelection()
  if (fail === 0) ElMessage.success(t('plugins.batchDeleteSuccess', { count: ok }))
  else ElMessage.warning(t('plugins.batchPartial', { success: ok, fail }))
  await refreshAfterPluginChange()
}

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

  await refreshAfterPluginChange()
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
      if (shouldOpen) {
        packagePanelEverOpened.value = true
        marketPanelVisible.value = false
      }
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
  window.addEventListener(TUTORIAL_ACTION_EVENT, handleTutorialAction)
  await Promise.all([loadMarketEntry(), refreshForInitialOpen()])
})

onUnmounted(() => {
  window.removeEventListener(TUTORIAL_ACTION_EVENT, handleTutorialAction)
  closePluginContextMenu()
  closeDangerDialog()
  stopMetricsAutoRefresh()
})
</script>

<style scoped>
.plugin-workbench {
  --plugin-entry-radius: var(--radius-card);
  --drawer-width: clamp(320px, 42vw, 620px);
  --drawer-duration: 320ms;
  --drawer-ease: cubic-bezier(0.22, 1, 0.36, 1);
  /* ── Unified radius system ── */
  --radius-card: 16px;       /* large containers: card, dropdown */
  --radius-panel: 14px;      /* medium panels: filter bar, toolbar, floating bar */
  --radius-control: 10px;    /* buttons, inputs, interactive controls */
  --radius-chip: 8px;
  display: flex;
  align-items: stretch;
  gap: 20px;
  min-width: 0;
  padding-bottom: 80px; /* space for floating bar */
}

.plugin-workbench__main {
  flex: 1 1 0;
  min-width: 0;
}

.plugin-workbench__rail {
  flex: 0 0 0;
  width: 0;
  min-width: 0;
  align-self: stretch;
  position: relative;
  overflow: hidden;
  contain: layout paint size;
  transition:
    flex-basis var(--drawer-duration) var(--drawer-ease),
    width var(--drawer-duration) var(--drawer-ease),
    margin var(--drawer-duration) var(--drawer-ease);
  margin: 0;
}

/* 收起时取消它那一侧的 gap，避免主列表多出一条空白 */
.plugin-workbench__rail--market { margin-right: -20px; }
.plugin-workbench__rail--package { margin-left: -20px; }

.plugin-workbench--market-open .plugin-workbench__rail--market,
.plugin-workbench--package-open .plugin-workbench__rail--package {
  flex-basis: var(--drawer-width);
  width: var(--drawer-width);
  margin: 0;
}

/* 面板内容固定宽度，完全脱离 rail 的 flex 布局，只靠 transform 滑入 */
.plugin-workbench__rail-inner {
  position: absolute;
  top: 0;
  bottom: 0;
  width: var(--drawer-width);
  max-width: 100%;
  transition: transform var(--drawer-duration) var(--drawer-ease);
  will-change: transform;
}

.plugin-workbench__rail--market .plugin-workbench__rail-inner {
  left: 0;
  transform: translate3d(-100%, 0, 0);
}

.plugin-workbench__rail--package .plugin-workbench__rail-inner {
  right: 0;
  transform: translate3d(100%, 0, 0);
}

.plugin-workbench--market-open .plugin-workbench__rail--market .plugin-workbench__rail-inner,
.plugin-workbench--package-open .plugin-workbench__rail--package .plugin-workbench__rail-inner {
  transform: translate3d(0, 0, 0);
}

.plugin-workbench__rail-inner > * {
  width: 100%;
  height: 100%;
}

.plugin-list-card {
  border-radius: var(--radius-card);
}

.workbench-header {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  justify-content: flex-start;
  gap: 10px;
}

.workbench-header__copy {
  display: flex;
  align-items: center;
  min-width: 0;
  flex: 0 1 auto;
  gap: 10px;
  flex-wrap: wrap;
}

/* ── Market quick-access trigger (top-left) ── */
.market-trigger {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px 8px 12px;
  border: 1px solid color-mix(in srgb, var(--el-color-primary) 24%, transparent);
  border-radius: var(--radius-control);
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--el-color-primary) 10%, transparent) 0%,
    color-mix(in srgb, var(--el-color-primary) 2%, transparent) 100%
  );
  color: var(--el-color-primary);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  transition:
    transform 0.22s ease,
    box-shadow 0.22s ease,
    border-color 0.22s ease,
    background-color 0.22s ease;
}

.market-trigger:hover {
  transform: translateY(-1px);
  border-color: var(--el-color-primary);
  box-shadow: 0 6px 16px color-mix(in srgb, var(--el-color-primary) 18%, transparent);
}

.market-trigger--active {
  background: var(--el-color-primary);
  color: #fff;
  border-color: var(--el-color-primary);
  box-shadow: 0 6px 16px color-mix(in srgb, var(--el-color-primary) 22%, transparent);
}

.market-trigger__icon {
  font-size: 16px;
}

.market-trigger__arrow {
  font-size: 14px;
  opacity: 0.75;
  transition: transform 0.22s ease;
}

.market-trigger:hover .market-trigger__arrow {
  transform: translateX(2px);
}

.market-trigger--active .market-trigger__arrow {
  opacity: 1;
  transform: translateX(0);
}

.market-auth-trigger {
  --el-button-border-radius: var(--radius-control);
  font-weight: 600;
  padding: 8px 14px;
  gap: 6px;
}

.market-auth-trigger--connected {
  --el-button-text-color: var(--el-color-success);
  --el-button-border-color: color-mix(in srgb, var(--el-color-success) 35%, transparent);
  --el-button-bg-color: color-mix(in srgb, var(--el-color-success) 8%, transparent);
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

/* ── Header action buttons (unified) ── */
.header-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border: 1px solid color-mix(in srgb, var(--el-border-color) 60%, transparent);
  border-radius: var(--radius-control);
  background: color-mix(in srgb, var(--el-bg-color) 92%, white);
  color: var(--el-text-color-regular);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition:
    background-color 0.2s ease,
    border-color 0.2s ease,
    color 0.2s ease,
    transform 0.18s ease,
    box-shadow 0.2s ease;
}

.header-btn .el-icon {
  font-size: 15px;
}

.header-btn:hover {
  border-color: color-mix(in srgb, var(--el-color-primary) 30%, var(--el-border-color));
  color: var(--el-color-primary);
  background: color-mix(in srgb, var(--el-color-primary) 6%, var(--el-bg-color));
  transform: translateY(-1px);
  box-shadow: 0 4px 12px color-mix(in srgb, var(--el-color-primary) 10%, transparent);
}

.header-btn:active {
  transform: translateY(0) scale(0.97);
}

.header-btn:disabled {
  opacity: 0.45;
  pointer-events: none;
}

/* Active state (toggled on) */
.header-btn--active {
  border-color: color-mix(in srgb, var(--el-color-primary) 40%, var(--el-border-color));
  color: var(--el-color-primary);
  background: color-mix(in srgb, var(--el-color-primary) 8%, var(--el-bg-color));
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--el-color-primary) 12%, transparent);
}

/* Color variants */
.header-btn--accent {
  border-style: dashed;
  border-color: color-mix(in srgb, var(--el-color-success) 40%, var(--el-border-color));
  color: var(--el-color-success);
  background: color-mix(in srgb, var(--el-color-success) 4%, var(--el-bg-color));
}

.header-btn--accent:hover {
  border-color: var(--el-color-success);
  color: var(--el-color-success);
  background: color-mix(in srgb, var(--el-color-success) 10%, var(--el-bg-color));
  box-shadow: 0 4px 12px color-mix(in srgb, var(--el-color-success) 16%, transparent);
}

.header-btn--primary {
  border-color: color-mix(in srgb, var(--el-color-primary) 30%, var(--el-border-color));
  color: var(--el-color-primary);
  background: color-mix(in srgb, var(--el-color-primary) 4%, var(--el-bg-color));
}

.header-btn--primary:hover {
  border-color: var(--el-color-primary);
  background: color-mix(in srgb, var(--el-color-primary) 10%, var(--el-bg-color));
  box-shadow: 0 4px 12px color-mix(in srgb, var(--el-color-primary) 16%, transparent);
}

.header-btn--success {
  border-color: color-mix(in srgb, var(--el-color-success) 30%, var(--el-border-color));
  color: var(--el-color-success);
  background: color-mix(in srgb, var(--el-color-success) 4%, var(--el-bg-color));
}

.header-btn--success:hover {
  border-color: var(--el-color-success);
  background: color-mix(in srgb, var(--el-color-success) 10%, var(--el-bg-color));
  box-shadow: 0 4px 12px color-mix(in srgb, var(--el-color-success) 16%, transparent);
}

.header-btn--warn {
  border-color: color-mix(in srgb, var(--el-color-warning) 30%, var(--el-border-color));
  color: var(--el-color-warning);
  background: color-mix(in srgb, var(--el-color-warning) 4%, var(--el-bg-color));
}

.header-btn--warn:hover {
  border-color: var(--el-color-warning);
  background: color-mix(in srgb, var(--el-color-warning) 10%, var(--el-bg-color));
  box-shadow: 0 4px 12px color-mix(in srgb, var(--el-color-warning) 16%, transparent);
}

.import-file-input {
  display: none;
}

/* ── Export button in batch row ── */
.fab-action--export:hover {
  background: color-mix(in srgb, var(--el-color-primary) 10%, transparent);
  color: var(--el-color-primary);
}

/* ── Floating bottom action bar ── */
.floating-select-bar {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 2000;
  pointer-events: auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
}

/* ── Batch operations row ── */
.floating-select-bar__batch {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 10px;
  border-radius: var(--radius-panel);
  background: color-mix(in srgb, var(--el-bg-color) 78%, transparent);
  backdrop-filter: blur(20px) saturate(1.6);
  -webkit-backdrop-filter: blur(20px) saturate(1.6);
  border: 1px solid color-mix(in srgb, var(--el-border-color) 40%, transparent);
  box-shadow:
    0 12px 40px color-mix(in srgb, var(--el-text-color-primary) 12%, transparent),
    0 4px 16px color-mix(in srgb, var(--el-color-primary) 6%, transparent),
    inset 0 1px 0 color-mix(in srgb, white 30%, transparent);
}

.floating-select-bar__batch-divider {
  width: 1px;
  height: 20px;
  background: color-mix(in srgb, var(--el-border-color) 50%, transparent);
  flex-shrink: 0;
  margin: 0 2px;
}

.fab-action--batch {
  font-size: 12.5px;
  padding: 6px 12px;
  gap: 5px;
}

.fab-action--batch .el-icon {
  font-size: 15px;
}

.fab-action--batch:disabled {
  opacity: 0.45;
  pointer-events: none;
}

.fab-action--success:hover {
  background: color-mix(in srgb, var(--el-color-success) 10%, transparent);
  color: var(--el-color-success);
}

.fab-action--warn:hover {
  background: color-mix(in srgb, var(--el-color-warning) 10%, transparent);
  color: var(--el-color-warning);
}

/* ── Batch row transition ── */
.batch-row-enter-active {
  transition:
    opacity 0.28s cubic-bezier(0.22, 1, 0.36, 1),
    transform 0.32s cubic-bezier(0.34, 1.56, 0.64, 1),
    max-height 0.32s cubic-bezier(0.22, 1, 0.36, 1);
}

.batch-row-leave-active {
  transition:
    opacity 0.18s ease,
    transform 0.2s ease,
    max-height 0.2s ease;
}

.batch-row-enter-from {
  opacity: 0;
  transform: translateY(8px) scale(0.95);
  max-height: 0;
}

.batch-row-leave-to {
  opacity: 0;
  transform: translateY(4px) scale(0.97);
  max-height: 0;
}

.batch-row-enter-to,
.batch-row-leave-from {
  opacity: 1;
  transform: translateY(0) scale(1);
  max-height: 60px;
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
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-start;
  width: 100%;
  flex: 0 1 auto;
  min-width: 0;
}

/* ── Spacing for workbench filter bar + toolbar ── */
.filter-bar-spacing {
  margin-top: 14px;
}

.toolbar-spacing {
  margin-top: 10px;
}

@media (max-width: 1280px) {
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
