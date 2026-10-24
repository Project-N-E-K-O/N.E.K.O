<template>
  <div
    class="plugin-workbench"
    :class="{
      'plugin-workbench--market-open': drawers.market.visible,
      'plugin-workbench--package-open': drawers.package.visible,
      'plugin-workbench--mirror-open': drawers.mirror.visible,
      'plugin-workbench--multi-select': multiSelectEnabled,
    }"
    data-yui-guide-id="plugin-list-workbench"
  >
    <aside
      class="plugin-workbench__rail plugin-workbench__rail--market"
      :aria-hidden="!drawers.market.visible"
      :inert="!drawers.market.visible"
    >
      <Transition name="drawer-start">
        <div v-show="drawers.market.visible" class="plugin-workbench__rail-inner">
          <MarketPanel
            v-if="drawers.market.everOpened"
            embedded
            :active="drawers.market.visible"
            @close="drawers.market.visible = false"
          />
        </div>
      </Transition>
    </aside>

    <section
      class="plugin-workbench__main"
      data-yui-guide-id="plugin-list-main"
    >
      <el-card class="plugin-list-card" data-yui-guide-id="plugin-list-card-shell">
        <template #header>
          <div
            ref="workbenchHeaderRef"
            class="workbench-header"
            :class="{ 'workbench-header--compact': headerActionsCompact }"
          >
            <!-- 左：市场相关入口；右：模式 │ 面板 │ 视图选项 │ 数据操作。窄头部只保留图标。 -->
            <div ref="headerMarketRef" class="workbench-header__market">
              <button
                v-if="marketUrl"
                class="market-trigger"
                :class="{ 'market-trigger--active': drawers.market.visible }"
                type="button"
                data-yui-guide-id="plugin-list-market-toggle"
                :title="drawers.market.visible ? $t('market.closeMarket') : $t('market.openMarket')"
                :aria-label="headerActionsCompact ? (drawers.market.visible ? $t('market.closeMarket') : $t('market.openMarket')) : undefined"
                @click="toggleDrawer('market')"
              >
                <el-icon class="market-trigger__icon"><ShoppingCart /></el-icon>
                <span v-if="!headerActionsCompact" class="market-trigger__label">
                  {{ drawers.market.visible ? $t('market.closeMarket') : $t('market.getNewPlugins') }}
                </span>
                <el-icon class="market-trigger__arrow">
                  <component :is="drawers.market.visible ? ArrowLeft : ArrowRight" />
                </el-icon>
              </button>
              <el-button
                v-if="marketUrl"
                class="plugin-update-trigger"
                :loading="pluginUpdatesStore.checking"
                plain
                data-yui-guide-id="plugin-list-update-check"
                :title="$t('pluginUpdates.button')"
                :aria-label="headerActionsCompact ? $t('pluginUpdates.button') : undefined"
                @click="handleCheckUpdates"
              >
                <el-icon><Top /></el-icon>
                <span v-if="!headerActionsCompact">{{ pluginUpdatesStore.checking ? $t('pluginUpdates.buttonBusy') : $t('pluginUpdates.button') }}</span>
                <span
                  v-if="pluginUpdatesStore.candidates.length > 0"
                  class="plugin-update-trigger__badge"
                >{{ pluginUpdatesStore.candidates.length }}</span>
              </el-button>
              <el-button
                v-if="marketUrl && marketAuth.auth_state === 'pending'"
                class="market-auth-trigger"
                :loading="marketLogoutBusy"
                aria-live="polite"
                :aria-label="headerActionsCompact ? `${$t('market.authVerificationPendingLabel')} · ${$t('market.logout')}` : undefined"
                :title="$t('market.logout')"
                plain
                @click="confirmMarketLogout"
              >
                <el-icon><User /></el-icon>
                <span v-if="!headerActionsCompact">{{ $t('market.authVerificationPendingLabel') }} · {{ $t('market.logout') }}</span>
              </el-button>
              <el-popover
                v-else-if="marketUrl && marketAuth.authenticated"
                placement="bottom-start"
                :width="300"
                trigger="click"
                popper-class="market-account-popover"
                @show="loadMarketAccountSummary"
              >
                <template #reference>
                  <el-button
                    class="market-auth-trigger market-auth-trigger--connected"
                    :loading="marketAuthBusy"
                    :title="$t('market.accountConnected', { name: marketAuthDisplayName })"
                    plain
                  >
                    <el-icon><User /></el-icon>
                    <span v-if="!headerActionsCompact">{{ $t('market.accountConnected', { name: marketAuthDisplayName }) }}</span>
                  </el-button>
                </template>
                <div class="market-account-card" v-loading="marketAccountSummaryBusy">
                  <div class="market-account-card__identity">
                    <el-avatar :size="44" :src="marketAccountSummary?.profile?.avatar_url || ''">
                      {{ marketAuthDisplayName.slice(0, 1) }}
                    </el-avatar>
                    <div>
                      <strong>{{ marketAccountSummary?.profile?.display_name || marketAuthDisplayName }}</strong>
                      <p v-if="marketAccountSummary?.profile?.username">
                        @{{ marketAccountSummary.profile.username }}
                      </p>
                    </div>
                  </div>
                  <p v-if="marketAccountSummaryBusy" class="market-account-card__hint">
                    {{ $t('market.accountSummaryLoading') }}
                  </p>
                  <p
                    v-else-if="marketAuthStateMessageKey"
                    class="market-account-card__hint"
                  >
                    {{ $t(marketAuthStateMessageKey) }}
                  </p>
                  <div v-else-if="marketAccountSummary?.market" class="market-account-card__stats">
                    <span v-if="marketAccountSummary.market.member_days !== null">
                      {{ $t('market.accountMemberDays', { days: marketAccountSummary.market.member_days }) }}
                    </span>
                    <span v-if="marketAccountSummary.market.published_plugins !== null">
                      {{ $t('market.accountPublished', { count: marketAccountSummary.market.published_plugins }) }}
                    </span>
                  </div>
                  <el-button class="market-account-card__logout" text type="danger" @click="confirmMarketLogout">
                    {{ $t('market.logout') }}
                  </el-button>
                </div>
              </el-popover>
              <el-button
                v-else-if="marketUrl"
                class="market-auth-trigger"
                :loading="marketAuthBusy"
                :title="$t('market.login')"
                plain
                @click="startMarketLogin()"
              >
                <el-icon><User /></el-icon>
                <span v-if="!headerActionsCompact">{{ $t('market.login') }}</span>
              </el-button>
            </div>

            <!-- 三级：导入是唯一主按钮；其余为描边次按钮，开关态用浅底；低频视图选项和全部重载收进“更多”。 -->
            <div ref="headerActionsRef" class="header-actions" data-testid="plugin-list-header-actions">
              <el-button
                :type="multiSelectEnabled ? 'primary' : 'default'"
                :plain="multiSelectEnabled"
                :aria-pressed="multiSelectEnabled"
                :aria-label="headerActionsCompact ? $t('plugins.multiSelect') : undefined"
                :title="headerActionsCompact ? $t('plugins.multiSelect') : undefined"
                data-yui-guide-id="plugin-list-multi-select"
                @click="toggleMultiSelectMode"
              >
                <el-icon><Finished /></el-icon>
                <span v-if="!headerActionsCompact">{{ $t('plugins.multiSelect') }}</span>
              </el-button>
              <span class="header-actions__divider" aria-hidden="true" />
              <el-button
                class="package-trigger"
                :class="{ 'package-trigger--active': drawers.package.visible }"
                :aria-pressed="drawers.package.visible"
                :aria-label="headerActionsCompact ? $t('plugins.openPackageManager') : undefined"
                :title="headerActionsCompact ? $t('plugins.openPackageManager') : undefined"
                data-yui-guide-id="plugin-list-package-panel-toggle"
                @click="toggleDrawer('package')"
              >
                <el-icon><Box /></el-icon>
                <span v-if="!headerActionsCompact">{{ $t('plugins.openPackageManager') }}</span>
              </el-button>
              <el-button
                :type="drawers.mirror.visible ? 'primary' : 'default'"
                :plain="drawers.mirror.visible"
                :aria-pressed="drawers.mirror.visible"
                :aria-label="headerActionsCompact ? $t('plugins.openMirrorSource') : undefined"
                :title="headerActionsCompact ? $t('plugins.openMirrorSource') : undefined"
                data-yui-guide-id="plugin-list-mirror-source-toggle"
                @click="toggleDrawer('mirror')"
              >
                <el-icon><Connection /></el-icon>
                <span v-if="!headerActionsCompact">{{ $t('plugins.openMirrorSource') }}</span>
              </el-button>
              <span class="header-actions__divider" aria-hidden="true" />
              <el-dropdown trigger="click" :persistent="false" @command="handleMoreAction">
                <el-button
                  :aria-label="$t('plugins.moreActions')"
                  :title="$t('plugins.moreActions')"
                  data-testid="plugin-list-more-actions"
                >
                  <el-icon><MoreFilled /></el-icon>
                </el-button>
                <template #dropdown>
                  <!-- 视觉上是勾选项；menuitem 不能带 aria-checked，读屏用 aria-label 读出将要执行的动作。 -->
                  <el-dropdown-menu class="header-more-menu">
                    <el-dropdown-item
                      command="metrics"
                      :aria-label="showMetrics ? $t('plugins.hideMetrics') : $t('plugins.showMetrics')"
                      data-yui-guide-id="plugin-list-metrics-toggle"
                    >
                      <el-icon class="header-more-menu__check" :class="{ 'is-checked': showMetrics }"><Check /></el-icon>
                      {{ $t('plugins.showMetrics') }}
                    </el-dropdown-item>
                    <el-dropdown-item
                      command="sourceDetail"
                      :aria-label="showSourceDetail ? $t('plugins.hideSourceDetail') : $t('plugins.showSourceDetail')"
                      data-yui-guide-id="plugin-list-source-toggle"
                    >
                      <el-icon class="header-more-menu__check" :class="{ 'is-checked': showSourceDetail }"><Check /></el-icon>
                      {{ $t('plugins.showSourceDetail') }}
                    </el-dropdown-item>
                    <el-dropdown-item command="reloadAll" divided :disabled="reloadAllDisabled">
                      <span class="header-more-menu__danger">
                        <el-icon><RefreshRight /></el-icon>
                        {{ $t('plugins.reloadAll') }}
                      </span>
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
              <span class="header-actions__divider" aria-hidden="true" />
              <el-button
                :disabled="loading"
                :aria-label="headerActionsCompact ? $t('common.refresh') : undefined"
                :title="headerActionsCompact ? $t('common.refresh') : undefined"
                data-yui-guide-id="plugin-list-refresh"
                @click="handleRefresh"
              >
                <el-icon><Refresh /></el-icon>
                <span v-if="!headerActionsCompact">{{ $t('common.refresh') }}</span>
              </el-button>
              <el-button
                type="primary"
                :disabled="importing"
                data-yui-guide-id="plugin-list-import"
                @click="importDialogVisible = true"
              >
                <el-icon><Upload /></el-icon>
                <span>{{ importing ? $t('plugins.importing') : $t('plugins.import') }}</span>
              </el-button>
            </div>

            <WorkbenchFilterBar
              v-model:filter-text="filterText"
              :rule-groups="filterRuleGroups"
              :placeholder="$t('plugins.filterPlaceholder')"
              :rules-trigger-label="$t('plugins.filterRules')"
              :rules-title="$t('plugins.filterRulesTitle')"
              :rules-hint="$t('plugins.filterRulesHint')"
              :guide-ids="{ rulesTrigger: 'plugin-list-filter-rules', input: 'plugin-list-filter-input' }"
            >
              <template #footer>
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
              </template>
            </WorkbenchFilterBar>
          </div>
        </template>

        <div v-if="listRefreshError || listRefreshWarning" class="plugin-list-feedback" data-testid="plugin-list-feedback">
          <ErrorMessage
            :message="listRefreshError ? (hasListData ? t('messages.refreshPartialFailure') : t('plugins.listLoadFailed')) : listRefreshWarning"
            :type="hasListData ? 'warning' : 'error'"
            :closable="false"
          />
          <el-button :disabled="loading" @click="handleRefresh">{{ t('market.retry') }}</el-button>
        </div>
        <LoadingSpinner
          v-if="loading && rawPlugins.length === 0"
          :loading="true"
          :text="$t('common.loading')"
        />
        <template v-else-if="rawPlugins.length === 0">
          <EmptyState v-if="!listRefreshError" :description="$t('plugins.noPlugins')" />
        </template>
        <EmptyState
          v-else-if="filteredPurePlugins.length + filteredAdapters.length === 0"
          :description="t('plugins.noMatches')"
        >
          <el-button @click="clearFilters">{{ t('plugins.clearFilters') }}</el-button>
        </EmptyState>

        <template v-else>
          <div
            v-for="section in pluginSections"
            :key="section.key"
          >
            <PluginGridSection
              :title="section.title"
              :icon="section.icon"
              :items="section.items"
              :motion-phase="filterText.trim() ? 'filter' : 'initial'"
              :layout-mode="layoutMode"
              :multi-select-enabled="multiSelectEnabled"
              :selected-plugin-ids="selectedPluginIds"
              :show-metrics="showMetrics"
              :show-source-detail="showSourceDetail"
              :identity-plugin-ids="duplicateDisplayNamePluginIds"
              :variant="section.variant"
              @item-click="handlePluginPrimaryAction"
              @item-open-ui="handlePluginUiAction"
              @item-contextmenu="handlePluginContextMenu"
              @toggle-selection="togglePluginSelection"
            />
          </div>
        </template>
      </el-card>
    </section>

    <aside
      class="plugin-workbench__rail plugin-workbench__rail--package"
      :aria-hidden="!drawers.package.visible"
      :inert="!drawers.package.visible"
    >
      <Transition name="drawer-end">
        <div v-show="drawers.package.visible" class="plugin-workbench__rail-inner">
          <PackageManagerPanel
            v-if="drawers.package.everOpened"
            embedded
            :external-selected-plugin-ids="selectedPluginIds"
            @close="drawers.package.visible = false"
          />
        </div>
      </Transition>
    </aside>

    <aside
      class="plugin-workbench__rail plugin-workbench__rail--mirror"
      :aria-hidden="!drawers.mirror.visible"
      :inert="!drawers.mirror.visible"
    >
      <Transition name="drawer-end">
        <div v-show="drawers.mirror.visible" class="plugin-workbench__rail-inner">
          <GithubMirrorSourcePanel
            v-if="drawers.mirror.everOpened"
            @close="drawers.mirror.visible = false"
          />
        </div>
      </Transition>
    </aside>

    <!-- Floating multi-select action bar; teleported so the route transition's
         transform never becomes its containing block. -->
    <Teleport to="body">
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
    </Teleport>

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

    <PluginImportDialog
      v-model:visible="importDialogVisible"
      @busy-change="importing = $event"
      @installed="refreshAfterPluginChange"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { AxiosError } from 'axios'
