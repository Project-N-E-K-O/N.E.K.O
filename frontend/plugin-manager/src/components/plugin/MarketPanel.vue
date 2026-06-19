<template>
  <div
    class="market-panel"
    :class="{ 'market-panel--embedded': embedded }"
    data-yui-guide-id="market-panel-root"
  >
    <div
      v-if="embedded"
      class="market-panel__heading"
      data-yui-guide-id="market-panel-heading"
    >
      <div class="market-panel__heading-copy">
        <div class="market-panel__heading-title">
          <el-icon><ShoppingCart /></el-icon>
          <span>{{ t('market.title') }}</span>
        </div>
        <span class="market-panel__heading-hint">{{ t('market.subtitle') }}</span>
      </div>
      <div class="market-panel__heading-actions">
        <!-- v2 (R7.2): channel 切换 popover —— 决定 Market 列表按 stable/beta 拉取。 -->
        <el-popover
          placement="bottom-end"
          :width="240"
          trigger="click"
          popper-class="market-panel__channel-popover"
        >
          <template #reference>
            <button
              class="market-panel__icon-btn"
              :title="t('settings.channel')"
            >
              <el-icon><Setting /></el-icon>
            </button>
          </template>
          <div class="market-panel__channel-form">
            <div class="market-panel__channel-label">
              {{ t('settings.channel') }}
            </div>
            <el-radio-group v-model="userPref.channel" size="small">
              <el-radio-button value="stable">
                {{ t('settings.channelStable') }}
              </el-radio-button>
              <el-radio-button value="beta">
                {{ t('settings.channelBeta') }}
              </el-radio-button>
            </el-radio-group>
            <p class="market-panel__channel-hint">
              {{ t('settings.channelHint') }}
            </p>
          </div>
        </el-popover>
        <button
          v-if="marketBaseUrl"
          class="market-panel__icon-btn"
          :title="t('market.openInBrowser')"
          @click="openMarketExternal"
        >
          <el-icon><Link /></el-icon>
        </button>
        <el-button text circle @click="$emit('close')">
          <el-icon><Close /></el-icon>
        </el-button>
      </div>
    </div>

    <WorkbenchFilterBar
      v-model:filter-text="filterText"
      v-model:use-regex="useRegex"
      v-model:filter-mode="filterMode"
      :regex-error="regexError"
      :rule-groups="filterRuleGroups"
      :placeholder="t('market.searchPlaceholder')"
      :rules-trigger-label="t('market.filterRules')"
      :rules-title="t('market.filterRulesTitle')"
      :rules-hint="t('market.filterRulesHint')"
      :whitelist-label="t('plugins.filterWhitelist')"
      :blacklist-label="t('plugins.filterBlacklist')"
      :invalid-regex-label="t('plugins.invalidRegex')"
    />

    <WorkbenchToolbar class="market-panel__toolbar">
      <WorkbenchGroupFilter
        v-model:selected-ids="selectedGroupIds"
        :choices="groupChoices"
        :counts="groupCounts"
        selection-mode="single"
      />
      <div class="market-panel__toolbar-right">
        <el-select
          v-model="sortBy"
          size="small"
          class="market-panel__sort"
          @change="onSortChange"
        >
          <el-option
            v-for="opt in sortOptions"
            :key="opt.value"
            :value="opt.value"
            :label="opt.label"
          />
        </el-select>
        <WorkbenchLayoutSwitcher
          v-model:layout-mode="layoutMode"
          :choices="layoutChoices"
        />
      </div>
    </WorkbenchToolbar>

    <div class="market-panel__content">
      <EmptyState
        v-if="!marketAvailable && !loading"
        :description="t('market.notConfigured')"
      >
        <template #description>
          <p>{{ t('market.notConfigured') }}</p>
          <p class="market-panel__empty-hint">{{ t('market.configHint') }}</p>
        </template>
      </EmptyState>

      <LoadingSpinner
        v-else-if="loading && plugins.length === 0"
        :loading="true"
        :text="t('common.loading')"
      />

      <EmptyState
        v-else-if="lastLoadFailed && plugins.length === 0"
        :description="t('market.loadFailed')"
      >
        <el-button type="primary" :loading="loading" @click="loadPlugins">
          {{ t('market.retry') }}
        </el-button>
      </EmptyState>

      <EmptyState
        v-else-if="filteredItems.length === 0"
        :description="t('market.noResults')"
      />

      <template v-else>
        <GridSection
          :title="activeGroupLabel"
          :items="filteredItems"
          :layout-mode="layoutMode"
          :multi-select-enabled="false"
          :selected-ids="[]"
          variant="default"
          guide-prefix="market-panel"
        >
          <template #item="{ item }">
            <MarketPluginCard
              :plugin="item"
              :installed="isInstalled(item)"
              :installing="installingId === item.id"
              :local-version="getLocalInstalledVersion(item)"
              :yanked="isYanked(item)"
              :upgrading="upgradingId === item.id"
              @click="handlePluginClick(item)"
              @install="handleInstall(item)"
              @upgrade="handleUpgrade(item)"
            />
          </template>
        </GridSection>

        <div v-if="totalPages > 1" class="market-panel__pagination">
          <el-pagination
            v-model:current-page="currentPage"
            :page-size="pageSize"
            :total="totalCount"
            :small="embedded"
            layout="prev, pager, next, total"
            @current-change="handlePageChange"
          />
        </div>
      </template>
    </div>

    <el-dialog
      v-model="installTaskDialogVisible"
      :title="installTaskTitle"
      width="420px"
      :close-on-click-modal="false"
      :show-close="installTaskDone"
    >
      <div class="market-install-progress">
        <el-progress
          :percentage="installTaskPercent"
          :status="installTaskStatus"
        />
        <div class="market-install-progress__message">
          {{ activeInstallTask?.message || t('market.installPreparing') }}
        </div>
        <div class="market-install-progress__meta">
          <span>{{ installTaskStageLabel }}</span>
          <span v-if="downloadProgressText">{{ downloadProgressText }}</span>
        </div>
        <el-alert
          v-if="activeInstallTask?.rollback?.running"
          type="warning"
          :closable="false"
          show-icon
          :title="t('market.rollbackRunning')"
        />
        <el-alert
          v-else-if="activeInstallTask?.rollback?.restored"
          type="success"
          :closable="false"
          show-icon
          :title="t('market.rollbackCompleted')"
        />
        <el-alert
          v-if="activeInstallTask?.error"
          type="error"
          :closable="false"
          show-icon
          :title="activeInstallTask.error"
        />
      </div>
      <template #footer>
        <el-button
          v-if="installTaskDone"
          type="primary"
          @click="installTaskDialogVisible = false"
        >
          {{ t('common.close') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { ShoppingCart, Close, Link, Setting } from '@element-plus/icons-vue'
import MarketPluginCard from '@/components/plugin/MarketPluginCard.vue'
import LoadingSpinner from '@/components/common/LoadingSpinner.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import WorkbenchFilterBar from '@/components/common/WorkbenchFilterBar.vue'
import WorkbenchGroupFilter from '@/components/common/WorkbenchGroupFilter.vue'
import WorkbenchLayoutSwitcher from '@/components/common/WorkbenchLayoutSwitcher.vue'
import WorkbenchToolbar from '@/components/common/WorkbenchToolbar.vue'
import GridSection from '@/components/common/GridSection.vue'
import {
  fetchMarketPlugins,
  fetchMarketPluginVersions,
  getMarketUrl,
  isMarketAvailable,
  type MarketPlugin,
  type FetchMarketPluginsParams,
} from '@/api/market'
import { useMarketWorkbench, type MarketWorkbenchItem } from '@/composables/useMarketWorkbench'
import type {
  FilterRuleGroupDescriptor,
  GroupChoiceDescriptor,
  LayoutChoiceDescriptor,
} from '@/composables/workbenchDescriptors'
import { usePluginStore } from '@/stores/plugin'
import { useUserPreferenceStore } from '@/stores/userPreference'
import { narrowMarketChannel } from '@/utils/narrowChannel'
import { openExternalUrl } from '@/utils/openExternal'

interface Props {
  embedded?: boolean
  /** 外部触发的打开事件，用于切换可见时重新校验状态 */
  active?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  embedded: false,
  active: true,
})

defineEmits<{ close: [] }>()

const { t } = useI18n()
const pluginStore = usePluginStore()
const userPref = useUserPreferenceStore()

const loading = ref(false)
// ``marketAvailable`` is the Market *configuration* flag set once by
// ``isMarketAvailable()`` during initialize. ``lastLoadFailed`` tracks the
// most recent ``loadPlugins`` outcome separately so a transient API error
// does not lock the user out of the panel.
const marketAvailable = ref(false)
const lastLoadFailed = ref(false)
const marketBaseUrl = ref<string | null>(null)
const plugins = ref<MarketPlugin[]>([])
const currentPage = ref(1)
const pageSize = props.embedded ? 8 : 12
const totalCount = ref(0)
const installingId = ref<string | null>(null)
const upgradingId = ref<string | number | null>(null)
const bridgeToken = ref('')

interface MarketInstallTask {
  task_id: string
  status: string
  stage: string
  progress: number
  message: string
  downloaded_bytes?: number
  total_bytes?: number | null
  error?: string | null
  error_code?: string | null
  rollback?: {
    running?: boolean
    restored?: boolean
  } | null
}

const installTaskDialogVisible = ref(false)
const activeInstallTask = ref<MarketInstallTask | null>(null)
const activeInstallPluginName = ref('')
const activeInstallMode = ref<'install' | 'upgrade' | 'reinstall'>('install')

const installTaskDone = computed(() => {
  const status = activeInstallTask.value?.status
  return status === 'completed' || status === 'failed'
})

const installTaskPercent = computed(() =>
  Math.round((activeInstallTask.value?.progress ?? 0) * 100),
)

const installTaskStatus = computed(() => {
  const status = activeInstallTask.value?.status
  if (status === 'failed') return 'exception'
  if (status === 'completed') return 'success'
  return undefined
})

const installTaskTitle = computed(() => {
  const name = activeInstallPluginName.value
  if (activeInstallMode.value === 'upgrade' || activeInstallMode.value === 'reinstall') {
    return t('market.installDialogTitleUpgrade', { name })
  }
  return t('market.installDialogTitle', { name })
})

const installTaskStageLabel = computed(() => {
  const stage = activeInstallTask.value?.stage || 'pending'
  const key = `market.installStage.${stage}`
  const translated = t(key)
  return translated === key ? stage : translated
})

function formatByteCount(value: number): string {
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${value} B`
}

const downloadProgressText = computed(() => {
  const task = activeInstallTask.value
  if (!task || task.stage !== 'download') return ''
  const downloaded = task.downloaded_bytes ?? 0
  if (task.total_bytes) {
    return `${formatByteCount(downloaded)} / ${formatByteCount(task.total_bytes)}`
  }
  return formatByteCount(downloaded)
})

function beginInstallTaskTracking(
  taskId: string,
  pluginName: string,
  mode: 'install' | 'upgrade' | 'reinstall' = 'install',
) {
  activeInstallPluginName.value = pluginName
  activeInstallMode.value = mode
  activeInstallTask.value = {
    task_id: taskId,
    status: 'pending',
    stage: 'pending',
    progress: 0,
    message: t('market.installPreparing'),
  }
  installTaskDialogVisible.value = true
}

function markInstallTaskFailed(
  taskId: string,
  message: string,
  options: { error?: string } = {},
) {
  activeInstallTask.value = {
    ...(activeInstallTask.value || {
      task_id: taskId,
      progress: 0,
      message,
    }),
    status: 'failed',
    stage: 'failed',
    error: options.error ?? message,
  }
}

function resolveInstallTaskErrorMessage(task: MarketInstallTask): string {
  const code = task.error_code || ''
  if (code === 'version_already_at_target') return t('market.upgradeAlreadyAtTarget')
  if (code === 'upgrade_target_not_greater') return t('market.upgradeTargetNotGreater')
  if (code === 'plugin_not_installed_for_upgrade') return t('market.pluginNotInstalled')
  if (code === 'upgrade_rollback_completed') return t('market.upgradeRollback')
  if (code === 'lock_write_failed') return t('market.lockWriteFailed')
  return task.message || task.error || t('market.installFailed')
}
const sortBy = ref<'created_at' | 'download_count' | 'rating_average' | 'name'>('created_at')
const sortOrder = ref<'asc' | 'desc'>('desc')

// 已装插件 (plugin_id → installed version + latest_install_source) 索引，
// 由 /market/installed 拉回。yank 检测和 upgrade 按钮判定都从这里读。
interface InstalledMarketEntry {
  plugin_id: string
  market_id?: string
  installed_version: string
  channel: 'stable' | 'beta'
  package_url: string
}
const installedByPid = ref<Map<string, InstalledMarketEntry>>(new Map())
// pluginId → 当前装的版本是否已被作者撤回（v2 yank 检测）
const yankedMap = ref<Record<string, boolean>>({})

// 5 分钟内存缓存：避免每次渲染都打 Market versions 接口
const yankCache = new Map<
  string,
  { fetchedAt: number; yankedVersions: Set<string> }
>()
const YANK_TTL_MS = 5 * 60 * 1000

function extractRepoPluginId(githubRepo?: string): string | undefined {
  const match = githubRepo?.match(/n\.e\.k\.o_plugin_([a-z_][a-z0-9_]*)/i)
  return match?.[1]
}

function marketIdentityKeys(plugin: {
  slug?: string
  name?: string
  id: string | number
  rawId?: number | string
  github_repo?: string
}): string[] {
  const keys = new Set<string>()
  for (const value of [
    plugin.slug,
    plugin.name,
    String(plugin.id),
    plugin.rawId !== undefined ? String(plugin.rawId) : '',
    extractRepoPluginId(plugin.github_repo),
  ]) {
    const normalized = String(value || '').trim().toLowerCase()
    if (normalized) keys.add(normalized)
  }
  return [...keys]
}

function resolveExpectedTomlId(plugin: Pick<MarketPlugin, 'slug' | 'github_repo'>): string | null {
  return extractRepoPluginId(plugin.github_repo) || plugin.slug || null
}

// ─── 本地插件对比：slug / repo plugin_id / lock 三路配对 ───────────
const localPluginKeys = computed(() => {
  const keys = new Set<string>()
  for (const p of pluginStore.pluginsWithStatus) {
    const id = String(p.id || '').toLowerCase()
    const name = String(p.name || '').toLowerCase()
    if (id) keys.add(id)
    if (name) keys.add(name)
  }
  return keys
})

function isInstalled(plugin: MarketPlugin): boolean {
  for (const key of marketIdentityKeys(plugin)) {
    if (installedByPid.value.has(key)) return true
  }
  return marketIdentityKeys(plugin).some((key) => localPluginKeys.value.has(key))
}

// ─── 工作台：过滤 + 分组 + 布局 ───────────────────────────────────
const {
  filterText,
  useRegex,
  filterMode,
  selectedGroupIds,
  layoutMode,
  regexError,
  groupCounts,
  filteredItems,
} = useMarketWorkbench(plugins, { isInstalled })

const activeGroupId = computed(() => selectedGroupIds.value[0] || 'all')
const activeGroupLabel = computed(() =>
  activeGroupId.value === 'recommended'
    ? t('market.recommended')
    : t('market.allPlugins'),
)

// ─── UI 描述符 ────────────────────────────────────────────────────
const groupChoices = computed<GroupChoiceDescriptor[]>(() => [
  { id: 'recommended', label: t('market.recommended') },
  { id: 'all', label: t('market.allPlugins') },
])

const layoutChoices = computed<LayoutChoiceDescriptor[]>(() => [
  { value: 'list', label: t('plugins.layoutList') },
  { value: 'single', label: t('plugins.layoutSingle') },
  { value: 'double', label: t('plugins.layoutDouble') },
  { value: 'compact', label: t('plugins.layoutCompact') },
])

const sortOptions = computed(() => [
  { value: 'created_at', label: t('market.sortNewest') },
  { value: 'download_count', label: t('market.sortMostDownloads') },
  { value: 'rating_average', label: t('market.sortTopRated') },
  { value: 'name', label: t('market.sortName') },
])

const filterRuleGroups = computed<FilterRuleGroupDescriptor[]>(() => [
  {
    key: 'state',
    title: t('market.filterGroups.state'),
    rules: [
      { token: 'is:recommended', label: t('market.filterLabels.recommended') },
      { token: 'is:installed', label: t('market.filterLabels.installed') },
      { token: 'is:uninstalled', label: t('market.filterLabels.uninstalled') },
    ],
  },
  {
    key: 'zone',
    title: t('market.filterGroups.zone'),
    rules: [
      { token: 'zone:game', label: t('market.zones.game') },
      { token: 'zone:companion', label: t('market.zones.companion') },
      { token: 'zone:function', label: t('market.zones.function') },
      { token: 'zone:entertainment', label: t('market.zones.entertainment') },
      { token: 'zone:tool', label: t('market.zones.tool') },
    ],
  },
  {
    key: 'meta',
    title: t('market.filterGroups.meta'),
    rules: [
      { token: 'tag:', label: t('market.filterLabels.tag') },
      { token: 'author:', label: t('market.filterLabels.author') },
      { token: 'name:', label: t('market.filterLabels.name') },
      { token: 'v:>=', label: t('market.filterLabels.versionGte') },
      { token: 'has:repo', label: t('market.filterLabels.hasRepo') },
      { token: 'has:tags', label: t('market.filterLabels.hasTags') },
    ],
  },
])

const totalPages = computed(() => Math.ceil(totalCount.value / pageSize))

// ─── 后端查询：提取纯关键词，qualifier 和 regex 留给前端 ────────
/** 从用户输入里抽取可以直传给后端 q= 的"裸 term"。 */
function extractServerQuery(input: string): string {
  if (!input.trim()) return ''
  if (useRegex.value) return ''
  const tokens = input.match(/"[^"]+"|\S+/g) || []
  const terms = tokens
    .map((raw) => {
      const negated = raw.startsWith('-')
      const body = negated ? raw.slice(1) : raw
      const unquoted = body.replace(/^"(.*)"$/, '$1').trim()
      if (!unquoted || unquoted.includes(':')) return ''
      if (negated) return ''
      return unquoted
    })
    .filter(Boolean)
  return terms.join(' ').trim()
}

async function ensureBridgeToken(options: { forceRefresh?: boolean } = {}): Promise<string> {
  if (bridgeToken.value && !options.forceRefresh) return bridgeToken.value
  if (options.forceRefresh) {
    bridgeToken.value = ''
    localStorage.removeItem('neko_bridge_token')
  }
  try {
    const res = await fetch('/market/bridge-token')
    if (res.ok) {
      const data = await res.json()
      if (data.bridge_token) {
        bridgeToken.value = data.bridge_token
        localStorage.setItem('neko_bridge_token', data.bridge_token)
      }
    }
  } catch {
    // 静默降级
  }
  if (!bridgeToken.value) {
    bridgeToken.value = localStorage.getItem('neko_bridge_token') || ''
  }
  return bridgeToken.value
}

function bridgeUrl(path: string, token: string): string {
  const separator = path.includes('?') ? '&' : '?'
  return `${path}${separator}token=${encodeURIComponent(token)}`
}

async function fetchBridge(
  path: string,
  init?: RequestInit,
  options: { retryOnForbidden?: boolean } = {},
): Promise<Response | null> {
  const token = await ensureBridgeToken()
  if (!token) return null
  let res = await fetch(bridgeUrl(path, token), init)
  if (res.status !== 403 || options.retryOnForbidden === false) return res

  const freshToken = await ensureBridgeToken({ forceRefresh: true })
  if (!freshToken) return res
  res = await fetch(bridgeUrl(path, freshToken), init)
  return res
}

let loadSeq = 0

async function loadPlugins() {
  if (!marketAvailable.value) return
  const mySeq = ++loadSeq
  loading.value = true
  try {
    const params: FetchMarketPluginsParams = {
      page: currentPage.value,
      page_size: pageSize,
      sort_by: sortBy.value,
      sort_order: sortOrder.value,
      // v2 (R7.3): 全局 channel 偏好透传给 Market；切换后 watcher 会触发重载
      channel: userPref.channel,
    }
    const q = extractServerQuery(filterText.value)
    if (q) params.search = q
    if (activeGroupId.value === 'recommended') params.featured_only = true

    const result = await fetchMarketPlugins(params)
    // 只接受最新一次请求的返回值，避免乱序覆盖
    if (mySeq !== loadSeq) return
    if (result) {
      plugins.value = result.items
      totalCount.value = result.total
      lastLoadFailed.value = false
    } else {
      // ``fetchMarketPlugins`` returns null on transient API/network error.
      // Keep ``marketAvailable`` driven by ``isMarketAvailable()`` only so a
      // hiccup here does not freeze the early-return guard below and lock
      // the user into the "not configured" empty state until remount.
      lastLoadFailed.value = true
    }
  } catch {
    if (mySeq === loadSeq) lastLoadFailed.value = true
  } finally {
    if (mySeq === loadSeq) loading.value = false
  }
}

// ─── Installed snapshot + yank detection (R8) ────────────────────────

interface MarketInstalledItem {
  plugin_id: string
  path: string
  latest_install_source: {
    plugin_market_id?: string
    channel: 'stable' | 'beta'
    version: string
    package_sha256: string
    payload_hash: string | null
    package_url: string
    published_at: string
  } | null
}

async function fetchInstalledFromBridge(): Promise<MarketInstalledItem[]> {
  try {
    const res = await fetchBridge('/market/installed')
    if (!res) return []
    if (!res.ok) return []
    const data = await res.json()
    return Array.isArray(data?.installed) ? data.installed : []
  } catch {
    return []
  }
}

/**
 * 拉一遍 /market/installed，更新 installedByPid 与 yankedMap。
 *
 * yank 检测策略（R8.1 / R8.5 / R8.6）：
 *   - 同 (plugin_id, channel) 五分钟内复用缓存；
 *   - Market 不可达 / 拉版本失败时静默不更新（不闪红，不抛错）；
 *   - 仅对"已装且 latest_install_source 非空"的条目执行版本表查询。
 */
async function yankSweep() {
  if (!marketAvailable.value) return
  const installed = await fetchInstalledFromBridge()
  const newIndex = new Map<string, InstalledMarketEntry>()
  const uniqueEntries = new Map<string, InstalledMarketEntry>()
  for (const item of installed) {
    if (!item.latest_install_source) continue
    const entry: InstalledMarketEntry = {
      plugin_id: item.plugin_id,
      market_id: item.latest_install_source.plugin_market_id,
      installed_version: item.latest_install_source.version,
      channel: item.latest_install_source.channel,
      package_url: item.latest_install_source.package_url,
    }
    uniqueEntries.set(item.plugin_id.toLowerCase(), entry)
    newIndex.set(item.plugin_id.toLowerCase(), entry)
    if (entry.market_id) {
      newIndex.set(String(entry.market_id).toLowerCase(), entry)
    }
  }
  installedByPid.value = newIndex
  // Build the next yank map into a local, then atomic-swap below — so a
  // transient `fetchMarketPluginVersions` failure preserves the previous
  // warning (R8.5 "失败静默") instead of clearing every entry's flag until
  // the next successful sweep. Uninstalled plugins drop out naturally
  // because only keys we visit get carried over.
  const previousYanked = yankedMap.value
  const nextYanked: Record<string, boolean> = {}

  for (const entry of uniqueEntries.values()) {
    const pidKey = entry.plugin_id.toLowerCase()
    const marketKey = entry.market_id ? String(entry.market_id).toLowerCase() : ''
    // Query the channel the plugin was actually installed from; otherwise a
    // user who installed a stable version and later switched the global
    // preference to beta would lose the yanked flag on the stable install.
    const narrowedEntryChannel = narrowMarketChannel(entry.channel)
    const entryChannel = narrowedEntryChannel === 'unknown' ? userPref.channel : narrowedEntryChannel
    const cacheKey = `${entry.market_id || entry.plugin_id}::${entryChannel}`
    const cached = yankCache.get(cacheKey)
    let yankedVersions: Set<string>

    if (cached && Date.now() - cached.fetchedAt < YANK_TTL_MS) {
      yankedVersions = cached.yankedVersions
    } else {
      const versions = await fetchMarketPluginVersions(entry.market_id || entry.plugin_id, {
        channel: entryChannel,
        include_yanked: true,
      })
      if (!versions) {
        // Fetch failed — carry the previous flag forward so a known-yanked
        // package doesn't lose its warning on a flaky network.
        const carryPid = previousYanked[pidKey]
        if (carryPid !== undefined) nextYanked[pidKey] = carryPid
        if (marketKey) {
          const carryMarket = previousYanked[marketKey]
          if (carryMarket !== undefined) nextYanked[marketKey] = carryMarket
        }
        continue
      }
      yankedVersions = new Set(
        versions
          .filter((v) => v.yanked_at !== null && v.yanked_at !== undefined)
          .map((v) => v.version),
      )
      yankCache.set(cacheKey, { fetchedAt: Date.now(), yankedVersions })
    }

    const yanked = yankedVersions.has(entry.installed_version)
    nextYanked[pidKey] = yanked
    if (marketKey) nextYanked[marketKey] = yanked
  }

  yankedMap.value = nextYanked
}

// ─── 交互：分页、搜索 debounce、排序、分组切换 ────────────────────

let searchDebounceTimer: number | null = null

watch(filterText, () => {
  if (searchDebounceTimer) clearTimeout(searchDebounceTimer)
  searchDebounceTimer = window.setTimeout(() => {
    currentPage.value = 1
    loadPlugins()
  }, 400)
})

watch(useRegex, () => {
  currentPage.value = 1
  loadPlugins()
})

// v2 (R7.5): 切换全局 channel 立即重载列表 + 重新跑 yank sweep
watch(
  () => userPref.channel,
  () => {
    currentPage.value = 1
    yankCache.clear()
    yankedMap.value = {}
    loadPlugins()
    yankSweep()
  },
)

watch(activeGroupId, () => {
  currentPage.value = 1
  loadPlugins()
})

function onSortChange() {
  // name 字段默认升序，其他字段默认降序
  sortOrder.value = sortBy.value === 'name' ? 'asc' : 'desc'
  currentPage.value = 1
  loadPlugins()
}

function handlePageChange(page: number) {
  currentPage.value = page
  loadPlugins()
}

function handlePluginClick(plugin: MarketWorkbenchItem): void {
  if (marketBaseUrl.value) {
    const path = `/#/plugin/${encodeURIComponent(String(plugin.rawId))}`
    openExternalUrl(`${marketBaseUrl.value}${path}`)
  } else if (plugin.github_repo) {
    openExternalUrl(plugin.github_repo)
  }
}

function openMarketExternal() {
  if (marketBaseUrl.value) openExternalUrl(marketBaseUrl.value)
}

// ─── 安装流程（与之前一致，换成新的 MarketPlugin id 类型） ───────

interface ResolvedInstallPayload {
  package_url: string
  package_sha256: string | null
  payload_hash: string | null
  version: string
  channel: string | null
  published_at: string | null
}

async function resolveInstallPayload(
  plugin: MarketWorkbenchItem,
): Promise<ResolvedInstallPayload | null> {
  // v2: Market 接口已经把 latest_version 嵌套对象的所有字段一次性给出，
  // 优先直接用 plugin 上的派生字段；只在数据缺失时才回退到二次拉取
  // /plugins/{id}/versions 拿权威 release 行。
  if (plugin.has_release && plugin.download_url) {
    return {
      package_url: plugin.download_url,
      package_sha256: plugin.latest_package_sha256 || null,
      payload_hash: plugin.latest_payload_hash ?? null,
      version: plugin.version,
      channel: plugin.latest_channel || null,
      published_at: plugin.latest_published_at || null,
    }
  }

  // 兜底：从 /plugins/{id}/versions 拿一行匹配 plugin.version 的版本
  let packageUrl = plugin.download_url || ''
  let packageSha256: string | null = null
  let payloadHash: string | null = null
  let version = plugin.version
  let channel: string | null = plugin.latest_channel || null
  let publishedAt: string | null = plugin.latest_published_at || null

  try {
    const versions = await fetchMarketPluginVersions(plugin.rawId, {
      channel: userPref.channel,
    })
    if (versions && versions.length > 0) {
      const matched =
        versions.find((v) => v.version === plugin.version) ?? versions[0]
      if (matched) {
        packageUrl = matched.package_url || packageUrl
        packageSha256 = matched.package_sha256 || null
        payloadHash = matched.payload_hash ?? null
        version = matched.version || version
        channel = matched.channel || channel
      }
    }
  } catch {
    // 静默降级
  }

  if (!packageUrl) return null
  return {
    package_url: packageUrl,
    package_sha256: packageSha256,
    payload_hash: payloadHash,
    version,
    channel,
    published_at: publishedAt,
  }
}