import { Refresh, RefreshRight, Box, Connection, Finished, Sort, CircleClose, Close, VideoPlay, VideoPause, Delete, Upload, Download, ShoppingCart, ArrowRight, ArrowLeft, User, Top, MoreFilled, Check } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { usePluginStore } from '@/stores/plugin'
import { useMetricsStore } from '@/stores/metrics'
import { useMarketVersionsStore, type MarketVersionTarget } from '@/stores/marketVersions'
import { usePluginUpdatesStore } from '@/stores/pluginUpdates'
import PluginGridSection from '@/components/plugin/PluginGridSection.vue'
import PluginContextMenu from '@/components/plugin/PluginContextMenu.vue'
import PluginImportDialog from '@/components/plugin/PluginImportDialog.vue'
import ErrorMessage from '@/components/common/ErrorMessage.vue'
import PluginDangerConfirmDialog from '@/components/plugin/PluginDangerConfirmDialog.vue'
import { deferredPanel } from '@/components/common/deferredPanel'
const PackageManagerPanel = deferredPanel(() => import('@/components/plugin/PackageManagerPanel.vue'))
const GithubMirrorSourcePanel = deferredPanel(() => import('@/components/plugin/GithubMirrorSourcePanel.vue'))
const MarketPanel = deferredPanel(() => import('@/components/plugin/MarketPanel.vue'))
import LoadingSpinner from '@/components/common/LoadingSpinner.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import WorkbenchFilterBar from '@/components/common/WorkbenchFilterBar.vue'
import WorkbenchGroupFilter from '@/components/common/WorkbenchGroupFilter.vue'
import WorkbenchLayoutSwitcher from '@/components/common/WorkbenchLayoutSwitcher.vue'
import type {
  FilterRuleGroupDescriptor,
  GroupChoiceDescriptor,
  LayoutChoiceDescriptor,
} from '@/composables/workbenchDescriptors'
import { getMarketUrl } from '@/api/market'
import { deletePlugin } from '@/api/plugins'
import { buildPluginCli, downloadPluginPackage } from '@/api/pluginCli'
import { usePluginListContextActions, type ResolvedPluginListAction } from '@/composables/usePluginListContextActions'
import { usePluginWorkbench } from '@/composables/usePluginWorkbench'
import { useBatchActions } from '@/composables/useBatchActions'
import { useMarketAuth } from '@/composables/useMarketAuth'
import { useCollapsibleLabels } from '@/composables/useCollapsibleLabels'
import { METRICS_REFRESH_INTERVAL, PANEL_HOST_MIN_HEIGHT } from '@/utils/constants'
import { formatHttpError, isRequestTimeout } from '@/utils/request'
import { resolveLocalizedText } from '@/utils/i18nLabel'
import { findDuplicatePluginDisplayNameIds, isOrdinaryPlugin } from '@/utils/pluginDisplay'
import { openExternalUrl } from '@/utils/openExternal'
import { isOpenUiNavigationAction } from '@/utils/pluginListActions'
import { useI18n } from 'vue-i18n'
import type {
  PluginInstallSourceDetailMarket,
  PluginListAction,
  PluginMeta,
} from '@/types/api'