async function pollInstallTask(
  taskId: string,
  pluginName: string,
  options: { mode?: 'install' | 'upgrade' | 'reinstall' } = {},
): Promise<boolean> {
  const mode = options.mode ?? 'install'
  beginInstallTaskTracking(taskId, pluginName, mode)

  const deadline = Date.now() + 3 * 60 * 1000
  while (Date.now() < deadline) {
    try {
      const res = await fetchBridge(`/market/tasks/${taskId}`)
      if (!res) {
        markInstallTaskFailed(taskId, t('market.installFailed'), {
          error: t('market.pairRequired'),
        })
        ElMessage.warning(t('market.pairRequired'))
        return false
      }
      if (res.ok) {
        const task = (await res.json()) as MarketInstallTask
        activeInstallTask.value = task

        if (task.status === 'completed') {
          ElMessage.success(
            mode === 'upgrade' || mode === 'reinstall'
              ? t('market.upgradeSuccess', { name: pluginName })
              : t('market.installSuccess', { name: pluginName }),
          )
          pluginStore.syncRegistryAndFetch().catch(() => {})
          yankSweep().catch(() => {})
          return true
        }
        if (task.status === 'failed') {
          ElMessage.error(resolveInstallTaskErrorMessage(task))
          return false
        }
      }
    } catch {
      // 继续轮询
    }
    await new Promise((r) => setTimeout(r, 800))
  }

  activeInstallTask.value = {
    ...(activeInstallTask.value || {
      task_id: taskId,
      stage: 'failed',
      progress: 0,
      message: t('market.installFailed'),
    }),
    status: 'failed',
    stage: 'failed',
    error: t('market.installFailed'),
  }
  ElMessage.warning(t('market.installFailed'))
  return false
}