const route = useRoute()
const router = useRouter()
const pluginStore = usePluginStore()
const metricsStore = useMetricsStore()
const { t, locale } = useI18n()
const { buildActions, executeAction, shouldUseHoldConfirm } = usePluginListContextActions()
const TUTORIAL_ACTION_EVENT = 'neko:plugin-tutorial:action'

const reloadingAll = ref(false)
const importing = ref(false)
const importDialogVisible = ref(false)
type DrawerKey = 'package' | 'mirror' | 'market'
interface DrawerState {
  visible: boolean
  everOpened: boolean
}
const DRAWER_KEYS: readonly DrawerKey[] = ['package', 'mirror', 'market']
const drawers = reactive<Record<DrawerKey, DrawerState>>({
  package: { visible: false, everOpened: false },
  mirror: { visible: false, everOpened: false },
  market: { visible: false, everOpened: false },
})
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
  marketLogoutBusy,
  marketAuthDisplayName,
  marketAuthStateMessageKey,
  marketAccountSummary,
  marketAccountSummaryBusy,
  loadMarketAuthStatus,
  loadMarketAccountSummary,
  logoutMarketAccount,
  startMarketLogin,
} = useMarketAuth()

async function confirmMarketLogout() {
  try {
    await ElMessageBox.confirm(
      t('market.logoutConfirm'),
      t('common.logoutConfirmTitle'),
      { confirmButtonText: t('common.confirm'), cancelButtonText: t('common.cancel'), type: 'warning' },
    )
  } catch (error) {
    const action = typeof error === 'object' && error !== null && 'action' in error
      ? (error as { action?: unknown }).action
      : error
    if (action === 'cancel' || action === 'close') return
    ElMessage.error(error instanceof Error ? error.message : t('market.logoutFailed'))
    return
  }

  try {
    await logoutMarketAccount()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('market.logoutFailed'))
  }
}

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

const rawPlugins = computed(() => pluginStore.pluginSummariesWithStatus.filter(isOrdinaryPlugin))
const duplicateDisplayNamePluginIds = computed(() => [
  ...findDuplicatePluginDisplayNameIds(rawPlugins.value, locale.value),
])
const {
  filterText,
  selectedTypes,
  layoutMode,
  selectedCount,
  multiSelectEnabled,
  groupCounts,
  filteredPurePlugins,
  filteredAdapters,
  selectedPluginIds,
  togglePlugin: togglePluginSelection,
  selectAllVisible,
  invertVisibleSelection,
  clearSelection,
  pruneSelection,
  toggleMultiSelect,
} = usePluginWorkbench(rawPlugins)

// Covers the registry sync that precedes the summary fetch, so a first open
// shows the skeleton instead of flashing the empty state.
const pendingListRefreshes = ref(0)
const loading = computed(() => pendingListRefreshes.value > 0)
const hasLoadedList = ref(false)
const hasListData = computed(() => hasLoadedList.value || rawPlugins.value.length > 0)
const listRefreshError = ref(false)
const listRefreshWarning = ref('')
let listRefreshSequence = 0
const showMetrics = ref(false)
let metricsRefreshTimer: number | null = null

// Show-source-detail mirrors the Metrics toggle pattern. Turning it on
// also kicks off a best-effort fetch of the Market's latest versions so
// the "update available" badge can light up on market-installed plugins.
const showSourceDetail = ref(false)
const marketVersionsStore = useMarketVersionsStore()
const pluginUpdatesStore = usePluginUpdatesStore()

/** Toolbar entry point for the update float window. The check itself is
 *  best-effort — a failure only lands in the logs, never in the UI. */
function handleCheckUpdates(): void {
  void pluginUpdatesStore.openFromButton()
}

function installedMarketVersionTargets(): MarketVersionTarget[] {
  const targets: MarketVersionTarget[] = []
  for (const plugin of pluginStore.pluginSummariesWithStatus) {
    const installSource = plugin.install_source
    if (installSource?.source !== 'market') continue
    const detail = installSource.source_detail as PluginInstallSourceDetailMarket | null
    const pluginId = String(detail?.plugin_market_id || '').trim()
    if (!/^\d+$/.test(pluginId)) continue
    targets.push({
      pluginId,
      channel: detail?.channel === 'beta' ? 'beta' : 'stable',
    })
  }
  return targets
}