async function handleInstall(plugin: MarketWorkbenchItem) {
  if (!plugin.has_release) {
    ElMessage.warning(t('market.noVersionAvailable'))
    return
  }
  const payload = await resolveInstallPayload(plugin)
  if (!payload) {
    ElMessage.warning(t('market.noDownloadUrl'))
    return
  }
  if (!payload.package_sha256) {
    ElMessage.error(t('market.installFailed'))
    return
  }

  installingId.value = plugin.id

  try {
    const res = await fetchBridge('/market/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        package_url: payload.package_url,
        package_sha256: payload.package_sha256,
        payload_hash: payload.payload_hash,
        plugin_id: String(plugin.rawId),
        version: payload.version,
        channel: payload.channel,
        published_at: payload.published_at,
        // v2 (Option C): 把 Market slug 作为期望的 plugin.toml id，让 bridge
        // 在 unpack 后做身份一致性校验；不一致不阻塞，只 warn。
        expected_plugin_toml_id: resolveExpectedTomlId(plugin),
        mode: 'install',
        on_conflict: 'rename',
      }),
    })
    if (!res) {
      ElMessage.warning(t('market.pairRequired'))
      return
    }

    if (res.ok) {
      const data = await res.json()
      if (data.task_id) {
        await pollInstallTask(data.task_id, plugin.name)
      } else {
        ElMessage.success(t('market.installSuccess', { name: plugin.name }))
      }
    } else if (res.status === 403) {
      ElMessage.warning(t('market.pairRequired'))
    } else {
      const err = await res.json().catch(() => ({}))
      ElMessage.error(err.detail || t('market.installFailed'))
    }
  } catch {
    openExternalUrl(payload.package_url)
  } finally {
    installingId.value = null
  }
}