function refreshInstalledMarketVersions(): void {
  // Fire-and-forget; if Market is unreachable the badge simply won't appear.
  // ``ensureFresh`` compares the target signature, so status-only updates do
  // not cause a network request while a newly installed Market plugin does.
  marketVersionsStore.ensureFresh(installedMarketVersionTargets()).catch((err) => {
    console.warn('Failed to refresh market versions:', err)
  })
}

async function toggleSourceDetail() {
  showSourceDetail.value = !showSourceDetail.value
  if (showSourceDetail.value) {
    refreshInstalledMarketVersions()
  }
}

// The initial plugin fetch and a Market install can both finish after source
// details become visible. Recompute the target set on either change so update
// badges are not held to the empty/stale snapshot from the original toggle.
function marketVersionTargetSignature() {
  return installedMarketVersionTargets()
    .map((target) => `${target.pluginId}:${target.channel}`)
    .sort()
    .join('|')
}

watch(
  marketVersionTargetSignature,
  () => {
    if (showSourceDetail.value) refreshInstalledMarketVersions()
  },
)

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
])

const typeFilterChoices = computed<GroupChoiceDescriptor[]>(() => [
  { id: 'plugin', label: t('plugins.typePlugin'), icon: Box },
  { id: 'adapter', label: t('plugins.typeAdapter'), icon: Connection },
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
      { token: 'is:ui', label: t('plugins.filterRuleLabels.ui') },
      { token: 'has:entries', label: t('plugins.filterRuleLabels.entries') },
    ],
  },
  {
    key: 'meta',
    title: t('plugins.filterRuleGroups.meta'),
    rules: [
      { token: 'name:', label: t('plugins.filterRuleLabels.name') },
      { token: 'id:', label: t('plugins.filterRuleLabels.id') },
      { token: 'version:', label: t('plugins.filterRuleLabels.version') },
      { token: 'entry:', label: t('plugins.filterRuleLabels.entry') },
      { token: 'author:', label: t('plugins.filterRuleLabels.author') },
    ],
  },
])

type PluginListRefreshMode = 'full' | 'after-change' | 'initial-open' | 'light'

async function refreshPluginListData(mode: PluginListRefreshMode) {
  const sequence = ++listRefreshSequence
  let warningMessage = ''
  let failed = false
  pendingListRefreshes.value += 1
  try {
    if (mode === 'full' || mode === 'after-change') {
      const syncResult = await pluginStore.syncRegistryAndFetchSummaries({ invalidateInstalled: mode === 'after-change' })
      warningMessage = syncResult.warningMessage || ''
    } else if (mode === 'initial-open') {
      try {
        const syncResult = await pluginStore.ensurePluginListRegistrySynced()
        warningMessage = syncResult?.warningMessage || ''
        if (!syncResult) {
          await pluginStore.fetchPluginSummaries()
        }
      } catch (syncError) {
        console.warn('Failed to sync plugin registry on first plugin list open:', syncError)
        await pluginStore.fetchPluginSummaries()
        warningMessage = t('messages.refreshPartialFailure')
      }
    } else {
      await pluginStore.fetchPluginSummaries()
    }
    hasLoadedList.value = true
    if (mode === 'full' || mode === 'after-change') {
      if (await pluginStore.fetchPluginStatus(undefined, true) === 'failed') warningMessage ||= t('messages.refreshPartialFailure')
    } else {
      if (await pluginStore.ensurePluginStatus() === 'failed') warningMessage ||= t('messages.refreshPartialFailure')
    }
  } catch (error) {
    console.warn('Failed to refresh plugin data:', error)
    failed = true
  } finally {
    pendingListRefreshes.value -= 1
    if (sequence === listRefreshSequence) {
      listRefreshError.value = failed
      listRefreshWarning.value = warningMessage
    }
  }
  if (showMetrics.value) {
    try {
      await metricsStore.fetchAllMetrics()
    } catch (error) {
      console.warn('Failed to refresh metrics:', error)
    }
  }
  if (sequence === listRefreshSequence && warningMessage) {
    ElMessage.warning(warningMessage)
  }
}

function clearFilters() {
  filterText.value = ''
  selectedTypes.value = ['plugin', 'adapter']
}

async function handleRefresh() {
  await refreshPluginListData('full')
}

async function refreshAfterPluginChange() {
  await refreshPluginListData('after-change')
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
  if (document.hidden) return
  stopMetricsAutoRefresh()
  metricsRefreshTimer = window.setInterval(() => {
    // Skip refresh if no running plugins
    if (runningPlugins.value.length === 0) return
    metricsStore.fetchAllMetrics().catch((error) => {
      console.warn('Auto-refresh metrics failed:', error)
    })
  }, METRICS_REFRESH_INTERVAL)
}

function handleMetricsVisibilityChange() {
  if (document.hidden) stopMetricsAutoRefresh()
  else if (showMetrics.value) startMetricsAutoRefresh()
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

async function handlePluginUiAction(plugin: PluginMeta, action: PluginListAction) {
  const resolvedAction = buildActions(plugin).find((candidate) => (
    candidate.source === 'plugin'
      && isOpenUiNavigationAction(candidate)
      && candidate.id === action.id
  ))
  if (!resolvedAction || resolvedAction.disabled) return

  if (shouldUseHoldConfirm(resolvedAction)) {
    openDangerDialog(resolvedAction, plugin)
    return
  }

  const target = action.target?.trim() || ''
  if (target) {
    await executeAction(resolvedAction, plugin)
    return
  }

  const confirmMessage = resolveLocalizedText(action.confirm_message, locale.value, '')
  if (confirmMessage) {
    try {
      await ElMessageBox.confirm(confirmMessage, t('common.confirm'), {
        type: action.danger ? 'warning' : 'info',
      })
    } catch {
      return
    }
  }

  const fallback = {
    path: `/plugins/${encodeURIComponent(plugin.id)}`,
    query: { tab: 'ui' },
  }
  if (action.open_in === 'same_tab') {
    await router.push(fallback)
  } else {
    openExternalUrl(router.resolve(fallback).href)
  }
}

function toggleMultiSelectMode() {
  toggleMultiSelect()
}

// Drawers are mutually exclusive: opening one closes the others.
function openDrawer(key: DrawerKey) {
  for (const other of DRAWER_KEYS) {
    drawers[other].visible = other === key
  }
  drawers[key].everOpened = true
}

function toggleDrawer(key: DrawerKey) {
  if (drawers[key].visible) {
    drawers[key].visible = false
  } else {
    openDrawer(key)
  }
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
  if (isRequestTimeout(error)) {
    return false
  }
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
  return filteredPurePlugins.value[0] || filteredAdapters.value[0] || rawPlugins.value[0] || null
}

async function showTutorialContextMenu() {
  const plugin = getTutorialPlugin()
  if (!plugin) {
    return
  }
  drawers.package.visible = false
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
    openDrawer('package')
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
  return rawPlugins.value.filter((plugin) => plugin.status === 'running')
})

const reloadAllDisabled = computed(() => reloadingAll.value || runningPlugins.value.length === 0)

// 头部按内容测量（市场/包管理抽屉打开后主区变窄；文案随语言和登录态变化），而不是固定阈值。
const workbenchHeaderRef = ref<HTMLElement | null>(null)
const headerMarketRef = ref<HTMLElement | null>(null)
const headerActionsRef = ref<HTMLElement | null>(null)
const { compact: headerActionsCompact } = useCollapsibleLabels(
  workbenchHeaderRef,
  [headerMarketRef, headerActionsRef],
  () => [
    locale.value,
    marketUrl.value,
    marketAuth.value.auth_state,
    marketAuth.value.authenticated,
    marketAuthDisplayName.value,
    pluginUpdatesStore.checking,
    importing.value,
  ],
)

type MoreActionCommand = 'metrics' | 'sourceDetail' | 'reloadAll'

function handleMoreAction(command: MoreActionCommand) {
  if (command === 'metrics') void toggleMetrics()
  else if (command === 'sourceDetail') void toggleSourceDetail()
  else void handleReloadAll()
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

const { busy: batchBusy, runBatch } = useBatchActions(getSelectedPlugins, refreshAfterPluginChange)

async function handleBatchStart() {
  await runBatch({
    filter: (p) => p.status !== 'running' && p.status !== 'disabled',
    action: (p) => pluginStore.start(p.id, { refresh: false }),
    noItemsKey: 'plugins.batchNoStartable',
    confirmKey: 'plugins.batchStartConfirm',
    confirmType: 'info',
    successKey: 'plugins.batchStartSuccess',
    partialKey: 'plugins.batchPartial',
  })
}

async function handleBatchStop() {
  await runBatch({
    filter: (p) => p.status === 'running',
    action: (p) => pluginStore.stop(p.id, { refresh: false }),
    noItemsKey: 'plugins.batchNoStoppable',
    confirmKey: 'plugins.batchStopConfirm',
    confirmType: 'warning',
    successKey: 'plugins.batchStopSuccess',
    partialKey: 'plugins.batchPartial',
  })
}

async function handleBatchReload() {
  await runBatch({
    filter: (p) => p.status === 'running',
    action: (p) => pluginStore.reload(p.id, { refresh: false }),
    noItemsKey: 'plugins.batchNoReloadable',
    confirmKey: 'plugins.batchReloadConfirm',
    confirmType: 'warning',
    successKey: 'plugins.batchReloadSuccess',
    partialKey: 'plugins.batchPartial',
  })
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
  const restartWarnings: string[] = []
  for (const p of plugins) {
    try {
      const result = await deletePlugin(p.id)
      ok++
      if (result.restored_builtin_restart_error) {
        restartWarnings.push(t('messages.pluginDeletedBuiltinRestartFailed', {
          plugin: p.name,
          error: result.restored_builtin_restart_error.message,
        }))
      }
    } catch { fail++ }
  }
  batchBusy.value = false
  clearSelection()
  if (restartWarnings.length > 0) ElMessage.warning(restartWarnings.join('; '))
  if (fail > 0) ElMessage.warning(t('plugins.batchPartial', { success: ok, fail }))
  else if (restartWarnings.length === 0) {
    ElMessage.success(t('plugins.batchDeleteSuccess', { count: ok }))
  }
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
    // 全部重载也要清掉被重启插件的待重载标记，否则列表重载后编辑器仍会显示提示。
    const result = await pluginStore.reloadAll({ refresh: false })
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
    if (!isRequestTimeout(error as AxiosError)) {
      ElMessage.error(t('messages.reloadFailed'))
    }
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
    if (drawers.package.visible !== shouldOpen) {
      if (shouldOpen) {
        openDrawer('package')
      } else {
        drawers.package.visible = false
      }
    }
  },
  { immediate: true },
)