/**
 * v2 (R9): 升级已装插件到 Market 的最新版本。
 *
 * 与 install 路径区别：
 *   - mode = 'upgrade' 让 bridge 走 _do_upgrade 分支（rename 旧目录 →
 *     unpack 新包 → record_market_upgrade）；
 *   - on_conflict = 'fail'：旧目录已 rename 走，新目录不该撞名；
 *   - 错误码识别在 pollInstallTask 内统一处理。
 */
async function handleUpgrade(plugin: MarketWorkbenchItem) {
  if (!plugin.has_release) {
    ElMessage.warning(t('market.noVersionAvailable'))
    return
  }
  const payload = await resolveInstallPayload(plugin)
  if (!payload) {
    ElMessage.warning(t('market.noDownloadUrl'))
    return
  }
  if (!payload.package_sha256) {
    ElMessage.error(t('market.installFailed'))
    return
  }

  upgradingId.value = plugin.id
  try {
    const res = await fetchBridge('/market/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        package_url: payload.package_url,
        package_sha256: payload.package_sha256,
        payload_hash: payload.payload_hash,
        plugin_id: String(plugin.rawId),
        version: payload.version,
        channel: payload.channel,
        published_at: payload.published_at,
        // v2 (Option C): 升级路径同样透传 slug 做身份对账
        expected_plugin_toml_id: resolveExpectedTomlId(plugin),
        mode: 'upgrade',
        on_conflict: 'fail',
      }),
    })
    if (!res) {
      ElMessage.warning(t('market.pairRequired'))
      return
    }

    if (res.ok) {
      const data = await res.json()
      if (data.task_id) {
        await pollInstallTask(data.task_id, plugin.name, { mode: 'upgrade' })
      }
    } else if (res.status === 400) {
      const err = await res.json().catch(() => ({}))
      const code = err?.detail?.code || ''
      if (code === 'plugin_not_installed_for_upgrade') {
        ElMessage.error(t('market.pluginNotInstalled'))
      } else {
        ElMessage.error(err?.detail?.message || t('market.installFailed'))
      }
    } else if (res.status === 403) {
      ElMessage.warning(t('market.pairRequired'))
    } else {
      const err = await res.json().catch(() => ({}))
      ElMessage.error(err.detail || t('market.installFailed'))
    }
  } catch {
    ElMessage.error(t('market.installFailed'))
  } finally {
    upgradingId.value = null
  }
}