watch(() => drawers.package.visible, (visible) => {
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
  document.addEventListener('visibilitychange', handleMetricsVisibilityChange)
  await Promise.all([loadMarketEntry(), refreshForInitialOpen()])
})

onUnmounted(() => {
  window.removeEventListener(TUTORIAL_ACTION_EVENT, handleTutorialAction)
  document.removeEventListener('visibilitychange', handleMetricsVisibilityChange)
  closePluginContextMenu()
  closeDangerDialog()
  stopMetricsAutoRefresh()
})
</script>

<style scoped>
.plugin-list-feedback {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.plugin-workbench {
  --plugin-entry-radius: var(--radius-card);
  --drawer-width: clamp(320px, 42vw, 620px);
  display: flex;
  align-items: stretch;
  /*
    页面根绑定到 .app-main 内容盒的确定高度，让这个工作台变成"视口内固定、两栅各自滚动"。
    不绑的话，右侧抽屉（rail）的高度会被**左侧列表栅的内容高度**左右：列表长时抽屉被拉到
    3459px（里面一大片空白，面板内容一长就多出一条滚动条）；列表短/为空时抽屉被压到 0px，
    而 rail 是 overflow:hidden → 整个面板被裁掉、看不全。
    矮窗口用与详情页同一个下限常量兜底，不够高时由 .app-main 滚动。
    详见文件末尾 .plugin-workbench__main 与抽屉打开态的说明。
  */
  height: 100%;
  min-height: v-bind('PANEL_HOST_MIN_HEIGHT');
  gap: 20px;
  min-width: 0;
}

/* 多选浮动条预留。原先写在上面的 .plugin-workbench 里（padding-bottom: 80px），
   但浮动条是 position:fixed，而预留挂在**整个工作台**上就意味着给页面底部永久留白：
   工作台绑成视口高度后，底部会一直露出一条 80px 的空白带，面板背景到不了底。
   改成：只在该模式下给**可滚内容**加底部内边距（它们本来就是滚动容器），
   最后一行依旧能滚到浮动条上方；抽屉的高度则是 padding box，绝对定位的内容会自动让位。 */
.plugin-workbench--multi-select .plugin-list-card :deep(.el-card__body),
.plugin-workbench--multi-select .plugin-workbench__rail-inner > * {
  padding-bottom: 80px;
}

.plugin-workbench__main {
  flex: 1 1 0;
  min-width: 0;
  /* 列表栅不再由内容撑高：撑高就会把抽屉一起拉长（见 .plugin-workbench 上的说明）。
     改为自己滞：卡片体内阶滚动（el-card__body 本身已是 overflow:auto）。 */
  min-height: 0;
  display: flex;
  flex-direction: column;
}

/* 只命中最外层列表卡片；切记不要写成后代选择器，否则会连网格里的插件卡片一起 flex 化。 */
.plugin-workbench__main > .el-card {
  flex: 1 1 0;
  min-height: 0;
}

.plugin-workbench__rail {
  flex: 0 0 0;
  width: 0;
  min-width: 0;
  align-self: stretch;
  position: relative;
  overflow: hidden;
  contain: layout paint size;
  /* 宽度不做过渡：开关抽屉时列表宽度一步到位，卡片由网格的 reflow 动画一次滑到新位置；
     宽度连续过渡会让列数多次跨阈值、reflow 反复重启。滑入滑出只交给 rail-inner 的 transform。 */
  margin: 0;
}

/* 收起时取消它那一侧的 gap，避免主列表多出一条空白 */
.plugin-workbench__rail--market { margin-right: -20px; }
.plugin-workbench__rail--package { margin-left: -20px; }
.plugin-workbench__rail--mirror { margin-left: -20px; }

.plugin-workbench--market-open .plugin-workbench__rail--market,
.plugin-workbench--package-open .plugin-workbench__rail--package,
.plugin-workbench--mirror-open .plugin-workbench__rail--mirror {
  flex-basis: var(--drawer-width);
  width: var(--drawer-width);
  margin: 0;
}

/* 关闭时 rail 宽度立即归零（列表一步到位），面板还在滑出：这段时间取消 rail 的裁剪，
   让面板从列表上方滑出工作区，由 .app-body 的圆角裁掉。z-index 盖过正在 reflow 的卡片。 */
.plugin-workbench__rail:has(> :is(.drawer-start-leave-active, .drawer-end-leave-active)) {
  z-index: 1;
  overflow: visible;
  contain: layout size;
}

/* 面板内容固定宽度，完全脱离 rail 的 flex 布局，只靠 transform 滑入滑出。
   靠工作区边缘的三边留出 shell 间距，面板作为独立圆角卡片浮在工作区里；
   朝向列表的一边由工作台的 gap 分隔。 */
.plugin-workbench__rail-inner {
  position: absolute;
  top: 0;
  bottom: 0;
  width: var(--drawer-width);
  max-width: 100%;
  padding-block: var(--shell-spacing);
}

.plugin-workbench__rail--market .plugin-workbench__rail-inner {
  left: 0;
  padding-left: var(--shell-spacing);
}

.plugin-workbench__rail--package .plugin-workbench__rail-inner,
.plugin-workbench__rail--mirror .plugin-workbench__rail-inner {
  right: 0;
  padding-right: var(--shell-spacing);
}

/* 与网格 reflow 同一条曲线和时长，面板滑入滑出和卡片让位是同一个动作。 */
.drawer-start-enter-active,
.drawer-start-leave-active,
.drawer-end-enter-active,
.drawer-end-leave-active {
  transition: transform var(--motion-duration-layout) var(--motion-ease-layout);
}

/* rail 已归零，max-width: 100% 会把滑出中的面板压成 0 宽。 */
.drawer-start-leave-active,
.drawer-end-leave-active {
  max-width: none;
}

.drawer-start-enter-from,
.drawer-start-leave-to {
  transform: translate3d(-100%, 0, 0);
}

.drawer-end-enter-from,
.drawer-end-leave-to {
  transform: translate3d(100%, 0, 0);
}

.plugin-workbench__rail-inner > * {
  width: 100%;
  /*
    面板本身就是滚动容器：白底、圆角、边框画在固定高度的卡片上，内容在卡片里滚动，
    滚动条也落在卡片的圆角边框之内，和左侧列表的滚动区域界限分明。
  */
  height: 100%;
  overflow-y: auto;
}

.plugin-list-card {
  border: none;
  border-radius: 0;
  background: var(--surface-workspace);
  box-shadow: none;
}

.plugin-list-card :deep(> .el-card__header) {
  background: var(--surface-workspace);
  border-bottom-color: var(--surface-border);
}

/* 单行头部：左侧市场组，右侧动作组；空间不足时动作组整体换到下一行，不拆散。 */
.workbench-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px 16px;
}