/**
 * 当前 plugin 已装 + 本地版本 < Market latest 时返回本地版本。
 * 用作 MarketPluginCard 的 :local-version prop，让 card 内部走 semver 比较。
 */
function getLocalInstalledVersion(plugin: MarketWorkbenchItem): string | undefined {
  for (const key of marketIdentityKeys(plugin)) {
    const entry = installedByPid.value.get(key)
    if (entry?.installed_version) return entry.installed_version
  }
  return undefined
}

function isYanked(plugin: MarketWorkbenchItem): boolean {
  for (const key of marketIdentityKeys(plugin)) {
    if (yankedMap.value[key]) return true
  }
  return false
}

async function initialize() {
  marketAvailable.value = await isMarketAvailable()
  marketBaseUrl.value = await getMarketUrl()
  await ensureBridgeToken()
  if (marketAvailable.value) {
    await loadPlugins()
    yankSweep().catch(() => {})
  }
  if (pluginStore.pluginsWithStatus.length === 0) {
    pluginStore.fetchPlugins().catch(() => {})
  }
}

onMounted(() => {
  if (props.active !== false) initialize()
})

onBeforeUnmount(() => {
  if (searchDebounceTimer) {
    clearTimeout(searchDebounceTimer)
    searchDebounceTimer = null
  }
  // 让在途 loadPlugins 的 mySeq 全部失效
  loadSeq++
})