.workbench-header__market,
.header-actions {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 8px;
}

.workbench-header__market :deep(.el-button),
.header-actions :deep(.el-button) {
  --el-button-border-radius: var(--radius-control);
  margin-left: 0;
  /* 收起文字会改内边距；Element Plus 默认 transition: all，按钮组宽度会在过渡中被测到。 */
  transition-property: color, background-color, border-color;
}

.header-actions {
  margin-left: auto;
}

/* 筛选块始终独占按钮行下方的一整行。 */
.workbench-header > .wb-filter-bar {
  flex-basis: 100%;
}

.header-actions__divider {
  width: 1px;
  height: 18px;
  background: var(--surface-border);
}

.workbench-header--compact .header-actions :deep(.el-button),
.workbench-header--compact .workbench-header__market :deep(.el-button) {
  padding-inline: 9px;
}

.workbench-header--compact .market-trigger {
  padding: 8px 10px;
}

/* “更多”菜单被 teleport 到 body，只有本模板写进插槽的节点带 scoped 属性。 */
.header-more-menu__check {
  margin-right: 8px;
  visibility: hidden;
}

.header-more-menu__check.is-checked {
  visibility: visible;
  color: var(--el-color-primary);
}

.header-more-menu__danger {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--el-color-danger);
}

/* ── Market quick-access trigger (top-left) ── */
/* 浅蓝渐变底配主色字；面板打开后转为实心主色。 */
.market-trigger {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px 8px 12px;
  border: 1px solid color-mix(in srgb, var(--el-color-primary) 30%, var(--surface-card));
  border-radius: var(--radius-control);
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--el-color-primary) 14%, var(--surface-card)) 0%,
    color-mix(in srgb, var(--el-color-primary) 4%, var(--surface-card)) 100%
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

.market-trigger:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}

.market-trigger--active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary);
  color: var(--on-primary);
  box-shadow: 0 6px 16px color-mix(in srgb, var(--el-color-primary) 22%, transparent);
}

/* 包管理用 success 语义色：平时浅绿底绿字，面板打开时实心绿底。 */
.header-actions :deep(.package-trigger) {
  --el-button-text-color: var(--el-color-success);
  --el-button-bg-color: var(--el-color-success-light-9);
  --el-button-border-color: var(--el-color-success-light-5);
  --el-button-hover-text-color: var(--el-color-success);
  --el-button-hover-bg-color: var(--el-color-success-light-8);
  --el-button-hover-border-color: var(--el-color-success-light-3);
  --el-button-active-text-color: var(--el-color-success);
  --el-button-active-bg-color: var(--el-color-success-light-7);
  --el-button-active-border-color: var(--el-color-success);
  font-weight: 600;
}

.header-actions :deep(.package-trigger--active) {
  --el-button-text-color: var(--on-primary);
  --el-button-bg-color: var(--el-color-success);
  --el-button-border-color: var(--el-color-success);
  --el-button-hover-text-color: var(--on-primary);
  --el-button-hover-bg-color: var(--el-color-success-light-3);
  --el-button-hover-border-color: var(--el-color-success-light-3);
  --el-button-active-text-color: var(--on-primary);
  --el-button-active-bg-color: var(--el-color-success-dark-2);
  --el-button-active-border-color: var(--el-color-success-dark-2);
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

.plugin-update-trigger {
  --el-button-border-radius: var(--radius-control);
  font-weight: 600;
}

/* Count lives inside the button so the toolbar keeps a single row. */
.plugin-update-trigger__badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  margin-left: 6px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--el-color-danger);
  color: var(--on-danger);
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  font-variant-numeric: tabular-nums;
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

.market-account-card {
  display: grid;
  gap: 12px;
}

.market-account-card__identity {
  display: flex;
  align-items: center;
  gap: 10px;
}

.market-account-card__identity strong,
.market-account-card__identity p {
  display: block;
  margin: 0;
}

.market-account-card__identity p,
.market-account-card__hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.market-account-card__stats {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  color: var(--el-text-color-regular);
  font-size: 12px;
}

.market-account-card__logout {
  justify-self: start;
  padding-left: 0;
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
  background: var(--surface-card);
  border: 1px solid var(--surface-border);
  box-shadow: var(--surface-shadow-hover);
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
  background: var(--surface-card);
  border: 1px solid color-mix(in srgb, var(--el-color-primary) 18%, var(--surface-border));
  box-shadow: var(--surface-shadow-hover);
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
  color: var(--on-primary);
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