watch(
  () => props.active,
  (active) => {
    if (active && plugins.value.length === 0 && !loading.value) {
      initialize()
    }
  },
)
</script>

<style scoped>
.market-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
  width: 100%;
  min-width: 0;
}

.market-panel--embedded {
  height: 100%;
  padding: 18px 18px 24px;
  background: var(--el-bg-color);
  border-radius: 16px;
  border: 1px solid var(--el-border-color-lighter);
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.04);
}

.market-panel__heading {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  padding-bottom: 4px;
}

.market-panel__heading-copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.market-panel__heading-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 700;
  color: var(--el-text-color-primary);
}

.market-panel__heading-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.market-panel__heading-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.market-panel__icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: var(--el-text-color-secondary);
  cursor: pointer;
  transition: background-color 0.2s ease, color 0.2s ease;
}

.market-panel__icon-btn:hover {
  background: color-mix(in srgb, var(--el-color-primary) 8%, transparent);
  color: var(--el-color-primary);
}

.market-panel__toolbar {
  margin-top: 0;
}

.market-panel__toolbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.market-panel__sort {
  width: 140px;
}

.market-panel__content {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.market-panel__pagination {
  display: flex;
  justify-content: center;
  padding-top: 8px;
}

.market-panel__empty-hint {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin-top: 8px;
}

.market-panel__channel-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.market-panel__channel-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.market-panel__channel-hint {
  margin: 0;
  font-size: 12px;
  line-height: 1.4;
  color: var(--el-text-color-secondary);
}

.market-install-progress {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.market-install-progress__message {
  font-size: 14px;
  color: var(--el-text-color-primary);
  line-height: 1.5;
}

.market-install-progress__meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
